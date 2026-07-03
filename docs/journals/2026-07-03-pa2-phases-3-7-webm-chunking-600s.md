# PA2 Phases 3–7: WebM Compositing, 600s Chunking, and Presets — Pipe Buffers, Alpha Assertions, and Silent Failures

**Date**: 2026-07-03 10:00
**Severity**: High (safety + performance critical)
**Component**: Compositing driver, chunking pipeline, UI/presets
**Status**: Resolved

## What Happened

Shipped Phase 3 (WebM alpha via VP9 yuva420p + Opus), Phase 4 (600s chunking gate + halo slicing), and Phase 7 (presets, drift UI, docs) across the PA2 v2-refresh. All regression tests green. Two Highs and four Mediums surfaced in code review; all fixed same session.

## The Brutal Truth

I committed avoidable mistakes that almost made 600s chunking unreliable in production. The code review wasn't optional paranoia — it caught two silent failure modes: all-done manifests that silently purged on crash (losing 1.5h of renders), and a race between Resume + Generate that could double-GPU or chdir-race. I also wasted 30min debugging a deadlock I *created* in my own test harness by piping tqdm output and never draining it. The alpha-assertion trap was my misreading the plan's own spec against a correct encoder.

## Technical Details

**Phase 3 wins:** composite() replaced composite_to_green; per-sink fan replaces per-format special-case. Green H.264+AAC goes direct; VP9 yuva420p+Opus pipes raw RGBA to ffmpeg stdin + stderr→FILE (avoids 64KB Windows pipe-buffer deadlock). Per-sink failure isolation proven: kill mid-stream, green survives, WebM logs warning. E2E 78s (both) vs 64s (green-only); v1 E2E unmodified.

**Phase 4 critical:** audio2coeff(600s) = 11.3s/2.45GB (contingency dead, 15000 frames render in 94min = 9.4x RT). Halo slicing ±13 semantic_radius proven window-equivalent. Manifest integrity-bound: coeff SHA1 + fingerprint of render-affecting fields only. E2E variance 0.460/255 mean diff (bar 2/255). Kill/resume reuses done segments (mtime-watched). RAM/VRAM flat (94 samples).

**Failures caught & fixed:** (H1) all-done manifests unresumable + silent purge on compositing crash — adopted all-done-runs pattern + render lock. (H2) Resume + Generate concurrent under gradio 4 per-listener concurrency — shared concurrency_id + chdir lock. Plus 4 Mediums (finalize isolation, own-pid adoption, fingerprint exhaustiveness guard, corrupt-pid guard).

## What We Tried

1. **Test harness deadlock (30min waste):** Launched kill/resume driver with stdout=PIPE, never drained stderr. SadTalker's tqdm filled 64KB buffer, froze everything. Fix: redirected driver output to files. The pipe-buffer rule applies to test harnesses too.
2. **VP9 alpha assertion trap:** Encoder was correct; ffprobe reports base stream yuv420p because alpha rides as container side-data (alpha_mode=1). Only libvpx-vp9 native decoder exports it; ffmpeg's default was opaque. Rewrote assertions to check container metadata, not decoded pixels.
3. **Gate measurement killed at 33min:** Piped long-running output through grep (buffered). Lost data. Reran with direct file logging (lesson: measurements → files, not pipes).
4. **Manifest purge during compositing:** Code path was correct but unprotected—crash in expensive window (1.5h in) discarded all segments. Adopted all-done-runs idiom from render phase.

## Root Cause Analysis

The deadlock and pipe mistakes came from copy-pasting patterns without testing the inverse: I tested that pipes worked for ffmpeg (they do, with redirected stderr), but never tested that test harnesses shouldn't pipe long-running subprocesses. The manifest purge was a scope blindness—I optimized for "fast path: skip done segments" without protecting the all-done transition. The race between Resume + Generate was gradio 4's default per-listener concurrency (not per-session). The VP9 alpha assertion was me trusting ffprobe output over the spec; the plan text was wrong, not the code.

## Lessons Learned

1. **Pipe buffers are universal.** Test harnesses, not just production code, must drain or redirect. Use files for long-running measurements and subprocesses.
2. **Silent failure modes are more dangerous than loud ones.** All-done manifests silently purging is worse than a loud error because you don't know you lost work until the retry fails. Test resumption paths with injected failures.
3. **Assertions must match the actual wire format.** ffprobe != ffmpeg decoder. Verify against what the consumer actually receives, not a tool's text output.
4. **Per-listener concurrency in gradio is per-socket, not per-browser-tab.** Lock shared resources explicitly when multiple handlers touch them.
5. **Code review is not paranoia on safety-critical pipelines.** The Highs caught would have been catastrophic in production (losing renders, double-GPU). Always review resumption logic and concurrency edges.

## Next Steps

- Phase 5–6 (LatentSync/MuseTalk) blocked-on-assets by own dependency graph; real MC clip is test set. Honest state: decision to defer is sound.
- Phase 7 complete: presets, drift UI, docs measured. Final 61 fast + 5 E2E regression all green.
- Monitor Phase 4 compositing in production: any crash logs during all-done transition or Resume+Generate collision (will log chdir race attempts). Increase test coverage on resumption failure injection.

---

**Status**: DONE  
**Summary**: Shipped phases 3–7 (WebM, 600s chunking, presets); two critical concurrency/manifest safety issues caught and fixed in code review; key lesson is that pipe buffers and silent purges kill production reliability.
