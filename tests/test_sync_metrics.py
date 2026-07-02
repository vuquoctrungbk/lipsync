"""Unit tests for scripts/sync_metrics.py — parsing + window math only.

No syncnet install or model needed (that path is exercised manually per
docs/vietnamese-validation-protocol.md).
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from sync_metrics import (  # noqa: E402
    SyncMetricsError,
    parse_duration,
    parse_scores,
    plan_windows,
)


def test_parse_scores_single_track():
    log = (
        "2026-07-03 01:00:00 __main__ INFO: AV offset: \t1\n"
        "2026-07-03 01:00:00 __main__ INFO: Min dist: \t7.312\n"
        "2026-07-03 01:00:00 __main__ INFO: Confidence: \t5.421\n"
    )
    s = parse_scores(log)
    assert s == {"lse_d": 7.312, "lse_c": 5.421, "tracks": 1}


def test_parse_scores_multi_track_averages():
    log = (
        "Min dist: \t6.0\nConfidence: \t4.0\n"
        "Min dist: \t8.0\nConfidence: \t6.0\n"
    )
    s = parse_scores(log)
    assert s["lse_d"] == 7.0 and s["lse_c"] == 5.0 and s["tracks"] == 2


def test_parse_scores_no_tracks_raises():
    with pytest.raises(SyncMetricsError, match="no face-track scores"):
        parse_scores("some ffmpeg noise, no scores here")


def test_parse_duration_from_ffmpeg_banner():
    stderr = (
        "Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'x.mp4':\n"
        "  Duration: 00:02:20.52, start: 0.000000, bitrate: 1274 kb/s\n"
    )
    assert parse_duration(stderr) == pytest.approx(140.52)


def test_parse_duration_missing_raises():
    with pytest.raises(SyncMetricsError):
        parse_duration("no duration line")


def test_plan_windows_short_clip_single_window():
    assert plan_windows(30.0, 60) == [(0.0, 30.0)]


def test_plan_windows_exact_multiple():
    assert plan_windows(120.0, 60) == [(0.0, 60.0), (60.0, 120.0)]


def test_plan_windows_merges_tiny_tail():
    # 130s: the 10s tail (<25% of 60) merges into the second window
    assert plan_windows(130.0, 60) == [(0.0, 60.0), (60.0, 130.0)]


def test_plan_windows_keeps_fractional_tail():
    # a sub-second remainder must still be covered, merged into the last window
    assert plan_windows(60.5, 60) == [(0.0, 60.5)]
    assert plan_windows(120.9, 60) == [(0.0, 60.0), (60.0, 120.9)]


def test_plan_windows_keeps_substantial_tail():
    # 100s: the 40s tail stands alone
    assert plan_windows(100.0, 60) == [(0.0, 60.0), (60.0, 100.0)]


def test_plan_windows_zero_disables():
    assert plan_windows(500.0, 0) == [(0.0, 500.0)]
