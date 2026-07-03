# Code Review — PA2 Phases 3+4: WebM Alpha Export + Long-Audio Chunking/Resume

Scope: uncommitted working tree vs `06013a5`. 14 files, +809/−198. Review-only; no code changed.
Verified against vendored SadTalker sources, phase specs, empirical wav-stability check, fast suite re-run (58 passed, 6 skipped, 21.6s). GPU/E2E results taken as given per task constraints.

## Verdict

Architecture matches the red-teamed phase specs; halo math, manifest integrity binding, and sink isolation are real and unit-proven. Load-bearing resume assumption (wav sha1 stability) **empirically verified — holds**. Two HIGH findings: the most expensive crash window (all segments done, composite running) produces an *unresumable and silently purged* run, contradicting phase-04's "composite restarts from the segment iterator" design; and the new Resume button introduces a concurrent-render hazard under gradio 4's per-event concurrency.

## Verified claims (evidence)

- `semantic_radius=13`, clamped window: `third_party/SadTalker/src/generate_facerender_batch.py:12,93-98` — test `_vendored_window` is a faithful transcription.
- Frame count from mat rows, never audio: `generate_facerender_batch.py:59` (`frame_num = generated_3dmm.shape[0]`); `animate.py:188` trims batch padding. 1s-silent-wav trick is safe: pydub slice beyond length returns what exists; vendored mux has no `-shortest` (`videoio.py:22`), so video length is untouched; `render_segment` ffprobe count is the backstop.
- No first-frame kp normalization in facerender (`make_animation.py:127` `kp_norm = kp_driving`) — segment independence holds architecturally; seeded chunked-vs-full E2E (mean 0.460/255, worst 0.777 over 750 frames) confirms.
- **wav sha1 stability (resume-critical)**: measured with project venv + system ffmpeg — `prepare_audio(mp3)` twice → identical sha1; `prepare_audio(decoded_wav)` → byte-identical to input (96,078 bytes, same hash). Resume matching works for both the Resume button (re-feeds decoded wav) and re-Generate (re-decodes original). Residual: ffmpeg *upgrade* between crash and resume changes the `Lavf` header tag → silent non-match (see L3).
- Purge on resumed success purges BOTH old run dir and new work dir: `pipeline.py:212-215`.
- `os.kill(pid, 0)` on Windows does terminate the target — the ctypes `OpenProcess` approach in `run_manifest.pid_alive` is correct; pid-reuse false-alive fails safe (refuse adopt) and is documented.
- v1 touchpoints: `run()` contract additive (`output`/`outputs`/`warnings`); ≤120s dispatch unchanged (`pipeline.py:168`); `tests/test_pipeline_e2e.py` untouched by the diff and reported green; app `generate()` output slots consistent.
- No leftover `composite_to_green` references in production code (only stale `docs/codebase-summary.md` + old plan/report files).

## Critical Issues

None.

## High Priority

### H1. Crash during composite = all segment work lost (unresumable + silently purged)
- `run_manifest.py:214-215` (`find_resumable`: `if is_complete(m): continue`), `pipeline.py:341` (`latest_resumable` same skip), `run_manifest.py:231-242` (`purge_stale_runs` purges matching dead-owner runs with **no** completeness check).
- Scenario: 600s render → ~2h of segments all `done` → composite (matting + VP9, 20-40+ min) crashes or machine reboots → relaunch → Resume button: "No interrupted render found" → user clicks Generate → `purge_stale_runs` deletes the complete run dir → audio2coeff + ALL segments re-render from scratch.
- This contradicts phase-04 architecture: "Composite stage is NOT checkpointable … resume restarts it from the segment iterator." The rest of the machinery already supports all-done resume: `_chunked_animate`'s pending loop no-ops when `pending == []` and proceeds straight to the iterator.
- Fix: stop skipping complete manifests in `find_resumable`/`latest_resumable` (a run is finished only when its run dir is purged on success, which already happens at `pipeline.py:212`); keep `purge_stale_runs` as-is or make it equally completeness-aware. Small change, big payoff — this is the single most valuable resume case.

### H2. Resume button enables concurrent renders in one process (gradio 4 per-event concurrency)
- `app.py:162-167`: `run_btn.click` and `resume_btn.click` are separate event listeners. gradio 4.44 `queue()` applies `default_concurrency_limit=1` **per listener** — Generate and Resume can execute simultaneously (v1 had one button, so this hazard is new to this diff).
- Consequences when both run: (a) `render_segment`'s process-global `os.chdir` (`chunked_facerender.py:137-142`) flaps CWD under the vendored CWD-relative temp/mux (`videoio.py:21-26`, `paste_pic.py:56` resolve relative names at *different times* — `shutil.move`/`os.system` can hit wrong-CWD → FileNotFoundError or files leaking into the other run's dir); (b) shared engine mutation: `pipeline.py:65` `self._sad.cfg = cfg` swaps the live engine's config mid-render of the other run (wrong expression/still/enhancer for remaining segments, undetected by the fingerprint computed at start); (c) two facerender+matting stacks on one 12GB card → OOM.
- Precondition: a dead resumable run exists (else `resume_render` raises gr.Error fast) — but that is exactly the post-crash state where users click both buttons.
- Fix (one line per listener): give both events a shared `concurrency_id="render"` (limit 1), or guard `Pipeline.run` with a process-wide lock that raises a friendly "render already in progress".

## Medium Priority

### M1. Per-sink isolation breaks at finalize for non-SinkError exceptions
- Driver catches only `SinkError` at write (`green_compositor.py:288`) and finalize (`:304`). But finalize paths can raise raw exceptions: `_WebmSink.finalize` `stdin.close()` → `BrokenPipeError` if ffmpeg died after the last write (buffered flush); `_GreenSink.finalize` fallback mux `run(...)` (`:121`) → `FFmpegError` if the *fallback* also fails (disk full); `writer.close()` (`:107`) → OSError; `os.replace` (`:124`, `:215`) → PermissionError (Windows AV lock on tmp).
- Any of these escapes to the `except BaseException` handler (`:311`) → aborts ALL sinks and raises — in `both` mode a webm finalize hiccup discards the green result reporting (green file may already be renamed on disk but the pipeline reports failure), and with H1 the chunked segments are then unrecoverable.
- Fix: catch `Exception` (wrap into a warning) in the driver's finalize loop, or have sinks convert internal finalize failures to `SinkError`. Mid-stream isolation is fine; only the finalize boundary leaks.

### M2. Same-process retry cannot adopt its own crashed run
- `run_manifest.py:216-217`: owner alive → refuse. If a chunked render fails with an exception (e.g. `SegmentRenderError` frame-count mismatch, disk check) but the app process survives, `owner_pid` is *our own live pid* — clicking Generate again refuses adoption AND `purge_stale_runs` skips it (alive owner) → silent full re-render into a second run dir; the valid segments sit unusable until app restart.
- Fix: treat `owner_pid == os.getpid()` as adoptable (safe once H2's serialization is in place).

### M3. No fingerprint exhaustiveness guard for future RenderConfig fields
- `_FINGERPRINT_FIELDS` (`run_manifest.py:34-36`) is a frozen tuple. A future render-affecting field (phase 5/6 knobs) silently stays out of the fingerprint → old segments adopted under different render settings → mixed-look output with no error. Current classification is correct (checked all 18 fields; `batch_size`/`sequential_vram` are perf-only, rest composite-side).
- Fix: unit test asserting `set(_FINGERPRINT_FIELDS) | KNOWN_NON_RENDER_FIELDS == {f.name for f in dataclasses.fields(RenderConfig)}` so any new field forces an explicit decision.

### M4. Two-instance adoption race (TOCTOU on owner_pid)
- `find_resumable` checks `pid_alive` then the adopter writes `owner_pid` later (`pipeline.py:258-259`). Two app instances resuming in the same window both pass the check → both render into the same run dir → manifest last-writer-wins clobbering, duplicate GPU work, and the first finisher `rmtree`s the run dir while the other's `iter_segment_frames` may still be reading.
- Reality check: local single-user app on 127.0.0.1; likelihood low. Cheapest hardening: `O_EXCL` lock file in the run dir at adoption, or re-read the manifest after writing `owner_pid` and back off if it isn't ours.

## Low Priority

- **L1 Corrupt-manifest crashes instead of skip**: `int(m.get("owner_pid", 0))` (`run_manifest.py:216,241`) raises on a non-numeric value; `validate_segments` KeyErrors on missing `frames_expected`/`path` types. `load_manifest` validates only top-level keys — one hand-mangled manifest bricks all resume/generate attempts until deleted. Wrap per-candidate handling in try/except → treat as non-candidate.
- **L2 Disk estimates**: `_check_disk_for_segments` (`pipeline.py:364-376`) prices segments at *source* resolution even for `preprocess="crop"/"resize"` (output is face_size) → over-estimate → possible false rejection on tight disks. Composite-entry check (`pipeline.py:191-197`) silently no-ops when `count_frames()` failed (`total=None`) — acceptable for a soft check, worth a comment.
- **L3 ffmpeg upgrade between crash and resume** breaks sha match (Lavf header tag in the decoded wav) → silent full re-render, and the old run dir is *never* purged (identity no longer matches) — combined with no TTL cleanup for non-matching incomplete runs, temp/ can accumulate multi-GB orphans. Consider `-fflags +bitexact` on `prepare_audio` output (makes the wav version-independent) and/or an age-based purge.
- **L4 `chunk_seconds` has no sane floor** (`chunked_facerender.py:54`, `max(1, …)`): `chunk_seconds=1` → 25 kept frames with 26 halo frames → >2x render cost. Config-only knob (not in UI); clamp to e.g. ≥10s or warn.
- **L5 `cfg.fps` footgun on the chunked path**: segments render at SadTalker's hardcoded 25fps but the compositor encodes at `cfg.fps` (`pipeline.py:332`) — `fps≠25` yields silent audio desync + `-shortest` truncation. Not UI-exposed; assert 25 or derive fps from segment metadata.
- **L6 Partial-success purge**: in `both` mode, if the green sink died and only webm succeeded, the run dir is still purged (`pipeline.py:212`) — retrying the green output costs a full re-render. Debatable trade-off; consider keeping the run dir when `warnings` contains a sink failure.
- **L7 Stale docs**: `docs/codebase-summary.md` still documents `composite_to_green` and the 120s cap. Phase 7 scope — flagging so it isn't dropped.
- **L8 Segment decode at composite is not re-verified**: `iter_segment_frames` trusts files verified minutes (fresh) or at validation time (resume); a decode-short segment would silently shorten the video (`-shortest` masks it). Negligible residual risk; the frame-count E2E asserts cover regressions.

## Task-specific questions answered

- **wav re-decode sha1 stability**: bit-stable (empirically verified, see above). Not a HIGH; resume matching works.
- **purge on resume**: both old run dir and new work dir cleaned (`pipeline.py:212-215`).
- **plan_segments kept<2·HALO / rows<HALO**: mathematically correct (halos clamp; window-equivalence test covers kept≥1); only wasteful (L4).
- **slice_coeff_mat loadmat per segment**: (15000,70) float64 ≈ 8.4MB × ~12 loads — negligible; acceptable as-is.
- **Sink lifecycle**: first-frame prep, abort-on-partial-finalize, generator close, all-sinks-dead path all verified correct; write-after-death impossible (sink removed before abort); `abort()` idempotent; stderr handle closed on `__init__` failure and double-close safe. Only gap is M1 (finalize non-SinkError).
- **fingerprint drift**: M3. **resume cfg reconstruction**: sound — JSON round-trip preserves all fingerprinted types; list→tuple and Path coercion handled in `app.py:94-98`; removed fields filtered, new fields default.

## Phase criteria status

Phase 3: all implementation criteria **PASS** (both-mode E2E + unit spies + sink-death tests + unmodified v1 E2E, per given results and code reading). CapCut manual import remains open (user action, phase 7).

Phase 4:
| Criterion | Status |
|---|---|
| 600s @256 no OOM, flat VRAM/RAM | PENDING — render in progress on GPU |
| Seeded chunked-vs-full < 2/255 | PASS (0.460 mean / 0.777 worst, 750 frames) |
| Frame count == round(dur×25)±1, original audio, ±0.3s | PASS via E2E asserts (seeded ±1; duration in webm E2E; kill-test rerunning) |
| Kill+resume re-renders only pending | PARTIAL — harness fixed, rerun in progress; mechanism code-verified; NOTE H1 hole for crash-in-composite |
| Coeff corruption → new run id | PASS (unit + `pipeline.py:273-275`) |
| Cap without ffprobe | PASS (fail-closed units) |
| ≤120s single-shot unchanged | PASS (v1 E2E green, dispatch at `pipeline.py:168`) |
| Composite-only knob keeps fingerprint | PASS (unit) |
| No stray uuid temps after crash | PARTIAL — chdir guard verified; no explicit crash-case assertion; H2 can defeat the guard |

## Recommended actions (priority order)

1. H1: make complete-but-unconsumed runs resumable (remove `is_complete` skip in the two finders); re-run kill/resume E2E variant that kills during composite.
2. H2: serialize Generate/Resume with a shared `concurrency_id` or process-wide render lock.
3. M1: catch `Exception` in the driver finalize loop (convert to warning; keep surviving outputs).
4. M2: allow same-pid adoption (after #2).
5. M3: fingerprint exhaustiveness test.
6. L1-L8 as time permits; L3's `+bitexact` is cheap and makes resume robust across ffmpeg upgrades.

## Metrics

- Fast suite: 58 passed, 6 skipped, 21.6s (re-run this session).
- E2E: webm both-mode PASS, v1 contract PASS, seeded chunked-vs-full PASS (given); kill/resume rerunning; 600s render in progress.
- Lint/type gates: none configured in repo (no ruff/mypy config) — N/A.

## Unresolved questions

1. Should complete-but-uncomposited runs survive `purge_stale_runs` even after H1's finder fix, if the user changes a *render-affecting* knob in between? (Current identity-keyed purge would correctly leave them, but they then linger forever — ties into L3 TTL question.)
2. Is `both`-mode partial success (webm only, green failed) worth keeping segments for retry (L6), or is the warning-and-purge behavior the accepted trade-off?
3. Confirm intended gradio concurrency semantics: was serialization across the two buttons assumed (it held in v1 with a single button)?
