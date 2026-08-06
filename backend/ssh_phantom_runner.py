from __future__ import annotations

import json
import shlex
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from audio import maybe_add_audio
from config import settings
from http_util import http_ok
from media_utils import apply_visual_style
from phantom_runner import (
    MODELS,
    RESOLUTIONS,
    GenerationMode,
    Job,
    JobStatus,
    _subject_refs,
    job_store,
)
from upscaler import upscale_to_1080


def comfy_health() -> dict:
    url = settings.cloudit_comfy_url.rstrip("/")
    ok, err = http_ok(f"{url}/system_stats", timeout=8)
    if ok:
        return {"available": True, "url": url}
    return {"available": False, "url": url, "error": err}


def ssh_phantom_health() -> dict:
    host = settings.cloudit_gpu_ssh
    root = settings.remote_phantom_root
    try:
        cmd = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=8",
            host,
            f"test -f {root}/generate.py && echo ok",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=15)
        ok = result.returncode == 0 and "ok" in result.stdout
        return {
            "available": ok,
            "host": host,
            "phantom_root": str(root),
            "error": None if ok else (result.stderr.strip() or "Phantom not found on GPU host"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "host": host, "phantom_root": str(root), "error": str(exc)}


def _ssh(host: str, remote_cmd: str, timeout: int = 7200) -> subprocess.CompletedProcess:
    cmd = ["ssh", "-o", "BatchMode=yes", host, remote_cmd]
    return subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=timeout)


def _scp_to_remote(local: Path, host: str, remote: str) -> None:
    subprocess.run(
        ["scp", "-o", "BatchMode=yes", str(local), f"{host}:{remote}"],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _scp_from_remote(host: str, remote: str, local: Path) -> None:
    local.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["scp", "-o", "BatchMode=yes", f"{host}:{remote}", str(local)],
        check=True,
        capture_output=True,
        text=True,
        timeout=600,
    )


def _remote_job_paths(job: Job) -> tuple[str, str]:
    work = settings.remote_work_dir
    suffix = ".png" if job.mode == GenerationMode.TEXT_TO_IMAGE else ".mp4"
    remote_out = f"{work}/{job.id}{suffix}"
    return str(work), remote_out


def _build_remote_command(job: Job, remote_out: str, remote_images: dict[str, str]) -> str:
    """Build Phantom CLI on GPU host (no ComfyUI)."""
    model = MODELS[job.model]
    task = model["task_map"][job.mode]
    size = RESOLUTIONS.get(job.resolution, RESOLUTIONS["480p"])
    root = settings.remote_phantom_root
    parts = [
        f"cd {shlex.quote(str(root))}",
        "&&",
        "python3 generate.py",
        "--task",
        shlex.quote(task),
        "--size",
        shlex.quote(size),
        "--frame_num",
        str(job.frame_num),
        "--ckpt_dir",
        shlex.quote(str(settings.remote_wan_ckpt_dir)),
        "--phantom_ckpt",
        shlex.quote(str(settings.remote_phantom_ckpt)),
        "--prompt",
        shlex.quote(apply_visual_style(job.prompt, job.visual_style)),
        "--base_seed",
        str(job.seed),
        "--save_file",
        shlex.quote(remote_out),
        "--offload_model",
        "True",
    ]

    if job.mode == GenerationMode.IMAGE_TO_VIDEO:
        parts.extend(["--image", shlex.quote(remote_images["start"])])

    if job.mode == GenerationMode.SUBJECT_TO_VIDEO:
        refs = []
        if "start" in remote_images:
            refs.append(remote_images["start"])
        if "end" in remote_images:
            refs.append(remote_images["end"])
        for key, path in remote_images.items():
            if key.startswith("ref") and path not in refs:
                refs.append(path)
        if not refs:
            refs = _subject_refs(job)
            refs = [remote_images.get("start", p) for p in refs if p]
        parts.extend(["--ref_image", shlex.quote(",".join(refs))])

    return " ".join(parts)


def run_ssh_phantom_job(job_id: str) -> Job:
    job = job_store.get(job_id)
    if not job:
        raise KeyError(job_id)

    host = settings.cloudit_gpu_ssh
    job.status = JobStatus.RUNNING
    job.log_tail = f"engine=phantom-ssh host={host}"
    job_store.save(job)

    try:
        work_dir, remote_out = _remote_job_paths(job)
        _ssh(host, f"mkdir -p {shlex.quote(work_dir)}")

        remote_images: dict[str, str] = {}
        if job.image_path:
            remote_start = f"{work_dir}/{job.id}-start{Path(job.image_path).suffix}"
            _scp_to_remote(Path(job.image_path), host, remote_start)
            remote_images["start"] = remote_start
        if job.end_image_path:
            remote_end = f"{work_dir}/{job.id}-end{Path(job.end_image_path).suffix}"
            _scp_to_remote(Path(job.end_image_path), host, remote_end)
            remote_images["end"] = remote_end
        for idx, ref in enumerate(job.ref_images):
            remote_ref = f"{work_dir}/{job.id}-ref{idx}{Path(ref).suffix}"
            _scp_to_remote(Path(ref), host, remote_ref)
            remote_images[f"ref{idx}"] = remote_ref

        remote_cmd = _build_remote_command(job, remote_out, remote_images)
        job.log_tail = f"phantom-ssh: {remote_cmd[:500]}"
        job_store.save(job)

        result = _ssh(host, remote_cmd, timeout=7200)
        tail = (result.stdout + "\n" + result.stderr)[-4000:]
        if result.returncode != 0:
            raise RuntimeError(tail or f"Remote Phantom exited {result.returncode}")

        local_out = settings.outputs_dir / Path(remote_out).name
        _scp_from_remote(host, remote_out, local_out)

        final_output = local_out
        if job.upscale_1080:
            job.status = JobStatus.UPSCALING
            job_store.save(job)
            upscaled = settings.outputs_dir / f"{job.id}_1080{local_out.suffix}"
            upscale_to_1080(local_out, upscaled)
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
        job.log_tail = tail
        job.completed_at = datetime.now(timezone.utc).isoformat()
    except Exception as exc:  # noqa: BLE001
        job.status = JobStatus.FAILED
        job.error = str(exc)
        job.completed_at = datetime.now(timezone.utc).isoformat()

    job_store.save(job)
    return job
