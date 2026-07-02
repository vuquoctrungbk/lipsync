# Usage Guide

## 1. Setup (one time)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_env.ps1
```

This installs Python 3.11 deps into `.venv`, PyTorch (CUDA 12.1), clones SadTalker
(pinned), downloads checkpoints (~2.5 GB), and runs the CUDA smoke test. Re-running
is safe (it skips completed steps).

If Python 3.11 is missing the script tells you to run:
`winget install --id Python.Python.3.11 --scope user`.

## 2. Run

```powershell
run_app.bat
```

Open http://127.0.0.1:7860. Upload a **character image** and a **voice audio**
file, optionally open **Settings**, then click **Generate**. The result is written
to `outputs\lipsync_green_<timestamp>.mp4` and shown in the UI.

## 3. Input tips

- **Image:** clear, front-facing single face; head-and-shoulders or half-body
  works well with `full` framing. Avoid extreme angles / occluded faces (the face
  detector will reject "no face found").
- **Audio:** any common format (wav/mp3/m4a). Converted to 16 kHz mono internally.
  Limit is 120 s per clip (configurable in `lipsync/config.py`).

## 4. Compositing the green screen

The output has a solid green background (`#00B140` by default). In your editor:

- **DaVinci Resolve:** add the clip, apply **3D Keyer** / **Qualifier**, pick the green.
- **Premiere:** **Ultra Key**, set Key Color to the green.
- **ffmpeg:** `ffmpeg -i in.mp4 -i bg.mp4 -filter_complex "[0:v]colorkey=0x00B140:0.3:0.2[fg];[1:v][fg]overlay" out.mp4`

Pick a background color that does not appear on the character (change it in Settings).

## 5. Quality vs speed

- `face size 256` (default) is fast; `512` is sharper but slower and uses more VRAM.
- Enable **GFPGAN enhance** for a crisper face (downloads weights once; slower).
- Shorter clips render proportionally faster (matting is per-frame).
- **Matting engine choice:** RVM (default) is fast (~25 fps at 800x1200, ~17 s per 5.5 s clip) and temporally stable (recurrent); BiRefNet is slower (~3.3 fps) but stateless. RVM is GPL-3 for personal/non-commercial use; set `commercial_safe=True` in `lipsync/config.py` to force BiRefNet (MIT license) in commercial contexts.
- **Reproducible renders:** set `seed: int` in `lipsync/config.py` to seed torch/numpy/random at render start; allows identical outputs for A/B comparisons (SadTalker's stochastic pose CVAE + blink sampling).

## 6. Lip-sync validation

Render a test clip and check the mouth frame-by-frame. The encoder is language-agnostic,
but accuracy is unverified on your language — confirm visually before production use.

For objective metrics, use `scripts/sync_metrics.py`:
```powershell
python scripts/sync_metrics.py --video outputs/clip.mp4 --json report.json
```
LSE-D (lower = better; ~6.2 baseline) and LSE-C (higher = better) measure audio-visual
alignment relative to the SyncNet model. SyncNet is English-trained on VoxCeleb, so treat
scores as relative (compare engines on the same clip). Absolute validation lives in
`docs/vietnamese-validation-protocol.md`.

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| "No face detected" | Use a clearer front-facing portrait. |
| CUDA out of memory | Lower face size to 256; close other GPU apps; set `sequential_vram=True` in `config.py`. Change matting engine to BiRefNet (stateless, lower VRAM footprint) in `config.py`. |
| `torch.cuda.is_available()` is False | Update NVIDIA driver; re-run `scripts\cuda_smoke_test.py`. |
| Setup fails on librosa/`pkg_resources` | Ensure `setuptools<81` (handled by setup script). |
| App won't launch over RDP | Loopback works on the host; open the URL in a browser on that machine. |
| SyncNet script fails with missing `tools/syncnet/` | Build the isolated harness venv per the rebuild commands in `docs/vietnamese-validation-protocol.md` §1 (it is NOT part of `setup_env.ps1`). |
