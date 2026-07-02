---
phase: 1
title: "Validation Harness & VN Pilot"
status: completed  # pilot verdict itself BLOCKED-ON-ASSETS (explicit criterion arm)
effort: "1.5d"
priority: P1
dependencies: []
---

# Phase 1: Validation Harness & VN Pilot

## Overview

Build the objective lip-sync measurement harness (SyncNet LSE-D/LSE-C) + Vietnamese phoneme spot-check protocol, record the SadTalker baseline, and close the v1 Phase-8 Vietnamese pilot gate. Every later quality decision (RVM visual check, LatentSync gain, MuseTalk bake-off, chunk drift) consumes this harness — it ships FIRST.

## Requirements

- Functional: given any MP4 with speech audio → print/save LSE-D + LSE-C; per-60s-window scores for long videos; reproducible (±0.5 across reruns).
- Functional: documented VN pilot protocol + recorded verdict on the REAL MC portrait + voice clip.
- Non-functional: harness must NOT pollute the app venv (syncnet deps conflict with pinned numpy 1.23.5/librosa 0.9.2) — isolated venv, invoked via subprocess/CLI.

## Architecture

```
tools/syncnet/            # gitignored working dir
  .venv/                  # isolated Python env (own torch OK)
  syncnet_python/         # cloned github.com/joonson/syncnet_python
scripts/sync_metrics.py   # thin CLI wrapper: --video X [--window 60] -> JSON + table
docs/vietnamese-validation-protocol.md   # protocol + pilot verdict + annotated frames
```

`sync_metrics.py` shells into `tools/syncnet/.venv` python; parses LSE-D/LSE-C from syncnet output; `--window 60` splits video via ffmpeg `-ss/-t` into temp segments and scores each (consumed on demand by the phase-7 "Analyze sync drift" button — never auto-run inside a render).

## Related Code Files

- Create: `scripts/sync_metrics.py`, `docs/vietnamese-validation-protocol.md`, `tests/test_sync_metrics.py` (arg/parse unit, no model)
- Modify: `.gitignore` (add `tools/`), `README.md` (validation section pointer — final wording in phase 7)

## Implementation Steps

1. Clone syncnet_python into `tools/syncnet/` and CHECK OUT a recorded commit (pin before venv creation); create isolated venv (any compatible torch; GPU optional — CPU acceptable for 10-30s clips); download S3FD + syncnet weights per repo README, note source/revision. Measure harness throughput (sec per 30s of video, CPU vs CUDA if available) — this number decides how usable on-demand drift analysis is on 600s outputs (phases 4/7). Timebox 0.5d; if install fails → fallback to Wav2Lip repo's `evaluation/` scripts; if both fail → document, harness = manual protocol only, flag to user.
2. Write `scripts/sync_metrics.py`: subprocess wrapper, JSON output `{video, lse_d, lse_c, windows: [{start, end, lse_d, lse_c}]}`; resolves ffmpeg via the existing helper: `sys.path.insert(0, repo_root)` + `from lipsync.ffmpeg_utils import ffmpeg_exe` — the chain is torch-free (`lipsync/__init__.py` has no imports; same pattern as `tests/test_pipeline_e2e.py:13-14`). One resolver everywhere; no parallel reimplementation.
3. Calibrate: score (a) a real talking-head video (expect LSE-D ≈ 6.5-8), (b) an intentionally desynced copy (`ffmpeg -itsoffset 0.5` on audio; expect LSE-D visibly worse). Both recorded in the protocol doc as sanity anchors.
4. Write `docs/vietnamese-validation-protocol.md`: thresholds (PASS: LSE-D < 8 AND phoneme spot-check ≥4/5), phoneme checklist — bilabials /b,m,p/ full lip closure at onset frame, rounded /o,u,ô/ narrowed lips 2 frames pre-release, open /a,ă,â/ jaw-drop lag ≤1 frame @25fps; FAIL escalation = present evidence + options (tune 512/enhancer, LatentSync pass, MuseTalk, accept) — never silent-swap.
5. Baseline runs: current pipeline @256 and @512 on the REAL VN clip + portrait (user-supplied; if not yet delivered, use any clear VN speech clip for harness bring-up and mark pilot BLOCKED-ON-ASSETS). Render the @256 baseline 3× UNSEEDED and record the LSE-D spread σ — SadTalker output is stochastic (unseeded CVAE pose noise `src/audio2pose_models/audio2pose.py:68-77`, unseeded blink `src/generate_batch.py:37-49`), so single-render comparisons measure noise; phase 5/6 win-thresholds = max(0.5, 2σ), and cross-engine comparisons pin `cfg.seed` (added in phase 2). Record LSE-D/LSE-C + per-stage timings into the protocol doc.
6. Execute pilot: frame-extract 5 phoneme transitions (`ffmpeg -ss T -vframes 1`), fill checklist, record verdict PASS/FAIL + 3-4 annotated frames into `docs/`.

## Success Criteria

- [x] `python scripts/sync_metrics.py --video outputs/<any>.mp4` prints LSE-D/LSE-C; two consecutive runs differ ≤0.5 (measured Δ 0.000)
- [x] Desync sanity check shows clearly worse LSE-D than the synced anchor (15.21 vs 6.564; adelay method — see protocol doc §4)
- [x] SadTalker baseline @256 + @512 recorded (scores + timings, protocol doc §5)
- [x] Render-to-render LSE-D spread σ (3× @256) recorded — σ 0.058, P5/P6 floor = 0.5
- [x] Harness throughput recorded — CUDA ≈50-60s per 30s of video; CPU mode N/A (vendored torch.load lacks map_location; documented deviation)
- [x] VN pilot verdict recorded (PASS/FAIL + evidence) OR explicitly BLOCKED-ON-ASSETS with harness proven on substitute audio → BLOCKED-ON-ASSETS arm, harness proven
- [x] App venv untouched (`pip check` clean; unit suite 28 passed)

## Completion Notes (2026-07-03)

All success criteria met (pilot verdict via its explicit BLOCKED-ON-ASSETS arm):
- Harness live: `joonson/syncnet_python@907c0b57` + isolated venv (torch 2.5.1+cu121, py3.11); weights syncnet_v2.model + sfd_face.pth from robots.ox.ac.uk. Install was painless — upstream modernized (the 2017-era dep fear didn't materialize). CPU mode N/A (vendored `torch.load` lacks `map_location` with a CUDA torch build) — GPU-only, documented in the protocol doc with throughput ≈50-60s per 30s of video.
- Calibration: synced anchor LSE-D 6.564 / desynced (adelay 1.2s) 15.21 — clean separation. Two methodology findings recorded in the protocol doc: SyncNet re-aligns offsets ≤0.6s by design (vshift search); `-itsoffset` desyncs are destroyed by syncnet's own `-async 1` preprocessing — physical `adelay` required.
- Reproducibility: identical scores on re-run (Δ 0.000 ≤ 0.5 bar).
- Baselines on 18.6s VN TTS (edge-tts vi-VN-HoaiMyNeural) + `full_body_1.png`: @256 ×3 unseeded LSE-D 6.148/6.241/6.255 (**σ 0.058** → P5/P6 win floor stays **0.5**), @512 6.333. All < 8 PASS bar; @512 buys no sync, only sharpness.
- App venv untouched: `pip check` clean, fast suite 28 passed (edge-tts installed to scratchpad via `--target`, never into a project venv).
- Pilot verdict: **BLOCKED-ON-ASSETS** — awaiting real MC portrait + VN clip (10-30s). Protocol doc section 6 has the run-book for when they arrive; phoneme spot-check (step 6) executes then.

## Risk Assessment

- syncnet_python is old research code (2017-era deps) → isolated venv + CPU fallback; Wav2Lip eval as plan B (both from `researcher-260702-2323-alpha-export-long-audio-vn-syncnet-report.md` Topic C).
- SyncNet trained on English (VoxCeleb) → treat LSE-D as RELATIVE metric (compare engines/settings on same clip), phoneme checklist covers VN-specific absolute judgment.
- Real assets late → do not block phases 2-4; only the pilot verdict + bake-off wait on assets.
