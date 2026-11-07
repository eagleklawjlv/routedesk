"""Cached solar-system index built from CCP's public static data or ESI names."""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from everoute.paths import bundled_data_dir


@dataclass(frozen=True)
class SolarSystem:
    system_id: int
    name: str
    security: float | None
    region_name: str
    wormhole_class: int | None

    @property
    def band(self) -> str:
        # Classes 7–9 on k-space systems are not wormhole space. J-space ids start at 31000000.
        if self.system_id >= 31_000_000 or (self.wormhole_class or 0) >= 10:
            return "wormhole"
        if self.security is None:
            return "unknown"
        if self.security >= 0.45:
            return "high"
        if self.security > 0:
            return "low"
        return "null"


def security_text(system: SolarSystem | None, fallback: float | None = None) -> str:
    value = system.security if system is not None and system.security is not None else fallback
    if value is None:
        return "—"
    return f"{value:.2f}"


class SystemIndex:
    def __init__(self, systems: list[SolarSystem], meta: dict | None = None):
        self.systems = list(systems)
        self.meta = dict(meta or {})
        self.by_id = {system.system_id: system for system in self.systems}
        self.by_name: dict[str, SolarSystem] = {}
        for system in self.systems:
            self.by_name.setdefault(system.name.casefold(), system)

    def __len__(self) -> int:
        return len(self.systems)

    @classmethod
    def empty(cls) -> SystemIndex:
        return cls([], {"source": "none"})

    @classmethod
    def load(cls, path: Path) -> SystemIndex:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        systems = []
        for row in payload.get("systems") or []:
            system_id, name, security, region, wormhole = row
            systems.append(
                SolarSystem(
                    int(system_id),
                    str(name),
                    None if security is None else float(security),
                    str(region or ""),
                    int(wormhole) if wormhole else None,
                )
            )
        meta = {key: value for key, value in payload.items() if key != "systems"}
        return cls(systems, meta)

    def get(self, system_id: int) -> SolarSystem | None:
        return self.by_id.get(int(system_id))

    def exact(self, name: str) -> SolarSystem | None:
        return self.by_name.get(name.casefold())

    def search(self, query: str) -> list[SolarSystem]:
        """Every match, best rank first. No result cap."""
        text = query.casefold().strip()
        if not text:
            return []
        id_hits: list[SolarSystem] = []
        if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
            found = self.by_id.get(int(text))
            if found is not None:
                id_hits.append(found)
        exact: list[SolarSystem] = []
        prefix: list[SolarSystem] = []
        contain: list[SolarSystem] = []
        for system in self.systems:
            name = system.name.casefold()
            if name == text:
                exact.append(system)
            elif name.startswith(text):
                prefix.append(system)
            elif text in name:
                contain.append(system)
        prefix.sort(key=lambda system: system.name.casefold())
        contain.sort(key=lambda system: (len(system.name), system.name.casefold()))
        seen: set[int] = set()
        ordered: list[SolarSystem] = []
        for system in id_hits + exact + prefix + contain:
            if system.system_id in seen:
                continue
            seen.add(system.system_id)
            ordered.append(system)
        return ordered


def load_best_index(user_cache: Path | None = None) -> SystemIndex:
    candidates = []
    if user_cache is not None and user_cache.exists():
        candidates.append(user_cache)
    bundled = bundled_data_dir() / "systems.json"
    if bundled.exists():
        candidates.append(bundled)
    if not candidates:
        return SystemIndex.empty()
    best = max(candidates, key=lambda path: path.stat().st_mtime)
    return SystemIndex.load(best)


def _english_name(value) -> str:
    if isinstance(value, dict):
        return str(value.get("en") or "").strip()
    if value is None:
        return ""
    return str(value).strip()


def _find_member(archive: zipfile.ZipFile, filename: str) -> str:
    matches = [name for name in archive.namelist() if name.endswith(filename)]
    if not matches:
        raise FileNotFoundError(filename)
    return sorted(matches, key=len)[0]


def _iter_jsonl(archive: zipfile.ZipFile, filename: str):
    member = _find_member(archive, filename)
    with archive.open(member) as raw:
        for line in io.TextIOWrapper(raw, encoding="utf-8"):
            line = line.strip()
            if line:
                yield json.loads(line)


def build_cache_from_sde(zip_path: Path, dest: Path, sde_build: str | None = None) -> dict:
    """Write every named system. Records with no English name are counted and skipped."""
    zip_path = Path(zip_path)
    dest = Path(dest)
    regions: dict[int, str] = {}
    skipped = 0
    rows: list[list] = []
    with zipfile.ZipFile(zip_path) as archive:
        for record in _iter_jsonl(archive, "mapRegions.jsonl"):
            regions[int(record["_key"])] = _english_name(record.get("name"))
        for record in _iter_jsonl(archive, "mapSolarSystems.jsonl"):
            name = _english_name(record.get("name"))
            if not name:
                skipped += 1
                continue
            region_id = int(record.get("regionID") or 0)
            security = record.get("securityStatus")
            wormhole = record.get("wormholeClassID") or 0
            rows.append(
                [
                    int(record["_key"]),
                    name,
                    None if security is None else round(float(security), 5),
                    regions.get(region_id, ""),
                    int(wormhole) if wormhole else 0,
                ]
            )
    rows.sort(key=lambda row: row[1].casefold())
    payload = {
        "version": 1,
        "source": "CCP static data export mapSolarSystems + mapRegions",
        "sde_build": sde_build,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "system_count": len(rows),
        "skipped_without_english_name": skipped,
        "systems": rows,
    }
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return {
        "system_count": len(rows),
        "skipped_without_english_name": skipped,
        "sde_build": sde_build,
        "path": str(dest),
    }
