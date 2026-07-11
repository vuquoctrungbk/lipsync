#!/usr/bin/env python
"""Matte any talking-head video onto solid green / transparent WebM.

Wraps the app's streaming matte pipeline (RVM default, BiRefNet optional) so
externally rendered clips — e.g. Colab hybrid Ditto+LatentSync output from
tools/colab/lipsync_render.ipynb — get the exact same green-screen treatment
as the built-in SadTalker path. Replaces the ad-hoc inline snippet used during
the animation-engine spike.

Usage:
    python scripts/matte_video.py --video outputs/colab/hybrid_256.mp4
    python scripts/matte_video.py --video clip.mp4 --format both --engine birefnet
    # flicker fix: compute alpha from a cleaner frame-aligned encode
    # (mouth-refine finals are recompressed hard; their noise makes the matte
    #  flicker — the pre-refine clip has identical background motion):
    python scripts/matte_video.py --video hybrid_up1402.mp4 \
        --engine birefnet --alpha-from outputs/colab/ditto_raw.mp4

The audio track defaults to the input video itself (ffmpeg maps its first
audio stream when muxing); pass --audio for a separate wav. --alpha-from
requires the same frame size as --video (scale one first if they differ).
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Iterator

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from lipsync.config import TEMP_DIR, RenderConfig, ensure_runtime_dirs  # noqa: E402


def build_config(engine: str, output_format: str, commercial_safe: bool) -> RenderConfig:
    """Defaults tuned in RenderConfig; only the matte-relevant knobs vary here."""
    return RenderConfig(matting_engine=engine, output_format=output_format,
                        commercial_safe=commercial_safe)


def default_run_id() -> str:
    # Same shape the app uses for outputs/lipsync_green_<runid>.mp4
    return f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


# LatentSync pads output to a multiple of 16 frames, so the final clip is up
# to 15 frames longer than the pre-refine clip the alpha comes from.
PADDING_TOLERANCE_FRAMES = 16


def plan_alignment(total: int | None, fps: float,
                   alpha_total: int | None, alpha_fps: float) -> int | None:
    """Frame count to render when alpha comes from a sibling clip.

    Codec padding makes small count gaps normal — trim to the shorter clip.
    Large gaps or an fps mismatch mean it is NOT the same take: fail loud
    BEFORE spending minutes matting (a mismatch renders a silently
    time-shifted matte).
    """
    if abs(alpha_fps - fps) > 0.01:
        raise ValueError(
            f"--alpha-from runs at {alpha_fps:g} fps but the video is {fps:g} fps "
            "— not the same take?")
    if total is None or alpha_total is None:
        return total  # counts unknowable: composite still fails loud if short
    if alpha_total >= total:
        return total
    if total - alpha_total <= PADDING_TOLERANCE_FRAMES:
        return alpha_total
    raise ValueError(
        f"--alpha-from has {alpha_total} frames, the video has {total} — the gap "
        f"exceeds codec padding ({PADDING_TOLERANCE_FRAMES}); different clip?")


def _take(source: Iterator, n: int) -> Iterator:
    # itertools.islice has no .close(), which would leave the underlying
    # reader to interpreter-exit GC (imageio throws WinError 6 there).
    try:
        for _ in range(n):
            yield next(source)
    except StopIteration:
        return
    finally:
        close = getattr(source, "close", None)
        if close:
            close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--video", required=True, help="input video to matte")
    p.add_argument("--audio", default=None,
                   help="audio to mux (default: the input video's own track)")
    p.add_argument("--alpha-from", default=None, dest="alpha_from",
                   help="frame-aligned, same-size video to compute alpha from "
                        "(cleaner encode -> stable matte edges)")
    p.add_argument("--engine", choices=("rvm", "birefnet"), default="rvm",
                   help="matting engine (rvm = fast, GPL-3 personal use)")
    p.add_argument("--format", choices=("green_mp4", "webm_alpha", "both"),
                   default="green_mp4", dest="output_format")
    p.add_argument("--commercial-safe", action="store_true",
                   help="force the MIT BiRefNet path; never load GPL RVM")
    p.add_argument("--out-dir", default=None,
                   help="output directory (default: outputs/)")
    p.add_argument("--run-id", default=None,
                   help="output name suffix (default: timestamp)")
    args = p.parse_args(argv)

    args.video = Path(args.video)
    if not args.video.is_file():
        p.error(f"--video not found: {args.video}")
    if args.audio is not None:
        args.audio = Path(args.audio)
        if not args.audio.is_file():
            p.error(f"--audio not found: {args.audio}")
    if args.alpha_from is not None:
        args.alpha_from = Path(args.alpha_from)
        if not args.alpha_from.is_file():
            p.error(f"--alpha-from not found: {args.alpha_from}")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    # Heavy imports after arg validation so --help stays instant.
    from lipsync.green_compositor import CompositeError, composite, iter_video_frames
    from lipsync.pipeline import Pipeline

    cfg = build_config(args.engine, args.output_format, args.commercial_safe)
    audio = args.audio if args.audio is not None else args.video
    out_dir = Path(args.out_dir) if args.out_dir else cfg.output_dir
    run_id = args.run_id or default_run_id()

    ensure_runtime_dirs()
    # Pipeline._matting owns engine selection (commercial_safe override, fp16
    # on CUDA, load/unload caching) — reuse it instead of duplicating the rules.
    matte = Pipeline()._matting(cfg)
    frames, fps, total = iter_video_frames(args.video)
    alpha_source = None
    if args.alpha_from is not None:
        alpha_source, alpha_fps, alpha_total = iter_video_frames(args.alpha_from)
        try:
            aligned = plan_alignment(total, fps, alpha_total, alpha_fps)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if aligned is not None and total is not None and aligned < total:
            print(f"trimming {total - aligned} codec-padding frame(s) not covered "
                  "by --alpha-from")
            frames = _take(frames, aligned)
            total = aligned
    work_dir = TEMP_DIR / f"matte_{run_id}"

    print(f"matting {args.video.name} ({fps:g} fps, "
          f"{total if total is not None else '?'} frames) "
          f"engine={cfg.matting_engine} format={cfg.output_format}")
    t0 = time.monotonic()

    def progress(frac: float, msg: str) -> None:
        print(f"\r{msg}", end="", flush=True)

    try:
        outputs, warnings = composite(frames, fps, matte, cfg, audio, out_dir,
                                      run_id, work_dir, total=total,
                                      progress=progress,
                                      alpha_source=alpha_source)
    except CompositeError as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1

    print(f"\ndone in {time.monotonic() - t0:.1f}s")
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    for fmt, path in outputs.items():
        print(f"{fmt}: {path}")
    # scratch only (full-size silent intermediates) — pipeline convention is
    # to clean the per-run work dir once outputs are finalized
    shutil.rmtree(work_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
