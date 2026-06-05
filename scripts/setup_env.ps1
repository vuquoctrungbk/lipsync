# Idempotent environment bootstrap for the Local Vietnamese Lip-Sync app.
# Re-runnable: skips steps already done. Run from anywhere:
#   powershell -ExecutionPolicy Bypass -File scripts\setup_env.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

# 1. Locate a real Python 3.11 (the box ships only the MS Store stub).
$pyExe = "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
if (-not (Test-Path $pyExe)) {
    try { $pyExe = (& py -3.11 -c "import sys; print(sys.executable)") } catch { $pyExe = $null }
}
if (-not $pyExe -or -not (Test-Path $pyExe)) {
    throw "Python 3.11 not found. Install it:  winget install --id Python.Python.3.11 --scope user"
}
Write-Host "Using Python: $pyExe" -ForegroundColor Cyan

# 2. Virtual environment.
if (-not (Test-Path .venv)) { & $pyExe -m venv .venv }
$vpy = ".\.venv\Scripts\python.exe"

# 3. Build backbone (setuptools<81 keeps pkg_resources for librosa 0.9.2).
& $vpy -m pip install --upgrade pip "setuptools<81" wheel

# 4. PyTorch (CUDA 12.1) from the PyTorch index so we never get the CPU build.
& $vpy -m pip install torch==2.1.2 torchvision==0.16.2 torchaudio==2.1.2 `
    --index-url https://download.pytorch.org/whl/cu121

# 5. Remaining runtime dependencies.
& $vpy -m pip install -r requirements.txt

# 6. SadTalker, used as a LIBRARY (pinned commit for a stable import contract).
$sad = "third_party\SadTalker"
$pin = "cd4c0465ae0b54a6f85af57f5c65fec9fe23e7f8"
if (-not (Test-Path $sad)) {
    git clone https://github.com/OpenTalker/SadTalker.git $sad
    git -C $sad checkout $pin
}

# 7. Model checkpoints (SadTalker core + GFPGAN aux). Idempotent + verified.
& $vpy scripts\download_models.py

# 8. CUDA-over-RDP gate — must pass before the app can run on the GPU.
& $vpy scripts\cuda_smoke_test.py

Write-Host "Setup complete." -ForegroundColor Green
