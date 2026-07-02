# v2 Plan Red-Team: Four Independent Reviewers Kill a "Zero-Seam" Chunking Design Before Any Code Existed

**Date**: 2026-07-03 ~02:00
**Severity**: High (design flaw caught pre-implementation; zero code damage)
**Component**: v2 planning — long-audio chunking (SadTalker facerender), matting swap, alpha export
**Status**: Plan hardened + validated; awaiting 4 user answers + pilot assets

## What Happened

One session took the app from "v1 works, what next?" to a red-team-hardened 7-phase v2 plan (`plans/260703-0017-pa2-v2-completion-tech-refresh/`): brainstorm (3 research agents, 2025-26 engine landscape) → user approved PA2 + CapCut/WebM + ≤10 min clips → plan (7 phases, ck CLI scaffold) → 4-lens adversarial red team → 15/15 findings applied → validate interview opened (4 questions pending, user AFK).

## The Brutal Truth

The plan's cornerstone claim — "facerender frames are independent given the source; slice the coeff .mat anywhere, zero seams by construction" — was **wrong**, and I wrote it after a code exploration that "verified" it. The Explore pass checked that the render LOOP was stateless (`make_animation.py:113-137`, true) but not the loop's INPUT: `get_facerender_data` conditions every frame on a ±13-row coeff window (`semantic_radius = 13`, `generate_facerender_batch.py:12`), edge-clamped at mat boundaries (`:93-98`). Naive slicing = expression stutter every 50s, and my planned unit test ("exact cover, no gap/overlap") would have ENSHRINED the bug. All four hostile reviewers found it independently. Fix is cheap at plan-time: ±13-frame halo slices, drop halo frames at consumption (~2% overhead). Mid-implementation it would have been a redesign of slicing, tests, and the concat design.

Second humiliation: the researcher agent's #1 engine recommendation (HunyuanVideo-Avatar, "10GB, 2–4x faster, Apache") failed cross-checking — datacenter-GPU extrapolations + guessed license. Research narrowed candidates; only local benchmarks decide. That rule is now written into the plan.

## Technical Details (what the red team changed)

- **Halo chunking** replaces naive slicing; seeded chunked-vs-full E2E (<2/255 mean abs diff) is the guarantee; pixel seam metric demoted to advisory (it averages away localized mouth pops).
- audio2coeff is **non-deterministic** (unseeded CVAE `audio2pose.py:68-77` + blink `generate_batch.py:37-49`) → manifest binds segments to ONE mat by sha1+rows; `cfg.seed` added for comparisons; measurement plan now records 3× baseline spread σ and gates engine verdicts at max(0.5, 2σ) — n=1 measurement of a stochastic system is a coin flip.
- Default `preprocess="full"` runs `paste_pic`: whole-segment RAM buffering at SOURCE resolution + CWD uuid temps + `os.system` mux with IGNORED exit codes (`videoio.py:20-26`) → RAM-aware chunk sizing, chdir guard, fail-loud post-checks.
- 600s cap failed OPEN without ffprobe (imageio-ffmpeg ships none) → cap now derived from the decoded 16k wav.
- Matting-engine cache had no invalidation → flipping `commercial_safe` mid-session would silently keep GPL RVM loaded. Keyed cache + unload-on-switch + live-flip test.
- WebM stdin pipe: undrained stderr = deadlock at ~frame 9k; BrokenPipe would kill the green sink too in `both` mode → stderr-to-file + per-sink failure isolation.
- torch.hub RVM: unpinned = executing remote HEAD; default trust prompt = EOFError on non-interactive first run → pinned SHA + `trust_repo=True`.
- Portrait FILENAME flows into a vendored `os.system` ffmpeg line — a `%`-containing Vietnamese filename corrupts the command silently → sanitize-copy into the run dir.

## Lessons Learned

1. **Verify the data-prep layer, not just the loop.** "Stateless loop" ≠ "independent frames" — conditioning windows live upstream of the loop. Trace the tensor's provenance, not the iteration.
2. **Adversarial review before code has absurd ROI here:** ~30 min of 4 parallel reviewers rewrote one phase and patched six others; the Critical alone would have cost days mid-build.
3. **Never let research estimates for consumer-GPU inference into a plan unbenchmarked** — A100 numbers and "likely Apache" licenses are noise.
4. **Stochastic systems need σ before verdicts.** Record render-to-render spread first; only then do "engine A beats engine B by 0.5" claims mean anything.
5. **Fail-open guards are landmines** (duration cap skipping when ffprobe missing). Derive limits from artifacts that must exist (the decoded wav), not from optional tooling.

## Next Steps

1. User answers 4 validation questions (defaults documented in plan.md Validation Log) — none block cook.
2. User supplies real VN voice clip (10–30s) + MC portrait → P1 pilot + P5/P6 test set.
3. `/ck:cook D:\Project2\Lipsync\plans\260703-0017-pa2-v2-completion-tech-refresh\plan.md` (P1+P2 are independent starters; 7 tasks hydrated with dependency chains).

## Unresolved Questions

- The 4 pending validation answers (UI output default, spike gating, asset timing, escalation order).
- Vietnamese lip-sync accuracy remains empirically unknown until the pilot — everything downstream of it is provisioned, not promised.
