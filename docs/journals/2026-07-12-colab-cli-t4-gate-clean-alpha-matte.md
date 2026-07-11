# Colab CLI T4 Gate + Clean-Alpha Matte (6→7/10)

**Date**: 2026-07-12 01:50
**Severity**: High value (gate PASSED on numbers; user 7/10; next bottleneck identified = body motion)
**Component**: Colab CLI automation (WSL), tools/colab/colab_render_job.py, lipsync/green_compositor.py (alpha_source), scripts/matte_video.py (--alpha-from)
**Status**: Shipped on `feat/colab-render-offload`; body-motion research done, direction pending user

## What Happened

Ran the phase-2 T4 gate **fully automated through the official Google Colab CLI** (June 2026 release) instead of the hand-run notebook: installed Ubuntu-24.04 WSL beside docker-desktop, pipx-installed `google-colab-cli`, held the OAuth flow open on a FIFO for 11 hours until the user pasted the code, then drove `new/upload/exec/download/stop` end to end. Results: **render 17 min for a 20.4 s clip on free T4** (vs 157 min for local 512 — 9.2×), **LSE-D 8.221 ≈ the 512 baseline's 8.20**. The free tier granted two consecutive T4 VMs in one evening.

The user then graded quality: 6/10 with background flicker → diagnosed as matte-layer noise (LatentSync's writer re-encodes at ~430 kbps; RVM's recurrence propagates that noise) → built four matte variants; the winner computes **alpha from the pre-refine Ditto clip** (identical background motion, one less lossy encode) applied to the final RGB. User: **7/10**. Productized as `composite(alpha_source=...)` + `matte_video.py --alpha-from` with fail-loud alignment (fps check, codec-padding trim ≤16 frames, hard error beyond).

## The Brutal Truth

Three env pins that "worked locally" each detonated on the real target: onnxruntime-gpu 1.27 links `libcudart.so.13` (Colab image is CUDA 12.8); the Jupyter kernel leaks `MPLBACKEND=module://matplotlib_inline...` into subprocesses whose venvs lack that package; and LatentSync's final video write buffers every frame at source resolution — 1402 px frames OOM-killed the 12.7 GB-RAM VM **after** 12 minutes of successful diffusion, and the OOM took the whole VM (and every artifact on it) down with it. We had not downloaded `ditto_raw.mp4` yet. Everything re-ran from zero.

Also real: the first VM's venv build took 35 minutes; the second took 79 seconds. Colab free is a lottery on I/O, not just on GPUs — never benchmark setup time from one sample.

The scratchpad experiment that produced the winning video used `zip()` and silently ate 2 frames; the productized fail-loud path caught that the "same" clips differ by LatentSync's pad-to-16 — the exact class of silent desync the reviewer had flagged (MD1) an hour earlier.

## Lessons Learned

1. **Pin to the TARGET's CUDA line, not the dev box's** — check `/usr/local/cuda*` before choosing wheel lines.
2. **Download artifacts the moment they exist** on preemptible VMs; stage the pipeline so every stage's output is fetched before the next stage risks the machine.
3. **Long verbose subprocess output must go to a VM-side file**: raw-fd streams are invisible to the CLI, and captured-then-printed streams stall the exec websocket into client TimeoutErrors (kernel work survives — the error is cosmetic, twice confirmed).
4. **Alpha from a cleaner sibling encode** is a legitimate matte upgrade whenever a refine pass recompresses: background motion is identical by construction. Now a first-class flag.
5. Generators wrapping imageio readers must be `.close()`d explicitly (zip leftovers, islice wrappers) — interpreter-exit GC teardown throws WinError 6 on Windows.
6. The keep-alive daemon + kernel-state persistence made incident recovery cheap: the ORT/MPLBACKEND fixes were applied to a LIVE session without redoing 50 minutes of setup.

## Next Steps

- User's 10/10 requires **body motion** (torso/shoulders rigid — architectural limit of head-only animators). Research ladder delivered: EchoMimicV3 (Apache-2.0, semi-body, re-eligible now that Colab exists) → real-video template + face swap → commercial APIs. Direction pending user.
- Step 2 of the Colab ladder when requested: CLI bridge into the Gradio app (subprocess pattern proven by tts_bridge).
