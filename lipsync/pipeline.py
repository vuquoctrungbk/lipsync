"""End-to-end orchestration: image + audio -> green-screen talking-head MP4.

Stages: prepare audio -> SadTalker animate -> matte + green composite.
Engines are cached across runs (VRAM headroom on a 12 GB card lets SadTalker and
the matting engine stay co-resident), so repeat renders skip the model-load cost.
"""
from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from . import config
from .animation_sadtalker import SadTalkerEngine
from .audio_preprocess import prepare_audio
from .config import RenderConfig, ensure_runtime_dirs
from .green_compositor import composite_to_green
from .hardware import detect_device, free_vram, vram_report
from .matting_base import MattingEngine
from .matting_birefnet import BiRefNetMatte
from .matting_rvm import RVMMatte

ProgressFn = Optional[Callable[[float, str], None]]

# The only two engines; RVM is GPL-3 and stays behind this indirection so
# commercial_safe builds never touch it (see matting_rvm module docstring).
_MATTING_ENGINES: dict[str, type] = {"rvm": RVMMatte, "birefnet": BiRefNetMatte}

# SadTalker's vendored preprocess routes by extension: only these are read as
# images — anything else falls into its VIDEO path (os.system ffmpeg with the
# exit code ignored), so the portrait must land on one of these names.
_IMAGE_EXTS = ("jpg", "jpeg", "png")


class PipelineError(Exception):
    pass


class Pipeline:
    """Holds cached engines for a single process (one user)."""

    def __init__(self):
        self.device = detect_device().device
        self._sad: SadTalkerEngine | None = None
        self._sad_key: tuple | None = None
        self._matte: MattingEngine | None = None
        self._matte_key: tuple | None = None

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

    def _matting(self, cfg: RenderConfig) -> MattingEngine:
        engine = cfg.matting_engine
        if cfg.commercial_safe and engine != "birefnet":
            print(f"[matting] commercial_safe=True — forcing birefnet "
                  f"(requested {engine!r}; RVM is GPL-3)")
            engine = "birefnet"
        if engine not in _MATTING_ENGINES:
            raise PipelineError(
                f"unknown matting_engine {engine!r}; choose from {sorted(_MATTING_ENGINES)}")

        precision = "fp16" if self.device == "cuda" else "fp32"
        key = (engine, precision)
        # Keyed cache: flipping engine/commercial_safe mid-session must swap
        # (and unload) the live engine, not silently keep the old one. The key
        # is only assigned AFTER a successful load — if load() raises mid-swap
        # the cache must read as empty, or a retry under the old key would
        # cache-hit the wrong (broken) engine instance.
        if self._matte is None or self._matte_key != key:
            if self._matte is not None:
                self._matte.unload()
            self._matte = None
            self._matte_key = None
            matte = _MATTING_ENGINES[engine](self.device, precision=precision)
            matte.load()
            self._matte = matte
            self._matte_key = key
        return self._matte

    def _sanitize_portrait(self, image_path: str | Path, work: Path) -> Path:
        """Copy the upload to a fixed safe name inside the run dir.

        The original filename flows into SadTalker's vendored os.system ffmpeg
        line (exit code ignored), where '%' or exotic characters corrupt the
        command — same idea as prepare_audio's fixed wav name.
        """
        src = Path(image_path)
        if not src.exists():
            raise PipelineError(f"image file not found: {src}")
        ext = src.suffix.lower().lstrip(".")
        if ext in _IMAGE_EXTS:
            safe = work / f"portrait.{ext}"
            shutil.copy2(src, safe)
            return safe

        # Unsupported extension (webp/bmp/...): SadTalker would misroute it to
        # its video path. Re-encode to png losslessly instead of failing.
        import cv2
        import numpy as np

        data = np.fromfile(str(src), dtype=np.uint8)  # unicode-safe on Windows
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if img is None:
            raise PipelineError(
                f"unsupported image format: {src.name} (use jpg/png/jpeg/webp/bmp)")
        ok, buf = cv2.imencode(".png", img)  # encode+write: checkable, unicode-safe
        if not ok:
            raise PipelineError(f"could not re-encode {src.name} to png")
        safe = work / "portrait.png"
        safe.write_bytes(buf.tobytes())
        return safe

    def run(self, image_path: str | Path, audio_path: str | Path,
            cfg: RenderConfig, progress: ProgressFn = None) -> dict:
        ensure_runtime_dirs()
        # uuid suffix kills same-second run-id collisions (two clicks, one second).
        stamp = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
        work = config.TEMP_DIR / f"run_{stamp}"
        work.mkdir(parents=True, exist_ok=True)
        timings: dict[str, float] = {}
        warnings: list[str] = []

        def emit(frac: float, msg: str) -> None:
            if progress:
                progress(frac, msg)

        # 1) audio -> 16 kHz mono wav; portrait -> sanitized fixed name
        emit(0.03, "preparing audio")
        t = time.time()
        wav = prepare_audio(audio_path, work)
        safe_image = self._sanitize_portrait(image_path, work)
        timings["audio_s"] = round(time.time() - t, 1)

        # 2) SadTalker animation
        emit(0.10, "animating portrait (SadTalker)")
        t = time.time()
        sad = self._sadtalker(cfg)
        animated = sad.animate(safe_image, wav, work / "sadtalker")
        timings["animate_s"] = round(time.time() - t, 1)

        if cfg.sequential_vram:
            sad.unload()
            self._sad = None
            self._sad_key = None
            free_vram()

        # 3) matte + green composite (uses ORIGINAL audio for final mux quality)
        t = time.time()
        matte = self._matting(cfg)
        # _matte_key holds the EFFECTIVE engine (commercial_safe may override)
        emit(0.60, f"matting + green compositing ({self._matte_key[0]})")
        out_path = cfg.output_dir / f"lipsync_green_{stamp}.mp4"
        out_path, comp_warnings = composite_to_green(
            animated, matte, cfg, audio_path, out_path, work_dir=work,
            progress=lambda fr, m: emit(0.60 + 0.38 * fr, m),
        )
        warnings.extend(comp_warnings)
        timings["composite_s"] = round(time.time() - t, 1)

        emit(1.0, "done")
        return {
            "output": out_path,
            "timings": timings,
            "warnings": warnings,
            "vram": vram_report("pipeline-end"),
            "device": self.device,
        }
