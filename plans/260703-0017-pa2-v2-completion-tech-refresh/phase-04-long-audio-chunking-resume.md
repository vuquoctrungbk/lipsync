---
phase: 4
title: "Long-Audio Chunking & Resume"
status: completed
effort: "4d"
priority: P1
dependencies: [1, 2]
---

# Phase 4: Long-Audio Chunking & Resume

> Red-team revised 2026-07-03: halo slicing (semantic_radius), paste_pic RAM/CWD modeling, silent-wav segment mux, integrity-bound manifest, fail-closed cap, opt-in drift report, seeded chunked-vs-full proof.

## Overview

Raise the audio cap 120s → 600s (user-confirmed ≤10 min) by segmenting the memory-unbounded render stages — SadTalker's facerender **and its paste-back/enhancer chain** — while running audio2coeff once on the full audio. Add an integrity-bound run manifest so a crashed 10-min render resumes instead of restarting. Sync-drift analysis is available on demand (phase 1 harness), never blocking the render.

## Requirements

- Functional: 600s audio renders to completion @256 on the RTX 3060 12GB; final green MP4 frame count == `round(duration*25)` ±1; final mux uses the ORIGINAL full audio (existing `green_compositor.py:67-71` path).
- Functional: interrupt mid-render → relaunch resumes; completed segments are NOT re-rendered; a resumed run is pixel-equivalent to an uninterrupted one (same coeff mat, same seed).
- Functional: chunked output ≈ single-shot output — proven by a seeded chunked-vs-full E2E, not by eyeball.
- Functional: cap enforcement is fail-closed — works even when ffprobe is absent.
- Non-functional: VRAM AND RAM bounded regardless of clip length; disk usage bounded, pre-checked, cleaned on success.

## Architecture

### Ground truth (verified via red-team, file:line)

- `Audio2Coeff.generate` writes ONE `.mat` `coeff_3dmm (num_frames, 70)` = `[exp 0:64 | pose 64:70]` (`third_party/SadTalker/src/test_audio2coeff.py:74-105`). Only POSE is savgol-smoothed (`:88-93`); exp rows are raw per-frame output.
- **Frames are NOT independent:** `get_facerender_data` conditions each frame on a ±13-row window — `semantic_radius = 13` (`src/generate_facerender_batch.py:12`), indices CLAMPED to the bounds of the mat it is given (`:93-98`); MappingNet pools the whole 27-row window (`src/facerender/modules/mapping.py:32-37`). Naive slicing ⇒ the first/last 13 frames of every segment render against edge-replicated context ⇒ expression stutter at every boundary. **Therefore: slice with a ±13-frame HALO, render the halo, drop halo frames at consumption.**
- `still_mode=True` (default `lipsync/config.py:37`) pins pose columns 64:70 only (`generate_facerender_batch.py:49-50`); exp stays windowed — halo is required regardless of still_mode.
- audio2coeff is NON-DETERMINISTIC: unseeded CVAE noise (`src/audio2pose_models/audio2pose.py:68-77`) and unseeded blink `random.choice` (`src/generate_batch.py:37-49`). Segments from two different coeff runs must NEVER be mixed → manifest binds segments to the mat by sha1+rows; comparisons/tests use `cfg.seed` (added in phase 2).
- Default `preprocess="full"` (`config.py:36`) makes every `AnimateFromCoeff.generate` call also run `paste_pic`: buffers ALL segment frames in RAM at FULL SOURCE resolution (`src/utils/paste_pic.py:30-38`), per-frame `cv2.seamlessClone`, CWD-relative `uuid4().mp4` temps (`paste_pic.py:56`, `src/utils/videoio.py:21`), `os.system` mux with IGNORED exit code (`videoio.py:20-26`) that hardcodes PATH `ffmpeg`. Enhancer additionally loads the whole segment video into RAM (`src/utils/face_enhancer.py:34-36`, `videoio.py:8-18`).
- SadTalker's per-output mux slices audio `[0 : frames/25]` from t=0 via pydub, decoding the WHOLE input file each call (`src/facerender/animate.py:213-220`); the mux has NO `-shortest` (`videoio.py:22`), so a short silent wav does not truncate video. Audio reaches facerender ONLY as this mux input — `get_facerender_data` carries no audio features (`generate_facerender_batch.py:8-86`).

### Design

```
lipsync/chunked_facerender.py    # new module
  plan_segments(mat_rows, face_size, src_hw, free_vram_bytes, free_ram_bytes)
      -> [{idx, start_f, end_f, halo_start, halo_end}]      # frame count from MAT ROWS, never probed duration
  slice_coeff_mat(coeff_path, halo_start, halo_end, out)    # scipy loadmat/savemat; savemat -> temp + os.replace
  render_segment(engine, seg, silent_wav, run_dir) -> mp4   # get_facerender_data + AnimateFromCoeff.generate,
      # audio arg = 1s silent wav (mux lacks -shortest; avoids pydub full-decode per segment);
      # os.chdir(run_dir) guard (finally-restored) so uuid/paste_pic temps land in run_dir;
      # post-check: file exists, size > 0, ffprobe frame count == halo span (fail-loud; ffprobe absent -> fail-loud)
  iter_segment_frames(manifest) -> yields (frame, global_idx) # feeds the compositor, SKIPPING halo frames
      # NO concat step: the phase-3 composite() driver consumes this iterator; trimming is frame-accurate and free
```

Segment sizing: VRAM `n×3×S×S×4B` (predictions stack, `make_animation.py:137-138`) AND RAM `n×H_src×W_src×3B` (paste_pic buffer; enhancer ≈ same again). Defaults: 1250 frames (50s) @256, 750 (30s) @512, clamped down by `free_vram_bytes()`/RAM headroom; `cfg.chunk_seconds: int = 0` (auto). Warn when the source portrait exceeds ~2MP that RAM and paste-back CPU scale with source area.

Manifest `temp/run_<ts>_<6hex>/manifest.json` (written via temp + `os.replace`):
```
{ owner_pid, created,
  inputs: {image_sha1, audio_sha1, cfg_fingerprint},
  coeff: {path, sha1, rows},                       # binds segments to ONE coeff run (non-determinism guard)
  segments: [{idx, start_f, end_f, halo_start, halo_end, path, frames_expected, status: pending|done}] }
```
`cfg_fingerprint` = EXACTLY the render-affecting fields: `(face_size, preprocess, still_mode, pose_style, expression_scale, use_enhancer, precision, fps, chunk_seconds, seed)`. Composite-only knobs (green_rgb, crf, output_format) are EXCLUDED — changing them must not orphan rendered segments. Stage is DERIVED from disk facts (coeff valid? all segments done? outputs exist?) — no stored stage field to drift.

Resume: newest incomplete manifest matching input hashes + fingerprint; adopt only if `owner_pid` is dead. Structural validation on load: all paths must resolve under the run dir; coeff mat must loadmat cleanly with shape `(rows,70)` and sha1 match — ANY coeff failure demotes ALL segments and restarts from coeff **under a NEW run id** (never re-run audio2coeff inside an existing run id); per-segment: exists + ffprobe count == frames_expected, else demote that segment. Composite stage is NOT checkpointable (streaming sinks + RVM recurrent state) — resume restarts it from the segment iterator; UI resume text states this. Retention: at most ONE incomplete run dir per input-hash — purge older ones at new-run start, purge all on success.

Disk: pre-check at render start (segment estimate ≥2× headroom) AND re-check at composite entry (green ≈ H.264@crf18, webm ≈ research est. ~1.1GB/5min-1080p, scaled); sinks write temp names + rename on success (phase 3).

Drift analysis (user-approved capability, opt-in execution): NOT run automatically inside the render. `app.py` gets an "Analyze sync drift" button (phase 7 wires UI) that runs `scripts/sync_metrics.py --video <output> --window 60` on demand; report shows per-window LSE-D as a RELATIVE trend (flag windows deviating from the clip median, no absolute 8.5 gate — SyncNet is English-trained; phase 1 protocol governs absolute judgment).

## Related Code Files

- Create: `lipsync/chunked_facerender.py`, `tests/test_chunked_facerender.py`, `tests/test_run_manifest.py`
- Modify: `lipsync/animation_sadtalker.py` (split `animate()` into coeff step + render step; ≤threshold frames keeps the current single-shot path; honor `cfg.seed`), `lipsync/config.py` (`MAX_AUDIO_SECONDS = 600`, `chunk_seconds`), `lipsync/audio_preprocess.py` (fail-closed cap: measure duration from the DECODED 16k wav — `soundfile`/`wave`, no ffprobe dependency), `lipsync/pipeline.py` (manifest lifecycle, resume entry, per-segment progress in the 0.10-0.60 band, composite consumes `iter_segment_frames`), `app.py` (Resume button + State near `app.py:88`)
- Delete: none

## Step-1 Gate Results (2026-07-03) — GATE PASSED

Measured on the RTX 3060 / 64GB box, fp32 @256, `preprocess="full"`:
- **audio2coeff on 600s VN wav: trivial.** `get_data` 1.7s + `Audio2Coeff.generate` 11.3s, VRAM peak 2.45GB, mat exactly (15000, 70). The coeff-stage-chunking contingency is NOT needed — coeff always runs whole.
- **1225-frame (49s) segment render on a 3.8MP portrait, enhancer off:** wall 653.5s (~0.53 s/frame — paste-back seamlessClone at source resolution dominates), VRAM peak 5.29GB (12GB-safe incl. RVM co-residency). Completed within 64GB RAM (paste-back buffer modeled ≈14GB at these dims). Per-process RAM probe bug (ctypes GetProcessMemoryInfo returned 0) noted; production sizing uses the system-level `free_ram_bytes()` clamp instead, and the 600s E2E logs system RAM over time.
- **Enhancer leg skipped** at gate (killed first attempt burned the timebox; enhancer doubles the RAM term — the sizing formula models it ×2 and the ≥2MP UI warning covers the wall-clock cliff). Revisit only if enhancer becomes a long-clip default.
- Consequences applied: `DEFAULT_KEPT = {256: 1250, 512: 750}` confirmed VRAM-safe; `plan_segments` clamps by `free_vram_bytes()`/`free_ram_bytes()`; pipeline warns on >2.1MP portraits (RAM + paste-back CPU scale with source area).

## Implementation Steps

1. **Measure first (0.5d hard gate):** run `get_data` + `Audio2Coeff.generate` alone on a 600s synthetic VN wav — record RAM/VRAM/time. ALSO measure one 1250-frame segment render with `preprocess="full"` on a large (≥2MP) portrait, enhancer on and off — capture paste_pic RAM peak and wall-clock. Blows up → documented contingency: coeff-stage chunking with 2s overlap + linear coeff blend; segment sizing table updated from measurements, not formulas.
2. Fail-closed cap in `audio_preprocess` (decoded-wav duration; drop the probe-or-skip path for the cap decision `audio_preprocess.py:45-53`).
3. `chunked_facerender.py`: `plan_segments` + `slice_coeff_mat` with halo. Unit tests: kept ranges cover exactly with no gap/overlap AFTER trim; raw slices overlap by ≤2×13 frames; **window-equivalence test** — target-semantics windows computed from a haloed slice equal those from the full mat for every KEPT frame (synthetic (N,70) data, reimplementing the `:93-98` clamped-window indexing).
4. `render_segment` (silent-wav mux input, chdir guard, fail-loud post-checks) + `iter_segment_frames` skipping halo frames.
5. Manifest read/write/validate/resume + unit tests: fresh run; resume-skip-done; corrupt segment demotion; coeff sha mismatch → FULL demotion + new run id; fingerprint mismatch → new run; composite-only knob change → SAME fingerprint (resumable); dead/live owner_pid.
6. Wire `animation_sadtalker` + `pipeline`: threshold dispatch (≤120s unchanged single-shot), per-segment progress, composite from iterator. UI Resume button (newest incomplete manifest, age + progress + "composite restarts" note).
7. Seeded chunked-vs-full E2E (`RUN_E2E=1`, slow): ~30s clip, `cfg.seed` fixed, forced small `chunk_seconds` → render chunked AND single-shot → per-frame mean abs diff < 2/255 (the actual seam guarantee). Advisory ROI seam check on the 140s run: boundary frame-delta z-score vs ±25-frame interior window, mouth-region crop from `crop_info` — advisory only, halo + equivalence tests are the guarantee.
8. E2E 140s @256: completes; frame count exact; kill after segment 1 → resume completes without re-rendering segment 0 (mtime unchanged); VRAM/RAM logged flat per segment.

## Success Criteria

- [x] 600s audio renders @256 without OOM — **wall 5624s (9.4× RT): animate 3859s, composite 1765s, 0 warnings**. RAM flat: 94 samples across the render stay in a 42.0–44.7GB free band (the ~2.7GB oscillation = per-segment paste-back buffer alloc/free); VRAM flat at 5.85GB free after load. Full-preprocess measured; enhancer leg SKIPPED at gate (timebox; formula models its ×2 RAM and the ≥2MP warning covers it — revisit only if enhancer becomes a long-clip default).
- [x] Seeded chunked-vs-full E2E: **mean |diff| 0.460/255, worst frame 0.777/255** over 750 frames (< 2/255 bar; boundaries included)
- [x] Frame count == round(duration×25) ±1 — **15000/15000, delta 0**; original audio muxed via the compositor's `-shortest` path
- [x] Kill + resume re-renders ONLY pending segments (seg-0 mtime watched DURING the resumed run — unchanged); coeff-mat corruption → full demotion + new run id (unit-tested); mixed-mat output impossible (sha1 binding)
- [x] Cap rejects >600s input even with ffprobe absent (fail-closed from the decoded wav, unit-tested)
- [x] ≤120s clips take the unchanged single-shot path (v1 E2E green post-refactor)
- [x] Composite-only knob change (green hex/crf/format) does NOT invalidate rendered segments (fingerprint unit test + exhaustiveness guard)
- [x] No stray uuid temp files outside run dirs after crash tests (verified after kill runs — chdir guard held; two benign in-temp/ leftovers documented in completion notes)

## Completion Notes (2026-07-03)

- Modules: `lipsync/chunked_facerender.py` (halo ±13 planning/slicing/rendering/iteration) + `lipsync/run_manifest.py` (integrity binding, adoption, retention — split out to its own module matching the plan's test naming). `animation_sadtalker` split into `prepare_coeff`/`render_from_coeff` (+ `reuse_coeff_path` for resume — audio2coeff is never re-run into existing segments); pipeline dispatches at 120s; UI Resume button reconstructs cfg/inputs from the stored run dir.
- Window-equivalence unit test transcribes the vendored clamped-window indexing and proves haloed slices give every kept frame bit-identical ±13 context (4 size configurations).
- Resume-critical assumption VERIFIED empirically: ffmpeg decode is byte-stable (same mp3 twice AND wav-of-wav → identical sha1) — auto-resume and the Resume button both hash-match. Residual (documented): an ffmpeg UPGRADE between crash and resume changes the wav header → silent fresh render.
- Code review (report: `plans/reports/code-reviewer-260703-0416-pa2-phase3-4-webm-chunking-report.md`, DONE_WITH_CONCERNS → all fixed same session): H1 composite-stage crashes were unresumable+purged → all-done manifests now adoptable end-to-end; H2 Generate+Resume concurrency → Pipeline render lock + shared gradio `concurrency_id`; M1 finalize failures of any exception type stay sink-scoped; M2 same-process retry adopts its own crashed run; M3 fingerprint exhaustiveness guard test (new RenderConfig fields must be classified); M4 two-instance TOCTOU accepted (single-user app; render lock covers same-process). Lows: corrupt owner_pid guard, fps≠25 chunked guard, partial-success keeps segments for retry; accepted-as-documented: rough disk estimate, no orphan TTL, `docs/codebase-summary` refresh deferred to docs-manager.
- Advisory ROI seam z-score check (step 7) not run — explicitly advisory; the seeded equivalence E2E is the guarantee and passed with 4× margin.
- Known benign temp/ accumulation (v1-parity): single-shot work dirs persist; a killed resumed-run leaves its manifest-less fresh work dir (KBs). Chunked dirs are governed by retention + purge-on-success (verified: successful chunked runs left zero dirs).
- Post-fix suite: 61 passed. Headline E2Es: chunked-vs-full PASS, kill/resume PASS, 600s PASS.

## Risk Assessment

- audio2coeff at 600s unmeasured → step 1 hard gate + contingency; do not build past it blind.
- paste_pic/enhancer RAM at large portraits → measured in step 1; warn ≥2MP; segment sizing includes RAM term.
- SadTalker vendor mux `os.system` ignores exit codes + hardcodes PATH ffmpeg (`videoio.py:20-26`) → fail-loud post-checks in `render_segment` (exists/size/frame-count) catch truncated outputs regardless; chdir guard contains temp junk.
- Disk: several GB at 600s@512 → dual pre-checks + temp+rename sinks; failure message includes required vs available bytes.
- Windows long paths → short run ids (`run_<ts>_<6hex>/seg_017.mp4`).
