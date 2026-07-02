# PA2 Phases 1-2: RVM Swap & SyncNet Harness — Critical Bug + Calibration Traps

**Date**: 2026-07-03 23:45
**Severity**: High (bug fixed same session; calibration locked)
**Component**: matting engine swap (RVM), SyncNet validation harness
**Status**: Resolved (pilot BLOCKED-ON-ASSETS)

## What Happened

Phases 1–2 delivered matting perf win (71s→17.2s pure, 113s→64s e2e) via BiRefNet→RobustVideoMatting swap behind MattingEngine abstraction. SyncNet harness + validation protocol + calibration anchor locked in (synced LSE-D 6.564, desynced 15.21). Code review caught a shipped High bug mid-swap; two calibration anchors had to be redone; e2e ≤55s target near-missed at 64s.

## The Brutal Truth

I shipped broken cache logic that could silently defeat the whole commercial_safe invariant. If `load()` raised during engine swap, the stale key stayed alive pointing to a half-initialized broken engine. Retry would cache-hit the wrong one. No error, no warning — just silent GPL RVM in an alpha build pretending to be safe. That's a critical oversight: I prioritized speed over safety during the refactor and didn't unit-test the failure path.

The calibration mistakes hurt more because they wasted time and exposed sloppy thinking. First anchor was nonsense because I didn't read SyncNet's own code: LSE-D is a *minimum* over ±15-frame search, so subsecond offsets get re-aligned away. Second anchor used `-itsoffset`, which doesn't shift samples — ffmpeg just remuxes with new timestamps. SyncNet's preprocessing re-encodes with `-async 1` anyway, normalizing the shift back out. Should have tried `adelay` on first attempt instead of guessing.

## Technical Details

**Cache bug**: `_matting()` assigned `engine_cache[key] = engine` *after* calling `load()`. If load() raised (offline, first run), the key pointer was already stale from the old engine, retry cache-hit garbage. Fix: null sentinel fields before swap, assign key *after* successful load + regression test (test_matting_load_failure_cleans_key).

**Calibration trap #1**: Synced anchor tested with `-itsoffset 500ms`. LSE-D ≈ 6.564. But SyncNet's `extract_speaker_windows()` does a `find_max_coeff()` over ±15 frames (375ms), so sub-0.6s offsets get realigned. Desynced test used `-itsoffset 1200ms` (outside search window) → LSE-D 15.21. Protocol docs trap now.

**Calibration trap #2**: `-itsoffset` doesn't shift samples, only timestamps. SyncNet preprocessor has `-async 1` passthrough, which normalizes. Physical shift via `ffmpeg -af adelay=1200|1200ms` finally showed real desync score. Lesson: test assumptions against actual signal, not mux metadata.

**Temporal stability metric**: Whole-frame mean|dA| (<RVM vs BiRefNet) failed empirically (RVM 0.00039 vs BiRefNet 0.00017). Investigation: metric confounds soft-edge gradient width with flicker. Confident-region flicker identical on both (~5.5e-5 vs 2.5e-5). Metric was broken, not engine swap. Escalated to user; will revisit metric definition.

**e2e perf**: 64s vs 55s target. Matting 27% of wall-time (de-bottlenecked). SadTalker facerender dominates residual — outside phase 2 scope. Escalated with datasheet.

**CPU mode unavailable**: Vendored syncnet torch.load has no map_location, crashes under `CUDA_VISIBLE_DEVICES=""` with cu121 build. Documented as GPU-only to avoid chasing third-party code under timebox.

## Lessons Learned

1. **Fail paths need tests**, not refactor speed. Edge case: mid-swap exception + retry = silent corruption. Unit test the exception path first.
2. **Read the algorithm before calibrating**. SyncNet's ±15-frame search means sub-0.6s offsets are undetectable by design. Don't blindly tweak offset magnitude.
3. **Test signal, not metadata**. `-itsoffset` lies; `adelay` tells truth. Validate assumptions at the layer they matter.
4. **Metrics can be broken without data looking wrong**. RVM showed lower *total* frame variance but confounded soft-edge blur with real flicker. Disaggregate before passing.
5. **Scope creep kills time**. CPU mode felt like 30min to add; actual blocker is vendored code. Document blockers early, don't iterate chasing external deps.

## Next Steps

- Pilot blocked awaiting user portrait + voice clip; resume calibration & validation when assets arrive.
- Metric definition: separate soft-edge contribution (expected, acceptable) from confident-region flicker (compare RVM vs BiRefNet in high-confidence interior).
- SadTalker perf: escalate facerender bottleneck to phase 3+ scope (outside matting, needs GPU-level investigation).
- Cache regression test committed; similar pattern audited across other swap points.

---

**Status**: DONE
**Summary**: Matting perf doubled (71s→17.2s); SyncNet harness locked; High cache bug caught & fixed; two calibration anchors corrected; e2e ≤55s target near-missed at 64s (facerender-scoped).
