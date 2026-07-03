"""Shared fakes + fixtures for the lipsync test suite.

The fakes are behavior-bearing (they record calls and return real alpha
arrays) — duck-type-only shells would pass for an engine returning garbage.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


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


def tiny_png_bytes() -> bytes:
    import cv2

    ok, buf = cv2.imencode(".png", np.full((8, 8, 3), 128, dtype=np.uint8))
    assert ok
    return buf.tobytes()


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

    # stubbed runs take the single-shot path; chunked-path tests repatch this
    def fake_wav_duration(path):
        return 5.0

    def fake_iter_frames(video):
        return iter([]), 25.0, 0

    def fake_composite(frames, fps, matte, cfg, audio, out_dir, run_id, work_dir,
                       total=None, progress=None):
        # exercise the reset contract the way the real driver does
        matte.reset_clip()
        matte.alpha_for(np.zeros((4, 4, 3), dtype=np.uint8))
        matte.reset_clip()
        return {"green_mp4": Path(out_dir) / f"lipsync_green_{run_id}.mp4"}, []

    monkeypatch.setattr(pl, "SadTalkerEngine", FakeSad)
    monkeypatch.setattr(pl, "prepare_audio", fake_prepare_audio)
    monkeypatch.setattr(pl, "wav_duration_seconds", fake_wav_duration)
    # keep unit-test work dirs out of the real temp/ tree
    monkeypatch.setattr(pl.config, "TEMP_DIR", tmp_path / "temp")
    monkeypatch.setattr(pl, "iter_video_frames", fake_iter_frames)
    monkeypatch.setattr(pl, "composite", fake_composite)
    monkeypatch.setattr(pl, "_MATTING_ENGINES", {"rvm": FakeRVM, "birefnet": FakeBiRefNet})

    img = tmp_path / "portrait.png"
    img.write_bytes(tiny_png_bytes())
    aud = tmp_path / "voice.wav"
    aud.write_bytes(b"RIFF")
    return pl.Pipeline(), img, aud, tmp_path
