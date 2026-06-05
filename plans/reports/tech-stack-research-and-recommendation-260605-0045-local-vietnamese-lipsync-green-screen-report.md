# Tech-Stack Research & Recommendation — Local Vietnamese Lip-Sync (Green-Screen)

**Date:** 2026-06-05 | **Mode:** /bootstrap --full (ultracode) | **Method:** 14-agent web-grounded workflow (survey → adversarial verify → synthesis), 719K tokens.

## 1. Problem framing

- Input: **single still image** of a character + **Vietnamese voice audio**. Output: lip-synced talking-head **video on a solid green chroma-key background** for compositing.
- 100% local, no paid/cloud APIs, must run well on the target box.
- Key classification: this is **audio-driven portrait animation** (image+audio→video), NOT video dubbing. Rules out video-only tools (Wav2Lip/LatentSync/VideoReTalking need a driving video; a still gives frozen face).

## 2. Target hardware (verified live)

| Part | Spec | Note |
|---|---|---|
| GPU | RTX 3060 **12 GB**, Ampere CC 8.6 | fp16/bf16 tensor cores; ~10.5 GB usable after OS |
| CPU | Xeon E5-2680 v4, 14C/28T, AVX2 | strong CPU fallback |
| RAM | 64 GB | no pressure |
| Tooling | ffmpeg 8.0 ✓, git ✓, node 24 ✓ | **Python NOT installed** (MS Store stub only), no conda |
| Access | RDP ("Remote Display Adapter") | CUDA-over-RDP works but verify on first run |

## 3. Hard filters applied

(a) commercial-use license, (b) fits 12 GB fp16, (c) single-image input, (d) Windows-installable, (e) Vietnamese-safe audio encoder.

## 4. Eliminated (with reason)

| Model | Killed by |
|---|---|
| Wav2Lip / Wav2Lip-HD | LRS2/BBC **non-commercial**; needs video |
| LivePortrait | InsightFace weights **non-commercial** |
| Hallo2 / Hallo3 | derived from CogVideo-5B **non-commercial**; Hallo3 needs 24 GB |
| Sonic / FLOAT / V-Express / DreamTalk / SyncTalk | **non-commercial** licenses |
| AniPortrait | **OOM on 12 GB** (10 GB OOM confirmed in issues); xFormers fails on Win+CUDA12 |
| LatentSync v1.6 | needs **18 GB**; v1.5 (8 GB) needs video input, no single-image |
| EchoMimicV2 | README "academic research only" ambiguity + Vietnamese trained EN/Mandarin only (~50-70% cross-lingual) + Triton-Windows pain |
| RMBG-2.0 (matting) | CC BY-NC 4.0 **non-commercial** |
| RVM (matting) | **GPL-3.0** copyleft (viral for proprietary apps) |

## 5. Recommended stack

| Layer | Choice | License | Why |
|---|---|---|---|
| **Animation (primary)** | **SadTalker** | Apache-2.0 (+MIT weights) | Only commercial-safe model with **native single-image + natural head motion/blinks**, Windows webui installer, battle-tested (CVPR'23, ~14k★). fp16 ~3 GB, 3–8 s per output-sec on 3060. |
| Animation (alt/experiment) | MuseTalk v1.5 | MIT code + OpenRAIL-M weights | Crisper mouth + Whisper (best Vietnamese), but **mouth-only/frozen face on a still** (needs MuseV), Windows mmcv build pain. Good as a *selectable* second engine. |
| **Matting (REQUIRED for green)** | **BiRefNet** (HR-matting) | MIT | Per-frame alpha, actively maintained (2025), 3.45–6 GB fp16. Clean edges/hair. |
| **Compositing/encode** | **ffmpeg 8.0** | — | composite alpha→solid green; encode H.264/265 (flat green) or ProRes4444/WebM-alpha (true alpha). |
| **App shell** | **Gradio** | Apache-2.0 | KISS single-user local web UI, drag-drop image+audio→video, <2 s start, built-in queue. ComfyUI = alt if node-graph wanted. |
| **Runtime** | PyTorch 2.x CUDA 12.1 + torch.amp fp16, torch SDPA | BSD | fp16 mandatory on 12 GB; 3–5× speedup. |
| **Env** | Python 3.11 + uv (or venv) | — | must install real Python; uv fast on Windows. |

## 6. Correct pipeline (one correction to synthesis)

`install/env → audio prep (16kHz mono wav, ffmpeg) → face detect/align → SadTalker animate (fp16) → [optional GFPGAN face enhance] → BiRefNet per-frame matte (alpha) → composite onto solid green (ffmpeg) → encode`.

**Correction:** synthesis floated "ffmpeg chroma_key as the quick MVP". That is wrong here — the animated output keeps the *source image's* background (not green), so there is nothing green to key. To get a green background you MUST matte the foreground (BiRefNet) and fill the background green. **Matting is required, not optional.** (Exception: if source images always have a removable/plain background, matte the still once up-front instead.)

## 7. Top-line ranking (commercial-safe, 12 GB, single-image)

1. **SadTalker — 92** (recommended primary)
2. MuseTalk v1.5 — 78 (alt; frozen-face-on-still + Windows friction)
3. EchoMimicV2 — 52 (quality leader but licensing/Vietnamese/Windows risk)
4. LatentSync v1.5 — 48 (video-input only)
5. AniPortrait — 35 (OOM 12 GB)

## 8. Risks & mitigations

- **Vietnamese sync untested** on SadTalker (mel+wav2vec2 is language-agnostic but no VN benchmark) → pilot a 10–30 s clip of the real speaker, inspect mouth frame-by-frame; if off ≥2 frames, try MuseTalk (Whisper) for that clip.
- **Windows dlib/librosa build fragility** (SadTalker) → use prebuilt wheels / MediaPipe face-align fallback / ComfyUI wrapper.
- **VRAM OOM fp16** → 256px face region, `torch.cuda.empty_cache()` between runs, sequential model load (animate → free → matte).
- **RDP CUDA fragility** → validate inference on the RDP session first; CPU fallback exists (slow).
- **Green spill / edges** → prefer BiRefNet alpha + true-alpha export over chroma-key; feather/despill in ffmpeg.

## 9. Open questions (for user)

1. Primary engine: SadTalker (natural motion) vs MuseTalk (crisp mouth, frozen-face-on-still) vs both selectable?
2. Interface: Gradio vs ComfyUI vs packaged .exe?
3. Output: flat green video only, or also true-alpha (ProRes4444/WebM) export?
4. Target output resolution + acceptable latency per output-second (default: 512 face region, offline batch)?
5. Are source-character images guaranteed a single front-facing face? (multi/no-face handling)
6. Run only on this RDP box, or package for other machines later?

## 10. Build estimate

MVP (SadTalker + BiRefNet + green composite + Gradio): ~18–25 h. Production (+MuseTalk engine, +true-alpha, +packaging, +fallbacks): ~30–40 h.
