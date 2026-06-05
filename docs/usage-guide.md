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

## 6. Vietnamese validation (do this once)

Render a 10–30 s clip of your real speaker and check the mouth frame-by-frame. The
encoder is language-agnostic, but tonal accuracy is unverified — confirm visually
before production use.

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| "No face detected" | Use a clearer front-facing portrait. |
| CUDA out of memory | Lower face size to 256; close other GPU apps; set `sequential_vram=True` in `config.py`. |
| `torch.cuda.is_available()` is False | Update NVIDIA driver; re-run `scripts\cuda_smoke_test.py`. |
| Setup fails on librosa/`pkg_resources` | Ensure `setuptools<81` (handled by setup script). |
| App won't launch over RDP | Loopback works on the host; open the URL in a browser on that machine. |
