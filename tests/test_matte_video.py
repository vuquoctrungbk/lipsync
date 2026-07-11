"""scripts/matte_video.py — arg contract + opt-in real matte smoke.

Unit tests stay torch-free: the script defers heavy imports into main(),
so loading the module and exercising parse_args/build_config must not pull
in the matting stack. The E2E smoke (RUN_E2E=1) mattes a real spike clip
through RVM end to end.
"""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "matte_video.py"

spec = importlib.util.spec_from_file_location("matte_video", SCRIPT)
matte_video = importlib.util.module_from_spec(spec)
spec.loader.exec_module(matte_video)


@pytest.fixture()
def fake_video(tmp_path):
    v = tmp_path / "clip.mp4"
    v.write_bytes(b"\x00")
    return v


def test_module_import_stays_light():
    # Deferred heavy imports: in a CLEAN interpreter (this process may already
    # have torch loaded by other tests), loading the script must not drag it in.
    probe = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('mv', {str(SCRIPT)!r})\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(m)\n"
        "sys.exit(1 if 'torch' in sys.modules else 0)\n"
    )
    rc = subprocess.run([sys.executable, "-c", probe], timeout=60).returncode
    assert rc == 0, "importing matte_video pulled torch at module level"


def test_parse_args_defaults(fake_video):
    args = matte_video.parse_args(["--video", str(fake_video)])
    assert args.video == fake_video
    assert args.audio is None            # None -> mux from the video itself
    assert args.engine == "rvm"
    assert args.output_format == "green_mp4"
    assert args.commercial_safe is False


def test_parse_args_rejects_missing_video(tmp_path):
    with pytest.raises(SystemExit):
        matte_video.parse_args(["--video", str(tmp_path / "nope.mp4")])


def test_parse_args_rejects_missing_audio(fake_video, tmp_path):
    with pytest.raises(SystemExit):
        matte_video.parse_args(
            ["--video", str(fake_video), "--audio", str(tmp_path / "nope.wav")])


def test_parse_args_rejects_unknown_engine(fake_video):
    with pytest.raises(SystemExit):
        matte_video.parse_args(["--video", str(fake_video), "--engine", "wav2lip"])


def test_parse_args_alpha_from(fake_video, tmp_path):
    with pytest.raises(SystemExit):
        matte_video.parse_args(
            ["--video", str(fake_video), "--alpha-from", str(tmp_path / "nope.mp4")])
    src = tmp_path / "clean.mp4"
    src.write_bytes(b"\x00")
    args = matte_video.parse_args(["--video", str(fake_video), "--alpha-from", str(src)])
    assert args.alpha_from == src


def test_plan_alignment_equal_and_alpha_longer():
    assert matte_video.plan_alignment(510, 25.0, 510, 25.0) == 510
    assert matte_video.plan_alignment(510, 25.0, 512, 25.0) == 510


def test_plan_alignment_trims_codec_padding():
    # LatentSync pads output to /16 — the real gate clips were 512 vs 510
    assert matte_video.plan_alignment(512, 25.0, 510, 25.0) == 510


def test_plan_alignment_rejects_big_gap():
    with pytest.raises(ValueError, match="different clip"):
        matte_video.plan_alignment(512, 25.0, 400, 25.0)


def test_plan_alignment_rejects_fps_mismatch():
    with pytest.raises(ValueError, match="fps"):
        matte_video.plan_alignment(510, 25.0, 510, 24.0)


def test_plan_alignment_unknown_counts_pass_through():
    assert matte_video.plan_alignment(None, 25.0, 510, 25.0) is None


def test_build_config_passes_knobs_through():
    cfg = matte_video.build_config("birefnet", "both", True)
    assert cfg.matting_engine == "birefnet"
    assert cfg.output_format == "both"
    assert cfg.commercial_safe is True


def test_default_run_id_shape():
    run_id = matte_video.default_run_id()
    # matches the app's outputs/lipsync_green_<YYYYmmdd_HHMMSS_hex6> naming
    stamp, clock, suffix = run_id.split("_")
    assert len(stamp) == 8 and stamp.isdigit()
    assert len(clock) == 6 and clock.isdigit()
    assert len(suffix) == 6


# Real spike render from the hybrid bake-off; present on the dev machine only.
E2E_VIDEO = ROOT / "tools" / "latentsync" / "spike-out" / "hybrid_ditto_latentsync_20s.mp4"


@pytest.mark.skipif(
    os.environ.get("RUN_E2E") != "1", reason="set RUN_E2E=1 to run slow E2E tests")
@pytest.mark.skipif(not E2E_VIDEO.is_file(), reason=f"spike asset missing: {E2E_VIDEO}")
def test_matte_video_end_to_end(tmp_path):
    rc = matte_video.main([
        "--video", str(E2E_VIDEO),
        "--out-dir", str(tmp_path),
        "--run-id", "e2etest",
    ])
    assert rc == 0
    out = tmp_path / "lipsync_green_e2etest.mp4"
    assert out.is_file() and out.stat().st_size > 100_000
