"""NPC stations grouped by solar system.

Names come from public ESI POST /universe/names. System membership comes from
CCP's static data export. No character token is required.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

from everoute.paths import bundled_data_dir


@dataclass(frozen=True)
class NpcStation:
    station_id: int
    system_id: int
    name: str


class StationIndex:
    def __init__(self, stations: list[NpcStation]):
        self.by_id = {station.station_id: station for station in stations}
        self.by_system: dict[int, list[NpcStation]] = {}
        for station in stations:
            self.by_system.setdefault(station.system_id, []).append(station)
        for rows in self.by_system.values():
            rows.sort(key=lambda station: station.name.casefold())

    def __len__(self) -> int:
        return len(self.by_id)

    @classmethod
    def empty(cls) -> StationIndex:
        return cls([])

    @classmethod
    def load(cls, path: Path) -> StationIndex:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        stations = [
            NpcStation(int(station_id), int(system_id), str(name))
            for station_id, system_id, name in payload.get("stations") or []
        ]
        return cls(stations)

    def get(self, station_id: int) -> NpcStation | None:
        return self.by_id.get(int(station_id))

    def in_system(self, system_id: int) -> list[NpcStation]:
        return list(self.by_system.get(int(system_id), []))


def load_best_stations(user_cache: Path | None = None) -> StationIndex:
    candidates = []
    if user_cache is not None and user_cache.exists():
        candidates.append(user_cache)
    bundled = bundled_data_dir() / "stations.json"
    if bundled.exists():
        candidates.append(bundled)
    if not candidates:
        return StationIndex.empty()
    best = max(candidates, key=lambda path: path.stat().st_mtime)
    return StationIndex.load(best)


def station_rows_from_sde(zip_path: Path) -> list[tuple[int, int]]:
    rows: list[tuple[int, int]] = []
    with zipfile.ZipFile(zip_path) as archive:
        member = next(name for name in archive.namelist() if name.endswith("npcStations.jsonl"))
        with archive.open(member) as raw:
            for line in raw:
                if not line.strip():
                    continue
                record = json.loads(line)
                rows.append((int(record["_key"]), int(record["solarSystemID"])))
    return rows


def build_station_cache(zip_path: Path, names: dict[int, str], dest: Path) -> dict:
    """Write every NPC station. A missing public name is stored as Station {id}, not dropped."""
    skipped_names = 0
    packed = []
    for station_id, system_id in station_rows_from_sde(zip_path):
        name = names.get(station_id)
        if not name:
            skipped_names += 1
            name = f"Station {station_id}"
        packed.append([station_id, system_id, name])
    packed.sort(key=lambda row: (row[1], row[2].casefold()))
    payload = {
        "version": 1,
        "source": "CCP SDE npcStations solarSystemID plus public ESI POST /universe/names",
        "station_count": len(packed),
        "unnamed_count": skipped_names,
        "stations": packed,
    }
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return {"station_count": len(packed), "unnamed_count": skipped_names, "path": str(dest)}
