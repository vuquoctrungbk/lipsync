# Vietnamese Lip-Sync Validation Protocol

How we judge lip-sync quality on Vietnamese speech — objectively (SyncNet
LSE-D/LSE-C) and phonetically (VN phoneme spot-check). Every engine/settings
decision (RVM visuals aside) cites numbers from this harness. Created for the
v2 plan (`plans/260703-0017-pa2-v2-completion-tech-refresh/`), absorbing the
v1 Phase-8 pilot gate.

## 1. Harness

```
tools/syncnet/                 # gitignored — rebuild with the commands below
  syncnet_python/              # github.com/joonson/syncnet_python
  .venv/                       # ISOLATED env (deps conflict with app venv)
scripts/sync_metrics.py        # CLI wrapper -> JSON + table
```

Pinned harness source: `joonson/syncnet_python@907c0b579c2e2d83f0eae1b2ac9e720cde4e5623`
(cloned 2026-07-03). Weights from robots.ox.ac.uk (see `download_model.sh`):
`data/syncnet_v2.model`, `detectors/s3fd/weights/sfd_face.pth`.

Rebuild:

```powershell
mkdir tools\syncnet; cd tools\syncnet
git clone https://github.com/joonson/syncnet_python.git
cd syncnet_python; git checkout 907c0b579c2e2d83f0eae1b2ac9e720cde4e5623; cd ..
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install torch==2.5.1 torchvision==0.20.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cu121
.venv\Scripts\python.exe -m pip install numpy scipy scenedetect==0.6.7.1 opencv-contrib-python python_speech_features==0.6 tqdm
# weights: run the curl/wget lines from syncnet_python/download_model.sh
```

Usage:

```powershell
.venv\Scripts\python.exe scripts\sync_metrics.py --video outputs\clip.mp4
.venv\Scripts\python.exe scripts\sync_metrics.py --video long.mp4 --window 60 --json drift.json
```

## 2. Metrics & thresholds

- **LSE-D** (SyncNet min A/V distance) — lower is better. Real talking-head
  footage lands ≈ 6.5–8. **LSE-C** (confidence) — higher is better.
- SyncNet is trained on English (VoxCeleb). Scores are **RELATIVE**: use them
  to compare engines/settings on the *same* clip, and to track drift across a
  long clip against its own median. The absolute Vietnamese verdict comes from
  the phoneme spot-check below, not from an English-trained network.
- **Stochasticity:** SadTalker renders are non-deterministic (unseeded pose
  CVAE + blink sampling). Single-render deltas smaller than the recorded σ
  (section 5) are noise. Engine comparisons pin `cfg.seed` and must win by
  **max(0.5, 2σ)** to count.

**PILOT PASS** = LSE-D < 8 on the real MC clip **AND** phoneme spot-check
≥ 4/5.

## 3. VN phoneme spot-check (5 transitions, frame-by-frame)

Extract single frames at 5 phoneme onsets (`ffmpeg -ss <t> -vframes 1`) chosen
from the real script; judge each PASS/FAIL at 25 fps:

| # | Class | What must be visible |
|---|-------|----------------------|
| 1 | Bilabial /b, m, p/ (e.g. "mẹ", "ba") | Full lip closure ON the onset frame |
| 2 | Bilabial, second instance | Same — closure, not a half-open blur |
| 3 | Rounded /o, u, ô/ (e.g. "muốn", "cô") | Lips visibly narrowed/rounded ≤ 2 frames before vowel release |
| 4 | Open /a, ă, â/ (e.g. "ta", "ăn") | Jaw drop lags audio onset by ≤ 1 frame |
| 5 | Tone contour word (e.g. "được") | Mouth shape tracks the vowel, no frozen/rubber-band mouth |

Record: timestamp, word, expected shape, observed shape, PASS/FAIL + annotated
frame grabs (3–4) saved under `docs/` when the pilot runs.

## 4. Calibration anchors (harness sanity)

Scored at bring-up 2026-07-03 (syncnet venv: torch 2.5.1+cu121, GPU); re-run if
the harness env changes.

| Anchor | Expectation | Measured |
|---|---|---|
| `example.avi` (synced, syncnet's demo) | LSE-D ≈ 6.5–8 | **LSE-D 6.564 / LSE-C 8.351** |
| Same video, audio delayed 1.2s via `adelay` | LSE-D clearly worse (Δ ≥ 2) | **LSE-D 15.21 / LSE-C 0.921** (Δ 8.6) |
| Reproducibility (same video scored twice) | Δ ≤ 0.5 | **Δ 0.000** (6.148/9.069 both runs) |
| Harness throughput (GPU) | sec per 30s of video | **≈ 50–60s** (23.5s per 5s clip; 36s per 18.6s clip) → 600s render with `--window 60` ≈ 8–10 min |
| Harness throughput (CPU) | sec per 30s of video | **N/A** — vendored syncnet `torch.load` lacks `map_location`; CPU mode crashes with a CUDA-build torch. GPU-only harness (acceptable: it shares the render GPU, runs opt-in). |

**Desync-anchor methodology (learned the hard way):**
- SyncNet reports the MINIMUM distance over a ±15-frame (±0.6s) search — an
  offset inside that range is re-aligned away by design. Sanity offsets must
  exceed 0.6s.
- `-itsoffset`-style timestamp shifts DO NOT survive syncnet's own `-async 1`
  re-encode preprocessing (scored 6.69 ≈ synced). A valid desync anchor must
  physically shift samples: `ffmpeg -af "adelay=1200|1200"`.

## 5. SadTalker baseline (substitute audio until real assets arrive)

Recorded 2026-07-03. Inputs: SadTalker example portrait `full_body_1.png` +
18.6s Vietnamese TTS (edge-tts `vi-VN-HoaiMyNeural`; sentence covers the
section-3 phoneme classes). Pipeline v2 (RVM matting), fp32 SadTalker,
still_mode, RTX 3060 12GB.

| Run | LSE-D | LSE-C | animate_s | composite_s | wall_s |
|---|---|---|---|---|---|
| @256 run 1 (unseeded) | 6.148 | 9.069 | 88.8 | 37.3 | 126.3 |
| @256 run 2 (unseeded) | 6.241 | 8.932 | 83.1 | 40.4 | 123.7 |
| @256 run 3 (unseeded) | 6.255 | 8.989 | 79.3 | 36.7 | 116.2 |
| **σ(LSE-D) across the 3** | **0.058** → P5/P6 win threshold = max(0.5, 2σ) = **0.5** | | | | |
| @512 | 6.333 | 9.222 | 237.9 | 35.5 | 273.5 |

Reading: SadTalker on clean VN TTS beats the real-video anchor (6.56) at every
setting — comfortably under the PASS bar (LSE-D < 8). @512 does not improve
LSE-D over @256 (Δ +0.12 < the 0.5 noise floor) but yields the highest LSE-C;
sync quality is size-independent, 512 is a sharpness knob only. Caveat: TTS
audio is studio-clean — the real MC voice verdict may differ (section 6).

## 6. Pilot verdict

**Status: BLOCKED-ON-ASSETS** (2026-07-03). The harness is PROVEN (section 4
anchors separate cleanly; reproducibility exact) and the substitute-audio
baseline is strong (section 5: LSE-D ≈ 6.2, well under the PASS bar of 8) —
but the verdict itself requires the **real MC portrait + real VN voice clip
(10–30s)** from the user. When they arrive: render @256 and @512, score both,
run the 5-point spot-check, record PASS/FAIL + 3–4 annotated frames here.

## 7. On FAIL — escalation (never silent-swap)

Present the evidence (scores + annotated frames) and these options, cheapest
first; the user chooses:

1. Tune: face_size 512 + GFPGAN enhancer, re-score.
2. LatentSync refinement pass on SadTalker output (plan phase 5 spike).
3. MuseTalk as alternative engine (plan phase 6 bake-off).
4. Accept current quality (documented, revisitable).
