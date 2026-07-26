import json

from agency.agents.designer import Designer
from agency.org import BrandContext

_BRAND = BrandContext(name="Example Co", voice="bold and minimal", audience="everyone", guidelines=[], banned_topics=[])


class RecordingProvider:
    def __init__(self, reply: str):
        self.reply = reply
        self.last_system = None
        self.last_user = None
        self.last_image_bytes = None
        self.last_mime = None

    def complete(self, system: str, user: str) -> str:
        self.last_system = system
        self.last_user = user
        return self.reply

    def complete_with_image(self, system: str, user: str, image_bytes: bytes, mime_type: str) -> str:
        self.last_system = system
        self.last_user = user
        self.last_image_bytes = image_bytes
        self.last_mime = mime_type
        return self.reply


def test_recommend_style_parses_json():
    provider = RecordingProvider(json.dumps({"style": "photoreal", "checkpoint": "sdxl.safetensors", "negative_prompt": "blurry"}))

    rec = Designer(provider).recommend_style(_BRAND, "a product shot", ["default", "photoreal"])

    assert rec.style == "photoreal"
    assert rec.checkpoint == "sdxl.safetensors"
    assert rec.negative_prompt == "blurry"
    assert "photoreal" in provider.last_system


def test_recommend_style_falls_back_to_first_available_on_bad_json():
    provider = RecordingProvider("not json")

    rec = Designer(provider).recommend_style(_BRAND, "a product shot", ["default", "photoreal"])

    assert rec.style == "default"
    assert rec.checkpoint is None


def test_review_asset_sends_image_bytes_and_parses_verdict(tmp_path):
    image_path = tmp_path / "asset.png"
    image_path.write_bytes(b"fake-png-bytes")
    provider = RecordingProvider(json.dumps({"approved": True, "reason": "on brand"}))

    verdict = Designer(provider).review_asset(_BRAND, "a product shot", image_path)

    assert verdict.approved is True
    assert verdict.reason == "on brand"
    assert provider.last_image_bytes == b"fake-png-bytes"
    assert provider.last_mime == "image/png"


def test_review_asset_guesses_mime_from_suffix(tmp_path):
    image_path = tmp_path / "asset.jpg"
    image_path.write_bytes(b"fake-jpg-bytes")
    provider = RecordingProvider(json.dumps({"approved": False, "reason": "off brand"}))

    verdict = Designer(provider).review_asset(_BRAND, "a product shot", image_path)

    assert verdict.approved is False
    assert provider.last_mime == "image/jpeg"


def test_review_asset_handles_unparseable_reply(tmp_path):
    image_path = tmp_path / "asset.png"
    image_path.write_bytes(b"x")
    provider = RecordingProvider("not json")

    verdict = Designer(provider).review_asset(_BRAND, "a product shot", image_path)

    assert verdict.approved is False
