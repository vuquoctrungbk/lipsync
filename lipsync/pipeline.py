"""End-to-end orchestration: image + audio -> green-screen talking-head MP4.

Stages: prepare audio -> SadTalker animate -> matte + green composite.
Engines are cached across runs (VRAM headroom on a 12 GB card lets SadTalker and
the matting engine stay co-resident), so repeat renders skip the model-load cost.
"""
from __future__ import annotations

import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from . import config, run_manifest
from .animation_sadtalker import SadTalkerEngine
from .audio_preprocess import prepare_audio, wav_duration_seconds
from .chunked_facerender import (iter_segment_frames, plan_segments,
                                 render_segment, slice_coeff_mat,
                                 write_silent_wav)
from .config import RenderConfig, ensure_runtime_dirs
from .ffmpeg_utils import ffmpeg_exe, has_encoder, has_ffprobe
from .green_compositor import OUTPUT_FORMATS, composite, iter_video_frames
from .hardware import detect_device, free_ram_bytes, free_vram_bytes, free_vram, vram_report
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
        # One render at a time: the engines are shared state, and the chunked
        # path chdir()s process-globally. Generate + Resume clicking together
        # must not interleave.
        self._render_lock = threading.Lock()

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
        if not self._render_lock.acquire(blocking=False):
            raise PipelineError(
                "another render is already running in this app — wait for it "
                "to finish (Generate and Resume share the GPU).")
        try:
            return self._run_locked(image_path, audio_path, cfg, progress)
        finally:
            self._render_lock.release()

    def _run_locked(self, image_path: str | Path, audio_path: str | Path,
                    cfg: RenderConfig, progress: ProgressFn = None) -> dict:
        # Fail fast on format/encoder problems BEFORE the expensive stages.
        if cfg.output_format not in OUTPUT_FORMATS:
            raise PipelineError(
                f"unknown output_format {cfg.output_format!r}; "
                f"choose from {sorted(OUTPUT_FORMATS)}")
        if cfg.output_format in ("webm_alpha", "both") and not has_encoder("libvpx-vp9"):
            raise PipelineError(
                "WebM alpha export needs an ffmpeg build with the libvpx-vp9 "
                f"encoder; the ffmpeg in use ({ffmpeg_exe()}) lacks it. Install "
                "full ffmpeg (e.g. `winget install ffmpeg`) or set output "
                "format to Green MP4.")

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

        # 2) SadTalker animation — single-shot for short clips (v1 path,
        #    unchanged), halo-chunked facerender for long ones.
        duration = wav_duration_seconds(wav)
        t = time.time()
        sad = self._sadtalker(cfg)
        purge_run_dir: Path | None = None
        if duration <= config.SINGLE_SHOT_MAX_SECONDS:
            emit(0.10, "animating portrait (SadTalker)")
            animated = sad.animate(safe_image, wav, work / "sadtalker")
            frames, src_fps, total = iter_video_frames(animated)
        else:
            frames, src_fps, total, run_dir, resumed = self._chunked_animate(
                sad, cfg, safe_image, wav, work, emit, warnings)
            purge_run_dir = run_dir
            if resumed:
                warnings.append(
                    "resumed an interrupted render — completed segments were "
                    "reused; compositing restarted (it is not checkpointable).")
        timings["animate_s"] = round(time.time() - t, 1)

        if cfg.sequential_vram:
            sad.unload()
            self._sad = None
            self._sad_key = None
            free_vram()

        # 3) matte + composite (uses ORIGINAL audio for final mux quality)
        # Disk re-check at composite entry: a disk-full failure here would
        # discard the whole (possibly multi-hour) render pass on every retry.
        est_out = int(total or 0) * 3 * 100_000 // 25  # ~100KB/s/format, rough
        free_disk = shutil.disk_usage(cfg.output_dir if cfg.output_dir.exists()
                                      else config.ROOT).free
        if est_out > 0 and free_disk < 2 * est_out:
            raise PipelineError(
                f"not enough disk for the final encode: ~{2 * est_out / 1e9:.1f}GB "
                f"headroom wanted, {free_disk / 1e9:.1f}GB free on the output drive.")

        t = time.time()
        matte = self._matting(cfg)
        # _matte_key holds the EFFECTIVE engine (commercial_safe may override)
        emit(0.60, f"matting + compositing ({self._matte_key[0]}, {cfg.output_format})")
        outputs, comp_warnings = composite(
            frames, src_fps, matte, cfg, audio_path, cfg.output_dir,
            run_id=stamp, work_dir=work, total=total,
            progress=lambda fr, m: emit(0.60 + 0.38 * fr, m),
        )
        warnings.extend(comp_warnings)
        timings["composite_s"] = round(time.time() - t, 1)

        # Reclaim scratch once ALL requested outputs are finalized in
        # outputs/. work/ (single-shot: wav+portrait+animated mp4; chunked:
        # fresh inputs) and any adopted chunked run dir (segments) both go.
        # On a chunked PARTIAL success keep the segments so a retry resumes.
        expected_outputs = 2 if cfg.output_format == "both" else 1
        full_success = len(outputs) == expected_outputs
        if full_success:
            shutil.rmtree(work, ignore_errors=True)
            if purge_run_dir is not None and purge_run_dir != work:
                shutil.rmtree(purge_run_dir, ignore_errors=True)
        elif purge_run_dir is not None:
            warnings.append(
                "segments were kept on disk (one output failed) — "
                "re-running the same render will reuse them.")

        emit(1.0, "done")
        return {
            # "output" stays the primary artifact for existing consumers:
            # green when produced, else the webm (webm_alpha-only mode).
            "output": outputs.get("green_mp4") or outputs["webm_alpha"],
            "outputs": outputs,
            "timings": timings,
            "warnings": warnings,
            "vram": vram_report("pipeline-end"),
            "device": self.device,
        }

    # ------------------------------------------------------------------ #
    # chunked long-audio path                                             #
    # ------------------------------------------------------------------ #

    def _chunked_animate(self, sad: SadTalkerEngine, cfg: RenderConfig,
                         safe_image: Path, wav: Path, work: Path,
                         emit, warnings: list[str]) -> tuple:
        """Render >120s audio as halo-overlapped facerender segments.

        Returns (frame iterator, fps, total kept frames, run_dir, resumed).
        """
        if not has_ffprobe():
            raise PipelineError(
                "long-audio rendering needs ffprobe for fail-loud segment "
                "verification — install full ffmpeg (winget install ffmpeg) "
                "or trim the audio to 120s.")
        if cfg.fps != 25:
            # SadTalker renders 25fps regardless; a different cfg.fps would
            # mistime the composite and desync the final mux.
            warnings.append(f"fps={cfg.fps} ignored for long renders — "
                            "SadTalker outputs 25fps; using 25.")
            cfg.fps = 25

        image_sha1 = run_manifest.sha1_file(safe_image)
        audio_sha1 = run_manifest.sha1_file(wav)
        fingerprint = run_manifest.cfg_fingerprint(cfg)

        resumable = run_manifest.find_resumable(
            config.TEMP_DIR, image_sha1, audio_sha1, fingerprint)
        resumed = False
        if resumable is not None:
            run_dir, manifest = resumable
            if run_manifest.coeff_valid(manifest, run_dir):
                emit(0.10, "resuming interrupted render (validating segments)")
                run_manifest.validate_segments(manifest, run_dir)
                manifest["owner_pid"] = os.getpid()
                run_manifest.write_manifest(run_dir, manifest)
                # rebuild the (deterministic) preprocess bundle; the manifest
                # pins the coeff mat — audio2coeff is NEVER re-run here
                old_wav = run_dir / "audio_16k_mono.wav"
                old_image = next(iter(run_dir.glob("portrait.*")), None)
                if old_image is None or not old_wav.exists():
                    shutil.rmtree(run_dir, ignore_errors=True)
                    resumable = None
                else:
                    bundle = sad.prepare_coeff(
                        old_image, old_wav, run_dir / "sadtalker",
                        reuse_coeff_path=run_dir / manifest["coeff"]["path"])
                    resumed = True
            else:
                # ANY coeff doubt -> the whole run restarts under a NEW id
                shutil.rmtree(run_dir, ignore_errors=True)
                resumable = None

        if resumable is None:
            run_manifest.purge_stale_runs(
                config.TEMP_DIR, image_sha1, audio_sha1, fingerprint, keep=work)
            run_dir = work
            emit(0.10, "computing motion coefficients (full audio, once)")
            bundle = sad.prepare_coeff(safe_image, wav, run_dir / "sadtalker")
            coeff_path = bundle["coeff_path"]

            from scipy.io import loadmat

            rows = int(loadmat(str(coeff_path))["coeff_3dmm"].shape[0])
            import cv2

            img = cv2.imread(str(safe_image))
            src_hw = img.shape[:2] if img is not None else (1080, 1920)
            # measured (gate, 2026-07-03): paste-back runs ~0.4s/frame PER 3.8MP
            # at source resolution — RAM and wall-clock scale with source area
            if src_hw[0] * src_hw[1] > 2_100_000:
                mp = src_hw[0] * src_hw[1] / 1e6
                warnings.append(
                    f"portrait is {mp:.1f}MP — long renders buffer frames in RAM "
                    "and paste-back CPU time scales with source area; consider "
                    "downscaling the portrait to ~2MP for multi-minute clips.")
            segments = plan_segments(
                rows, cfg.face_size, src_hw, free_vram_bytes(), free_ram_bytes(),
                chunk_seconds=cfg.chunk_seconds, use_enhancer=cfg.use_enhancer,
                fps=cfg.fps)

            self._check_disk_for_segments(segments, src_hw)

            manifest = run_manifest.new_manifest(
                image_sha1, audio_sha1, cfg,
                coeff_rel_path=str(coeff_path.relative_to(run_dir)),
                coeff_sha1=run_manifest.sha1_file(coeff_path),
                coeff_rows=rows, segments=segments)
            run_manifest.write_manifest(run_dir, manifest)

        silent = write_silent_wav(run_dir / "silent_1s.wav")
        segments = manifest["segments"]
        pending = [s for s in segments if s["status"] != "done"]
        done_count = len(segments) - len(pending)
        coeff_full = run_dir / manifest["coeff"]["path"]

        for i, seg in enumerate(sorted(pending, key=lambda s: s["idx"])):
            frac = 0.15 + 0.45 * (done_count + i) / len(segments)
            emit(frac, f"rendering segment {seg['idx'] + 1}/{len(segments)} "
                       f"(frames {seg['start_f']}-{seg['end_f']})")
            coeff_slice = slice_coeff_mat(
                coeff_full, seg["halo_start"], seg["halo_end"],
                run_dir / f"coeff_seg_{seg['idx']:03d}.mat")
            render_segment(sad, bundle, seg, coeff_slice, silent, run_dir)
            seg["status"] = "done"
            run_manifest.write_manifest(run_dir, manifest)

        total_kept = sum(s["end_f"] - s["start_f"] for s in segments)
        return (iter_segment_frames(segments, run_dir), float(cfg.fps),
                total_kept, run_dir, resumed)

    def latest_resumable(self) -> dict | None:
        """Newest interrupted chunked run whose owner is dead — feeds the UI
        Resume button (age + progress + inputs stored inside the run dir)."""
        candidates = []
        for d in config.TEMP_DIR.glob("run_*"):
            m = run_manifest.load_manifest(d)
            # all-done manifests stay resumable: they mean the crash hit
            # during compositing — the segment loop no-ops, composite reruns
            if m is None or run_manifest._owner_blocks_adoption(m):
                continue
            img = next(iter(d.glob("portrait.*")), None)
            wav = d / "audio_16k_mono.wav"
            if img is None or not wav.exists():
                continue
            candidates.append((d.stat().st_mtime, d, m, img, wav))
        if not candidates:
            return None
        _, d, m, img, wav = max(candidates, key=lambda t: t[0])
        done = sum(1 for s in m["segments"] if s.get("status") == "done")
        return {
            "run_dir": d,
            "created": m.get("created", "?"),
            "segments_done": done,
            "segments_total": len(m["segments"]),
            "cfg": m["inputs"].get("cfg", {}),
            "image": img,
            "audio": wav,
        }

    def _check_disk_for_segments(self, segments: list[dict],
                                 src_hw: tuple[int, int]) -> None:
        """Pre-check: rough segment-store estimate must fit in HALF the free
        disk (the estimate is rough — mp4v at source resolution, ~0.02 of raw)."""
        total_frames = sum(s["frames_expected"] for s in segments)
        h, w = src_hw
        est = int(total_frames * h * w * 3 * 0.02)
        free = shutil.disk_usage(config.TEMP_DIR).free
        if est * 2 > free:
            raise PipelineError(
                f"not enough disk for segment rendering: need ~{est * 2 / 1e9:.1f}GB "
                f"headroom (2x estimate), {free / 1e9:.1f}GB free on the temp drive. "
                "Free space or lower chunk_seconds/face size.")
