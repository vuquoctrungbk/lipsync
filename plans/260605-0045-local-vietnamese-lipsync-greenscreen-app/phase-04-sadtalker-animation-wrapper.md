# Phase 04 — SadTalker Animation Wrapper (fp16)

## Context Links

- Plan overview: [plan.md](plan.md)
- SadTalker verification: `plans/reports/researcher-260605-0040-sadtalker-adversarial-verification-report.md` (sec 2 VRAM, sec 3 Windows, sec 4 Vietnamese, sec 5 no green-screen)
- Architecture report: sec 3.2 (fp16 amp), 3.6 (device select)
- Depends on: Phase 01 (SadTalker cloned + checkpoints), Phase 02 (device/dtype/free_vram), Phase 03 (16k WAV)

## Overview

- **Priority:** P1
- **Status:** pending
- **Description:** Wrap SadTalker as a library behind one function `animate(image_path, audio_path, cfg) -> AnimateResult` (path to a raw talking-head video over the SOURCE-IMAGE background + the frame dir). Enforce fp16 via `torch.amp.autocast`, profile VRAM, manage face detection with a MediaPipe CPU fallback, and free VRAM before returning so Phase 05 (matte) can load.

## Key Insights

- SadTalker `inference.py` has NO fp16 flag (verified). We import its internal classes (`CropAndExtract`, `Audio2Coeff`, `AnimateFromCoeff`) and wrap forward passes in `autocast('cuda', dtype=fp16)`. We do NOT shell out to its CLI — we own device/precision/VRAM lifecycle.
- Output keeps the SOURCE image background — NOT green. This is the core reason Phase 05 matting is REQUIRED, not optional (verified, report sec 5). Do not attempt chroma_key on this output.
- fp16 mandatory: fp32 baseline ~6 GB, peaks 8–10 GB -> OOM risk. fp16 ~3 GB (verified sec 2).
- Face-region 256 is OOM-safe default; 512 selectable. Long audio raises VRAM/time (cap enforced in Phase 03).
- dlib/face_alignment may fail on Windows. SadTalker's `CropAndExtract` uses its own face detector (safetensors-based, not dlib for the core path) — but if its detector path errors, fall back to a MediaPipe-based crop. Wire the fallback as a thin pre-crop that produces the aligned face SadTalker expects.
- Vietnamese: NO special handling here (language-agnostic). Accuracy validated in Phase 08, not assumed here.
- GFPGAN enhancer is a TOGGLE (default off). It adds ~1.5 GB and latency; keep optional.

## Requirements

### Functional
- `animate(image_path, audio_path, cfg, device, dtype, progress=None) -> AnimateResult` where `AnimateResult` = `{ video_path, frames_dir, fps, num_frames }`.
- Load SadTalker checkpoints from `config.SADTALKER_CKPT`; honor `cfg.face_region` (256/512), `cfg.use_enhancer`.
- Run all GPU forwards under fp16 autocast on cuda; bf16/cpu fallback per device.
- Output frames as PNG sequence (needed by Phase 05 per-frame matte) AND/OR a raw mp4 for debugging.
- Profile + log VRAM before/after via `vram_report()`; call `free_vram()` before return.

### Non-functional
- Wrapper file < 200 LoC. If SadTalker glue exceeds that, split: `animation_sadtalker.py` (public API + orchestration) + `sadtalker_loader.py` (checkpoint/model construction) + `face_detect.py` (detect/crop + fallback).
- No edits to `third_party/SadTalker` source (treat as vendored read-only); all adaptation lives in our wrapper. If a monkeypatch is unavoidable, isolate it in one documented function with a comment explaining the why.

## Architecture

```
src/lipsync/
├── face_detect.py            # SadTalker detector path + MediaPipe CPU fallback -> aligned crop
├── sadtalker_loader.py       # build CropAndExtract / Audio2Coeff / AnimateFromCoeff w/ paths+dtype
└── animation_sadtalker.py    # animate(): orchestrate crop->coeff->render under autocast, emit frames
```

Data flow:
```
image -> face_detect (crop+align) ----------------+
audio(16k) -> Audio2Coeff (mel+wav2vec2) -> coeffs |
                                                   v
                       AnimateFromCoeff (fp16 autocast) -> frames (PNG seq) + raw mp4
                                                   |
                          [optional GFPGAN enhance per-frame]
                                                   v
                       free_vram() -> AnimateResult
```

## Related Code Files

**Create:**
- `src/lipsync/face_detect.py`
- `src/lipsync/sadtalker_loader.py`
- `src/lipsync/animation_sadtalker.py`

**Modify:** none (SadTalker source stays untouched).

**Delete:** none.

## Implementation Steps

1. **Make SadTalker importable.** In `sadtalker_loader.py`, prepend `str(config.SADTALKER_SRC)` to `sys.path` once. Import `from src.utils.preprocess import CropAndExtract`, `from src.test_audio2coeff import Audio2Coeff`, `from src.facerender.animate import AnimateFromCoeff`, `from src.utils.init_path import init_path`. (These are SadTalker's public internals used by its own `inference.py`.)

2. **Resolve checkpoint paths** via `init_path(config.SADTALKER_CKPT, os.path.join(SADTALKER_SRC,'src/config'), size=cfg.face_region, old_version=False, preprocess='crop')`. Build the three model objects on `device`.

3. **Face detect + align (`face_detect.py`).** Primary: use SadTalker `CropAndExtract.generate(...)` to produce the cropped/aligned 3DMM + coeff inputs. Wrap in try/except. Fallback: if it raises (detector/dlib failure), run MediaPipe FaceMesh (CPU) to get a face bounding box, center-crop+resize to `face_region`, and feed that crop back into the SadTalker preprocess. Log which path was used. Raise a clear error if NO face is found (single front-facing face assumption).

4. **Audio2Coeff under autocast.** Run coeff generation inside:
   ```python
   amp = torch.amp.autocast('cuda', dtype=dtype) if device.type=='cuda' else nullcontext()
   with torch.no_grad(), amp:
       coeffs = audio2coeff.generate(batch, save_dir, pose_style, ref_info)
   ```

5. **AnimateFromCoeff under autocast.** Same autocast wrapper around `animate_from_coeff.generate(...)`. Configure it to output a PNG frame sequence into `temp/frames_<uuid>/` (SadTalker writes frames internally before muxing; ensure we keep the frame dir — set `save_dir` and prevent cleanup, or extract frames from the produced mp4 with ffmpeg if SadTalker deletes them). Capture `fps` (default `cfg.fps`).

6. **Optional GFPGAN enhance.** If `cfg.use_enhancer`, run SadTalker's built-in enhancer path (`enhancer='gfpgan'`) — but note it raises VRAM ~1.5 GB; call `free_vram()` between coeff and render if headroom tight. Keep toggle wired from config.

7. **VRAM profiling + cleanup.** Log `vram_report()` after each major stage. After producing frames, move models off GPU (`.to('cpu')` or `del` + `free_vram()`) so Phase 05's BiRefNet has room. This realizes the sequential-load contract from the plan.

8. **Return `AnimateResult`** with `frames_dir`, `video_path` (raw, source-bg), `fps`, `num_frames`.

9. **First-run validation hook.** On first successful run, assert frames exist and count > 0; assert the raw video plays (ffprobe). This is the integration checkpoint feeding Phase 08.

## Todo List

- [ ] `sadtalker_loader.py`: sys.path inject + import internals + `init_path` + build 3 models on device
- [ ] `face_detect.py`: SadTalker crop primary + MediaPipe CPU fallback + no-face error
- [ ] `animation_sadtalker.py`: `animate()` orchestration
- [ ] Wrap Audio2Coeff + AnimateFromCoeff in fp16 autocast
- [ ] Emit PNG frame sequence + capture fps/num_frames
- [ ] Wire optional GFPGAN toggle
- [ ] VRAM profiling logs + `free_vram()` before return
- [ ] Smoke test: portrait + 5 s WAV -> frames + raw mp4 with moving mouth

## Success Criteria

- `animate()` produces N>0 PNG frames + a playable raw mp4 with visible mouth/head motion synced to audio (eyeball check), background = source image (confirms matting still needed).
- Peak VRAM during animate stays under ~9 GB at face_region=256, fp16 (logged).
- With `dlib-bin` absent, the MediaPipe fallback path still produces frames (fallback proven).
- After return, `vram_report()` shows animate models freed (allocated drops) so BiRefNet can load.
- No modifications to `third_party/SadTalker`.

## Risk Assessment

| Risk | Likelihood x Impact | Mitigation |
|------|---------------------|------------|
| dlib/face_alignment build fail | Medium x High | MediaPipe CPU fallback in `face_detect.py`; dlib isolated in Phase 01 |
| fp16 NaNs / black frames in autocast | Medium x High | Keep coeff math fp32 if unstable; autocast only the heavy renderer; bf16 fallback option |
| OOM at face_region=512 | Medium x High | Default 256; `free_vram()` between stages; enhancer off by default |
| SadTalker deletes frame dir after mux | Medium x Medium | Re-extract frames from raw mp4 via ffmpeg if dir missing |
| Internal API import path changed in cloned version | Low x High | Pin a known SadTalker commit in Phase 01 clone; document the import contract |
| Multi-face / no-face input | Medium x Medium | Use largest/most-central face; clear error if none |

## Security Considerations

- Treat user image as untrusted: validate it decodes (PIL open) before passing to models; reject non-image MIME.
- No `shell=True` for any ffmpeg frame extraction; arg-list only.
- Keep `third_party/SadTalker` read-only; do not exec arbitrary code from checkpoints (load with `weights_only=True` where torch.load is used by our code).

## Next Steps

- Unblocks Phase 05 (consumes `frames_dir` for per-frame matte) and Phase 06 (orchestrates animate->matte).
- Feeds Phase 08 Vietnamese pilot (this is the engine under test).
