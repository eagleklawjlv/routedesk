"""Refresh-token storage. Access tokens are not stored here."""

from __future__ import annotations

import os
import stat
from pathlib import Path

SERVICE = "everoute-desk"


class CredentialError(Exception):
    pass


class MemoryCredentialStore:
    def __init__(self):
        self._tokens: dict[int, str] = {}

    def set_refresh_token(self, character_id: int, token: str) -> None:
        self._tokens[int(character_id)] = token

    def get_refresh_token(self, character_id: int) -> str | None:
        return self._tokens.get(int(character_id))

    def delete_refresh_token(self, character_id: int) -> None:
        self._tokens.pop(int(character_id), None)


class FileCredentialStore:
    """Opt-in fallback when the OS keystore is unavailable. Files are mode 0600."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.directory, 0o700)
        except OSError:
            pass

    def _path(self, character_id: int) -> Path:
        return self.directory / f"{int(character_id)}.token"

    def set_refresh_token(self, character_id: int, token: str) -> None:
        path = self._path(character_id)
        path.write_text(token, encoding="utf-8")
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)

    def get_refresh_token(self, character_id: int) -> str | None:
        path = self._path(character_id)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def delete_refresh_token(self, character_id: int) -> None:
        path = self._path(character_id)
        if path.exists():
            path.unlink()


class KeyringCredentialStore:
    def __init__(self):
        import keyring

        self._keyring = keyring

    def _name(self, character_id: int) -> str:
        return f"refresh:{int(character_id)}"

    def set_refresh_token(self, character_id: int, token: str) -> None:
        self._keyring.set_password(SERVICE, self._name(character_id), token)

    def get_refresh_token(self, character_id: int) -> str | None:
        return self._keyring.get_password(SERVICE, self._name(character_id))

    def delete_refresh_token(self, character_id: int) -> None:
        try:
            self._keyring.delete_password(SERVICE, self._name(character_id))
        except Exception:
            pass


def keyring_works() -> tuple[bool, str]:
    try:
        import keyring

        keyring.set_password(SERVICE, "probe", "ok")
        got = keyring.get_password(SERVICE, "probe")
        try:
            keyring.delete_password(SERVICE, "probe")
        except Exception:
            pass
        if got != "ok":
            return False, "The OS credential store did not return the probe value."
        return True, ""
    except Exception as exc:
        return False, str(exc)


def open_credential_store(token_dir: Path, allow_file: bool):
    ok, detail = keyring_works()
    if ok:
        return KeyringCredentialStore(), "os"
    if allow_file:
        return FileCredentialStore(token_dir), "file"
    raise CredentialError(
        "The operating system credential store is unavailable "
        f"({detail}). Route Desk will not keep refresh tokens until you turn on "
        "the private-file option in SSO setup, or until a keystore is available "
        "(Windows Credential Manager, macOS Keychain, or a Secret Service daemon)."
    )
