from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # cloudit = try Comfy video API, fall back to Phantom on GPU via SSH
    execution_backend: str = "cloudit"
    cloudit_video_url: str = "https://video.cloudsit.app"
    cloudit_comfy_url: str = "https://comfy.cloudsit.app"
    cloudit_ai_url: str = "https://ai.dannygc.cloud/v1"
    cloudit_gpu_ssh: str = "temp-gpu"
    cloudit_fallback_phantom: bool = True

    # Remote Phantom paths on cloudit GPU (SSH target)
    remote_phantom_root: Path = Path("/data/Phantom")
    remote_wan_ckpt_dir: Path = Path("/data/Phantom/Wan2.1-T2V-1.3B")
    remote_phantom_ckpt: Path = Path("/data/Phantom/Phantom-Wan-Models/Phantom-Wan-1.3B.pth")
    remote_work_dir: Path = Path("/data/phantos-jobs")

    # Local Phantom (EXECUTION_BACKEND=local only)
    phantom_root: Path = Path.home() / "Phantom"
    wan_ckpt_dir: Path = Path.home() / "Phantom" / "Wan2.1-T2V-1.3B"
    phantom_ckpt: Path = Path.home() / "Phantom" / "Phantom-Wan-Models" / "Phantom-Wan-1.3B.pth"
    python_bin: str = "python"
    cuda_visible_devices: str = "0"
    default_model: str = "phantom-wan-14b"
    host: str = "0.0.0.0"
    port: int = 8787
    outputs_dir: Path = Path(__file__).resolve().parent.parent / "outputs"
    uploads_dir: Path = Path(__file__).resolve().parent.parent / "uploads"
    assets_dir: Path = Path(__file__).resolve().parent.parent / "assets"
    sample_reference: str = "IMG_4204.png"
    jobs_db: Path = Path(__file__).resolve().parent.parent / "outputs" / "jobs.json"


settings = Settings()
