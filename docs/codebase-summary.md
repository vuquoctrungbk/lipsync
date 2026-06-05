# Codebase Summary

## Entry points

- `app.py` — Gradio UI; builds inputs/settings, calls `Pipeline.run`, returns the
  output video. Binds `127.0.0.1:7860`.
- `run_app.bat` — launcher (checks venv, runs `app.py`).
- `scripts/setup_env.ps1` — idempotent environment bootstrap.

## Package `lipsync/`

| File | Lines* | Summary |
|---|---|---|
| `config.py` | ~70 | Paths (repo-root relative), `RenderConfig` dataclass, `ensure_runtime_dirs`. |
| `hardware.py` | ~70 | `detect_device`, `resolve_precision`, `free_vram`, `vram_report`, `reset_peak`. |
| `ffmpeg_utils.py` | ~45 | `ffmpeg_exe`, `ffprobe_exe`, `run` (subprocess + error capture). |
| `audio_preprocess.py` | ~75 | `probe_duration`, `prepare_audio` (→ 16 kHz mono WAV), `AudioError`. |
| `animation_sadtalker.py` | ~130 | `SadTalkerEngine` (load/unload/animate); mirrors SadTalker inference flow. |
| `matting_birefnet.py` | ~80 | `BiRefNetMatte.alpha_for(rgb)` → soft alpha; fp16. |
| `green_compositor.py` | ~80 | `composite_to_green` (stream → matte → green → encode → remux). |
| `pipeline.py` | ~110 | `Pipeline.run` orchestration; engine caching; timings. |

\* approximate; all under the 200-line module guideline.

## Scripts

- `download_models.py` — fetches + size-verifies SadTalker core + GFPGAN aux
  weights from official GitHub releases into `models/sadtalker/`.
- `cuda_smoke_test.py` — proves CUDA + fp16 matmul work (the RDP CUDA gate).

## Tests

- `tests/test_lipsync_units.py` — fast unit tests (config, hardware, audio guard,
  hex parsing). 6 tests.
- `tests/test_pipeline_e2e.py` — opt-in (`RUN_E2E=1`) full render + green-pixel
  assertion using SadTalker example assets.

## Data / generated (gitignored)

- `third_party/SadTalker/` — vendored source (pinned commit).
- `models/` — checkpoints (SadTalker ~1.8 GB, GFPGAN aux ~0.7 GB, BiRefNet cache).
- `outputs/` — rendered MP4s. `temp/` — per-run scratch.

## Dependency notes (fragile pins)

- `torch 2.1.2 / torchvision 0.16.2` — 0.16 still ships `functional_tensor`, which
  `basicsr 1.4.2` (GFPGAN dep) imports; 0.17+ removed it.
- `numpy 1.23.5` (<2) — matches torch 2.1 build + numba/opencv.
- `librosa 0.9.2` — SadTalker targets the 0.9.x API; needs `setuptools<81` for
  `pkg_resources`.
