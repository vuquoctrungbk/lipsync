"""Static checks for tools/colab/lipsync_render.ipynb.

The notebook can't be executed locally (Colab-only: google.colab, Drive,
T4). What CAN break silently in review is exactly what these tests pin:
JSON validity, Python syntax of every cell, the spike-proven identifiers
(pinned commits, HF repos, 256 config), and committed outputs bloating
diffs. Parsed as plain JSON on purpose — the app venv has no nbformat.
"""
import json
from pathlib import Path

NOTEBOOK = Path(__file__).resolve().parents[1] / "tools" / "colab" / "lipsync_render.ipynb"


def load_notebook() -> dict:
    return json.loads(NOTEBOOK.read_text(encoding="utf-8"))


def code_cells() -> list[dict]:
    return [c for c in load_notebook()["cells"] if c["cell_type"] == "code"]


def source_of(cell: dict) -> str:
    return "".join(cell["source"])


def test_notebook_is_valid_nbformat4():
    nb = load_notebook()
    assert nb["nbformat"] == 4
    assert nb["cells"], "notebook has no cells"
    # GPU hint so Colab suggests the right runtime on open
    assert nb["metadata"].get("accelerator") == "GPU"


def test_every_code_cell_compiles():
    for i, cell in enumerate(code_cells()):
        # ! / % lines are IPython-only; the notebook is written to avoid them,
        # but strip defensively so a future shell one-liner can't crash this.
        py = "\n".join(
            line for line in source_of(cell).splitlines()
            if not line.lstrip().startswith(("!", "%"))
        )
        compile(py, f"notebook-code-cell-{i}", "exec")


def test_spike_proven_identifiers_present():
    src = "\n".join(source_of(c) for c in code_cells())
    # pinned commits (must match the locally verified spike installs)
    assert "c3e47ee" in src, "ditto commit pin missing"
    assert "a229c39" in src, "latentsync commit pin missing"
    # checkpoint sources
    assert "digital-avatar/ditto-talkinghead" in src
    assert "ByteDance/LatentSync-1.5" in src, "256 model comes from the 1.5 repo"
    assert "ditto_pytorch" in src, "T4 is Turing — must use the PyTorch path, not TRT"
    # 256 config, NOT the 512 one that thrashes below 18 GB VRAM
    assert "stage2.yaml" in src
    assert "drive.mount" in src


def test_battle_tested_pins_survive_regeneration():
    # The notebook is regenerated from a builder script; these pins each fixed
    # a real T4 failure (2026-07-11 gate) and must not drift on regen.
    src = "\n".join(source_of(c) for c in code_cells())
    assert "onnxruntime-gpu==1.22" in src, "1.27 links libcudart.so.13; image is CUDA 12"
    assert '"3.11"' in src, "the proven interpreter is 3.11"
    assert "MPLBACKEND" in src, "kernel leaks the inline mpl backend into subprocesses"
    assert "max_width" in src, "full-res LatentSync video-write OOM-kills the free VM"


def test_no_outputs_or_execution_counts_committed():
    for i, cell in enumerate(code_cells()):
        assert not cell.get("outputs"), f"cell {i} has committed outputs"
        assert cell.get("execution_count") is None, f"cell {i} has execution_count"


def test_open_in_colab_badge_points_at_this_file():
    markdown = "\n".join(
        source_of(c) for c in load_notebook()["cells"] if c["cell_type"] == "markdown"
    )
    assert "colab.research.google.com/github/vuquoctrungbk/lipsync" in markdown
    assert "tools/colab/lipsync_render.ipynb" in markdown
