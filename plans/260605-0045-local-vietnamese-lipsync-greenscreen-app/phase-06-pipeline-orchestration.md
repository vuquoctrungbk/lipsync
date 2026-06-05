# Phase 06 — Pipeline Orchestration

## Context Links

- Plan overview: [plan.md](plan.md)
- Depends on: Phase 02 (device/dtype/config/free_vram), Phase 03 (audio), Phase 04 (animate), Phase 05 (matte/composite/encode)

## Overview

- **Priority:** P1
- **Status:** pending
- **Description:** A single `run_pipeline(image_path, audio_path, cfg, progress=None) -> PipelineResult` that wires all stages in the locked order, owns the temp-file lifecycle, enforces VRAM-aware sequencing (animate -> free -> matte), reports progress, and returns the final green MP4 path. This is the one entry point the Gradio UI (Phase 07) and E2E tests (Phase 08) call.

## Key Insights

- Orchestration is the single place that guarantees the sequential model-load contract: SadTalker and BiRefNet never coexist on the 12 GB GPU. Each stage calls `free_vram()` on exit.
- Temp lifecycle: one per-run working dir under `temp/run_<uuid>/`; cleaned on success (configurable keep-on-debug). Prevents disk bloat across many runs.
- Progress callback is engine-agnostic (`progress(frac, msg)`) so Gradio can show a bar without the pipeline importing gradio (DRY/decoupling).
- Error handling: each stage raises typed errors with friendly messages; orchestrator catches, cleans temp, re-raises a `PipelineError` the UI can show.

## Requirements

### Functional
- `run_pipeline(...)` executes: ensure_dirs -> device/precision detect -> audio preprocess -> SadTalker animate -> free_vram -> BiRefNet matte -> green composite -> encode -> return `PipelineResult{final_video, duration_s, timings, vram_peaks}`.
- Stage timing + VRAM peak captured per stage for Phase 08 perf numbers.
- Cleanup temp on success; keep on failure if `cfg.debug` for diagnosis.
- Optional `keep_intermediate` flag (default False).

### Non-functional
- `pipeline.py` < 200 LoC (delegates real work to stage modules; it only sequences).
- No GPU/model code here — pure orchestration (DRY: reuses Phases 03–05).
- Deterministic temp dir per run; thread/queue-safe (Gradio serializes via queue anyway).

## Architecture

```
src/lipsync/
└── pipeline.py     # run_pipeline() : sequences stages, temp + VRAM lifecycle, progress, errors
```

Data flow:
```
(image, audio, cfg)
  -> ensure_dirs + run_dir = temp/run_<uuid>
  -> get_device_and_precision()
  -> preprocess_audio(audio) ............................ [0.0-0.1]
  -> animate(image, wav, cfg) -> frames ................. [0.1-0.6]  then free_vram()
  -> matte_frames(frames) -> alpha ..................... [0.6-0.85] then free_vram()
  -> composite_green(frames, alpha, green) -> composited [0.85-0.9]
  -> encode_video(composited, wav, fps) -> final.mp4 ... [0.9-1.0]
  -> move final.mp4 to outputs/; cleanup run_dir
  -> PipelineResult
```

## Related Code Files

**Create:**
- `src/lipsync/pipeline.py`

**Modify:** none (imports Phases 02–05 modules).

**Delete:** none.

## Implementation Steps

1. **Define `PipelineResult` + `PipelineError`.** Result: `final_video: Path`, `duration_s: float`, `timings: dict[str,float]`, `vram_peaks: dict[str,float]`. Error: wraps stage name + friendly message + original exception.

2. **Per-run working dir.** `run_dir = config.TEMP / f"run_{uuid4().hex}"; run_dir.mkdir(parents=True)`. All intermediate (wav, frames, alpha, composited) live under it.

3. **Device/precision once.** `device, dtype = get_device_and_precision()`. Enable TF32/cudnn.benchmark (from Phase 02 helper). Assert `dtype == fp16` when cuda (guard against accidental fp32 OOM); log a warning if cpu.

4. **Stage sequencing with progress + timing.** Wrap each stage:
   ```python
   def _stage(name, frac, fn):
       if progress: progress(frac, name)
       t0 = time.perf_counter()
       out = fn()
       timings[name] = time.perf_counter() - t0
       vram_peaks[name] = vram_report().get("reserved_gb", 0)
       return out
   ```
   Call audio -> animate -> `free_vram()` -> matte -> `free_vram()` -> composite -> encode in order, each via `_stage`.

5. **VRAM-aware sequencing (the contract).** Explicit `free_vram()` calls AFTER animate and AFTER matte. Add an assert/log that allocated VRAM dropped after animate before BiRefNet loads — fail fast with a clear message if not (prevents silent OOM).

6. **Finalize.** Move/copy final mp4 to `outputs/<timestamp>_<slug>.mp4`. If `not keep_intermediate and not cfg.debug`, `shutil.rmtree(run_dir, ignore_errors=True)`.

7. **Error path.** Wrap stages in try/except; on failure, optionally keep `run_dir` for debug, raise `PipelineError(stage, friendly_msg)`. Never leak a raw stack trace to the UI layer (Phase 07 shows `.message`).

8. **Return `PipelineResult`** including timings + vram_peaks (Phase 08 reads these for the perf table).

## Todo List

- [ ] Define `PipelineResult` + `PipelineError`
- [ ] Per-run temp dir creation
- [ ] Device/precision detect + fp16 guard
- [ ] `_stage` wrapper (progress + timing + vram peak)
- [ ] Sequence: audio -> animate -> free -> matte -> free -> composite -> encode
- [ ] Assert VRAM freed between animate and matte
- [ ] Finalize to outputs/ + temp cleanup (keep-on-debug)
- [ ] End-to-end dry run: image + short WAV -> green MP4

## Success Criteria

- One call `run_pipeline(img, wav, cfg)` returns a valid green MP4 end-to-end with no manual steps.
- `timings` and `vram_peaks` populated per stage; logged.
- Temp `run_dir` removed on success; retained on failure when `cfg.debug`.
- VRAM logs prove SadTalker freed before BiRefNet loads (peak never sums both engines).
- `pipeline.py` < 200 LoC; contains no model/ffmpeg logic of its own.

## Risk Assessment

| Risk | Likelihood x Impact | Mitigation |
|------|---------------------|------------|
| Stage leaves models on GPU -> OOM at next stage | Medium x High | Mandatory `free_vram()` + post-animate VRAM assert |
| Temp dir not cleaned -> disk fill | Medium x Medium | rmtree on success; uuid per run; document keep-on-debug |
| Partial failure leaves orphan temp | Low x Low | keep-on-debug only; periodic temp sweep documented in Phase 09 |
| Progress callback coupling to UI | Low x Low | Generic `(frac,msg)` signature; no gradio import here |

## Security Considerations

- Validate inputs exist + decode before starting (defense in depth; UI also validates).
- All temp/output writes confined to `config.TEMP`/`config.OUTPUTS`.
- No `shell=True` anywhere in the chain (inherited from Phases 03/05).

## Next Steps

- Unblocks Phase 07 (Gradio calls `run_pipeline`) and Phase 08 (E2E/perf harness calls it directly).
