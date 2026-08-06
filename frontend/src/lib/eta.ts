import type { GenerationMode } from "./api";

/** Estimated seconds for RTX Pro 6000 class GPU (Wan/Phantom benchmarks). */
export function estimateSeconds(input: {
  mode: GenerationMode;
  model: string;
  resolution: string;
  duration_sec: number;
  upscale_1080?: boolean;
}): number {
  if (input.mode === "text-to-image") {
    let seconds = input.model.includes("14b") ? 150 : 90;
    if (input.upscale_1080) seconds += 30;
    return seconds;
  }

  // Baseline: 4s text-to-video @ 480p on 1.3B ≈ 4.5 min
  let seconds = 270 * (input.duration_sec / 4);

  if (input.resolution === "720p") seconds *= 7.5;
  if (input.model.includes("14b")) seconds *= 3.5;
  if (input.mode === "image-to-video" || input.mode === "subject-to-video") seconds *= 1.2;
  if (input.upscale_1080) seconds += 45 + input.duration_sec * 12;

  return Math.round(seconds);
}

export function formatDuration(totalSeconds: number): string {
  const clamped = Math.max(0, totalSeconds);
  const mins = Math.floor(clamped / 60);
  const secs = clamped % 60;
  if (mins === 0) return `${secs}s`;
  return `${mins}m ${secs.toString().padStart(2, "0")}s`;
}

export function formatEtaLabel(totalSeconds: number, prefix = "ETA"): string {
  return `${prefix} ~${formatDuration(totalSeconds)}`;
}
