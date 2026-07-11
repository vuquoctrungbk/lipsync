# Codebase Summary

## Entry points

- `app.py` — Gradio UI; builds inputs/settings, calls `Pipeline.run`, returns the
  output video. Binds `127.0.0.1:7860`. **TTS delta:** audio source now a Tabs widget
  ("Âm thanh có sẵn" / "Văn bản → Giọng nói"); `generate()` gained `(tts_wav, input_mode)`
  args; 4-line source resolution logic routes text via TTS or file upload to the
  existing `prepare_audio()` path.
- `run_app.bat` — launcher (checks venv, runs `app.py`).
- `scripts/setup_env.ps1` — idempotent environment bootstrap.

## Package `lipsync/`

| File | Lines* | Summary |
|---|---|---|
| `config.py` | ~82 | Paths, `RenderConfig` dataclass (face_size, preprocess, still_mode, pose_style, expression_scale, use_enhancer, precision, sequential_vram, matting_engine, commercial_safe, seed, green_rgb, fps, crf, output_format, chunk_seconds); `MAX_AUDIO_SECONDS=600`, `SINGLE_SHOT_MAX_SECONDS=120`. |
| `hardware.py` | ~95 | `detect_device`, `resolve_precision`, `free_vram`, `free_vram_bytes`, `free_ram_bytes`, `vram_report`, `reset_peak`. |
| `ffmpeg_utils.py` | ~50 | `ffmpeg_exe`, `ffprobe_exe`, `has_ffprobe`, `has_encoder`, `run` (subprocess + error capture). |
| `audio_preprocess.py` | ~103 | `probe_duration`, `prepare_audio` (→ 16 kHz mono WAV, fail-closed 600s cap from decoded wav), `wav_duration_seconds`, `AudioError`. |
| `animation_sadtalker.py` | ~183 | `SadTalkerEngine` (load/unload); `prepare_coeff` (face detect + 3DMM + audio2coeff once for full audio) + `render_from_coeff` (facerender + paste-back per segment or full); `animate` (single-shot ≤120s). |
| `matting_base.py` | ~33 | `MattingEngine` protocol (load/unload/alpha_for/reset_clip); abstract interface. |
| `matting_rvm.py` | ~92 | `RVMMatte`: RobustVideoMatting (GPL-3); recurrent state; torch.hub pinned commit 53d74c6. |
| `matting_birefnet.py` | ~80 | `BiRefNetMatte.alpha_for(rgb)` → soft alpha; fp16 (MIT license, fast, stateless). |
| `green_compositor.py` | ~324 | `composite()` driver: stream frames → matte once → fan to sinks. `_GreenSink` (H.264 MP4 + AAC), `_WebmSink` (VP9 yuva420p WebM + Opus). Per-sink failure isolation; temp+rename atomic. |
| `chunked_facerender.py` | ~150+ | `plan_segments()` (VRAM/RAM-sized), `slice_coeff_mat()`, `render_segment()`, `iter_segment_frames()`. ±13-frame halo for `semantic_radius=13`. |
| `run_manifest.py` | ~150+ | `sha1_file()`, `cfg_fingerprint()` (render-affecting fields only), `new_manifest()`, `write_manifest()`, `load_manifest()`, `pid_alive()`, `coeff_valid()`, `validate_segments()`, `find_resumable()`, `purge_stale_runs()`. Atomic writes; dead-owner adoption. |
| `pipeline.py` | ~400+ | `Pipeline.run()` orchestration (audio → dispatch ≤120s single-shot or >120s chunked → matte + composite). `_chunked_animate()` (segment planning, coeff slicing, per-segment rendering, resumability). Keyed engine cache; render lock (serial Generate/Resume); portrait sanitization; timings. |
| `tts_bridge.py` | ~80 | Subprocess wrapper for Vietnamese TTS. `synth_text()` (validate input, call `tts_cli.py`, parse JSON response, enforce 600s render cap). TTSError for user-facing messages; temp/tts cleanup. |
| `tts_ui.py` | ~120 | Gradio tab "Văn bản → Giọng nối": textbox, duration counter, preset/clone voice dropdown, preview button ("Tạo & nghe thử"), preview invalidation on any change. Shares gpu-render lock. Degrades gracefully if `tools/tts/.venv` missing. |

\* approximate; architecture supports modular growth per phase.

## Tools: Text-to-speech (`tools/tts/`)

| File / Module | Summary |
|---|---|
| `tts_cli.py` | Entry point: parses CLI args (engine, model, text/text-file, language, voice, voice-ref, voice-ref-text, out, seed); calls `engines/{engine}.py`; outputs one JSON per line (success: ok, out, duration_s, synth_s, load_s; failure: ok=false, kind, error); chunks text ≤380 chars. |
| `engines/__init__.py` | Registry: `get_engine(name, model)` dispatcher; instantiates engine classes. |
| `engines/base.py` | `TTSEngine` base class (load, synthesize, unload, get_voices); abstract. |
| `engines/vieneu.py` | VieNeu-TTS v3 Turbo (Apache-2.0): load ONNX model (~9.7 s/call, 522 MB cached HF), synthesize (preset voices + zero-shot clone from .wav), measured RTF 1.06–1.17 (≈ realtime), CPU-only (torch-free). Inaudible Perth watermark on output. |
| `benchmark_tts.py` | Measures model load time, synthesis RTF, VRAM/RAM footprint on a test corpus. |
| `requirements.lock` | Pinned deps: vieneu==3.0.11, ONNX Runtime, HuggingFace tokenizers, numpy, scipy. |

`tts_cli.py` is reached only via subprocess from `lipsync/tts_bridge.py` (main venv untouched).

## Scripts

- `setup_tts_env.ps1` — idempotent installer for `tools/tts/.venv` (gates on $LASTEXITCODE).
- `download_models.py` — fetches + size-verifies SadTalker core + GFPGAN aux
  weights from official GitHub releases into `models/sadtalker/`.
- `cuda_smoke_test.py` — proves CUDA + fp16 matmul work (the RDP CUDA gate).
- `sync_metrics.py` — objective lip-sync scoring (LSE-D/LSE-C) via SyncNet harness in isolated `tools/syncnet/` venv; relative metrics (English-trained); per-window drift analysis.
- `matte_video.py` — CLI mattes ANY talking-head video onto green/WebM-alpha via the app's streaming matte pipeline (`iter_video_frames` + `composite` + `Pipeline._matting`); built for Colab hybrid clips, works on anything. `--alpha-from <clip>` computes alpha from a cleaner frame-aligned encode (`composite(alpha_source=...)`) — fixes matte flicker caused by the mouth-refine pass's heavy recompression (background motion is identical, so the clean clip's alpha lines up).

## Tools: Colab render offload (`tools/colab/`)

- `lipsync_render.ipynb` — versioned Colab notebook (Open-in-Colab badge, T4 free): 2 isolated uv venvs (Ditto torch 2.3.1/numpy 1.26 vs LatentSync torch 2.5.1 — conflicting stacks, mirrors the proven local 2-venv layout), pinned repo commits (`c3e47ee` / `a229c39`), HF checkpoints (`digital-avatar/ditto-talkinghead` PyTorch path + `ByteDance/LatentSync-1.5` = the 256 model), Drive job handoff (`lipsync-jobs/in|out/<job>/`), per-job `timings.json` + VRAM-peak poller. Offloads the VRAM-bound hybrid render (LatentSync-512 needs ~18 GB → 157 min/20 s on the 3060); matte stays local.

## Tests

**Shared**: `tests/conftest.py` (behavior-bearing fakes: FakeMatte, FakeRVM, FakeBiRefNet; tiny_png_bytes fixture; duck-type test doubles that record calls + return real alpha arrays).

**Default (fast, 61 tests)**: `tests/test_lipsync_units.py` (config, hardware, audio duration guard, hex parsing).

**Colab notebook (static)**: `tests/test_colab_notebook.py` (nbformat-4 JSON validity, every code cell compiles, pinned commits + HF repo + 256-config identifiers, no committed outputs, badge path). `tests/test_matte_video.py` (arg contract, torch-free module import via clean subprocess; `RUN_E2E=1` mattes a real spike clip).

**Model tests** (`RUN_MODEL_TESTS=1`): `tests/test_matting_engines.py` (engine load/unload, reset_clip, alpha shape).

**Chunked facerender** (`RUN_MODEL_TESTS=1`): `tests/test_chunked_facerender.py` (plan_segments with VRAM/RAM budgets, slice_coeff_mat, frame iterator).

**Run manifest** (`RUN_MODEL_TESTS=1`): `tests/test_run_manifest.py` (sha1_file, cfg_fingerprint, pid_alive Windows gate, dead-owner adoption).

**E2E chunked** (`RUN_E2E=1`): `tests/test_chunked_e2e.py` (600s chunked render with manifest resumability, seeded equivalence chunked-vs-full).

**E2E WebM alpha** (`RUN_E2E=1`): `tests/test_webm_alpha_e2e.py` (VP9 yuva420p WebM output, ffmpeg stdin pipe, per-sink failure handling).

**Full e2e** (`RUN_E2E=1`): `tests/test_pipeline_e2e.py` (single-shot render + green-pixel assertion) + `tests/test_sync_metrics.py` (SyncNet LSE scoring).

## Data / generated (gitignored)

- `third_party/SadTalker/` — vendored source (pinned commit).
- `models/` — checkpoints (SadTalker ~1.8 GB, GFPGAN aux ~0.7 GB, BiRefNet cache, RVM cache, VieNeu-TTS weights ~522 MB).
- `tools/syncnet/` — SyncNet isolation venv (deps conflict with app venv).
- `tools/tts/.venv/` — TTS isolation venv (VieNeu-TTS deps, ONNX Runtime, HuggingFace; pinned in `tools/tts/requirements.lock`).
- `tools/joyvasa/`, `tools/ditto/`, `tools/latentsync/` — animation-engine spike workspaces (cloned repos + venvs + weights, multi-GB); disposable, reports are the durable output.
- `voices/vi/` — user voice clones (.wav + optional .txt transcript sidecar); PII-sensitive, gitignored.
- `outputs/` — rendered MP4s. `temp/` — per-run work dirs (per-run-id scratch + intermediate files).

## Dependency notes (fragile pins)

- `torch 2.1.2 / torchvision 0.16.2` — 0.16 still ships `functional_tensor`, which
  `basicsr 1.4.2` (GFPGAN dep) imports; 0.17+ removed it.
- `numpy 1.23.5` (<2) — matches torch 2.1 build + numba/opencv.
- `librosa 0.9.2` — SadTalker targets the 0.9.x API; needs `setuptools<81` for
  `pkg_resources`.
- `scipy` (loadmat/savemat for coeff slicing in chunked renders).
- Windows `ctypes.windll.kernel32` for `pid_alive()` liveness probe (PID reuse safe).
