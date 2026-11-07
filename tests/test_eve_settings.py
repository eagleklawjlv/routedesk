"""Character settings copy uses synthetic files only. No live EVE profile is touched."""

import json
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from everoute.eve_settings import (
    KIND_USER,
    ClientsStillRunning,
    SettingsSyncError,
    apply_copies,
    describe_character,
    client_running_from_tasklist,
    exefile_listed,
    identify_profile,
    list_account_files,
    list_character_files,
    lookup_public_name,
    plan_copies,
    restore_manifest,
)
from everoute.gui.settings_sync import SettingsSyncDialog
from everoute.http import HttpClient, HttpResult
from everoute.models import Character

SOURCE = 900000001
TARGET_A = 900000002
TARGET_B = 900000003
ACCOUNT_SOURCE = 800000001
ACCOUNT_TARGET = 800000002


def profile_tree(root: Path, server_folder: str = "c_eve_sharedcache_tq_tranquility") -> Path:
    profile = root / server_folder / "settings_Default"
    profile.mkdir(parents=True)
    (profile / f"core_char_{SOURCE}.dat").write_bytes(b"SOURCE-CHAR")
    (profile / f"core_char_{TARGET_A}.dat").write_bytes(b"OLD-A")
    (profile / f"core_char_{TARGET_B}.dat").write_bytes(b"OLD-B")
    (profile / f"core_user_{ACCOUNT_SOURCE}.dat").write_bytes(b"ACCOUNT-SOURCE")
    (profile / f"core_user_{ACCOUNT_TARGET}.dat").write_bytes(b"ACCOUNT-OLD")
    (profile / "core_char_12.bak").write_bytes(b"ignore-bak")
    (profile / "LauncherLocal.db").write_bytes(b"ignore-launcher")
    (profile / "cache").mkdir()
    (profile / "cache" / f"core_char_{SOURCE}.dat").write_bytes(b"nested")
    return profile


def test_profile_server_comes_from_the_cache_folder_name(tmp_path):
    tranquility = profile_tree(tmp_path / "tq")
    assert identify_profile(tranquility).server == "tranquility"
    singularity = profile_tree(tmp_path / "sisi", "c_eve_sharedcache_sisi_singularity")
    assert identify_profile(singularity).server == "singularity"
    serenity = profile_tree(tmp_path / "cn", "c_eve_sharedcache_serenity")
    assert identify_profile(serenity).server == "serenity"
    with pytest.raises(SettingsSyncError, match="will not guess"):
        identify_profile(profile_tree(tmp_path / "both", "tq_singularity_tranquility"))
    with pytest.raises(SettingsSyncError, match="settings_Default"):
        identify_profile(tranquility.parent)


def test_listing_ignores_everything_except_exact_dat_names(tmp_path):
    profile = profile_tree(tmp_path)
    characters = list_character_files(profile)
    accounts = list_account_files(profile)
    assert [item.owner_id for item in characters] == [SOURCE, TARGET_A, TARGET_B]
    assert [item.owner_id for item in accounts] == [ACCOUNT_SOURCE, ACCOUNT_TARGET]
    assert all(item.path.parent == profile for item in characters + accounts)


def test_plan_excludes_source_and_refuses_an_empty_selection(tmp_path):
    profile = profile_tree(tmp_path)
    plans = plan_copies(profile, "core_char", SOURCE, [SOURCE, TARGET_A, TARGET_A, TARGET_B])
    assert [plan.target_id for plan in plans] == [TARGET_A, TARGET_B]
    with pytest.raises(SettingsSyncError, match="at least one target"):
        plan_copies(profile, "core_char", SOURCE, [SOURCE])
    with pytest.raises(SettingsSyncError, match="not in this profile"):
        plan_copies(profile, "core_char", 42, [TARGET_A])


def test_copy_replaces_character_bytes_and_records_hashes(tmp_path):
    profile = profile_tree(tmp_path)
    data = tmp_path / "routedesk-data"
    data.mkdir()
    (data / "settings.json").write_text("{}", encoding="utf-8")
    (data / "tokens").mkdir()
    plans = plan_copies(profile, "core_char", SOURCE, [TARGET_A, TARGET_B])
    manifest = apply_copies(profile, plans, data / "settings-sync-backups", clients_running=lambda: False)
    assert (profile / f"core_char_{TARGET_A}.dat").read_bytes() == b"SOURCE-CHAR"
    assert (profile / f"core_char_{TARGET_B}.dat").read_bytes() == b"SOURCE-CHAR"
    assert (profile / f"core_char_{SOURCE}.dat").read_bytes() == b"SOURCE-CHAR"
    assert (profile / f"core_user_{ACCOUNT_TARGET}.dat").read_bytes() == b"ACCOUNT-OLD"
    assert (data / "settings.json").read_text(encoding="utf-8") == "{}"
    assert manifest["server"] == "tranquility"
    assert {item["sha256_before"] for item in manifest["files"]} != {item["sha256_after"] for item in manifest["files"]}
    backup = Path(manifest["backup_dir"])
    assert (backup / f"core_char_{TARGET_A}.dat").read_bytes() == b"OLD-A"
    assert json.loads((backup / "manifest.json").read_text(encoding="utf-8"))["files"][0]["sha256_before"]


def test_failed_copy_rolls_back_and_a_running_client_writes_nothing(tmp_path, monkeypatch):
    profile = profile_tree(tmp_path)
    data = tmp_path / "backups"
    plans = plan_copies(profile, "core_char", SOURCE, [TARGET_A, TARGET_B])
    with pytest.raises(ClientsStillRunning):
        apply_copies(profile, plans, data, clients_running=lambda: True)
    assert (profile / f"core_char_{TARGET_A}.dat").read_bytes() == b"OLD-A"
    assert not data.exists()

    import everoute.eve_settings as module

    calls = {"n": 0}
    real = module._write_bytes_atomic

    def fail_second(target, payload):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full")
        real(target, payload)

    monkeypatch.setattr(module, "_write_bytes_atomic", fail_second)
    with pytest.raises(OSError, match="disk full"):
        apply_copies(profile, plans, data, clients_running=lambda: False)
    assert (profile / f"core_char_{TARGET_A}.dat").read_bytes() == b"OLD-A"
    assert (profile / f"core_char_{TARGET_B}.dat").read_bytes() == b"OLD-B"


def test_account_files_copy_only_when_explicitly_planned(tmp_path):
    profile = profile_tree(tmp_path)
    data = tmp_path / "backups"
    plans = plan_copies(profile, KIND_USER, ACCOUNT_SOURCE, [ACCOUNT_TARGET, ACCOUNT_SOURCE])
    assert [plan.target_id for plan in plans] == [ACCOUNT_TARGET]
    apply_copies(profile, plans, data, clients_running=lambda: False)
    assert (profile / f"core_user_{ACCOUNT_TARGET}.dat").read_bytes() == b"ACCOUNT-SOURCE"
    assert (profile / f"core_char_{TARGET_A}.dat").read_bytes() == b"OLD-A"


def test_restore_puts_the_backup_back_and_rejects_other_paths(tmp_path):
    profile = profile_tree(tmp_path)
    data = tmp_path / "backups"
    plans = plan_copies(profile, "core_char", SOURCE, [TARGET_A])
    manifest = apply_copies(profile, plans, data, clients_running=lambda: False)
    other = profile_tree(tmp_path / "other-server", "c_eve_sharedcache_sisi_singularity")
    with pytest.raises(SettingsSyncError, match="different profile"):
        restore_manifest(other, Path(manifest["backup_dir"]) / "manifest.json", data, clients_running=lambda: False)
    restore_manifest(
        profile,
        Path(manifest["backup_dir"]) / "manifest.json",
        data,
        clients_running=lambda: False,
    )
    assert (profile / f"core_char_{TARGET_A}.dat").read_bytes() == b"OLD-A"
    forged = Path(manifest["backup_dir"]) / "manifest.json"
    payload = json.loads(forged.read_text(encoding="utf-8"))
    payload["files"][0]["filename"] = "../core_char_1.dat"
    forged.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SettingsSyncError, match="not a character or account"):
        restore_manifest(profile, forged, data, clients_running=lambda: False)


def test_new_target_file_is_removed_on_restore(tmp_path):
    profile = profile_tree(tmp_path)
    plans = plan_copies(profile, "core_char", SOURCE, [900000009])
    assert plans[0].existed is False
    manifest = apply_copies(profile, plans, tmp_path / "backups", clients_running=lambda: False)
    assert (profile / "core_char_900000009.dat").read_bytes() == b"SOURCE-CHAR"
    restore_manifest(profile, Path(manifest["backup_dir"]) / "manifest.json", tmp_path / "backups", clients_running=lambda: False)
    assert not (profile / "core_char_900000009.dat").exists()


def test_symlink_settings_file_is_refused(tmp_path):
    profile = profile_tree(tmp_path)
    outside = tmp_path / "outside.dat"
    outside.write_bytes(b"secret")
    link = profile / "core_char_900000010.dat"
    link.symlink_to(outside)
    with pytest.raises(SettingsSyncError, match="outside the profile|link"):
        plan_copies(profile, "core_char", SOURCE, [900000010])
    assert outside.read_bytes() == b"secret"


def test_names_come_from_library_or_public_esi_and_are_not_invented():
    library = [
        Character(character_id=SOURCE, character_name="Authorized Pilot", demo=False),
        Character(character_id=TARGET_A, character_name="Demo Pilot Alpha", demo=True),
    ]
    assert describe_character(SOURCE, library) == "Authorized Pilot"
    assert "not a verified EVE name" in describe_character(TARGET_A, library)
    assert describe_character(TARGET_B, library) == "name not loaded"

    class Transport:
        def __init__(self):
            self.urls = []

        def request(self, method, url, headers=None, body=None, timeout=None):
            self.urls.append(url)
            if url.endswith(f"/{TARGET_B}/"):
                body_bytes = json.dumps({"name": "Public Pilot"}).encode()
                return HttpResult(200, {}, body_bytes, url)
            if url.endswith(f"/{SOURCE}/"):
                body_bytes = json.dumps({"name": "Someone Else"}).encode()
                return HttpResult(200, {}, body_bytes, "https://esi.evetech.net/latest/characters/42/")
            return HttpResult(404, {}, b"missing", url)

    transport = Transport()
    http = HttpClient(transport)
    assert lookup_public_name(TARGET_B, http) == "Public Pilot"
    assert lookup_public_name(SOURCE, http) is None
    assert lookup_public_name(TARGET_A, http) is None
    assert lookup_public_name(123456, http) is None
    assert all("esi.evetech.net" in url for url in transport.urls)
    assert "token" not in repr(transport.urls).lower()


def test_tasklist_parser_recognizes_only_the_eve_client():
    assert exefile_listed('"exefile.exe","1234","Console","1","20,000 K"') is True
    assert exefile_listed("INFO: No tasks are running matching the specified criteria.") is False
    assert exefile_listed('"evelauncher.exe","10","Console","1","1 K"') is False
    assert client_running_from_tasklist(0, "INFO: No tasks are running matching the specified criteria.") is False
    assert client_running_from_tasklist(0, "") is True
    assert client_running_from_tasklist(1, "exefile.exe") is True


def test_dialog_selects_targets_previews_the_subset_and_copies_once(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    profile = profile_tree(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    dialog = SettingsSyncDialog(data, [Character(TARGET_A, "Demo Pilot Alpha", demo=True)], client_running=lambda: False)
    dialog.set_fixture_banner("Synthetic fixture — not a live EVE profile.")
    dialog.load_profile(profile)
    assert "Tranquility" in dialog.server_label.text()
    assert "does not copy all EVE settings" in dialog.coverage.toPlainText()
    assert "will not guess" in dialog.coverage.toPlainText()
    dialog._set_all(dialog.targets, True)
    labels = [dialog.targets.item(index).text() for index in range(dialog.targets.count())]
    assert all(str(SOURCE) not in label for label in labels)
    assert len(labels) == 2
    preview = dialog.preview.toPlainText()
    assert f"core_char_{TARGET_A}.dat" in preview
    assert f"core_char_{TARGET_B}.dat" in preview
    assert "No core_user file would be written" in preview
    assert any("name not loaded" in label for label in labels)
    assert all("Demo Pilot Alpha" not in label for label in labels)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    dialog._client_running = lambda: True
    dialog.copy_clicked()
    assert warnings
    assert (profile / f"core_char_{TARGET_A}.dat").read_bytes() == b"OLD-A"
    dialog._client_running = lambda: False
    message = dialog.perform_copy()
    assert "Copied 2" in message
    assert (profile / f"core_char_{TARGET_A}.dat").read_bytes() == b"SOURCE-CHAR"
    assert (profile / f"core_user_{ACCOUNT_TARGET}.dat").read_bytes() == b"ACCOUNT-OLD"
    dialog.account_box.setChecked(True)
    dialog._set_all(dialog.account_targets, True)
    account_preview = dialog.preview.toPlainText()
    assert "core_user_" in account_preview
    dialog.close()
    app.processEvents()
