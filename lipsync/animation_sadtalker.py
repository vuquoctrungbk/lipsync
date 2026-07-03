"""SadTalker wrapped as a library engine.

Mirrors SadTalker's inference.py flow (preprocess -> audio2coeff -> facerender)
but keeps full control of device, precision and VRAM lifecycle. Produces an MP4
of the animated portrait on its ORIGINAL background; the green-screen matte is a
later pipeline stage.
"""
from __future__ import annotations

import contextlib
import random
import sys
import warnings
from pathlib import Path

import numpy as np
import torch

from . import config
from .config import RenderConfig
from .hardware import detect_device, free_vram, reset_peak, resolve_precision, vram_report

# Make SadTalker importable as a library (its modules use `from src...`).
_SAD_SRC = str(config.SADTALKER_SRC)
if _SAD_SRC not in sys.path:
    sys.path.insert(0, _SAD_SRC)

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from src.generate_batch import get_data
    from src.generate_facerender_batch import get_facerender_data
    from src.test_audio2coeff import Audio2Coeff
    from src.facerender.animate import AnimateFromCoeff
    from src.utils.init_path import init_path
    from src.utils.preprocess import CropAndExtract


class SadTalkerError(Exception):
    pass


class SadTalkerEngine:
    """Loads SadTalker models once; animate() can be called repeatedly."""

    def __init__(self, cfg: RenderConfig, device: str):
        self.cfg = cfg
        self.device = device
        self._loaded = False

    def load(self) -> None:
        if self._loaded:
            return
        paths = init_path(
            str(config.SADTALKER_CKPT_DIR),
            str(config.SADTALKER_CONFIG_DIR),
            self.cfg.face_size,
            False,                      # old_version=False -> use safetensors
            self.cfg.preprocess,
        )
        self._preprocess = CropAndExtract(paths, self.device)
        self._audio2coeff = Audio2Coeff(paths, self.device)
        self._animate = AnimateFromCoeff(paths, self.device)
        self._loaded = True

    def unload(self) -> None:
        for attr in ("_preprocess", "_audio2coeff", "_animate"):
            if hasattr(self, attr):
                delattr(self, attr)
        self._loaded = False
        free_vram()

    def _autocast(self):
        use_fp16 = (
            self.device == "cuda"
            and resolve_precision(self.cfg.precision, detect_device()) == "fp16"
        )
        if use_fp16:
            return torch.autocast("cuda", dtype=torch.float16)
        return contextlib.nullcontext()

    def prepare_coeff(self, image_path: str | Path, audio_path: str | Path,
                      work_dir: str | Path,
                      reuse_coeff_path: str | Path | None = None) -> dict:
        """Face detect + 3DMM + audio2coeff ONCE for the FULL audio.

        Returns the bundle every later render step needs. audio2coeff emits a
        cheap (num_frames, 70) mat, so this runs whole even for 600s clips —
        only facerender needs chunking.

        `reuse_coeff_path` (resume): rebuild only the DETERMINISTIC
        preprocess bundle and pin the given coeff mat — audio2coeff is
        stochastic, so re-running it would produce a mat that no longer
        matches already-rendered segments.
        """
        self.load()
        save_dir = Path(work_dir)
        first_frame_dir = save_dir / "first_frame"
        first_frame_dir.mkdir(parents=True, exist_ok=True)
        reset_peak()

        # SadTalker is stochastic (unseeded pose-CVAE noise + blink sampling).
        # A pinned seed makes comparison renders and chunked-vs-full
        # equivalence tests reproducible; None keeps natural variation.
        if self.cfg.seed is not None:
            random.seed(self.cfg.seed)
            np.random.seed(self.cfg.seed)
            torch.manual_seed(self.cfg.seed)
            if torch.cuda.is_available():
                torch.cuda.manual_seed_all(self.cfg.seed)

        # Face detect + 3DMM in fp32 (detection is sensitive to precision).
        first_coeff, crop_pic, crop_info = self._preprocess.generate(
            str(image_path), str(first_frame_dir), self.cfg.preprocess,
            source_image_flag=True, pic_size=self.cfg.face_size,
        )
        if first_coeff is None:
            raise SadTalkerError(
                "No face detected in the source image. Use a clear, front-facing portrait."
            )

        if reuse_coeff_path is not None:
            coeff_path = reuse_coeff_path
        else:
            with self._autocast():
                batch = get_data(first_coeff, str(audio_path), self.device,
                                 ref_eyeblink_coeff_path=None, still=self.cfg.still_mode)
                coeff_path = self._audio2coeff.generate(
                    batch, str(save_dir), self.cfg.pose_style, ref_pose_coeff_path=None)

        return {
            "image_path": str(image_path),
            "first_coeff": first_coeff,
            "crop_pic": crop_pic,
            "crop_info": crop_info,
            "coeff_path": Path(coeff_path),
        }

    def render_from_coeff(self, bundle: dict, coeff_path: str | Path,
                          audio_for_mux: str | Path, out_dir: str | Path) -> Path:
        """facerender + paste-back for ONE coeff mat (full or a halo slice).

        `audio_for_mux` only feeds the vendored per-output mux — chunked
        segments pass a 1s silent wav (the mux decodes its whole audio input
        per call and the final audio is muxed by the compositor anyway).
        """
        self.load()
        out_path = Path(out_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        with self._autocast():
            data = get_facerender_data(
                str(coeff_path), bundle["crop_pic"], bundle["first_coeff"],
                str(audio_for_mux), self.cfg.batch_size, None, None, None,
                expression_scale=self.cfg.expression_scale,
                still_mode=self.cfg.still_mode,
                preprocess=self.cfg.preprocess,
                size=self.cfg.face_size,
            )
            enhancer = "gfpgan" if self.cfg.use_enhancer else None
            result = self._animate.generate(
                data, str(out_path), bundle["image_path"], bundle["crop_info"],
                enhancer=enhancer, background_enhancer=None,
                preprocess=self.cfg.preprocess, img_size=self.cfg.face_size,
            )

        out = Path(result)
        if not out.exists():
            raise SadTalkerError("SadTalker did not produce an output video")
        return out

    def animate(self, image_path: str | Path, audio_path: str | Path,
                work_dir: str | Path) -> Path:
        """Single-shot path (≤120s audio): coeff + render in one go.

        Behavior-identical to v1 — the chunked path composes the same two
        steps with sliced mats instead.
        """
        bundle = self.prepare_coeff(image_path, audio_path, work_dir)
        out = self.render_from_coeff(bundle, bundle["coeff_path"],
                                     audio_path, work_dir)
        print(vram_report("sadtalker"))
        return out
