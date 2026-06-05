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


def test_hex_to_rgb():
    from app import _hex_to_rgb
    assert _hex_to_rgb("#00B140") == (0, 177, 64)
    assert _hex_to_rgb("bad") == DEFAULT_GREEN_RGB
