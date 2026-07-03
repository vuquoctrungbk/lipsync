# Code Review — PA2 v2 post-fix + Phase-7 UI (adversarial re-review)

**Reviewer:** Opus 4.8, first-person adversarial. **Date:** 2026-07-03.
**Scope (the delta the two prior subagent reviews did NOT cover):**
1. Fixes applied AFTER the P3+P4 review (run_manifest H1, pipeline render-lock/all-done-adopt/disk/fps, green_compositor finalize broadening).
2. Phase-7 `app.py` UI — presets, engine dropdown, commercial_safe, Resume button, `resume_render`, `analyze_drift` — **never adversarially reviewed until now.**

**Verification (fresh):** `py_compile` clean on all 10 changed/new modules; fast suite **61 passed / 6 skipped** (112s). E2E flavors green from the implementation session (v1-contract, webm both-mode, seeded chunked-vs-full 0.460/255, kill/resume, 600s).

## Prior-review High/Medium fixes — VERIFIED CORRECT

Read the current code, not the claim:
- **H1 (all-done runs unresumable + silently purged):** FIXED. `find_resumable` + `latest_resumable` no longer skip complete manifests; `_owner_blocks_adoption` gates on liveness only. Segment loop no-ops on all-done → composite reruns. Test-covered (`test_find_resumable_matches_and_respects_owner` now asserts all-done IS resumable; green).
- **H2 (Generate/Resume concurrent render):** FIXED, defence-in-depth. `Pipeline._render_lock` non-blocking acquire with correct acquire/try/finally-release (no leak, no deadlock — verified no re-entrant `run()` path; `threading.Lock` safe) + shared gradio `concurrency_id="gpu-render"` limit 1 across generate/resume/drift.
- **M1 (finalize failures abort all sinks):** FIXED. Finalize loop is `except Exception` sink-scoped; other sinks' finished outputs survive.
- **M2 (same-process retry can't adopt own crashed run):** FIXED + test (`test_own_dead_run_is_adoptable_by_same_process`).
- **M3 (fingerprint exhaustiveness):** guard test added; classifies all 18 RenderConfig fields (10 fingerprint + 8 composite-only). Green.

## New findings (all Low / no Highs, no solid Mediums)

| # | Sev | File:line | Issue | Verdict |
|---|-----|-----------|-------|---------|
| L1 | Low (pre-existing) | `pipeline.py:227` | Single-shot (`≤120s`) `work=temp/run_*` dir was NEVER cleaned (`purge_run_dir` set only on the chunked branch) → temp/ grew unbounded over many short renders. Same pattern existed in v1 (`3786a12`), not a regression. **FIXED:** `work` now rmtree'd on full success for both paths; chunked partial-success still keeps segments for retry. Suite green. | FIXED |
| L2 | Low | `app.py:86,125` | `generate`/`resume_render` validated inputs with `gr.Error` but let render exceptions (disk-full, "another render running", encoder-missing) propagate as an unstyled trace. **FIXED:** `run()` wrapped → `raise gr.Error(f"{type}: {e}") from e` (clean toast, server trace preserved via `from`). Input-validation gr.Errors sit before the try, unaffected. | FIXED |
| L3 | Low (unreachable) | `green_compositor.py:282-283` | A later frame differing from frame-0 is only CROPPED (`frame[:h,:w]`), never padded; a SMALLER frame would desync the fixed-`-s WxH` webm pipe. Unreachable — SadTalker output is uniform per video and per segment. | DEFER (note only) |
| L4 | Low | `green_compositor.py` sinks | Hard kill (not caught exception) mid-composite orphans a `*.tmp.mp4/.tmp.webm` in `outputs/`; `abort()` only cleans on caught failures. | DEFER |
| N1 | Note | `run_manifest.py:34` | `batch_size` classified composite-only (resume-safe). Defensible (not UI-exposed; facerender output is batch-invariant) but it IS render-plumbing — flagged for awareness. | — |
| N2 | Note | `app.py:24-28` | `_PIPE` lazy init unlocked; safe ONLY because `concurrency_limit=1` serializes first init. Latent if concurrency config changes. | — |

## Adversarial scenarios that PASSED (no defect)

- Resume `cfg` reconstruction: JSON round-trip (green_rgb list→tuple restored, output_dir str→Path, unknown/missing fields filtered by `__dataclass_fields__`) — sound.
- All-done adoption on a fresh Generate re-composites identical frames (reuses pinned coeff — MORE deterministic, not less); at most one such "surprising" reuse before purge-on-success.
- Two-app-instance theft: live foreign `owner_pid` blocks adoption (`pid_alive` True) — no run stealing.
- wav sha1 resume assumption: independently re-confirmed byte-stable (mp3→wav twice + wav→wav all identical sha1) — auto-resume and Resume-button both match.
- Render lock: no acquire-without-release, no deadlock, no re-entrancy.
- 12 UI inputs ↔ 12 `generate` params: order verified exact.

## Unresolved questions

- L1/L2 are trivial one-line hardening — apply now, or leave for a follow-up? (Recommend apply now; both are cheap and user-facing.)
- CapCut manual import of a real `lipsync_alpha_*.webm` still unverified (user action; the encode + alpha assertions pass programmatically).
