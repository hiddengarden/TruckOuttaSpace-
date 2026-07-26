import subprocess
from pathlib import Path
from typing import Callable

from agency.agents.designer import CreativeVerdict, Designer
from agency.org import BrandContext

FfmpegRunner = Callable[[list[str]], None]


class FfmpegError(Exception):
    pass


def default_ffmpeg_runner(args: list[str]) -> None:
    result = subprocess.run(["ffmpeg", "-y", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise FfmpegError(result.stderr[-2000:])


class StudioWorker:
    """Assembles a brand's existing asset bank (images/video clips/audio) into
    shorts/reels/longer video via ffmpeg -- no generation happens here, only
    editing/assembly of what Artist/VideoMaster/MusicAgent already produced.
    Reports to Designer: assemble_and_review() extracts the result's first
    frame and submits it for the same brand-voice QC pass used elsewhere.
    """

    def __init__(self, designer: Designer | None = None, ffmpeg_runner: FfmpegRunner = default_ffmpeg_runner):
        self._designer = designer
        self._run = ffmpeg_runner

    def concatenate_clips(self, clip_paths: list[Path], output_path: str | Path) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        list_file = output_path.with_suffix(".concat.txt")
        list_file.write_text("".join(f"file '{Path(p).resolve()}'\n" for p in clip_paths))
        self._run(["-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(output_path)])
        list_file.unlink(missing_ok=True)
        return output_path

    def add_audio_track(self, video_path: str | Path, audio_path: str | Path, output_path: str | Path) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self._run(
            [
                "-i", str(video_path),
                "-i", str(audio_path),
                "-c:v", "copy",
                "-c:a", "aac",
                "-shortest",
                str(output_path),
            ]
        )
        return output_path

    def assemble_slideshow(
        self,
        image_paths: list[Path],
        output_path: str | Path,
        seconds_per_image: float = 3.0,
        audio_path: str | Path | None = None,
    ) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        list_file = output_path.with_suffix(".slideshow.txt")

        lines = []
        for image_path in image_paths:
            lines.append(f"file '{Path(image_path).resolve()}'\n")
            lines.append(f"duration {seconds_per_image}\n")
        if image_paths:
            # ffmpeg's concat demuxer quirk: the last entry's duration is
            # ignored unless the file is repeated once more without one.
            lines.append(f"file '{Path(image_paths[-1]).resolve()}'\n")
        list_file.write_text("".join(lines))

        args = ["-f", "concat", "-safe", "0", "-i", str(list_file)]
        if audio_path is not None:
            args += ["-i", str(audio_path)]
        args += ["-vsync", "vfr", "-pix_fmt", "yuv420p"]
        if audio_path is not None:
            args += ["-c:a", "aac", "-shortest"]
        args += [str(output_path)]

        self._run(args)
        list_file.unlink(missing_ok=True)
        return output_path

    def extract_first_frame(self, video_path: str | Path, output_path: str | Path) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self._run(["-i", str(video_path), "-frames:v", "1", str(output_path)])
        return output_path

    def assemble_and_review(
        self,
        brand: BrandContext,
        brief: str,
        image_paths: list[Path],
        output_dir: str | Path,
        audio_path: str | Path | None = None,
        seconds_per_image: float = 3.0,
    ) -> tuple[Path, CreativeVerdict | None]:
        output_dir = Path(output_dir)
        video_path = self.assemble_slideshow(
            image_paths, output_dir / "assembled.mp4", seconds_per_image, audio_path
        )

        verdict = None
        if self._designer is not None:
            frame_path = self.extract_first_frame(video_path, output_dir / "assembled-frame.jpg")
            verdict = self._designer.review_asset(brand, brief, frame_path)

        return video_path, verdict
