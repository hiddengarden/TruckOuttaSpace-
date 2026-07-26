import json
from pathlib import Path

from agency.agents.designer import StyleRecommendation
from agency.agents.ghost_writer import Chapter, GhostWriter, Publication, render_publication_markdown, slugify
from agency.org import BrandContext

_BRAND = BrandContext(name="Example Co", voice="plain and direct", audience="beginners", guidelines=[], banned_topics=[])


class SequencedProvider:
    def __init__(self, replies):
        self._replies = iter(replies)
        self.calls = []

    def complete(self, system, user):
        self.calls.append((system, user))
        return next(self._replies)


class FakeDesigner:
    def recommend_style(self, brand, brief, available_styles):
        return StyleRecommendation(style=available_styles[0], checkpoint=None, negative_prompt="blurry")


class FakeArtist:
    def __init__(self):
        self.calls = []
        self._counter = 0

    def generate(self, brand, brief, assets_dir, style="default", negative_prompt="", checkpoint=None, seed=None):
        self.calls.append({"brief": brief, "style": style, "negative_prompt": negative_prompt})
        self._counter += 1
        path = Path(assets_dir) / f"out-{self._counter}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        return [path]


def test_write_outline_parses_json_array():
    provider = SequencedProvider([json.dumps(["Chapter One", "Chapter Two"])])

    titles = GhostWriter(provider).write_outline(_BRAND, "a beginner's guide", 2)

    assert titles == ["Chapter One", "Chapter Two"]


def test_write_outline_falls_back_to_raw_text_on_bad_json():
    provider = SequencedProvider(["not json"])

    titles = GhostWriter(provider).write_outline(_BRAND, "a beginner's guide", 2)

    assert titles == ["not json"]


def test_write_chapter_returns_provider_output():
    provider = SequencedProvider(["## Chapter One\nsome content"])

    content = GhostWriter(provider).write_chapter(_BRAND, "a beginner's guide", "Chapter One")

    assert content == "## Chapter One\nsome content"


def test_request_illustration_without_designer_or_artist_returns_none(tmp_path):
    gw = GhostWriter(SequencedProvider([]))

    assert gw.request_illustration(_BRAND, "brief", tmp_path, ["default"]) is None


def test_request_illustration_without_available_styles_returns_none(tmp_path):
    gw = GhostWriter(SequencedProvider([]), designer=FakeDesigner(), artist=FakeArtist())

    assert gw.request_illustration(_BRAND, "brief", tmp_path, []) is None


def test_request_illustration_uses_designer_and_artist(tmp_path):
    artist = FakeArtist()
    gw = GhostWriter(SequencedProvider([]), designer=FakeDesigner(), artist=artist)

    path = gw.request_illustration(_BRAND, "cover brief", tmp_path, ["default", "photoreal"])

    assert path == tmp_path / "out-1.png"
    assert artist.calls[0]["style"] == "default"
    assert artist.calls[0]["negative_prompt"] == "blurry"


def test_write_publication_builds_cover_and_chapters_without_chapter_illustrations(tmp_path):
    provider = SequencedProvider(
        [json.dumps(["Intro", "Conclusion"]), "## Intro\ncontent1", "## Conclusion\ncontent2"]
    )
    artist = FakeArtist()
    gw = GhostWriter(provider, designer=FakeDesigner(), artist=artist)

    pub = gw.write_publication(_BRAND, "a beginner's guide", tmp_path, chapter_count=2, available_styles=["default"])

    assert pub.cover_image_path is not None
    assert [c.title for c in pub.chapters] == ["Intro", "Conclusion"]
    assert all(c.illustration_path is None for c in pub.chapters)
    assert len(artist.calls) == 1  # only the cover


def test_write_publication_illustrates_every_chapter_when_asked(tmp_path):
    provider = SequencedProvider(
        [json.dumps(["Intro", "Conclusion"]), "## Intro\ncontent1", "## Conclusion\ncontent2"]
    )
    artist = FakeArtist()
    gw = GhostWriter(provider, designer=FakeDesigner(), artist=artist)

    pub = gw.write_publication(
        _BRAND, "a beginner's guide", tmp_path, chapter_count=2, illustrate_chapters=True, available_styles=["default"]
    )

    assert all(c.illustration_path is not None for c in pub.chapters)
    assert len(artist.calls) == 3  # cover + 2 chapters


def test_render_publication_markdown_includes_cover_and_chapter_images():
    pub = Publication(
        title="A Beginner's Guide",
        cover_image_path=Path("/assets/cover.png"),
        chapters=[Chapter(title="Intro", content="## Intro\ntext", illustration_path=Path("/assets/intro.png"))],
    )

    markdown = render_publication_markdown(pub)

    assert "# A Beginner's Guide" in markdown
    assert "![cover](/assets/cover.png)" in markdown
    assert "## Intro\ntext" in markdown
    assert "![Intro](/assets/intro.png)" in markdown


def test_slugify():
    assert slugify("A Beginner's Guide!") == "a-beginner-s-guide"
