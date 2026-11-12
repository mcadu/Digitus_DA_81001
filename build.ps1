$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) { Write-Error 'uv is required. Install uv, then rerun this script.'; exit 1 }

Write-Host 'Syncing locked dependencies...'
& uv sync --locked
if ($LASTEXITCODE -ne 0) { Write-Error 'Dependency sync failed, skipping exe build.'; exit 1 }

$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'

Write-Host 'Checking sources compile...'
& $python -m py_compile BarcodeReader.py BarcodeReaderUI.py
if ($LASTEXITCODE -ne 0) { Write-Error 'Compilation failed, skipping exe build.'; exit 1 }

Write-Host 'Building exe with PyInstaller...'
& $python -m PyInstaller --noconfirm --clean BarcodeReader.spec
if ($LASTEXITCODE -ne 0) { Write-Error 'PyInstaller failed.'; exit 1 }

# The exe reads user.json and books.json from its own folder; keep existing (edited) copies.
if (-not (Test-Path 'dist\user.json')) { Copy-Item user.json dist\user.json }
if (-not (Test-Path 'dist\books.json')) { Copy-Item books.json dist\books.json }

Write-Host 'Done: dist\BarcodeReader.exe'
