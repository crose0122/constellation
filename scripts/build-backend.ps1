# Build the native Constellation backend (memoryvault-brain.exe) on Windows.
# Requires Python 3.11+ on PATH. Output: dist\memoryvault-brain\ (onedir bundle).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
python -m venv .buildvenv
& .\.buildvenv\Scripts\python.exe -m pip install --upgrade pip pyinstaller -r requirements-backend.txt
& .\.buildvenv\Scripts\pyinstaller.exe --noconfirm --clean constellation-backend.spec
Write-Host "Built: $PSScriptRoot\dist\memoryvault-brain\"
