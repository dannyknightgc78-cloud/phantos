from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path

from config import settings


def extract_video_frames(video_path: Path, out_dir: Path | None = None) -> dict[str, str]:
    """Extract start / mid / end frames from an uploaded video for adapt/i2v."""
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("ffmpeg/ffprobe required to adapt from video")

    out_dir = out_dir or settings.uploads_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"vid-{uuid.uuid4().hex[:8]}"

    # Duration
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        duration = max(0.5, float(probe.stdout.strip()))
    except ValueError:
        duration = 3.0

    mid = max(0.0, duration / 2.0)
    end = max(0.0, duration - 0.15)

    paths: dict[str, str] = {}
    for key, t in (("start", 0.0), ("mid", mid), ("end", end)):
        dest = out_dir / f"{stem}-{key}.png"
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{t:.3f}",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(dest),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and dest.exists():
            paths[key] = str(dest)

    if "start" not in paths:
        raise RuntimeError("Could not extract frames from video")
    return paths


PHOTOREAL_SUFFIX = (
    ", photorealistic, ultra realistic photography, natural skin texture, "
    "real camera, 35mm lens, shallow depth of field, cinematic lighting, "
    "8k detail, no cartoon, no anime, no illustration, no CGI look"
)


def apply_visual_style(prompt: str, style: str) -> str:
    base = prompt.strip()
    if style == "photoreal":
        if "photoreal" not in base.lower():
            return base + PHOTOREAL_SUFFIX
        return base
    if style == "cinematic":
        return base + ", cinematic color grade, film still, anamorphic, dramatic lighting"
    if style == "anime":
        return base + ", anime style, clean lines, vibrant colors"
    return base
