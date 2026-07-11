# Colab T4 Offload Notebook + Hybrid PASS Verdict

**Date**: 2026-07-11 01:20
**Severity**: Medium (no incident; strategic direction locked + shipping the cloud path for a VRAM-bound render)
**Component**: tools/colab/lipsync_render.ipynb (new), scripts/matte_video.py (new), animation-engine saga (v4)
**Status**: Phase 1 shipped on `feat/colab-render-offload`; Phase 2 = user gate on real T4 run

## What Happened

User verdict unblocked everything: **hybrid Ditto + LatentSync PASSED on quality** (`lipsync_green_hybrid-green-final.mp4`) — after JoyVASA failed twice and SadTalker's stiffness proved architectural, the remaining problem is purely speed (LatentSync-512 = 157 min for 20.4 s on the 3060; the model wants ~18 GB VRAM, 12 GB thrashes). PR #6 (TTS GPU) merged; new branch cut from main.

Shipped step 1 of the Colab ladder from the 07-10 research: a versioned notebook running **Ditto + LatentSync-256 on free T4** with Google Drive job handoff (`lipsync-jobs/in|out/<job>/`), mirroring the proven local 2-venv isolation (torch 2.3.1 vs 2.5.1 stacks) via `uv` with pinned repo commits (`c3e47ee`/`a229c39`) and HF checkpoints (`digital-avatar/ditto-talkinghead` PyTorch path — T4 is Turing, the prebuilt TRT is Ampere_Plus; `ByteDance/LatentSync-1.5` = the 256 model). Plus `scripts/matte_video.py`: a CLI that feeds ANY external clip through the app's existing streaming matte → green/WebM pipeline, replacing the inline snippet hand-run twice during the spike. 13 new tests; suite 95 passed/10 skipped; a real RUN_E2E matte smoke (161 s) produced a green MP4 from the spike hybrid clip.

## The Brutal Truth

The code-reviewer caught a plan-premise error that would have burned the user's first Colab session: the plan said "pin Python 3.10 (per ditto environment.yaml)" but every locally proven venv is actually **3.11.9** — the exact wheel set (mediapipe 0.10.35, ORT-gpu 1.27) was never resolved under cp310. Pinning to an interpreter nothing was proven on, to *avoid* version risk, is the kind of confident wrongness that only gets caught by checking the actual artifact (`python --version` in the venvs) instead of the docs. Same review: my venv idempotency guard keyed on the python binary existing — but `uv venv` creates that binary *before* any package installs, so a mid-install network failure would permanently brick the cell's re-run ("idempotent" setup that isn't). And the per-job error isolation only caught `CalledProcessError`, so one malformed `params.json` would have killed the whole batch loop. All three were cheap fixes; none would have been cheap to debug remotely over a Drive handoff with a user reading tqdm logs.

## Technical Details

- **LatentSync 1.5/1.6 share one codebase** (HF README confirms): switching 512→256 is just ckpt + `resolution` in `configs/unet/stage2.yaml`. The 256 run quality is still the open bet — it was never tried locally.
- **Notebook is static-tested only** (it can only execute on Colab): JSON validity, every code cell compiles, spike-proven identifiers asserted (commits, HF repos, stage2.yaml), no committed outputs. Runtime proof is exactly what Phase 2 measures.
- `matte_video.py` defaults `--audio` to the input video itself — `_GreenSink`/`_WebmSink` mux via ffmpeg `-map 1:a:0`, which happily takes an mp4's audio stream.
- Torch-free import discipline test: initial version asserted on `sys.modules` in-process and failed under the full suite (other tests import torch first). Rewritten as a clean-subprocess probe — module-level lightness is a property of the module, not of whichever process happens to host the test.

## Lessons Learned

1. **Verify the interpreter/wheel matrix against the artifact, not the plan text.** `python --version` in the proven venv beats any environment.yaml claim.
2. **"Idempotent" needs a completion sentinel, not an existence check** — anything created *before* the last step of setup is a lying sentinel.
3. **Batch runners get `except Exception` at the loop**, with per-item error files; typed exceptions are for the messages, not the isolation.
4. Reviewer time was well spent on the one artifact that can't be executed locally — static review substitutes for the missing test tier.

## Next Steps

- **Phase 2 (user, ~30–45 min)**: open the notebook on T4, run the standard job (MC image + the 20.4 s TTS wav), download `hybrid_256.mp4` + `timings.json`, matte locally, score LSE-D. Gate criteria in `plans/260711-0040-colab-t4-hybrid-render-offload/phase-02-gate-t4-run-quality-verdict.md` (256 quality bar vs 512's 8.20; total ≤ ~30 min/20 s).
- If 256 passes → step 2: export/import job buttons in the Gradio app. If it fails on quality → Colab Pro L4 + 512 (the notebook documents the two-line switch).
- Still wanted: a real 10–30 s MC voice clip (TTS-flat-prosody hypothesis remains untested).
