# Phase 02 — Hardware Abstraction + Central Config

## Context Links

- Plan overview: [plan.md](plan.md)
- Architecture/optimization: `plans/reports/researcher-260605-0035-app-architecture-inference-optimization-report.md` (sec 3.2, 3.6)
- Depends on: Phase 01 (working torch + checkpoint paths)

## Overview

- **Priority:** P1
- **Status:** pending
- **Description:** Build two small, dependency-free modules: `hardware.py` (GPU/VRAM detect, precision select, CPU fallback, VRAM guard helpers) and `config.py` (single source of truth for paths, model files, and defaults). Every downstream module imports device/dtype/paths from here — no hard-coded paths or precision flags elsewhere (DRY).

## Key Insights

- Auto-precision rule (from architecture report sec 3.6): VRAM >= 24 GB -> fp32; >= 12 GB -> fp16; else bf16; no CUDA -> cpu/fp32. On this box this resolves to **cuda + fp16**.
- fp16 is mandatory at 12 GB; the selector must NOT pick fp32 on this hardware.
- VRAM lifecycle matters more than any single optimization: helpers for `empty_cache()` + a "free memory" log let Phases 04/05 sequence model loads safely.
- Config must expose 256 vs 512 SadTalker face-region size; 256 is the OOM-safe default, 512 selectable.
- KISS: no GPU library beyond torch. No registry, no plugin system (YAGNI).

## Requirements

### Functional
- `get_device_and_precision() -> (torch.device, torch.dtype)` with the auto rule above.
- `free_vram()` helper: `torch.cuda.empty_cache()` + optional `gc.collect()`; logs free/total VRAM.
- `vram_report() -> dict` (total, reserved, allocated, free) for profiling in Phase 04/05/08.
- `config.py` exposing: project root, `models/` subpaths, `third_party/SadTalker` path, output/temp dirs, defaults (green RGB `#00B140`-ish or `0,177,64`; resolution; fps; enhancer off; face-region size 256).

### Non-functional
- Each file < 200 LoC.
- Zero side effects on import (config is data + path resolution only; hardware detect is a function call, not import-time).
- Pure-stdlib + torch; no new pip deps.

## Architecture

```
src/lipsync/
├── __init__.py
├── hardware.py     # device/precision detect, vram helpers, cpu fallback
└── config.py       # AppConfig dataclass + path constants + defaults
```

Data flow: `config.py` resolves absolute paths from project root (computed via `Path(__file__).resolve().parents[2]`). `hardware.py` is called once at pipeline start; result threaded through stages. No global mutable state.

## Related Code Files

**Create:**
- `src/lipsync/__init__.py`
- `src/lipsync/hardware.py`
- `src/lipsync/config.py`

**Modify:** none.

**Delete:** none.

## Implementation Steps

1. **`config.py` — paths + defaults.** Define an `AppConfig` dataclass (frozen) with fields:
   ```python
   from dataclasses import dataclass, field
   from pathlib import Path

   ROOT = Path(__file__).resolve().parents[2]            # D:\Project2\Lipsync
   MODELS = ROOT / "models"
   SADTALKER_SRC = ROOT / "third_party" / "SadTalker"
   SADTALKER_CKPT = MODELS / "sadtalker"
   BIREFNET_CKPT = MODELS / "birefnet"
   OUTPUTS = ROOT / "outputs"
   TEMP = ROOT / "temp"

   @dataclass(frozen=True)
   class AppConfig:
       green_rgb: tuple = (0, 177, 64)     # broadcast chroma green
       resolution: int = 512               # output video long edge
       face_region: int = 256              # SadTalker render size (OOM-safe)
       fps: int = 25
       use_enhancer: bool = False          # GFPGAN toggle
       audio_sr: int = 16000
       crf: int = 18                       # x264 quality
       codec: str = "libx264"
   ```
   Provide `ensure_dirs()` to create OUTPUTS/TEMP. Validate `face_region in (256, 512)`.

2. **`hardware.py` — device/precision.** Implement per architecture report sec 3.6 but FORCE-cap fp16 at 12 GB (never fp32 on this box):
   ```python
   import gc, torch

   def get_device_and_precision():
       if not torch.cuda.is_available():
           return torch.device("cpu"), torch.float32
       p = torch.cuda.get_device_properties(0)
       vram = p.total_memory / 1e9
       if vram >= 24:   dtype = torch.float32
       elif vram >= 12: dtype = torch.float16
       else:            dtype = torch.bfloat16
       return torch.device("cuda:0"), dtype
   ```

3. **VRAM helpers.**
   ```python
   def free_vram():
       gc.collect()
       if torch.cuda.is_available():
           torch.cuda.empty_cache()
           torch.cuda.synchronize()

   def vram_report():
       if not torch.cuda.is_available():
           return {}
       return {
           "total_gb": torch.cuda.get_device_properties(0).total_memory/1e9,
           "reserved_gb": torch.cuda.memory_reserved()/1e9,
           "allocated_gb": torch.cuda.memory_allocated()/1e9,
       }
   ```

4. **Enable global perf knobs** (call once from pipeline, defined here): `torch.backends.cuda.matmul.allow_tf32 = True`, `torch.backends.cudnn.benchmark = True`. SDPA is the default attention backend in torch 2.2 — no code change needed; document that SadTalker's attention will route through SDPA automatically.

5. **Unit-test scaffolding hooks.** Keep functions pure so Phase 08 can assert: on this box `get_device_and_precision()` returns `(cuda:0, float16)`; on a CPU-only mock returns `(cpu, float32)`.

## Todo List

- [ ] Create `src/lipsync/__init__.py`
- [ ] Write `config.py` (AppConfig + paths + ensure_dirs + validation)
- [ ] Write `hardware.py` (`get_device_and_precision`, `free_vram`, `vram_report`)
- [ ] Add TF32/cudnn.benchmark enable helper
- [ ] Manual check: prints `(cuda:0, torch.float16)` on the box

## Success Criteria

- `python -c "from src.lipsync.hardware import get_device_and_precision as g; print(g())"` prints `(device(type='cuda', index=0), torch.float16)`.
- `config.AppConfig().face_region == 256` default; raises on invalid face_region.
- All paths in `config.py` resolve to absolute `D:\Project2\Lipsync\...` locations.
- Both files < 200 LoC; no import-time side effects (importing config does not touch GPU).

## Risk Assessment

| Risk | Likelihood x Impact | Mitigation |
|------|---------------------|------------|
| Precision selector picks fp32 -> OOM downstream | Low x High | Explicit 12 GB -> fp16 branch; assert dtype==fp16 on cuda in Phase 04 |
| Path resolution wrong (parents index) | Low x Medium | Compute from `__file__`; print resolved ROOT in a self-check |
| cudnn.benchmark causes non-determinism in tests | Low x Low | Acceptable for inference app; note in Phase 08 |

## Security Considerations

- No external input here; config defaults only. Green RGB and resolution validated to safe ranges before use in ffmpeg (Phase 05) to avoid command-injection via numeric args.

## Next Steps

- Unblocks Phases 04, 05, 06 (all import device/dtype/paths/free_vram from here).
- Phase 03 (audio) consumes `AppConfig.audio_sr`.
