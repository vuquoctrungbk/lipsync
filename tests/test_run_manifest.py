"""Manifest lifecycle: integrity binding, resume validation, retention.

Covers the red-team scenarios: fresh run, resume-skip-done, corrupt segment
demotion, coeff sha mismatch -> full demotion, fingerprint mismatch -> new
run, composite-only knob change -> SAME fingerprint, dead/live owner_pid.
No models; segment "videos" are tiny real mp4s only where frame counts matter.
"""
import os
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lipsync import run_manifest as rm  # noqa: E402
from lipsync.config import RenderConfig  # noqa: E402


def _mk_run(tmp_path, name="run_20260703_000000_aaaaaa", rows=100,
            owner_pid=None, segments=None) -> tuple[Path, dict]:
    from scipy.io import savemat

    run_dir = tmp_path / name
    (run_dir / "sadtalker").mkdir(parents=True)
    coeff = run_dir / "sadtalker" / "coeff.mat"
    rng = np.random.default_rng(1)
    savemat(str(coeff), {"coeff_3dmm": rng.normal(size=(rows, 70))})

    segments = segments or [
        {"idx": 0, "start_f": 0, "end_f": 50, "halo_start": 0, "halo_end": 63,
         "path": "seg_000.mp4", "frames_expected": 63, "status": "pending"},
        {"idx": 1, "start_f": 50, "end_f": 100, "halo_start": 37, "halo_end": 100,
         "path": "seg_001.mp4", "frames_expected": 63, "status": "pending"},
    ]
    m = rm.new_manifest("img1", "aud1", RenderConfig(),
                        coeff_rel_path="sadtalker/coeff.mat",
                        coeff_sha1=rm.sha1_file(coeff), coeff_rows=rows,
                        segments=segments)
    if owner_pid is not None:
        m["owner_pid"] = owner_pid
    rm.write_manifest(run_dir, m)
    return run_dir, m


def test_write_is_atomic_and_loads_back(tmp_path):
    run_dir, m = _mk_run(tmp_path)
    assert not list(run_dir.glob("*.tmp")), "temp manifest must be renamed away"
    loaded = rm.load_manifest(run_dir)
    assert loaded["coeff"]["rows"] == 100
    assert loaded["inputs"]["cfg_fingerprint"] == rm.cfg_fingerprint(RenderConfig())


def test_load_rejects_corrupt_manifest(tmp_path):
    d = tmp_path / "run_x"
    d.mkdir()
    (d / rm.MANIFEST_NAME).write_text("{not json", encoding="utf-8")
    assert rm.load_manifest(d) is None
    (d / rm.MANIFEST_NAME).write_text('{"segments": []}', encoding="utf-8")
    assert rm.load_manifest(d) is None  # missing coeff key


def test_fingerprint_excludes_composite_only_knobs():
    base = rm.cfg_fingerprint(RenderConfig())
    # composite-only knobs: changing them must NOT orphan rendered segments
    assert rm.cfg_fingerprint(RenderConfig(green_rgb=(9, 9, 9))) == base
    assert rm.cfg_fingerprint(RenderConfig(crf=30)) == base
    assert rm.cfg_fingerprint(RenderConfig(output_format="both")) == base
    # render-affecting knobs: MUST change the fingerprint
    assert rm.cfg_fingerprint(RenderConfig(face_size=512)) != base
    assert rm.cfg_fingerprint(RenderConfig(seed=7)) != base
    assert rm.cfg_fingerprint(RenderConfig(use_enhancer=True)) != base
    assert rm.cfg_fingerprint(RenderConfig(chunk_seconds=30)) != base


def test_coeff_valid_and_sha_mismatch(tmp_path):
    run_dir, m = _mk_run(tmp_path)
    assert rm.coeff_valid(m, run_dir) is True

    # tamper with the mat -> sha mismatch -> whole run demoted
    coeff = run_dir / "sadtalker" / "coeff.mat"
    coeff.write_bytes(coeff.read_bytes() + b"x")
    assert rm.coeff_valid(m, run_dir) is False


def test_coeff_path_escape_rejected(tmp_path):
    run_dir, m = _mk_run(tmp_path)
    m["coeff"]["path"] = "../../outside.mat"
    assert rm.coeff_valid(m, run_dir) is False


def test_validate_segments_demotes_missing_and_wrong_count(tmp_path):
    import imageio

    run_dir, m = _mk_run(tmp_path)
    # segment 0: real mp4 with the EXPECTED frame count
    w = imageio.get_writer(str(run_dir / "seg_000.mp4"), fps=5,
                           codec="libx264", macro_block_size=None)
    for _ in range(63):
        w.append_data(np.zeros((32, 32, 3), dtype=np.uint8))
    w.close()
    # segment 1: wrong frame count (truncated render)
    w = imageio.get_writer(str(run_dir / "seg_001.mp4"), fps=5,
                           codec="libx264", macro_block_size=None)
    for _ in range(10):
        w.append_data(np.zeros((32, 32, 3), dtype=np.uint8))
    w.close()

    m["segments"][0]["status"] = "done"
    m["segments"][1]["status"] = "done"
    rm.validate_segments(m, run_dir)
    assert m["segments"][0]["status"] == "done"
    assert m["segments"][1]["status"] == "pending", "truncated segment must demote"


def test_find_resumable_matches_and_respects_owner(tmp_path):
    fp = rm.cfg_fingerprint(RenderConfig())

    # dead owner (pid 1 is never a live user process we own on Windows;
    # use an impossible pid to be deterministic)
    run_dir, m = _mk_run(tmp_path, owner_pid=999999999)
    rm.write_manifest(run_dir, m)
    found = rm.find_resumable(tmp_path, "img1", "aud1", fp)
    assert found is not None and found[0] == run_dir

    # live owner in ANOTHER process -> refuse adoption (do not steal its run)
    import subprocess
    import sys as _sys

    sleeper = subprocess.Popen([_sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        m["owner_pid"] = sleeper.pid
        rm.write_manifest(run_dir, m)
        assert rm.find_resumable(tmp_path, "img1", "aud1", fp) is None
    finally:
        sleeper.kill()
        sleeper.wait()

    # fingerprint mismatch -> not resumable
    m["owner_pid"] = 999999999
    m["inputs"]["cfg_fingerprint"] = "other"
    rm.write_manifest(run_dir, m)
    assert rm.find_resumable(tmp_path, "img1", "aud1", fp) is None

    # ALL-DONE run -> STILL resumable: it means the crash happened during
    # compositing (the most expensive state to lose); the segment loop no-ops
    # and compositing restarts from the segment iterator.
    m["inputs"]["cfg_fingerprint"] = fp
    for s in m["segments"]:
        s["status"] = "done"
    rm.write_manifest(run_dir, m)
    found = rm.find_resumable(tmp_path, "img1", "aud1", fp)
    assert found is not None and found[0] == run_dir


def test_own_dead_run_is_adoptable_by_same_process(tmp_path):
    """A failed run() in THIS process leaves owner_pid == our live pid; the
    retry must adopt it (the render lock rules out a concurrent self-run)."""
    fp = rm.cfg_fingerprint(RenderConfig())
    run_dir, m = _mk_run(tmp_path, owner_pid=os.getpid())
    rm.write_manifest(run_dir, m)
    found = rm.find_resumable(tmp_path, "img1", "aud1", fp)
    assert found is not None and found[0] == run_dir


def test_corrupt_owner_pid_does_not_crash(tmp_path):
    fp = rm.cfg_fingerprint(RenderConfig())
    run_dir, m = _mk_run(tmp_path, owner_pid=999999999)
    m["owner_pid"] = "not-a-pid"
    rm.write_manifest(run_dir, m)
    found = rm.find_resumable(tmp_path, "img1", "aud1", fp)
    assert found is not None  # corrupt pid -> treated as unowned, adoptable


def test_purge_stale_runs_keeps_current_and_other_inputs(tmp_path):
    fp = rm.cfg_fingerprint(RenderConfig())
    old1, m1 = _mk_run(tmp_path, name="run_1_old", owner_pid=999999999)
    old2, m2 = _mk_run(tmp_path, name="run_2_other", owner_pid=999999999)
    m2["inputs"]["audio_sha1"] = "DIFFERENT"
    rm.write_manifest(old2, m2)
    keep, _ = _mk_run(tmp_path, name="run_3_keep", owner_pid=999999999)

    rm.purge_stale_runs(tmp_path, "img1", "aud1", fp, keep=keep)
    assert not old1.exists(), "stale same-input run must be purged"
    assert old2.exists(), "different-input run must survive"
    assert keep.exists(), "current run must survive"


def test_pid_alive_basics():
    assert rm.pid_alive(os.getpid()) is True
    assert rm.pid_alive(999999999) is False
    assert rm.pid_alive(0) is False
    assert rm.pid_alive(-5) is False


def test_fingerprint_fields_stay_exhaustive():
    """Every RenderConfig field must be CLASSIFIED: either render-affecting
    (in the fingerprint) or composite-only (safe to change mid-resume). A new
    field failing this test forces the author to decide — silently escaping
    the fingerprint would let stale segments be adopted for a different look."""
    from dataclasses import fields

    composite_only = {
        # changing these must NOT invalidate rendered segments
        "green_rgb", "crf", "output_format", "output_dir",
        # engine plumbing that does not alter SadTalker's frames
        "batch_size", "sequential_vram", "matting_engine", "commercial_safe",
    }
    all_fields = {f.name for f in fields(RenderConfig)}
    classified = set(rm._FINGERPRINT_FIELDS) | composite_only
    unclassified = all_fields - classified
    assert not unclassified, (
        f"new RenderConfig field(s) {sorted(unclassified)} must be added to "
        "either _FINGERPRINT_FIELDS (render-affecting) or the composite_only "
        "set in this test (resume-safe)")
    assert set(rm._FINGERPRINT_FIELDS) <= all_fields, "fingerprint names drifted"
