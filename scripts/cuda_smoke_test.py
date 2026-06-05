"""CUDA-over-RDP smoke test.

Confirms the GPU is visible and a real fp16 matmul runs on it. This is the gate
that proves CUDA works inside the current (RDP) session before we build further.
Exits non-zero on any failure so callers (setup_env.ps1) can stop early.
"""
import sys


def main() -> int:
    import torch

    if not torch.cuda.is_available():
        print("FAIL: torch.cuda.is_available() == False "
              "(CUDA not visible over RDP — check driver/session)", file=sys.stderr)
        return 1

    name = torch.cuda.get_device_name(0)
    props = torch.cuda.get_device_properties(0)
    print(f"GPU: {name}")
    print(f"VRAM: {props.total_memory / 1e9:.1f} GB | "
          f"compute capability: {props.major}.{props.minor}")
    print(f"torch: {torch.__version__} | numpy: {_numpy_version()}")

    # Real fp16 tensor-core matmul on device (Ampere CC 8.6 supports fp16).
    dev = torch.device("cuda:0")
    x = torch.randn(2048, 2048, device=dev, dtype=torch.float16)
    checksum = (x @ x).float().sum().item()
    torch.cuda.synchronize()
    print(f"fp16 matmul OK | checksum={checksum:.1f}")
    torch.cuda.empty_cache()
    return 0


def _numpy_version() -> str:
    try:
        import numpy
        return numpy.__version__
    except Exception:
        return "not-installed"


if __name__ == "__main__":
    raise SystemExit(main())
