"""composite(alpha_source=...) — alpha computed from a cleaner aligned clip.

Born from the Colab-256 gate: the mouth-refine final is recompressed so hard
its noise makes the matte flicker; alpha from the pre-refine encode (identical
background motion) fixed it. These run the REAL green sink (imageio/ffmpeg)
on tiny 4-frame clips, so they stay in the default fast suite.
"""
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from conftest import FakeMatte  # noqa: E402
from lipsync.config import RenderConfig  # noqa: E402
from lipsync.green_compositor import CompositeError, composite  # noqa: E402

RED = np.full((32, 32, 3), (255, 0, 0), dtype=np.uint8)
BLUE = np.full((32, 32, 3), (0, 0, 255), dtype=np.uint8)


class RecordingMatte(FakeMatte):
    def __init__(self):
        super().__init__()
        self.inputs = []

    def alpha_for(self, rgb):
        self.inputs.append(rgb.copy())
        return super().alpha_for(rgb)


@pytest.fixture()
def tiny_wav(tmp_path):
    # 0.2 s of 8 kHz 16-bit mono silence — just enough for the AAC mux.
    n = 1600
    data = b"\x00\x00" * n
    hdr = (b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt "
           + struct.pack("<IHHIIHH", 16, 1, 1, 8000, 16000, 2, 16)
           + b"data" + struct.pack("<I", len(data)))
    p = tmp_path / "tiny.wav"
    p.write_bytes(hdr + data)
    return p


def run_composite(tmp_path, tiny_wav, alpha_source):
    matte = RecordingMatte()
    outputs, warnings = composite(
        iter([RED.copy() for _ in range(4)]), 25.0, matte,
        RenderConfig(output_format="green_mp4"), tiny_wav,
        tmp_path / "out", "alphasrc", tmp_path / "work", total=4,
        alpha_source=alpha_source)
    return matte, outputs


def test_alpha_comes_from_alpha_source(tmp_path, tiny_wav):
    matte, outputs = run_composite(
        tmp_path, tiny_wav, iter([BLUE.copy() for _ in range(4)]))
    assert (tmp_path / "out" / "lipsync_green_alphasrc.mp4").is_file()
    assert len(matte.inputs) == 4
    for seen in matte.inputs:  # matte saw the CLEAN clip, not the rendered one
        assert (seen == BLUE).all()


def test_without_alpha_source_matte_sees_rendered_frames(tmp_path, tiny_wav):
    matte, _ = run_composite(tmp_path, tiny_wav, None)
    assert all((seen == RED).all() for seen in matte.inputs)


def test_alpha_source_running_out_fails_loud(tmp_path, tiny_wav):
    with pytest.raises(CompositeError, match="ran out"):
        run_composite(tmp_path, tiny_wav, iter([BLUE.copy() for _ in range(2)]))


def test_alpha_source_size_mismatch_fails_loud(tmp_path, tiny_wav):
    big = np.zeros((64, 64, 3), dtype=np.uint8)
    with pytest.raises(CompositeError, match="scale one to match"):
        run_composite(tmp_path, tiny_wav, iter([big.copy() for _ in range(4)]))


def test_alpha_source_generator_is_closed(tmp_path, tiny_wav):
    closed = []

    def gen():
        try:
            while True:
                yield BLUE.copy()
        finally:
            closed.append(True)

    run_composite(tmp_path, tiny_wav, gen())
    assert closed, "composite must close a longer-than-clip alpha_source"
