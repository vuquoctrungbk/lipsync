# System Architecture

## Overview

A local, single-user desktop app that converts a still character image + a voice
audio file into a green-screen talking-head video. Everything runs on the local
GPU; no external services. Audio input can come from an uploaded file OR
Vietnamese text synthesized by an isolated TTS subprocess.

## Text-to-speech (TTS) pre-stage

Optional Vietnamese text input (VieNeu-TTS v3 Turbo) runs in an isolated subprocess
at `tools/tts/tts_cli.py` to avoid dependency conflicts. The subprocess is reached
via `lipsync/tts_bridge.py` (main-venv wrapper) which enforces a 600 s render-time
cap and cleans up temp files. CLI contract is JSON-based (one JSON object per line
on stdout; exit code 1 on failure). Output: 48 kHz WAV file passed to the standard
`prepare_audio()` path.

**Isolation rationale:** VieNeu deps (ONNX Runtime, HuggingFace tokenizers) have
pinned versions in `tools/tts/requirements.lock`; main venv is untouched
(`tools/tts/.venv` only). This mirrors the `tools/syncnet/` precedent.

**Voices:** 10 SDK-bundled Vietnamese presets + zero-shot clone from user-supplied
5–10 s samples (refs cached at `voices/vi/`, gitignored).

## Data flow

```
              ┌────────────┐
 image ─────▶ │            │
 audio ─────▶ │  app.py    │  Gradio UI (127.0.0.1:7860)
 text ──┐     │  (UI)      │
        │     └─────┬──────┘
        │           ▼
        │   lipsync/pipeline.py  ── orchestrates stages, caches engines (time+uuid6 run IDs)
        │           │
        └──▶ tools/tts/tts_cli.py (isolated subprocess, JSON protocol)
            ├─ VieNeu-TTS v3 Turbo (ONNX, 48 kHz)
            └─ WAV output ──▶ prepare_audio() [re-enters pipeline]
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
| `config.py` | Repo-relative paths; `RenderConfig` (face_size, preprocess, still_mode, expression_scale, use_enhancer, matting_engine, commercial_safe, output_format, chunk_seconds, seed); `MAX_AUDIO_SECONDS=600`, `SINGLE_SHOT_MAX_SECONDS=120`; runtime dirs. |
| `hardware.py` | GPU detection, precision selection, VRAM/RAM helpers (`free_vram_bytes()`, `free_ram_bytes()`); CUDA gate. |
| `ffmpeg_utils.py` | Locate ffmpeg/ffprobe; check encoder availability (libvpx-vp9 for WebM); run commands with error capture. |
| `audio_preprocess.py` | Validate + convert audio to 16 kHz mono PCM WAV (fail-closed 600s cap from decoded wav); `wav_duration_seconds()`. |
| `animation_sadtalker.py` | SadTalker as a library; `prepare_coeff()` (face detect + 3DMM + audio2coeff once) + `render_from_coeff()` (facerender + paste-back per segment or full); optional fp16 autocast. |
| `matting_base.py` | `MattingEngine` protocol (load/unload/alpha_for/reset_clip); abstract interface. |
| `matting_rvm.py` | RobustVideoMatting (GPL-3); recurrent state; torch.hub pinned to commit 53d74c6. |
| `matting_birefnet.py` | BiRefNet (MIT); fast, stateless alpha matte per frame (fp16). |
| `green_compositor.py` | `composite()` driver: stream frames → matte once → fan to per-format sinks (_GreenSink H.264+AAC, _WebmSink VP9 yuva420p via ffmpeg stdin). Per-sink failure isolation; temp+rename. |
| `chunked_facerender.py` | `plan_segments()` (sized by free VRAM/RAM), `slice_coeff_mat()`, `render_segment()`, `iter_segment_frames()`. ±13-frame halo overlap for `semantic_radius=13` conditioning. |
| `run_manifest.py` | Atomic manifest writes (temp + os.replace). Coeff SHA1+rows binding, config fingerprint, dead-owner adoption, `latest_resumable()` for resume button. |
| `pipeline.py` | Orchestrate: prepare audio → dispatch (≤120s single-shot or >120s chunked) → matte + composite. Keyed engine cache (engine+precision); render lock (serial Generate/Resume); portrait sanitization. |
| `tts_bridge.py` | Subprocess wrapper for `tools/tts/tts_cli.py`. Enforces 600 s render cap (fail-closed via existing `wav_duration_seconds`), captures JSON response, cleans up temp files >2 days old, raises `TTSError` for Vietnamese-specific messages. |
| `tts_ui.py` | Gradio tab "Văn bản → Giọng nối" (Text → Voice): textbox + duration counter, preset/clone dropdown, MANDATORY preview ("Tạo & nghe thử") before Generate. Any text/voice/engine/ref change invalidates preview. Shares render concurrency with gpu-render lock. Degrades to setup instructions if `tools/tts/.venv` absent. |

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

## Output sinks

The `composite()` driver in `green_compositor.py` streams animated frames through
ONE matting pass, then fans them out to per-format sinks in a single loop:

- **_GreenSink** (H.264 MP4 + AAC): imageio H.264 encoder writes silent video
  (yuv420p), then ffmpeg muxes the original audio and applies AAC encoding (192 kbps).
  Audio mux failures degrade gracefully: the app emits the video-only version with a
  warning (silent output is recorded, not lost).

- **_WebmSink** (VP9 yuva420p WebM + Opus): pipes raw RGBA frames to ffmpeg
  stdin (straight alpha, unpremultiplied). `stderr` is drained to a file (Windows
  pipe buffer is small; undrained stderr causes silent hangs). VP9 encodes with
  `-pix_fmt yuva420p` to preserve per-frame alpha for transparency in editors like
  CapCut. Opus audio is muxed at standard quality. ffmpeg crashes mid-stream are
  caught and wrapped as warnings; partial files are cleaned up.

**Per-sink failure isolation**: if one sink dies (e.g., disk full, ffmpeg crash),
its error becomes a warning and the other sinks keep streaming. Sinks write to
temporary names (`.tmp.mp4`, `.tmp.webm`) and only rename on success, so a killed
render never leaves a plausible-looking partial output.

## Long-audio chunking

Audio over 120 seconds renders in halo-overlapped facerender segments. Rendering
longer clips in one shot fails: VRAM stacks up (paste-back buffers N frames at
source resolution in RAM; facerender needs n*3*S*S*4B per frame on GPU) and wall
time grows unbounded.

**Halo overlap**: SadTalker's `semantic_radius=13` means every rendered frame
conditions on a ±13-row window of the motion-coefficient matrix. Naive segment
splits would cause expression stutter at boundaries (the edge rows would be
replicated). Instead, segments render with a ±13-frame halo, then the halo frames
are dropped when consumed by the compositor. The compositor streams directly from
`iter_segment_frames`, making trimming frame-accurate and free (no concat step).

**Coefficient computation**: `audio2coeff` runs ONCE for the full audio (measured
11.3 s for 600 s audio on the RTX 3060 at a 2.45 GB VRAM peak; the resulting
(15000, 70) mat file is only ~8 MB). Once the coefficient matrix is ready, it is
sliced per segment and bound to the manifest.

**Chunking strategy** (`chunked_facerender.py`):
- `plan_segments()` sizes kept frames (the non-halo portion per segment) based on
  measured free VRAM and RAM headroom at render start. Default: 1250 kept frames
  at 256 face size, 750 at 512 (larger face = larger paste-back buffers in RAM).
- Segments tile the coeff rows exactly (no gap, no overlap in kept ranges).
- Each segment renders in-process under a chdir guard (`os.chdir` to the run
  dir, `finally`-restored) — the vendored mux writes CWD-relative temp files
  that must land in the run dir, not wherever the app was launched.
- Segment videos are written to disk and validated (ffprobe frame count checked).

**Manifest & resumability** (`run_manifest.py`):
- Every chunked render writes an atomic manifest binding inputs (image/audio SHA1),
  render config fingerprint (exactly the coeff-affecting fields: `face_size`,
  `preprocess`, `still_mode`, `pose_style`, `expression_scale`, `use_enhancer`,
  `precision`, `fps`, `chunk_seconds`, `seed`), and the coeff SHA1+row count.
- Composite-only knobs (green color, CRF, output format) are excluded on purpose:
  changing them must not orphan hours of rendered segments.
- All manifest writes are atomic (temp + `os.replace`).
- Progress ("stage") is always derived from disk facts (which segments exist +
  have the correct frame count); there is no stored stage field to drift.
- Dead-owner adoption: if the app crashes mid-render and then restarts, the
  latest resumable run (if its owner PID is dead and the coeff is valid) can be
  resumed. The UI Resume button presents the oldest incomplete run for user
  confirmation.

## VRAM budget & performance (RTX 3060 12 GB)

| Clip | Settings | Wall time | Animate | Composite | RAM (free) | VRAM (free) | Notes |
|---|---|---|---|---|---|---|---|
| 5.5 s | 256, RVM | 64 s | 47 s | 17 s | — | — | v1 single-shot path. |
| 18.6 s | 256, RVM | 116–126 s | 79–89 s | ~30 s | — | — | animate dominates. |
| 18.6 s | 512, RVM | 274 s | — | — | — | — | 512 ≈ 3× animate cost. |
| **600 s** | **256, RVM, chunked** | **94 min** | **~3859 s (64 min)** | **~1765 s (29 min)** | **42.0–44.7 GB** | **5.85 GB** | 12 segments, ±13-frame halo. Chunked-vs-full seeded diff: 0.460/255 (max). |

**Component peaks**:
- SadTalker (256/full/fp32): ~2.8 GB VRAM
- SadTalker + RVM (fp16) co-resident: ~4.0 GB VRAM
- SadTalker + BiRefNet (fp16) co-resident: ~4.0 GB VRAM
- Matting alone: RVM ~25 fps @ 800×1200; BiRefNet ~3.3 fps (7.6× slower).

Long-audio renders (600s+) stay flat in RAM and VRAM across the 12+ segments
thanks to chunked facerender. The bottleneck shifts from VRAM to wall time
(paste-back CPU at source resolution scales with image area; large portraits
slow the render proportionally). Portraits >2 MP are flagged with a warning.

## Validation harness

Optional lip-sync scoring via `scripts/sync_metrics.py` (objective LSE-D/LSE-C
metrics via SyncNet) and `tools/syncnet/` (isolated venv; SyncNet deps conflict
with app venv). LSE-D (lower = better sync) targets ~6.5–8 for real talking heads;
LSE-C (higher = better) is model confidence. Scores are relative (English-trained
model; absolute Vietnamese judgment lives in `docs/vietnamese-validation-protocol.md`).

The UI "Analyze sync drift" button (opt-in; ~1 min SyncNet per minute of video)
scores per-60s windows of a finished render for drift detection. Never automatic.

## Cloud render offload (optional, in evaluation)

The next-gen hybrid animation path (Ditto motion + LatentSync mouth-refine) is
VRAM-bound locally: LatentSync-512 needs ~18 GB → 157 min for a 20 s clip on the
3060. `tools/colab/lipsync_render.ipynb` offloads just that stage to Colab
(T4 free, config 256) with Google Drive job handoff (`lipsync-jobs/in|out/`);
`scripts/matte_video.py` then feeds the returned clip through the SAME local
matte → green/WebM-alpha pipeline (privacy: only image + audio leave the
machine, matting stays local). Details: `plans/260711-0040-colab-t4-hybrid-render-offload/`.

## External dependencies

- SadTalker source vendored under `third_party/SadTalker` (pinned commit
  `cd4c0465ae0b54a6f85af57f5c65fec9fe23e7f8`).
- Checkpoints under `models/` (downloaded by `scripts/download_models.py`).
- ffmpeg invoked as an external executable.
