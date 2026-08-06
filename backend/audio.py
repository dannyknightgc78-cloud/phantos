from __future__ import annotations

import math
import shutil
import struct
import subprocess
import wave
from pathlib import Path


def tts_available() -> bool:
    return bool(
        shutil.which("edge-tts")
        or shutil.which("say")
        or shutil.which("espeak-ng")
        or shutil.which("espeak")
    )


def music_available() -> bool:
    return bool(shutil.which("ffmpeg"))


def _probe_duration(path: Path) -> float:
    if not shutil.which("ffprobe"):
        return 8.0
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        return max(2.0, float(result.stdout.strip()))
    except ValueError:
        return 8.0


def pick_music_mood(prompt: str, explicit: str | None = None) -> str:
    if explicit and explicit in {"tense", "scifi", "calm", "cinematic", "ambient", "none"}:
        return explicit
    text = prompt.lower()
    if any(w in text for w in ("stress", "chaos", "urgent", "panic", "paperwork", "deadline")):
        return "tense"
    if any(w in text for w in ("futur", "ai", "orb", "neon", "cyber", "space", "glow")):
        return "scifi"
    if any(w in text for w in ("calm", "peace", "nature", "soft", "gentle", "warm")):
        return "calm"
    if any(w in text for w in ("epic", "cinematic", "dramatic", "hero")):
        return "cinematic"
    return "ambient"


def synthesize_narration(dialog: str, wav_path: Path, voice: str = "en-US-GuyNeural") -> Path | None:
    """Generate TTS from explicit dialog only (not the scene prompt)."""
    text = dialog.strip()
    if not text:
        return None
    if len(text) > 800:
        text = text[:800]

    wav_path.parent.mkdir(parents=True, exist_ok=True)

    if shutil.which("edge-tts"):
        result = subprocess.run(
            [
                "edge-tts",
                "--voice",
                voice,
                "--text",
                text,
                "--write-media",
                str(wav_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and wav_path.is_file():
            return wav_path

    if shutil.which("say"):
        aiff = wav_path.with_suffix(".aiff")
        result = subprocess.run(
            ["say", "-o", str(aiff), text],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and aiff.is_file() and shutil.which("ffmpeg"):
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(aiff), str(wav_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            aiff.unlink(missing_ok=True)
            if wav_path.is_file():
                return wav_path
        if aiff.is_file():
            return aiff

    for bin_ in ("espeak-ng", "espeak"):
        if shutil.which(bin_):
            result = subprocess.run(
                [bin_, "-w", str(wav_path), text],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0 and wav_path.is_file():
                return wav_path

    return None


def synthesize_music_bed(duration_sec: float, mood: str, out_path: Path) -> Path:
    """Create a free ambient music bed (ffmpeg lavfi, fallback pure-Python pad)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    duration_sec = max(2.0, min(120.0, duration_sec + 0.5))

    # Mood → root freqs + noise color
    beds = {
        "tense": ([110, 165, 220, 277], "pink", 0.06),
        "scifi": ([98, 147, 196, 294], "brown", 0.05),
        "calm": ([130, 164, 196, 261], "pink", 0.04),
        "cinematic": ([82, 123, 164, 246], "brown", 0.07),
        "ambient": ([100, 150, 200, 300], "pink", 0.05),
    }
    freqs, noise, amp = beds.get(mood, beds["ambient"])

    if shutil.which("ffmpeg"):
        inputs: list[str] = []
        labels: list[str] = []
        for i, f in enumerate(freqs):
            inputs.extend(["-f", "lavfi", "-i", f"sine=frequency={f}:duration={duration_sec}:sample_rate=44100"])
            labels.append(f"[{i}]")
        noise_idx = len(freqs)
        inputs.extend(
            [
                "-f",
                "lavfi",
                "-i",
                f"anoisesrc=color={noise}:duration={duration_sec}:amplitude={amp}:sample_rate=44100",
            ]
        )
        labels.append(f"[{noise_idx}]")
        n = len(labels)
        mix = "".join(labels) + f"amix=inputs={n}:duration=longest:dropout_transition=0,"
        mix += "lowpass=f=1200,highpass=f=60,volume=0.35,afade=t=in:st=0:d=1.2,afade=t=out:st="
        mix += f"{max(0.5, duration_sec - 1.5)}:d=1.4"

        result = subprocess.run(
            ["ffmpeg", "-y", *inputs, "-filter_complex", mix, "-c:a", "pcm_s16le", str(out_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and out_path.is_file():
            return out_path

    # Pure-Python fallback pad
    rate = 44100
    samples = int(duration_sec * rate)
    frames = bytearray()
    for n in range(samples):
        t = n / rate
        fade_in = min(1.0, t / 1.2)
        fade_out = min(1.0, max(0.0, (duration_sec - t) / 1.4))
        env = fade_in * fade_out
        val = 0.0
        for f in freqs:
            val += 0.12 * math.sin(2 * math.pi * f * t)
        val += 0.03 * math.sin(2 * math.pi * 0.25 * t)  # slow swell
        sample = int(max(-32767, min(32767, val * env * 16000)))
        frames += struct.pack("<h", sample)

    with wave.open(str(out_path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(frames)
    return out_path


def mux_audio(video_path: Path, audio_path: Path, output_path: Path) -> Path:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg required to mux audio onto video")

    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(audio_path),
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(output_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not output_path.exists():
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(video_path),
                "-i",
                str(audio_path),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                str(output_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stdout + "\n" + result.stderr)[-2000:] or "audio mux failed")

    return output_path


def mix_voice_and_music(
    video_path: Path,
    voice_path: Path | None,
    music_path: Path | None,
    output_path: Path,
) -> Path:
    """Mux video with optional voice + music (music ducked under voice)."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg required for soundtrack")

    if voice_path and music_path:
        # Voice louder, music quieter underneath
        filt = (
            "[1:a]volume=1.0[v];"
            "[2:a]volume=0.28[m];"
            "[v][m]amix=inputs=2:duration=first:dropout_transition=2[a]"
        )
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(voice_path),
            "-i",
            str(music_path),
            "-filter_complex",
            filt,
            "-map",
            "0:v:0",
            "-map",
            "[a]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    elif voice_path:
        return mux_audio(video_path, voice_path, output_path)
    elif music_path:
        return mux_audio(video_path, music_path, output_path)
    else:
        return video_path

    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not output_path.exists():
        retry = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(voice_path),
            "-i",
            str(music_path),
            "-filter_complex",
            filt,
            "-map",
            "0:v:0",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(output_path),
        ]
        result = subprocess.run(retry, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError((result.stdout + "\n" + result.stderr)[-2000:] or "soundtrack mux failed")
    return output_path


def finalize_soundtrack(
    video_path: Path,
    prompt: str,
    *,
    with_audio: bool = True,
    with_music: bool = True,
    dialog: str = "",
    music_mood: str | None = None,
    voice: str = "en-US-GuyNeural",
) -> Path:
    """Add optional dialog VO and/or music bed to a silent video."""
    if video_path.suffix.lower() not in {".mp4", ".mov", ".webm"}:
        return video_path
    if not with_audio and not with_music:
        return video_path

    duration = _probe_duration(video_path)
    voice_path: Path | None = None
    music: Path | None = None

    if with_audio:
        # Only speak explicit dialog — never the scene prompt
        voice_path = synthesize_narration(
            dialog,
            video_path.with_name(f"{video_path.stem}_narration.wav"),
            voice=voice,
        )
        if voice_path is None and not with_music:
            raise RuntimeError(
                "Voice over is ON but dialog is empty. Type the exact lines to speak."
            )

    if with_music:
        mood = pick_music_mood(prompt, music_mood)
        if mood != "none":
            music = synthesize_music_bed(
                duration, mood, video_path.with_name(f"{video_path.stem}_music_{mood}.wav")
            )

    if voice_path is None and music is None:
        return video_path

    out = video_path.with_name(f"{video_path.stem}_soundtrack.mp4")
    return mix_voice_and_music(video_path, voice_path, music, out)


def maybe_add_audio(
    video_path: Path,
    prompt: str,
    with_audio: bool,
    with_music: bool = False,
    *,
    dialog: str = "",
    music_mood: str | None = None,
    voice: str = "en-US-GuyNeural",
) -> Path:
    if not with_audio and not with_music:
        return video_path
    return finalize_soundtrack(
        video_path,
        prompt,
        with_audio=with_audio,
        with_music=with_music,
        dialog=dialog,
        music_mood=music_mood,
        voice=voice,
    )
