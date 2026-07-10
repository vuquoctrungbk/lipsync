# Idempotent bootstrap for the ISOLATED text-to-speech venv (tools/tts/.venv).
# Separate from the main .venv on purpose: modern TTS stacks need newer
# transformers/onnx than the pinned SadTalker stack tolerates. Re-runnable:
#   powershell -ExecutionPolicy Bypass -File scripts\setup_tts_env.ps1        # CPU (default)
#   powershell -ExecutionPolicy Bypass -File scripts\setup_tts_env.ps1 -Gpu   # + PyTorch CUDA
# -Gpu adds torch/torchaudio (CUDA) + transformers (~2.5 GB) so the TTS tab can
# offer a GPU device. CPU (torch-free ONNX) works without it.
param([switch]$Gpu)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

# 1. Locate a real Python 3.11 (same resolution as scripts/setup_env.ps1).
$pyExe = "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
if (-not (Test-Path $pyExe)) {
    try { $pyExe = (& py -3.11 -c "import sys; print(sys.executable)") } catch { $pyExe = $null }
}
if (-not $pyExe -or -not (Test-Path $pyExe)) {
    throw "Python 3.11 not found. Install it:  winget install --id Python.Python.3.11 --scope user"
}
Write-Host "Using Python: $pyExe" -ForegroundColor Cyan

# 2. Virtual environment (isolated; gitignored).
if (-not (Test-Path "tools\tts\.venv")) { & $pyExe -m venv tools\tts\.venv }
$vpy = ".\tools\tts\.venv\Scripts\python.exe"

# 3. Deps — install the FULL frozen lock (every transitive pinned; the loose
#    requirements.txt is the human-readable intent doc). PS 5.1 does not stop
#    on native-command failure, so gate each step on $LASTEXITCODE explicitly.
& $vpy -m pip install --upgrade pip wheel
if ($LASTEXITCODE -ne 0) { throw "pip bootstrap failed (exit $LASTEXITCODE)" }
& $vpy -m pip install -r tools\tts\requirements.lock
if ($LASTEXITCODE -ne 0) { throw "TTS dependency install failed (exit $LASTEXITCODE)" }

# 4. Import smoke. Model weights download from HuggingFace on FIRST synthesis
#    (cached under models\tts\hf via HF_HOME, which tts_cli.py sets itself).
& $vpy -c "import vieneu; print('vieneu import OK')"
if ($LASTEXITCODE -ne 0) { throw "vieneu import smoke failed (exit $LASTEXITCODE)" }

# 5. Optional GPU extras — VieNeu v3turbo's PyTorch backend (CUDA). Minimal set
#    only (torch/torchaudio/transformers); the full vieneu[gpu] extra pulls
#    Windows-fragile lmdeploy/triton/llama-cpp that v3turbo does not use.
if ($Gpu) {
    Write-Host "Installing GPU extras (PyTorch CUDA, ~2.5 GB)..." -ForegroundColor Cyan
    & $vpy -m pip install -r tools\tts\requirements-gpu.lock
    if ($LASTEXITCODE -ne 0) { throw "TTS GPU extras install failed (exit $LASTEXITCODE)" }
    $torchInfo = & $vpy -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())"
    if ($LASTEXITCODE -ne 0) { throw "torch GPU smoke failed (exit $LASTEXITCODE)" }
    Write-Host $torchInfo
    if ($torchInfo -notmatch 'cuda True') {
        Write-Warning "torch installed but CUDA not available (driver/GPU mismatch?) — the app keeps the GPU option HIDDEN until this is fixed."
    }
}

$mode = if ($Gpu) { "CPU + GPU" } else { "CPU (add -Gpu for GPU)" }
Write-Host "TTS setup complete [$mode]. First synthesis downloads model weights (internet once)." -ForegroundColor Green
