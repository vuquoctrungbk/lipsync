"""TTS CLI — the ONLY doorway between the app and the isolated TTS venv.

Called by lipsync/tts_bridge.py via subprocess (pattern: scripts/sync_metrics.py).
Contract (FROZEN — phase-01 plan doc):

  tools/tts/.venv/Scripts/python.exe tools/tts/tts_cli.py \
    --engine vieneu --model v3turbo [--device cpu|cuda|auto] \
    --text-file <utf8.txt> --language vi \
    (--voice "<preset name>" | --voice-ref <ref.wav> [--voice-ref-text "<transcript>"]) \
    --out <out.wav> [--seed N]

Voice: EITHER a named SDK preset (--voice, see engine list_presets) OR a
reference wav for zero-shot cloning (--voice-ref, transcript optional).
Device: cpu (default, torch-free ONNX) | cuda (PyTorch, needs the -Gpu extras) |
auto (SDK picks). GPU is opt-in; CPU keeps today's behavior exactly.

Last stdout line is exactly one JSON object:
  ok:    {"ok": true, "out": "...", "duration_s": 12.3, "engine": "vieneu",
          "model": "v3turbo", "device": "cpu", "synth_s": 8.1, "load_s": 4.2, "chunks": 3}
  error: {"ok": false, "kind": "input|engine_load|synthesis", "error": "..."} + exit 1

All logs go to stderr; stdout stays machine-parseable.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import wave
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Keep HuggingFace downloads inside the repo (models/ is gitignored), never in
# the user profile. Must be set BEFORE any engine SDK import.
os.environ.setdefault("HF_HOME", str(REPO_ROOT / "models" / "tts" / "hf"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

import numpy as np  # noqa: E402  (venv guarantees it via engine deps)

from engines import ENGINES  # noqa: E402
from engines.base_engine import (  # noqa: E402
    KIND_INPUT, KIND_SYNTHESIS, TTSEngineError,
)

# Chunking: keep requests engine-friendly. Sentences joined up to this many
# characters per synthesis call; longer lone sentences are hard-split on spaces.
# 380 stays under VieNeu's internal max_chars=384 so the SDK never re-splits.
MAX_CHUNK_CHARS = 380
JOIN_SILENCE_SECONDS = 0.25


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def split_text(text: str) -> list[str]:
    """Sentence-split then greedily merge into chunks <= MAX_CHUNK_CHARS."""
    text = re.sub(r"\s+", " ", text).strip()
    sentences = [s.strip() for s in re.split(r"(?<=[.!?…;])\s+", text) if s.strip()]
    if not sentences:
        return []
    chunks: list[str] = []
    cur = ""
    for s in sentences:
        while len(s) > MAX_CHUNK_CHARS:  # pathological unpunctuated run
            cut = s.rfind(" ", 0, MAX_CHUNK_CHARS)
            cut = cut if cut > 0 else MAX_CHUNK_CHARS
            head, s = s[:cut].strip(), s[cut:].strip()
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(head)
        if not cur:
            cur = s
        elif len(cur) + 1 + len(s) <= MAX_CHUNK_CHARS:
            cur = f"{cur} {s}"
        else:
            chunks.append(cur)
            cur = s
    if cur:
        chunks.append(cur)
    return chunks


def write_wav(path: Path, samples: np.ndarray, sr: int) -> None:
    """float32 mono [-1,1] -> 16-bit PCM wav via stdlib (no soundfile dep here)."""
    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def fail(kind: str, message: str) -> "NoReturn":  # noqa: F821
    emit({"ok": False, "kind": kind, "error": message})
    sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser(description="Synthesize speech (isolated TTS venv).")
    ap.add_argument("--engine", required=True, choices=sorted(ENGINES))
    ap.add_argument("--model", default="v3turbo")
    ap.add_argument("--device", default="cpu", choices=("cpu", "cuda", "auto"))
    ap.add_argument("--text-file", required=True)
    ap.add_argument("--language", default="vi")
    ap.add_argument("--voice", default=None, help="named SDK preset voice")
    ap.add_argument("--voice-ref", default=None, help="reference wav for zero-shot clone")
    ap.add_argument("--voice-ref-text", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    text_file = Path(args.text_file)
    out_path = Path(args.out)
    if not text_file.exists():
        fail(KIND_INPUT, f"text file not found: {text_file}")
    if bool(args.voice) == bool(args.voice_ref):
        fail(KIND_INPUT, "provide exactly one of --voice (preset) or --voice-ref (clone wav)")
    voice_ref = Path(args.voice_ref) if args.voice_ref else None
    if voice_ref is not None and not voice_ref.exists():
        fail(KIND_INPUT, f"voice reference not found: {voice_ref}")
    text = text_file.read_text(encoding="utf-8")
    chunks = split_text(text)
    if not chunks:
        fail(KIND_INPUT, "text is empty after normalization")

    if args.seed is not None:
        import random
        random.seed(args.seed)
        np.random.seed(args.seed)

    try:
        engine = ENGINES[args.engine](model=args.model, device=args.device)
    except TTSEngineError as exc:
        fail(exc.kind, str(exc))

    t0 = time.time()
    try:
        engine.load()
    except TTSEngineError as exc:
        fail(exc.kind, str(exc))
    except Exception as exc:  # SDK import/download failures
        import traceback
        traceback.print_exc(file=sys.stderr)
        fail("engine_load", f"{type(exc).__name__}: {exc}")
    load_s = time.time() - t0
    log(f"[tts] engine={args.engine} model={args.model} device={args.device} "
        f"loaded in {load_s:.1f}s; {len(chunks)} chunk(s)")

    t1 = time.time()
    pieces: list[np.ndarray] = []
    sr: int | None = None
    try:
        for i, chunk in enumerate(chunks, 1):
            log(f"[tts] chunk {i}/{len(chunks)} ({len(chunk)} chars)")
            samples, chunk_sr = engine.synthesize_chunk(
                chunk, str(voice_ref) if voice_ref else None,
                args.voice_ref_text, args.voice)
            if sr is None:
                sr = chunk_sr
            elif chunk_sr != sr:
                raise TTSEngineError(
                    KIND_SYNTHESIS, f"sample-rate changed mid-run: {sr} -> {chunk_sr}")
            pieces.append(samples)
    except TTSEngineError as exc:
        fail(exc.kind, str(exc))
    except Exception as exc:
        import traceback
        traceback.print_exc(file=sys.stderr)
        fail(KIND_SYNTHESIS, f"{type(exc).__name__}: {exc}")
    synth_s = time.time() - t1

    assert sr is not None
    silence = np.zeros(int(sr * JOIN_SILENCE_SECONDS), dtype=np.float32)
    joined: list[np.ndarray] = []
    for i, p in enumerate(pieces):
        if i:
            joined.append(silence)
        joined.append(p)
    audio = np.concatenate(joined)
    write_wav(out_path, audio, sr)

    emit({
        "ok": True,
        "out": str(out_path.resolve()),
        "duration_s": round(len(audio) / sr, 2),
        "engine": args.engine,
        "model": args.model,
        "device": getattr(engine, "backend_device", args.device),
        "voice": args.voice or f"clone:{voice_ref.name}",
        "synth_s": round(synth_s, 1),
        "load_s": round(load_s, 1),
        "chunks": len(chunks),
    })


if __name__ == "__main__":
    main()
