import json

import pytest

from everoute.__main__ import main
from everoute.account import configure_sso, validate_client_id
from everoute.cli import command_authorize
from everoute.credentials import MemoryCredentialStore
from everoute.models import Library, Route, Stop
from everoute.paths import AppPaths
from everoute.store import SettingsStore, Store

PUBLIC_ID = "abcdef0123456789"


def test_setup_turns_demo_off_and_keeps_routes(tmp_path, capsys):
    paths = AppPaths(tmp_path)
    library = Library(routes=[Route(id="keep", name="Kept", stops=[Stop(30000142, "Jita", "origin")])])
    Store(paths).save(library)
    before = paths.library.read_text(encoding="utf-8")
    code = main(
        ["--data-dir", str(tmp_path), "setup", "--client-id", PUBLIC_ID, "--host", "127.0.0.1", "--port", "45831"]
    )
    assert code == 0
    settings = SettingsStore(paths).load()
    assert settings.demo_mode is False
    assert settings.client_id == PUBLIC_ID
    assert settings.redirect_uri() == "http://127.0.0.1:45831/callback"
    assert paths.library.read_text(encoding="utf-8") == before
    assert "refresh_token" not in paths.settings.read_text(encoding="utf-8")
    output = capsys.readouterr().out
    assert "Demo mode: off" in output
    assert "No waypoint request was sent" in output
    assert Store(paths).load().route("keep").name == "Kept"


def test_secret_flags_and_secret_shaped_ids_are_refused(tmp_path, capsys):
    assert main(["setup", "--client-secret", "nope"]) == 2
    assert main(["authorize", "--password", "hunter2"]) == 2
    assert main(["--data-dir", str(tmp_path), "setup", "--client-id", "super-secret-id"]) == 2
    with pytest.raises(ValueError):
        validate_client_id("aaa.bbb.ccc")
    err = capsys.readouterr().err
    assert "password" in err.lower() or "secret" in err.lower()
    assert not (tmp_path / "settings.json").exists() or "super-secret-id" not in (tmp_path / "settings.json").read_text()


def test_authorize_saves_character_without_a_waypoint_call(tmp_path, monkeypatch, capsys):
    paths = AppPaths(tmp_path)
    configure_sso(SettingsStore(paths), client_id=PUBLIC_ID, host="127.0.0.1", port=45831)
    creds = MemoryCredentialStore()
    calls = []

    def fake_login(client, port, open_browser, timeout=300, scopes=None):
        calls.append(("login", port, timeout))
        open_browser("https://login.eveonline.com/v2/oauth/authorize?response_type=code&client_id=" + PUBLIC_ID)
        return (
            {"access_token": "access-not-stored", "refresh_token": "refresh-not-printed", "expires_in": 1200},
            {
                "sub": "CHARACTER:EVE:555001",
                "name": "CLI Pilot",
                "scp": ["esi-ui.write_waypoint.v1"],
            },
        )

    def forbid_http(self, *args, **kwargs):
        raise AssertionError("authorize must not call ESI or SSO in this test double")

    monkeypatch.setattr("everoute.http.HttpClient.request", forbid_http)
    from argparse import Namespace

    code = command_authorize(Namespace(timeout=30), paths, login=fake_login, credentials=creds)
    assert code == 0
    assert calls and calls[0][1] == 45831
    library = Store(paths).load()
    character = library.character(555001)
    assert character is not None
    assert character.character_name == "CLI Pilot"
    assert character.demo is False
    text = paths.library.read_text(encoding="utf-8")
    assert "refresh-not-printed" not in text
    assert "access-not-stored" not in text
    assert creds.get_refresh_token(555001) == "refresh-not-printed"
    output = capsys.readouterr().out
    assert "does not set waypoints" in output
    assert "refresh-not-printed" not in output
    assert json.loads(text)["characters"][-1]["character_name"] == "CLI Pilot"


def test_status_hides_tokens(tmp_path, capsys):
    paths = AppPaths(tmp_path)
    configure_sso(SettingsStore(paths), client_id=PUBLIC_ID)
    code = main(["--data-dir", str(tmp_path), "status"])
    assert code == 0
    output = capsys.readouterr().out
    assert "Demo mode: off" in output
    assert "Client ID saved: yes" in output
    assert PUBLIC_ID not in output or "saved: yes" in output
    assert "refresh" not in output.lower()
