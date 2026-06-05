"""End-to-end pipeline test (slow: loads models, renders a clip).

Opt-in via:  set RUN_E2E=1  before running pytest. Uses SadTalker's bundled
example assets and asserts the output exists with a green background.
"""
import os
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_E2E") != "1", reason="set RUN_E2E=1 to run the slow E2E test"
)


def test_pipeline_produces_green_video():
    import imageio

    from lipsync.config import RenderConfig
    from lipsync.pipeline import Pipeline

    img = ROOT / "third_party/SadTalker/examples/source_image/full_body_1.png"
    aud = ROOT / "third_party/SadTalker/examples/driven_audio/chinese_poem1.wav"
    assert img.exists() and aud.exists(), "SadTalker example assets missing"

    cfg = RenderConfig(face_size=256, preprocess="full", still_mode=True)
    res = Pipeline().run(img, aud, cfg)
    out = Path(res["output"])
    assert out.exists() and out.stat().st_size > 0

    reader = imageio.get_reader(str(out), "ffmpeg")
    frame = reader.get_data(0)
    reader.close()

    corners = np.concatenate([
        frame[:15, :15].reshape(-1, 3),
        frame[:15, -15:].reshape(-1, 3),
        frame[-15:, :15].reshape(-1, 3),
    ]).mean(axis=0)
    # background should be near digital green (0,177,64); allow codec drift
    assert corners[1] > 140, f"green channel too low: {corners}"
    assert corners[0] < 60 and corners[2] < 110, f"not green enough: {corners}"
