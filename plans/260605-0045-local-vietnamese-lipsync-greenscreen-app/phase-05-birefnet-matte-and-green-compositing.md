# Phase 05 — BiRefNet Matte + Green Compositing + Encode

## Context Links

- Plan overview: [plan.md](plan.md)
- Tech-stack report: `plans/reports/tech-stack-research-and-recommendation-260605-0045-local-vietnamese-lipsync-green-screen-report.md` (sec 5 BiRefNet MIT 3.45–6 GB; sec 6 correction: matting REQUIRED)
- Depends on: Phase 01 (BiRefNet weights + transformers/timm), Phase 02 (device/dtype/free_vram, green_rgb), Phase 04 (frames_dir)

## Overview

- **Priority:** P1
- **Status:** pending
- **Description:** Generate a per-frame alpha matte for each SadTalker frame with BiRefNet (MIT, HR-matting), composite the foreground onto a SOLID green background, and encode to H.264/265 MP4 muxed with the original audio. This is what actually creates the green screen — a naive ffmpeg `chromakey` cannot work (no green exists in the raw output).

## Key Insights

- Matting is REQUIRED (verified correction, report sec 6): SadTalker output background = source image, so there is nothing green to key. We synthesize the green.
- BiRefNet HR-matting: MIT, ~3.45–6 GB fp16. Load it ONLY after Phase 04 frees VRAM (sequential load contract). Both engines do not coexist on the 12 GB card.
- Avoid RVM (GPL-3.0) and RMBG-2.0 (CC BY-NC) — license-incompatible (verified eliminated). BiRefNet only.
- Compositing math: `out = fg*alpha + green*(1-alpha)`. Do it on GPU (torch) per-frame for speed, then write PNG (with composited RGB), then ffmpeg encodes the sequence + audio. Alternatively write alpha PNGs and let ffmpeg `color`+`overlay` composite — but torch compositing is simpler/faster here (KISS).
- Despill/feather: green fringing on hair edges. v1 = simple alpha composite; add optional 1px edge feather (alpha erosion/blur) if fringing observed in Phase 08. Do NOT over-engineer despill now (YAGNI).
- Green value from `AppConfig.green_rgb` (default 0,177,64). User-selectable in UI (Phase 07).

## Requirements

### Functional
- `matte_frames(frames_dir, device, dtype, progress=None) -> alpha_dir` : per-frame alpha (float/uint8) via BiRefNet.
- `composite_green(frames_dir, alpha_dir, green_rgb, out_dir) -> composited_dir` : fg-over-green RGB frames.
- `encode_video(composited_dir, audio_path, fps, cfg) -> mp4_path` : ffmpeg encode + audio mux (H.264 default, H.265 optional).
- Batch frames through BiRefNet (small batch, e.g. 2–4) under autocast to amortize overhead while staying in VRAM.
- Load BiRefNet, run, then `free_vram()` (mirror Phase 04 cleanup).

### Non-functional
- Split into 2 files to stay < 200 LoC each:
  - `matting_birefnet.py` (model load + per-frame/batch alpha)
  - `green_compositor.py` (composite + ffmpeg encode + mux)
- No GPL/non-commercial deps. BiRefNet via `transformers`/`safetensors` (already in Phase 01).

## Architecture

```
src/lipsync/
├── matting_birefnet.py    # load BiRefNet, frames -> alpha mattes
└── green_compositor.py    # alpha+fg -> solid green frames; ffmpeg encode + audio mux
```

Data flow:
```
frames_dir (PNG) --BiRefNet(fp16 autocast, batch)--> alpha_dir (PNG L / float)
frames + alpha + green_rgb --torch composite--------> composited_dir (PNG RGB on green)
composited_dir + audio + fps --ffmpeg--------------> final.mp4 (H.264, +AAC audio)
                                          --free_vram()-->
```

## Related Code Files

**Create:**
- `src/lipsync/matting_birefnet.py`
- `src/lipsync/green_compositor.py`

**Modify:** none.

**Delete:** none.

## Implementation Steps

1. **Load BiRefNet (`matting_birefnet.py`).** Load HR-matting weights from `config.BIREFNET_CKPT` onto `device`, `.eval()`, cast to `dtype` on cuda. Define the model's expected input transform (resize to model res, normalize ImageNet stats). Document the exact variant pinned in Phase 01.

2. **Per-frame / batched alpha.** Iterate `frames_dir` sorted; load in small batches (configurable, default 2–4 to respect VRAM). For each batch:
   ```python
   with torch.no_grad(), torch.amp.autocast('cuda', dtype=dtype):
       pred = birefnet(batch_tensor)        # logits/probs
   alpha = pred.sigmoid()                    # -> [0,1]
   ```
   Resize alpha back to the original frame size; save as 8-bit grayscale PNG in `alpha_dir`, or keep in-memory if compositing immediately (preferred: composite inline to avoid double disk I/O — see step 3).

3. **Composite onto green (`green_compositor.py`).** For each frame: `out = fg*alpha + green*(1-alpha)` on GPU (broadcast green tensor). Clamp to [0,255], to uint8, write PNG to `composited_dir`. Keep alpha and fg as float during the multiply to avoid banding. (Inline matte+composite in one loop is the KISS/fast path; separate functions remain for testability.)

4. **Optional edge feather (guarded).** Add a `feather_px` param (default 0). If >0, apply a small Gaussian blur / erosion to alpha before compositing to soften green fringe. Off by default; enabled only if Phase 08 shows fringing.

5. **Encode + mux (ffmpeg).** Encode the composited PNG sequence with audio:
   ```python
   subprocess.run([ffmpeg, "-y",
     "-framerate", str(fps), "-i", str(composited_dir/"%06d.png"),
     "-i", str(audio_path),
     "-c:v", cfg.codec, "-crf", str(cfg.crf), "-pix_fmt", "yuv420p",
     "-c:a", "aac", "-b:a", "192k", "-shortest",
     str(out_mp4)], check=True, capture_output=True)
   ```
   H.265 = `cfg.codec="libx265"`. `yuv420p` for broad player compat. `-shortest` aligns to audio.

6. **VRAM cleanup.** After matting, `del birefnet; free_vram()`. Compositing/encoding are CPU/ffmpeg-bound and need no GPU.

7. **Validate output.** ffprobe the result: has video+audio streams, duration ≈ audio duration, resolution matches `cfg.resolution`. Eyeball one frame: solid green where background was.

## Todo List

- [ ] `matting_birefnet.py`: load HR-matting weights on device + input transform
- [ ] Batched alpha generation under fp16 autocast; resize alpha to frame size
- [ ] `green_compositor.py`: torch composite fg-over-green (float math -> uint8)
- [ ] Optional `feather_px` guarded path
- [ ] ffmpeg encode (H.264 default, H.265 toggle) + AAC audio mux + yuv420p
- [ ] `free_vram()` after matting
- [ ] ffprobe validation + eyeball green check

## Success Criteria

- Output MP4 shows the talking head over a SOLID green background (no source-image background remnants), edges clean (hair acceptable).
- Audio is muxed and in sync; duration ≈ input audio.
- Resolution = `cfg.resolution`; pix_fmt yuv420p; plays in standard players.
- BiRefNet peak VRAM under ~7 GB at chosen batch; total pipeline never loads SadTalker + BiRefNet simultaneously (verified via VRAM logs).
- Both files < 200 LoC; no GPL/NC deps introduced.

## Risk Assessment

| Risk | Likelihood x Impact | Mitigation |
|------|---------------------|------------|
| Green fringe / spill on hair edges | Medium x Medium | Optional feather/erosion; tune green value; documented for Phase 08 |
| BiRefNet OOM with large frames/batch | Medium x High | Sequential load after SadTalker freed; batch=2 default; downscale infer res then upscale alpha |
| Alpha temporal flicker between frames | Medium x Medium | Per-frame is acceptable for v1; note as known limit; revisit only if E2E flickers badly |
| ffmpeg PNG-seq pattern mismatch | Low x Medium | Zero-padded `%06d.png`; assert frame count matches |
| Wrong BiRefNet variant (non-matting) gives hard mask | Medium x Medium | Pin HR-matting variant in Phase 01; verify soft alpha values mid-range on hair |

## Security Considerations

- ffmpeg via arg-list, no `shell=True`; validate `green_rgb` ints in [0,255], `crf`/`fps`/`resolution` ints in safe ranges before interpolation.
- `torch.load` BiRefNet weights with `weights_only=True` (prefer safetensors) to avoid pickle code-exec.
- Write only into caller temp/output dirs.

## Next Steps

- Unblocks Phase 06 (orchestration chains animate -> matte -> composite -> encode).
- Feeds Phase 08 (green-quality + edge inspection on the Vietnamese pilot).
