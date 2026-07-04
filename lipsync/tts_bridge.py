"""Bridge to the ISOLATED TTS venv (tools/tts) — main-venv side of the boundary.

Modern TTS stacks need newer transformers/onnx than this app's fragile-pinned
venv tolerates (see the 2026-07-04 fastapi-drift incident), so synthesis runs
in `tools/tts/.venv` and is reached ONLY through tts_cli.py via subprocess —
the same pattern as scripts/sync_metrics.py -> tools/syncnet.

Zero new main-venv deps: stdlib + existing lipsync modules only.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .audio_preprocess import wav_duration_seconds
from .config import MAX_AUDIO_SECONDS, ROOT, TEMP_DIR

TTS_DIR = ROOT / "tools" / "tts"
TTS_PYTHON = TTS_DIR / ".venv" / "Scripts" / "python.exe"
TTS_CLI = TTS_DIR / "tts_cli.py"
VOICES_DIR = ROOT / "voices"
TTS_TEMP_DIR = TEMP_DIR / "tts"

SETUP_HINT = ("Môi trường TTS chưa cài. Chạy:  powershell -ExecutionPolicy Bypass "
              "-File scripts\\setup_tts_env.ps1")

# Measured on this machine (phase-1 benchmark): ~17-19 chars of Vietnamese per
# second of speech. /17 slightly over-estimates duration -> warns early.
# Public: the UI counter uses it for its shorten-to-N-chars hint.
CHARS_PER_SECOND = 17.0

# SDK-bundled preset voices (Apache-2.0). Safe to hardcode: the set ships as
# data inside the PINNED vieneu==3.0.11 wheel — re-verify when bumping the pin
# (tools/tts/requirements.txt).
PRESET_VOICES: tuple[tuple[str, str], ...] = (
    ("Ngọc Lan", "Ngọc Lan — nữ, giọng dịu dàng"),
    ("Mỹ Duyên", "Mỹ Duyên — nữ, giọng mượt mà"),
    ("Trúc Ly", "Trúc Ly — nữ, giọng trẻ trung"),
    ("Ngọc Linh", "Ngọc Linh — nữ, giọng tươi sáng"),
    ("Gia Bảo", "Gia Bảo — nam, giọng mượt mà"),
    ("Thái Sơn", "Thái Sơn — nam, giọng chắc khỏe"),
    ("Đức Trí", "Đức Trí — nam, giọng rõ ràng"),
    ("Xuân Vĩnh", "Xuân Vĩnh — nam, giọng vui tươi"),
    ("Trọng Hữu", "Trọng Hữu — nam, giọng uyên bác"),
    ("Bình An", "Bình An — nam, giọng điềm đạm"),
)

_TEMP_MAX_AGE_S = 2 * 24 * 3600  # best-effort cleanup horizon for temp/tts


class TTSError(Exception):
    """Synthesis failure with a Vietnamese user-facing message."""

    def __init__(self, user_message: str):
        super().__init__(user_message)
        self.user_message = user_message


@dataclass(frozen=True)
class Voice:
    """Either an SDK preset (preset set) or a clone reference (wav set).

    `wav` may arrive as str (gradio filepath) or Path — always coerced at use.
    """
    label: str
    preset: str | None = None
    wav: str | Path | None = None
    transcript: str | None = None


def tts_available() -> bool:
    """True when the isolated TTS venv exists (UI degrades gracefully if not)."""
    return TTS_PYTHON.exists()


def estimate_seconds(text: str) -> float:
    """Rough speech duration for the UI counter (measured vi speaking rate)."""
    return len(text.strip()) / CHARS_PER_SECOND


def list_voices(language: str = "vi") -> list[Voice]:
    """SDK presets first, then user clone wavs from voices/<lang>/ (+.txt sidecar)."""
    voices = [Voice(label=label, preset=name) for name, label in PRESET_VOICES]
    lang_dir = VOICES_DIR / language
    if lang_dir.is_dir():
        for wav in sorted(lang_dir.glob("*.wav")):
            sidecar = wav.with_suffix(".txt")
            transcript = None
            if sidecar.exists():
                # User-writable drop dir: a mis-encoded/locked sidecar must not
                # take the app down at build_ui time — skip the transcript only.
                try:
                    transcript = sidecar.read_text(encoding="utf-8").strip() or None
                except (OSError, UnicodeDecodeError) as exc:
                    print(f"[tts] bỏ qua transcript hỏng {sidecar.name}: {exc}",
                          file=sys.stderr)
            voices.append(Voice(label=f"{wav.stem} (giọng của bạn)",
                                wav=wav, transcript=transcript))
    return voices


def _cleanup_old_temp() -> None:
    """Best-effort purge of stale synth wavs/texts; never raises."""
    cutoff = time.time() - _TEMP_MAX_AGE_S
    try:
        for p in TTS_TEMP_DIR.glob("tts_*"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
            except OSError:
                pass
    except OSError:
        pass


def _parse_last_json_line(stdout: str) -> dict | None:
    """Newest parseable JSON line wins; junk '{'-lines (SDK stdout noise,
    partial writes) are skipped, not fatal — a finished synth must not be
    reported as failed because of an interleaved log line."""
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


def synthesize(text: str, voice: Voice, *, engine: str = "vieneu",
               model: str = "v3turbo", language: str = "vi",
               out_dir: Path | None = None, timeout_s: int = 1800,
               seed: int | None = None) -> Path:
    """Text -> wav via the isolated TTS CLI. Returns the wav path.

    Raises TTSError (Vietnamese user message) on any failure. The 600 s cap is
    enforced on the DECODED result wav — same fail-closed stance as
    prepare_audio().
    """
    text = (text or "").strip()
    if not text:
        raise TTSError("Văn bản trống — hãy nhập nội dung cần đọc.")
    if not tts_available():
        raise TTSError(SETUP_HINT)
    est = estimate_seconds(text)
    if est > MAX_AUDIO_SECONDS * 1.3:  # obvious over-length: reject before synth
        raise TTSError(
            f"Văn bản quá dài (ước tính ~{est:.0f}s giọng nói, giới hạn "
            f"{MAX_AUDIO_SECONDS}s). Hãy rút ngắn còn khoảng "
            f"{int(MAX_AUDIO_SECONDS * CHARS_PER_SECOND)} ký tự.")
    if voice.preset is None and (voice.wav is None or not Path(voice.wav).exists()):
        raise TTSError("Giọng tham chiếu không tồn tại — chọn giọng khác hoặc tải lại file.")

    out_root = Path(out_dir) if out_dir else TTS_TEMP_DIR
    out_root.mkdir(parents=True, exist_ok=True)
    _cleanup_old_temp()
    run_id = f"{time.strftime('%y%m%d-%H%M%S')}_{uuid.uuid4().hex[:6]}"
    text_file = out_root / f"tts_{run_id}.txt"
    out_wav = out_root / f"tts_{run_id}.wav"
    text_file.write_text(text, encoding="utf-8")

    cmd = [str(TTS_PYTHON), str(TTS_CLI),
           "--engine", engine, "--model", model,
           "--text-file", str(text_file), "--language", language,
           "--out", str(out_wav)]
    if voice.preset is not None:  # `is None` everywhere: ""-preset routes to CLI's own XOR error
        cmd += ["--voice", voice.preset]
    else:
        cmd += ["--voice-ref", str(voice.wav)]
        if voice.transcript:
            cmd += ["--voice-ref-text", voice.transcript]
    if seed is not None:
        cmd += ["--seed", str(seed)]

    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}  # keep CLI JSON/stderr UTF-8-safe
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=timeout_s, env=env)
    except subprocess.TimeoutExpired as exc:
        raise TTSError(
            f"TTS quá thời gian ({timeout_s}s) — hãy thử văn bản ngắn hơn.") from exc

    result = _parse_last_json_line(proc.stdout or "")
    if proc.returncode != 0 or not result or not result.get("ok"):
        if result and result.get("error"):
            raise TTSError(f"TTS lỗi ({result.get('kind', '?')}): {result['error']}")
        tail = ((proc.stderr or proc.stdout or "no output").strip())[-400:]
        raise TTSError(f"TTS thất bại (exit {proc.returncode}):\n{tail}")

    if not out_wav.exists() or out_wav.stat().st_size == 0:
        raise TTSError("TTS báo thành công nhưng không tạo được file audio.")
    duration = wav_duration_seconds(out_wav)
    if duration > MAX_AUDIO_SECONDS:
        out_wav.unlink(missing_ok=True)
        raise TTSError(
            f"Audio TTS dài {duration:.0f}s, vượt giới hạn {MAX_AUDIO_SECONDS}s "
            "— hãy rút ngắn văn bản.")
    return out_wav
