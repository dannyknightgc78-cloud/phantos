export type GenerationMode =
  | "text-to-video"
  | "image-to-video"
  | "subject-to-video"
  | "text-to-image";

export type JobStatus = "queued" | "running" | "upscaling" | "completed" | "failed";

export interface JobSummary {
  id: string;
  mode: GenerationMode;
  prompt: string;
  model: string;
  resolution: string;
  status: JobStatus;
  created_at: string;
  completed_at?: string | null;
  output_url?: string | null;
  error?: string | null;
}

export interface HealthResponse {
  status: string;
  execution_backend?: string;
  phantom_root: string;
  phantom_ready: boolean;
  cloudit?: {
    available: boolean;
    url: string;
    engine?: string;
    comfy?: { available: boolean; url: string; error?: string };
    phantom_ssh?: { available: boolean; host: string; error?: string | null };
    ai_url?: string;
    error?: string;
  };
  cloudit_ai_url?: string;
  ffmpeg_ready?: boolean;
  tts_ready?: boolean;
  music_ready?: boolean;
  gpu: {
    available: boolean;
    devices: Array<{ name: string; memory_total: string; memory_used: string }>;
  };
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(detail || res.statusText);
  }
  return res.json() as Promise<T>;
}

export function getHealth() {
  return api<HealthResponse>("/api/health");
}

export function getModels() {
  return api<{ models: Array<{ id: string; label: string }>; resolutions: string[] }>(
    "/api/models",
  );
}

export function getJobs() {
  return api<{ jobs: JobSummary[] }>("/api/jobs");
}

export function getJob(id: string) {
  return api<JobSummary & { log_tail?: string; frame_num?: number }>(`/api/jobs/${id}`);
}

export async function uploadImage(file: File) {
  const body = new FormData();
  body.append("file", file);
  return api<{
    path: string;
    filename: string;
    preview_url: string;
    kind?: "image" | "video";
    frames?: { start?: string; mid?: string; end?: string };
    start_path?: string;
    end_path?: string;
    mid_path?: string;
  }>("/api/upload", {
    method: "POST",
    body,
  });
}

export function useSampleReference() {
  return api<{ path: string; filename: string; preview_url: string }>(
    "/api/assets/use-sample",
    { method: "POST" },
  );
}

export function startGeneration(payload: {
  mode: GenerationMode;
  prompt: string;
  model: string;
  resolution: string;
  duration_sec: number;
  seed: number;
  image_path?: string | null;
  end_image_path?: string | null;
  ref_images?: string[];
  video_path?: string | null;
  upscale_1080?: boolean;
  with_audio?: boolean;
  with_music?: boolean;
  dialog?: string;
  music_mood?: string;
  visual_style?: string;
  voice?: string;
}) {
  return api<{ job_id: string; status: JobStatus; resolved_mode?: GenerationMode }>(
    "/api/generate",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
}
