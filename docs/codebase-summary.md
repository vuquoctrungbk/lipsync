# Codebase Summary

## Entry points

- `app.py` — Gradio UI; builds inputs/settings, calls `Pipeline.run`, returns the
  output video. Binds `127.0.0.1:7860`.
- `run_app.bat` — launcher (checks venv, runs `app.py`).
- `scripts/setup_env.ps1` — idempotent environment bootstrap.

## Package `lipsync/`

| File | Lines* | Summary |
|---|---|---|
| `config.py` | ~71 | Paths (repo-root relative), `RenderConfig` dataclass (matting_engine, commercial_safe, seed), `ensure_runtime_dirs`. |
| `hardware.py` | ~85 | `detect_device`, `resolve_precision`, `free_vram`, `free_vram_bytes`, `vram_report`, `reset_peak`. |
| `ffmpeg_utils.py` | ~50 | `ffmpeg_exe`, `ffprobe_exe`, `has_ffprobe`, `run` (subprocess + error capture). |
| `audio_preprocess.py` | ~75 | `probe_duration`, `prepare_audio` (→ 16 kHz mono WAV), `AudioError`. |
| `animation_sadtalker.py` | ~130 | `SadTalkerEngine` (load/unload/animate); mirrors SadTalker inference flow. |
| `matting_base.py` | ~33 | `MattingEngine` protocol (load/unload/alpha_for/reset_clip); abstract interface. |
| `matting_rvm.py` | ~92 | `RVMMatte`: RobustVideoMatting (GPL-3); recurrent state; torch.hub pinned commit 53d74c6. |
| `matting_birefnet.py` | ~80 | `BiRefNetMatte.alpha_for(rgb)` → soft alpha; fp16 (MIT license, fast, stateless). |
| `green_compositor.py` | ~85 | `composite_to_green` (stream → matte → green → encode → remux); returns (Path, warnings). |
| `pipeline.py` | ~180 | `Pipeline.run` orchestration; keyed matting cache; time+uuid6 run IDs; portrait sanitization; timings. |

\* approximate; all under the 200-line module guideline.

## Scripts

- `download_models.py` — fetches + size-verifies SadTalker core + GFPGAN aux
  weights from official GitHub releases into `models/sadtalker/`.
- `cuda_smoke_test.py` — proves CUDA + fp16 matmul work (the RDP CUDA gate).
- `sync_metrics.py` — objective lip-sync scoring (LSE-D/LSE-C) via SyncNet harness in isolated `tools/syncnet/` venv; relative metrics (English-trained); per-window drift analysis.

## Tests

Default (fast): `tests/test_lipsync_units.py` (config, hardware, audio guard, hex parsing).

Optional (`RUN_MODEL_TESTS=1`): `tests/test_matting_engines.py` (load, unload, reset_clip, alpha shape).

Full e2e (`RUN_E2E=1`): `tests/test_pipeline_e2e.py` (render + green-pixel assertion) + `tests/test_sync_metrics.py` (SyncNet LSE scoring).

## Data / generated (gitignored)

- `third_party/SadTalker/` — vendored source (pinned commit).
- `models/` — checkpoints (SadTalker ~1.8 GB, GFPGAN aux ~0.7 GB, BiRefNet cache, RVM cache).
- `tools/syncnet/` — SyncNet isolation venv (deps conflict with app venv).
- `outputs/` — rendered MP4s. `temp/` — per-run work dirs (per-run-id scratch + intermediate files).

## Dependency notes (fragile pins)

- `torch 2.1.2 / torchvision 0.16.2` — 0.16 still ships `functional_tensor`, which
  `basicsr 1.4.2` (GFPGAN dep) imports; 0.17+ removed it.
- `numpy 1.23.5` (<2) — matches torch 2.1 build + numba/opencv.
- `librosa 0.9.2` — SadTalker targets the 0.9.x API; needs `setuptools<81` for
  `pkg_resources`.
