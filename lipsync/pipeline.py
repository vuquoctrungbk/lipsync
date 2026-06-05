"""End-to-end orchestration: image + audio -> green-screen talking-head MP4.

Stages: prepare audio -> SadTalker animate -> BiRefNet matte + green composite.
Engines are cached across runs (VRAM headroom on a 12 GB card lets SadTalker and
BiRefNet stay co-resident), so repeat renders skip the model-load cost.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Optional

from . import config
from .animation_sadtalker import SadTalkerEngine
from .audio_preprocess import prepare_audio
from .config import RenderConfig, ensure_runtime_dirs
from .green_compositor import composite_to_green
from .hardware import detect_device, free_vram, vram_report
from .matting_birefnet import BiRefNetMatte

ProgressFn = Optional[Callable[[float, str], None]]


class Pipeline:
    """Holds cached engines for a single process (one user)."""

    def __init__(self):
        self.device = detect_device().device
        self._sad: SadTalkerEngine | None = None
        self._sad_key: tuple | None = None
        self._matte: BiRefNetMatte | None = None

    def _sadtalker(self, cfg: RenderConfig) -> SadTalkerEngine:
        # init_path depends on face_size + preprocess, so reload when they change.
        key = (cfg.face_size, cfg.preprocess)
        if self._sad is None or self._sad_key != key:
            if self._sad is not None:
                self._sad.unload()
            self._sad = SadTalkerEngine(cfg, self.device)
            self._sad.load()
            self._sad_key = key
        self._sad.cfg = cfg  # refresh runtime knobs (pose, expression, still, ...)
        return self._sad

    def _matting(self) -> BiRefNetMatte:
        if self._matte is None:
            precision = "fp16" if self.device == "cuda" else "fp32"
            self._matte = BiRefNetMatte(self.device, precision=precision)
            self._matte.load()
        return self._matte

    def run(self, image_path: str | Path, audio_path: str | Path,
            cfg: RenderConfig, progress: ProgressFn = None) -> dict:
        ensure_runtime_dirs()
        stamp = time.strftime("%Y%m%d_%H%M%S")
        work = config.TEMP_DIR / f"run_{stamp}"
        work.mkdir(parents=True, exist_ok=True)
        timings: dict[str, float] = {}

        def emit(frac: float, msg: str) -> None:
            if progress:
                progress(frac, msg)

        # 1) audio -> 16 kHz mono wav
        emit(0.03, "preparing audio")
        t = time.time()
        wav = prepare_audio(audio_path, work)
        timings["audio_s"] = round(time.time() - t, 1)

        # 2) SadTalker animation
        emit(0.10, "animating portrait (SadTalker)")
        t = time.time()
        sad = self._sadtalker(cfg)
        animated = sad.animate(image_path, wav, work / "sadtalker")
        timings["animate_s"] = round(time.time() - t, 1)

        if cfg.sequential_vram:
            sad.unload()
            self._sad = None
            self._sad_key = None
            free_vram()

        # 3) matte + green composite (uses ORIGINAL audio for final mux quality)
        emit(0.60, "matting + green compositing (BiRefNet)")
        t = time.time()
        matte = self._matting()
        out_path = cfg.output_dir / f"lipsync_green_{stamp}.mp4"
        composite_to_green(
            animated, matte, cfg, audio_path, out_path,
            progress=lambda fr, m: emit(0.60 + 0.38 * fr, m),
        )
        timings["composite_s"] = round(time.time() - t, 1)

        emit(1.0, "done")
        return {
            "output": out_path,
            "timings": timings,
            "vram": vram_report("pipeline-end"),
            "device": self.device,
        }
