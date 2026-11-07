"""Per-user data locations. Override with EVEROUTE_DATA_DIR for tests."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


def bundled_data_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS")) / "everoute" / "data"
    return Path(__file__).resolve().parent / "data"


@dataclass
class AppPaths:
    root: Path

    @property
    def library(self) -> Path:
        return self.root / "library.json"

    @property
    def jobs(self) -> Path:
        return self.root / "jobs.json"

    @property
    def settings(self) -> Path:
        return self.root / "settings.json"

    @property
    def cache(self) -> Path:
        return self.root / "systems.json"

    @property
    def stations(self) -> Path:
        return self.root / "stations.json"

    @property
    def structure_cache(self) -> Path:
        return self.root / "structure-cache"

    @property
    def tokens(self) -> Path:
        return self.root / "tokens"

    @property
    def sso_cache(self) -> Path:
        return self.root / "sso-metadata.json"

    @property
    def log(self) -> Path:
        return self.root / "app.log"


def default_paths() -> AppPaths:
    override = os.environ.get("EVEROUTE_DATA_DIR")
    if override:
        root = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
        root = base / "EveRouteDesk"
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support" / "EveRouteDesk"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
        root = base / "everoute-desk"
    root.mkdir(parents=True, exist_ok=True)
    return AppPaths(root)
