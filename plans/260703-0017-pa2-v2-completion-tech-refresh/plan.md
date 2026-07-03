---
title: "Lip-Sync v2 Completion - PA2 Measured Upgrades"
description: "Approved PA2 scope: LSE-D validation harness + VN pilot, RVM matting swap, WebM VP9 alpha export (CapCut), 600s chunked rendering with resume, gated LatentSync spike, gated MuseTalk bake-off, UX polish."
status: in-progress
priority: P1
branch: "main"
tags: [lipsync, rvm, webm-alpha, chunking, syncnet, latentsync, musetalk, vietnamese]
blockedBy: []
blocks: [260605-0045-local-vietnamese-lipsync-greenscreen-app]
created: "2026-07-02T17:52:35.406Z"
createdBy: "ck:plan"
source: skill
effort: 12d
---

# Lip-Sync v2 Completion - PA2 Measured Upgrades

## Overview

v1 (SadTalker + BiRefNet + ffmpeg + Gradio, commit `3786a12`) works end-to-end but: VN lip-sync unvalidated (v1 Phase 8 gate open), ~20x realtime (BiRefNet 2fps = bottleneck), 120s audio cap, green-only output. User-approved PA2 (2026-07-03): measurement-first upgrades, every engine bet gated by numbers on the actual RTX 3060 12GB.

**Locked decisions:** editor = CapCut → WebM VP9 alpha only (no ProRes). Max clip ≤10 min → cap 600s + checkpoint/resume. Quality-first (~20x RT acceptable). License = personal use (non-commercial/GPL OK) BUT keep commercial-safe fallback paths behind a config flag. Approved scope source: `plans/reports/brainstorm-260702-2334-v2-completion-tech-refresh-report.md`.

**Key architecture insight (explored 2026-07-03, corrected by red-team):** SadTalker's facerender accumulates ALL frames on GPU (`third_party/SadTalker/src/facerender/modules/make_animation.py:137-138`) — plus, under the default `preprocess="full"`, `paste_pic` buffers every frame in RAM at source resolution (`src/utils/paste_pic.py:30-38`) — the true reasons for the 120s cap. `Audio2Coeff` emits one cheap `(num_frames, 70)` `.mat`, so chunking = run audio2coeff ONCE, render facerender in segments (no audio splitting, no crossfades). Frames are NOT fully independent: each frame is conditioned on a ±13-row coeff window (`semantic_radius`, `src/generate_facerender_batch.py:12,93-98`) → segments are sliced with a **±13-frame halo** and halo frames are dropped at consumption; a seeded chunked-vs-full E2E proves equivalence. `still_mode=True` (default) pins pose only (`generate_facerender_batch.py:49-50`); only pose is savgol-smoothed — expression rows are raw, which is exactly why the halo matters.

## Pipeline (target state)

```
image + audio(≤600s)
  -> prepare_audio (16k mono wav; cap 600)
  -> CropAndExtract + Audio2Coeff ONCE -> full coeff .mat           [phase 4]
  -> facerender in halo-overlapped segments (manifest, resume)      [phase 4]
  -> MattingEngine (RVM default | BiRefNet commercial-safe)         [phase 2]
  -> segment-frame iterator (halo frames dropped) -> per-frame
     composite -> green MP4 and/or WebM VP9 yuva420p                [phase 3+4]
  -> on-demand LSE-D drift analysis (opt-in button, never blocking) [phase 1+7]
Gated tracks: LatentSync refine spike [5], MuseTalk bake-off [6] — subprocess-isolated, adopted only on measured wins.
```

## Phases

| Phase | Name | Status |
|-------|------|--------|
| 1 | [Validation Harness & VN Pilot](./phase-01-validation-harness-vn-pilot.md) | ✅ Completed (pilot verdict BLOCKED-ON-ASSETS) |
| 2 | [RVM Matting & Pipeline Hardening](./phase-02-rvm-matting-pipeline-hardening.md) | ✅ Completed (2 criteria flagged, see notes) |
| 3 | [WebM Alpha Export](./phase-03-webm-alpha-export.md) | ✅ Completed (CapCut manual check w/ user pending) |
| 4 | [Long-Audio Chunking & Resume](./phase-04-long-audio-chunking-resume.md) | ✅ Completed |
| 5 | [LatentSync Spike](./phase-05-latentsync-spike.md) | ⏸ Blocked-on-assets (real MC clip = its test set, per dependency graph) |
| 6 | [MuseTalk Bake-off](./phase-06-musetalk-bake-off.md) | ⏸ Blocked-on-assets (same) |
| 7 | [UX Polish & Regression](./phase-07-ux-polish-regression.md) | ✅ Completed (P5/P6 fold-in travels with their asset blocker) |

## Dependency graph

- P1 (harness) and P2 (RVM + hardening) are independent starters.
- P3 (WebM) needs P2 (compositor refactor). P4 (chunking/resume) needs P2 (run-id dirs, unique temp names, `cfg.seed`); its on-demand drift analysis reuses P1's harness (UI button wired in P7).
- P5, P6 need P1 (judged by harness); both are timeboxed gates, not commitments. P6 comparison is richer after P5's verdict.
- P7 needs P2+P3+P4 (docs/regression cover them); folds in P5/P6 verdicts.
- External dependency: **user-supplied real VN voice clip (10-30s) + MC portrait** — required for P1 pilot verdict and P5/P6 test set. Harness bring-up may use any VN audio; the PILOT verdict must use the real assets.

## Effort

P1 1.5d, P2 2d, P3 1d, P4 4d, P5 1d (timebox), P6 2d (timebox), P7 1d ≈ **12.5d**.

## Key risks (detail per phase)

- syncnet_python Windows install pain → isolated venv, timebox, Wav2Lip-eval fallback (P1).
- RVM recurrent-state VRAM growth on 600s clips → verify flat during P2; GPL isolated behind MattingEngine + `commercial_safe` flag.
- imageio-ffmpeg fallback binary may lack libvpx-vp9 AND ffprobe → startup capability checks; audio cap enforced from the decoded wav, fail-closed (P3, P4).
- audio2coeff full-length memory at 600s + paste_pic/enhancer RAM at source resolution unverified → measured at the P4 step-1 hard gate; chunk sizing by VRAM and RAM.
- Segment boundary artifacts from the ±13-frame semantic window → halo slicing + seeded chunked-vs-full equivalence E2E (P4).
- SadTalker's vendor mux slices audio [0:seg] from t=0 via pydub (full-file decode per call), `os.system` with ignored exit codes, CWD-relative uuid temps → segments render with a 1s silent wav (mux lacks `-shortest`), chdir guard, fail-loud post-checks (P4).
- audio2coeff is non-deterministic (unseeded CVAE + blink) → manifest binds segments to one coeff mat (sha1+rows); `cfg.seed` for comparisons (P2, P4).
- torch.hub RVM: unpinned ref executes remote HEAD code and `trust_repo` prompt crashes non-interactive first run → pinned commit + trust_repo=True + scoped hub dir (P2).
- LatentSync/MuseTalk torch/dep conflicts + `src` package collision with SadTalker → NEVER import into app process; subprocess + separate venvs only (P5, P6).
- VN accuracy may fail on all engines → harness gives evidence; escalate to user with data, never silent-swap (P1 protocol).

## Red Team Review

### Session — 2026-07-03
**Reviewers:** 4 hostile lenses (Security Adversary, Failure Mode Analyst, Assumption Destroyer, Scope & Complexity Critic), verification tier Full — 54+ plan claims grep/trace-verified against the codebase; 2 load-bearing claims refuted, both in phase 4.
**Findings:** 15 consolidated (15 accepted — 2 with modification, 0 rejected; none failed the evidence filter)
**Severity breakdown:** 1 Critical, 8 High, 6 Medium

| # | Finding | Severity | Disposition | Applied To |
|---|---------|----------|-------------|------------|
| 1 | `semantic_radius=13`: frames conditioned on ±13-row coeff window → naive slicing stutters at every boundary; fix = halo slicing + drop-at-consumption + seeded chunked-vs-full E2E; "savgol full sequence" corrected (pose only) | Critical | Accept | Phase 4, plan.md |
| 2 | `paste_pic`/enhancer buffer whole segment in RAM at SOURCE resolution; CWD uuid temps; `os.system` exit codes ignored → RAM-aware sizing, chdir guard, fail-loud post-checks, step-1 measurement incl. full-preprocess variant | High | Accept | Phase 4 |
| 3 | torch.hub unpinned (executes remote HEAD) + trust prompt raises EOFError non-interactive → pinned SHA + `trust_repo=True` + scoped hub dir + checkpoint SHA-256 | High | Accept | Phase 2 |
| 4 | Matting engine cache had no invalidation → flipping `commercial_safe` mid-session silently kept GPL RVM; fix = keyed cache + unload-on-switch + live-flip test | High | Accept | Phase 2 |
| 5 | `reset_clip()` had two contradictory owners; RVM state survives crashes → single owner (composite driver) + `finally` clear + cross-resolution test | High | Accept | Phase 2, 3 |
| 6 | Auto drift report would block renders for CPU-hours on a harness sized for 30s clips → opt-in UI button, relative trend, no absolute 8.5 gate (capability KEPT — it is user-approved scope; only the execution model changed) | High | Accept (modified) | Phase 4, 7, 1 |
| 7 | WebM stdin pipe deadlocks on undrained stderr; BrokenPipe would kill ALL sinks in `both` mode → stderr to file, wrapped writes, per-sink failure isolation | High | Accept | Phase 3 |
| 8 | audio2coeff is non-deterministic (unseeded CVAE + blink) → resume could stitch segments from two different coeff runs; fix = manifest binds coeff sha1+rows, atomic writes, full demotion on mismatch, new-run-id rule | High | Accept | Phase 4 |
| 9 | `cfg_fingerprint` undefined, manifest unowned, keep-3 retention hoards disk → enumerated render-affecting fields, `owner_pid`, keep-1 per input-hash, stage derived from disk | Medium | Accept | Phase 4 |
| 10 | 600s cap fails OPEN when ffprobe absent (imageio-ffmpeg ships none) → fail-closed cap from the decoded wav; `has_ffprobe()`; fail-loud resume validation | Medium | Accept | Phase 4, 2 |
| 11 | Measurement-first plan measured a stochastic engine with n=1 → 3× baseline spread σ, `cfg.seed` for comparisons, win-thresholds max(0.5, 2σ) | High | Accept | Phase 1, 2, 5, 6 |
| 12 | `run()` return/warnings contract unspecified at its 2 real consumers; dead compat wrapper; stale `BiRefNetMatte` type hint → additive contract fixed, wrapper replaced outright, param retyped | Medium | Accept | Phase 3, 2 |
| 13 | Composite stage not checkpointable + disk-full = unbounded redo loop → disk re-check at composite entry, temp+rename sinks, UI notice | Medium | Accept | Phase 4, 3 |
| 14 | Draft preset differed only by crf (not a UI control, no speed effect); "one-click fallback" unwired; duck-type protocol test is a phantom → presets on real knobs, engine dropdown + checkbox wired in P7, behavior tests | Medium | Accept (modified: `matting_base.py` kept as a tiny module per repo modularization convention) | Phase 7, 2 |
| 15 | Unpinned research-repo clones (P1/5/6); portrait filename taint into vendored `os.system`; `sync_metrics` ffmpeg-resolver duplication; 3 factual text errors (audio-slice claim, "mels in facerender data", imageio-yuva420p impossibility) | Medium | Accept | Phase 1, 2, 4, 5, 6 |

### Whole-Plan Consistency Sweep
- Files reread: plan.md + all 7 phase files (post-edit state)
- Decision deltas checked: 15 (halo slicing, no-concat iterator, silent-wav segment mux, keyed matting cache, single reset owner, opt-in drift, per-sink isolation, coeff sha binding, fingerprint enumeration, fail-closed cap, σ-aware thresholds + seed, additive run() contract, composite disk re-check, real-knob presets + UI wiring, pinning/sanitization/DRY/text corrections)
- Reconciled stale references: pipeline diagram, key-insight paragraph, risk list, dependency graph, effort (P4 3.5d→4d, total 12.5d), phase 1 drift wording, phase 5/6 thresholds
- Unresolved contradictions: 0

## Validation Log

### Session 1 — 2026-07-03
**Trigger:** post-red-team validate, user-selected at plan handoff.
**Questions asked:** 4 (recap presented first). **ANSWERS PENDING** — user went away mid-interview. None of the four blocks implementation; each has a no-answer default already encoded in the plan.

#### Questions & Proposed Answers (pending user)
1. **[Assumptions]** Default UI output format on Generate? Options: WebM alpha (Recommended — CapCut deliverable) | Both | Green MP4 (current). *No-answer default:* UI radio starts at `green_mp4` (matches config default; existing tests untouched).
2. **[Scope]** Timing for engine spikes P5/P6? Options: Gate on pilot verdict (Recommended — skip ~3d if pilot satisfies) | Run regardless. *No-answer default:* run per plan order (after P1).
3. **[Risks]** Real MC portrait + VN voice clip delivery? Options: at start | send later — start with P2 (Recommended) | sample/TTS for the pilot (not recommended). *No-answer default:* harness bring-up on substitute audio; pilot verdict BLOCKED-ON-ASSETS until real files arrive.
4. **[Tradeoffs]** Escalation order if the VN pilot underwhelms? Options: cheap→expensive: tune 512/enhancer → LatentSync spike → MuseTalk bake-off (Recommended) | straight to engine spikes | ask user at each step. *No-answer default:* phase-01 protocol — escalate to user with evidence (equivalent to "ask each step").

#### Confirmed Decisions
- None this session (pending).

#### Action Items
- [ ] User answers the 4 questions (or explicitly accepts the defaults) → complete this log + propagate any non-default answer to phases 1/3/5/6
- [ ] User supplies the real VN clip (10–30s, production MC voice) + MC portrait → unblocks P1 pilot verdict and the P5/P6 test set

#### Impact on Phases
- None yet — all four defaults are already the plan text.

### Session 2 — 2026-07-03 (cook start)
**Trigger:** `/cook plan.md` — the 4 pending questions were re-asked at implementation start; user away (60s timeout). Per plan protocol, proceeding on the encoded no-answer defaults:
1. Output format → UI starts at `green_mp4` (WebM alpha selectable). 2. P5/P6 → run per plan order after P1. 3. Assets → harness bring-up on substitute audio; **pilot verdict BLOCKED-ON-ASSETS**. 4. Escalation → present evidence, user decides each step.
Execution order chosen: P2 → P1 (both are plan-sanctioned independent starters; P2 needs no external assets and unblocks P3/P4). RVM hub pin resolved at implementation time: `PeterL1n/RobustVideoMatting@53d74c6826735f01f4406b5ca9075eee27bec094` (master HEAD 2026-07-03).
Action items from Session 1 remain open (user answers can still override defaults; real VN assets still awaited).

**Session 2 addendum (post P1+P2 finalize):** user returned and decided: (1) COMMIT phases 1-2; (2) e2e 64s vs ≤55s target ACCEPTED as phase-complete (matting de-bottlenecked 71→17.2s; residual is animate stage); (3) CONTINUE into P3 then P4 in this session. Phase-02 criterion checkbox updated accordingly.

**Session 2 close-out:** P3, P4, P7 completed same session (all criteria evidence in the phase files; P3/P4 review DONE_WITH_CONCERNS → 2 High + 4 Medium fixed same session). P5/P6 marked **blocked-on-assets** — the dependency graph makes the real MC clip + portrait their test set, so running them on TTS would produce non-decision-grade numbers. Remaining plan state: P5/P6 + pilot verdict + CapCut one-time manual check, ALL gated on the user's real assets (+ the 4 Session-1 answers, defaults in effect).

### Verification Results
- Covered by `## Red Team Review` (tier Full, 54+ claims grep/trace-verified, Failed: 0 after fixes). Validate step 2.5 guard applied; no `[UNVERIFIED]` tags remain.

### Whole-Plan Consistency Sweep
- No plan changes this session → state unchanged from the Red Team Review sweep (0 unresolved contradictions; stale-term grep scan clean across all 8 files).

## References

- Brainstorm (approved scope): `plans/reports/brainstorm-260702-2334-v2-completion-tech-refresh-report.md`
- Engines research: `plans/reports/researcher-260702-2323-talking-head-engines-2026-report.md` (NOTE: its HunyuanVideo/diffusion speed+license claims were cross-checked and REJECTED; MuseTalk/LatentSync entries usable)
- Matting research: `plans/reports/researcher-260702-2323-matting-speed-and-greenscreen-shortcuts-report.md`
- Alpha/long-audio/SyncNet research: `plans/reports/researcher-260702-2323-alpha-export-long-audio-vn-syncnet-report.md`
- MuseTalk v1 adversarial verification (Windows mmcv risk): `plans/reports/researcher-260605-1205-musetalk-adversarial-verification-report.md`
- v1 plan (its Phase-8 pilot gate is absorbed by P1 here): `plans/260605-0045-local-vietnamese-lipsync-greenscreen-app/plan.md`
