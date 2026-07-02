---
phase: 7
title: "UX Polish & Regression"
status: pending
effort: "1d"
priority: P3
dependencies: [2, 3, 4]
---

# Phase 7: UX Polish & Regression

## Overview

Close v2: quality presets, surfaced warnings, docs refresh, license notices, full regression on the target box, and v1 plan closure. Folds in phase 5/6 verdicts (documentation only — any engine integration is a separate follow-up plan).

## Requirements

- Functional: preset dropdown — Draft (face 256, enhancer off) [default] / High (face 512, enhancer on) / Custom (no-op); presets only pre-fill EXISTING controls (`app.py:80-87`), user can still override. No crf tier — crf is not a UI control and does not change render speed (animate + matte dominate wall-clock).
- Functional: `PipelineResult.warnings` (phase 2) + capability notices (phase 3) visible in the status panel.
- Non-functional: docs within `docs.maxLoc` 800/file; NOTICE.md reflects GPL/non-commercial components and the `commercial_safe` flag.

## Related Code Files

- Modify: `app.py` (preset dropdown maps onto controls before `RenderConfig` build at `app.py:43`; warnings panel; matting-engine dropdown + `commercial_safe` checkbox — makes phase 2's one-click fallback real; "Analyze sync drift" button running `scripts/sync_metrics.py --window 60` on a selected output — opt-in per phase 4, never automatic), `README.md` (settings table: matting engine, output format, presets, 600s cap, resume; perf table refresh), `docs/system-architecture.md` (MattingEngine, chunked facerender + manifest, WebM sink, harness), `docs/usage-guide.md` (CapCut WebM import steps, resume how-to, validation how-to), `NOTICE.md` (RVM GPL-3 personal-use note + commercial_safe fallback; syncnet/LatentSync/MuseTalk tool-only status), `plans/260605-0045-local-vietnamese-lipsync-greenscreen-app/plan.md` (close Phase-8 gate: link phase-1 verdict; mark plan completed if pilot PASSed)
- Create: none

## Implementation Steps

1. Preset dropdown + warnings panel + matting-engine dropdown/`commercial_safe` checkbox + "Analyze sync drift" button in `app.py`.
2. Docs sweep (README, system-architecture, usage-guide, NOTICE) — verify every claim against the shipped behavior (dates, timings from phase 2/4 measurements, exact formats).
3. Full regression on the RTX 3060 box: `pytest -m "not slow"`; then `RUN_E2E=1` suite (green, webm, 140s chunked, resume); record the new perf table (256/512, both engines) into README.
4. v1 plan closure per phase-1 verdict; journal entry via `/ck:journal`.
5. Final commit(s) — conventional commits, no AI references (repo rule).

## Success Criteria

- [ ] Presets behave as labeled; overrides respected
- [ ] Warnings/capability notices visible in UI status
- [ ] All docs updated + accurate; NOTICE.md lists new components with correct license status
- [ ] Full test suite green on target hardware; perf table recorded
- [ ] v1 plan gate closed with linked evidence

## Risk Assessment

- Doc drift (claims vs measured reality) → step 2 verifies against phase 2/4 logs, not memory.
- Preset defaults fighting user overrides in Gradio event wiring → presets set component values only on dropdown change, never on render click.
