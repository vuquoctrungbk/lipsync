"""Engine interface for the TTS CLI.

Each engine wraps one TTS SDK inside the ISOLATED tools/tts venv. Engines
return raw audio samples; the CLI owns text chunking, silence joins, wav
encoding and the JSON result contract (see tts_cli.py).
"""
from __future__ import annotations

import numpy as np

# Error kinds surfaced verbatim in the CLI JSON contract.
KIND_INPUT = "input"
KIND_ENGINE_LOAD = "engine_load"
KIND_SYNTHESIS = "synthesis"


class TTSEngineError(Exception):
    """Engine failure with a machine-readable kind for the JSON contract."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


class BaseEngine:
    """One synthesis backend. Subclasses set `name`/`models` and implement
    load() + synthesize_chunk()."""

    name = "base"
    models: tuple[str, ...] = ()

    def __init__(self, model: str):
        if model not in self.models:
            raise TTSEngineError(
                KIND_INPUT,
                f"engine '{self.name}' has no model '{model}' (choices: {', '.join(self.models)})",
            )
        self.model = model

    def load(self) -> None:
        """Import the SDK and load weights (downloads on first run)."""
        raise NotImplementedError

    def synthesize_chunk(self, text: str, voice_ref: str | None,
                         voice_ref_text: str | None,
                         voice_preset: str | None) -> tuple[np.ndarray, int]:
        """Synthesize ONE chunk with either a reference wav (clone) or a named
        SDK preset voice. Returns (float32 mono samples in [-1, 1], sample_rate)."""
        raise NotImplementedError


def coerce_audio(result, fallback_sr: int | None = None) -> tuple[np.ndarray, int]:
    """Normalize the various SDK return shapes to (float32 mono [-1,1], sr).

    Accepts: (sr, samples) / (samples, sr) tuples, bare arrays (needs
    fallback_sr), int16 or float arrays, and (n, 1)-shaped audio.
    """
    sr = fallback_sr
    samples = result
    if isinstance(result, tuple) and len(result) == 2:
        a, b = result
        if isinstance(a, (int, np.integer)):        # (sr, samples)
            sr, samples = int(a), b
        elif isinstance(b, (int, np.integer)):      # (samples, sr)
            samples, sr = a, int(b)
    samples = np.asarray(samples)
    if samples.ndim == 2 and min(samples.shape) == 1:   # (n,1)/(1,n) -> mono
        samples = samples.reshape(-1)
    elif samples.ndim == 2 and min(samples.shape) == 2:  # stereo -> mean-pool
        channel_axis = 0 if samples.shape[0] == 2 else 1
        samples = samples.mean(axis=channel_axis)
    elif samples.ndim != 1:  # anything else would be silent garbage — refuse
        raise TTSEngineError(KIND_SYNTHESIS,
                             f"engine returned unexpected audio shape {samples.shape}")
    if np.issubdtype(samples.dtype, np.integer):    # int16 PCM -> float
        samples = samples.astype(np.float32) / 32768.0
    else:
        samples = samples.astype(np.float32)
    if sr is None:
        raise TTSEngineError(KIND_SYNTHESIS, "engine returned audio without a sample rate")
    return np.clip(samples, -1.0, 1.0), int(sr)
