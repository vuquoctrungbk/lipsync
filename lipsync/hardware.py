"""GPU detection, precision selection, and VRAM helpers.

Keeps the rest of the app from touching torch.cuda directly so behaviour is
consistent and a CPU fallback is automatic.
"""
from __future__ import annotations

from dataclasses import dataclass

import torch


@dataclass
class DeviceInfo:
    device: str                       # "cuda" or "cpu"
    name: str
    total_vram_gb: float
    compute_capability: tuple[int, int] | None
    fp16_ok: bool                     # tensor-core fp16 worthwhile (CC >= 7.0)


def detect_device() -> DeviceInfo:
    if torch.cuda.is_available():
        p = torch.cuda.get_device_properties(0)
        return DeviceInfo(
            device="cuda",
            name=p.name,
            total_vram_gb=round(p.total_memory / 1e9, 1),
            compute_capability=(p.major, p.minor),
            fp16_ok=p.major >= 7,
        )
    return DeviceInfo("cpu", "cpu", 0.0, None, False)


def resolve_precision(requested: str, info: DeviceInfo) -> str:
    """fp16 only on a capable GPU; otherwise fp32."""
    if requested == "fp16" and info.device == "cuda" and info.fp16_ok:
        return "fp16"
    return "fp32"


def free_vram() -> None:
    if torch.cuda.is_available():
        torch.cuda.synchronize()
        torch.cuda.empty_cache()


def free_vram_bytes() -> int:
    """Free device memory right now (0 on CPU). Chunk sizing consumes this."""
    if torch.cuda.is_available():
        free, _total = torch.cuda.mem_get_info()
        return int(free)
    return 0


def reset_peak() -> None:
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()


def vram_report(tag: str = "") -> str:
    if not torch.cuda.is_available():
        return f"[vram] {tag}: cpu"
    alloc = torch.cuda.memory_allocated() / 1e9
    peak = torch.cuda.max_memory_reserved() / 1e9
    return f"[vram] {tag}: allocated={alloc:.2f}GB peak_reserved={peak:.2f}GB"
