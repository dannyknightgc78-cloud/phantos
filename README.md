# Phantos

DaVinci-style AI video studio. Default engine: **Cloudit GPUs** via ComfyUI Wan (`video.cloudsit.app`). Optional local/SSH Phantom for on-box RTX GPUs.

## Quick start

```bash
cp .env.example .env
cd frontend && npm install && cd ..
bash scripts/run.sh
```

- UI: http://localhost:5173  
- API: http://localhost:8787/api/health  

## Features

- Text → video, image → video, subject-consistent video, text → image
- **Photoreal** (default), cinematic, anime, or raw prompt styles
- **Dialog-only voice over** — TTS speaks the dialog field, never the scene prompt
- **Music moods**: ambient, tense, sci-fi, calm, cinematic
- **Adapt from video** — upload mp4/mov; start/end frames extracted for i2v
- 1080p ffmpeg upscale toggle
- Dark DaVinci-like studio UI + job history

## Cloudit stack

| Service | URL |
|---------|-----|
| Phantos UI (local) | http://localhost:5173 |
| Cloudit Video | https://video.cloudsit.app |
| ComfyUI | https://comfy.cloudsit.app |
| Cloudit AI | https://ai.dannygc.cloud/v1 |

Set `EXECUTION_BACKEND=cloudit` in `.env` (default). No local NVIDIA required on the Mac.

## Local Phantom (optional)

Set `EXECUTION_BACKEND=local` and install Phantom on a GPU box:

```bash
bash scripts/setup-phantom.sh
```

## Layout

```
backend/     FastAPI (:8787)
frontend/    React + Vite (:5173)
scripts/     run + Phantom setup
assets/      sample reference images
outputs/     generated media
uploads/     start / ref / adapt uploads
```

## API sketch

```bash
curl http://localhost:8787/api/health

curl -X POST http://localhost:8787/api/generate \
  -H 'Content-Type: application/json' \
  -d '{
    "mode":"text-to-video",
    "prompt":"A man at a cluttered desk as a glowing AI orb arrives",
    "model":"phantom-wan-1.3b",
    "resolution":"480p",
    "duration_sec":10,
    "visual_style":"photoreal",
    "with_music":true,
    "music_mood":"scifi",
    "with_audio":true,
    "dialog":"Too much work… wait — what is that?"
  }'
```

## Notes

- One generation job at a time (GPU queue).
- Voice over requires non-empty `dialog` when enabled.
- Phantom/CUDA is only needed for `EXECUTION_BACKEND=local` or SSH fallback.
