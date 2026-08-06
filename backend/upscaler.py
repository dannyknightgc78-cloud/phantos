from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def upscale_to_1080(input_path: Path, output_path: Path) -> None:
    if not ffmpeg_available():
        raise RuntimeError("ffmpeg is not installed. Install ffmpeg to use 1080p upscale.")

    vf = (
        "scale=1920:1080:force_original_aspect_ratio=decrease,"
        "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black"
    )

    if input_path.suffix.lower() in {".mp4", ".mov", ".webm"}:
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "copy",
            str(output_path),
        ]
    else:
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-vf",
            vf,
            str(output_path),
        ]

    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        tail = (result.stdout + "\n" + result.stderr)[-2000:]
        raise RuntimeError(tail or "ffmpeg upscale failed")

    if not output_path.exists():
        raise RuntimeError("Upscale finished but output file was not created.")
