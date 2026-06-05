# Bootstrap Vietnamese Lip-Sync Green-Screen App: From Empty Dir to Working End-to-End

**Date**: 2026-06-05 07:21  
**Severity**: Low (shipped working, but assumptions corrected mid-build)  
**Component**: Local inference pipeline (SadTalker + BiRefNet + ffmpeg)  
**Status**: Resolved (tested, committed as 3786a12)

## What Happened

Went from an empty directory to a fully functional, tested Vietnamese lip-sync desktop app in one session. Input: still character image + voice audio. Output: talking-head MP4 on solid green background (chroma-key ready), 100% local, no paid APIs.

14-agent research workflow selected the stack; user locked 4 core decisions (SadTalker, BiRefNet, Gradio, flat green MP4). Executed 9-phase implementation plan (env → audio → animate → matte → composite → UI → tests → docs). Built and verified on the actual target hardware (RTX 3060 12GB, RDP-accessed Windows 10, no Python pre-installed). Commit: 3786a12, all 6 unit + 1 E2E tests pass.

## The Brutal Truth

The plan was solid BUT built on assumptions that broke during implementation. A naive waterfall would have shipped with those broken assumptions and failed in production. Instead, we measured, found the assumptions wrong, and corrected them live — but it's frustrating that planning didn't catch these earlier.

Worse: the package namespace collision (`src/lipsync` vs SadTalker's `src`) would have been a silent, maddening import failure at deployment time if we hadn't caught it during testing. That's an architectural flaw in the planning phase (should have validated the import structure earlier).

The matting bottleneck (~2 fps @ 1024) is also a reality check — it works, but a user impatient with ~70s encode time might ask for faster. That's not a bug, but it's a friction point the plan underestimated.

## Technical Details

**Environment bootstrap:**
- Target box: Windows 10 Pro, RTX 3060 12GB (Ampere CC 8.6), Xeon E5-2680v4, 64GB RAM, RDP-accessed.
- Python: Only MS Store stub present → installed real Python 3.11 via `winget install Python.Python.3.11`.
- CUDA: 12.1 pre-installed; CUDA over RDP verified with smoke test.

**Dependency / compatibility collisions:**

1. **PyAV 11 (`av`) failed to compile** (missing avformat.lib on Windows) → removed; video I/O via imageio-ffmpeg + ffmpeg CLI (YAGNI wins).

2. **setuptools 82 removed `pkg_resources`** → librosa 0.9.2 import crashed → pinned `setuptools<81`.

3. **torchvision 0.17 removed `functional_tensor`** that basicsr 1.4.2 imports → deliberately pinned torchvision **0.16.2** with torch 2.1.2+cu121 (0.17 is broken for this use case).

4. **SadTalker imports gfpgan + face_alignment at module load** → both required (not optional); can't be lazy-loaded without refactoring their code. Accepted it.

5. **dlib build failures** → SadTalker uses FAN/S3FD via face_alignment anyway, so dlib was dropped entirely. No loss.

6. **setuptools/numpy/librosa version matrix** → locked numpy 1.23.5 + librosa 0.9.2 + setuptools<81; verified import order on first boot.

**Key measurements that corrected plan assumptions:**

- **fp16 mandatory?** NO. Plan assumed "fp16 autocast mandatory on 12GB". Measured: SadTalker peaks ~2.8 GB fp32 at 256px face, both animate + matte engines co-resident ~4 GB total. Headroom exists. Reverted to **fp32 default** (stable, no autocast NaN risk). fp16 available as a flag but not needed.

- **Matting is required?** YES, confirmed by measurement. Raw SadTalker output preserves the source image background. Naive ffmpeg `chroma_key` filter cannot fix this (no subject isolation). **BiRefNet per-frame alpha is MANDATORY** to isolate the subject and composite it onto solid green.

**Architectural fix:**

- Package clash: `src/lipsync/` would collide with SadTalker's own top-level `src` on sys.path (silent import failure). Moved to repo-root `lipsync/` before first test run. Caught by testing; never shipped broken.

**Performance bottleneck (expected, not a regression):**

- Matting: BiRefNet @ 1024x1024 achieves ~2 fps. 30-frame animation → ~70s matte + composite. Not a bug, but a friction point for impatient users.

## What We Tried

1. **PyAV path**: Attempted to compile `av` on Windows → failed (header files missing). Switched to ffmpeg CLI (proven, cross-platform). Correct call.

2. **Setuptools/librosa conflict**: Tried latest setuptools → librosa broke. Pinned setuptools<81 → works. Root cause: setuptools 82 removed pkg_resources API that librosa 0.9.2 still expects. Pinning was the right move (can't upgrade librosa fast enough for this deadline).

3. **torchvision 0.17**: Upgraded to latest → basicsr failed on `functional_tensor` import. Downgraded to 0.16.2 → works. basicsr 1.4.2 is locked by GFPGAN, so the bottleneck is there, not in our code.

4. **Import order**: Tried loading GFPGAN before torchvision → still failed. Root cause was torchvision version, not import order. Correct diagnosis after fix.

## Root Cause Analysis

**Why assumptions broke:**

1. **fp16 mandatory assumption**: Plan was pessimistic (common for 12GB VRAM). Didn't measure; assumed worst-case. Measurement revealed headroom. Lesson: measure before designing for constraints you haven't hit.

2. **Package namespace clash**: Planning focused on feature architecture (pipeline order, model selection) but skipped import/sys.path validation. Lesson: namespace conflicts are silent killers; validate imports + package structure as part of design review.

3. **Matting requirement clarity**: The plan statement "Matting is REQUIRED" was correct but the reasoning was hand-wavy. Should have been: "raw output = source bg, ffmpeg chroma_key cannot isolate, only alpha matte can." Clearer statement = clearer implementation.

**Why we didn't catch these earlier:**

- **fp16**: Pessimistic planning is conservative; measuring requires hardware. OK to be conservative in a plan, but should flag "assumption — validate on HW" explicitly.
- **Namespace clash**: Happens only when imports run; plan review is text-only. Should have prototyped import structure in phase 1.
- **Matting**: Plan was correct; the subtlety (why ffmpeg alone fails) just wasn't articulated clearly enough to catch in review.

## Lessons Learned

1. **Measure constraints live, don't assume.** VRAM, latency, device limits — measure on real hardware before committing to design decisions. Saves a revert cycle.

2. **Namespace/import conflicts are architectural issues.** Validate them in phase 1, not phase 7. A 5-minute prototype import of all major libraries catches these early.

3. **Flag plan assumptions explicitly.** "fp16 is assumed necessary; MEASURE peak VRAM before finalizing design." helps reviewers and implementers know where risk lives.

4. **Don't hide requirement reasoning behind brevity.** "Matting is REQUIRED" is correct, but "because raw output keeps source background, and ffmpeg chroma_key cannot isolate" is actionable. Forces reviewers to think critically.

5. **Downgrade willingly when the bottleneck is external.** torchvision 0.17 is newer, but basicsr 1.4.2 (locked by GFPGAN, locked by user choice) can't use it. Pinning is not a regression; it's correctness given the constraint.

6. **Sequential model load + empty_cache is the right pattern for VRAM-constrained inference.** Load animate, run, empty_cache, load matte, run. Works at 4GB; async/batching is a future optimization.

## Next Steps

1. **Vietnamese lip-sync accuracy validation (user gate).** E2E test passes (green output verified), but Vietnamese-speaker pilot clip UNVERIFIED. User must run a real VN-speaker clip. If sync fails (tonal accuracy, tone-deaf movements), escalate (consider MuseTalk/Whisper as backup). **Do NOT silently swap engines.**

2. **Matting speed optimization (future, not blocking v1).** Possible improvements: lower resolution intermediate, frame batching, GPU optimization. Currently ~2 fps is acceptable but not great. Note for v1.1.

3. **GFPGAN enhancer download.** Weights download to CWD-relative `gfpgan/` on first enable (now gitignored). Works, but could be cleaner (explicit model cache dir). Acceptable for now.

4. **No other blockers.** App runs E2E on target hardware, tests pass, commits clean. Ready for user pilot.

## Unresolved Questions

- **Vietnamese lip-sync accuracy:** Pipeline is language-agnostic (audio feature extraction is universal). Tonal accuracy (Mandarin tones, Vietnamese tones) unbenchmarked. Depends on SadTalker's training data (unknown if VN-heavy or VN-naive). User must validate with a real VN clip.
- **Matting speed sensitivity:** Is 70s encode + 42s animate acceptable to user? No SLA given. If user asks for faster, first check if lower-res intermediate is acceptable (trade quality for speed), then consider batching/GPU optimization.
- **GFPGAN enhancer UX:** Currently enabled via dropdown toggle. Should it auto-enable, auto-disable by VRAM, or stay manual? Deferred until user feedback.
