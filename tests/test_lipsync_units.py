"""Fast unit tests (no model loading)."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lipsync.audio_preprocess import AudioError, prepare_audio  # noqa: E402
from lipsync.config import DEFAULT_GREEN_RGB, RenderConfig  # noqa: E402
from lipsync.hardware import DeviceInfo, detect_device, resolve_precision  # noqa: E402


def test_render_config_defaults():
    cfg = RenderConfig()
    assert cfg.face_size in (256, 512)
    assert cfg.preprocess == "full"
    assert cfg.precision == "fp32"          # measured: fp32 fits 12 GB, stable
    assert cfg.green_rgb == DEFAULT_GREEN_RGB


def test_detect_device_shape():
    info = detect_device()
    assert isinstance(info, DeviceInfo)
    assert info.device in ("cuda", "cpu")


def test_resolve_precision_cpu_forces_fp32():
    cpu = DeviceInfo("cpu", "cpu", 0.0, None, False)
    assert resolve_precision("fp16", cpu) == "fp32"


def test_resolve_precision_gpu_allows_fp16():
    gpu = DeviceInfo("cuda", "x", 12.0, (8, 6), True)
    assert resolve_precision("fp16", gpu) == "fp16"
    assert resolve_precision("fp32", gpu) == "fp32"


def test_prepare_audio_missing_file(tmp_path):
    with pytest.raises(AudioError):
        prepare_audio(tmp_path / "nope.wav", tmp_path)


def _write_wav(path, seconds, rate=16000):
    import wave

    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * int(seconds * rate))


def test_wav_duration_seconds_exact(tmp_path):
    from lipsync.audio_preprocess import wav_duration_seconds

    _write_wav(tmp_path / "a.wav", 1.5)
    assert wav_duration_seconds(tmp_path / "a.wav") == pytest.approx(1.5)


def test_audio_cap_fail_closed_without_ffprobe(tmp_path, monkeypatch):
    """The cap must reject over-limit audio even when ffprobe is absent —
    enforcement reads the DECODED wav, the probe is only a courtesy."""
    import lipsync.audio_preprocess as ap

    monkeypatch.setattr(ap, "probe_duration", lambda p: None)  # no ffprobe
    _write_wav(tmp_path / "long.wav", 3.0)
    with pytest.raises(AudioError, match="limit is 2s"):
        prepare_audio(tmp_path / "long.wav", tmp_path / "work", max_seconds=2)


def test_audio_cap_allows_under_limit_without_ffprobe(tmp_path, monkeypatch):
    import lipsync.audio_preprocess as ap

    monkeypatch.setattr(ap, "probe_duration", lambda p: None)
    _write_wav(tmp_path / "short.wav", 1.0)
    out = prepare_audio(tmp_path / "short.wav", tmp_path / "work", max_seconds=2)
    assert out.exists()


def test_hex_to_rgb():
    from app import _hex_to_rgb
    assert _hex_to_rgb("#00B140") == (0, 177, 64)
    assert _hex_to_rgb("bad") == DEFAULT_GREEN_RGB
