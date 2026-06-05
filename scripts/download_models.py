"""Download + verify model checkpoints into ./models (gitignored).

Sources are official GitHub releases only (commercial-safe weights):
  - SadTalker core checkpoints (Apache-2.0 / MIT weights)
  - GFPGAN + facexlib auxiliary weights (for the optional enhancer toggle)

BiRefNet matting weights are fetched in the matting phase via huggingface_hub
(repo id pinned there), not here.

Verification: each file's on-disk size must equal the server Content-Length.
Completed files are skipped on re-run (idempotent). Exits non-zero on any failure.
"""
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
SAD = ROOT / "models" / "sadtalker"
GFP = SAD / "gfpgan" / "weights"

# (url, destination) — destinations are created as needed.
SADTALKER_RC = "https://github.com/OpenTalker/SadTalker/releases/download/v0.0.2-rc"
FACEXLIB_010 = "https://github.com/xinntao/facexlib/releases/download/v0.1.0"
FACEXLIB_022 = "https://github.com/xinntao/facexlib/releases/download/v0.2.2"
GFPGAN_130 = "https://github.com/TencentARC/GFPGAN/releases/download/v1.3.0"

MODELS = [
    # SadTalker core (required for animation)
    (f"{SADTALKER_RC}/mapping_00109-model.pth.tar", SAD / "mapping_00109-model.pth.tar"),
    (f"{SADTALKER_RC}/mapping_00229-model.pth.tar", SAD / "mapping_00229-model.pth.tar"),
    (f"{SADTALKER_RC}/SadTalker_V0.0.2_256.safetensors", SAD / "SadTalker_V0.0.2_256.safetensors"),
    (f"{SADTALKER_RC}/SadTalker_V0.0.2_512.safetensors", SAD / "SadTalker_V0.0.2_512.safetensors"),
    # Enhancer auxiliary weights (optional GFPGAN toggle)
    (f"{FACEXLIB_010}/alignment_WFLW_4HG.pth", GFP / "alignment_WFLW_4HG.pth"),
    (f"{FACEXLIB_010}/detection_Resnet50_Final.pth", GFP / "detection_Resnet50_Final.pth"),
    (f"{FACEXLIB_022}/parsing_parsenet.pth", GFP / "parsing_parsenet.pth"),
    (f"{GFPGAN_130}/GFPGANv1.4.pth", GFP / "GFPGANv1.4.pth"),
]

CHUNK = 1 << 20  # 1 MiB


def _expected_size(url: str) -> int | None:
    """Resolve final Content-Length following redirects (GitHub -> CDN)."""
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        cl = r.headers.get("Content-Length")
        return int(cl) if cl is not None else None


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    expected = _expected_size(url)

    if dest.exists() and expected is not None and dest.stat().st_size == expected:
        print(f"  skip (complete): {dest.name} ({expected/1e6:.1f} MB)")
        return

    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"  download: {dest.name} "
          f"({expected/1e6:.1f} MB)" if expected else f"  download: {dest.name}")
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        written = 0
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(CHUNK):
                f.write(chunk)
                written += len(chunk)

    if expected is not None and written != expected:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"size mismatch for {dest.name}: got {written}, expected {expected}")
    tmp.replace(dest)


def main() -> int:
    print(f"Target: {SAD}")
    failures = []
    for url, dest in MODELS:
        try:
            download(url, dest)
        except Exception as exc:  # noqa: BLE001 - report and continue
            print(f"  FAIL {dest.name}: {exc}", file=sys.stderr)
            failures.append(dest.name)

    print("\n--- manifest ---")
    for _, dest in MODELS:
        if dest.exists():
            print(f"  ok   {dest.relative_to(ROOT)}  ({dest.stat().st_size/1e6:.1f} MB)")
        else:
            print(f"  MISS {dest.relative_to(ROOT)}")

    if failures:
        print(f"\n{len(failures)} download(s) failed: {', '.join(failures)}", file=sys.stderr)
        return 1
    print("\nAll checkpoints present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
