# Phase 07 — Gradio UI

## Context Links

- Plan overview: [plan.md](plan.md)
- Architecture report: `plans/reports/researcher-260605-0035-app-architecture-inference-optimization-report.md` (sec 1.1 Gradio skeleton, queue)
- Depends on: Phase 06 (`run_pipeline`), Phase 02 (`AppConfig`)

## Overview

- **Priority:** P2
- **Status:** pending
- **Description:** A thin Gradio web app (`app.py`) exposing image + audio inputs, a video output, a progress bar, and a small settings panel (green color, resolution, codec, enhancer toggle). It builds an `AppConfig` from UI values and calls `run_pipeline`. Local single-user, bound to 127.0.0.1.

## Key Insights

- Gradio is the locked UI choice. Native `gr.Image`/`gr.Audio`/`gr.Video` widgets + built-in queue serialize GPU jobs (protects the 12 GB card from concurrent runs) — exactly the single-user fit.
- `app.py` must stay THIN: parse inputs -> build config -> call `run_pipeline` -> return video. No model/ffmpeg logic in the UI (DRY; all in Phases 03–06).
- Progress: Gradio's `gr.Progress` maps cleanly to the pipeline's `(frac, msg)` callback.
- Bind to `127.0.0.1` only (not 0.0.0.0) — RDP user accesses locally; do not expose on LAN.
- Settings are minimal (YAGNI): green color picker, resolution (256/512), codec (H.264/H.265), enhancer toggle, optional pose-style/face-region advanced. No account system, no history DB.

## Requirements

### Functional
- Inputs: `gr.Image(type="filepath")` (portrait), `gr.Audio(type="filepath")` (Vietnamese voice).
- Settings: green color (`gr.ColorPicker`, default #00B140), resolution radio (256/512), codec radio (H.264/H.265), enhancer checkbox (default off).
- Button -> calls `predict()` -> `run_pipeline` with progress -> returns `final_video` to `gr.Video`.
- Error display: catch `PipelineError`, show `gr.Error`/`gr.Warning` with the friendly message.
- Bound to `127.0.0.1:7860`, `show_error=True`, queue enabled.

### Non-functional
- `app.py` < 150 LoC.
- No new pip deps (gradio from Phase 01).
- Startup < a few seconds (models load lazily inside pipeline on first run, not at import).

## Architecture

```
Lipsync/
└── app.py     # Gradio Blocks: inputs + settings -> predict() -> run_pipeline -> video
```

Data flow:
```
UI widgets -> predict(img, audio, green_hex, res, codec, enhancer, gr.Progress)
   -> hex_to_rgb(green_hex); build AppConfig(...)
   -> run_pipeline(img, audio, cfg, progress=lambda f,m: gr_progress(f, desc=m))
   -> return result.final_video  (gr.Video)
```

## Related Code Files

**Create:**
- `app.py`

**Modify:** none.

**Delete:** none.

## Implementation Steps

1. **`hex_to_rgb` helper** to convert `gr.ColorPicker` hex -> `(r,g,b)` ints for `AppConfig.green_rgb`. Validate range.

2. **`predict()` function:**
   ```python
   def predict(image, audio, green_hex, resolution, codec, use_enhancer, progress=gr.Progress()):
       if not image or not audio:
           raise gr.Error("Please provide both a portrait image and an audio file.")
       cfg = AppConfig(green_rgb=hex_to_rgb(green_hex),
                       resolution=int(resolution), face_region=int(resolution),
                       codec="libx265" if codec=="H.265" else "libx264",
                       use_enhancer=bool(use_enhancer))
       def cb(frac, msg): progress(frac, desc=msg)
       try:
           res = run_pipeline(image, audio, cfg, progress=cb)
       except PipelineError as e:
           raise gr.Error(e.message)
       return str(res.final_video)
   ```

3. **Build the Blocks UI** (per architecture skeleton): title, two-column inputs (image | audio), a collapsible "Settings" accordion (color/resolution/codec/enhancer), generate button, video output. `queue=True` on the click.

4. **Launch guarded:**
   ```python
   if __name__ == "__main__":
       AppConfig  # ensure import
       demo.queue().launch(server_name="127.0.0.1", server_port=7860, show_error=True)
   ```

5. **Lazy model loading.** Ensure `run_pipeline` loads models on call, not at import, so the UI starts fast and a bad GPU state surfaces at run time with a clean error (not at launch).

6. **Friendly empty-state + examples (optional).** A short markdown note: "Single front-facing portrait + Vietnamese voice -> green-screen talking head." Optionally a `gr.Examples` with a bundled sample portrait (no NC assets).

## Todo List

- [ ] Write `hex_to_rgb` helper
- [ ] Write `predict()` calling `run_pipeline` with progress + error mapping
- [ ] Build Blocks UI (inputs + settings accordion + button + video)
- [ ] Bind to 127.0.0.1:7860, queue enabled, show_error
- [ ] Confirm lazy model load (fast startup)
- [ ] Manual run: drag image+audio, watch progress, get green video

## Success Criteria

- `python app.py` starts a local server in a few seconds; opens at `127.0.0.1:7860`.
- Dropping a portrait + Vietnamese audio and clicking Generate produces the green MP4 in the video pane.
- Progress bar advances through audio/animate/matte/encode stages.
- Bad input (missing file, no face) shows a friendly `gr.Error`, no raw stack trace.
- Settings (green color, resolution, codec, enhancer) take effect in output.
- `app.py` < 150 LoC; contains no model/ffmpeg logic.

## Risk Assessment

| Risk | Likelihood x Impact | Mitigation |
|------|---------------------|------------|
| Concurrent runs OOM the GPU | Low x High | Gradio queue serializes jobs (single-user); document do-not-disable-queue |
| Long run looks "frozen" | Medium x Low | Progress callback with per-stage messages |
| ColorPicker hex parse edge cases | Low x Low | `hex_to_rgb` validation + default fallback |
| Port 7860 in use | Low x Low | Configurable port; print bound URL |
| Gradio 4.x API drift | Low x Medium | Pin gradio==4.44.1 (Phase 01); skeleton uses stable Blocks API |

## Security Considerations

- Bind `127.0.0.1` ONLY — never `0.0.0.0`/`share=True` (no public tunnel; keeps it off the network).
- Validate uploaded files decode (image via PIL, audio via ffprobe in Phase 03) before processing.
- Output files served from `outputs/`; do not expose arbitrary filesystem paths.

## Next Steps

- Unblocks Phase 08 (manual + scripted runs go through this UI and `run_pipeline`).
- Feeds Phase 09 (the `.bat` launcher starts `app.py`).
