"""BiRefNet portrait matting (MIT license).

Produces a soft alpha matte per frame so the animated character can be
composited onto a green background. The model has NO audio role — it only
separates foreground from background.
"""
from __future__ import annotations

import warnings

import cv2
import numpy as np
import torch

from . import config
from .hardware import free_vram


class MattingError(Exception):
    pass


# ImageNet normalization (BiRefNet's expected input stats).
_MEAN = torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)
_STD = torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)


class BiRefNetMatte:
    REPO = "ZhengPeng7/BiRefNet-matting"

    def __init__(self, device: str, precision: str = "fp16", resolution: int = 1024):
        self.device = device
        self.half = (precision == "fp16" and device == "cuda")
        self.res = resolution
        self._model = None
        self._mean = _MEAN.to(device)
        self._std = _STD.to(device)

    def load(self) -> None:
        if self._model is not None:
            return
        from transformers import AutoModelForImageSegmentation

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = AutoModelForImageSegmentation.from_pretrained(
                self.REPO, trust_remote_code=True, cache_dir=str(config.BIREFNET_DIR),
            )
        model.to(self.device).eval()
        if self.half:
            model.half()
            self._mean = self._mean.half()
            self._std = self._std.half()
        self._model = model

    def unload(self) -> None:
        self._model = None
        free_vram()

    def reset_clip(self) -> None:
        """BiRefNet is stateless per frame — nothing to clear."""

    @torch.inference_mode()
    def alpha_for(self, rgb: np.ndarray) -> np.ndarray:
        """rgb: HxWx3 uint8 -> alpha HxW float32 in [0,1] at the input size."""
        if self._model is None:
            self.load()
        h, w = rgb.shape[:2]

        resized = cv2.resize(rgb, (self.res, self.res), interpolation=cv2.INTER_LINEAR)
        t = torch.from_numpy(resized).to(self.device).float().div_(255.0).permute(2, 0, 1)
        if self.half:
            t = t.half()
        t = (t - self._mean) / self._std
        t = t.unsqueeze(0)

        out = self._model(t)
        pred = out[-1] if isinstance(out, (list, tuple)) else out
        alpha = pred.sigmoid()[0, 0].float().cpu().numpy()

        alpha = cv2.resize(alpha, (w, h), interpolation=cv2.INTER_LINEAR)
        return np.clip(alpha, 0.0, 1.0)
