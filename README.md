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
image + audio
   -> ffmpeg: audio -> 16 kHz mono WAV
   -> SadTalker: animate the portrait (face detect -> 3DMM -> audio2coeff -> facerender)   [fp32, ~3 GB VRAM]
   -> BiRefNet: per-frame alpha matte                                                       [fp16]
   -> composite foreground over solid green + ffmpeg encode (H.264) + remux audio
   -> outputs\lipsync_green_<timestamp>.mp4
```

Matting is **required**: SadTalker keeps the source image's background, so the character is matted out and placed on green (you cannot just chroma-key the raw output — there is no green to key).

## Settings (in the UI)

| Setting | Default | Notes |
|---|---|---|
| Face render size | 256 | 512 = sharper, slower, more VRAM |
| Framing | full | `full` keeps the whole portrait (best for green screen); `crop` = head only |
| Still mode | on | less head drift, more stable from a single image |
| GFPGAN enhance | off | sharper face; slower; downloads its weights on first enable (needs internet once) |
| Expression scale | 1.0 | mouth/expression intensity |
| Pose style | 0 | head-pose preset [0–45] |
| Background color | #00B140 | the key color to fill behind the character |

## Performance (RTX 3060 12 GB, measured)

~5.5 s clip @ 256/full/fp32: animate ≈ 42 s, matte+composite ≈ 71 s. Peak VRAM ≈ 4 GB (SadTalker + BiRefNet co-resident). Matting (~2 fps at 1024) is the bottleneck — lower the face size or clip length for faster turnaround.

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
