---
phase: 5
title: "LatentSync Spike"
status: pending
effort: "1d (hard timebox)"
priority: P2
dependencies: [1]
---

# Phase 5: LatentSync Spike

## Overview

Timeboxed feasibility spike — NOT integration. LatentSync (ByteDance) refines lips on an EXISTING video (needs video input → perfect as pass 2 after SadTalker). Answer four questions with evidence; adoption is a separate follow-up decision. Attacks SadTalker's weakest point (mouth articulation) identified in brainstorm.

## Requirements

Answer with evidence:
1. **License**: read the actual LICENSE file(s) of github.com/bytedance/LatentSync (code + weights on HF) — record exact terms; personal-use OK?
2. **Fit**: does inference run on the RTX 3060 12GB (256 and/or 512 face) — peak VRAM measured, incl. any low-VRAM flags/ComfyUI variant if native OOMs.
3. **Gain**: LSE-D delta on the SEEDED VN baseline video (phase 1 harness, same clip, `cfg.seed` pinned) — target improvement ≥ max(0.5, 2σ) where σ = phase-1 render-to-render spread; plus eyeball check (teeth/blur artifacts — v1.6 claims fixes).
4. **Cost**: wall-clock per second of video on the 3060.

## Architecture

Isolation is mandatory: LatentSync's torch/diffusers stack conflicts with app pins (torch 2.1.2, numpy 1.23.5) AND it likely ships a top-level `src` package colliding with SadTalker's (`animation_sadtalker.py:22-24` sys.path insert). → `tools/latentsync/` own venv, invoked via CLI subprocess only. NEVER import into the app process.

```
tools/latentsync/.venv + repo clone   # gitignored
input: outputs/<sadtalker_baseline>.mp4 + original audio
output: outputs/spike/latentsync_refined.mp4 → scripts/sync_metrics.py both
```

## Related Code Files

- Create: `plans/reports/spike-260703-latentsync-12gb-verdict-report.md` (evidence + verdict)
- Modify: none (app untouched — that is the definition of a spike)

## Implementation Steps

1. Record + check out a specific LatentSync commit BEFORE creating the venv; prefer safetensors weights and record the HF revision (a pickle `.ckpt` is code-execution-on-load; the venv isolates DEPENDENCIES, not trust — this repo's code runs as your user). Pin exact versions in the report. Timebox: if env not running inference within 4h → record blockers, verdict = FAIL-ENV, stop.
2. Run inference on the SEEDED phase-1 VN baseline video (256). Capture peak VRAM (`nvidia-smi --query-gpu=memory.used -lms 500` sidecar log) + wall-clock. If OOM → try 512→256 input downscale / official low-VRAM flags / GGUF-ComfyUI variant; one attempt each, then verdict.
3. Score original vs refined with `scripts/sync_metrics.py`; phoneme spot-check the same 5 transitions; screenshot tooth/blur regions.
4. Write verdict report: PASS = license OK + fits 12GB + LSE-D gain ≥ max(0.5, 2σ) + runtime ≤ ~3× the animate stage. PASS → propose follow-up integration phase (subprocess `RefinerEngine` behind a UI toggle "Refine mouth (slow)"); FAIL → document, close track, app unchanged.

## Success Criteria

- [ ] All four questions answered with numbers/quotes (no "likely")
- [ ] Verdict PASS/FAIL recorded in report + plan.md notes; follow-up phase proposed only on PASS
- [ ] App venv + runtime untouched (`pytest -m "not slow"` still green)

## Risk Assessment

- Timebox discipline is the mitigation — this phase cannot "fail", only return a verdict. Research licenses/VRAM claims for LatentSync were UNVERIFIED (engines report §6) — that is exactly why this spike exists.
- Whisper dependency inside LatentSync handles VN audio features (99-lang) — note behavior on tonal audio in the eyeball check.
