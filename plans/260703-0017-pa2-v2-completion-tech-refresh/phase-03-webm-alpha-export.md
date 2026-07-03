---
phase: 3
title: "WebM Alpha Export"
status: completed  # CapCut manual import check pending (user action, docs in phase 7)
effort: "1d"
priority: P2
dependencies: [2]
---

# Phase 3: WebM Alpha Export

## Overview

Add true-alpha output for CapCut: WebM VP9 `yuva420p` (user-confirmed editor; ProRes dropped — CapCut can't import it, Adobe alpha bugs irrelevant now). Green MP4 stays the default/preview; `both` mode mattes once and feeds both writers.

## Requirements

- Functional: `cfg.output_format` in {`green_mp4` (default), `webm_alpha`, `both`}; `both` runs the matte loop ONCE.
- Functional: WebM has audio (Opus) and correct duration; corner pixels transparent.
- Non-functional: memory-flat (frame-streaming, same as current compositor); VP9 encode slowness (~0.5x realtime, from research Topic A) is accepted and surfaced in progress text.

## Architecture

Attach at the existing per-frame loop (`lipsync/green_compositor.py:46-59` — reads via `imageio.get_reader`, even-dim crop at `:50-52`, alpha at `:54`; in phase 4 the input becomes the segment-frame iterator). RGBA is piped to ffmpeg stdin — chosen for explicit control of `yuva420p`, muxing, and error surfaces:

```
ffmpeg -y -hide_banner -nostats -loglevel error
       -f rawvideo -pix_fmt rgba -s {W}x{H} -r {fps} -i pipe:0
       -i {audio} -map 0:v -map 1:a
       -c:v libvpx-vp9 -pix_fmt yuva420p -crf 33 -b:v 0 -row-mt 1
       -c:a libopus -shortest {out}.webm     # stderr -> temp FILE, never PIPE
```

Replace `composite_to_green` with a single streaming driver: `composite(frames, matte, cfg, audio, out_dir) -> (outputs: dict[str, Path], warnings: list[str])` with per-format frame sinks (green sink = current imageio writer + mux step, PRESERVING its deliberate silent-video fallback `green_compositor.py:72-74` as a warning; webm sink = ffmpeg rgba pipe with in-line audio). Driver owns `matte.reset_clip()` at start + `finally` clear (phase 2 single-owner rule). **Per-sink failure isolation:** a failing sink closes, lands in `warnings`, remaining sinks keep streaming — a webm mux error must never destroy the green output of a multi-hour render. Sinks write temp names, rename on success. `Pipeline.run` contract (ADDITIVE — `tests/test_pipeline_e2e.py:33` and `app.py:61` pass unmodified in default mode): `{"output": <green mp4 path; or the webm when green not requested>, "outputs": {format: path}, "warnings": [...], "timings": ..., "vram": ..., "device": ...}`. Startup capability check: `ffmpeg -encoders` must list `libvpx-vp9` (system ffmpeg 8.0 has it; imageio-ffmpeg fallback binary may NOT — fail with a clear message naming the ffmpeg actually resolved by `ffmpeg_utils.ffmpeg_exe()`).

## Related Code Files

- Create: `tests/test_webm_alpha_e2e.py`
- Modify: `lipsync/green_compositor.py` (sink refactor + webm pipe), `lipsync/config.py` (`output_format`), `lipsync/pipeline.py` (return multiple outputs + progress text), `lipsync/ffmpeg_utils.py` (`has_encoder(name) -> bool`), `app.py` (Radio "Output format": Green MP4 / WebM alpha (CapCut) / Both; `gr.Video` can't preview alpha-webm → add `gr.File` download slot next to `app.py:90`)
- Delete: none

## Implementation Steps

1. `ffmpeg_utils.has_encoder("libvpx-vp9")` (cached, parses `ffmpeg -encoders`); pipeline raises friendly error if webm requested without it.
2. Sink refactor in compositor: REPLACE `composite_to_green` outright (grep-verified at plan time: single production caller in `pipeline.py`; a compat wrapper would be dead weight) and update that call site. NOTE (post-P2): `tests/test_matting_engines.py` now ALSO imports it (mux-warning + reset-ownership test) — port that test to the new `composite()` driver in the same change.
3. WebM sink: `subprocess.Popen` with `stdin=PIPE`, `stderr` → temp FILE (never PIPE — undrained stderr fills the small Windows pipe buffer on a 15k-frame VP9 encode, ffmpeg stops reading stdin, `stdin.write` blocks forever: silent hang); write `rgba = np.dstack([frame, (alpha*255).uint8])` per frame with EACH write wrapped for `BrokenPipeError`/`OSError` → close, `wait()`, raise a sink-scoped `FFmpegError` with the stderr-file tail (the `subprocess.run` pattern in `ffmpeg_utils.py:34-39` does NOT transfer to streaming Popen); finalize = close stdin + wait + returncode check + rename temp→final; alpha straight (not premultiplied) — VP9 expects unassociated alpha.
4. UI wiring (Radio + File download + status shows produced paths).
5. E2E test (`RUN_E2E=1`): render 3s clip `output_format=both` → assert (a) ffprobe webm: codec vp9, `pix_fmt yuva420p`, has audio stream, duration ±0.3s; (b) decode frame 0 to RGBA (`ffmpeg -i out.webm -frames:v 1 -f rawvideo -pix_fmt rgba -`) → corner 15×15 patches mean alpha <10/255, center patch alpha >200/255; (c) green MP4 still passes existing corner-green assertions; (d) matte called once per frame (counter spy in unit variant); (e) sink isolation: force the webm sink to die mid-stream (monkeypatched Popen or corrupt audio) in `both` mode → green MP4 completes and the webm failure appears in `warnings`.
6. Manual acceptance (document in usage guide, phase 7): import into CapCut Desktop, place over a background — verify transparency + no green fringe.

## Success Criteria

- [x] `both` render produces valid green MP4 + WebM alpha in one matte pass (E2E 78s; unit spy proves matte called once/frame)
- [x] ffprobe + RGBA-decode assertions pass (corner alpha <10/255, center >200/255, opus audio, duration ±0.3s). CORRECTION vs plan text: VP9 alpha in WebM probes as `pix_fmt yuv420p` + stream tag `alpha_mode=1` (alpha is container side-data), and RGBA decode must force `-c:v libvpx-vp9` — the native decoder silently returns opaque alpha. Tests assert the corrected truth.
- [x] Missing libvpx-vp9 produces a clear actionable error naming the resolved ffmpeg (unit test; check runs BEFORE the expensive animate stage)
- [x] Webm sink failure in `both` mode leaves the green output intact + surfaces a warning (2 unit tests: injected mid-stream death + corrupt audio; dead-sink tmp files cleaned)
- [x] `tests/test_pipeline_e2e.py` passes UNMODIFIED (73s run post-refactor — default-mode contract preserved)
- [ ] CapCut import verified once manually (USER action — drag `lipsync_alpha_*.webm` into CapCut Desktop over any background; screenshot/note lands in docs via phase 7)

## Completion Notes (2026-07-03)

- `composite()` driver replaced `composite_to_green` (single production caller updated; P2 behavior tests ported to the new signature in the same change, per the post-P2 note).
- Sinks: `_GreenSink` (imageio H.264 + AAC mux with preserved silent-video fallback-as-warning) and `_WebmSink` (Popen rgba pipe; stderr → FILE to avoid the Windows pipe-buffer deadlock; straight alpha; temp+rename). Driver additionally calls `sink.abort()` when a sink dies mid-stream (reaps the ffmpeg process + partial tmp — gap found during implementation, not in the plan).
- `both` mode measured: 78s vs 64s green-only on the 5.5s reference clip — VP9 encodes concurrently with matting, ~14s overhead, no second matte pass.
- `Pipeline.run` additive contract shipped: `outputs: {format: Path}`, `output` = green else webm. UI: output-format Radio (defaults Green MP4 per validation-log default), WebM download via `gr.File` (gr.Video can't preview alpha), warnings surfaced in status markdown (closes the P2 populate-only channel).
- Suite: 34 passed fast tier (incl. 6 new webm tests), both-mode E2E + v1-contract E2E green.

## Risk Assessment

- VP9 encode slow on 14C Xeon → accepted (quality-first); progress text warns; `-row-mt 1` uses the cores.
- Premultiplied-vs-straight alpha mistakes show as dark fringes → explicit straight-alpha + CapCut manual check.
- imageio-ffmpeg fallback lacking encoder → capability check (step 1) converts this from silent failure to instruction ("install system ffmpeg").
