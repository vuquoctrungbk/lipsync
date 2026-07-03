---
phase: 6
title: "MuseTalk Bake-off"
status: blocked  # blocked-on-assets 2026-07-03: bake-off test set = real MC clip + portrait (plan dependency graph)
effort: "2d (hard timebox)"
priority: P2
dependencies: [1]
---

# Phase 6: MuseTalk Bake-off

## Overview

Empirical bake-off: MuseTalk 1.5 vs SadTalker baseline (vs +LatentSync if phase 5 PASSed) on the SAME VN portrait + audio, judged by phase-1 harness + phoneme checklist + style fit. MuseTalk = MIT license, ~4GB fp16, fast, Whisper audio (VN-capable) — but mouth-only animation (static head, no blinks) and documented Windows mmcv dependency hell (v1 report: `plans/reports/researcher-260605-1205-musetalk-adversarial-verification-report.md`). Winner-by-numbers becomes optional engine 2 in a follow-up; loser is dropped with evidence.

## Requirements

- Same test inputs across engines (real MC portrait + VN clip from phase 1).
- Metrics: LSE-D/LSE-C, phoneme checklist score, wall-clock, VRAM peak, subjective naturalness note (static head vs SadTalker's motion/blinks — style question for a presenter/MC use case).
- App untouched until a winner is declared; integration is a follow-up phase.

## Architecture

Isolation identical to phase 5: `tools/musetalk/` own venv (mmcv/mmdet/mmpose chain must not touch app env; also `src`-package collision risk). Record + check out a specific MuseTalk commit before the venv; prefer safetensors weights, record HF revisions (venv isolates dependencies, not trust). Install ladder with hard timeboxes:
1. Native pip in isolated venv following repo README (2h) — v1 report predicts mmcv wheel deadlock on Windows;
2. `mim install` pre-built wheels / community wheels (1h);
3. ComfyUI portable + MuseTalk custom node (2h);
4. All fail → verdict FAIL-ENV, document exact errors, close track.

## Related Code Files

- Create: `plans/reports/bakeoff-260703-musetalk-vs-sadtalker-vn-report.md`; sample outputs under `outputs/bakeoff/`
- Modify: none

## Implementation Steps

1. Install per ladder above; record the exact working recipe (wheel versions) or the failure wall.
2. Generate: MuseTalk on (portrait, VN wav) at default settings; note it animates mouth region only onto the static image.
3. Score all contenders on the same clip: SEEDED SadTalker baseline (phase 1), MuseTalk, +LatentSync variant if available. Win margins are judged against the phase-1 spread — a difference below max(0.5, 2σ) is noise, not a verdict. Table: LSE-D, LSE-C, phoneme /5, sec-compute-per-sec-video, VRAM peak.
4. Style check (cannot be automated): does a static head with moving lips read as acceptable for the user's MC/newsreader format? Produce side-by-side clips in `outputs/bakeoff/` for the user to eyeball — user makes the style call.
5. Verdict in report: ADOPT (wins lip metrics AND user accepts style → follow-up phase: `animation_musetalk.py` subprocess engine + UI engine dropdown) / REJECT (numbers or style lose → dropped, evidence retained).

## Success Criteria

- [ ] Working install recipe documented OR failure wall documented (timeboxes respected)
- [ ] Metric table complete on identical inputs; sample videos delivered for user style call
- [ ] Verdict recorded; app unchanged; follow-up phase proposed only on ADOPT

## Risk Assessment

- mmcv Windows deadlock is the known killer (60-70% native fail per v1 report) → ladder + timeboxes make it a bounded cost, not a tarpit.
- MuseTalk face-crop at 256 may soften the mouth region vs source resolution → include a 512-source variant if trivial; note in report.
- Static-head style may be unacceptable regardless of metrics → user decides on real side-by-sides (step 4); do not pre-judge.
