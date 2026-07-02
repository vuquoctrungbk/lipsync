# System Architecture

## Overview

A local, single-user desktop app that converts a still character image + a voice
audio file into a green-screen talking-head video. Everything runs on the local
GPU; no external services.

## Data flow

```
              ┌────────────┐
 image ─────▶ │            │
 audio ─────▶ │  app.py    │  Gradio UI (127.0.0.1:7860)
              │  (UI)      │
              └─────┬──────┘
                    ▼
            lipsync/pipeline.py  ── orchestrates stages, caches engines (time+uuid6 run IDs)
                    │
   ┌────────────────┼───────────────────────────┐
   ▼                ▼                             ▼
audio_preprocess  animation_sadtalker      matting_engine + green_compositor
(ffmpeg ->        (SadTalker library:      (MattingEngine protocol: RVM or BiRefNet
 16k mono wav)     detect -> 3DMM ->        alpha per frame -> composite over green ->
                   audio2coeff ->           ffmpeg encode + remux audio + warnings)
                   facerender -> MP4)
                    │                             │
                    └──────────► outputs/lipsync_green_<ts>.mp4 ◄──────────┘
```

## Modules (`lipsync/`)

| Module | Responsibility |
|---|---|
| `config.py` | Repo-relative paths; `RenderConfig` (all UI knobs, matting engine, commercial_safe flag); runtime dirs. |
| `hardware.py` | GPU detection, precision selection, VRAM helpers, `free_vram_bytes()`. |
| `ffmpeg_utils.py` | Locate ffmpeg/ffprobe; check probes availability; run commands with error capture. |
| `audio_preprocess.py` | Validate + convert audio to 16 kHz mono PCM WAV. |
| `animation_sadtalker.py` | SadTalker as a library; load/unload; optional fp16 autocast. |
| `matting_base.py` | `MattingEngine` protocol (load/unload/alpha_for/reset_clip); abstract interface. |
| `matting_rvm.py` | RobustVideoMatting (GPL-3); recurrent state; torch.hub pinned to commit 53d74c6. |
| `matting_birefnet.py` | BiRefNet (MIT); fast, stateless alpha matte per frame (fp16). |
| `green_compositor.py` | Stream frames → matte → composite on green → encode → remux; returns warnings list. |
| `pipeline.py` | Orchestrate stages; keyed matting cache (engine+precision); sanitize uploads; time+uuid6 run IDs. |

## Key design decisions

- **SadTalker used as a library, not its CLI** — we own device/precision/VRAM and
  call `init_path → CropAndExtract → Audio2Coeff → AnimateFromCoeff` directly.
- **Package lives at repo root (`lipsync/`), not `src/`** — avoids a name clash
  with SadTalker's own top-level `src` package on `sys.path`.
- **fp32 by default** — measured SadTalker peak ≈ 2.8 GB at 256/full on a 12 GB
  card; fp32 is stable (no autocast NaN risk). fp16 is an opt-in speed mode.
- **Matting engine abstraction** — `MattingEngine` protocol decouples the composite
  driver from engine choice. RVM (GPL-3) is fast but restricted to personal builds;
  `commercial_safe=True` forces BiRefNet (MIT). Keyed cache by (engine, precision)
  swaps engines mid-session without silent cache misses (key only assigned after
  successful load).
- **Reset ownership** — the compositor driver is the single owner of `reset_clip()`
  calls (once at clip start, once in `finally`). This prevents recurrent engines
  (RVM) from leaking cross-frame state into the next render, even after a crash.
- **Engines co-resident** — combined peak ≈ 4 GB, so SadTalker + RVM/BiRefNet stay
  loaded across renders for speed (`sequential_vram=True` unloads between stages
  for tighter cards).
- **Streaming compositor** — frames are processed one at a time, so memory stays
  flat regardless of clip length. Non-fatal warnings (e.g., audio fallback) are
  returned in a `warnings` list, not swallowed.

## VRAM budget & performance (RTX 3060 12 GB, measured)

| Component | Peak VRAM | Notes |
|---|---|---|
| SadTalker (256/full/fp32) | ~2.8 GB | — |
| + RVM (fp16) co-resident | ~4.0 GB | RVM: 25 fps matting @ 800x1200, 17.2 s composite on the 5.5 s clip. |
| + BiRefNet (fp16) co-resident | ~4.0 GB | BiRefNet: 3.3 fps matting, 71 s on 5.5 s clip @ 800x1200. RVM faster & stable VRAM. |

Full e2e with RVM: ~64 s (vs ~113 s with BiRefNet on same config).

## External dependencies

- SadTalker source vendored under `third_party/SadTalker` (pinned commit
  `cd4c0465ae0b54a6f85af57f5c65fec9fe23e7f8`).
- Checkpoints under `models/` (downloaded by `scripts/download_models.py`).
- ffmpeg invoked as an external executable.
