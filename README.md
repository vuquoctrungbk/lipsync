# Lip-Sync AI — Green Screen (Vietnamese)

Turn **one still character image + a voice audio file** into a **lip-synced talking-head video on a solid green background**, ready to chroma-key into other videos. 100% local — no paid APIs, no cloud.

- **Animation:** [SadTalker](https://github.com/OpenTalker/SadTalker) (Apache-2.0) — natural head motion + blinks + lip-sync from a single image.
- **Matting:** [RobustVideoMatting](https://github.com/PeterL1n/RobustVideoMatting) (GPL-3.0, default — fast + temporally stable, personal use) or [BiRefNet](https://github.com/ZhengPeng7/BiRefNet) (MIT — set `commercial_safe=True` to force it). RVM is loaded via torch.hub pinned to commit `53d74c68…` (weights cached in `models/rvm`, ~15 MB on first run; checkpoint sha256 `3c7c1d92…4f8`).
- **Compositing/encode:** ffmpeg → solid green MP4.
- **UI:** Gradio (local, single-user).

> Audio is the **input** — the app does not generate speech. The lip-sync encoder is language-agnostic (mel-spectrogram), so Vietnamese works the same as any language. Accuracy on Vietnamese tones should be eyeballed once on your real speaker (see *Vietnamese validation*).

## Requirements

- Windows 10/11, NVIDIA GPU with **≥ 6 GB VRAM** (developed/tested on **RTX 3060 12 GB**, CUDA 12.x driver). CPU works but is very slow.
- ffmpeg on PATH (already present if you used winget). git.
- Python 3.11 is installed automatically by the setup script if missing.

## Quick start

```powershell
# 1. One-time setup: venv + PyTorch (CUDA 12.1) + deps + SadTalker + checkpoints (~3 GB)
powershell -ExecutionPolicy Bypass -File scripts\setup_env.ps1

# 2. Launch the app (opens http://127.0.0.1:7860)
run_app.bat
```

Then upload a character image + a voice audio file, click **Generate**, and download the green-screen MP4 from `outputs\`.

## How it works

```
image + audio (≤600 s)
   -> ffmpeg: audio -> 16 kHz mono WAV (length cap enforced from the decoded wav)
   -> SadTalker: face detect -> 3DMM -> audio2coeff ONCE (full audio)          [fp32]
      -> facerender: single-shot ≤120 s, else ±13-frame halo-overlapped
         segments with a crash-resumable manifest
   -> matting engine (RVM default | BiRefNet): per-frame alpha                 [fp16]
   -> one streaming matte pass fans out to the requested sinks:
        green MP4 (H.264 + AAC)  and/or  WebM VP9 alpha (yuva420p + Opus)
   -> outputs\lipsync_green_<runid>.mp4 / lipsync_alpha_<runid>.webm
```

Matting is **required**: SadTalker keeps the source image's background, so the character is matted out and placed on green (you cannot just chroma-key the raw output — there is no green to key).

## Settings (in the UI)

| Setting | Default | Notes |
|---|---|---|
| Quality preset | Draft | Draft = 256/no enhancer; High = 512 + GFPGAN; Custom = leave controls alone |
| Face render size | 256 | 512 = sharper, slower, more VRAM |
| Framing | full | `full` keeps the whole portrait (best for green screen); `crop` = head only |
| Still mode | on | less head drift, more stable from a single image |
| GFPGAN enhance | off | sharper face; slower; downloads its weights on first enable (needs internet once) |
| Expression scale | 1.0 | mouth/expression intensity |
| Pose style | 0 | head-pose preset [0–45] |
| Background color | #00B140 | the key color to fill behind the character |
| Matting engine | RVM | RVM = fast + temporally stable (GPL-3, personal use); BiRefNet = MIT, slower |
| Commercial-safe mode | off | forces BiRefNet; GPL RVM is never loaded |
| Output format | Green MP4 | WebM alpha (CapCut, true transparency) or Both — one matte pass either way |

Long clips: audio up to **600 s (10 min)**. Clips over 120 s render in
checkpointed segments — if the app dies mid-render, **Resume interrupted
render** continues from the last finished segment (compositing restarts).
The **Analyze sync drift** button scores per-60s LSE-D windows of a finished
render on demand (see Vietnamese validation below).

## Performance (RTX 3060 12 GB, measured 2026-07-03)

| Clip | Settings | Wall time | Notes |
|---|---|---|---|
| 5.5 s | 256, RVM | **64 s** (animate 47 + composite 17) | was ~113 s with BiRefNet in v1 |
| 18.6 s | 256, RVM | 116–126 s | animate 79–89 s dominates |
| 18.6 s | 512, RVM | 274 s | 512 ≈ 3× animate cost, no sync gain |
| 600 s | 256, RVM, chunked | **94 min** (≈9.4× realtime) | flat RAM/VRAM, 12 segments, exact 15000 frames |

Pure matting: RVM ≈ 25 fps vs BiRefNet ≈ 3.3 fps at 800×1200 (7.6×). Peak VRAM ≈ 6 GB co-resident. The remaining bottleneck is SadTalker's facerender + paste-back — portraits over ~2 MP slow paste-back and grow RAM (the app warns).

## Vietnamese validation

Objective lip-sync scoring (SyncNet LSE-D/LSE-C) plus a Vietnamese phoneme spot-check protocol live in [docs/vietnamese-validation-protocol.md](docs/vietnamese-validation-protocol.md). Score any render with:

```powershell
.venv\Scripts\python.exe scripts\sync_metrics.py --video outputs\clip.mp4
```

The pilot verdict on the real MC speaker is tracked there (currently blocked on real assets). Engine changes are never made silently — the protocol defines evidence-based escalation.

## Keying the green in an editor

Import the MP4 and apply a chroma-key / "Ultra Key" (Premiere), "Keyer" (DaVinci Resolve), or `colorkey`/`chromakey` (ffmpeg) on color `#00B140`. For cleaner edges you can re-render at a higher face size.

## Project layout

```
app.py                 Gradio entrypoint
lipsync/               application package (config, hardware, audio, animation, matting, compositor, pipeline)
scripts/               setup_env.ps1, download_models.py, cuda_smoke_test.py
third_party/SadTalker  vendored SadTalker (cloned, pinned commit) — gitignored
models/                checkpoints (downloaded) — gitignored
outputs/               rendered videos
tests/                 unit + opt-in E2E tests (RUN_E2E=1)
docs/                  architecture, codebase summary, usage guide
```

## Licenses

This build targets **personal (non-commercial) use**: the default matting engine, RobustVideoMatting, is **GPL-3.0**. For a commercial-safe run set `commercial_safe=True` in `RenderConfig` — that forces the MIT-licensed BiRefNet path and never loads RVM. All other runtime components (SadTalker Apache-2.0, BiRefNet MIT) are commercial-use-safe; see [NOTICE.md](NOTICE.md). No Wav2Lip/InsightFace/RMBG-2.0 weights are bundled.
