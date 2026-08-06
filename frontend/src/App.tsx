import { useCallback, useEffect, useMemo, useState } from "react";
import {
  GenerationMode,
  JobSummary,
  getHealth,
  getJob,
  getJobs,
  getModels,
  startGeneration,
  uploadImage,
  useSampleReference,
} from "./lib/api";
import { estimateSeconds, formatDuration, formatEtaLabel } from "./lib/eta";

type Tab = "create" | "edit" | "motion";
type ImageSlot = { path: string; preview: string };

const DEFAULT_PROMPT =
  "Man in office looking stressed out with piles of paperwork and a screen full of emails and tasks. Then a futuristic AI appears, sends a glowing orb at the desk, the paperwork disappears into confetti.";

export default function App() {
  const [tab, setTab] = useState<Tab>("create");
  const [prompt, setPrompt] = useState(DEFAULT_PROMPT);
  const [model, setModel] = useState("phantom-wan-1.3b");
  const [resolution, setResolution] = useState("480p");
  const [duration, setDuration] = useState(10);
  const [seed, setSeed] = useState(42);
  const [models, setModels] = useState<Array<{ id: string; label: string }>>([]);
  const [resolutions, setResolutions] = useState<string[]>(["480p", "720p"]);
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [activeOutput, setActiveOutput] = useState<string | null>(null);
  const [statusText, setStatusText] = useState("Ready");
  const [generating, setGenerating] = useState(false);
  const [health, setHealth] = useState<string>("Checking GPU…");
  const [startImage, setStartImage] = useState<ImageSlot | null>(null);
  const [endImage, setEndImage] = useState<ImageSlot | null>(null);
  const [refImages, setRefImages] = useState<ImageSlot[]>([]);
  const [upscale1080, setUpscale1080] = useState(false);
  const [withAudio, setWithAudio] = useState(false);
  const [withMusic, setWithMusic] = useState(true);
  const [dialog, setDialog] = useState("");
  const [musicMood, setMusicMood] = useState("ambient");
  const [visualStyle, setVisualStyle] = useState("photoreal");
  const [adaptVideo, setAdaptVideo] = useState<ImageSlot | null>(null);
  const [ffmpegReady, setFfmpegReady] = useState(true);
  const [ttsReady, setTtsReady] = useState(true);
  const [musicReady, setMusicReady] = useState(true);
  const [backend, setBackend] = useState("cloudit");
  const [clouditUrl, setClouditUrl] = useState("https://video.cloudsit.app");
  const [etaTotalSec, setEtaTotalSec] = useState<number | null>(null);
  const [etaRemainingSec, setEtaRemainingSec] = useState<number | null>(null);
  const [jobStartedAt, setJobStartedAt] = useState<number | null>(null);

  const mode: GenerationMode = useMemo(() => {
    if (tab === "edit") return "text-to-image";
    if (startImage && endImage) return "subject-to-video";
    if (tab === "motion" && (startImage || refImages.length)) return "subject-to-video";
    if (startImage || adaptVideo) return "image-to-video";
    return "text-to-video";
  }, [tab, startImage, endImage, refImages.length, adaptVideo]);

  const predictedEtaSec = useMemo(
    () =>
      estimateSeconds({
        mode,
        model,
        resolution,
        duration_sec: duration,
        upscale_1080: upscale1080,
      }),
    [mode, model, resolution, duration, upscale1080],
  );

  const refreshJobs = useCallback(async () => {
    const data = await getJobs();
    setJobs(data.jobs);
    const latest = data.jobs.find((j) => j.status === "completed" && j.output_url);
    if (latest?.output_url && !activeOutput) setActiveOutput(latest.output_url);
  }, [activeOutput]);

  useEffect(() => {
    void (async () => {
      const [modelData, healthData] = await Promise.all([getModels(), getHealth()]);
      setModels(modelData.models);
      setResolutions(modelData.resolutions);
      setBackend(healthData.execution_backend || "cloudit");
      setClouditUrl(healthData.cloudit?.url || "https://video.cloudsit.app");
      const engine = healthData.cloudit?.engine || "unknown";
      const engineLabel =
        engine === "comfy-wan"
          ? "ComfyUI · Wan 14B"
          : engine === "phantom-ssh"
            ? "Phantom AI (Comfy bypass)"
            : "Cloudit GPU";
      const gpu =
        healthData.execution_backend === "local"
          ? healthData.gpu.available
            ? healthData.gpu.devices.map((d) => d.name).join(", ")
            : "No GPU detected"
          : healthData.cloudit?.available
            ? engineLabel
            : `Cloudit offline (${healthData.cloudit?.comfy?.error || healthData.cloudit?.phantom_ssh?.error || "unreachable"})`;
      setHealth(
        healthData.execution_backend === "local"
          ? healthData.phantom_ready
            ? `Phantom ready · ${gpu}${healthData.ffmpeg_ready === false ? " · ffmpeg missing" : ""}`
            : `Phantom not installed at ${healthData.phantom_root}`
          : `${gpu}${healthData.ffmpeg_ready === false ? " · ffmpeg missing for 1080 upscale" : ""}`,
      );
      setFfmpegReady(healthData.ffmpeg_ready !== false);
      setTtsReady(healthData.tts_ready !== false);
      setMusicReady(healthData.music_ready !== false);
      await refreshJobs();
    })();
  }, [refreshJobs]);

  useEffect(() => {
    if (!generating || !jobStartedAt || etaTotalSec === null) {
      if (!generating) setEtaRemainingSec(null);
      return;
    }

    const tick = () => {
      const elapsed = Math.floor((Date.now() - jobStartedAt) / 1000);
      setEtaRemainingSec(Math.max(0, etaTotalSec - elapsed));
    };

    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, [generating, jobStartedAt, etaTotalSec]);

  useEffect(() => {
    if (!activeJobId) return;
    const timer = window.setInterval(async () => {
      const job = await getJob(activeJobId);
      setStatusText(
        job.status === "upscaling"
          ? `Upscaling to 1080p…`
          : `${job.status}${job.error ? `: ${job.error}` : ""}`,
      );
      if (job.status === "completed" && job.output_url) {
        setActiveOutput(job.output_url);
        setGenerating(false);
        setActiveJobId(null);
        setEtaRemainingSec(0);
        setJobStartedAt(null);
        await refreshJobs();
      }
      if (job.status === "failed") {
        setGenerating(false);
        setActiveJobId(null);
        setEtaRemainingSec(null);
        setJobStartedAt(null);
        await refreshJobs();
      }
    }, 2500);
    return () => window.clearInterval(timer);
  }, [activeJobId, refreshJobs]);

  async function onPickImage(kind: "start" | "end" | "ref" | "video", file: File | null) {
    if (!file) return;
    const uploaded = await uploadImage(file);
    const preview = uploaded.preview_url || URL.createObjectURL(file);

    if (kind === "video" || uploaded.kind === "video") {
      setAdaptVideo({ path: uploaded.path, preview });
      if (uploaded.start_path) {
        setStartImage({
          path: uploaded.start_path,
          preview: `/api/uploads/${uploaded.start_path.split(/[/\\]/).pop()}`,
        });
      }
      if (uploaded.end_path) {
        setEndImage({
          path: uploaded.end_path,
          preview: `/api/uploads/${uploaded.end_path.split(/[/\\]/).pop()}`,
        });
      }
      if (uploaded.mid_path) {
        setRefImages((prev) => [
          ...prev,
          {
            path: uploaded.mid_path!,
            preview: `/api/uploads/${uploaded.mid_path!.split(/[/\\]/).pop()}`,
          },
        ]);
      }
      setStatusText("Video frames extracted — adapting from start/end");
      return;
    }

    const slot = { path: uploaded.path, preview };
    if (kind === "start") setStartImage(slot);
    else if (kind === "end") setEndImage(slot);
    else setRefImages((prev) => [...prev, slot]);
  }

  async function onUseSample(as: "start" | "ref") {
    const sample = await useSampleReference();
    const slot = { path: sample.path, preview: sample.preview_url };
    if (as === "start") setStartImage(slot);
    else setRefImages((prev) => [...prev, slot]);
    setStatusText("Loaded sample reference (IMG_4204.png)");
  }

  async function onGenerate() {
    const eta = estimateSeconds({
      mode,
      model,
      resolution,
      duration_sec: duration,
      upscale_1080: upscale1080,
    });
    setEtaTotalSec(eta);
    setEtaRemainingSec(eta);
    setJobStartedAt(Date.now());
    setGenerating(true);
    setStatusText(`Queued… ${formatEtaLabel(eta)}`);
    try {
      const result = await startGeneration({
        mode,
        prompt,
        model,
        resolution,
        duration_sec: duration,
        seed,
        image_path: startImage?.path ?? null,
        end_image_path: endImage?.path ?? null,
        ref_images: refImages.map((r) => r.path),
        video_path: adaptVideo?.path ?? null,
        upscale_1080: upscale1080,
        with_audio: withAudio,
        with_music: withMusic,
        dialog,
        music_mood: musicMood,
        visual_style: visualStyle,
      });
      setActiveJobId(result.job_id);
      setStatusText(
        `Job ${result.job_id} ${result.status}${result.resolved_mode ? ` · ${result.resolved_mode}` : ""}`,
      );
    } catch (err) {
      setGenerating(false);
      setEtaRemainingSec(null);
      setJobStartedAt(null);
      setStatusText(err instanceof Error ? err.message : "Generation failed");
    }
  }

  const etaDisplay = generating && etaRemainingSec !== null
    ? ` · ${formatDuration(etaRemainingSec)} left`
    : ` · est. ${formatDuration(predictedEtaSec)}`;

  const appUrl = typeof window !== "undefined" ? window.location.origin : "http://localhost:5173";

  const isVideo = mode !== "text-to-image";

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">P</div>
          <div>
            <strong>Phantos</strong>
            <span>Local Phantom Studio</span>
          </div>
        </div>

        <nav className="nav">
          <button className={tab === "create" ? "active" : ""} onClick={() => setTab("create")}>
            Create Video
          </button>
          <button className={tab === "edit" ? "active" : ""} onClick={() => setTab("edit")}>
            Create Image
          </button>
          <button className={tab === "motion" ? "active" : ""} onClick={() => setTab("motion")}>
            Subject Video
          </button>
        </nav>

        <div className="sidebar-card">
          <div className="label">Account</div>
          <strong>daniel knight</strong>
          <div className="muted">{backend === "local" ? "Local GPU" : "Cloudit GPU · Wan + AI"}</div>
        </div>

        <div className="sidebar-card health">{health}</div>

        <div className="sidebar-card urls">
          <div className="label">URLs</div>
          <a href={appUrl} target="_blank" rel="noreferrer">
            Phantos · {appUrl.replace("http://", "")}
          </a>
          <a href={clouditUrl} target="_blank" rel="noreferrer">
            Cloudit Video · {clouditUrl.replace("https://", "")}
          </a>
          <a href="https://comfy.cloudsit.app/" target="_blank" rel="noreferrer">
            ComfyUI · comfy.cloudsit.app
          </a>
          <a href="https://ai.dannygc.cloud/v1" target="_blank" rel="noreferrer">
            Cloudit AI · ai.dannygc.cloud
          </a>
        </div>

        <div className="sample-actions">
          <div className="label">Quick refs</div>
          <button type="button" onClick={() => void onUseSample("start")}>
            Use sample as Start
          </button>
          <button type="button" onClick={() => void onUseSample("ref")}>
            Use sample as Reference
          </button>
        </div>

        <div className="history">
          <div className="label">Recent</div>
          {jobs.slice(0, 8).map((job) => (
            <button
              key={job.id}
              className={`history-item ${job.id === activeJobId ? "active" : ""}`}
              onClick={() => job.output_url && setActiveOutput(job.output_url)}
            >
              <span>{job.mode}</span>
              <small>{job.status}</small>
            </button>
          ))}
        </div>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <div className="tabs-inline">
            <span className={tab === "create" ? "pill active" : "pill"}>Create Video</span>
            <span className={tab === "edit" ? "pill active" : "pill"}>Edit Video</span>
            <span className={tab === "motion" ? "pill active" : "pill"}>Motion Control</span>
            <button
              type="button"
              className={`pill sound-pill ${withAudio ? "active" : ""}`}
              disabled={!ttsReady}
              onClick={() => setWithAudio((v) => !v)}
            >
              Voice over {withAudio ? "ON" : "OFF"}
            </button>
            <button
              type="button"
              className={`pill sound-pill ${withMusic ? "active" : ""}`}
              disabled={!musicReady}
              onClick={() => setWithMusic((v) => !v)}
            >
              Music {withMusic ? "ON" : "OFF"}
            </button>
            <button
              type="button"
              className={`pill sound-pill ${upscale1080 ? "active" : ""}`}
              disabled={!ffmpegReady}
              onClick={() => setUpscale1080((v) => !v)}
            >
              1080p {upscale1080 ? "ON" : "OFF"}
            </button>
          </div>
          <div className="status">
            {statusText}
            {generating && etaRemainingSec !== null ? (
              <span className="eta-countdown">{formatDuration(etaRemainingSec)} left</span>
            ) : null}
          </div>
        </header>

        <section className="preview-panel">
          {activeOutput ? (
            isVideo || activeOutput.endsWith(".mp4") ? (
              <video src={activeOutput} controls autoPlay loop className="preview-media" />
            ) : (
              <img src={activeOutput} alt="Generated output" className="preview-media" />
            )
          ) : (
            <div className="preview-empty">
              <h2>Generate with Phantos</h2>
              <p>Cloudit GPUs — ComfyUI when up, Phantom AI on GPU when Comfy is down.</p>
            </div>
          )}
        </section>

        <section className="composer">
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="Describe your scene…"
            rows={3}
          />

          {withAudio ? (
            <textarea
              className="dialog-input"
              value={dialog}
              onChange={(e) => setDialog(e.target.value)}
              placeholder='Voice over dialog only — e.g. "Too much work… wait — what is that orb?"'
              rows={2}
            />
          ) : null}

          <div className="upload-row upload-row-4">
            <label className="upload-slot">
              <span>Start Image</span>
              {startImage ? <img src={startImage.preview} alt="" /> : <em>+</em>}
              <input
                type="file"
                accept="image/*"
                hidden
                onChange={(e) => void onPickImage("start", e.target.files?.[0] ?? null)}
              />
            </label>
            <label className="upload-slot">
              <span>End Image</span>
              {endImage ? <img src={endImage.preview} alt="" /> : <em>+</em>}
              <input
                type="file"
                accept="image/*"
                hidden
                onChange={(e) => void onPickImage("end", e.target.files?.[0] ?? null)}
              />
            </label>
            <label className="upload-slot">
              <span>Add References</span>
              {refImages[0] ? <img src={refImages[0].preview} alt="" /> : <em>+</em>}
              <input
                type="file"
                accept="image/*"
                hidden
                onChange={(e) => void onPickImage("ref", e.target.files?.[0] ?? null)}
              />
            </label>
            <label className="upload-slot">
              <span>Adapt Video</span>
              {adaptVideo ? <img src={adaptVideo.preview} alt="" /> : <em>▶</em>}
              <input
                type="file"
                accept="video/mp4,video/quicktime,video/webm,video/*"
                hidden
                onChange={(e) => void onPickImage("video", e.target.files?.[0] ?? null)}
              />
            </label>
          </div>

          {adaptVideo ? (
            <div className="hint">
              Video uploaded — start/end frames extracted for image-to-video adapt.
            </div>
          ) : null}

          {startImage && endImage ? (
            <div className="hint">
              Start + End detected — Phantos will use subject-to-video with both frames as references.
            </div>
          ) : null}

          {upscale1080 ? (
            <div className="hint">
              Generates at native resolution, then ffmpeg upscales to 1920×1080 with letterboxing if needed.
            </div>
          ) : null}

          <div className="sound-bar">
            <div className="sound-bar-label">Soundtrack + HD</div>
            <button
              type="button"
              className={`sound-tab ${withAudio ? "on" : ""}`}
              disabled={!ttsReady}
              onClick={() => setWithAudio((v) => !v)}
            >
              Voice over {withAudio ? "ON" : "OFF"}
            </button>
            <button
              type="button"
              className={`sound-tab ${withMusic ? "on" : ""}`}
              disabled={!musicReady}
              onClick={() => setWithMusic((v) => !v)}
            >
              Music {withMusic ? "ON" : "OFF"}
            </button>
            {withMusic ? (
              <select
                className="mood-select"
                value={musicMood}
                onChange={(e) => setMusicMood(e.target.value)}
              >
                <option value="ambient">Ambient</option>
                <option value="tense">Tense</option>
                <option value="scifi">Sci-fi</option>
                <option value="calm">Calm</option>
                <option value="cinematic">Cinematic</option>
              </select>
            ) : null}
            <button
              type="button"
              className={`sound-tab ${upscale1080 ? "on" : ""}`}
              disabled={!ffmpegReady}
              onClick={() => setUpscale1080((v) => !v)}
            >
              1080p {upscale1080 ? "ON" : "OFF"}
            </button>
            <span className="sound-hint">
              {[
                withAudio ? "dialog VO" : null,
                withMusic ? musicMood : null,
                upscale1080 ? "1080p upscale" : null,
              ]
                .filter(Boolean)
                .join(" · ") || "Silent · native res"}
            </span>
          </div>

          <div className="controls-row">
            <select value={visualStyle} onChange={(e) => setVisualStyle(e.target.value)}>
              <option value="photoreal">Photoreal</option>
              <option value="cinematic">Cinematic</option>
              <option value="anime">Anime</option>
              <option value="raw">Raw prompt</option>
            </select>
            <select value={model} onChange={(e) => setModel(e.target.value)}>
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label}
                </option>
              ))}
            </select>
            <select value={resolution} onChange={(e) => setResolution(e.target.value)}>
              {resolutions.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
            <select value={duration} onChange={(e) => setDuration(Number(e.target.value))}>
              {[5, 10, 15, 20, 30, 45, 60].map((d) => (
                <option key={d} value={d}>
                  {d}s
                </option>
              ))}
            </select>
            <select value={seed} onChange={(e) => setSeed(Number(e.target.value))}>
              <option value={42}>Seed 42</option>
              <option value={7}>Seed 7</option>
              <option value={99}>Seed 99</option>
            </select>
            <div className="mode-badge">
              {mode}
              <span className="eta-hint">{etaDisplay}</span>
            </div>
            <button
              className="generate"
              disabled={
                generating ||
                prompt.trim().length < 3 ||
                (withAudio && dialog.trim().length < 1)
              }
              onClick={() => void onGenerate()}
            >
              {generating
                ? etaRemainingSec !== null
                  ? `Generating… ${formatDuration(etaRemainingSec)}`
                  : "Generating…"
                : `Generate · ~${formatDuration(predictedEtaSec)}`}
            </button>
          </div>
        </section>
      </main>
    </div>
  );
}
