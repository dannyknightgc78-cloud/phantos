from __future__ import annotations

import json
import os
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Any

from audio import maybe_add_audio
from config import settings
from media_utils import apply_visual_style
from upscaler import upscale_to_1080


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    UPSCALING = "upscaling"
    COMPLETED = "completed"
    FAILED = "failed"


class GenerationMode(str, Enum):
    TEXT_TO_VIDEO = "text-to-video"
    IMAGE_TO_VIDEO = "image-to-video"
    SUBJECT_TO_VIDEO = "subject-to-video"
    TEXT_TO_IMAGE = "text-to-image"


MODELS: dict[str, dict[str, Any]] = {
    "phantom-wan-1.3b": {
        "label": "Phantos Wan 1.3B",
        "task_map": {
            GenerationMode.TEXT_TO_VIDEO: "t2v-1.3B",
            GenerationMode.IMAGE_TO_VIDEO: "i2v-14B",
            GenerationMode.SUBJECT_TO_VIDEO: "s2v-1.3B",
            GenerationMode.TEXT_TO_IMAGE: "t2i-14B",
        },
    },
    "phantom-wan-14b": {
        "label": "Phantos Wan 14B",
        "task_map": {
            GenerationMode.TEXT_TO_VIDEO: "t2v-14B",
            GenerationMode.IMAGE_TO_VIDEO: "i2v-14B",
            GenerationMode.SUBJECT_TO_VIDEO: "s2v-14B",
            GenerationMode.TEXT_TO_IMAGE: "t2i-14B",
        },
    },
}

RESOLUTIONS = {
    "480p": "832*480",
    "720p": "1280*720",
}

ASPECT_PRESETS = {
    "16:9": "832*480",
    "9:16": "480*832",
    "1:1": "832*480",
}


@dataclass
class Job:
    id: str
    mode: GenerationMode
    prompt: str
    model: str
    resolution: str
    frame_num: int
    status: JobStatus = JobStatus.QUEUED
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: str | None = None
    completed_at: str | None = None
    output_path: str | None = None
    error: str | None = None
    image_path: str | None = None
    end_image_path: str | None = None
    ref_images: list[str] = field(default_factory=list)
    seed: int = 42
    upscale_1080: bool = False
    with_audio: bool = False
    with_music: bool = True
    dialog: str = ""
    music_mood: str = "ambient"
    visual_style: str = "photoreal"
    voice: str = "en-US-GuyNeural"
    video_path: str | None = None
    log_tail: str = ""


class JobStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._lock = Lock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.db_path.exists():
            self._write({})

    def _read(self) -> dict[str, dict[str, Any]]:
        if not self.db_path.exists():
            return {}
        return json.loads(self.db_path.read_text())

    def _write(self, data: dict[str, dict[str, Any]]) -> None:
        self.db_path.write_text(json.dumps(data, indent=2))

    def save(self, job: Job) -> None:
        with self._lock:
            data = self._read()
            data[job.id] = asdict(job)
            data[job.id]["mode"] = job.mode.value
            data[job.id]["status"] = job.status.value
            self._write(data)

    def get(self, job_id: str) -> Job | None:
        data = self._read().get(job_id)
        if not data:
            return None
        return Job(
            id=data["id"],
            mode=GenerationMode(data["mode"]),
            prompt=data["prompt"],
            model=data["model"],
            resolution=data["resolution"],
            frame_num=data["frame_num"],
            status=JobStatus(data["status"]),
            created_at=data["created_at"],
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            output_path=data.get("output_path"),
            error=data.get("error"),
            image_path=data.get("image_path"),
            end_image_path=data.get("end_image_path"),
            ref_images=data.get("ref_images") or [],
            seed=data.get("seed", 42),
            upscale_1080=bool(data.get("upscale_1080", False)),
            with_audio=bool(data.get("with_audio", False)),
            with_music=bool(data.get("with_music", True)),
            dialog=data.get("dialog") or "",
            music_mood=data.get("music_mood") or "ambient",
            visual_style=data.get("visual_style") or "photoreal",
            voice=data.get("voice") or "en-US-GuyNeural",
            video_path=data.get("video_path"),
            log_tail=data.get("log_tail", ""),
        )

    def list_jobs(self, limit: int = 50) -> list[Job]:
        data = self._read()
        jobs = [self.get(job_id) for job_id in data]
        jobs = [job for job in jobs if job is not None]
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]


job_store = JobStore(settings.jobs_db)


def _frame_count(duration_sec: int, fps: int = 16) -> int:
    raw = duration_sec * fps + 1
    return max(17, ((raw - 1) // 4) * 4 + 1)


def resolve_mode(
    *,
    requested: GenerationMode,
    image_path: str | None,
    end_image_path: str | None,
    ref_images: list[str],
) -> GenerationMode:
    if requested == GenerationMode.TEXT_TO_IMAGE:
        return GenerationMode.TEXT_TO_IMAGE

    if image_path and end_image_path:
        return GenerationMode.SUBJECT_TO_VIDEO

    if requested == GenerationMode.SUBJECT_TO_VIDEO or ref_images:
        return GenerationMode.SUBJECT_TO_VIDEO

    if image_path:
        return GenerationMode.IMAGE_TO_VIDEO

    return GenerationMode.TEXT_TO_VIDEO


def _subject_refs(job: Job) -> list[str]:
    refs: list[str] = []
    if job.image_path:
        refs.append(job.image_path)
    if job.end_image_path and job.end_image_path not in refs:
        refs.append(job.end_image_path)
    for ref in job.ref_images:
        if ref not in refs:
            refs.append(ref)
    return refs


def _build_command(job: Job) -> list[str]:
    model = MODELS[job.model]
    task = model["task_map"][job.mode]
    size = RESOLUTIONS.get(job.resolution, RESOLUTIONS["480p"])
    output = settings.outputs_dir / f"{job.id}{'.png' if job.mode == GenerationMode.TEXT_TO_IMAGE else '.mp4'}"

    cmd = [
        settings.python_bin,
        str(settings.phantom_root / "generate.py"),
        "--task",
        task,
        "--size",
        size,
        "--frame_num",
        str(job.frame_num),
        "--ckpt_dir",
        str(settings.wan_ckpt_dir),
        "--phantom_ckpt",
        str(settings.phantom_ckpt),
        "--prompt",
        apply_visual_style(job.prompt, job.visual_style),
        "--base_seed",
        str(job.seed),
        "--save_file",
        str(output),
        "--offload_model",
        "True",
    ]

    if job.mode == GenerationMode.IMAGE_TO_VIDEO:
        if not job.image_path:
            raise ValueError("Image-to-video requires a start image.")
        cmd.extend(["--image", job.image_path])

    if job.mode == GenerationMode.SUBJECT_TO_VIDEO:
        refs = _subject_refs(job)
        if not refs:
            raise ValueError("Subject-to-video requires at least one reference image.")
        cmd.extend(["--ref_image", ",".join(refs)])

    return cmd


def enqueue_job(
    *,
    mode: GenerationMode,
    prompt: str,
    model: str,
    resolution: str,
    duration_sec: int,
    image_path: str | None = None,
    end_image_path: str | None = None,
    ref_images: list[str] | None = None,
    seed: int = 42,
    upscale_1080: bool = False,
    with_audio: bool = False,
    with_music: bool = True,
    dialog: str = "",
    music_mood: str = "ambient",
    visual_style: str = "photoreal",
    voice: str = "en-US-GuyNeural",
    video_path: str | None = None,
) -> Job:
    if model not in MODELS:
        raise ValueError(f"Unknown model: {model}")

    refs = ref_images or []
    resolved_mode = resolve_mode(
        requested=mode,
        image_path=image_path,
        end_image_path=end_image_path,
        ref_images=refs,
    )

    job = Job(
        id=str(uuid.uuid4())[:8],
        mode=resolved_mode,
        prompt=prompt.strip(),
        model=model,
        resolution=resolution,
        frame_num=_frame_count(duration_sec),
        image_path=image_path,
        end_image_path=end_image_path,
        ref_images=refs,
        seed=seed,
        upscale_1080=upscale_1080,
        with_audio=with_audio,
        with_music=with_music,
        dialog=dialog.strip(),
        music_mood=music_mood,
        visual_style=visual_style,
        voice=voice,
        video_path=video_path,
    )
    job_store.save(job)
    return job


def run_job(job_id: str) -> Job:
    job = job_store.get(job_id)
    if not job:
        raise KeyError(job_id)

    job.status = JobStatus.RUNNING
    job.started_at = datetime.now(timezone.utc).isoformat()
    job_store.save(job)

    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = settings.cuda_visible_devices

    try:
        cmd = _build_command(job)
        result = subprocess.run(
            cmd,
            cwd=str(settings.phantom_root),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        job.log_tail = (result.stdout + "\n" + result.stderr)[-4000:]
        if result.returncode != 0:
            raise RuntimeError(job.log_tail or f"Phantom exited with code {result.returncode}")

        suffix = ".png" if job.mode == GenerationMode.TEXT_TO_IMAGE else ".mp4"
        output = settings.outputs_dir / f"{job.id}{suffix}"
        if not output.exists():
            raise RuntimeError("Phantom finished but output file was not created.")

        final_output = output
        if job.upscale_1080:
            job.status = JobStatus.UPSCALING
            job_store.save(job)
            upscaled = settings.outputs_dir / f"{job.id}_1080{output.suffix}"
            upscale_to_1080(output, upscaled)
            final_output = upscaled

        if (job.with_audio or job.with_music) and job.mode != GenerationMode.TEXT_TO_IMAGE:
            job.log_tail = (job.log_tail or "") + "\n→ adding soundtrack"
            job_store.save(job)
            final_output = maybe_add_audio(
                Path(final_output),
                job.prompt,
                job.with_audio,
                job.with_music,
                dialog=job.dialog,
                music_mood=job.music_mood,
                voice=job.voice,
            )

        job.status = JobStatus.COMPLETED
        job.output_path = str(final_output)
        job.completed_at = datetime.now(timezone.utc).isoformat()
    except Exception as exc:  # noqa: BLE001
        job.status = JobStatus.FAILED
        job.error = str(exc)
        job.completed_at = datetime.now(timezone.utc).isoformat()

    job_store.save(job)
    return job
