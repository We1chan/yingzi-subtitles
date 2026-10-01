$ErrorActionPreference = 'Stop'
$yingziRoot = Split-Path $PSScriptRoot -Parent
$yingziPython = Join-Path $yingziRoot '.venv\Scripts\pythonw.exe'
$yingziMain = Join-Path $yingziRoot 'main.py'
if (-not (Test-Path -LiteralPath $yingziPython)) { throw 'Run setup.cmd first.' }
$yingziClasses = 'HKCU:\Software\Classes'
$yingziAssociation = Join-Path $yingziClasses '.yingziproj'
$yingziProgram = Join-Path $yingziClasses 'Yingzi.Project'
New-Item -Path $yingziAssociation -Force | Out-Null
Set-Item -LiteralPath $yingziAssociation -Value 'Yingzi.Project'
New-Item -Path $yingziProgram -Force | Out-Null
Set-Item -LiteralPath $yingziProgram -Value 'Yingzi subtitle project'
$yingziIcon = Join-Path $yingziProgram 'DefaultIcon'
New-Item -Path $yingziIcon -Force | Out-Null
Set-Item -LiteralPath $yingziIcon -Value ('"' + (Join-Path $yingziRoot 'runtime\yingzi.ico') + '",0')
$yingziCommand = Join-Path $yingziProgram 'shell\open\command'
New-Item -Path $yingziCommand -Force | Out-Null
Set-Item -LiteralPath $yingziCommand -Value ('"' + $yingziPython + '" "' + $yingziMain + '" "%1"')
& $yingziPython -c 'import ctypes; ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)'
Write-Output '.yingziproj files now open with Yingzi for the current Windows user.'
