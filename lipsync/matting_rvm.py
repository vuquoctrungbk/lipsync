"""RobustVideoMatting adapter (GPL-3.0 — personal/non-commercial builds only).

RVM is recurrent: each ``alpha_for`` call feeds hidden state (r1..r4) forward,
which is what makes its mattes temporally stable (no flicker). The state lives
in this adapter and is cleared by ``reset_clip()`` — the compositor driver owns
those calls (see matting_base). A stateless per-frame wrapper would throw away
the temporal stability that justifies the engine swap.

Pinned source (torch.hub executes the repo's hubconf.py — an unpinned ref would
run whatever upstream HEAD becomes):
    repo:      PeterL1n/RobustVideoMatting
    commit:    53d74c6826735f01f4406b5ca9075eee27bec094  (master, 2026-07-03)
    variant:   mobilenetv3 (rvm_mobilenetv3.pth, downloaded on first load)
    ckpt sha256: 3c7c1d92033f7c38d6577c481d13a195d7d80a159b960f4f3119ac7b534cf4f8
Cache lives under models/rvm (torch.hub dir is scoped + restored around load).

GPL containment: this module is the ONLY place RVM code/weights are touched;
``commercial_safe=True`` in config forces the BiRefNet (MIT) engine and never
loads this one.
"""
from __future__ import annotations

import numpy as np
import torch

from . import config
from .hardware import free_vram

HUB_REPO = "PeterL1n/RobustVideoMatting"
HUB_COMMIT = "53d74c6826735f01f4406b5ca9075eee27bec094"
RVM_HUB_DIR = config.MODELS_DIR / "rvm"


class RVMMatte:
    def __init__(self, device: str, precision: str = "fp16"):
        self.device = device
        self.half = (precision == "fp16" and device == "cuda")
        self._model = None
        self._rec: list = [None, None, None, None]

    def load(self) -> None:
        if self._model is not None:
            return
        RVM_HUB_DIR.mkdir(parents=True, exist_ok=True)
        # torch.hub dir is process-global state; scope it so RVM's repo zip +
        # checkpoint land in models/rvm, then restore whatever was set before.
        prev_dir = torch.hub.get_dir()
        try:
            torch.hub.set_dir(str(RVM_HUB_DIR))
            # trust_repo=True: the default prompt raises EOFError on a
            # non-interactive first run (run_app.bat).
            model = torch.hub.load(
                f"{HUB_REPO}:{HUB_COMMIT}", "mobilenetv3", trust_repo=True,
            )
        finally:
            torch.hub.set_dir(prev_dir)
        model.to(self.device).eval()
        if self.half:
            model.half()
        self._model = model

    def unload(self) -> None:
        self._model = None
        self._rec = [None, None, None, None]
        free_vram()

    def reset_clip(self) -> None:
        self._rec = [None, None, None, None]

    @torch.inference_mode()
    def alpha_for(self, rgb: np.ndarray) -> np.ndarray:
        """rgb: HxWx3 uint8 -> alpha HxW float32 in [0,1] at the input size."""
        if self._model is None:
            self.load()
        h, w = rgb.shape[:2]

        src = torch.from_numpy(rgb).to(self.device).float().div_(255.0)
        src = src.permute(2, 0, 1).unsqueeze(0)  # 1x3xHxW, RVM takes raw 0..1 RGB
        if self.half:
            src = src.half()

        # RVM's own auto rule: internal downsample so the coarse pass sees
        # ~512px on the long side; the deep guided filter upsamples the matte
        # back to source resolution.
        ratio = min(512.0 / max(h, w), 1.0)

        fgr, pha, *rec = self._model(src, *self._rec, downsample_ratio=ratio)
        self._rec = rec

        alpha = pha[0, 0].float().cpu().numpy()
        return np.clip(alpha, 0.0, 1.0)
