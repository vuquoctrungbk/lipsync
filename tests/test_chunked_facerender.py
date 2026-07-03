"""Halo slicing math + segment iteration. No models, no GPU.

The window-equivalence test is the load-bearing one: it reimplements the
vendored SadTalker clamped-window indexing (generate_facerender_batch.py:93-98,
semantic_radius=13) and proves that for EVERY KEPT frame, the ±13-row context
computed from a haloed slice equals the context from the full mat — i.e. the
chunk boundaries are mathematically invisible.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lipsync.chunked_facerender import (  # noqa: E402
    HALO_FRAMES,
    plan_segments,
    slice_coeff_mat,
)


def _plan(rows, kept=100):
    # chunk_seconds path: kept = chunk_seconds * fps -> use fps=1 for exact control
    return plan_segments(rows, 256, (512, 512), free_vram=0, free_ram=0,
                         chunk_seconds=kept, fps=1)


def test_kept_ranges_tile_exactly():
    for rows in (1, 99, 100, 101, 250, 1000):
        segs = _plan(rows, kept=100)
        assert segs[0]["start_f"] == 0
        assert segs[-1]["end_f"] == rows
        for a, b in zip(segs, segs[1:]):
            assert a["end_f"] == b["start_f"], "gap/overlap in kept ranges"


def test_halo_bounds_clamped_and_limited():
    segs = _plan(1000, kept=100)
    for s in segs:
        assert 0 <= s["halo_start"] <= s["start_f"]
        assert s["end_f"] <= s["halo_end"] <= 1000
        assert s["start_f"] - s["halo_start"] <= HALO_FRAMES
        assert s["halo_end"] - s["end_f"] <= HALO_FRAMES
        assert s["frames_expected"] == s["halo_end"] - s["halo_start"]
    # raw slices overlap by at most 2*halo
    for a, b in zip(segs, segs[1:]):
        assert a["halo_end"] - b["halo_start"] <= 2 * HALO_FRAMES


def test_single_segment_when_clip_fits():
    segs = _plan(80, kept=100)
    assert len(segs) == 1
    assert segs[0]["halo_start"] == 0 and segs[0]["halo_end"] == 80


def test_empty_mat_yields_no_segments():
    assert _plan(0, kept=100) == []


def test_sizing_clamps_by_vram_and_ram():
    # tiny VRAM -> small segments (predictions stack n*3*S*S*4B)
    segs = plan_segments(10_000, 256, (1080, 1920),
                         free_vram=200 * 1024 * 1024, free_ram=0)
    kept = segs[0]["end_f"] - segs[0]["start_f"]
    assert kept < 1250, f"VRAM clamp did not bite: {kept}"

    # tiny RAM + enhancer -> even smaller (paste-back buffers n*H*W*3B x2)
    segs2 = plan_segments(10_000, 256, (2400, 1600),
                          free_vram=0, free_ram=2 * 1024**3, use_enhancer=True)
    kept2 = segs2[0]["end_f"] - segs2[0]["start_f"]
    assert kept2 <= kept
    assert kept2 >= 125, "sizing must keep a sane floor"


# ---------------------------------------------------------------------------
# window equivalence — the seam guarantee
# ---------------------------------------------------------------------------

def _vendored_window(mat: np.ndarray, frame_idx: int) -> np.ndarray:
    """Transcription of generate_facerender_batch.py:93-98 (clamped ±13)."""
    num = mat.shape[0]
    index = [min(max(item, 0), num - 1)
             for item in range(frame_idx - HALO_FRAMES, frame_idx + HALO_FRAMES + 1)]
    return mat[index, :]


@pytest.mark.parametrize("rows,kept", [(260, 100), (1000, 250), (137, 50), (75, 100)])
def test_window_equivalence_full_vs_sliced(rows, kept):
    rng = np.random.default_rng(42)
    full = rng.normal(size=(rows, 70)).astype(np.float32)

    for seg in _plan(rows, kept=kept):
        sliced = full[seg["halo_start"]:seg["halo_end"]]
        for g in range(seg["start_f"], seg["end_f"]):
            local = g - seg["halo_start"]
            want = _vendored_window(full, g)
            got = _vendored_window(sliced, local)
            assert np.array_equal(want, got), (
                f"context mismatch at global frame {g} "
                f"(segment {seg['idx']}, rows={rows}, kept={kept})")


def test_slice_coeff_mat_roundtrip(tmp_path):
    from scipy.io import loadmat, savemat

    rng = np.random.default_rng(7)
    full = rng.normal(size=(300, 70)).astype(np.float64)
    src = tmp_path / "full.mat"
    savemat(str(src), {"coeff_3dmm": full})

    out = slice_coeff_mat(src, 87, 213, tmp_path / "slice.mat")
    back = loadmat(str(out))["coeff_3dmm"]
    assert back.shape == (126, 70)
    assert np.allclose(back, full[87:213])
    assert not list(tmp_path.glob("*.tmp.mat")), "atomic write must clean temp"


def test_slice_coeff_mat_rejects_bad_bounds(tmp_path):
    from scipy.io import savemat

    from lipsync.chunked_facerender import SegmentRenderError

    savemat(str(tmp_path / "f.mat"),
            {"coeff_3dmm": np.zeros((10, 70), dtype=np.float32)})
    with pytest.raises(SegmentRenderError, match="out of bounds"):
        slice_coeff_mat(tmp_path / "f.mat", 5, 20, tmp_path / "s.mat")


def test_iter_segment_frames_drops_halo(tmp_path):
    """Segments rendered as videos with per-frame identifiable content:
    iterating must yield exactly the kept global frames, in order."""
    import imageio

    from lipsync.chunked_facerender import iter_segment_frames

    rows, kept = 50, 20
    segs = _plan(rows, kept=kept)
    for seg in segs:
        # encode frames whose MEAN gray encodes the global frame index
        w = imageio.get_writer(str(tmp_path / seg["path"]), fps=5,
                               codec="libx264", macro_block_size=None,
                               output_params=["-crf", "0"])  # lossless-ish
        for g in range(seg["halo_start"], seg["halo_end"]):
            w.append_data(np.full((32, 32, 3), g * 4, dtype=np.uint8))
        w.close()
        seg["status"] = "done"

    got = [int(round(f.mean() / 4))
           for f in iter_segment_frames(segs, tmp_path)]
    assert got == list(range(rows)), f"halo frames leaked or kept frames lost: {got[:60]}"
