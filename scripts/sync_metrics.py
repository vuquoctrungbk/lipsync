#!/usr/bin/env python
"""Objective lip-sync scoring: LSE-D / LSE-C via the SyncNet harness.

Shells into the ISOLATED venv at tools/syncnet/.venv (its deps conflict with
the app venv's pinned numpy/librosa — never import syncnet here) and parses
the scores from run_pipeline.py + run_syncnet.py.

Usage:
    python scripts/sync_metrics.py --video outputs/clip.mp4
    python scripts/sync_metrics.py --video long.mp4 --window 60 --json report.json

Notes:
    - LSE-D (lower = better sync; real talking heads land ~6.5-8) is SyncNet's
      min audio-visual distance; LSE-C (higher = better) is its confidence.
    - SyncNet is English-trained (VoxCeleb): treat scores as RELATIVE — compare
      engines/settings on the SAME clip. Absolute Vietnamese judgment lives in
      docs/vietnamese-validation-protocol.md.
    - --window N scores per-N-second segments for drift analysis on long
      renders (consumed by the app's opt-in "Analyze sync drift" action —
      never run automatically inside a render).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# torch-free import chain (lipsync/__init__.py is empty) — same pattern as
# tests/test_pipeline_e2e.py. One ffmpeg resolver for the whole repo.
from lipsync.ffmpeg_utils import ffmpeg_exe  # noqa: E402

SYNCNET_DIR = REPO_ROOT / "tools" / "syncnet" / "syncnet_python"
SYNCNET_PYTHON = REPO_ROOT / "tools" / "syncnet" / ".venv" / "Scripts" / "python.exe"

_MIN_DIST_RE = re.compile(r"Min dist:\s*([0-9]+(?:\.[0-9]+)?)")
_CONF_RE = re.compile(r"Confidence:\s*([0-9]+(?:\.[0-9]+)?)")
_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)")


class SyncMetricsError(Exception):
    pass


def parse_scores(log_text: str) -> dict:
    """Extract per-track scores from syncnet's combined stdout/stderr log.

    Multiple face tracks each print a 'Min dist' + 'Confidence' pair; the
    clip-level LSE-D/LSE-C is the mean across tracks (Wav2Lip convention).
    """
    dists = [float(m) for m in _MIN_DIST_RE.findall(log_text)]
    confs = [float(m) for m in _CONF_RE.findall(log_text)]
    if not dists:
        raise SyncMetricsError(
            "syncnet produced no face-track scores — face too small (<100px), "
            "track shorter than 4s, or no face found. Full log follows:\n"
            + log_text[-2000:]
        )
    return {
        "lse_d": round(statistics.fmean(dists), 3),
        "lse_c": round(statistics.fmean(confs), 3),
        "tracks": len(dists),
    }


def parse_duration(ffmpeg_stderr: str) -> float:
    """Duration in seconds from `ffmpeg -i` banner output (no ffprobe needed —
    imageio-ffmpeg installs ship none)."""
    m = _DURATION_RE.search(ffmpeg_stderr)
    if not m:
        raise SyncMetricsError("could not read duration from ffmpeg output")
    hh, mm, ss = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return hh * 3600 + mm * 60 + ss


def plan_windows(duration: float, window: int) -> list[tuple[float, float]]:
    """[(start, end)] covering the FULL clip (fractional tail included); a
    trailing remainder <25% of the window merges into the previous one (too
    short to score alone)."""
    if window <= 0 or duration <= window:
        return [(0.0, duration)]
    spans = []
    s = 0.0
    while s < duration:
        spans.append((s, min(s + window, duration)))
        s += window
    if len(spans) >= 2 and (spans[-1][1] - spans[-1][0]) < 0.25 * window:
        spans[-2] = (spans[-2][0], spans[-1][1])
        spans.pop()
    return spans


def _env_with_ffmpeg() -> dict:
    """syncnet scripts invoke bare `ffmpeg`; make our resolved one reachable.

    The imageio-ffmpeg fallback binary has a versioned exe name (e.g.
    ffmpeg-win64-v4.2.2.exe) that a bare `ffmpeg` call can never hit — fail
    loud instead of letting syncnet die with a confusing WinError.
    """
    exe = Path(ffmpeg_exe())
    env = os.environ.copy()
    if exe.stem.lower() == "ffmpeg":
        env["PATH"] = str(exe.parent) + os.pathsep + env.get("PATH", "")
    elif shutil.which("ffmpeg") is None:
        raise SyncMetricsError(
            "the harness needs a real `ffmpeg` on PATH — the imageio-ffmpeg "
            f"fallback binary ({exe.name}) cannot be invoked as `ffmpeg` by syncnet"
        )
    return env


def probe_duration_s(video: Path) -> float:
    proc = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-i", str(video)],
        capture_output=True, text=True,
    )
    # `ffmpeg -i` with no output exits non-zero by design; the banner is on stderr.
    return parse_duration(proc.stderr or "")


def score_video(video: Path, timeout_s: int = 3600) -> dict:
    """Run the 2-step syncnet pipeline on one video, return scores + timing."""
    if not SYNCNET_PYTHON.exists():
        raise SyncMetricsError(
            f"harness venv missing: {SYNCNET_PYTHON}\n"
            "Set it up per docs/vietnamese-validation-protocol.md (tools/syncnet)."
        )
    data_dir = Path(tempfile.mkdtemp(prefix="syncnet_"))
    ref = uuid.uuid4().hex[:8]
    log = ""
    t0 = time.time()
    try:
        for script in ("run_pipeline.py", "run_syncnet.py"):
            proc = subprocess.run(
                [str(SYNCNET_PYTHON), script,
                 "--videofile", str(video.resolve()),
                 "--reference", ref, "--data_dir", str(data_dir)],
                cwd=str(SYNCNET_DIR), env=_env_with_ffmpeg(),
                capture_output=True, text=True, timeout=timeout_s,
            )
            log += proc.stdout + proc.stderr
            if proc.returncode != 0:
                raise SyncMetricsError(
                    f"{script} failed (exit {proc.returncode}):\n{log[-2000:]}")
        scores = parse_scores(log)
        scores["scoring_s"] = round(time.time() - t0, 1)
        return scores
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)


def _cut_segment(video: Path, start: float, end: float, out: Path) -> None:
    subprocess.run(
        [ffmpeg_exe(), "-y", "-loglevel", "error",
         "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(video),
         "-c:v", "libx264", "-crf", "18", "-c:a", "aac", str(out)],
        check=True, capture_output=True,
    )


def analyze(video: Path, window: int = 0, timeout_s: int = 3600) -> dict:
    result: dict = {"video": str(video)}
    whole = score_video(video, timeout_s)
    result.update(whole)

    result["windows"] = []
    if window > 0:
        duration = probe_duration_s(video)
        seg_dir = Path(tempfile.mkdtemp(prefix="syncnet_win_"))
        try:
            for start, end in plan_windows(duration, window):
                seg = seg_dir / f"win_{int(start):05d}.mp4"
                _cut_segment(video, start, end, seg)
                try:
                    s = score_video(seg, timeout_s)
                    result["windows"].append(
                        {"start": start, "end": end,
                         "lse_d": s["lse_d"], "lse_c": s["lse_c"]})
                except SyncMetricsError as exc:
                    result["windows"].append(
                        {"start": start, "end": end, "error": str(exc)[:300]})
        finally:
            shutil.rmtree(seg_dir, ignore_errors=True)
    return result


def _print_table(res: dict) -> None:
    print(f"\nvideo:  {res['video']}")
    print(f"LSE-D:  {res['lse_d']}   (lower = better; real videos ~6.5-8)")
    print(f"LSE-C:  {res['lse_c']}   (higher = better)")
    print(f"tracks: {res['tracks']}   scored in {res['scoring_s']}s")
    if res.get("windows"):
        good = [w["lse_d"] for w in res["windows"] if "lse_d" in w]
        med = statistics.median(good) if good else float("nan")
        print(f"\nper-window drift (clip median LSE-D {med:.2f}):")
        print(f"{'window':>16}  {'LSE-D':>7}  {'LSE-C':>7}  vs median")
        for w in res["windows"]:
            span = f"{int(w['start'])}-{int(w['end'])}s"
            if "error" in w:
                print(f"{span:>16}  {'ERROR':>7}  {'':>7}  {w['error'][:60]}")
            else:
                delta = w["lse_d"] - med
                flag = "  <-- drift?" if delta > 1.0 else ""
                print(f"{span:>16}  {w['lse_d']:>7.3f}  {w['lse_c']:>7.3f}  "
                      f"{delta:+.2f}{flag}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--video", required=True, help="video file with speech audio")
    ap.add_argument("--window", type=int, default=0,
                    help="also score per-N-second windows (0 = whole clip only)")
    ap.add_argument("--json", dest="json_out", default=None,
                    help="write the result dict to this JSON file")
    ap.add_argument("--timeout", type=int, default=3600,
                    help="per-video scoring timeout, seconds")
    args = ap.parse_args(argv)

    video = Path(args.video)
    if not video.exists():
        print(f"error: video not found: {video}", file=sys.stderr)
        return 2
    try:
        res = analyze(video, window=args.window, timeout_s=args.timeout)
    except SyncMetricsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired:
        print(f"error: scoring exceeded --timeout {args.timeout}s", file=sys.stderr)
        return 1

    _print_table(res)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(res, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
