import json
from pathlib import Path

from everoute.models import Library, Route, Stop, duplicate_route, reverse_route
from everoute.paths import AppPaths
from everoute.sample import (
    IGNOITTON_ID,
    JITA_ID,
    ROUTE_TO_JITA,
    ROUTE_TO_SISEIDE,
    SISEIDE_ID,
    sample_library,
)
from everoute.store import Store, export_routes, import_routes


def test_sample_routes_are_independent_and_use_verified_ids():
    library = sample_library()
    outbound = library.route_by_name(ROUTE_TO_SISEIDE)
    back = library.route_by_name(ROUTE_TO_JITA)
    assert [stop.system_id for stop in outbound.stops] == [JITA_ID, IGNOITTON_ID, SISEIDE_ID]
    assert [stop.system_id for stop in back.stops] == [SISEIDE_ID, IGNOITTON_ID, JITA_ID]
    assert "outbound" not in outbound.to_dict()
    assert "return" not in outbound.to_dict()
    back.stops[1].leg_type = "cyno"
    back.notes = "edited alone"
    assert outbound.stops[1].leg_type == "jump"
    assert outbound.notes != "edited alone"


def test_reverse_and_duplicate_do_not_link_or_mutate():
    library = sample_library()
    original = library.route_by_name(ROUTE_TO_SISEIDE)
    before = [stop.system_id for stop in original.stops]
    flipped = reverse_route(original)
    copied = duplicate_route(original)
    copied.stops[0].system_name = "Changed"
    flipped.stops[0].system_name = "Changed too"
    assert [stop.system_id for stop in original.stops] == before
    assert original.stops[0].system_name == "Jita"
    assert [stop.system_id for stop in flipped.stops] == [SISEIDE_ID, IGNOITTON_ID, JITA_ID]
    assert flipped.id != original.id
    assert copied.id != original.id
    assert flipped.stops[1].leg_type == "jump"
    assert flipped.stops[2].leg_type == "jump"


def test_many_routes_and_long_route_roundtrip(tmp_path):
    library = Library()
    long_stops = [
        Stop(
            system_id=30_000_001 + index,
            system_name=f"Sys-{index:04d}",
            leg_type="origin" if index == 0 else "gate",
        )
        for index in range(1000)
    ]
    library.routes.append(Route(id="long", name="Long haul", stops=long_stops, notes="kept whole"))
    for index in range(400):
        library.routes.append(
            Route(
                id=f"r{index}",
                name=f"Route {index}",
                stops=[Stop(JITA_ID, "Jita", "origin")],
            )
        )
    store = Store(AppPaths(tmp_path))
    store.save(library)
    loaded = store.load()
    assert len(loaded.routes) == 401
    long = loaded.route("long")
    assert long is not None
    assert len(long.stops) == 1000
    assert [stop.system_id for stop in long.stops] == [30_000_001 + index for index in range(1000)]
    assert long.stops[0].system_name == "Sys-0000"
    assert long.stops[-1].system_name == "Sys-0999"
    assert long.notes == "kept whole"
    assert loaded.route("r399").name == "Route 399"


def test_import_export_keeps_every_stop_and_adds_copies(tmp_path):
    library = sample_library()
    payload = export_routes(library.routes)
    blob = json.dumps(payload)
    assert "refresh_token" not in blob
    imported = import_routes(json.loads(blob))
    assert len(imported) == 2
    assert [stop.system_id for stop in imported[0].stops] == [stop.system_id for stop in library.routes[0].stops]
    assert imported[0].id != library.routes[0].id
    again = import_routes(json.loads(blob))
    library.routes.extend(imported)
    library.routes.extend(again)
    store = Store(AppPaths(tmp_path))
    store.save(library)
    assert len(store.load().routes) == 6


def test_import_rejects_garbage():
    try:
        import_routes({"format": "other", "version": 1, "routes": []})
    except ValueError as exc:
        assert "not a Route Desk" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_library_file_does_not_contain_refresh_tokens(tmp_path):
    from everoute.credentials import FileCredentialStore
    from everoute.sample import ALPHA_ID

    paths = AppPaths(tmp_path)
    store = Store(paths)
    store.save(sample_library())
    FileCredentialStore(paths.tokens).set_refresh_token(ALPHA_ID, "super-secret-refresh-token")
    text = paths.library.read_text(encoding="utf-8")
    assert "super-secret-refresh-token" not in text
    assert "refresh_token" not in text
    mode = (paths.tokens / f"{ALPHA_ID}.token").stat().st_mode & 0o777
    assert mode == 0o600


def test_no_artificial_cap_constants():
    root = Path(__file__).resolve().parents[1] / "everoute"
    banned = ("MAX_ROUTES", "MAX_STOPS", "MAX_WAYPOINTS", "max_routes", "max_stops")
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in banned:
            assert token not in text, f"{path} contains {token}"
