$ErrorActionPreference = 'Stop'
$asrRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $asrRoot
$env:UV_CACHE_DIR = Join-Path $asrRoot '.cache\uv'
$env:PIP_CACHE_DIR = Join-Path $asrRoot '.cache\pip'
$env:TEMP = Join-Path $asrRoot '.cache\temp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force $env:TEMP | Out-Null
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        uv venv --python 3.12 .venv
    } else {
        py -3.12 -m venv .venv
    }
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required. Install Python 3.12 or uv, then retry.' }
}
$asrPython = Join-Path $asrRoot '.venv\Scripts\python.exe'
& $asrPython -m ensurepip --upgrade
& $asrPython -m pip install 'torch==2.8.0' 'torchaudio==2.8.0' --index-url https://download.pytorch.org/whl/cu128
if ($LASTEXITCODE -ne 0) { throw 'PyTorch installation failed.' }
& $asrPython -m pip install -r requirements-lock.txt --index-url https://pypi.org/simple
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& $asrPython -u tools\download_assets.py
if ($LASTEXITCODE -ne 0) { throw 'Model download failed. Run setup.cmd again to resume.' }
& (Join-Path $PSScriptRoot 'create_shortcut.ps1')
Write-Host 'Ready. Open the Yingzi desktop shortcut or launch the VBS file.' -ForegroundColor Green
