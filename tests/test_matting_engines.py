"""Matting engine contract + pipeline cache/hardening tests.

Three tiers, mirroring the repo's opt-in convention:
  - default:              fast, no model downloads, no GPU needed
  - RUN_MODEL_TESTS=1:    loads real matting models (RVM ~15MB via torch.hub,
                          BiRefNet from the local HF cache) on tiny frames
  - RUN_E2E=1:            full renders (see also tests/test_pipeline_e2e.py)
"""
import os
import sys
import time
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lipsync.config import RenderConfig  # noqa: E402

MODEL_TESTS = pytest.mark.skipif(
    os.environ.get("RUN_MODEL_TESTS") != "1",
    reason="set RUN_MODEL_TESTS=1 to run tests that load real matting models",
)
E2E = pytest.mark.skipif(
    os.environ.get("RUN_E2E") != "1", reason="set RUN_E2E=1 to run slow E2E tests"
)


# ---------------------------------------------------------------------------
# fakes (behavior-bearing, not duck-type shells: they record calls and return
# real alpha arrays so downstream math actually runs)
# ---------------------------------------------------------------------------

class FakeMatte:
    def __init__(self, device="cpu", precision="fp32"):
        self.device = device
        self.precision = precision
        self.loaded = False
        self.unload_calls = 0
        self.reset_calls = 0

    def load(self):
        self.loaded = True

    def unload(self):
        self.unload_calls += 1
        self.loaded = False

    def reset_clip(self):
        self.reset_calls += 1

    def alpha_for(self, rgb):
        return np.ones(rgb.shape[:2], dtype=np.float32)


class FakeRVM(FakeMatte):
    pass


class FakeBiRefNet(FakeMatte):
    pass


@pytest.fixture()
def stubbed_pipeline(monkeypatch, tmp_path):
    """Pipeline with heavy stages stubbed so run() exercises its real control
    flow (sanitize, run ids, keyed matting cache, warnings) without models."""
    import lipsync.pipeline as pl

    class FakeSad:
        def __init__(self, cfg, device):
            self.cfg = cfg

        def load(self):
            pass

        def unload(self):
            pass

        def animate(self, image, wav, work_dir):
            return Path(work_dir) / "fake_animated.mp4"

    def fake_prepare_audio(path, work, max_seconds=None):
        out = Path(work) / "audio_16k_mono.wav"
        out.write_bytes(b"RIFF")
        return out

    def fake_composite(animated, matte, cfg, audio, out_path, work_dir, progress=None):
        # exercise the reset contract the way the real driver does
        matte.reset_clip()
        matte.alpha_for(np.zeros((4, 4, 3), dtype=np.uint8))
        matte.reset_clip()
        return Path(out_path), []

    monkeypatch.setattr(pl, "SadTalkerEngine", FakeSad)
    monkeypatch.setattr(pl, "prepare_audio", fake_prepare_audio)
    monkeypatch.setattr(pl, "composite_to_green", fake_composite)
    monkeypatch.setattr(pl, "_MATTING_ENGINES", {"rvm": FakeRVM, "birefnet": FakeBiRefNet})

    img = tmp_path / "portrait.png"
    img.write_bytes(_tiny_png())
    aud = tmp_path / "voice.wav"
    aud.write_bytes(b"RIFF")
    return pl.Pipeline(), img, aud, tmp_path


def _tiny_png() -> bytes:
    import cv2

    ok, buf = cv2.imencode(".png", np.full((8, 8, 3), 128, dtype=np.uint8))
    assert ok
    return buf.tobytes()


def _cfg(tmp_path, **kw) -> RenderConfig:
    return RenderConfig(output_dir=tmp_path / "out", **kw)


# ---------------------------------------------------------------------------
# fast unit tests
# ---------------------------------------------------------------------------

def test_config_defaults_v2():
    cfg = RenderConfig()
    assert cfg.matting_engine == "rvm"
    assert cfg.commercial_safe is False
    assert cfg.seed is None


def test_commercial_safe_forces_birefnet(stubbed_pipeline, tmp_path):
    pipe, img, aud, tp = stubbed_pipeline
    pipe.run(img, aud, _cfg(tp, matting_engine="rvm", commercial_safe=True))
    assert isinstance(pipe._matte, FakeBiRefNet)


def test_live_engine_flip_unloads_old(stubbed_pipeline, tmp_path):
    """Flipping commercial_safe mid-session must swap engines on ONE Pipeline
    and unload the old engine — a stale cache would keep GPL RVM live."""
    pipe, img, aud, tp = stubbed_pipeline

    pipe.run(img, aud, _cfg(tp, matting_engine="rvm"))
    first = pipe._matte
    assert isinstance(first, FakeRVM) and first.loaded

    pipe.run(img, aud, _cfg(tp, matting_engine="rvm", commercial_safe=True))
    second = pipe._matte
    assert isinstance(second, FakeBiRefNet)
    assert first.unload_calls == 1 and not first.loaded
    assert second is not first

    # flip back -> RVM again, BiRefNet unloaded
    pipe.run(img, aud, _cfg(tp, matting_engine="rvm"))
    assert isinstance(pipe._matte, FakeRVM)
    assert second.unload_calls == 1


def test_unknown_matting_engine_raises(stubbed_pipeline, tmp_path):
    from lipsync.pipeline import PipelineError

    pipe, img, aud, tp = stubbed_pipeline
    with pytest.raises(PipelineError, match="unknown matting_engine"):
        pipe.run(img, aud, _cfg(tp, matting_engine="nope"))


def test_failed_load_does_not_poison_cache(stubbed_pipeline, tmp_path, monkeypatch):
    """If an engine swap dies in load() (e.g. RVM first-run offline), a retry
    under the PREVIOUS key must rebuild that engine — not cache-hit the broken
    instance left behind by the failed swap (that path would let a
    commercial_safe render lazy-load GPL RVM inside alpha_for)."""
    import lipsync.pipeline as pl

    class ExplodingRVM(FakeMatte):
        def load(self):
            raise RuntimeError("offline: torch.hub unreachable")

    pipe, img, aud, tp = stubbed_pipeline
    pipe.run(img, aud, _cfg(tp, matting_engine="birefnet"))  # cache: birefnet
    first = pipe._matte

    monkeypatch.setitem(pl._MATTING_ENGINES, "rvm", ExplodingRVM)
    with pytest.raises(RuntimeError, match="offline"):
        pipe.run(img, aud, _cfg(tp, matting_engine="rvm"))

    res = pipe.run(img, aud, _cfg(tp, matting_engine="birefnet"))
    assert isinstance(pipe._matte, FakeBiRefNet)
    assert pipe._matte is not first          # old one was unloaded in the swap
    assert pipe._matte.loaded
    assert "warnings" in res


def test_same_second_runs_get_distinct_ids(stubbed_pipeline, tmp_path):
    pipe, img, aud, tp = stubbed_pipeline
    cfg = _cfg(tp)
    r1 = pipe.run(img, aud, cfg)
    r2 = pipe.run(img, aud, cfg)
    assert r1["output"] != r2["output"]
    assert "warnings" in r1  # additive contract field always present


def test_sanitize_portrait_copies_known_ext(tmp_path):
    from lipsync.pipeline import Pipeline

    pipe = Pipeline.__new__(Pipeline)  # no engine init needed
    src = tmp_path / "we%ird %04d name.PNG"
    src.write_bytes(_tiny_png())
    safe = pipe._sanitize_portrait(src, tmp_path)
    assert safe.name == "portrait.png"
    assert safe.read_bytes() == src.read_bytes()


def test_sanitize_portrait_reencodes_exotic_ext(tmp_path):
    import cv2

    from lipsync.pipeline import Pipeline

    pipe = Pipeline.__new__(Pipeline)
    ok, buf = cv2.imencode(".webp", np.full((8, 8, 3), 90, dtype=np.uint8))
    assert ok
    src = tmp_path / "photo.webp"
    src.write_bytes(buf.tobytes())
    safe = pipe._sanitize_portrait(src, tmp_path)
    assert safe.name == "portrait.png"
    assert cv2.imread(str(safe)) is not None


def test_sanitize_portrait_rejects_garbage(tmp_path):
    from lipsync.pipeline import Pipeline, PipelineError

    pipe = Pipeline.__new__(Pipeline)
    src = tmp_path / "not_an_image.xyz"
    src.write_bytes(b"garbage")
    with pytest.raises(PipelineError, match="unsupported image format"):
        pipe._sanitize_portrait(src, tmp_path)


def test_mux_failure_surfaces_warning_and_resets_clip(tmp_path):
    """Audio-mux failure must degrade to silent video WITH a warning, and the
    compositor must own exactly two reset_clip calls (start + finally)."""
    import imageio

    from lipsync.green_compositor import composite_to_green

    vid = tmp_path / "animated.mp4"
    w = imageio.get_writer(str(vid), fps=5, codec="libx264", macro_block_size=None)
    for _ in range(4):
        w.append_data(np.full((32, 32, 3), 200, dtype=np.uint8))
    w.close()

    matte = FakeMatte()
    out, warns = composite_to_green(
        vid, matte, RenderConfig(), tmp_path / "missing_audio.wav",
        tmp_path / "out.mp4", work_dir=tmp_path / "work",
    )
    assert out.exists() and out.stat().st_size > 0
    assert len(warns) == 1 and "silent" in warns[0]
    assert matte.reset_calls == 2


def test_free_vram_bytes_and_has_ffprobe_shapes():
    from lipsync.ffmpeg_utils import has_ffprobe
    from lipsync.hardware import free_vram_bytes

    assert isinstance(free_vram_bytes(), int)
    assert free_vram_bytes() >= 0
    assert isinstance(has_ffprobe(), bool)


# ---------------------------------------------------------------------------
# real-model contract tests (RUN_MODEL_TESTS=1)
# ---------------------------------------------------------------------------

def _rand_frame(h, w):
    rng = np.random.default_rng(0)
    return rng.integers(0, 255, size=(h, w, 3), dtype=np.uint8)


@MODEL_TESTS
def test_rvm_alpha_contract_and_reset_determinism():
    from lipsync.hardware import detect_device
    from lipsync.matting_rvm import RVMMatte

    device = detect_device().device
    eng = RVMMatte(device, precision="fp16" if device == "cuda" else "fp32")
    eng.load()
    try:
        f = _rand_frame(48, 64)
        a1 = eng.alpha_for(f)
        assert a1.shape == (48, 64) and a1.dtype == np.float32
        assert 0.0 <= float(a1.min()) and float(a1.max()) <= 1.0

        eng.alpha_for(f)  # advance recurrent state
        eng.reset_clip()
        a3 = eng.alpha_for(f)
        # post-reset output must equal a fresh clip's first-frame output
        assert np.allclose(a1, a3, atol=1e-6), "recurrent state survived reset_clip()"

        # resolution change after reset must not crash (stale-state hazard)
        eng.reset_clip()
        b = eng.alpha_for(_rand_frame(96, 80))
        assert b.shape == (96, 80)
    finally:
        eng.unload()


@MODEL_TESTS
def test_birefnet_alpha_contract():
    from lipsync.hardware import detect_device
    from lipsync.matting_birefnet import BiRefNetMatte

    device = detect_device().device
    eng = BiRefNetMatte(device, precision="fp16" if device == "cuda" else "fp32")
    eng.load()
    try:
        f = _rand_frame(48, 64)
        a = eng.alpha_for(f)
        assert a.shape == (48, 64) and a.dtype == np.float32
        assert 0.0 <= float(a.min()) and float(a.max()) <= 1.0
        eng.reset_clip()  # must exist and be a no-op
    finally:
        eng.unload()


@MODEL_TESTS
def test_rvm_vram_flat_over_many_frames():
    import torch

    from lipsync.matting_rvm import RVMMatte

    if not torch.cuda.is_available():
        pytest.skip("VRAM growth check needs CUDA")
    from lipsync.hardware import free_vram_bytes

    eng = RVMMatte("cuda", precision="fp16")
    eng.load()
    try:
        f = _rand_frame(480, 640)
        eng.reset_clip()
        for _ in range(30):  # warm-up: allocator reaches steady state
            eng.alpha_for(f)
        baseline = free_vram_bytes()
        for _ in range(300):
            eng.alpha_for(f)
        drift = baseline - free_vram_bytes()
        assert drift < 64 * 1024 * 1024, f"VRAM grew {drift/1e6:.0f}MB over 300 frames"
    finally:
        eng.unload()


# ---------------------------------------------------------------------------
# E2E (RUN_E2E=1) — the default-engine E2E lives in tests/test_pipeline_e2e.py
# ---------------------------------------------------------------------------

def _assert_green_corners(path):
    import imageio

    reader = imageio.get_reader(str(path), "ffmpeg")
    frame = reader.get_data(0)
    reader.close()
    corners = np.concatenate([
        frame[:15, :15].reshape(-1, 3),
        frame[:15, -15:].reshape(-1, 3),
        frame[-15:, :15].reshape(-1, 3),
    ]).mean(axis=0)
    assert corners[1] > 140, f"green channel too low: {corners}"
    assert corners[0] < 60 and corners[2] < 110, f"not green enough: {corners}"


@E2E
@pytest.mark.parametrize("engine", ["rvm", "birefnet"])
def test_e2e_green_output_per_engine(engine):
    from lipsync.pipeline import Pipeline

    img = ROOT / "third_party/SadTalker/examples/source_image/full_body_1.png"
    aud = ROOT / "third_party/SadTalker/examples/driven_audio/chinese_poem1.wav"
    assert img.exists() and aud.exists(), "SadTalker example assets missing"

    cfg = RenderConfig(face_size=256, preprocess="full", still_mode=True,
                       matting_engine=engine)
    t0 = time.time()
    res = Pipeline().run(img, aud, cfg)
    wall = time.time() - t0

    out = Path(res["output"])
    assert out.exists() and out.stat().st_size > 0
    _assert_green_corners(out)
    print(f"\n[e2e:{engine}] wall={wall:.0f}s timings={res['timings']} {res['vram']}")
