"""MattingEngine protocol — the contract every matting backend implements.

Engines are constructed as ``Engine(device, precision="fp16")`` (extra kwargs
allowed per engine) and produce a soft alpha matte per frame. Stateful engines
(RVM's recurrent memory) expose that state ONLY through ``reset_clip()``; the
compositor driver is the single owner of reset calls — once at clip start and
once in its ``finally`` — so a crashed clip can never leak state (or a stale
resolution) into the next render.
"""
from __future__ import annotations

from typing import Protocol

import numpy as np


class MattingEngine(Protocol):
    def load(self) -> None:
        """Load model weights onto the device (idempotent)."""
        ...

    def unload(self) -> None:
        """Drop the model and free VRAM."""
        ...

    def alpha_for(self, rgb: np.ndarray) -> np.ndarray:
        """HxWx3 uint8 RGB -> HxW float32 alpha in [0, 1] at the input size."""
        ...

    def reset_clip(self) -> None:
        """Clear any cross-frame state before/after a clip. No-op if stateless."""
        ...
