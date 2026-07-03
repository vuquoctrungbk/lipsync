"""Run manifest: crash-safe bookkeeping for chunked long-audio renders.

A manifest binds every rendered segment to ONE audio2coeff output (sha1+rows)
— SadTalker's audio2coeff is non-deterministic (unseeded CVAE pose noise +
blink sampling), so segments from two different coeff runs must never be
stitched together. Any coeff-side doubt demotes the WHOLE run; per-segment
doubt demotes just that segment.

The fingerprint contains EXACTLY the render-affecting config fields.
Composite-only knobs (green color, crf, output_format) are excluded on
purpose: changing them must not orphan hours of rendered segments.

All writes go through temp + os.replace (atomic on NTFS). Progress ("stage")
is always DERIVED from disk facts — there is no stored stage field to drift.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

from .config import RenderConfig
from .ffmpeg_utils import ffprobe_exe

MANIFEST_NAME = "manifest.json"

# The exact render-affecting fields (red-team finding: enumerate, don't guess).
_FINGERPRINT_FIELDS = ("face_size", "preprocess", "still_mode", "pose_style",
                       "expression_scale", "use_enhancer", "precision", "fps",
                       "chunk_seconds", "seed")


class ManifestError(Exception):
    pass


def sha1_file(path: str | Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def cfg_fingerprint(cfg: RenderConfig) -> str:
    fields = {k: getattr(cfg, k) for k in _FINGERPRINT_FIELDS}
    return json.dumps(fields, sort_keys=True, default=str)


def input_key(image_sha1: str, audio_sha1: str, fingerprint: str) -> str:
    """Stable identity of (inputs, render settings) — retention is keyed on it."""
    return hashlib.sha1(
        f"{image_sha1}|{audio_sha1}|{fingerprint}".encode()).hexdigest()[:16]


def pid_alive(pid: int) -> bool:
    """Conservative liveness probe. NEVER use os.kill(pid, 0) on Windows — it
    TERMINATES the target. PID reuse can report a dead owner as alive; that
    fails safe (we refuse to adopt, user gets a clear message)."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def new_manifest(image_sha1: str, audio_sha1: str, cfg: RenderConfig,
                 coeff_rel_path: str, coeff_sha1: str, coeff_rows: int,
                 segments: list[dict]) -> dict:
    return {
        "owner_pid": os.getpid(),
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "inputs": {
            "image_sha1": image_sha1,
            "audio_sha1": audio_sha1,
            "cfg_fingerprint": cfg_fingerprint(cfg),
            # full cfg so the UI Resume button can reconstruct the render
            # without re-uploads (fingerprint stays the strict subset above)
            "cfg": {k: (str(v) if isinstance(v, Path) else v)
                    for k, v in asdict(cfg).items()},
        },
        "coeff": {"path": coeff_rel_path, "sha1": coeff_sha1, "rows": coeff_rows},
        "segments": segments,
    }


def write_manifest(run_dir: str | Path, manifest: dict) -> None:
    run_dir = Path(run_dir)
    tmp = run_dir / (MANIFEST_NAME + ".tmp")
    tmp.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    os.replace(tmp, run_dir / MANIFEST_NAME)


def load_manifest(run_dir: str | Path) -> dict | None:
    p = Path(run_dir) / MANIFEST_NAME
    if not p.exists():
        return None
    try:
        m = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(m, dict) or "segments" not in m or "coeff" not in m:
        return None
    return m


def _ffprobe_frame_count(path: Path) -> int | None:
    pe = ffprobe_exe()
    if not pe:
        return None
    proc = subprocess.run(
        [pe, "-v", "error", "-select_streams", "v:0", "-count_frames",
         "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return None


def _path_under(base: Path, rel: str) -> Path | None:
    """Resolve rel inside base; None if it escapes (corrupt/hostile manifest)."""
    candidate = (base / rel).resolve()
    try:
        candidate.relative_to(base.resolve())
    except ValueError:
        return None
    return candidate


def validate_segments(manifest: dict, run_dir: Path) -> dict:
    """Demote any 'done' segment that fails disk validation back to pending.

    Fail-loud philosophy: a segment only counts as done if its file exists,
    is non-empty, AND ffprobe confirms the exact halo-span frame count
    (SadTalker's vendored mux ignores exit codes — a truncated mp4 would
    otherwise poison the final video)."""
    for seg in manifest["segments"]:
        if seg.get("status") != "done":
            continue
        ok = False
        p = _path_under(run_dir, seg.get("path", ""))
        if p is not None and p.exists() and p.stat().st_size > 0:
            n = _ffprobe_frame_count(p)
            ok = (n == seg["frames_expected"])
        if not ok:
            seg["status"] = "pending"
    return manifest


def coeff_valid(manifest: dict, run_dir: Path) -> bool:
    """The coeff mat must resolve inside the run dir, loadmat cleanly with
    shape (rows, 70), and match the recorded sha1. ANY failure here means the
    whole run restarts under a NEW run id (never re-run audio2coeff into an
    existing run — the new mat would not match the old segments)."""
    info = manifest["coeff"]
    p = _path_under(run_dir, info.get("path", ""))
    if p is None or not p.exists():
        return False
    if sha1_file(p) != info.get("sha1"):
        return False
    try:
        from scipy.io import loadmat

        arr = loadmat(str(p)).get("coeff_3dmm")
    except Exception:  # noqa: BLE001 - any parse failure demotes the run
        return False
    return arr is not None and arr.ndim == 2 and arr.shape == (info.get("rows"), 70)


def _owner_blocks_adoption(manifest: dict) -> bool:
    """A run is adoptable when its owner is dead OR is THIS process (a failed
    run() left it behind; the pipeline's render lock rules out a concurrent
    self-render). A different LIVE process keeps ownership."""
    try:
        pid = int(manifest.get("owner_pid", 0))
    except (TypeError, ValueError):
        return False  # corrupt pid -> nobody owns it
    if pid == os.getpid():
        return False
    return pid_alive(pid)


def find_resumable(temp_dir: str | Path, image_sha1: str, audio_sha1: str,
                   fingerprint: str) -> tuple[Path, dict] | None:
    """Newest adoptable run dir with matching inputs.

    ALL-DONE manifests are included on purpose: they mean the crash happened
    during compositing — the most expensive state to lose. The segment loop
    no-ops on them and compositing restarts from the segment iterator.
    """
    temp_dir = Path(temp_dir)
    candidates: list[tuple[float, Path, dict]] = []
    for d in temp_dir.glob("run_*"):
        m = load_manifest(d)
        if m is None:
            continue
        ins = m.get("inputs", {})
        if (ins.get("image_sha1") != image_sha1
                or ins.get("audio_sha1") != audio_sha1
                or ins.get("cfg_fingerprint") != fingerprint):
            continue
        if _owner_blocks_adoption(m):
            continue
        candidates.append((d.stat().st_mtime, d, m))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0])
    return candidates[-1][1], candidates[-1][2]


def purge_stale_runs(temp_dir: str | Path, image_sha1: str, audio_sha1: str,
                     fingerprint: str, keep: Path | None = None) -> None:
    """Retention: at most ONE incomplete run dir per input identity."""
    import shutil

    temp_dir = Path(temp_dir)
    for d in temp_dir.glob("run_*"):
        if keep is not None and d.resolve() == Path(keep).resolve():
            continue
        m = load_manifest(d)
        if m is None:
            continue  # not a chunked run dir (single-shot work dirs stay)
        ins = m.get("inputs", {})
        if (ins.get("image_sha1") == image_sha1
                and ins.get("audio_sha1") == audio_sha1
                and ins.get("cfg_fingerprint") == fingerprint
                and not _owner_blocks_adoption(m)):
            shutil.rmtree(d, ignore_errors=True)
