"""tools/colab/colab_render_job.py — static checks + pure-logic units.

The script executes only on a Colab runtime (via `colab exec -f`), so like
the notebook it gets identifier pinning + syntax verification here, plus
unit tests for the bits that run anywhere (params parsing, image discovery).
"""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "colab" / "colab_render_job.py"

spec = importlib.util.spec_from_file_location("colab_render_job", SCRIPT)
colab_render_job = importlib.util.module_from_spec(spec)
spec.loader.exec_module(colab_render_job)


def test_spike_proven_identifiers_match_notebook():
    src = SCRIPT.read_text(encoding="utf-8")
    assert "c3e47ee" in src, "ditto commit pin missing"
    assert "a229c39" in src, "latentsync commit pin missing"
    assert "digital-avatar/ditto-talkinghead" in src
    assert "ByteDance/LatentSync-1.5" in src, "256 model comes from the 1.5 repo"
    assert "ditto_pytorch" in src, "T4 is Turing — PyTorch path, not TRT"
    assert "stage2.yaml" in src and "stage2_512.yaml" not in src, "must run the 256 config"
    assert "--python\", \"3.11" in src or '"3.11"' in src, "venvs pin the proven interpreter"
    assert "onnxruntime-gpu==1.22" in src, "1.27 links libcudart.so.13; Colab image is CUDA 12"
    assert "MPLBACKEND" in src, "kernel leaks inline mpl backend into subprocess envs"
    assert "max_width" in src, "full-res LatentSync video-write OOM-kills the free VM"


def test_load_params_accepts_missing_and_valid(tmp_path, monkeypatch):
    monkeypatch.setattr(colab_render_job, "JOB_DIR", tmp_path)
    assert colab_render_job.load_params() == {}  # no params.json -> defaults
    (tmp_path / "params.json").write_text('{"inference_steps": 32}')
    assert colab_render_job.load_params() == {"inference_steps": 32}


def test_load_params_rejects_non_object(tmp_path, monkeypatch):
    monkeypatch.setattr(colab_render_job, "JOB_DIR", tmp_path)
    (tmp_path / "params.json").write_text('["not", "a", "dict"]')
    with pytest.raises(ValueError):
        colab_render_job.load_params()


def test_find_image_prefers_png_and_handles_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(colab_render_job, "JOB_DIR", tmp_path)
    assert colab_render_job.find_image() is None
    (tmp_path / "b.jpg").write_bytes(b"x")
    (tmp_path / "a.png").write_bytes(b"x")
    assert colab_render_job.find_image().name == "a.png"  # png pattern wins
