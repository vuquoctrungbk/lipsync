"""Audio input validation + normalization.

SadTalker's audio encoder expects 16 kHz mono PCM. The encoder is
language-agnostic (mel-spectrogram based), so no Vietnamese-specific handling is
needed here — only format normalization.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .config import MAX_AUDIO_SECONDS
from .ffmpeg_utils import FFmpegError, ffmpeg_exe, ffprobe_exe, run


class AudioError(Exception):
    pass


def probe_duration(path: Path) -> float | None:
    """Return duration in seconds, or None if ffprobe is unavailable."""
    pe = ffprobe_exe()
    if not pe:
        return None
    proc = subprocess.run(
        [pe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return None
    try:
        return float(json.loads(proc.stdout)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError):
        return None


def prepare_audio(input_path: str | Path, work_dir: str | Path,
                  max_seconds: int = MAX_AUDIO_SECONDS) -> Path:
    """Validate and convert audio to 16 kHz mono PCM WAV. Returns the WAV path."""
    src = Path(input_path)
    if not src.exists():
        raise AudioError(f"audio file not found: {src}")

    duration = probe_duration(src)
    if duration is not None:
        if duration <= 0:
            raise AudioError(f"audio has no playable duration: {src.name}")
        if duration > max_seconds:
            raise AudioError(
                f"audio is {duration:.0f}s; the limit is {max_seconds}s. "
                "Trim it and try again."
            )

    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    out = work / "audio_16k_mono.wav"

    try:
        run([
            ffmpeg_exe(), "-y", "-i", str(src),
            "-vn", "-ac", "1", "-ar", "16000", "-acodec", "pcm_s16le",
            str(out),
        ])
    except FFmpegError as exc:
        raise AudioError(f"failed to decode/convert audio: {exc}") from exc

    if not out.exists() or out.stat().st_size == 0:
        raise AudioError("audio conversion produced an empty file")
    return out
