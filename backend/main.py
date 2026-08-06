from __future__ import annotations

import shutil
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path  # noqa: TC003

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from audio import music_available, tts_available
from cloudit_runner import cloudit_health, run_cloudit_job
from config import settings
from media_utils import extract_video_frames
from phantom_runner import (
    ASPECT_PRESETS,
    MODELS,
    RESOLUTIONS,
    GenerationMode,
    JobStatus,
    enqueue_job,
    job_store,
    run_job,
)
from upscaler import ffmpeg_available

app = FastAPI(title="Phantos", version="0.1.0")
executor = ThreadPoolExecutor(max_workers=1)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

settings.outputs_dir.mkdir(parents=True, exist_ok=True)
settings.uploads_dir.mkdir(parents=True, exist_ok=True)


class GenerateRequest(BaseModel):
    mode: GenerationMode
    prompt: str = Field(min_length=3, max_length=4000)
    model: str = settings.default_model
    resolution: str = "480p"
    duration_sec: int = Field(default=4, ge=2, le=60)
    seed: int = 42
    image_path: str | None = None
    end_image_path: str | None = None
    ref_images: list[str] = Field(default_factory=list)
    video_path: str | None = None
    upscale_1080: bool = False
    with_audio: bool = False
    with_music: bool = True
    dialog: str = ""
    music_mood: str = "ambient"
    visual_style: str = "photoreal"
    voice: str = "en-US-GuyNeural"


def _gpu_info() -> dict:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.used", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = [line.split(", ") for line in result.stdout.strip().splitlines()]
            return {
                "available": True,
                "devices": [
                    {"name": name, "memory_total": total, "memory_used": used}
                    for name, total, used in parts
                ],
            }
    except FileNotFoundError:
        pass
    return {"available": False, "devices": []}


def _schedule(job_id: str) -> None:
    if settings.execution_backend.lower() == "cloudit":
        executor.submit(run_cloudit_job, job_id)
    else:
        executor.submit(run_job, job_id)


@app.get("/api/health")
def health() -> dict:
    phantom_ready = (settings.phantom_root / "generate.py").exists()
    cloudit = cloudit_health()
    backend = settings.execution_backend.lower()
    return {
        "status": "ok",
        "execution_backend": backend,
        "phantom_root": str(settings.phantom_root),
        "phantom_ready": phantom_ready,
        "cloudit": cloudit,
        "cloudit_ai_url": settings.cloudit_ai_url,
        "ffmpeg_ready": ffmpeg_available(),
        "tts_ready": tts_available(),
        "music_ready": music_available(),
        "gpu": _gpu_info() if backend == "local" else {"available": True, "devices": [{"name": "Cloudit GPU fleet", "memory_total": "remote", "memory_used": "remote"}]},
    }


@app.get("/api/models")
def models() -> dict:
    return {
        "models": [
            {"id": model_id, "label": meta["label"]}
            for model_id, meta in MODELS.items()
        ],
        "resolutions": list(RESOLUTIONS.keys()),
        "aspect_presets": list(ASPECT_PRESETS.keys()),
        "modes": [mode.value for mode in GenerationMode],
    }


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)) -> dict:
    suffix = Path(file.filename or "upload.png").suffix.lower() or ".png"
    image_ok = suffix in {".png", ".jpg", ".jpeg", ".webp"}
    video_ok = suffix in {".mp4", ".mov", ".webm", ".mkv"}
    if not image_ok and not video_ok:
        raise HTTPException(status_code=400, detail="Unsupported file type.")

    dest = settings.uploads_dir / f"{uuid.uuid4().hex[:10]}{suffix}"
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    result: dict = {
        "path": str(dest),
        "filename": dest.name,
        "preview_url": f"/api/uploads/{dest.name}",
        "kind": "video" if video_ok else "image",
    }

    if video_ok:
        try:
            frames = extract_video_frames(dest)
            result["frames"] = frames
            result["start_path"] = frames.get("start")
            result["end_path"] = frames.get("end")
            result["mid_path"] = frames.get("mid")
            if frames.get("start"):
                start_name = Path(frames["start"]).name
                result["preview_url"] = f"/api/uploads/{start_name}"
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"Video adapt failed: {exc}") from exc

    return result


def _sample_asset_path() -> Path:
    path = settings.assets_dir / settings.sample_reference
    if not path.exists():
        raise HTTPException(status_code=404, detail="Sample reference not found.")
    return path


@app.get("/api/assets/sample-reference")
def sample_reference() -> dict:
    path = _sample_asset_path()
    return {
        "filename": path.name,
        "preview_url": "/api/assets/sample-reference/file",
    }


@app.get("/api/assets/sample-reference/file")
def sample_reference_file():
    path = _sample_asset_path()
    return FileResponse(path, media_type="image/png")


@app.post("/api/assets/use-sample")
def use_sample_reference() -> dict:
    source = _sample_asset_path()
    dest = settings.uploads_dir / f"sample-{uuid.uuid4().hex[:8]}{source.suffix}"
    shutil.copy2(source, dest)
    return {
        "path": str(dest),
        "filename": dest.name,
        "preview_url": f"/api/uploads/{dest.name}",
    }


@app.get("/api/uploads/{filename}")
def uploaded_file(filename: str):
    path = settings.uploads_dir / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Upload not found")
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        media = "image/jpeg"
    elif suffix in {".mp4", ".mov", ".webm", ".mkv"}:
        media = "video/mp4"
    else:
        media = "image/png"
    return FileResponse(path, media_type=media)


@app.post("/api/generate")
def generate(payload: GenerateRequest, background_tasks: BackgroundTasks) -> dict:
    image_path = payload.image_path
    end_image_path = payload.end_image_path
    ref_images = list(payload.ref_images)
    video_path = payload.video_path

    if video_path and Path(video_path).is_file() and not image_path:
        try:
            frames = extract_video_frames(Path(video_path))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"Video adapt failed: {exc}") from exc
        image_path = frames.get("start")
        end_image_path = end_image_path or frames.get("end")
        if frames.get("mid") and frames["mid"] not in ref_images:
            ref_images.append(frames["mid"])

    if payload.with_audio and not (payload.dialog or "").strip():
        raise HTTPException(
            status_code=400,
            detail="Voice over is ON — enter the exact dialog lines to speak (not the scene prompt).",
        )

    try:
        job = enqueue_job(
            mode=payload.mode,
            prompt=payload.prompt,
            model=payload.model,
            resolution=payload.resolution,
            duration_sec=payload.duration_sec,
            image_path=image_path,
            end_image_path=end_image_path,
            ref_images=ref_images,
            seed=payload.seed,
            upscale_1080=payload.upscale_1080,
            with_audio=payload.with_audio,
            with_music=payload.with_music,
            dialog=payload.dialog,
            music_mood=payload.music_mood,
            visual_style=payload.visual_style,
            voice=payload.voice,
            video_path=video_path,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    background_tasks.add_task(_schedule, job.id)
    return {"job_id": job.id, "status": job.status.value, "resolved_mode": job.mode.value}


@app.get("/api/jobs")
def jobs(limit: int = 50) -> dict:
    items = job_store.list_jobs(limit=limit)
    return {
        "jobs": [
            {
                "id": job.id,
                "mode": job.mode.value,
                "prompt": job.prompt,
                "model": job.model,
                "resolution": job.resolution,
                "status": job.status.value,
                "created_at": job.created_at,
                "completed_at": job.completed_at,
                "output_url": f"/api/outputs/{Path(job.output_path).name}" if job.output_path else None,
                "error": job.error,
            }
            for job in items
        ]
    }


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: str) -> dict:
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return {
        "id": job.id,
        "mode": job.mode.value,
        "prompt": job.prompt,
        "model": job.model,
        "resolution": job.resolution,
        "frame_num": job.frame_num,
        "status": job.status.value,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "output_url": f"/api/outputs/{Path(job.output_path).name}" if job.output_path else None,
        "error": job.error,
        "log_tail": job.log_tail,
    }


@app.get("/api/outputs/{filename}")
def output_file(filename: str):
    path = settings.outputs_dir / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Output not found")
    media = "video/mp4" if path.suffix == ".mp4" else "image/png"
    return FileResponse(path, media_type=media)


frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")
