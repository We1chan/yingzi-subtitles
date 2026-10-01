param([ValidateSet('original','online','local')][string]$Profile,
      [ValidateSet('auto','cpu','cuda')][string]$Device = 'auto', [switch]$PlanOnly)
$ErrorActionPreference = 'Stop'
$asrRoot = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot 'install_plan.ps1')
if (-not $Profile) {
    if ($PlanOnly) { $Profile = 'local' }
    else {
        Write-Host '请选择安装用途（以后可在管理模型中补齐）：'
        Write-Host '1. 仅原文字幕：下载识别和对齐模型'
        Write-Host '2. 在线翻译：识别与对齐在本机，翻译使用你配置的 API'
        Write-Host '3. 完全本地：额外下载翻译模型和翻译引擎'
        $asrChoice = Read-Host '输入 1 / 2 / 3（回车选择 1）'
        $Profile = switch ($asrChoice.Trim()) { '' { 'original' } '1' { 'original' } '2' { 'online' } '3' { 'local' } default { throw '请输入 1、2 或 3，再运行安装。' } }
    }
}
$asrPlan = Get-InstallPlan -Root $asrRoot -Profile $Profile -Device $Device
if ($PlanOnly) { $asrPlan | ConvertTo-Json -Depth 4; exit 0 }
Write-Host "安装方式：$Profile；运行设备：$($asrPlan.device)；下载组件：$($asrPlan.assets -join ', ')"
Write-Host "预计需要可用空间约 $($asrPlan.required_free_gb) GB；当前可用 $($asrPlan.available_gb) GB（包含临时下载预算）"
if (-not $asrPlan.device_available) { throw '未检测到可用的 NVIDIA 驱动。请使用 -Device cpu，或安装驱动后重试。' }
if ($asrPlan.available_gb -lt $asrPlan.required_free_gb) { throw '磁盘可用空间不足。请释放空间或将应用移到其他磁盘，再运行安装。' }
if (-not (Test-Path -LiteralPath (Join-Path $asrRoot '.venv\Scripts\python.exe')) -and
    -not (Get-Command uv -ErrorAction SilentlyContinue) -and -not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw '需要 Python 3.12 或 uv。请先安装，再运行 setup.cmd。'
}
Set-Location -LiteralPath $asrRoot
$env:UV_CACHE_DIR = Join-Path $asrRoot '.cache\uv'
$env:PIP_CACHE_DIR = Join-Path $asrRoot '.cache\pip'
$env:TEMP = Join-Path $asrRoot '.cache\temp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force $env:TEMP | Out-Null
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    if (Get-Command uv -ErrorAction SilentlyContinue) { uv venv --python 3.12 .venv }
    else { py -3.12 -m venv .venv }
    if ($LASTEXITCODE -ne 0) { throw '创建环境失败。需要 Python 3.12，请检查后重试。' }
}
$asrPython = Join-Path $asrRoot '.venv\Scripts\python.exe'
& $asrPython -c 'import sys; assert sys.version_info[:2] == (3,12), "Python 3.12 is required"'
if ($LASTEXITCODE -ne 0) { throw '现有环境不是 Python 3.12，请修复环境后重试。' }
& $asrPython -m ensurepip --upgrade
if ($LASTEXITCODE -ne 0) { throw 'pip 初始化失败。' }
& $asrPython -m pip install @($asrPlan.torch_packages) --index-url $asrPlan.torch_index
if ($LASTEXITCODE -ne 0) { throw 'PyTorch 安装失败。' }
if ($asrPlan.device -eq 'cuda') {
    & $asrPython -c 'import torch; assert torch.cuda.is_available(), "CUDA is unavailable"'
    if ($LASTEXITCODE -ne 0) { throw 'CUDA 不可用，请更新 NVIDIA 驱动，或使用 -Device cpu 重试。' }
}
$asrLock = Join-Path $asrRoot '.cache\requirements-app-lock.txt'
Get-Content -LiteralPath (Join-Path $asrRoot 'requirements-lock.txt') |
    Where-Object { $_ -notmatch '^(torch|torchaudio)==' } | Set-Content -LiteralPath $asrLock -Encoding Ascii
& $asrPython -m pip install -r $asrLock --index-url https://pypi.org/simple
if ($LASTEXITCODE -ne 0) { throw '应用依赖安装失败。' }
& $asrPython -m pip check
if ($LASTEXITCODE -ne 0) { throw '依赖检查失败，请修复后再下载模型。' }
& $asrPython -u tools\download_assets.py @($asrPlan.assets)
if ($LASTEXITCODE -ne 0) { throw '模型下载失败。再次运行相同安装方式可续传。' }
& $asrPython tools\configure_install.py --profile $Profile --device $asrPlan.device
if ($LASTEXITCODE -ne 0) { throw '安装选项保存失败，已保留原设置。' }
& (Join-Path $PSScriptRoot 'create_shortcut.ps1')
Write-Host '安装完成。打开映字桌面快捷方式或启动映字.vbs。' -ForegroundColor Green
