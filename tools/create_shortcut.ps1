$ErrorActionPreference = 'Stop'
$asrRoot = Split-Path $PSScriptRoot -Parent
& (Join-Path $asrRoot '.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'create_icon.py')
if ($LASTEXITCODE -ne 0) { throw 'Icon generation failed.' }
$asrShell = New-Object -ComObject WScript.Shell
$asrDesktop = $asrShell.SpecialFolders.Item('Desktop')
$asrShortcut = $asrShell.CreateShortcut((Join-Path $asrDesktop '映字 - 双语字幕.lnk'))
$asrShortcut.TargetPath = Join-Path $asrRoot '.venv\Scripts\pythonw.exe'
$asrShortcut.Arguments = '"' + (Join-Path $asrRoot 'main.py') + '"'
$asrShortcut.WorkingDirectory = $asrRoot
$asrShortcut.IconLocation = (Join-Path $asrRoot 'runtime\yingzi.ico') + ',0'
$asrShortcut.Description = '本地视频识别、日中双语字幕与时间轴校对'
$asrShortcut.Save()
Write-Output (Join-Path $asrDesktop '映字 - 双语字幕.lnk')
