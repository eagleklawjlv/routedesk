"""Shared SSO setup and character save used by the CLI and the window."""

from __future__ import annotations

import re

from everoute.models import Character, Library, utc_now
from everoute.oauth import AuthError, character_id_from_sub
from everoute.store import Settings, SettingsStore

CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,199}$")
SECRET_FLAGS = frozenset(
    {
        "--client-secret",
        "--password",
        "--secret",
        "--token",
        "--access-token",
        "--refresh-token",
    }
)


def validate_client_id(value: str) -> str:
    """Accept a public EVE application id. Reject blanks, secrets, and token-shaped values."""
    client_id = value.strip()
    lowered = client_id.lower()
    if (
        not CLIENT_ID_RE.fullmatch(client_id)
        or "secret" in lowered
        or lowered.startswith("bearer ")
        or client_id.count(".") >= 2
    ):
        raise ValueError(
            "That is not a public client ID. Paste the client ID from the EVE developers site. "
            "Do not paste a client secret, password, or token."
        )
    return client_id


def normalize_callback(host: str | None, port: int | None, current: Settings) -> tuple[str, int]:
    chosen_host = (host if host is not None else current.callback_host).strip()
    if chosen_host not in ("127.0.0.1", "localhost"):
        raise ValueError("Callback host must be 127.0.0.1 or localhost.")
    chosen_port = current.callback_port if port is None else int(port)
    if chosen_port < 1024 or chosen_port > 65535:
        raise ValueError("Callback port must be between 1024 and 65535.")
    return chosen_host, chosen_port


def configure_sso(
    settings_store: SettingsStore,
    *,
    client_id: str,
    host: str | None = None,
    port: int | None = None,
    allow_file_token_store: bool | None = None,
) -> Settings:
    """Save the public client ID, loopback callback, and turn demo mode off.

    Does not open a browser, exchange a token, or call the waypoint endpoint.
    Routes and characters already on disk are not loaded or rewritten.
    """
    settings = settings_store.load()
    settings.client_id = validate_client_id(client_id)
    settings.callback_host, settings.callback_port = normalize_callback(host, port, settings)
    settings.demo_mode = False
    if allow_file_token_store is not None:
        settings.allow_file_token_store = bool(allow_file_token_store)
    settings_store.save(settings)
    return settings


def scopes_from_claims(claims: dict) -> list[str]:
    scopes = claims.get("scp")
    if isinstance(scopes, str):
        return [part for part in scopes.split() if part]
    return [str(item) for item in (scopes or [])]


def remember_authorized_character(library: Library, credentials, claims: dict, tokens: dict) -> Character:
    """Store the refresh token and add the character to the library. No waypoint call."""
    character_id = character_id_from_sub(str(claims["sub"]))
    name = str(claims.get("name") or "").strip()
    if not name:
        raise AuthError("The access token did not include a character name.")
    refresh = tokens.get("refresh_token")
    if not refresh:
        raise AuthError("EVE SSO did not return a refresh token.")
    credentials.set_refresh_token(character_id, refresh)
    scopes = scopes_from_claims(claims)
    existing = library.character(character_id)
    if existing is None:
        character = Character(
            character_id=character_id,
            character_name=name,
            demo=False,
            scopes=scopes,
            authorized_at=utc_now(),
        )
        library.characters.append(character)
        return character
    existing.character_name = name
    existing.demo = False
    existing.scopes = scopes
    existing.authorized_at = utc_now()
    return existing
