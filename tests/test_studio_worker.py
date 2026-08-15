from pathlib import Path
from unittest.mock import patch

import pytest

from agency.agents.designer import CreativeVerdict
from agency.agents.studio_worker import (
    FfmpegError,
    StudioWorker,
    WhisperError,
    default_ffmpeg_runner,
    default_whisper_transcriber,
)
from agency.org import BrandContext

_BRAND = BrandContext(name="Example Co", voice="energetic", audience="everyone", guidelines=[], banned_topics=[])


class FakeFfmpegRunner:
    def __init__(self):
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        output_path = Path(args[-1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake-output")


class FakeDesigner:
    def __init__(self, verdict):
        self.verdict = verdict
        self.last_asset_path = None

    def review_asset(self, brand, brief, asset_path):
        self.last_asset_path = asset_path
        return self.verdict


def test_concatenate_clips_writes_list_file_and_calls_ffmpeg(tmp_path):
    runner = FakeFfmpegRunner()
    worker = StudioWorker(ffmpeg_runner=runner)
    clips = [tmp_path / "a.mp4", tmp_path / "b.mp4"]

    output = worker.concatenate_clips(clips, tmp_path / "out" / "final.mp4")

    assert output == tmp_path / "out" / "final.mp4"
    args = runner.calls[0]
    assert args[:4] == ["-f", "concat", "-safe", "0"]
    assert args[-1] == str(tmp_path / "out" / "final.mp4")
    assert not (tmp_path / "out" / "final.concat.txt").exists()  # cleaned up


def test_add_audio_track_calls_ffmpeg_with_both_inputs(tmp_path):
    runner = FakeFfmpegRunner()
    worker = StudioWorker(ffmpeg_runner=runner)

    worker.add_audio_track(tmp_path / "video.mp4", tmp_path / "audio.flac", tmp_path / "out.mp4")

    args = runner.calls[0]
    assert "-shortest" in args
    assert str(tmp_path / "video.mp4") in args
    assert str(tmp_path / "audio.flac") in args


def test_assemble_slideshow_repeats_last_image_and_sets_durations(tmp_path):
    runner = FakeFfmpegRunner()
    worker = StudioWorker(ffmpeg_runner=runner)
    images = [tmp_path / "1.png", tmp_path / "2.png"]

    output_path = tmp_path / "out" / "slideshow.mp4"
    worker.assemble_slideshow(images, output_path, seconds_per_image=2.5)

    args = runner.calls[0]
    assert "-vsync" in args
    assert str(output_path) == args[-1]


def test_assemble_slideshow_includes_audio_input_when_given(tmp_path):
    runner = FakeFfmpegRunner()
    worker = StudioWorker(ffmpeg_runner=runner)

    worker.assemble_slideshow([tmp_path / "1.png"], tmp_path / "out.mp4", audio_path=tmp_path / "bg.flac")

    args = runner.calls[0]
    assert str(tmp_path / "bg.flac") in args
    assert "-shortest" in args


def test_assemble_slideshow_list_file_has_correct_concat_syntax(tmp_path):
    captured = {}

    def capturing_runner(args):
        list_file = Path(args[args.index("-i") + 1])
        captured["content"] = list_file.read_text()
        Path(args[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(args[-1]).write_bytes(b"x")

    worker = StudioWorker(ffmpeg_runner=capturing_runner)
    images = [tmp_path / "1.png", tmp_path / "2.png"]

    worker.assemble_slideshow(images, tmp_path / "out.mp4", seconds_per_image=3.0)

    lines = captured["content"].strip().splitlines()
    assert lines[0] == f"file '{(tmp_path / '1.png').resolve()}'"
    assert lines[1] == "duration 3.0"
    assert lines[2] == f"file '{(tmp_path / '2.png').resolve()}'"
    assert lines[3] == "duration 3.0"
    assert lines[4] == f"file '{(tmp_path / '2.png').resolve()}'"  # repeated, no duration


def test_extract_first_frame_calls_ffmpeg(tmp_path):
    runner = FakeFfmpegRunner()
    worker = StudioWorker(ffmpeg_runner=runner)

    worker.extract_first_frame(tmp_path / "video.mp4", tmp_path / "frame.jpg")

    assert "-frames:v" in runner.calls[0]


def test_assemble_and_review_submits_first_frame_to_designer(tmp_path):
    runner = FakeFfmpegRunner()
    designer = FakeDesigner(CreativeVerdict(approved=True, reason="on brand"))
    worker = StudioWorker(designer=designer, ffmpeg_runner=runner)

    video_path, verdict = worker.assemble_and_review(
        _BRAND, "a product reel", [tmp_path / "1.png"], tmp_path / "out"
    )

    assert video_path == tmp_path / "out" / "assembled.mp4"
    assert verdict.approved is True
    assert designer.last_asset_path == tmp_path / "out" / "assembled-frame.jpg"


def test_assemble_and_review_skips_designer_when_none():
    worker = StudioWorker(designer=None, ffmpeg_runner=FakeFfmpegRunner())

    video_path, verdict = worker.assemble_and_review(
        _BRAND, "a product reel", [Path("/tmp/1.png")], Path("/tmp/out_no_designer")
    )

    assert verdict is None


@patch("subprocess.run")
def test_default_ffmpeg_runner_raises_on_nonzero_exit(mock_run):
    mock_run.return_value.returncode = 1
    mock_run.return_value.stderr = "ffmpeg exploded"

    with pytest.raises(FfmpegError):
        default_ffmpeg_runner(["-i", "in.mp4", "out.mp4"])


@patch("subprocess.run")
def test_default_ffmpeg_runner_succeeds_on_zero_exit(mock_run):
    mock_run.return_value.returncode = 0

    default_ffmpeg_runner(["-i", "in.mp4", "out.mp4"])

    called_args = mock_run.call_args[0][0]
    assert called_args[0] == "ffmpeg"
    assert "-y" in called_args


# --- auto-captions (Whisper -> burned-in subtitles) ---


class FakeWhisperTranscriber:
    def __init__(self, srt_path=None):
        self.calls = []
        self._srt_path = srt_path

    def __call__(self, input_path, output_dir, model):
        self.calls.append((input_path, output_dir, model))
        srt_path = self._srt_path or (output_dir / f"{input_path.stem}.srt")
        srt_path.parent.mkdir(parents=True, exist_ok=True)
        srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n")
        return srt_path


def test_add_captions_transcribes_then_burns_in_via_ffmpeg(tmp_path):
    ffmpeg = FakeFfmpegRunner()
    whisper = FakeWhisperTranscriber()
    worker = StudioWorker(ffmpeg_runner=ffmpeg, whisper_transcriber=whisper)
    video_path = tmp_path / "video.mp4"

    output = worker.add_captions(video_path, tmp_path / "out" / "captioned.mp4", model="small")

    assert output == tmp_path / "out" / "captioned.mp4"
    assert whisper.calls == [(video_path, tmp_path / "out", "small")]
    args = ffmpeg.calls[0]
    assert args[0] == "-i"
    assert args[1] == str(video_path)
    vf_index = args.index("-vf")
    assert "subtitles=" in args[vf_index + 1]
    assert str(tmp_path / "out" / "video.srt") in args[vf_index + 1]
    assert "-c:a" in args and args[args.index("-c:a") + 1] == "copy"
    # video is NOT stream-copied (subtitles filter requires re-encoding)
    assert "-c:v" not in args


def test_add_captions_escapes_colons_and_backslashes_in_srt_path(tmp_path):
    # Simulate a path containing a colon, the character ffmpeg's subtitles
    # filter treats as an option separator.
    tricky_dir = tmp_path / "weird:dir"
    ffmpeg = FakeFfmpegRunner()
    whisper = FakeWhisperTranscriber(srt_path=tricky_dir / "video.srt")
    worker = StudioWorker(ffmpeg_runner=ffmpeg, whisper_transcriber=whisper)

    worker.add_captions(tmp_path / "video.mp4", tmp_path / "out.mp4")

    args = ffmpeg.calls[0]
    vf_value = args[args.index("-vf") + 1]
    assert "weird\\:dir" in vf_value  # colon escaped
    assert vf_value.startswith("subtitles='") and vf_value.endswith("'")


def test_assemble_and_review_with_burn_captions_returns_captioned_path(tmp_path):
    ffmpeg = FakeFfmpegRunner()
    whisper = FakeWhisperTranscriber()
    worker = StudioWorker(ffmpeg_runner=ffmpeg, whisper_transcriber=whisper)

    video_path, verdict = worker.assemble_and_review(
        _BRAND, "a product reel", [tmp_path / "1.png"], tmp_path / "out",
        audio_path=tmp_path / "narration.mp3", burn_captions=True, whisper_model="tiny",
    )

    assert video_path == tmp_path / "out" / "assembled-captioned.mp4"
    assert whisper.calls[0][2] == "tiny"


def test_assemble_and_review_without_burn_captions_never_calls_whisper(tmp_path):
    whisper = FakeWhisperTranscriber()
    worker = StudioWorker(ffmpeg_runner=FakeFfmpegRunner(), whisper_transcriber=whisper)

    video_path, _ = worker.assemble_and_review(_BRAND, "brief", [tmp_path / "1.png"], tmp_path / "out")

    assert video_path == tmp_path / "out" / "assembled.mp4"
    assert whisper.calls == []


@patch("subprocess.run")
def test_default_whisper_transcriber_raises_on_nonzero_exit(mock_run):
    mock_run.return_value.returncode = 1
    mock_run.return_value.stderr = "whisper exploded"

    with pytest.raises(WhisperError):
        default_whisper_transcriber(Path("in.mp4"), Path("/tmp/out"))


@patch("subprocess.run")
def test_default_whisper_transcriber_returns_expected_srt_path(mock_run, tmp_path):
    mock_run.return_value.returncode = 0

    result = default_whisper_transcriber(Path("clip.mp4"), tmp_path, model="medium")

    assert result == tmp_path / "clip.srt"
    called_args = mock_run.call_args[0][0]
    assert called_args[0] == "whisper"
    assert "--model" in called_args and called_args[called_args.index("--model") + 1] == "medium"
    assert "--output_format" in called_args and called_args[called_args.index("--output_format") + 1] == "srt"
