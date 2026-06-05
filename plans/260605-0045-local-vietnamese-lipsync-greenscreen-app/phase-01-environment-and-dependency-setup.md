# Phase 01 — Environment & Dependency Setup

## Context Links

- Plan overview: [plan.md](plan.md)
- Research: `plans/reports/tech-stack-research-and-recommendation-260605-0045-local-vietnamese-lipsync-green-screen-report.md`
- SadTalker verification: `plans/reports/researcher-260605-0040-sadtalker-adversarial-verification-report.md`
- Architecture/optimization: `plans/reports/researcher-260605-0035-app-architecture-inference-optimization-report.md`

## Overview

- **Priority:** P1 (blocks all other phases)
- **Status:** pending
- **Description:** Install a real Python 3.11 (box has only the MS Store stub), create an isolated venv, install PyTorch CUDA 12.1 + SadTalker deps + BiRefNet deps + Gradio, clone SadTalker as a library, download all checkpoints, and prove CUDA works over RDP. End state: `python -c "import torch; print(torch.cuda.is_available())"` prints `True` and SadTalker/BiRefNet imports succeed.

## Key Insights

- Python is NOT installed (only `C:\Users\admin\AppData\Local\Microsoft\WindowsApps\python.exe` stub). Must install python.org 3.11.x. (verified live: `where python` returns only the WindowsApps stub.)
- ffmpeg 8.0, git 2.50, node 24 already present (verified live). Do NOT reinstall ffmpeg.
- GPU verified live: `RTX 3060, 12288 MiB, driver 591.74`. CUDA 12.1 wheels are compatible.
- SadTalker `inference.py` has NO fp16 flag; we use SadTalker as a **library**, not its CLI, so we own device/precision. No checkpoint patching needed at install time.
- Windows fragility: dlib/face_alignment CUDA build commonly fails; librosa/numba/numpy version conflicts are the top install failure. Pin versions; install in a verified order; have a MediaPipe CPU face-detect fallback ready (wired in Phase 04).
- RDP CUDA pass-through "usually works" but MUST be smoke-tested on this exact session before building further.
- uv is preferred for speed, but native `venv` is the safe fallback and needs no extra install. Plan uses **venv** as the primary path (zero new tooling, fewer moving parts on a fragile Windows box); uv documented as optional accelerator.

## Requirements

### Functional
- A reproducible `.venv` with Python 3.11 containing torch(cu121), torchvision, torchaudio, gradio, and all SadTalker + BiRefNet runtime deps.
- SadTalker source cloned to `third_party/SadTalker` and importable.
- All model checkpoints present under `models/` (gitignored).
- A CUDA smoke test script that confirms GPU usable over RDP.

### Non-functional
- Deterministic installs via pinned `requirements.txt`.
- No global `pip install` (everything inside `.venv`).
- No non-commercial deps pulled in (no InsightFace weights, no Wav2Lip checkpoints, no RMBG-2.0).

## Architecture

```
Lipsync/
├── .venv/                         # gitignored
├── third_party/SadTalker/         # cloned source (gitignored or vendored)
├── models/
│   ├── sadtalker/                 # SadTalker checkpoints + gfpgan weights
│   └── birefnet/                  # BiRefNet HR-matting weights
├── scripts/
│   ├── setup_env.ps1              # one-shot env bootstrap
│   ├── download_models.py         # fetch + verify checkpoints
│   └── cuda_smoke_test.py         # CUDA-over-RDP validation
├── requirements.txt
└── .gitignore
```

Data flow: `setup_env.ps1` -> creates venv -> installs requirements -> clones SadTalker ->
runs `download_models.py` -> runs `cuda_smoke_test.py`. Each step exits non-zero on failure.

## Related Code Files

**Create:**
- `scripts/setup_env.ps1`
- `scripts/download_models.py`
- `scripts/cuda_smoke_test.py`
- `requirements.txt`
- `.gitignore`

**Modify:** none (greenfield).

**Delete:** none.

## Implementation Steps

1. **Install Python 3.11 (manual, one-time).** Download `python-3.11.x-amd64.exe` from python.org. Install with "Add python.exe to PATH" checked. Verify in a NEW PowerShell:
   ```powershell
   python --version   # must print 3.11.x, NOT redirect to Store
   python -c "import sys; print(sys.executable)"
   ```
   If it still resolves to WindowsApps stub: Settings -> Apps -> App execution aliases -> turn OFF the python.exe/python3.exe aliases.

2. **Write `.gitignore`** with at least: `.venv/`, `models/`, `third_party/SadTalker/`, `__pycache__/`, `*.pyc`, `outputs/`, `temp/`, `*.mp4`, `*.wav`.

3. **Write `requirements.txt`** with pinned versions known to coexist (the librosa/numba/numpy triad is the fragile zone):
   ```
   --extra-index-url https://download.pytorch.org/whl/cu121
   torch==2.2.2+cu121
   torchvision==0.17.2+cu121
   torchaudio==2.2.2+cu121
   numpy==1.23.5
   numba==0.58.1
   llvmlite==0.41.1
   librosa==0.10.1
   scipy==1.11.4
   imageio==2.34.0
   imageio-ffmpeg==0.4.9
   av==11.0.0
   opencv-python==4.9.0.80
   pillow==10.2.0
   scikit-image==0.22.0
   gfpgan==1.3.8
   facexlib==0.3.0
   basicsr==1.4.2
   kornia==0.7.1
   yacs==0.1.8
   pydub==0.25.1
   safetensors==0.4.2
   einops==0.7.0
   transformers==4.38.2
   timm==0.9.16
   mediapipe==0.10.11
   gradio==4.44.1
   tqdm
   ```
   Notes: `mediapipe` is the CPU face-detect fallback (wired in Phase 04). `timm`/`einops`/`transformers` support BiRefNet. `gfpgan`+`facexlib`+`basicsr` support the optional enhancer. Pin `numpy<1.24` because `basicsr`/`numba` break on newer numpy. Do NOT add `dlib` to requirements — install it separately (step 6) so a dlib failure does not block the whole install.

4. **Write `scripts/setup_env.ps1`** (idempotent):
   ```powershell
   $ErrorActionPreference = "Stop"
   Set-Location $PSScriptRoot\..
   if (-not (Test-Path .venv)) { python -m venv .venv }
   .\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   if (-not (Test-Path third_party)) { New-Item -ItemType Directory third_party | Out-Null }
   if (-not (Test-Path third_party\SadTalker)) {
     git clone https://github.com/OpenTalker/SadTalker.git third_party\SadTalker
   }
   .\.venv\Scripts\python.exe scripts\download_models.py
   .\.venv\Scripts\python.exe scripts\cuda_smoke_test.py
   Write-Host "Setup complete." -ForegroundColor Green
   ```

5. **Write `scripts/download_models.py`.** Pull checkpoints from official HF mirrors into `models/`. Use `huggingface_hub` (add to requirements if not present) or direct URLs. Targets:
   - SadTalker: `mapping_00109-model.pth.tar`, `mapping_00229-model.pth.tar`, `SadTalker_V0.0.2_256.safetensors`, `SadTalker_V0.0.2_512.safetensors` (from `vinthony/SadTalker`, MIT weights) -> `models/sadtalker/`.
   - GFPGAN/facexlib aux weights (`GFPGANv1.4.pth`, `alignment_WFLW_4HG.pth`, `detection_Resnet50_Final.pth`, `parsing_parsenet.pth`) -> `models/sadtalker/` (downloaded lazily by gfpgan/facexlib on first run; pre-fetch to avoid first-run network).
   - BiRefNet HR-matting: `ZhengPeng7/BiRefNet` (MIT) general/HR variant `.safetensors` -> `models/birefnet/`.
   After each download, assert file exists + size > expected min; print a manifest. Do NOT download any InsightFace, Wav2Lip, or RMBG weights.

6. **dlib install attempt with documented fallback (separate step).**
   ```powershell
   .\.venv\Scripts\python.exe -m pip install dlib-bin   # prebuilt; avoids CUDA build
   .\.venv\Scripts\python.exe -c "import dlib; print(dlib.__version__)"
   ```
   If `dlib-bin` import fails, do NOT block. Record the failure; Phase 04 face detect will use the MediaPipe CPU path (already in requirements). SadTalker's `face_alignment` can run on its non-dlib detector; confirm in Phase 04.

7. **Write + run `scripts/cuda_smoke_test.py`** (the RDP CUDA gate):
   ```python
   import torch
   assert torch.cuda.is_available(), "CUDA not visible over RDP — check driver/session"
   dev = torch.device("cuda:0")
   print("GPU:", torch.cuda.get_device_name(0))
   props = torch.cuda.get_device_properties(0)
   print(f"VRAM: {props.total_memory/1e9:.1f} GB, CC: {props.major}.{props.minor}")
   x = torch.randn(2048, 2048, device=dev, dtype=torch.float16)
   y = (x @ x).float().sum().item()
   torch.cuda.synchronize()
   print("fp16 matmul OK, checksum:", y)
   torch.cuda.empty_cache()
   ```
   Must print GPU name, ~12 GB, CC 8.6, and complete the fp16 matmul without error.

8. **Run the full bootstrap:** `powershell -ExecutionPolicy Bypass -File scripts\setup_env.ps1`. Iterate on requirement pins if librosa/numba/basicsr conflict (expect 1–2 cycles per verification report).

## Todo List

- [ ] Install python.org Python 3.11; disable Store aliases; verify `python --version`
- [ ] Write `.gitignore`
- [ ] Write pinned `requirements.txt`
- [ ] Write `scripts/setup_env.ps1`
- [ ] Write `scripts/download_models.py` (SadTalker + GFPGAN + BiRefNet only)
- [ ] Write `scripts/cuda_smoke_test.py`
- [ ] Run setup_env.ps1; resolve any librosa/numba/numpy conflicts
- [ ] Clone SadTalker into `third_party/SadTalker`
- [ ] Download + verify all checkpoints; print manifest
- [ ] Attempt `dlib-bin`; record success/fallback decision
- [ ] Run CUDA smoke test over RDP; confirm fp16 matmul

## Success Criteria

- `.\.venv\Scripts\python.exe -c "import torch,gradio,librosa,cv2,timm,transformers; print('ok')"` prints `ok`.
- `cuda_smoke_test.py` prints `True`, GPU name, ~12 GB, CC 8.6, and "fp16 matmul OK".
- `import sys; sys.path.insert(0,'third_party/SadTalker'); import src.utils.init_path` resolves (SadTalker importable as library).
- All required checkpoints present under `models/` with non-zero sizes; manifest printed.
- No non-commercial weights present anywhere under `models/`.

## Risk Assessment

| Risk | Likelihood x Impact | Mitigation |
|------|---------------------|------------|
| librosa/numba/numpy conflict aborts install | High x High | Pinned triad (numpy 1.23.5 / numba 0.58.1 / librosa 0.10.1); install in requirements order; isolate dlib to its own step |
| dlib CUDA build fails | Medium x Medium | Use `dlib-bin`; if fails, MediaPipe CPU fallback (Phase 04); not a blocker |
| CUDA not visible over RDP | Medium x High | `cuda_smoke_test.py` run first; if fails, escalate (driver/session config) before building further |
| Store stub shadows real Python | Medium x Medium | Disable App execution aliases; verify `sys.executable` |
| basicsr/torchvision API drift breaks gfpgan import | Medium x Medium | Pin torchvision 0.17.2 + basicsr 1.4.2; known-good pair; document `functional_tensor` shim if needed |

## Security Considerations

- Download checkpoints over HTTPS from official HF orgs only (`vinthony`, `ZhengPeng7`); verify file sizes.
- No secrets in repo. `.gitignore` excludes models/venv/outputs.
- Run app bound to `127.0.0.1` (enforced in Phase 07), not exposed on LAN.

## Next Steps

- Unblocks Phase 02 (hardware/config consumes the working torch + checkpoint paths).
- Hand the verified `models/` paths to Phase 02 `config.py`.
