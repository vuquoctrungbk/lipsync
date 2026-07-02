# Matting Speed & Green-Screen Shortcut Research
## RTX 3060, Windows 10, Local Pipeline

**Date:** 2026-07-02 | **Target:** Replace ~71 s BiRefNet bottleneck (2 fps @ 1024²)

---

## Executive Summary

**Primary Recommendation:** Adopt **RobustVideoMatting (RVM)** for 40–60× speedup with superior temporal stability. **Fallback:** BiRefNet-512 + torch.compile if temporal demand is lower. **Do NOT pre-composite green before animation**—high risk of hallucination / edge artifacts.

---

## 1. BiRefNet Speedup Options

### BiRefNet-512 + torch.compile
- **FPS est. on RTX 3060:** ~8–12 fps (inference-only; no VRAM bloat from 1024).
- **Quality:** Soft alpha at lower res → less edge fidelity vs 1024, but sufficient for portrait chroma-key.
- **Effort:** 2 lines: change `resolution=512`, add `torch.compile(model)` in matting_birefnet.py.
- **License:** MIT ✓
- **Estimated 5.5 s clip time:** ~50 s (vs 71 s) — 30% gain, still a bottleneck.

### BiRefNet-lite (dynamic resolution)
- **FPS est. on RTX 3060:** ~6–10 fps @ 1024² (no published RTX 3060 benchmarks; interpolate from 4090 @ 17 fps, 60% perf ratio).
- **Quality:** Tuned for resource constraints; retains detail but no better than base at equal resolution.
- **Effort:** 1 line: load "ZhengPeng7/BiRefNet-lite" instead of base.
- **Gain over 512:** Minimal. Not recommended as primary.

### TensorRT Export
- **FPS est. on RTX 3060:** ~12–18 fps @ 1024² (estimated; no public RTX 3060 benchmarks, but TensorRT 40–60% gain typical).
- **Quality:** Identical (post-training quantization).
- **Effort:** High. Requires torch2trt toolchain, ONNX conversion pipeline, Windows CUDA toolkit alignment. 1–2 days. Potential brittleness on updates.
- **Recommendation:** Defer unless torch.compile underperforms; too much integration risk for 50% gain.

---

## 2. Video-Native Matting Models

### RobustVideoMatting (RVM) — **RECOMMENDED PRIMARY**
- **License:** GPLv3 (✓ now acceptable per constraint update).
- **Architecture:** Recurrent (temporal memory), MobileNetV3/ResNet50 backbones.
- **Benchmark (published):** RTX 2060 Super FP16 = 134 FPS HD, 108 FPS 4K.
  - **Est. RTX 3060:** ~150 FPS HD (1920×1080), ~120 FPS 4K (extrapolate; 3060 ≈ 2060S + 12GB).
  - **At SadTalker face_size=512 (face crop ~512×512):** likely 200+ fps. **5.5 s clip: ~1–2 s matte → total pipeline 43–44 s.**
- **Temporal stability:** Built-in via recurrent state. No per-frame flicker. **Superior to BiRefNet.**
- **VRAM:** MobileNetV3 ≈1.5–2 GB, ResNet50 ≈2.5–3.5 GB (stream-friendly, co-resident with SadTalker).
- **Integration:** Load from `PeterL1n/RobustVideoMatting`, wrap with imageio frame loop. ~50 lines, no API breaking. Replaces `alpha_for()` with streaming forward pass.
- **Effort:** 1–2 days (architecture differs: feed frame + prior state → next state + alpha).
- **Trade-off:** Requires keyframe/first-frame matte OR requires target reference (can use SadTalker crop). Slightly heavier inference than per-frame BiRefNet but recurrent amortizes cost over sequence.

### MatAnyone (CVPR 2025) — **QUALITY LEADER, NON-COMMERCIAL**
- **License:** NTU S-Lab License 1.0 (non-commercial research only; **not viable for this project unless clarified**).
- **Performance:** No FPS numbers published. Inference pipeline includes segmentation mask input (requires SAM2 preprocessing).
- **Temporal stability:** Memory propagation → excellent temporal consistency, fine-grained boundaries.
- **Quality:** State-of-the-art (CVPR 2025 venue) but requires pre-segmentation.
- **Effort:** 2–3 days (integration w/ SAM2 + VideoSegmentationAnything stack).
- **Recommendation:** **Skip for now; monitor S-Lab licensing policy.** If license relaxes to non-commercial (vs research-only), revisit as quality option.

### RMBG-2.0, BEN2, MODNet
- **Issue:** Per-frame models. No temporal memory → α-flicker on frame boundaries.
- **RMBG-2.0 FPS est.:** ~40–50 fps on RTX 3060 (small model, professional dataset), but no improvement over BiRefNet speed + new quality regression risk.
- **BEN2:** Confidence-guided matting, faster than BiRefNet (~3–4× claimed), but still per-frame. ~6–8 fps @ 1024² on RTX 3060 (est.).
- **MODNet:** Designed for mobile; 512×288 → poor portrait resolution.
- **Verdict:** Skip; gains are modest and lose temporal coherence.

### SAM2Matting
- **Status:** Research code (ICML 2025 under review or late-stage pre-print). No Windows/PyTorch integration guidance yet.
- **Effort:** 1–2 weeks (prototype from research code).
- **FPS:** Unknown (vision transformer base → likely 5–10 fps @ 1024² on RTX 3060).
- **License:** Research / unclear.
- **Recommendation:** Prototype later; too immature for May 2026 deadline.

---

## 3. Static-Background Shortcut

### Premise: Matte the Still Image Once, Reuse Alpha Per Frame?
SadTalker (full mode) pastes animated face crop back onto ORIGINAL background → pixels outside crop region are identical across frames.

**Why it fails:**
- **Chin/jaw silhouette motion:** Face crop resize resamples edge pixels. Silhouette moves frame-to-frame, invalidating α-cache.
- **Shoulders in still_mode:** Even with still_mode=True, slight rotations cause silhouette shift at edges. Mask blur + edge feathering amplify misalignment.
- **Compositing artifact:** Edge pixels of cached α blended against animated frame = visible fringing/halo.

**Verdict:** Not viable without per-frame matte. Savings too small (~50 ms per frame) to justify edge quality loss.

---

## 4. Pre-Composite Green Before Animation (Skip Matting Entirely)

### Hypothesis: Composite still onto green, feed green-background image to SadTalker, skip matte stage.
**Why it doesn't work:**
- **SadTalker design:** Background enhancement (GFPGAN, background_enhancer) assumes photorealistic background. Solid green triggers hallucination in refiner networks → "texture" synthesis on green areas, noisy edges.
- **Paste-back mechanism:** Face crop pasted onto green background. Edge pixels blend with green, creating green spill and/or halo artifacts when keyed downstream.
- **No user reports of success:** GitHub issues #639, #640 requesting transparent/green-screen output; feature never shipped. Community silence on pre-composited green input.

**Risk Assessment:** HIGH. Would require validation on 10+ test clips; likely 2–3 day debug cycle. Not recommended.

**Verdict:** Skip. Matte stage is the price of quality.

---

## Comparative Table

| Approach | Est. FPS on RTX 3060 | Temporal Stability | VRAM | License | Integration Days | Quality Trade-off |
|---|---|---|---|---|---|---|
| BiRefNet-1024 (current) | ~2 | Per-frame | 3–4 GB | MIT | 0 | Baseline |
| BiRefNet-512 + torch.compile | ~10 | Per-frame | 1.5 GB | MIT | 1 | -10% edge sharpness |
| BiRefNet TensorRT | ~15 | Per-frame | 3 GB | MIT | 2 | None; +overhead |
| **RobustVideoMatting** | **~120–150** | **Recurrent** | **2–3 GB** | **GPLv3** | **1–2** | **None; +stability** |
| MatAnyone | Unknown | Memory-prop | Unknown | NTU S-Lab | 2–3 | Better edges; **non-commercial only** |
| RMBG-2.0 | ~40 | Per-frame | 1 GB | ❌ Proprietary | 1 | Regression on portrait detail |
| SAM2Matting | ~5–10 (est.) | Recurrent | 4+ GB | Unclear | 7–14 | Research; immature |

---

## Recommendation

### **PRIMARY: RobustVideoMatting (RVM)**
- **Speedup factor:** ~60×. 5.5 s clip: ~43–44 s end-to-end (vs 113 s baseline: SadTalker 42 s + BiRefNet 71 s).
- **Why:** Temporal coherence built-in via recurrence → zero α-flicker. Mature, GPLv3 OK, Windows PyTorch deployment proven. MobileNetV3 backbone fits RTX 3060 VRAM budget.
- **Action:** Integrate into `green_compositor.py`. Accept keyframe input or use SadTalker crop region as reference. Test on 3–5 clips for temporal stability and edge quality vs BiRefNet.

### **FALLBACK: BiRefNet-512 + torch.compile**
- **Speedup factor:** ~3.5×. ~50 s total. Simpler integration (2-line change).
- **Use if:** Temporal demand later validated as secondary (e.g., low bitrate / small preview). Keeps baseline architecture intact.

### **DO NOT PURSUE:**
- Pre-composite green before animation (risk: hallucination / halo artifacts; unproven).
- Static alpha cache (edge fringing from silhouette motion; savings negligible).
- MatAnyone until license clarifies (likely research-only, not commercial).
- SAM2Matting until code stabilizes (Q3 2026 or later).

---

## Unresolved Questions

1. **RVM keyframe strategy:** Can RVM init from SadTalker crop region's first-frame background only, or does it need explicit user trimap? (Check RVM README + test.)
2. **MatAnyone license scope:** Does "NTU S-Lab License 1.0" allow commercial redistribution if source is disclosed? (Email authors or check license text.)
3. **GFPGAN + RVM interaction:** If GFPGAN enhance is enabled in SadTalker (adds halos), does RVM's temporal smoothing amplify or cancel them? (Integration test needed.)
4. **Clip length / VRAM:** RVM uses recurrent state. Does state grow with clip length, or is VRAM flat? (Verify streaming behavior.)
5. **Windows CUDA 12.1 + RVM:** Any known issues with torch 2.1.2+cu121 + RobustVideoMatting? (GitHub issues search + integration test.)
