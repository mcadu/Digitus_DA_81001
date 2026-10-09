$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = 'python' }

Write-Host 'Checking sources compile...'
& $python -m py_compile BarcodeReader.py BarcodeReaderUI.py
if ($LASTEXITCODE -ne 0) { Write-Error 'Compilation failed, skipping exe build.'; exit 1 }

Write-Host 'Building exe with PyInstaller...'
& $python -m PyInstaller --noconfirm --clean BarcodeReader.spec
if ($LASTEXITCODE -ne 0) { Write-Error 'PyInstaller failed.'; exit 1 }

Write-Host 'Done: dist\BarcodeReader.exe'
