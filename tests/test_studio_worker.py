from pathlib import Path
from unittest.mock import patch

import pytest

from agency.agents.designer import CreativeVerdict
from agency.agents.studio_worker import FfmpegError, StudioWorker, default_ffmpeg_runner
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
