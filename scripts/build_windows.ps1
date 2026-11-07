# Build a windowed Route Desk folder on Windows.
# This script was not run in the environment that prepared the source tree.
# It does not mean EveRouteDesk.exe already exists.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --windowed --name EveRouteDesk `
  --add-data "everoute\data;everoute\data" `
  --hidden-import keyring.backends.Windows `
  launch.py
Write-Host ""
Write-Host "If PyInstaller succeeded, start dist\EveRouteDesk\EveRouteDesk.exe"
Write-Host "That exe is produced on your machine by this script. It was not compiled here."
Write-Host "Qt/PySide6 is LGPL. Prefer running from source if you do not want to ship a frozen build."
