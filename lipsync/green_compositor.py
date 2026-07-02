"""Composite an animated portrait video onto a solid green background.

Streams frames from the SadTalker MP4, applies the matting engine's alpha per
frame (foreground over green), encodes H.264, then remuxes the audio. Streaming
keeps memory flat regardless of clip length. This driver is the single owner of
``matte.reset_clip()`` — once at clip start and once in ``finally`` — so
recurrent engines (RVM) start every clip clean and never leak state across
renders, even after a crash.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

import imageio
import numpy as np

from .config import RenderConfig
from .ffmpeg_utils import FFmpegError, ffmpeg_exe, run
from .matting_base import MattingEngine

ProgressFn = Optional[Callable[[float, str], None]]


class CompositeError(Exception):
    pass


def composite_to_green(animated_video: str | Path, matte: MattingEngine,
                       cfg: RenderConfig, audio_path: str | Path,
                       out_path: str | Path, work_dir: str | Path,
                       progress: ProgressFn = None) -> tuple[Path, list[str]]:
    """Returns (output path, warnings). Warnings are non-fatal degradations
    (e.g. silent-video fallback) the UI must surface rather than swallow."""
    warnings: list[str] = []
    reader = imageio.get_reader(str(animated_video), "ffmpeg")
    meta = reader.get_meta_data()
    fps = meta.get("fps") or cfg.fps
    try:
        total = reader.count_frames()
    except Exception:  # noqa: BLE001 - frame count is best-effort for progress
        total = None

    green = np.array(cfg.green_rgb, dtype=np.float32)
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    # Inside the per-run work dir, so concurrent/same-second runs cannot collide.
    silent = work / "green_silent.mp4"

    writer = imageio.get_writer(
        str(silent), fps=fps, codec="libx264", macro_block_size=None,
        output_params=["-crf", str(cfg.crf), "-pix_fmt", "yuv420p"],
    )
    matte.reset_clip()
    try:
        for i, frame in enumerate(reader):
            if frame.ndim == 3 and frame.shape[2] == 4:
                frame = frame[..., :3]
            # yuv420p needs even dimensions.
            h, w = frame.shape[:2]
            h, w = h - (h % 2), w - (w % 2)
            frame = frame[:h, :w]

            alpha = matte.alpha_for(frame)[..., None]
            comp = frame.astype(np.float32) * alpha + green * (1.0 - alpha)
            writer.append_data(np.clip(comp, 0, 255).astype(np.uint8))

            if progress and total:
                progress((i + 1) / total, f"matting + keying frame {i + 1}/{total}")
    finally:
        writer.close()
        reader.close()
        matte.reset_clip()

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        run([
            ffmpeg_exe(), "-y", "-i", str(silent), "-i", str(audio_path),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", str(out),
        ])
    except FFmpegError as exc:
        # Audio missing/incompatible -> emit video-only rather than fail the
        # run, but the degradation must reach the user, not just the log.
        cause = (str(exc).splitlines() or ["unknown"])[-1][:200]
        warnings.append(
            f"audio mux failed — output is video-only (silent). Cause: {cause}"
        )
        run([ffmpeg_exe(), "-y", "-i", str(silent), "-c:v", "copy", str(out)])

    if not out.exists() or out.stat().st_size == 0:
        raise CompositeError("green-screen compositing produced no output")
    return out, warnings
