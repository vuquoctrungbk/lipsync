"""WebM VP9 alpha export: sink/driver unit tests + opt-in full-render E2E.

Unit tier needs a working ffmpeg with libvpx-vp9 (system ffmpeg 8.0 has it);
tests are skipped cleanly where the encoder is absent. RUN_E2E=1 runs the full
pipeline render with ffprobe + RGBA-decode assertions.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from conftest import FakeMatte  # noqa: E402

from lipsync.config import RenderConfig  # noqa: E402
from lipsync.ffmpeg_utils import ffmpeg_exe, ffprobe_exe, has_encoder  # noqa: E402

E2E = pytest.mark.skipif(
    os.environ.get("RUN_E2E") != "1", reason="set RUN_E2E=1 to run slow E2E tests"
)
NEEDS_VP9 = pytest.mark.skipif(
    not has_encoder("libvpx-vp9"), reason="resolved ffmpeg lacks libvpx-vp9"
)


class CenterMatte(FakeMatte):
    """Opaque center, transparent 8px border — lets alpha assertions bite."""

    def __init__(self):
        super().__init__()
        self.calls = 0

    def alpha_for(self, rgb):
        self.calls += 1
        a = np.zeros(rgb.shape[:2], dtype=np.float32)
        a[8:-8, 8:-8] = 1.0
        return a


def _write_tiny_mp4(path: Path, n=6, w=64, h=48) -> None:
    import imageio

    wri = imageio.get_writer(str(path), fps=5, codec="libx264", macro_block_size=None)
    rng = np.random.default_rng(1)
    for _ in range(n):
        wri.append_data(rng.integers(0, 255, (h, w, 3), dtype=np.uint8))
    wri.close()


def _write_tone_wav(path: Path, seconds=1.2) -> None:
    subprocess.run(
        [ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", f"sine=frequency=440:duration={seconds}", "-ar", "16000", str(path)],
        check=True, capture_output=True,
    )


# ---------------------------------------------------------------------------
# unit
# ---------------------------------------------------------------------------

def test_has_encoder_parses_encoder_table(monkeypatch):
    from lipsync import ffmpeg_utils

    sample = (
        " A....D aac                  AAC (Advanced Audio Coding)\n"
        " V....D libvpx-vp9           libvpx VP9 (codec vp9)\n"
    )

    class P:
        returncode = 0
        stdout = sample
        stderr = ""

    monkeypatch.setattr(ffmpeg_utils.subprocess, "run", lambda *a, **k: P())
    ffmpeg_utils.has_encoder.cache_clear()
    try:
        assert ffmpeg_utils.has_encoder("libvpx-vp9") is True
        assert ffmpeg_utils.has_encoder("libx265") is False
    finally:
        ffmpeg_utils.has_encoder.cache_clear()  # drop fabricated entries


def test_webm_without_encoder_is_friendly_error(stubbed_pipeline, tmp_path, monkeypatch):
    import lipsync.pipeline as pl
    from lipsync.pipeline import PipelineError

    pipe, img, aud, tp = stubbed_pipeline
    monkeypatch.setattr(pl, "has_encoder", lambda name: False)
    cfg = RenderConfig(output_dir=tp / "out", output_format="webm_alpha")
    with pytest.raises(PipelineError, match="libvpx-vp9"):
        pipe.run(img, aud, cfg)


def test_unknown_output_format_rejected_early(stubbed_pipeline, tmp_path):
    from lipsync.pipeline import PipelineError

    pipe, img, aud, tp = stubbed_pipeline
    cfg = RenderConfig(output_dir=tp / "out", output_format="prores")
    with pytest.raises(PipelineError, match="unknown output_format"):
        pipe.run(img, aud, cfg)


@NEEDS_VP9
def test_both_mode_mattes_once_and_produces_both(tmp_path):
    from lipsync.green_compositor import composite, iter_video_frames

    vid = tmp_path / "animated.mp4"
    _write_tiny_mp4(vid)
    wav = tmp_path / "tone.wav"
    _write_tone_wav(wav)

    matte = CenterMatte()
    frames, fps, total = iter_video_frames(vid)
    outputs, warns = composite(
        frames, fps, matte, RenderConfig(output_format="both"), wav,
        tmp_path / "out", run_id="r1", work_dir=tmp_path / "work", total=total,
    )

    assert set(outputs) == {"green_mp4", "webm_alpha"}
    assert warns == []
    assert matte.calls == 6, "matte must run ONCE per frame in both mode"
    assert matte.reset_calls == 2
    for p in outputs.values():
        assert p.exists() and p.stat().st_size > 0
    assert not list((tmp_path / "out").glob("*.tmp.*")), "temp names must be renamed away"

    pe = ffprobe_exe()
    if pe:
        info = json.loads(subprocess.run(
            [pe, "-v", "error", "-show_streams", "-of", "json",
             str(outputs["webm_alpha"])],
            capture_output=True, text=True, check=True).stdout)
        streams = {s["codec_type"]: s for s in info["streams"]}
        assert streams["video"]["codec_name"] == "vp9"
        # VP9 alpha lives in container side-data: the base stream always
        # probes as yuv420p; alpha_mode=1 is the real transparency marker.
        assert streams["video"].get("tags", {}).get("alpha_mode") == "1"
        assert streams["audio"]["codec_name"] == "opus"


@NEEDS_VP9
def test_webm_sink_death_isolated_from_green(tmp_path, monkeypatch):
    """A webm sink dying mid-stream must NOT hurt the green output."""
    from lipsync import green_compositor as gc

    vid = tmp_path / "animated.mp4"
    _write_tiny_mp4(vid)
    wav = tmp_path / "tone.wav"
    _write_tone_wav(wav)

    orig_write = gc._WebmSink.write
    state = {"n": 0}

    def dying_write(self, rgb, alpha):
        state["n"] += 1
        if state["n"] >= 3:
            raise gc.SinkError(self.fmt, self._shutdown("injected mid-stream death"))
        orig_write(self, rgb, alpha)

    monkeypatch.setattr(gc._WebmSink, "write", dying_write)

    matte = CenterMatte()
    frames, fps, total = gc.iter_video_frames(vid)
    outputs, warns = gc.composite(
        frames, fps, matte, RenderConfig(output_format="both"), wav,
        tmp_path / "out", run_id="r2", work_dir=tmp_path / "work", total=total,
    )

    assert set(outputs) == {"green_mp4"}
    assert outputs["green_mp4"].exists() and outputs["green_mp4"].stat().st_size > 0
    assert any("injected mid-stream death" in w for w in warns)
    assert matte.calls == 6, "green sink must keep consuming all frames"
    assert not list((tmp_path / "out").glob("*.tmp.webm")), "dead sink tmp not cleaned"


@NEEDS_VP9
def test_corrupt_audio_degrades_both_sinks_gracefully(tmp_path):
    """Garbage audio: webm sink dies (needs decodable audio), green falls back
    to silent video — both failures surface as warnings, green survives."""
    from lipsync.green_compositor import composite, iter_video_frames

    vid = tmp_path / "animated.mp4"
    _write_tiny_mp4(vid)
    bad_audio = tmp_path / "garbage.wav"
    bad_audio.write_bytes(b"this is definitely not audio")

    matte = CenterMatte()
    frames, fps, total = iter_video_frames(vid)
    outputs, warns = composite(
        frames, fps, matte, RenderConfig(output_format="both"), bad_audio,
        tmp_path / "out", run_id="r3", work_dir=tmp_path / "work", total=total,
    )

    assert set(outputs) == {"green_mp4"}
    assert outputs["green_mp4"].exists()
    assert any("webm_alpha" in w for w in warns), f"webm failure missing: {warns}"
    assert any("silent" in w for w in warns), f"green fallback missing: {warns}"


# ---------------------------------------------------------------------------
# E2E (RUN_E2E=1): full pipeline render in `both` mode + alpha assertions
# ---------------------------------------------------------------------------

def _probe_streams(path: Path) -> dict:
    pe = ffprobe_exe()
    assert pe, "E2E alpha assertions need ffprobe on PATH"
    info = json.loads(subprocess.run(
        [pe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, check=True).stdout)
    return info


def _decode_rgba_frame(path: Path, w: int, h: int) -> np.ndarray:
    # ffmpeg's NATIVE vp9 decoder ignores the alpha side-data (returns 255
    # everywhere) — the libvpx decoder must be forced to export real alpha.
    raw = subprocess.run(
        [ffmpeg_exe(), "-v", "error", "-c:v", "libvpx-vp9", "-i", str(path),
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
        capture_output=True, check=True).stdout
    return np.frombuffer(raw[: w * h * 4], dtype=np.uint8).reshape(h, w, 4)


@E2E
@NEEDS_VP9
def test_e2e_both_render_alpha_and_green():
    from lipsync.pipeline import Pipeline

    img = ROOT / "third_party/SadTalker/examples/source_image/full_body_1.png"
    aud = ROOT / "third_party/SadTalker/examples/driven_audio/chinese_poem1.wav"
    assert img.exists() and aud.exists(), "SadTalker example assets missing"

    cfg = RenderConfig(face_size=256, preprocess="full", still_mode=True,
                       output_format="both")
    res = Pipeline().run(img, aud, cfg)

    assert set(res["outputs"]) == {"green_mp4", "webm_alpha"}
    webm = Path(res["outputs"]["webm_alpha"])
    green = Path(res["outputs"]["green_mp4"])
    assert res["output"] == green  # green stays the primary artifact

    # green corner assertions (same contract as v1)
    import imageio

    reader = imageio.get_reader(str(green), "ffmpeg")
    gframe = reader.get_data(0)
    reader.close()
    corners = np.concatenate([
        gframe[:15, :15].reshape(-1, 3),
        gframe[:15, -15:].reshape(-1, 3),
        gframe[-15:, :15].reshape(-1, 3),
    ]).mean(axis=0)
    assert corners[1] > 140 and corners[0] < 60 and corners[2] < 110

    # webm: stream properties (alpha_mode=1 is VP9's transparency marker —
    # the base bitstream always probes yuv420p, alpha is container side-data)
    info = _probe_streams(webm)
    streams = {s["codec_type"]: s for s in info["streams"]}
    assert streams["video"]["codec_name"] == "vp9"
    assert streams["video"].get("tags", {}).get("alpha_mode") == "1"
    assert "audio" in streams, "webm must carry an audio stream"
    w, h = int(streams["video"]["width"]), int(streams["video"]["height"])

    # duration within 0.3s of the source audio
    src_info = _probe_streams(aud)
    dur = float(info["format"]["duration"])
    src_dur = float(src_info["format"]["duration"])
    assert abs(dur - src_dur) <= 0.3, f"webm {dur}s vs audio {src_dur}s"

    # alpha: corners transparent, center opaque
    rgba = _decode_rgba_frame(webm, w, h)
    for patch in (rgba[:15, :15, 3], rgba[:15, -15:, 3], rgba[-15:, :15, 3]):
        assert patch.mean() < 10, f"corner alpha not transparent: {patch.mean()}"
    cy, cx = h // 2, w // 2
    center = rgba[cy - 8:cy + 8, cx - 8:cx + 8, 3]
    assert center.mean() > 200, f"center alpha not opaque: {center.mean()}"
