---
phase: 2
title: "RVM Matting & Pipeline Hardening"
status: completed  # two criteria PARTIAL, flagged to user: e2e 64s vs ≤55s target; stability metric literal-vs-intent
effort: "2d"
priority: P1
dependencies: []
---

# Phase 2: RVM Matting & Pipeline Hardening

## Overview

Swap the matting bottleneck (BiRefNet @1024 ≈ 2fps → 71s of a 113s render) for RobustVideoMatting (~120-150fps on 3060, recurrent = flicker-free) behind a `MattingEngine` interface; keep BiRefNet as the commercial-safe fallback. Piggyback the pipeline hardening that phase 4's resume needs: unique run ids, per-run temp names, loud (not silent) audio-mux failures, free-VRAM helper.

## Requirements

- Functional: `cfg.matting_engine` in {`rvm` (default), `birefnet`}; `cfg.commercial_safe=True` forces `birefnet` (RVM is GPL-3 — fine for personal use, excluded from any commercial/distribution mode).
- Functional: identical green output contract — existing E2E green-pixel test passes with both engines.
- Non-functional: 5.5s/256 clip end-to-end ≤55s (from ~113s); alpha temporally stable (no flicker).

## Architecture

Current contract (from `lipsync/matting_birefnet.py:31-79`): ctor `(device, precision="fp16", resolution=1024)`, `load()`, `unload()`, `alpha_for(rgb uint8 HxWx3) -> float32 HxW in [0,1]` at input size. Only per-frame call site: `lipsync/green_compositor.py:54`; construction: `lipsync/pipeline.py:47-50`.

```
lipsync/matting_base.py      # MattingEngine Protocol: load/unload/alpha_for/reset_clip
lipsync/matting_rvm.py       # RVM adapter: torch.hub PeterL1n/RobustVideoMatting (mobilenetv3),
                             # fp16 on cuda, recurrent state (r1..r4) held internally across
                             # alpha_for() calls, reset_clip() clears state, downsample_ratio
                             # auto (~0.25 at 1080p per RVM docs), weights cached under models/rvm
lipsync/matting_birefnet.py  # + no-op reset_clip(); unchanged otherwise
lipsync/pipeline.py          # _matting() KEYED cache: _matte_key=(engine, precision), unload old
                             # engine on key change (mirrors _sad_key, pipeline.py:33-43) — without
                             # this, flipping commercial_safe mid-session silently keeps GPL RVM live
```

RVM adapter note: recurrent state is the point — a stateless per-frame wrapper would discard RVM's temporal stability. State lives in the adapter. `reset_clip()` has exactly ONE owner: the top of the shared compositor driver (`composite()`, phase 3), paired with a `finally` clear so a crashed clip cannot poison the next render at a different resolution. Never per frame, never a second call site.

Hardening (same files touched anyway):
- `lipsync/pipeline.py:55,87`: run id = `time.strftime + "_" + uuid4().hex[:6]` (kills same-second collisions); pass run work dir down.
- `lipsync/green_compositor.py:39`: `green_silent.mp4` → `work/<runid>_green_silent.mp4` (kills fixed-name collision).
- `lipsync/green_compositor.py:72-74`: audio-mux failure currently falls back to SILENT video with no signal — keep fallback but return a `warnings: [str]` channel up through `PipelineResult` dict and show in UI status (phase 7 wires UI; here: populate).
- `lipsync/hardware.py`: add `free_vram_bytes() -> int` via `torch.cuda.mem_get_info()` (phase 4 chunk sizing consumes this).
- `lipsync/pipeline.py`: copy the uploaded portrait to a sanitized fixed name in the run dir (`work/portrait.<ext>`, mirroring `prepare_audio`'s fixed wav name) BEFORE handing it to SadTalker — the original filename flows into a vendored `os.system` ffmpeg line (`src/utils/videoio.py:22-23`, fed via `src/utils/preprocess.py:65` → `src/test_audio2coeff.py:102`); a `%`-containing or exotic filename corrupts the command and the exit code is ignored.
- `lipsync/ffmpeg_utils.py`: `has_ffprobe() -> bool` capability probe (imageio-ffmpeg ships NO ffprobe; consumers must fail loud, not guess — phase 4 resume validation).
- `lipsync/config.py` + `lipsync/animation_sadtalker.py`: `seed: int | None = None` → when set, seed `torch`/`random`/`numpy` at render start. Pins SadTalker's stochastic CVAE pose + blink noise for comparison renders and the phase-4 chunked-vs-full test (phases 1/4/5/6 consume).

## Related Code Files

- Create: `lipsync/matting_base.py`, `lipsync/matting_rvm.py`, `tests/test_matting_engines.py`
- Modify: `lipsync/matting_birefnet.py`, `lipsync/pipeline.py`, `lipsync/green_compositor.py`, `lipsync/hardware.py`, `lipsync/config.py` (fields: `matting_engine: str = "rvm"`, `commercial_safe: bool = False`), `requirements.txt` (RVM has no pip package — vendor via torch.hub with pinned commit or vendored checkpoint file; document)
- Delete: none

## Implementation Steps

1. `matting_base.py`: `MattingEngine` Protocol (typing.Protocol) = ctor contract doc + `load/unload/alpha_for/reset_clip`. Add `reset_clip()` no-op to BiRefNetMatte. Retype the compositor's matte param (`green_compositor.py:26`) from `BiRefNetMatte` to the protocol.
2. `matting_rvm.py`: load via `torch.hub.load("PeterL1n/RobustVideoMatting:<pinned-commit-sha>", "mobilenetv3", trust_repo=True)` — pin the FULL commit SHA (unpinned = executing upstream HEAD `hubconf.py`; the default trust prompt raises EOFError on a non-interactive first run under `run_app.bat`) and record the downloaded checkpoint's SHA-256; scope the hub cache to `models/rvm` (set + restore `torch.hub.set_dir` around the call — it mutates process-global state); fp16 on cuda; `alpha_for`: to tensor → `model(src, *rec, downsample_ratio)` → keep `rec` for next call → pha to float32 HxW numpy. Confirm VRAM ≈2-3GB and flat across ≥1500 frames (assert via `free_vram_bytes` before/after in a test log).
3. Wire `pipeline._matting()` with keyed cache `(matting_engine_effective, precision)` + unload-on-switch (mirror `_sad_key`, `pipeline.py:33-43`); `commercial_safe` override with a logged notice. UI exposure (engine dropdown + checkbox) lands in phase 7; until then the config field is the switch.
4. Hardening edits (run id, temp name, warnings channel, `free_vram_bytes`, `has_ffprobe`, portrait-filename sanitization, `cfg.seed` wiring).
5. Tests — `tests/test_matting_engines.py`: (unit) BEHAVIOR contract per engine — `alpha_for` returns HxW float32 in [0,1] at input size on a tiny random frame (CPU where feasible); RVM reset determinism — after `reset_clip()`, `alpha_for(f)` equals the first-call output; live engine flip — `run()` twice on ONE Pipeline with `commercial_safe` toggled → engine class switches and the old engine's `unload()` was called (duck-type-only checks are phantom tests — they pass for an engine returning garbage); (E2E `RUN_E2E=1`) run existing pipeline with `matting_engine=rvm` — reuse green-corner assertions from `tests/test_pipeline_e2e.py:36-47`; log per-stage timings for the perf claim; run once with `birefnet` to prove fallback intact.
6. Temporal stability check (manual/scripted): render one clip both engines, compute mean |alpha[t]-alpha[t-1]| border variance — RVM must be ≤ BiRefNet (no flicker regression). Record numbers in the phase completion notes.

## Success Criteria

- [x] 5.5s/256 clip end-to-end ≤55s with RVM (baseline ~113s) — **64s measured, ACCEPTED by user 2026-07-03** (composite 71→17.2s achieved the matting goal; residual = animate stage, outside matting scope; animate speed revisits with P5/P6 if at all).
- [x] Existing green E2E passes with BOTH engines (no output contract change)
- [x] `commercial_safe=True` demonstrably forces BiRefNet (unit test), including a LIVE flip on one Pipeline instance (old engine unloaded) + failed-load cache-poisoning regression test
- [x] RVM ref pinned: hub commit SHA + checkpoint SHA-256 recorded (module docstring + README + requirements.txt); reviewer re-hashed and confirmed
- [x] RVM reset determinism verified (post-reset output == fresh-engine output)
- [x] RVM VRAM flat across a 60s clip — 0.0MB drift over 1500 frames @720p
- [x] Two renders launched in the same second produce distinct outputs/temp files (uuid-suffixed run ids, unit test)
- [x] Audio-mux failure surfaces a warning in `PipelineResult` (unit test with real missing-audio mux failure; reset_clip ownership asserted in same test)

## Completion Notes (2026-07-03)

**Measured on the RTX 3060 12GB (5.5s clip @256/full/fp32 SadTalker, fp16 matting):**
- Pure matting throughput @800×1200: RVM **25.0 fps** vs BiRefNet **3.3 fps** (7.6×).
- E2E wall: **64s** (audio 0.2 + animate 46.6 + composite 17.2) vs v1 baseline ~113s (composite 71s → **17.2s**). The ≤55s success criterion is a **near-miss**: matting stopped being the bottleneck (now ~27% of wall); the remaining time is SadTalker facerender + H.264 encode, out of this phase's scope. FLAGGED to user, not silently accepted.
- Temporal stability: whole-frame mean|dA| RVM 0.00039 vs BiRefNet 0.00017 — the literal "RVM ≤ BiRefNet" reading FAILS, but refined isolation shows why: confident-region (alpha<0.05 or >0.95) flicker is RVM 0.000055 vs BiRefNet 0.000025 — both ≈0.01 gray levels/frame, invisible; soft-edge deltas are comparable (0.0093 vs 0.0086, motion-driven). The gap is RVM's wider soft-edge band, not flicker. Corner worst-case single-pixel blip: RVM 0.093 vs BiRefNet 0.047 (E2E corner-green assertions still pass). FLAGGED to user with numbers.
- RVM VRAM flat across 300 frames @480×640 (drift < 64MB, model test); pipeline-end peak_reserved 2.84GB.
- Pins: hub `PeterL1n/RobustVideoMatting@53d74c6826735f01f4406b5ca9075eee27bec094`, ckpt sha256 `3c7c1d92033f7c38d6577c481d13a195d7d80a159b960f4f3119ac7b534cf4f8` (recorded in module docstring + README + requirements.txt).
- Tests: fast suite 16 passed; RUN_MODEL_TESTS 4 passed (contract, reset determinism, BiRefNet contract, VRAM flat); RUN_E2E green-corner PASS for BOTH engines; distinct same-second run ids + mux-warning channel covered by unit tests.
- 18.6s VN clip renders (phase-1 baselines) reconfirm composite ≈36-40s regardless of face_size (RVM matting at source resolution), animate 79-89s @256 / 238s @512.

**Code review (report: `plans/reports/code-reviewer-260703-0142-pa2-phase2-rvm-matting-hardening-report.md`, DONE_WITH_CONCERNS → all concerns resolved):**
- High H1 FIXED: `_matting()` stale `_matte_key` on load() failure could cache-hit a broken/wrong engine (worst case: commercial_safe render lazy-loading GPL RVM). Key now assigned only after successful load; regression test `test_failed_load_does_not_poison_cache` added.
- Mediums FIXED: `_sanitize_portrait` re-encode now checkable via `imencode`+`write_bytes`; NOTICE.md RVM contradiction corrected (personal-use default + commercial_safe carve-out); `sync_metrics._env_with_ffmpeg` fails loud when only the imageio versioned binary exists; phase-03 stale "zero test imports" note corrected.
- Lows FIXED: `plan_windows` fractional-tail drop (+test); effective-engine progress text; empty-exc-message guard; `TimeoutExpired` CLI handling; `.gitignore` narrowed to `tools/syncnet/`.
- Deferred to phase 7 (accepted): codebase-summary refresh (docs-manager), shape-only BiRefNet test depth.
- VRAM criterion closed at full spec after review: 0.0MB drift across 1500 frames @720p (60s @25fps).
- Post-fix fast suite: 28 passed.

## Risk Assessment

- RVM + torch 2.1.2+cu121 compat unknown edge (research Q5) → smoke test first; RVM is torchscript-friendly, low risk.
- torch.hub needs internet once → cache to `models/rvm`, document in README (same pattern as GFPGAN weights). Pinned `repo:commit` + `trust_repo=True` prevent remote-HEAD drift and the interactive trust prompt (EOFError under non-interactive launch).
- GPL-3 containment → module boundary + flag; no GPL import outside `matting_rvm.py`; NOTICE.md updated in phase 7.
- Edge-quality regression vs BiRefNet 1024 on fine hair → E2E visual spot-check; if unacceptable, `matting_engine=birefnet` remains one click away (that is the point of the interface).
