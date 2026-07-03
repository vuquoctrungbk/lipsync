"""Chunked facerender: renders long audio in halo-overlapped coeff segments.

Why halos: SadTalker conditions EVERY frame on a ±13-row window of the coeff
mat (`semantic_radius = 13` in the vendored `generate_facerender_batch.py`;
indices clamp at mat edges). Slicing the mat naively would make the first/last
13 frames of each segment render against edge-replicated context — visible
expression stutter at every boundary. So segments render WITH a ±13-frame
halo, and the halo frames are dropped when the segments are consumed. There
is deliberately NO concat step: the compositor streams straight from
`iter_segment_frames`, which makes trimming frame-accurate and free.

Frame counts always come from MAT ROWS, never from probed audio duration.
"""
from __future__ import annotations

import os
import wave
from pathlib import Path
from typing import Iterator

import imageio
import numpy as np

# semantic_radius in the vendored SadTalker (generate_facerender_batch.py:12)
HALO_FRAMES = 13

# Default KEPT frames per segment, sized from the measured VRAM/RAM profile
# (facerender stacks predictions n*3*S*S*4B on GPU; paste-back buffers
# n*H*W*3B in RAM at source resolution — enhancer buffers that again).
DEFAULT_KEPT = {256: 1250, 512: 750}

# facerender VRAM overhead beyond the predictions stack (models + activations),
# and the safety fraction of free memory we allow a segment to claim.
_VRAM_BUDGET_FRACTION = 0.6
_RAM_BUDGET_FRACTION = 0.5


class SegmentRenderError(Exception):
    pass


def plan_segments(mat_rows: int, face_size: int, src_hw: tuple[int, int],
                  free_vram: int, free_ram: int, chunk_seconds: int = 0,
                  use_enhancer: bool = False, fps: int = 25) -> list[dict]:
    """Split `mat_rows` kept frames into halo-overlapped segments.

    Kept ranges tile [0, mat_rows) exactly (no gap, no overlap); raw ranges
    add a ±HALO_FRAMES halo clamped to the mat bounds.
    """
    if mat_rows <= 0:
        return []

    if chunk_seconds > 0:
        kept = max(1, chunk_seconds * fps)
    else:
        kept = DEFAULT_KEPT.get(face_size, DEFAULT_KEPT[256])
        # VRAM term: predictions stack is n*3*S*S*4 bytes fp32
        if free_vram > 0:
            frame_vram = 3 * face_size * face_size * 4
            kept = min(kept, max(125, int(free_vram * _VRAM_BUDGET_FRACTION / frame_vram)))
        # RAM term: paste-back buffers n*H*W*3 bytes at SOURCE resolution;
        # the enhancer chain holds a second copy of the segment
        if free_ram > 0:
            h, w = src_hw
            frame_ram = h * w * 3 * (2 if use_enhancer else 1)
            kept = min(kept, max(125, int(free_ram * _RAM_BUDGET_FRACTION / frame_ram)))

    segments = []
    start = 0
    idx = 0
    while start < mat_rows:
        end = min(start + kept, mat_rows)
        segments.append({
            "idx": idx,
            "start_f": start,
            "end_f": end,
            "halo_start": max(0, start - HALO_FRAMES),
            "halo_end": min(mat_rows, end + HALO_FRAMES),
            "path": f"seg_{idx:03d}.mp4",
            "frames_expected": min(mat_rows, end + HALO_FRAMES) - max(0, start - HALO_FRAMES),
            "status": "pending",
        })
        start = end
        idx += 1
    return segments


def slice_coeff_mat(coeff_path: str | Path, halo_start: int, halo_end: int,
                    out_path: str | Path) -> Path:
    """Write rows [halo_start, halo_end) of the coeff mat to out_path
    (atomic: temp + os.replace)."""
    from scipy.io import loadmat, savemat

    full = loadmat(str(coeff_path))["coeff_3dmm"]
    if not (0 <= halo_start < halo_end <= full.shape[0]):
        raise SegmentRenderError(
            f"slice [{halo_start}:{halo_end}) out of bounds for mat rows {full.shape[0]}")
    out = Path(out_path)
    tmp = out.with_suffix(".tmp.mat")
    savemat(str(tmp), {"coeff_3dmm": full[halo_start:halo_end]})
    os.replace(tmp, out)
    return out


def write_silent_wav(path: str | Path, seconds: float = 1.0, rate: int = 16000) -> Path:
    """1s of 16k mono silence — the per-segment mux input.

    SadTalker's vendored mux decodes its WHOLE audio argument per call (pydub)
    and has no `-shortest`, so a short silent wav (a) avoids re-decoding the
    full 600s file once per segment and (b) cannot truncate the video.
    """
    path = Path(path)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return path


def render_segment(engine, bundle: dict, seg: dict, coeff_slice: Path,
                   silent_wav: Path, run_dir: Path) -> Path:
    """Render one halo segment via the engine's facerender step.

    Runs under a chdir guard: the vendored paste-back writes CWD-relative
    uuid temp files — they must land in the run dir, not wherever the app was
    launched from. Post-checks are fail-loud because the vendored mux ignores
    ffmpeg exit codes (a truncated segment must die HERE, not poison the
    final video).
    """
    from .ffmpeg_utils import has_ffprobe
    from .run_manifest import _ffprobe_frame_count

    seg_dir = run_dir / f"segwork_{seg['idx']:03d}"
    seg_dir.mkdir(parents=True, exist_ok=True)

    prev_cwd = os.getcwd()
    os.chdir(run_dir)
    try:
        out = engine.render_from_coeff(bundle, coeff_slice, silent_wav, seg_dir)
    finally:
        os.chdir(prev_cwd)

    final = run_dir / seg["path"]
    os.replace(out, final)

    if not final.exists() or final.stat().st_size == 0:
        raise SegmentRenderError(f"segment {seg['idx']} produced no output")
    if not has_ffprobe():
        raise SegmentRenderError(
            "chunked rendering requires ffprobe for fail-loud segment "
            "verification — install full ffmpeg (winget install ffmpeg)")
    n = _ffprobe_frame_count(final)
    if n != seg["frames_expected"]:
        raise SegmentRenderError(
            f"segment {seg['idx']} frame count {n} != expected "
            f"{seg['frames_expected']} (vendored mux truncation?)")
    return final


def iter_segment_frames(segments: list[dict], run_dir: Path) -> Iterator[np.ndarray]:
    """Stream KEPT frames of done segments in order, dropping halo frames.

    Feeds the compositor directly — no concat file, frame-accurate trims.
    """
    for seg in sorted(segments, key=lambda s: s["idx"]):
        drop_head = seg["start_f"] - seg["halo_start"]
        keep = seg["end_f"] - seg["start_f"]
        reader = imageio.get_reader(str(run_dir / seg["path"]), "ffmpeg")
        try:
            for i, frame in enumerate(reader):
                if i < drop_head:
                    continue
                if i >= drop_head + keep:
                    break
                yield frame
        finally:
            reader.close()
