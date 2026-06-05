# Phase 09 — Packaging + Docs + Licenses

## Context Links

- Plan overview: [plan.md](plan.md)
- All prior phases (this documents + packages the working app)
- License facts: tech-stack report sec 5 + SadTalker verification sec 1
- Depends on: Phase 07 (working UI), Phase 08 (validation PASS or accepted escalation)

## Overview

- **Priority:** P2
- **Status:** pending
- **Description:** Make the app launchable in one click and documented for handoff. Create a `run_app.bat` launcher, `README.md`, the `docs/` set per the project's documentation conventions, and a `NOTICE`/licenses section listing every third-party component and its license. License hygiene is a hard requirement (all components must be commercial-safe).

## Key Insights

- License hygiene is non-negotiable: SadTalker (Apache-2.0, MIT weights), BiRefNet (MIT), Gradio (Apache-2.0), PyTorch/torchvision/torchaudio (BSD), GFPGAN (Apache-2.0)/facexlib, MediaPipe (Apache-2.0), ffmpeg (already present). Explicitly EXCLUDED: InsightFace weights, Wav2Lip checkpoints, RMBG-2.0, RVM. The NOTICE must record this.
- Docs follow the user's convention: `project-overview-pdr.md`, `system-architecture.md`, `codebase-summary.md`, `code-standards.md`, `deployment-guide.md`, plus roadmap/changelog. Keep them concise and current.
- `.bat` launcher must use the venv python explicitly (`.venv\Scripts\python.exe app.py`) — never the Store stub.
- This is the last phase; it captures the Phase 08 perf table + Vietnamese pilot verdict so future readers know v1's verified state.

## Requirements

### Functional
- `run_app.bat` (double-click): activates/uses `.venv`, launches `app.py`, prints the local URL.
- `README.md`: what it is, hardware reqs, install (run `setup_env.ps1`), run (`run_app.bat`), usage (image+audio->green video), troubleshooting (dlib/CUDA/OOM), license summary.
- `docs/` set populated (PDR, system-architecture, codebase-summary, code-standards, deployment-guide, project-roadmap, project-changelog).
- `NOTICE` (or `docs/licenses.md`): component -> license -> source URL table + the excluded-NC list.

### Non-functional
- Docs concise (sacrifice grammar for concision per project rules).
- No secrets committed; `.gitignore` already excludes models/venv/outputs (Phase 01).
- If/when this becomes a git repo: conventional commits, no AI references, no plan-artifact references in code/commits (phase numbers/finding codes stay in `plans/` only).

## Architecture

```
Lipsync/
├── run_app.bat
├── README.md
├── NOTICE                         # or docs/licenses.md
└── docs/
    ├── project-overview-pdr.md
    ├── system-architecture.md
    ├── codebase-summary.md
    ├── code-standards.md
    ├── deployment-guide.md
    ├── project-roadmap.md
    └── project-changelog.md
```

## Related Code Files

**Create:**
- `run_app.bat`
- `README.md`
- `NOTICE` (or `docs/licenses.md`)
- `docs/project-overview-pdr.md`
- `docs/system-architecture.md`
- `docs/codebase-summary.md`
- `docs/code-standards.md`
- `docs/deployment-guide.md`
- `docs/project-roadmap.md`
- `docs/project-changelog.md`

**Modify:** `.gitignore` (only if gaps found).

**Delete:** none.

## Implementation Steps

1. **`run_app.bat`:**
   ```bat
   @echo off
   cd /d %~dp0
   if not exist .venv\Scripts\python.exe (
     echo venv missing. Run: powershell -ExecutionPolicy Bypass -File scripts\setup_env.ps1
     pause & exit /b 1
   )
   .venv\Scripts\python.exe app.py
   pause
   ```

2. **`NOTICE` / `docs/licenses.md`** — component table:
   | Component | License | Commercial | Source |
   |---|---|---|---|
   | SadTalker (code) | Apache-2.0 | yes | github.com/OpenTalker/SadTalker |
   | SadTalker weights | MIT | yes | huggingface.co/vinthony/SadTalker |
   | BiRefNet | MIT | yes | github.com/ZhengPeng7/BiRefNet |
   | Gradio | Apache-2.0 | yes | gradio.app |
   | PyTorch/torch* | BSD-3 | yes | pytorch.org |
   | GFPGAN / facexlib | Apache-2.0 | yes | github.com/TencentARC/GFPGAN |
   | MediaPipe | Apache-2.0 | yes | github.com/google/mediapipe |
   | ffmpeg | LGPL/GPL build | (system, present) | ffmpeg.org |

   Plus an EXCLUDED list: InsightFace weights (NC), Wav2Lip checkpoints (NC), RMBG-2.0 (CC BY-NC), RVM (GPL-3.0) — "deliberately not used; do not add."

3. **`README.md`** — sections: Overview; Hardware (Win10, RTX 3060 12 GB, fp16 mandatory); Install (`setup_env.ps1`, installs Python 3.11 prereq note); Run (`run_app.bat` -> 127.0.0.1:7860); Usage (front portrait + VN voice -> green MP4); Settings (green/resolution/codec/enhancer); Troubleshooting (dlib fallback, CUDA-over-RDP, OOM -> use 256/enhancer-off, librosa/numba pins); Vietnamese note (validated in Phase 08 — link verdict); License summary (link NOTICE).

4. **`docs/system-architecture.md`** — the locked pipeline diagram + module map (config/hardware/audio/face_detect/animation_sadtalker/matting_birefnet/green_compositor/pipeline/app) + VRAM sequencing contract + data flow.

5. **`docs/codebase-summary.md`** — one-paragraph-per-module summary + entry points (`app.py`, `run_pipeline`).

6. **`docs/code-standards.md`** — snake_case, files < 200 LoC, no `shell=True`, fp16 autocast pattern, `free_vram()` between GPU stages, no plan-artifact refs in code/commits, weights_only torch.load.

7. **`docs/deployment-guide.md`** — fresh-machine bring-up: install Python 3.11, run `setup_env.ps1`, download models, CUDA smoke test, run `run_app.bat`; RDP notes; temp-dir sweep guidance.

8. **`docs/project-overview-pdr.md`** — problem, scope (in: SadTalker+green MP4; out: MuseTalk, true-alpha), success criteria, constraints (local, commercial-safe, 12 GB).

9. **`docs/project-roadmap.md` + `docs/project-changelog.md`** — roadmap: v1 done = phases 1–8 PASS; deferred = MuseTalk engine, true-alpha export, packaged .exe. Changelog: seed with v1 entries as phases complete.

10. **Embed Phase 08 results** — paste the perf table + Vietnamese pilot verdict into README/system-architecture so the verified state is durable.

## Todo List

- [ ] Write `run_app.bat` (uses venv python, guards missing venv)
- [ ] Write `NOTICE`/`docs/licenses.md` (component table + excluded-NC list)
- [ ] Write `README.md` (install/run/usage/troubleshooting/license)
- [ ] Write `docs/system-architecture.md` (pipeline + module map + VRAM contract)
- [ ] Write `docs/codebase-summary.md`
- [ ] Write `docs/code-standards.md`
- [ ] Write `docs/deployment-guide.md`
- [ ] Write `docs/project-overview-pdr.md`
- [ ] Write `docs/project-roadmap.md` + `docs/project-changelog.md`
- [ ] Embed Phase 08 perf table + VN verdict into docs/README

## Success Criteria

- Double-clicking `run_app.bat` launches the app and prints the local URL (using venv python, not the stub).
- A new user can go from clone -> install -> running app using only README + deployment-guide.
- NOTICE lists every shipped component with a commercial-safe license; excluded-NC list present; no NC weights anywhere in repo.
- All `docs/` files exist, concise, and reflect the actual built modules + verified v1 state.
- No secrets or large binaries committed (gitignore verified).

## Risk Assessment

| Risk | Likelihood x Impact | Mitigation |
|------|---------------------|------------|
| Hidden NC dependency slips in | Low x High | NOTICE audit pass; cross-check installed pkgs vs excluded list before sign-off |
| `.bat` uses Store python | Low x Medium | Hard-code `.venv\Scripts\python.exe`; guard if missing |
| Docs drift from code | Medium x Low | Generated last, after code stable; codebase-summary mirrors module map |
| ffmpeg license nuance (GPL build) | Low x Low | ffmpeg is a separate system binary invoked via CLI, not linked/redistributed; note in NOTICE |

## Security Considerations

- Confirm `.gitignore` excludes `.venv/`, `models/`, `outputs/`, `temp/`, any `*.wav`/`*.mp4` samples with PII.
- README documents 127.0.0.1-only binding; warn against `share=True`.
- No credentials/API keys (app is fully local — none needed).

## Next Steps

- v1 ships. Deferred follow-ups (separate plans): MuseTalk second engine (if Phase 08 escalated), true-alpha export (ProRes4444/WebM), packaged .exe for other machines, temporal-stability matte improvements.
