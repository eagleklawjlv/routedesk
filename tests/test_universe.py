import json
import zipfile
from pathlib import Path

from everoute.paths import bundled_data_dir
from everoute.sample import IGNOITTON_ID, JITA_ID, SISEIDE_ID
from everoute.universe import SystemIndex, build_cache_from_sde


def test_search_returns_every_match_without_a_cap():
    systems = []
    from everoute.universe import SolarSystem

    for index in range(120):
        systems.append(SolarSystem(30000000 + index, f"Gate {index:03d}", 0.5, "Test", None))
    systems.append(SolarSystem(JITA_ID, "Jita", 0.94591, "The Forge", None))
    index = SystemIndex(systems, {})
    matches = index.search("gate")
    assert len(matches) == 120
    assert index.exact("Jita").system_id == JITA_ID
    assert index.search("30000142")[0].name == "Jita"
    assert index.search("") == []


def test_sde_parser_keeps_named_systems(tmp_path: Path):
    region = {"_key": 10000002, "name": {"en": "The Forge"}}
    jita = {
        "_key": JITA_ID,
        "name": {"en": "Jita"},
        "regionID": 10000002,
        "securityStatus": 0.94591,
    }
    blank = {"_key": 30000002, "name": {"en": ""}, "regionID": 10000002, "securityStatus": 0.1}
    archive_path = tmp_path / "sde.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("mapRegions.jsonl", json.dumps(region) + "\n")
        archive.writestr("mapSolarSystems.jsonl", json.dumps(jita) + "\n" + json.dumps(blank) + "\n")
    dest = tmp_path / "systems.json"
    meta = build_cache_from_sde(archive_path, dest, sde_build="test")
    assert meta["system_count"] == 1
    assert meta["skipped_without_english_name"] == 1
    loaded = SystemIndex.load(dest)
    system = loaded.exact("Jita")
    assert system.system_id == JITA_ID
    assert system.region_name == "The Forge"
    assert system.security == 0.94591


def test_shipped_cache_matches_public_ids():
    path = bundled_data_dir() / "systems.json"
    if not path.exists():
        return
    index = SystemIndex.load(path)
    assert index.exact("Jita").system_id == JITA_ID
    assert index.exact("Jita").band == "high"
    assert index.exact("Ignoitton").system_id == IGNOITTON_ID
    assert index.exact("Ignoitton").band == "low"
    assert index.exact("Siseide").system_id == SISEIDE_ID
    assert index.exact("Siseide").region_name == "Heimatar"
    assert index.exact("J100001").band == "wormhole"
    assert len(index) > 1000
    blob = path.read_text(encoding="utf-8")
    assert "Jita" in blob
