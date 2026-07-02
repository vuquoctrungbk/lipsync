"""Central paths and render configuration.

All filesystem locations resolve relative to the repo root so the app runs the
same regardless of the current working directory.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# repo root = .../lipsync/config.py -> parents[1]
ROOT = Path(__file__).resolve().parents[1]

MODELS_DIR = ROOT / "models"
SADTALKER_SRC = ROOT / "third_party" / "SadTalker"
SADTALKER_CKPT_DIR = MODELS_DIR / "sadtalker"
SADTALKER_CONFIG_DIR = SADTALKER_SRC / "src" / "config"
GFPGAN_WEIGHTS_DIR = SADTALKER_CKPT_DIR / "gfpgan" / "weights"
BIREFNET_DIR = MODELS_DIR / "birefnet"
OUTPUTS_DIR = ROOT / "outputs"
TEMP_DIR = ROOT / "temp"

# Standard chroma-key green (RGB). 0,177,64 is the common "digital green".
DEFAULT_GREEN_RGB = (0, 177, 64)

# Hard cap on input audio length (v1 OOM / runtime guard).
MAX_AUDIO_SECONDS = 120


@dataclass
class RenderConfig:
    """Knobs exposed to the UI; sensible defaults for an RTX 3060 12 GB."""

    # SadTalker animation
    face_size: int = 256              # 256 (fast, light) or 512 (sharper, heavier)
    preprocess: str = "full"          # crop | resize | full ; full keeps whole portrait
    still_mode: bool = True           # minimal head drift from a single still image
    pose_style: int = 0               # [0, 46)
    expression_scale: float = 1.0
    batch_size: int = 2
    use_enhancer: bool = False        # GFPGAN face enhance (slower, optional)
    precision: str = "fp32"           # fp32 (stable) | fp16 (faster autocast on Ampere)

    # VRAM strategy: keep engines co-resident (measured SadTalker peak ~2.8 GB +
    # BiRefNet fp16 fits well within 12 GB -> faster repeat renders). Set True to
    # unload SadTalker before matting on tighter cards.
    sequential_vram: bool = False

    # Matting backend. RVM (GPL-3) is the fast default for personal use;
    # commercial_safe=True forces the MIT-licensed BiRefNet path regardless.
    matting_engine: str = "rvm"       # rvm | birefnet
    commercial_safe: bool = False

    # When set, seeds torch/numpy/random at render start so SadTalker's
    # stochastic pose CVAE + blink sampling are reproducible (comparison
    # renders, chunked-vs-full equivalence tests).
    seed: int | None = None

    # Green-screen compositing / encoding
    green_rgb: tuple[int, int, int] = DEFAULT_GREEN_RGB
    fps: int = 25                     # SadTalker renders at 25 fps
    crf: int = 18                     # x264 quality (lower = better)

    output_dir: Path = field(default_factory=lambda: OUTPUTS_DIR)


def ensure_runtime_dirs() -> None:
    """Create the writable dirs the pipeline needs."""
    for d in (OUTPUTS_DIR, TEMP_DIR):
        d.mkdir(parents=True, exist_ok=True)
