"""Copy EVE client settings files inside one launcher profile.

CCP documents profiles as folders named settings_<name> under the shared-cache
directory (%LOCALAPPDATA%\\CCP\\EVE\\<drive>_..._tranquility\\ on Windows).
CCP does not publish the contents of the .dat files. Established client-side
practice, repeated on the EVE forums and in community guides, is:

- core_char_<character id>.dat is one character's file. Guides use a byte copy
  of this file for overview and per-character UI. The character id is the
  public id in the filename.
- core_user_<account id>.dat is one account's file, shared by every character
  on that account. The account id is not the character id. Nothing in the
  filename or in public ESI maps a character to an account.

This module copies those files unchanged. It does not decode them, and it does
not copy launcher login data, cache, or any other filename.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable

from everoute.http import HttpClient, HttpError

CHAR_FILENAME = re.compile(r"^core_char_([1-9][0-9]*)\.dat$", re.IGNORECASE)
USER_FILENAME = re.compile(r"^core_user_([1-9][0-9]*)\.dat$", re.IGNORECASE)
SETTINGS_DIR = re.compile(r"^settings_.+", re.IGNORECASE)

KIND_CHAR = "core_char"
KIND_USER = "core_user"

CCP_PROFILE_ARTICLE = "https://support.eveonline.com/hc/en-us/articles/5885278408860-Profile-Manager"

INSTRUCTIONS = (
    "1. Set up the source character in EVE, then log that client out.\n"
    "2. Close every EVE client so the game cannot overwrite these files on exit.\n"
    "3. Choose the settings profile folder, one source character, and the target characters.\n"
    "4. Read the list of files that will be replaced, then press Copy.\n"
    "5. Launch EVE and check one target character before you rely on the rest."
)

COVERAGE_NOTE = (
    "This copies character files only unless you separately opt in to account files. "
    "It does not copy all EVE settings.\n\n"
    "Character file: core_char_<character id>.dat. EVE forum and community guides treat a "
    "byte-for-byte copy of this file as the way to reuse one character's overview and "
    "per-character UI on other characters. CCP does not publish a list of every value inside it. "
    "Guides also report that the target may try to rejoin the source character's chat channels. "
    "Route Desk does not open the file or remove anything from it.\n\n"
    "Account file: core_user_<account id>.dat. This is one file per account, not per character. "
    "Characters on the same account already share it, so copying it between those characters "
    "is not a character-to-character copy. Community guides put some client display and window "
    "settings here, and they disagree about how much of the window layout is in the character "
    "file instead. CCP does not document that split. Public ESI does not reveal which account "
    "id belongs to a character, so Route Desk will not guess. Account files change only when "
    "you turn the account option on and pick those account ids yourself.\n\n"
    "Not copied: the rest of the profile folder, launcher profiles, login, cache, or credentials."
)

SERVERS = {
    "tranquility": "Tranquility (live)",
    "singularity": "Singularity (test)",
    "serenity": "Serenity",
}


class SettingsSyncError(Exception):
    pass


class ClientsStillRunning(SettingsSyncError):
    pass


class ClientCheckFailed(SettingsSyncError):
    """The process check did not prove the client was closed, and did not see it running."""


class ClientCheck(Enum):
    CLOSED = "closed"
    RUNNING = "running"
    UNKNOWN = "unknown"


# tasklist.exe is a console program. pythonw has no console, so CreateProcess
# otherwise allocates one and it flashes before the Copy warning.
CREATE_NO_WINDOW = 0x08000000
STARTF_USESHOWWINDOW = 0x00000001
SW_HIDE = 0

# The en-US no-match line is "which match", not "matching". Both are accepted.
NO_MATCH_MARKERS = (
    "no tasks are running which match the specified criteria",
    "no tasks are running matching the specified criteria",
    "no tasks are running",
    "no tasks running",
)

EVE_STILL_OPEN_MESSAGE = (
    "Close every EVE client (exefile.exe) before copying. Nothing was written."
)
UNABLE_TO_VERIFY_MESSAGE = (
    "Unable to verify whether the EVE client (exefile.exe) is closed. Nothing was written."
)


@dataclass(frozen=True)
class ProfileLocation:
    path: Path
    profile_name: str
    server: str

    @property
    def server_label(self) -> str:
        return SERVERS[self.server]


@dataclass(frozen=True)
class SettingsFile:
    kind: str
    owner_id: int
    path: Path
    size: int
    modified_utc: str

    @property
    def filename(self) -> str:
        return self.path.name


@dataclass(frozen=True)
class PlannedCopy:
    kind: str
    source_id: int
    target_id: int
    source_path: Path
    target_path: Path
    existed: bool
    size_before: int | None
    sha256_before: str | None
    modified_before: str | None


def detect_server(cache_folder_name: str) -> str:
    """Server id from the shared-cache folder name CCP documents.

    Examples from CCP and the forums include c_eve_sharedcache_tq_tranquility.
    A folder that names more than one server, or none, is refused.
    """
    name = cache_folder_name.lower()
    found: list[str] = []
    if "tranquility" in name or re.search(r"(^|_)tq($|_)", name):
        found.append("tranquility")
    if "singularity" in name or re.search(r"(^|_)sisi($|_)", name):
        found.append("singularity")
    if "serenity" in name:
        found.append("serenity")
    unique = list(dict.fromkeys(found))
    if len(unique) != 1:
        raise SettingsSyncError(
            "This folder is not inside one recognized EVE server cache "
            "(tranquility, singularity, or serenity). Route Desk will not guess the server."
        )
    return unique[0]


def identify_profile(path: Path) -> ProfileLocation:
    candidate = Path(path)
    if not candidate.is_dir():
        raise SettingsSyncError("Choose the settings profile folder (a directory named settings_<name>).")
    if not SETTINGS_DIR.match(candidate.name):
        raise SettingsSyncError(
            "Choose the profile folder itself, for example settings_Default, "
            "not the shared-cache folder above it."
        )
    profile_name = candidate.name.split("_", 1)[1]
    if not profile_name:
        raise SettingsSyncError("The profile folder name has no profile after settings_.")
    server = detect_server(candidate.parent.name)
    return ProfileLocation(path=candidate, profile_name=profile_name, server=server)


def _matching_files(profile: Path, kind: str, pattern: re.Pattern[str]) -> list[SettingsFile]:
    found: list[SettingsFile] = []
    for entry in profile.iterdir():
        match = pattern.match(entry.name)
        if match is None or not entry.is_file() or entry.is_symlink():
            continue
        owner_id = int(match.group(1))
        stat = entry.stat()
        modified = datetime.fromtimestamp(stat.st_mtime, timezone.utc).replace(microsecond=0).isoformat()
        found.append(
            SettingsFile(
                kind=kind,
                owner_id=owner_id,
                path=entry,
                size=stat.st_size,
                modified_utc=modified,
            )
        )
    return sorted(found, key=lambda item: item.owner_id)


def list_character_files(profile: Path) -> list[SettingsFile]:
    return _matching_files(profile, KIND_CHAR, CHAR_FILENAME)


def list_account_files(profile: Path) -> list[SettingsFile]:
    return _matching_files(profile, KIND_USER, USER_FILENAME)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_inside(profile: Path, path: Path) -> Path:
    root = profile.resolve()
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise SettingsSyncError(f"Refusing a path outside the profile folder: {path.name}")
    if path.is_symlink():
        raise SettingsSyncError(f"Refusing a link: {path.name}")
    return resolved


def _file_for(files: Iterable[SettingsFile], owner_id: int) -> SettingsFile | None:
    for item in files:
        if item.owner_id == int(owner_id):
            return item
    return None


def plan_copies(profile: Path, kind: str, source_id: int, target_ids: Iterable[int]) -> list[PlannedCopy]:
    """Plan byte copies of one kind of file. The source id is never a target."""
    if kind not in (KIND_CHAR, KIND_USER):
        raise SettingsSyncError("Unknown settings file type.")
    location = identify_profile(profile)
    pattern = CHAR_FILENAME if kind == KIND_CHAR else USER_FILENAME
    files = list_character_files(location.path) if kind == KIND_CHAR else list_account_files(location.path)
    source = _file_for(files, source_id)
    if source is None:
        prefix = "core_char" if kind == KIND_CHAR else "core_user"
        raise SettingsSyncError(f"Source file {prefix}_{int(source_id)}.dat is not in this profile.")
    _require_inside(location.path, source.path)
    ordered: list[int] = []
    seen: set[int] = set()
    for raw in target_ids:
        target_id = int(raw)
        if target_id == int(source_id):
            continue
        if target_id in seen:
            continue
        if target_id < 1:
            raise SettingsSyncError("A target id must be a positive integer.")
        seen.add(target_id)
        ordered.append(target_id)
    if not ordered:
        raise SettingsSyncError("Select at least one target. The source is not a target.")
    plans: list[PlannedCopy] = []
    prefix = "core_char" if kind == KIND_CHAR else "core_user"
    for target_id in ordered:
        existing = _file_for(files, target_id)
        if existing is not None:
            target_path = existing.path
            _require_inside(location.path, target_path)
            existed = True
            size_before = existing.size
            sha_before = sha256_file(target_path)
            modified_before = existing.modified_utc
        else:
            filename = f"{prefix}_{target_id}.dat"
            if pattern.match(filename) is None:
                raise SettingsSyncError(f"Refusing an unexpected filename: {filename}")
            target_path = location.path / filename
            _require_inside(location.path, target_path)
            existed = False
            size_before = None
            sha_before = None
            modified_before = None
        if target_path.resolve() == source.path.resolve():
            raise SettingsSyncError("Refusing to copy a file onto itself.")
        plans.append(
            PlannedCopy(
                kind=kind,
                source_id=int(source_id),
                target_id=target_id,
                source_path=source.path,
                target_path=target_path,
                existed=existed,
                size_before=size_before,
                sha256_before=sha_before,
                modified_before=modified_before,
            )
        )
    return plans


def describe_plan(plans: list[PlannedCopy]) -> str:
    if not plans:
        return "Nothing selected."
    lines = []
    for plan in plans:
        action = "replace" if plan.existed else "create"
        before = "absent" if plan.sha256_before is None else plan.sha256_before[:12]
        lines.append(
            f"{action} {plan.target_path.name} from {plan.source_path.name} "
            f"(existing sha256 {before}, {plan.size_before if plan.existed else 0} bytes)"
        )
    return "\n".join(lines)


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _write_bytes_atomic(target: Path, payload: bytes) -> None:
    temporary = target.with_name(f".{target.name}.{os.getpid()}.routedesk-tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def interpret_client_check(result) -> ClientCheck:
    """Bool results stay valid for tests. Only an explicit running result means the client is open."""
    if result is True or result == ClientCheck.RUNNING:
        return ClientCheck.RUNNING
    if result is False or result == ClientCheck.CLOSED:
        return ClientCheck.CLOSED
    if isinstance(result, ClientCheck):
        return result
    return ClientCheck.UNKNOWN


def _clients_closed(clients_running: Callable[[], object]) -> None:
    try:
        status = interpret_client_check(clients_running())
    except Exception as exc:
        raise ClientCheckFailed(f"{UNABLE_TO_VERIFY_MESSAGE} ({exc})") from exc
    if status == ClientCheck.RUNNING:
        raise ClientsStillRunning(f"EVE's exefile.exe is running. {EVE_STILL_OPEN_MESSAGE}")
    if status == ClientCheck.UNKNOWN:
        raise ClientCheckFailed(UNABLE_TO_VERIFY_MESSAGE)


def apply_copies(
    profile: Path,
    plans: list[PlannedCopy],
    backup_root: Path,
    *,
    clients_running: Callable[[], bool],
) -> dict:
    """Back up each existing target, then replace it with the source bytes.

    A failure rolls back targets already replaced in this run. The manifest
    records hashes and backup filenames. File bytes are not interpreted.
    """
    if not plans:
        raise SettingsSyncError("Nothing to copy.")
    location = identify_profile(profile)
    kinds = {plan.kind for plan in plans}
    _clients_closed(clients_running)
    source_hashes: dict[Path, tuple[bytes, str]] = {}
    for plan in plans:
        _require_inside(location.path, plan.source_path)
        _require_inside(location.path, plan.target_path)
        if plan.source_path not in source_hashes:
            payload = plan.source_path.read_bytes()
            source_hashes[plan.source_path] = (payload, hashlib.sha256(payload).hexdigest())
    backup_dir = Path(backup_root) / _stamp()
    backup_dir.mkdir(parents=True, exist_ok=False)
    completed: list[tuple[PlannedCopy, Path | None]] = []
    records: list[dict] = []
    try:
        for plan in plans:
            _clients_closed(clients_running)
            payload, source_hash = source_hashes[plan.source_path]
            backup_path = None
            if plan.existed:
                backup_path = backup_dir / plan.target_path.name
                original = plan.target_path.read_bytes()
                original_hash = hashlib.sha256(original).hexdigest()
                if plan.sha256_before is not None and original_hash != plan.sha256_before:
                    raise SettingsSyncError(
                        f"{plan.target_path.name} changed after the preview. Close EVE and preview again."
                    )
                backup_path.write_bytes(original)
            completed.append((plan, backup_path))
            _write_bytes_atomic(plan.target_path, payload)
            written_hash = sha256_file(plan.target_path)
            if written_hash != source_hash:
                raise SettingsSyncError(f"{plan.target_path.name} did not match the source after writing.")
            records.append(
                {
                    "kind": plan.kind,
                    "source_id": plan.source_id,
                    "target_id": plan.target_id,
                    "filename": plan.target_path.name,
                    "existed": plan.existed,
                    "backup": None if backup_path is None else backup_path.name,
                    "sha256_before": plan.sha256_before,
                    "sha256_after": written_hash,
                    "size_before": plan.size_before,
                    "size_after": len(payload),
                }
            )
    except Exception:
        _rollback(completed)
        raise
    manifest = {
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "profile_dir": str(location.path),
        "profile_name": location.profile_name,
        "server": location.server,
        "kinds": sorted(kinds),
        "files": records,
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    manifest["backup_dir"] = str(backup_dir)
    return manifest


def _rollback(completed: list[tuple[PlannedCopy, Path | None]]) -> None:
    for plan, backup in reversed(completed):
        if backup is not None and backup.is_file():
            _write_bytes_atomic(plan.target_path, backup.read_bytes())
        elif not plan.existed and plan.target_path.exists():
            plan.target_path.unlink()


def list_manifests(backup_root: Path) -> list[Path]:
    root = Path(backup_root)
    if not root.is_dir():
        return []
    found = [child / "manifest.json" for child in root.iterdir() if (child / "manifest.json").is_file()]
    return sorted(found, reverse=True)


def load_manifest(path: Path) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("files"), list):
        raise SettingsSyncError("This backup manifest is not a Route Desk settings backup.")
    return payload


def _safe_backup_name(name: str) -> str:
    if name != Path(name).name or CHAR_FILENAME.match(name) is None and USER_FILENAME.match(name) is None:
        raise SettingsSyncError(f"Backup entry is not a character or account settings file: {name}")
    return name


def restore_manifest(
    profile: Path,
    manifest_path: Path,
    backup_root: Path,
    *,
    clients_running: Callable[[], bool],
) -> dict:
    """Put back the files recorded in one backup. Current files are backed up first."""
    location = identify_profile(profile)
    manifest = load_manifest(manifest_path)
    recorded = Path(manifest.get("profile_dir", ""))
    if recorded.resolve() != location.path.resolve():
        raise SettingsSyncError("This backup belongs to a different profile folder. Choose that folder, then restore.")
    if manifest.get("server") != location.server:
        raise SettingsSyncError("This backup is for a different server than the folder you chose.")
    _clients_closed(clients_running)
    backup_dir = Path(manifest_path).parent
    plans_to_write: list[tuple[str, bytes, bool]] = []
    for entry in manifest["files"]:
        filename = _safe_backup_name(str(entry["filename"]))
        target = location.path / filename
        _require_inside(location.path, target)
        existed = bool(entry.get("existed"))
        backup_name = entry.get("backup")
        if existed:
            if not isinstance(backup_name, str):
                raise SettingsSyncError(f"Backup manifest is missing the saved copy of {filename}.")
            saved = backup_dir / _safe_backup_name(backup_name)
            if saved.parent.resolve() != backup_dir.resolve() or not saved.is_file():
                raise SettingsSyncError(f"Saved copy of {filename} is not in this backup.")
            payload = saved.read_bytes()
            expected = entry.get("sha256_before")
            if expected and hashlib.sha256(payload).hexdigest() != expected:
                raise SettingsSyncError(f"Saved copy of {filename} does not match the backup hash.")
        else:
            payload = b""
        plans_to_write.append((filename, payload, existed))
    pre_restore = Path(backup_root) / f"before-restore-{_stamp()}"
    pre_restore.mkdir(parents=True, exist_ok=False)
    done: list[tuple[str, str]] = []
    try:
        for filename, payload, existed in plans_to_write:
            target = location.path / filename
            if target.exists():
                (pre_restore / filename).write_bytes(target.read_bytes())
                done.append(("had-bytes", filename))
            else:
                done.append(("was-absent", filename))
            if existed:
                _write_bytes_atomic(target, payload)
            elif target.exists():
                target.unlink()
    except Exception:
        for action, filename in reversed(done):
            target = location.path / filename
            saved = pre_restore / filename
            if action == "had-bytes" and saved.is_file():
                _write_bytes_atomic(target, saved.read_bytes())
            elif action == "was-absent" and target.exists():
                target.unlink()
        raise
    record = {
        "restored_from": str(manifest_path),
        "profile_dir": str(location.path),
        "server": location.server,
        "pre_restore_dir": str(pre_restore),
        "files": [filename for filename, _payload, _existed in plans_to_write],
    }
    (pre_restore / "restore.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def hidden_child_settings() -> dict[str, int]:
    """Console-hide flags for a child of pythonw. CREATE_NO_WINDOW stops the flash."""
    return {
        "creationflags": CREATE_NO_WINDOW,
        "dwFlags": STARTF_USESHOWWINDOW,
        "wShowWindow": SW_HIDE,
    }


def hidden_windows_popen_kwargs() -> dict:
    settings = hidden_child_settings()
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= settings["dwFlags"]
    startupinfo.wShowWindow = settings["wShowWindow"]
    return {
        "startupinfo": startupinfo,
        "creationflags": settings["creationflags"],
    }


def decode_tasklist_bytes(data: bytes | None) -> str:
    """tasklist writes UTF-16 when its output is a pipe. Do not decode that as ANSI."""
    if not data:
        return ""
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16")
    if b"\x00" in data:
        try:
            return data.decode("utf-16-le")
        except UnicodeError:
            pass
    for encoding in ("utf-8", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _csv_image_is_exefile(text: str) -> bool:
    try:
        rows = list(csv.reader(io.StringIO(text)))
    except csv.Error:
        return False
    for row in rows:
        if len(row) < 2:
            continue
        image = row[0].strip().strip('"').lower()
        pid = row[1].strip().strip('"')
        if image == "exefile.exe" and pid.isdigit():
            return True
    return False


def classify_tasklist(returncode: int | None, output: str) -> ClientCheck:
    """Read tasklist /FO CSV output.

    A row whose image name is exefile.exe means the client is running.
    The en-US no-match sentence means it is not. Exit 0 with no exefile.exe
    row is also closed, which covers a localized no-match sentence.
    Empty output, a failed launch, or a mention of exefile.exe that is not a
    process row is unknown — not proof the client is open.
    """
    text = (output or "").replace("\x00", "")
    if _csv_image_is_exefile(text):
        return ClientCheck.RUNNING
    lowered = text.lower()
    if any(marker in lowered for marker in NO_MATCH_MARKERS):
        return ClientCheck.CLOSED
    if returncode == 0 and text.strip() and "exefile.exe" not in lowered:
        return ClientCheck.CLOSED
    return ClientCheck.UNKNOWN


def windows_eve_client_status() -> ClientCheck:
    """Ask tasklist whether exefile.exe exists. No console window is created."""
    if sys.platform != "win32":
        return ClientCheck.UNKNOWN
    try:
        completed = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq exefile.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            timeout=5,
            check=False,
            **hidden_windows_popen_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return ClientCheck.UNKNOWN
    output = decode_tasklist_bytes(completed.stdout) + decode_tasklist_bytes(completed.stderr)
    return classify_tasklist(completed.returncode, output)


def describe_character(character_id: int, library, esi_names: dict[int, str] | None = None) -> str:
    """Label a file id without inventing a name.

    An authorized Route Desk character can supply the name already stored for
    that id. A demo pilot never does. Public ESI names are used only when the
    lookup returned one. Otherwise the id stays unresolved.
    """
    esi_names = esi_names or {}
    for character in library:
        if int(character.character_id) != int(character_id) or character.demo:
            continue
        name = str(character.character_name or "").strip()
        if name:
            return name
    looked_up = esi_names.get(int(character_id))
    if looked_up:
        return looked_up
    for character in library:
        if int(character.character_id) == int(character_id) and character.demo:
            return "name not loaded — this id is also a Route Desk demo pilot, not a verified EVE name"
    return "name not loaded"


def _character_id_from_url(url: str) -> int | None:
    match = re.search(r"/characters/([1-9][0-9]*)/?$", url.split("?", 1)[0])
    if match is None:
        return None
    return int(match.group(1))


def lookup_public_name(character_id: int, http: HttpClient) -> str | None:
    """Public character endpoint. No token and no extra SSO scope."""
    character_id = int(character_id)
    url = f"https://esi.evetech.net/latest/characters/{character_id}/"
    try:
        result = http.request("GET", url, timeout=15)
    except HttpError:
        return None
    if result.status != 200:
        return None
    if _character_id_from_url(result.url) != character_id:
        return None
    try:
        payload = result.json()
    except (UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    name = payload.get("name")
    if not isinstance(name, str):
        return None
    cleaned = name.strip()
    if not cleaned:
        return None
    return cleaned
