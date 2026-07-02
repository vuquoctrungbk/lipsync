# Brainstorm — v2 Completion & Tech Refresh (Lip-Sync Green-Screen App)

**Date:** 2026-07-02 | **Mode:** /brainstorm (max effort) | **Status:** APPROVED 2026-07-03 — PA2 confirmed; editor = CapCut (WebM VP9 alpha default, no ProRes); max clip length ≤ 10 min (cap 600s)

## 1. Problem Statement

v1 built & verified 2026-06-05 (commit `3786a12`): still image + Vietnamese audio → lip-synced talking-head MP4 on solid green `#00B140`. SadTalker (Apache-2.0) + BiRefNet (MIT) + ffmpeg + Gradio, 100% local, RTX 3060 12GB. Gaps blocking "hoàn thiện":

1. **Vietnamese lip-sync quality UNVALIDATED** — Phase 8 pilot gate still open (needs real VN clip).
2. **Slow**: ~20x realtime (5.5s clip = 42s animate + 71s matte). BiRefNet @1024 ≈ 2fps = bottleneck.
3. **Engine is 2023-gen** — SadTalker mouth articulation is the weakest visual link vs 2025-gen models.
4. **Deferred features**: true-alpha export, audio >120s, second engine, quality presets.

## 2. Requirements (Discovery answers, 2026-07-02)

- **Focus:** ALL 4 axes — quality, speed, output features, VN validation + stability.
- **Engine decision REOPENED** (v1 lock lifted for research; any actual swap still needs user approval on data).
- **License RELAXED → personal use**: non-commercial / GPL now acceptable. **Major constraint change from v1** (was commercial-safe only). Keep commercial-safe fallbacks isolated behind interfaces in case this reverts.
- **Speed:** quality-first; ~20x RT tolerable. No hard SLA.
- **Unchanged:** 100% local, no paid APIs, Windows 10, RTX 3060 12GB, input = 1 still image + audio, output = composite-ready talking head.

## 3. Research Inputs (3 parallel agents, 2026-07-02)

- `researcher-260702-2323-talking-head-engines-2026-report.md` — 22+ engines surveyed.
- `researcher-260702-2323-matting-speed-and-greenscreen-shortcuts-report.md`
- `researcher-260702-2323-alpha-export-long-audio-vn-syncnet-report.md`
- v1 baseline: `researcher-260605-1205-musetalk-adversarial-verification-report.md` (MuseTalk Windows mmcv pain documented).

### High-confidence findings (adopted)

| Finding | Impact |
|---|---|
| **RVM matting ~120–150fps on 3060** (published 2060S numbers), recurrent → zero alpha flicker, GPL-3 (OK for personal use), 2–3GB VRAM | Matte stage 71s → ~2s. Whole 5.5s clip 113s → ~45s. Highest-certainty win in the project |
| **Pre-greening source image before animation = ruled out** (GFPGAN/SadTalker hallucinate on flat green; no successful precedent; SadTalker issues #639/#640 never shipped transparency) | Matting stage stays mandatory |
| Static-alpha reuse ruled out (chin/jaw silhouette motion → fringing) | — |
| **LatentSync (ByteDance) = lip-refinement specialist over EXISTING video** | Perfect architectural fit as optional pass 2 after SadTalker (we already have video). VRAM on 12GB unverified → spike gate |
| **MuseTalk 1.5**: MIT explicit, ~4GB fp16, fastest; BUT mouth-only (static head) and v1 report documents Windows mmcv/mmpose install failure risk | Engine-2 candidate for "newsreader MC" style; timeboxed install |
| **No engine has Vietnamese evidence** — all Mandarin/English-heavy | VN pilot mandatory on EVERY path; need objective metric first |
| **syncnet_python** computes LSE-D/LSE-C locally on Windows; good sync = LSE-D < 8 (real-video ref 6.88); + VN phoneme checklist (bilabials /b,m,p/ closure, rounded /o,u,ô/, jaw-drop /a,ă,â/ ≤1 frame lag) | Converts "looks synced" into numbers; foundation for all quality decisions |
| **Alpha export: WebM VP9 `yuva420p` best default** — CapCut Desktop compatible, ~1.1GB/5min; ProRes 4444 has documented Adobe alpha-import bugs; AV1 alpha immature | Keep green MP4 as fast preview; add WebM alpha |
| **Long audio: ~50s chunks + 2-frame overlap + pose-lock + ±100ms audio crossfade**; monitor LSE-D per chunk for drift | 120s cap → 300s feasible |

### Low-confidence claims (REJECTED after cross-check — do not build on these)

- Engine report's #1 rec "HunyuanVideo-Avatar 10GB TeaCache, 2–4x FASTER than SadTalker, Apache-2.0": speed extrapolated from A100 — 13B-class video-diffusion on a 3060 is realistically **much slower** than SadTalker, not faster; license is Tencent Community License (restrictions), not plain Apache. Same optimism infects EchoMimicV3/Sonic/Hallo3 speed estimates.
- Multiple "license: Unknown/likely" entries (Sonic, Ditto, LatentSync, Hallo2/3) — must be read from LICENSE files before any integration.
- **Rule adopted: research narrows the candidate list; only benchmarks on the actual 3060 decide.**

## 4. Approaches Evaluated

| | PA1 — Optimize around SadTalker | PA2 — PA1 + LatentSync pass + measured bake-off ✅ | PA3 — Jump to 2025 diffusion engine |
|---|---|---|---|
| Scope | RVM matting, WebM alpha, chunked long audio, LSE-D harness + VN pilot, quality presets | All of PA1 + LatentSync 12GB spike (mouth-refine pass 2) + MuseTalk 1.5 (opt. Ditto) bake-off vs baseline on fixed VN test set | Replace engine with EchoMimicV3 / HunyuanVideo-Avatar via ComfyUI |
| Effort | ~1–1.5 wk | ~2.5–3.5 wk | +2–4 wk, high variance |
| Pros | Lowest risk; fixes speed/features/validation | Attacks weakest link (mouth) with specialist model; engine decisions by measurement; SadTalker always fallback; every bet has a cheap exit gate | Highest theoretical quality ceiling |
| Cons | Mouth quality stays 2023 | 2 gated risks (LatentSync VRAM, MuseTalk mmcv) | 12GB OOM edge; realistically slower than current on 3060; drags in ComfyUI runtime beside Gradio; murky licenses |

## 5. Recommendation: PA2 — staged, measurement-first

Stage order (each independently shippable):

0. **Measurement harness FIRST**: syncnet_python (LSE-D/C) + VN phoneme spot-check protocol; baseline current SadTalker output; close Phase 8 pilot with real VN clip. Without this, every later "quality improved" claim is vibes.
1. **RVM matting swap** behind a matting interface. RVM default (GPL, personal OK); **keep BiRefNet path as commercial-safe fallback flag** (license insurance). Expect 113s → ~45s per 5.5s clip; verify temporal stability + edge quality on 3–5 clips vs BiRefNet.
2. **WebM VP9 alpha export** as optional second output beside green MP4. CONFIRMED: user edits in CapCut → WebM VP9 `yuva420p` only; ProRes dropped (CapCut does not import it).
3. **Long-audio chunking** (~50s + 2-frame overlap + pose-lock + audio crossfade), cap 120s → 600s. CONFIRMED ≤10 min → stage kept, plus per-chunk checkpoint/resume (a 10-min render must survive a crash without restarting from zero) and per-chunk LSE-D drift monitor. Expectation set: ~1.5–2h wall-clock per 10-min clip @256 after RVM (animation dominates; quality-first accepted).
4. **LatentSync spike** (timeboxed ~1 day): verify LICENSE + VRAM fit on 12GB. Pass → optional "refine mouth" pass 2, adopted only if harness shows LSE-D gain on VN test set. Fail → skip silently to PA1 scope; no sunk cost.
5. **Engine bake-off**: MuseTalk 1.5 (+ Ditto if license reads clean) vs SadTalker(+LatentSync) on SAME VN portrait+audio set, judged by LSE-D + phoneme checklist + eyeball. Winner becomes optional engine 2 in UI; losers dropped. MuseTalk install timeboxed (~4h; ComfyUI fallback; else drop — v1 report predicts mmcv pain).
6. **UX polish**: quality presets (256-draft / 512-final — affordable once matte is fast), simple batch queue if still wanted.

Architecture note: `pipeline.py` already isolates engines behind `SadTalkerEngine`-style interfaces — add `MattingEngine` + optional `RefinerEngine` protocols; no rewrite needed.

## 6. Risks & Mitigations

| Risk | Gate/Mitigation |
|---|---|
| LatentSync VRAM/license unknown on 12GB | Stage-4 spike is a hard gate; skip on fail |
| MuseTalk mmcv Windows install (v1: 60-70% native fail) | Timebox 4h → ComfyUI alt → drop candidate |
| RVM recurrent state growth on 5-min clips | Verify streaming VRAM flatness during stage 1 |
| VN accuracy poor on ALL engines | Harness makes it measurable; escalate with data, never silent-swap |
| GPL/non-commercial components vs future commercial use | All behind interfaces + `commercial_safe` config flag keeping BiRefNet/SadTalker-only path |
| Chunk seams / identity drift on long clips | 2-frame overlap + pose-lock + per-chunk LSE-D monitor |

## 7. Success Metrics

- VN pilot PASS: LSE-D < 8 all chunks, phoneme spot-check ≥4/5, user visual approval on real MC portrait + voice.
- Speed: 5.5s clip ≤50s end-to-end @256 (from 113s); 512 preset ≤ ~2.5min.
- Alpha WebM imports clean in user's editor; green MP4 path unchanged (regression tests pass).
- 10-min clip renders without OOM, resumes after interrupt, no visible seams, LSE-D stable across chunks.
- Bake-off verdict documented with numbers; engine roster decided by data.

## 8. Decisions CONFIRMED by user (2026-07-03)

1. **Approach = PA2** (optimize + LatentSync spike + measured engine bake-off).
2. **Editor = CapCut** → WebM VP9 alpha is the only alpha format; ProRes dropped.
3. **Max clip length ≤ 10 min** → cap 600s; chunking + checkpoint/resume + drift monitoring required.

## 9. Next Steps & Dependencies

- Run `/ck:plan` with THIS report as context (approved scope above).
- **User must supply**: real Vietnamese voice clip (10–30s min; ideally the production MC voice) + the real MC portrait — needed for pilot (stage 0) AND bake-off (stage 5).
- Plan phases ≈ stages 0–6 above; stages 0–1 unblock everything else.

## Unresolved Questions

1. LatentSync exact license + measured VRAM on 12GB (stage-4 spike answers).
2. MuseTalk mmcv chain install outcome on this exact box (timeboxed attempt answers).
3. RVM recurrent-state VRAM behavior on 600s clips.
4. Vietnamese tonal accuracy of ANY engine — empirical only (stage 0/5 answer).
5. Ditto / Sonic / EchoMimicV3 actual LICENSE texts (only matters if bake-off extends or PA3 track opens).
6. ~~ProRes 4444 Adobe alpha bug status~~ — moot: CapCut confirmed, ProRes dropped.
