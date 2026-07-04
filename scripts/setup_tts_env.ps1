# Idempotent bootstrap for the ISOLATED text-to-speech venv (tools/tts/.venv).
# Separate from the main .venv on purpose: modern TTS stacks need newer
# transformers/onnx than the pinned SadTalker stack tolerates. Re-runnable:
#   powershell -ExecutionPolicy Bypass -File scripts\setup_tts_env.ps1
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

Write-Host "TTS setup complete. First synthesis will download model weights (needs internet once)." -ForegroundColor Green
