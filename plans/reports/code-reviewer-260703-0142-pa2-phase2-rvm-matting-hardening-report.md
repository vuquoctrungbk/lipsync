# Code Review — Phase 2: RVM Matting & Pipeline Hardening

Plan: `plans/260703-0017-pa2-v2-completion-tech-refresh/phase-02-rvm-matting-pipeline-hardening.md`
Diff: uncommitted tree vs `3786a12` (v1). Scope: 5 new files (~800 LOC), 9 modified.
Verification run: fast suite `26 passed, 5 skipped` (model/E2E markers), `py_compile` clean on all touched files. GPU suites NOT re-run per instruction; measured results taken as given.

## Overall Assessment

Solid phase. Architecture matches spec (Protocol + adapter + keyed cache + single-owner reset). Claims were spot-checked against reality, not taken from comments: RVM checkpoint SHA-256 recorded in `matting_rvm.py` **matches the actual file hash** (certutil), the pinned hub commit **exists upstream** (merge of PR #227), and the `%`-filename hazard motivating portrait sanitization **is real** in vendored SadTalker (`os.system` → cmd.exe `%`-expansion; ext routing `['jpg','png','jpeg']` case-sensitive). One High defect in the keyed-cache exception path undermines the phase's own licensing invariant; the rest is Medium/Low.

## Success Criteria (evidence-based)

| Criterion | Verdict | Evidence |
|---|---|---|
| e2e ≤55s (baseline ~113s) | **FAIL (near-miss)** | Measured 64s wall (animate 46.6 + composite 17.2). Composite 71→17.2s = 4.1×; matting bottleneck resolved. Residual gap is the animation stage — outside P2 scope. Lead decision: accept re-baselined or push to a later phase. |
| Green E2E both engines | PASS | RUN_E2E both engines green-corner PASS (given); assertions are real pixel checks (`test_matting_engines.py:329-341`), not phantom. |
| commercial_safe forces BiRefNet + live flip | PASS* | `test_commercial_safe_forces_birefnet`, `test_live_engine_flip_unloads_old` assert class switch + `unload()` on old engine. *Caveat: H1 exception path can defeat this guarantee. |
| RVM pin recorded (commit + ckpt sha256) | PASS | Docstring + README + requirements comment. Verified: commit `53d74c68…` exists upstream; `models/rvm/checkpoints/rvm_mobilenetv3.pth` hashes to exactly the recorded `3c7c1d92…4f8`. |
| Reset determinism | PASS | RUN_MODEL_TESTS passed (given); test asserts post-reset == first-call output + resolution change after reset (`test_matting_engines.py:254-278`). |
| VRAM flat across 60s clip | **PARTIAL** | Test covers 300 frames ≈12s@25fps (<64MB drift after 30-frame warmup), not the 60s/1500-frame bar in spec step 2. Fixed-size rec tensors make growth implausible, but the literal bar is unevidenced. Bump the loop when GPU frees (phase-4 long-clip work covers it). |
| Same-second runs distinct | PASS | uuid4 suffix in run id + output name (`pipeline.py:119`); `test_same_second_runs_get_distinct_ids`. |
| Mux failure surfaces warning | PASS | `test_mux_failure_surfaces_warning_and_resets_clip` drives a REAL ffmpeg failure, asserts warning text + silent output + exactly 2 reset_clip calls. `warnings` plumbed additively through `run()` dict. |
| Temporal stability ("RVM ≤ BiRefNet", spec step 6) | **PARTIAL — literal FAIL** | Whole-frame mean\|dA\|: RVM 0.00039 > BiRefNet 0.00017. Refined confident-region flicker: 5.5e-5 vs 2.5e-5 — both ~2 orders below perceptibility; delta consistent with RVM's wider soft edge, not temporal flicker. The as-authored metric fails; the refined-metric interpretation is reasonable but is a post-hoc re-spec. Record both numbers + methodology in completion notes and get explicit sign-off. |

## Critical

None.

## High

**H1. `_matting()` stale-key on `load()` failure → wrong-engine cache hit; worst case a `commercial_safe` render silently runs GPL RVM.** `lipsync/pipeline.py:77-82`.
Sequence: (1) run with `birefnet` → `_matte_key=("birefnet","fp16")`. (2) flip to `rvm`; BiRefNet unloaded; `RVMMatte.load()` raises — realistic: first run needs internet for torch.hub. Now `_matte` = RVM instance (unloaded) but `_matte_key` still `("birefnet","fp16")` (key assigned only after load, `pipeline.py:82`). (3) next run with `commercial_safe=True` → effective key `("birefnet","fp16")` == stale key → cache hit returns the **RVMMatte** instance → `alpha_for` lazy-loads RVM (`matting_rvm.py:73-74`). This resurrects exactly the failure the keyed cache exists to prevent (phase spec line 35). Fix (3 lines): null out `_matte`/`_matte_key` before constructing the replacement; assign key only after successful `load()`.
Note: `_sadtalker` has the same shape but self-heals — engine type can't diverge and `animate()` re-`load()`s from the refreshed cfg. Only `_matting` has the type-divergence hazard.

## Medium

**M1. `cv2.imwrite` return unchecked + not unicode-safe — inside the function whose purpose is path safety.** `lipsync/pipeline.py:112`. The read three lines up deliberately uses `np.fromfile` ("unicode-safe on Windows") but the write uses `cv2.imwrite`, which returns `False` (no exception) on failure and mishandles non-ASCII paths on Windows. Repo cloned under a non-ASCII path (plausible for Vietnamese users) → exotic-ext portraits silently produce no file → SadTalker fails far from the cause. Fix: `cv2.imencode(".png", img)[1].tofile(str(safe))` or check the bool and raise `PipelineError`.

**M2. NOTICE.md now contradicts the build.** NOTICE.md still states "no non-commercial weights are included or downloaded" and lists RVM under "Deliberately excluded", while this diff makes RVM the auto-downloading default and README's license section was already rewritten. Phase 7 owns NOTICE by plan, but committing this tree ships an internally contradictory license story. Cheap interim: one corrective line in NOTICE.md in this commit.

**M3. `_env_with_ffmpeg()` is ineffective in the only case it exists for.** `scripts/sync_metrics.py:100-103`. Verified in this venv: imageio-ffmpeg's binary is `ffmpeg-win64-v4.2.2.exe`, so prepending its directory to PATH does not make a child `ffmpeg` lookup resolve; when system ffmpeg is on PATH (this machine: winget ffmpeg 8.0) the prepend is redundant. Net effect: syncnet hard-requires system ffmpeg either way. Fail fast with a clear message when `shutil.which("ffmpeg")` is None, or drop the helper.

**M4. Phase-3 plan claim invalidated by this diff.** `phase-03-webm-alpha-export.md:45` plans to replace `composite_to_green` outright, justified by "zero test imports (grep-verified)". `tests/test_matting_engines.py:217,226` now imports and calls it directly. Executing phase 3 as written breaks this P2 test. Plan doc needs refresh before phase 3 (not editing per review-only constraint).

## Low

- **L1.** `plan_windows` drops sub-second tails when `int(duration)` is a window multiple — verified: `plan_windows(60.5, 60) == [(0.0, 60.0)]`, `120.9 → [(0,60),(60,120)]`. Contradicts the "covering the clip" docstring; immaterial for scoring. Also `duration=0` yields degenerate `[(0.0, 0.0)]`. `scripts/sync_metrics.py:87-97`.
- **L2.** Progress message shows the *requested* engine, not the effective one, when `commercial_safe` forces birefnet (`pipeline.py:150`) — UI would read "(rvm)" during a forced-birefnet render; the forcing notice only reaches stdout.
- **L3.** `str(exc).splitlines()[-1]` IndexErrors on an empty exception message (`green_compositor.py:88`). Unreachable from current `FFmpegError` raise sites; cheap to guard.
- **L4.** `subprocess.TimeoutExpired` / `CalledProcessError` (from `_cut_segment` `check=True`) escape `main()`'s `SyncMetricsError` handler → raw traceback on the CLI. Operator script — cosmetic.
- **L5.** `.gitignore` `tools/` ignores the whole namespace; future committed tooling would vanish silently. Scope to `tools/syncnet/`.
- **L6.** `docs/codebase-summary.md` now stale (BiRefNet-only architecture, "~80 line" compositor). Presumably the phase-7 docs pass; flagging so it is not forgotten.
- **L7.** `test_free_vram_bytes_and_has_ffprobe_shapes` is shape-only. Acceptable for trivial wrappers; noted per phantom-test lens.

## Verified Non-Issues (do not re-raise without new evidence)

- **torch.hub pin/restore**: `set_dir` restored in `finally`; `model.to(device)` correctly placed after restore; exceptions inside `hub.load` cannot leak the scoped dir (`matting_rvm.py:47-56`). Hub cache confirmed populated under `models/rvm` (repo zip dir + `checkpoints/` + `trusted_list`) → subsequent loads are offline.
- **Lazy `load()` inside `@torch.inference_mode()`**: empirically tested on torch 2.1.2 — module construction, `load_state_dict`, forward, and cross-context reuse all work. Not a defect.
- **Recurrent-state lifecycle**: `unload()` and `reset_clip()` both clear `_rec`; production reset call sites are exactly compositor start + `finally` (grep-verified) — single-owner contract honored; crashed clips cannot poison the next render.
- **Portrait sanitization**: hazard confirmed in vendored source (`videoio.py:22-23` os.system with quoted interpolation → cmd.exe `%`-expansion; `preprocess.py:74` case-sensitive ext routing). Sanitizer lowercases ext, fixes names, uses copy for known exts. Test covers `%04d` + uppercase `.PNG`. Residual pre-existing risk: non-ASCII/`%` in the *repo path itself* still flows into `os.system` via the work dir.
- **Touchpoint regressions (check b/c)**: `run()` dict change is additive (`warnings`); `app.py` consumes `output/timings/vram/device` — all intact, app.py untouched. `composite_to_green` signature change has exactly one production caller (`pipeline.py:154`), updated. E2E test contract unchanged (now exercises RVM by default — intended).
- **Seed wiring**: seeds random/np/torch/cuda at render start; run ids use `uuid4` (os.urandom) so seeding cannot collide run ids.
- **requirements.txt**: no new pip deps; fragile numpy/librosa triad untouched; RVM hub pin documented in-file.
- **Patterns (check d)**: module docstrings, dataclass config fields, load/unload/lazy-load mirror existing engines; all new files <200 LOC except tests (acceptable).

## Recommended Actions (priority order)

1. Fix H1: clear `_matte`/`_matte_key` before engine swap; set key after successful load. Add a unit test: failing `load()` then retry with old key must not return the new-type instance.
2. Fix M1: unicode-safe write or check `imwrite` return.
3. Add one interim corrective line to NOTICE.md (M2) or land phase-7's NOTICE update in the same commit.
4. Make `sync_metrics` fail fast when no system ffmpeg (M3).
5. Update phase-03 plan text re: `composite_to_green` test imports (M4) — lead/planner action.
6. Record temporal-stability numbers + methodology caveat and the 64s vs 55s near-miss in phase completion notes; get explicit acceptance for both PARTIAL/FAIL criteria.

## Metrics

- Fast suite: 26 passed, 5 skipped, 0 failed (14.8s). No new warnings beyond pre-existing gradio deprecation.
- Syntax: `py_compile` clean on all 12 touched Python files.
- Lint/type: no linter or mypy config in repo; not run.
- GPU/E2E: not re-run (per instruction); results taken as given.

## Unresolved Questions

1. Is 64s acceptable as the P2 exit bar (re-baseline), or does a later phase own closing the animate-stage gap to ≤55s?
2. Who signs off on the temporal-stability metric re-interpretation (whole-frame vs confident-region), and where do the numbers get recorded — phase completion notes only, or also the plan's success-criteria checklist?
3. Should the 1500-frame VRAM assertion be strengthened now or folded into phase-4's long-clip tests?
