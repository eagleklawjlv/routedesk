import json

from everoute.esi import EsiClient
from everoute.http import HttpClient, HttpResult
from everoute.messages import SCOPE
from everoute.models import KIND_STATION, KIND_STRUCTURE, Character, Library, Route, Stop, reverse_route
from everoute.stations import NpcStation, StationIndex
from everoute.structures import StructureCache, StructureDirectory, visibility_label
from everoute.waypoints import execute_jobs, plan_jobs

JITA = 30000142
NAVY = 60003760
STRUCTURE = 10_312_345_678_901


class ScriptHttp:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def request(self, method, url, *, headers=None, body=None, timeout=None):
        self.calls.append({"method": method, "url": url, "headers": headers or {}, "body": body})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_old_route_stop_migrates_to_system_destination():
    stop = Stop.from_dict({"system_id": JITA, "system_name": "Jita", "leg_type": "origin"})
    assert stop.destination_id == JITA
    assert stop.destination_kind == "system"
    again = Stop.from_dict(stop.to_dict())
    assert again.destination_id == JITA


def test_station_roundtrip_reverse_and_waypoint_id():
    station = Stop(
        system_id=JITA,
        system_name="Jita",
        leg_type="origin",
        destination_id=NAVY,
        destination_name="Jita IV - Moon 4 - Caldari Navy Assembly Plant",
        destination_kind=KIND_STATION,
    )
    route = Route(id="jita", name="Jita dock", stops=[station, Stop(30002647, "Ignoitton", "jump")])
    flipped = reverse_route(route)
    assert flipped.stops[0].system_name == "Ignoitton"
    assert flipped.stops[1].destination_id == NAVY
    assert flipped.stops[1].system_id == JITA
    blob = json.dumps(route.to_dict())
    loaded = Route.from_dict(json.loads(blob))
    assert loaded.stops[0].destination_id == NAVY
    library = Library(routes=[route], characters=[Character(1, "Pilot", demo=True)])
    jobs = plan_jobs(library, [1], target="selected", mode="replace", selected_route_id="jita")
    calls = []

    def send(_character_id, destination_id, add, clear):
        calls.append(destination_id)
        from everoute.esi import Outcome, OutcomeKind

        return Outcome(OutcomeKind.APPLIED, "ok", 204)

    execute_jobs(jobs, send)
    assert calls == [NAVY, 30002647]


def test_large_structure_id_survives_json_and_esi_query():
    stop = Stop(
        system_id=JITA,
        system_name="Jita",
        destination_id=STRUCTURE,
        destination_name="Keepstar",
        destination_kind=KIND_STRUCTURE,
        seen_by=[555001],
        visibility="Visible via Alpha — docking unverified",
    )
    text = json.dumps(stop.to_dict())
    assert str(STRUCTURE) in text
    assert Stop.from_dict(json.loads(text)).destination_id == STRUCTURE
    http = ScriptHttp([HttpResult(204, {}, b"", "https://esi.evetech.net/x")])
    EsiClient(HttpClient(http)).set_waypoint("token", STRUCTURE, add_to_beginning=False, clear_other_waypoints=False)
    assert f"destination_id={STRUCTURE}" in http.calls[0]["url"]
    assert "token" not in http.calls[0]["url"]


def test_shipped_jita_navy_station():
    from everoute.paths import bundled_data_dir

    path = bundled_data_dir() / "stations.json"
    if not path.exists():
        return
    index = StationIndex.load(path)
    navy = index.get(NAVY)
    assert navy is not None
    assert navy.system_id == JITA
    assert "Caldari Navy Assembly Plant" in navy.name
    assert len(index.in_system(JITA)) >= 10


def test_npc_station_dropdown_data():
    index = StationIndex(
        [
            NpcStation(NAVY, JITA, "Jita IV - Moon 4 - Caldari Navy Assembly Plant"),
            NpcStation(60000361, JITA, "Jita IV - Moon 4 - Caldari Business Tribunal"),
            NpcStation(60008494, 30002187, "Amarr Emperor Family Academy"),
        ]
    )
    names = [station.name for station in index.in_system(JITA)]
    assert names == sorted(names, key=str.casefold)
    assert len(names) == 2
    assert index.get(NAVY).system_id == JITA
    assert index.in_system(30002187)[0].station_id == 60008494


def test_structure_visibility_is_per_character_and_not_called_docking(tmp_path):
    http = ScriptHttp(
        [
            HttpResult(200, {}, json.dumps({"structure": [STRUCTURE]}).encode(), "https://esi.evetech.net/search"),
            HttpResult(
                200,
                {},
                json.dumps({"name": "Jita Trade Fortizar", "solar_system_id": JITA, "type_id": 35833}).encode(),
                "https://esi.evetech.net/structure",
            ),
            HttpResult(200, {}, json.dumps({"structure": [STRUCTURE]}).encode(), "https://esi.evetech.net/search"),
            HttpResult(403, {}, b'{"error":"Forbidden"}', "https://esi.evetech.net/structure"),
        ]
    )
    directory = StructureDirectory(http, StructureCache(tmp_path))
    alpha = Character(11, "Alpha", demo=False, scopes=[SCOPE, "esi-search.search_structures.v1", "esi-universe.read_structures.v1"])
    bravo = Character(22, "Bravo", demo=False, scopes=list(alpha.scopes))
    discovery = directory.discover([alpha, bravo], "fort", lambda _character_id: "test-token")
    assert len(discovery.hits) == 1
    hit = discovery.hits[0]
    assert hit.structure_id == STRUCTURE
    assert [character_id for character_id, _name in hit.seen_by] == [11]
    assert hit.label() == "Visible via Alpha — docking unverified"
    assert "you can dock" not in hit.label().lower()
    assert any(problem.character_name == "Bravo" and problem.kind == "failed" for problem in discovery.problems)
    assert json.loads((tmp_path / "22.json").read_text())["entries"][str(STRUCTURE)]["ok"] is False
    assert json.loads((tmp_path / "11.json").read_text())["entries"][str(STRUCTURE)]["ok"] is True
    assert all("test-token" not in call["url"] for call in http.calls)
    assert http.calls[0]["headers"]["Authorization"] == "Bearer test-token"


def test_missing_scope_does_not_call_esi_and_waypoint_scope_still_counts(tmp_path):
    http = ScriptHttp([])
    directory = StructureDirectory(http, StructureCache(tmp_path))
    pilot = Character(7, "Waypoint Only", demo=False, scopes=[SCOPE])
    discovery = directory.discover([pilot], "fortizar", lambda _character_id: "token")
    assert discovery.hits == []
    assert discovery.problems[0].kind == "missing_scope"
    assert "esi-search.search_structures.v1" in discovery.problems[0].message
    assert http.calls == []
    assert visibility_label(["Waypoint Only"]).endswith("docking unverified")


def test_gui_destination_combo_lists_npc_stations(tmp_path):
    pytest = __import__("pytest")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from everoute.gui.main_window import MainWindow
    from everoute.gui.theme import apply_theme
    from everoute.paths import AppPaths
    from everoute.sample import sample_index
    from everoute.store import SettingsStore, Store

    QApplication.instance() or QApplication([])
    apply_theme(QApplication.instance())

    window = MainWindow(Store(AppPaths(tmp_path)), SettingsStore(AppPaths(tmp_path)), index=sample_index())
    window.station_index = StationIndex([NpcStation(NAVY, JITA, "Jita IV - Moon 4 - Caldari Navy Assembly Plant")])
    window.select_route_by_name("Jita - Ignoitton - Siseide")
    window.fill_destination_combo(JITA)
    assert window.destination_combo.count() == 2
    assert window.destination_combo.itemText(0).startswith("System only")
    assert "Caldari Navy" in window.destination_combo.itemText(1)
    window.destination_combo.setCurrentIndex(1)
    window.add_selected_search_hit()
    added = window.current_route().stops[-1]
    assert added.destination_id == NAVY
    assert added.system_id == JITA
    window.close()
