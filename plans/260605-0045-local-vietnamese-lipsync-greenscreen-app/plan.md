---
title: "Local Vietnamese Lip-Sync Green-Screen Desktop App"
description: "Local Gradio app: still image + Vietnamese audio -> talking-head MP4 on solid green background, via SadTalker + BiRefNet + ffmpeg."
status: pending
priority: P1
effort: 26h
branch: (not a git repo yet)
tags: [lipsync, sadtalker, birefnet, gradio, vietnamese, green-screen, local-inference]
blockedBy: [260703-0017-pa2-v2-completion-tech-refresh]
created: 2026-06-05
---

# Local Vietnamese Lip-Sync Green-Screen App — Implementation Plan

100% local desktop app. Input: one still character image + one Vietnamese voice WAV/MP3.
Output: lip-synced talking-head MP4 rendered on a solid green chroma-key background.

Single engine for v1: **SadTalker** (Apache-2.0). UI: **Gradio**. Matting: **BiRefNet** (MIT).
Output: flat solid-green H.264/265 MP4. MuseTalk and true-alpha export are DEFERRED (out of v1 scope).

> **Phase-8 pilot gate status (2026-07-03):** absorbed by the v2 plan's Phase 1.
> The measurement harness is live (`scripts/sync_metrics.py`; calibrated anchors
> synced 6.564 / desynced 15.21) and the substitute-audio baseline is strong
> (LSE-D ≈ 6.2, under the 8 PASS bar), but the verdict itself is
> **BLOCKED-ON-ASSETS** — awaiting the real MC portrait + VN voice clip. Track in
> `docs/vietnamese-validation-protocol.md` §6. This plan stays open only for that
> gate; everything else shipped in commit `3786a12` and was upgraded by
> `plans/260703-0017-pa2-v2-completion-tech-refresh/` (RVM matting, WebM alpha,
> 600s chunked rendering + resume).

## Pipeline (locked order)

```
install/env -> audio prep (ffmpeg -> 16kHz mono WAV)
  -> face detect/align -> SadTalker animate (fp16 amp)
  -> [optional GFPGAN enhance, toggle] -> BiRefNet per-frame matte (alpha)
  -> composite onto solid green (ffmpeg) -> encode MP4 -> return to Gradio
```

VRAM-aware: load animate models -> run -> `empty_cache()` -> load matte model -> run.
fp16 autocast is MANDATORY (12 GB budget). Matting is REQUIRED (raw output keeps source bg, not green).

## Phases

| # | Phase | File | Effort | Status |
|---|-------|------|--------|--------|
| 1 | Environment & dependency setup | [phase-01-environment-and-dependency-setup.md](phase-01-environment-and-dependency-setup.md) | 4h | done |
| 2 | Hardware abstraction + central config | [phase-02-hardware-abstraction-and-config.md](phase-02-hardware-abstraction-and-config.md) | 2h | done |
| 3 | Audio preprocessing module | [phase-03-audio-preprocessing-module.md](phase-03-audio-preprocessing-module.md) | 1.5h | done |
| 4 | SadTalker animation wrapper (fp16) | [phase-04-sadtalker-animation-wrapper.md](phase-04-sadtalker-animation-wrapper.md) | 5h | done |
| 5 | BiRefNet matte + green compositing | [phase-05-birefnet-matte-and-green-compositing.md](phase-05-birefnet-matte-and-green-compositing.md) | 4h | done |
| 6 | Pipeline orchestration | [phase-06-pipeline-orchestration.md](phase-06-pipeline-orchestration.md) | 2.5h | done |
| 7 | Gradio UI | [phase-07-gradio-ui.md](phase-07-gradio-ui.md) | 2h | done |
| 8 | Vietnamese validation + E2E testing | [phase-08-vietnamese-validation-and-e2e-testing.md](phase-08-vietnamese-validation-and-e2e-testing.md) | 3h | done* |
| 9 | Packaging + docs + licenses | [phase-09-packaging-docs-and-licenses.md](phase-09-packaging-docs-and-licenses.md) | 2h | done |

\* E2E test passes (green output verified); the Vietnamese-speaker pilot gate needs a real VN clip from the user.

> Build note: measured SadTalker peak ≈2.8 GB fp32 at 256/full → fp16 is NOT mandatory on 12 GB (plan assumption corrected by measurement); default is fp32. dlib dropped (SadTalker uses FAN/S3FD via face_alignment). Package moved to repo-root `lipsync/` to avoid `src` clash with SadTalker.

Total: ~26h.

## Dependency graph

- P1 (env) blocks everything (no Python yet on the box).
- P2 (hardware/config) blocks P4, P5, P6 (they consume device/precision/paths).
- P3 (audio) blocks P4 (SadTalker needs 16k mono WAV) and P6.
- P4 (animate) + P5 (matte/green) feed P6 (orchestration). P5 needs P4 output frames.
- P6 blocks P7 (UI calls pipeline) and P8 (E2E runs pipeline).
- P8 gates "done" (Vietnamese pilot must pass before declaring v1 complete).
- P9 runs last; depends on a working P7 + passing P8.

## Key risks (full detail per phase)

- SadTalker dlib/face_alignment build fails on Windows -> MediaPipe CPU fallback (P1, P4).
- fp16 OOM on 12 GB -> 256px face region, sequential model load, empty_cache (P2, P4, P5).
- RDP CUDA pass-through unverified -> first-run CUDA smoke test (P1).
- Vietnamese sync unverified -> mandatory pilot validation gate (P8).
- librosa/numba/numpy conflicts -> pinned requirements + verified import order (P1).

## Engine note

SadTalker's `inference.py` has NO fp16 flag; fp16 is added by wrapping forward passes in
`torch.amp.autocast('cuda', dtype=torch.float16)` inside our wrapper (P4). We import SadTalker's
inference modules as a library, not via its CLI, so we control device/precision/VRAM lifecycle.
