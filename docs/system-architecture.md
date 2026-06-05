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
            lipsync/pipeline.py  ── orchestrates stages, caches engines
                    │
   ┌────────────────┼───────────────────────────┐
   ▼                ▼                             ▼
audio_preprocess  animation_sadtalker      matting_birefnet + green_compositor
(ffmpeg ->        (SadTalker library:      (BiRefNet alpha per frame ->
 16k mono wav)     detect -> 3DMM ->        composite over solid green ->
                   audio2coeff ->           ffmpeg encode + remux audio)
                   facerender -> MP4)
                    │                             │
                    └──────────► outputs/lipsync_green_<ts>.mp4 ◄──────────┘
```

## Modules (`lipsync/`)

| Module | Responsibility |
|---|---|
| `config.py` | Repo-relative paths; `RenderConfig` (all UI knobs); runtime dirs. |
| `hardware.py` | GPU detection, precision selection, VRAM helpers, CPU fallback. |
| `ffmpeg_utils.py` | Locate ffmpeg/ffprobe; run commands with error capture. |
| `audio_preprocess.py` | Validate + convert audio to 16 kHz mono PCM WAV. |
| `animation_sadtalker.py` | SadTalker as a library; load/unload; optional fp16 autocast. |
| `matting_birefnet.py` | BiRefNet alpha matte per frame (fp16). |
| `green_compositor.py` | Stream frames → matte → composite on green → encode → remux. |
| `pipeline.py` | Wire stages; cache engines; timings/VRAM report. |

## Key design decisions

- **SadTalker used as a library, not its CLI** — we own device/precision/VRAM and
  call `init_path → CropAndExtract → Audio2Coeff → AnimateFromCoeff` directly.
- **Package lives at repo root (`lipsync/`), not `src/`** — avoids a name clash
  with SadTalker's own top-level `src` package on `sys.path`.
- **fp32 by default** — measured SadTalker peak ≈ 2.8 GB at 256/full on a 12 GB
  card; fp32 is stable (no autocast NaN risk). fp16 is an opt-in speed mode.
- **Matting is mandatory** — the animated output retains the source background;
  the foreground is matted and placed on green (no green to chroma-key otherwise).
- **Engines co-resident** — combined peak ≈ 4 GB, so SadTalker + BiRefNet stay
  loaded across renders for speed (`sequential_vram=True` unloads between stages
  for tighter cards).
- **Streaming compositor** — frames are processed one at a time, so memory stays
  flat regardless of clip length.

## VRAM budget (RTX 3060 12 GB, measured)

| Stage | Peak |
|---|---|
| SadTalker (256/full/fp32) | ~2.8 GB |
| + BiRefNet (fp16) co-resident | ~4.0 GB |

## External dependencies

- SadTalker source vendored under `third_party/SadTalker` (pinned commit
  `cd4c0465ae0b54a6f85af57f5c65fec9fe23e7f8`).
- Checkpoints under `models/` (downloaded by `scripts/download_models.py`).
- ffmpeg invoked as an external executable.
