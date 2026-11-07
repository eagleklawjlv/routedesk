"""GUI login buttons must not pass the window where a scope list belongs."""

import time
import urllib.parse

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from everoute.gui.main_window import MainWindow
from everoute.gui.theme import apply_theme
from everoute.gui.workers import LoginWorker
from everoute.messages import READ_STRUCTURES_SCOPE, SCOPE, SEARCH_STRUCTURES_SCOPE
from everoute.models import Character
from everoute.oauth import AuthError, OAuthClient
from everoute.paths import AppPaths
from everoute.sample import sample_index
from everoute.store import SettingsStore, Store


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    return app


def _window(tmp_path, qapp):
    window = MainWindow(Store(AppPaths(tmp_path)), SettingsStore(AppPaths(tmp_path)), index=sample_index())
    window.settings.demo_mode = False
    window.settings.client_id = "abcdef0123456789"
    window._ensure_tokens = lambda: None
    window._oauth_client = lambda: OAuthClient(object(), "abcdef0123456789", "http://127.0.0.1:45831/callback")
    return window


def test_parent_window_is_not_the_scope_list(qapp):
    parent = QWidget()
    worker = LoginWorker(object(), 45831, parent)
    assert worker.scopes is None
    assert worker.parent() is parent
    scoped = LoginWorker(object(), 45831, parent, scopes=[SCOPE, SEARCH_STRUCTURES_SCOPE])
    assert scoped.scopes == [SCOPE, SEARCH_STRUCTURES_SCOPE]
    parent.deleteLater()


def test_authorize_and_reauthorize_forward_scopes(qapp, tmp_path, monkeypatch):
    seen = []

    def fake_login(client, port, open_browser, timeout=300, scopes=None, cancel=None):
        seen.append(list(scopes) if scopes is not None else None)
        open_browser("https://login.eveonline.com/v2/oauth/authorize?response_type=code")
        raise AuthError("stopped before any token exchange")

    monkeypatch.setattr("everoute.gui.workers.run_browser_login", fake_login)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    dialogs = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args, **kwargs: dialogs.append(args) or QMessageBox.StandardButton.Ok,
    )
    window = _window(tmp_path, qapp)
    window.library.characters.append(Character(9001, "Eagleklaw", demo=False, scopes=[SCOPE]))
    window._save_library()
    before = window.store.paths.library.read_text(encoding="utf-8")

    window.authorize_character()
    assert window._login_worker.wait(3000)
    QApplication.processEvents()
    assert seen == [None]
    assert "not iterable" not in window.statusBar().currentMessage()
    assert "stopped before any token exchange" in window.statusBar().currentMessage()
    assert dialogs and "stopped before any token exchange" in dialogs[-1][2]
    assert window.store.paths.library.read_text(encoding="utf-8") == before

    window.reauthorize_structures()
    assert window._login_worker.wait(3000)
    QApplication.processEvents()
    assert seen[1] == [SCOPE, SEARCH_STRUCTURES_SCOPE, READ_STRUCTURES_SCOPE]
    assert "Eagleklaw" in before
    window.close()


def test_cancel_login_does_not_store_a_character(qapp, tmp_path, monkeypatch):
    def fake_login(client, port, open_browser, timeout=300, scopes=None, cancel=None):
        open_browser("https://login.eveonline.com/v2/oauth/authorize?response_type=code")
        deadline = time.time() + 2
        while time.time() < deadline:
            if cancel is not None and cancel.is_set():
                raise AuthError("Login cancelled. No token was stored.")
            time.sleep(0.01)
        raise AuthError("should have cancelled")

    monkeypatch.setattr("everoute.gui.workers.run_browser_login", fake_login)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    window = _window(tmp_path, qapp)
    before = [character.character_id for character in window.library.characters]
    window.authorize_character()
    assert window._login_worker.isRunning()
    window._cancel_login()
    QApplication.processEvents()
    assert "cancelled" in window.statusBar().currentMessage().lower()
    assert [character.character_id for character in window.library.characters] == before
    window.close()


class _OfflineHttp:
    def get_json(self, *args, **kwargs):
        raise OSError("offline metadata")

    def request(self, *args, **kwargs):
        raise AssertionError("login test must not call the network")


def _scopes_from_url(url: str) -> list[str]:
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    raw = query["scope"][0]
    return raw.split()


def _click(window, label: str):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    matches = [button for button in window.findChildren(type(window.add_sso_button)) if button.text() == label]
    assert matches, label
    window.show()
    QApplication.processEvents()
    QTest.mouseClick(matches[0], Qt.MouseButton.LeftButton)
    QApplication.processEvents()


def test_button_clicks_put_the_expected_scopes_in_the_authorize_url(qapp, tmp_path, monkeypatch):
    urls = []
    prompts = []

    def fake_open(url, *args, **kwargs):
        urls.append(url)
        raise AuthError("stop before the callback")

    def question(parent, title, body, *args, **kwargs):
        prompts.append((title, body))
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr("webbrowser.open", fake_open)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "question", question)
    window = _window(tmp_path, qapp)
    window.settings_store.save(window.settings)
    window._oauth_client = lambda: OAuthClient(
        _OfflineHttp(), "abcdef0123456789", "http://127.0.0.1:45831/callback"
    )
    _click(window, "Authorize character")
    assert window._login_worker.wait(3000)
    QApplication.processEvents()
    authorize_scopes = _scopes_from_url(urls[0])
    assert authorize_scopes == [SCOPE]
    assert SEARCH_STRUCTURES_SCOPE not in urls[0]
    assert prompts[0][0] == "Authorize character"
    assert SCOPE in prompts[0][1]
    assert SEARCH_STRUCTURES_SCOPE not in prompts[0][1]
    assert "Checkboxes" in prompts[0][1]

    _click(window, "Reauthorize for structure search")
    assert window._login_worker.wait(3000)
    QApplication.processEvents()
    reauthorize_scopes = _scopes_from_url(urls[1])
    assert reauthorize_scopes == [SCOPE, SEARCH_STRUCTURES_SCOPE, READ_STRUCTURES_SCOPE]
    assert "client_secret" not in urls[1]
    assert prompts[1][0] == "Reauthorize for structure search"
    assert SEARCH_STRUCTURES_SCOPE in prompts[1][1]
    assert READ_STRUCTURES_SCOPE in prompts[1][1]
    assert "not only the waypoint scope" in prompts[1][1]
    window.close()


def test_scope_list_must_be_a_list():
    client = OAuthClient(object(), "abcdef0123456789", "http://127.0.0.1:45831/callback")
    with pytest.raises(AuthError, match="list of scope names"):
        client.authorization_url(object())
