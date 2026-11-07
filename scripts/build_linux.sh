#!/bin/sh
# Optional Linux folder build. A Windows exe is not produced here.
set -eu
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt pyinstaller
.venv/bin/python -m PyInstaller --noconfirm --clean --windowed --name EveRouteDesk \
  --add-data "everoute/data:everoute/data" \
  --hidden-import keyring.backends.SecretService \
  launch.py
echo "If PyInstaller succeeded, the app is dist/EveRouteDesk/EveRouteDesk"
