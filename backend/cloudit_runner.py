from __future__ import annotations

import json
import mimetypes
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from audio import maybe_add_audio
from config import settings
from http_util import http_bytes, http_json, http_ok, http_open
from media_utils import apply_visual_style
from phantom_runner import GenerationMode, Job, JobStatus, job_store
from ssh_phantom_runner import comfy_health, run_ssh_phantom_job, ssh_phantom_health
from upscaler import upscale_to_1080


def _base() -> str:
    return settings.cloudit_video_url.rstrip("/")


def cloudit_health() -> dict:
    comfy = comfy_health()
    phantom = ssh_phantom_health()
    video_ok, video_err = http_ok(f"{_base()}/")

    # Prefer public Comfy+Video when reachable; else SSH Phantom if installed
    if comfy["available"] and video_ok:
        engine = "comfy-wan"
        ready = True
    elif video_ok:
        # Video easy UI up even if comfy public probe failed — still usable
        engine = "comfy-wan"
        ready = True
    elif phantom["available"]:
        engine = "phantom-ssh"
        ready = True
    else:
        engine = "none"
        ready = False

    return {
        "available": ready,
        "url": _base(),
        "engine": engine,
        "comfy": comfy,
        "phantom_ssh": phantom,
        "video_ui_ok": video_ok,
        "video_ui_error": video_err,
        "ai_url": settings.cloudit_ai_url,
    }


def _orient(resolution: str) -> str:
    return "landscape"


def _upload_to_cloudit(local_path: Path) -> str:
    boundary = f"----phantos{int(time.time())}"
    filename = local_path.name
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    file_bytes = local_path.read_bytes()

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode() + file_bytes + f"\r\n--{boundary}--\r\n".encode()

    with http_open(
        f"{_base()}/api/upload",
        method="POST",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        timeout=120,
    ) as resp:
        result = json.loads(resp.read().decode())
    if not result.get("ok"):
        raise RuntimeError(result.get("error") or "Cloudit upload failed")
    return str(result["filename"])


def _download_output(video_url: str, dest: Path) -> None:
    url = video_url if video_url.startswith("http") else f"{_base()}{video_url}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(http_bytes(url, timeout=300))


def _run_comfy_job(job: Job) -> Path:
    if job.mode == GenerationMode.TEXT_TO_IMAGE:
        raise ValueError("Text-to-image needs Phantom SSH when Comfy path is used for video only.")

    mode = "t2v"
    cloud_image = ""
    if job.mode in {GenerationMode.IMAGE_TO_VIDEO, GenerationMode.SUBJECT_TO_VIDEO}:
        mode = "i2v"
        src = job.image_path or (job.ref_images[0] if job.ref_images else None)
        if not src:
            raise ValueError("Image modes require a start or reference image.")
        cloud_image = _upload_to_cloudit(Path(src))

    payload = {
        "prompt": apply_visual_style(job.prompt, job.visual_style),
        "mode": mode,
        "seconds": max(2, min(60, job.frame_num // 16)),
        "orient": _orient(job.resolution),
        "quality": "manus" if job.visual_style == "photoreal" or "14b" in job.model else "fast",
    }
    if cloud_image:
        payload["image"] = cloud_image

    queued = http_json("POST", f"{_base()}/api/generate", payload)
    if not queued.get("ok"):
        raise RuntimeError(queued.get("error") or "Cloudit queue failed")

    remote_id = queued["job_id"]
    job.log_tail = f"engine=comfy job={remote_id} segments={queued.get('segments')}"
    job_store.save(job)

    deadline = time.time() + 7200
    while time.time() < deadline:
        status = http_json("GET", f"{_base()}/api/job/{remote_id}", timeout=60)
        state = status.get("status")
        job.log_tail = status.get("message") or state or job.log_tail
        job_store.save(job)
        if state == "done" and status.get("video_url"):
            output = settings.outputs_dir / f"{job.id}.mp4"
            _download_output(status["video_url"], output)
            return output
        if state == "error":
            raise RuntimeError(status.get("message") or "Comfy generation failed")
        time.sleep(4)

    raise TimeoutError("Comfy job timed out")


def run_cloudit_job(job_id: str) -> Job:
    job = job_store.get(job_id)
    if not job:
        raise KeyError(job_id)

    job.status = JobStatus.RUNNING
    job.started_at = datetime.now(timezone.utc).isoformat()
    job_store.save(job)

    health = cloudit_health()

    if job.mode == GenerationMode.TEXT_TO_IMAGE:
        if not health.get("phantom_ssh", {}).get("available"):
            job.status = JobStatus.FAILED
            job.error = "Text-to-image requires Phantom on Cloudit GPU (SSH)."
            job.completed_at = datetime.now(timezone.utc).isoformat()
            job_store.save(job)
            return job
        return run_ssh_phantom_job(job_id)

    if health.get("engine") == "phantom-ssh":
        return run_ssh_phantom_job(job_id)

    output: Path | None = None
    last_error: Exception | None = None

    if health.get("engine") in {"comfy-wan", "none"} or health.get("video_ui_ok"):
        try:
            output = _run_comfy_job(job)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            job.log_tail = f"comfy failed: {exc}"
            job_store.save(job)

    if output is None and settings.cloudit_fallback_phantom and ssh_phantom_health()["available"]:
        job.log_tail = (job.log_tail or "comfy unavailable") + "\n→ fallback phantom-ssh"
        job_store.save(job)
        return run_ssh_phantom_job(job_id)

    try:
        if output is None:
            raise last_error or RuntimeError(
                "No engine available — ComfyUI down and Phantom SSH not reachable on Cloudit GPU"
            )

        final_output = output
        if job.upscale_1080:
            job.status = JobStatus.UPSCALING
            job_store.save(job)
            upscaled = settings.outputs_dir / f"{job.id}_1080.mp4"
            upscale_to_1080(output, upscaled)
            final_output = upscaled

        if job.with_audio or job.with_music:
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
