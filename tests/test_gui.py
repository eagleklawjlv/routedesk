import json

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from everoute.gui.main_window import MainWindow
from everoute.gui.theme import apply_theme
from everoute.messages import STORAGE_NOTE
from everoute.models import Stop
from everoute.paths import AppPaths
from everoute.sample import ALPHA_ID, ROUTE_TO_JITA, ROUTE_TO_SISEIDE, sample_index
from everoute.store import SettingsStore, Store
from everoute.universe import SolarSystem, SystemIndex
from everoute.waypoints import demo_send, execute_jobs


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    return app


def make_window(tmp_path, qapp):
    paths = AppPaths(tmp_path)
    return MainWindow(Store(paths), SettingsStore(paths), index=sample_index())


def test_routes_are_independent_in_the_window(qapp, tmp_path):
    window = make_window(tmp_path, qapp)
    window.select_route_by_name(ROUTE_TO_SISEIDE)
    assert window.stop_names() == ["Jita", "Ignoitton", "Siseide"]
    window.select_route_by_name(ROUTE_TO_JITA)
    window.set_leg(1, "cyno")
    window.select_route_by_name(ROUTE_TO_SISEIDE)
    assert window.leg_at(1) == "jump"
    assert window.stop_names() == ["Jita", "Ignoitton", "Siseide"]
    window.select_route_by_name(ROUTE_TO_JITA)
    assert window.leg_at(1) == "cyno"
    text = window.dump_widget_text()
    assert "Outbound" not in text
    assert "Save reversed copy" in text
    assert STORAGE_NOTE in text
    assert "does not enforce a maximum" in text
    window.close()


def test_window_scrolls_many_routes_and_stops_and_persists_them(qapp, tmp_path):
    window = make_window(tmp_path, qapp)
    base = window.route_count()
    for _ in range(35):
        window.new_route()
    assert window.route_count() == base + 35
    window.route_filter.setText("zzzz-no-such-route")
    assert window.visible_route_count() == 0
    assert window.route_count() == base + 35
    window.route_filter.clear()
    window.new_route()
    for index in range(55):
        window._append_stop(Stop(30_000_100 + index, f"Mid {index:03d}", "gate" if index else "origin"))
    assert window.stop_table.rowCount() == 55
    route_id = window.current_route_id()
    window.store.save(window.library)
    reloaded = Store(AppPaths(tmp_path)).load()
    route = reloaded.route(route_id)
    assert route is not None
    assert len(route.stops) == 55
    assert route.stops[-1].system_name == "Mid 054"
    assert len(reloaded.routes) == window.route_count()
    window.close()


def test_search_lists_every_match(qapp, tmp_path):
    window = make_window(tmp_path, qapp)
    systems = [SolarSystem(40_000_000 + index, f"Gate {index:03d}", 0.2, "Nowhere", None) for index in range(80)]
    window.index = SystemIndex(systems, {})
    window.system_search.setText("gate")
    window._run_search()
    assert window.search_results.count() == 80
    window.close()


def test_demo_apply_shows_ordered_progress(qapp, tmp_path):
    window = make_window(tmp_path, qapp)
    window.select_route_by_name(ROUTE_TO_SISEIDE)
    window.check_only([ALPHA_ID])
    window.set_target("selected")
    window.set_waypoint_mode("replace")
    jobs = window.collect_jobs()
    window.jobs = jobs
    window.prepare_progress(jobs)
    execute_jobs(jobs, demo_send, on_progress=lambda job, _index: window._paint_job(job))
    assert _progress_stops(window) == ["1. Jita", "2. Ignoitton", "3. Siseide"]
    assert "clear_other_waypoints=true" in window.progress_text()
    window.select_route_by_name(ROUTE_TO_JITA)
    window.set_waypoint_mode("append")
    jobs = window.collect_jobs()
    window.jobs = jobs
    window.prepare_progress(jobs)
    execute_jobs(jobs, demo_send, on_progress=lambda job, _index: window._paint_job(job))
    assert _progress_stops(window) == ["1. Siseide", "2. Ignoitton", "3. Jita"]
    assert "clear_other_waypoints=true" not in window.progress_text()
    window.close()


def test_reverse_button_adds_an_unlinked_route(qapp, tmp_path):
    window = make_window(tmp_path, qapp)
    window.select_route_by_name(ROUTE_TO_SISEIDE)
    before = window.route_count()
    window.reverse_selected()
    assert window.route_count() == before + 1
    assert window.stop_names() == ["Siseide", "Ignoitton", "Jita"]
    window.select_route_by_name(ROUTE_TO_SISEIDE)
    assert window.stop_names() == ["Jita", "Ignoitton", "Siseide"]
    window.close()


def _progress_stops(window) -> list[str]:
    return [window.progress.item(row, 2).text() for row in range(window.progress.rowCount())]


def test_session_demo_does_not_overwrite_saved_live_mode(qapp, tmp_path):
    from everoute.store import Settings

    from everoute.sample import sample_library

    paths = AppPaths(tmp_path)
    SettingsStore(paths).save(Settings(client_id="abcdef0123456789", demo_mode=False, seeded_sample=True))
    Store(paths).save(sample_library())
    window = MainWindow(Store(paths), SettingsStore(paths), index=sample_index(), seed_sample=False)
    assert window.effective_demo() is False
    window.use_session_demo()
    assert "DEMO" in window.banner.text()
    window.load_selected_route()
    assert SettingsStore(paths).load().demo_mode is False
    window.close()


def test_open_window_reloads_character_saved_by_the_cli(qapp, tmp_path):
    from everoute.models import Character

    window = make_window(tmp_path, qapp)
    fresh = window.store.load()
    fresh.characters.append(
        Character(
            character_id=555001,
            character_name="CLI Pilot",
            demo=False,
            scopes=["esi-ui.write_waypoint.v1"],
        )
    )
    window.store.save(fresh)
    window.settings.demo_mode = False
    window.settings.client_id = "abcdef0123456789"
    window.settings_store.save(window.settings)
    window.settings.demo_mode = True
    window.reload_account_from_disk()
    assert window.effective_demo() is False
    assert "CLI Pilot" in window.character_list.item(window.character_list.count() - 1).text()
    window.close()


def test_export_from_the_window_has_no_tokens(qapp, tmp_path):
    window = make_window(tmp_path, qapp)
    dest = tmp_path / "routes.json"
    window.export_routes_to_path(dest)
    payload = json.loads(dest.read_text(encoding="utf-8"))
    assert len(payload["routes"]) == window.route_count()
    assert "refresh_token" not in dest.read_text(encoding="utf-8")
    window.close()
