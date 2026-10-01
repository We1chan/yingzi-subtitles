function Get-InstallPlan {
    param([string]$Root, [ValidateSet('original','online','local')][string]$Profile,
          [ValidateSet('auto','cpu','cuda')][string]$Device = 'auto')
    $gpu = @()
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        $gpu = @(& nvidia-smi --query-gpu=name,driver_version --format=csv,noheader 2>$null)
        if ($LASTEXITCODE -ne 0) { $gpu = @() }
    }
    $selectedDevice = $Device
    if ($Device -eq 'auto') { $selectedDevice = if ($gpu.Count) { 'cuda' } else { 'cpu' } }
    $assets = @('asr', 'align')
    if ($Profile -eq 'local') { $assets += @('translate', 'runtime') }
    $requiredGB = if (Test-Path -LiteralPath (Join-Path $Root '.venv\Scripts\python.exe')) { 2.0 } else { 6.0 }
    if ($selectedDevice -eq 'cuda') { $requiredGB += 4.0 }
    if (-not (Test-Path -LiteralPath (Join-Path $Root 'models\Qwen3-ASR-1.7B\.complete'))) { $requiredGB += 4.0 }
    if (-not (Test-Path -LiteralPath (Join-Path $Root 'models\Qwen3-ForcedAligner-0.6B\.complete'))) { $requiredGB += 2.0 }
    if ($Profile -eq 'local') {
        if (-not (Test-Path -LiteralPath (Join-Path $Root 'models\Qwen3-4B-Instruct-2507-Q4_K_M.gguf.complete'))) { $requiredGB += 3.0 }
        if (-not (Test-Path -LiteralPath (Join-Path $Root 'runtime\release.json'))) { $requiredGB += 3.0 }
    }
    $drive = New-Object System.IO.DriveInfo ([System.IO.Path]::GetPathRoot([System.IO.Path]::GetFullPath($Root)))
    [PSCustomObject]@{
        profile = $Profile
        device = $selectedDevice
        gpu = $gpu
        assets = $assets
        torch_index = if ($selectedDevice -eq 'cuda') { 'https://download.pytorch.org/whl/cu128' } else { 'https://download.pytorch.org/whl/cpu' }
        torch_packages = if ($selectedDevice -eq 'cuda') { @('torch==2.8.0+cu128', 'torchaudio==2.8.0+cu128') } else { @('torch==2.8.0+cpu', 'torchaudio==2.8.0+cpu') }
        required_free_gb = $requiredGB
        available_gb = [Math]::Round($drive.AvailableFreeSpace / 1GB, 2)
        device_available = ($selectedDevice -ne 'cuda' -or $gpu.Count -gt 0)
    }
}
