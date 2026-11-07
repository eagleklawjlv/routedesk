"""EVE SSO authorization code + PKCE for a native public client.

No client secret is used. See https://developers.eveonline.com/docs/services/sso/
"""

from __future__ import annotations

import base64
import hashlib
import json
import queue
import secrets
import threading
import time
import urllib.parse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jwt
from jwt.algorithms import RSAAlgorithm

from everoute.http import HttpClient, HttpError, HttpResult, UrlNotAllowed
from everoute.messages import CALLBACK_HTML, SCOPE

SSO_HOST = "login.eveonline.com"
METADATA_URL = "https://login.eveonline.com/.well-known/oauth-authorization-server"
DEFAULT_AUTHORIZE = "https://login.eveonline.com/v2/oauth/authorize"
DEFAULT_TOKEN = "https://login.eveonline.com/v2/oauth/token"
DEFAULT_JWKS = "https://login.eveonline.com/oauth/jwks"
DEFAULT_REVOKE = "https://login.eveonline.com/v2/oauth/revoke"
ACCEPTED_ISSUERS = {
    "login.eveonline.com",
    "https://login.eveonline.com",
    "https://login.eveonline.com/",
}
EXPECTED_AUDIENCE = "EVE Online"


class AuthError(Exception):
    pass


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def generate_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    return verifier, pkce_challenge(verifier)


def character_id_from_sub(sub: str) -> int:
    prefix = "CHARACTER:EVE:"
    if not isinstance(sub, str) or not sub.startswith(prefix) or not sub[len(prefix) :].isdigit():
        raise AuthError("The access token subject was not an EVE character.")
    return int(sub[len(prefix) :])


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        return value.split()
    if isinstance(value, list):
        return value
    return [value]


def validate_access_token(token: str, jwks: dict, client_id: str) -> dict:
    if not client_id:
        raise AuthError("A client ID is required before a token can be accepted.")
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise AuthError("The access token was not a readable JWT.") from exc
    if header.get("alg") != "RS256":
        raise AuthError("The access token was not signed with RS256.")
    kid = header.get("kid")
    keys = jwks.get("keys") or []
    matches = [item for item in keys if item.get("kid") == kid]
    if not matches:
        raise AuthError("The access token signing key was not in the SSO key set.")
    try:
        public_key = RSAAlgorithm.from_jwk(json.dumps(matches[0]))
        claims = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            leeway=10,
            options={"verify_aud": False, "require": ["exp", "iss", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("The access token has expired.") from exc
    except jwt.PyJWTError as exc:
        raise AuthError("The access token signature was rejected.") from exc
    issuer = claims.get("iss")
    if issuer not in ACCEPTED_ISSUERS:
        raise AuthError("The access token issuer was not EVE SSO.")
    audience = _as_list(claims.get("aud"))
    if client_id not in audience or EXPECTED_AUDIENCE not in audience:
        raise AuthError("The access token audience did not match this application.")
    scopes = _as_list(claims.get("scp"))
    if SCOPE not in scopes:
        raise AuthError(
            f"The token does not include {SCOPE}. Authorize again and grant that scope. "
            "No other scope is required."
        )
    if "name" not in claims:
        raise AuthError("The access token did not include a character name.")
    character_id_from_sub(str(claims["sub"]))
    return claims


@dataclass
class Endpoints:
    authorization: str = DEFAULT_AUTHORIZE
    token: str = DEFAULT_TOKEN
    jwks: str = DEFAULT_JWKS
    revocation: str = DEFAULT_REVOKE


def endpoints_from_metadata(metadata: dict) -> Endpoints:
    def pick(name: str, default: str) -> str:
        value = str(metadata.get(name) or default)
        parsed = urllib.parse.urlparse(value)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() != SSO_HOST:
            raise AuthError("SSO metadata pointed at an unexpected host.")
        return value

    return Endpoints(
        authorization=pick("authorization_endpoint", DEFAULT_AUTHORIZE),
        token=pick("token_endpoint", DEFAULT_TOKEN),
        jwks=pick("jwks_uri", DEFAULT_JWKS),
        revocation=pick("revocation_endpoint", DEFAULT_REVOKE),
    )


@dataclass
class PendingLogin:
    state: str
    code_verifier: str
    expires_at: float
    redirect_uri: str


@dataclass
class CallbackResult:
    code: str | None = None
    state: str | None = None
    error: str | None = None
    error_description: str | None = None


class LoopbackServer:
    """HTTP server bound to loopback. Query strings are not written to the log."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        if host not in ("127.0.0.1", "localhost"):
            raise AuthError("The callback server only binds to loopback.")
        self._queue: queue.Queue[CallbackResult] = queue.Queue()
        handler = self._make_handler()
        try:
            self._httpd = ThreadingHTTPServer(("127.0.0.1", int(port)), handler)
        except OSError as exc:
            raise AuthError(
                f"Could not listen on 127.0.0.1:{port}. "
                "Close whatever is using that port, or choose the port you registered with CCP."
            ) from exc
        self.port = int(self._httpd.server_address[1])
        self._thread = threading.Thread(target=self._httpd.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
        self._thread.start()

    def _make_handler(self):
        pending = self._queue

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args) -> None:
                return

            def do_GET(self) -> None:  # noqa: N802
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != "/callback":
                    body = b"Not found"
                    self.send_response(404)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                query = urllib.parse.parse_qs(parsed.query)

                def one(name: str) -> str | None:
                    values = query.get(name) or [None]
                    return values[0]

                pending.put(
                    CallbackResult(
                        code=one("code"),
                        state=one("state"),
                        error=one("error"),
                        error_description=one("error_description"),
                    )
                )
                body = CALLBACK_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:  # noqa: N802
                self.send_response(405)
                self.end_headers()

        return Handler

    def wait_for_state(self, expected_state: str, timeout: float, cancel=None) -> CallbackResult:
        deadline = time.monotonic() + timeout
        while True:
            if cancel is not None and cancel.is_set():
                raise AuthError("Login cancelled. No token was stored.")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AuthError("Timed out waiting for the EVE login redirect. No token was requested.")
            try:
                result = self._queue.get(timeout=min(0.2, remaining))
            except queue.Empty:
                continue
            if not result.state or not secrets.compare_digest(result.state, expected_state):
                continue
            if result.error:
                detail = result.error_description or result.error
                raise AuthError(f"EVE SSO did not authorize the character ({detail}).")
            if not result.code:
                raise AuthError("The callback did not include an authorization code.")
            return result

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=2)


class OAuthClient:
    def __init__(self, http: HttpClient, client_id: str, redirect_uri: str, clock=None):
        self.http = http
        self.client_id = client_id
        self.redirect_uri = redirect_uri
        self.clock = clock or time.time
        self.pending: PendingLogin | None = None
        self.endpoints = Endpoints()
        self._jwks: dict | None = None

    def load_metadata(self) -> Endpoints:
        try:
            metadata = self.http.get_json(METADATA_URL, timeout=20)
            self.endpoints = endpoints_from_metadata(metadata)
        except (HttpError, UrlNotAllowed, AuthError, OSError, ValueError):
            self.endpoints = Endpoints()
        return self.endpoints

    def jwks(self) -> dict:
        if self._jwks is None:
            self._jwks = self.http.get_json(self.endpoints.jwks, timeout=20)
        return self._jwks

    def set_jwks(self, jwks: dict) -> None:
        self._jwks = jwks

    def authorization_url(self, scopes: list[str] | None = None) -> str:
        if scopes is not None and not isinstance(scopes, (list, tuple)):
            raise AuthError("Login scopes were not a list of scope names. No token was requested.")
        requested = list(scopes or [SCOPE])
        if SCOPE not in requested:
            requested.insert(0, SCOPE)
        verifier, challenge = generate_pkce()
        state = secrets.token_urlsafe(32)
        self.pending = PendingLogin(
            state=state,
            code_verifier=verifier,
            expires_at=self.clock() + 300,
            redirect_uri=self.redirect_uri,
        )
        query = urllib.parse.urlencode(
            {
                "response_type": "code",
                "redirect_uri": self.redirect_uri,
                "client_id": self.client_id,
                "scope": " ".join(requested),
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
            }
        )
        return f"{self.endpoints.authorization}?{query}"

    def _consume_pending(self, state: str) -> PendingLogin:
        pending = self.pending
        if pending is None:
            raise AuthError("No EVE login is in progress.")
        if self.clock() > pending.expires_at:
            self.pending = None
            raise AuthError("The login attempt expired. Start it again.")
        if not state or not secrets.compare_digest(pending.state, state):
            raise AuthError("The login state did not match. The authorization code was not sent.")
        self.pending = None
        return pending

    def exchange(self, code: str, state: str) -> dict:
        pending = self._consume_pending(state)
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self.client_id,
            "code_verifier": pending.code_verifier,
            "redirect_uri": pending.redirect_uri,
        }
        result = self.http.post_form(self.endpoints.token, form)
        return _parse_token_result(result, require_refresh=True)

    def refresh(self, refresh_token: str) -> dict:
        form = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.client_id,
        }
        result = self.http.post_form(self.endpoints.token, form)
        payload = _parse_token_result(result, require_refresh=False)
        if not payload.get("refresh_token"):
            payload["refresh_token"] = refresh_token
        return payload

    def revoke(self, refresh_token: str) -> None:
        form = {
            "token": refresh_token,
            "token_type_hint": "refresh_token",
            "client_id": self.client_id,
        }
        result = self.http.post_form(self.endpoints.revocation, form)
        if result.status not in (200, 204):
            raise AuthError(f"Revoke failed with status {result.status}.")

    def finish_callback(self, callback: CallbackResult) -> tuple[dict, dict]:
        if callback.error:
            detail = callback.error_description or callback.error
            raise AuthError(f"EVE SSO did not authorize the character ({detail}).")
        if not callback.code or not callback.state:
            raise AuthError("The callback was missing a code or state.")
        tokens = self.exchange(callback.code, callback.state)
        claims = validate_access_token(tokens["access_token"], self.jwks(), self.client_id)
        return tokens, claims


def _parse_token_result(result: HttpResult, *, require_refresh: bool) -> dict:
    if result.status != 200:
        detail = ""
        try:
            payload = result.json() or {}
            detail = str(payload.get("error_description") or payload.get("error") or "")
        except Exception:
            detail = result.body[:180].decode("utf-8", "replace")
        raise AuthError(f"Token endpoint returned {result.status}. {detail}".strip())
    payload = result.json() or {}
    if str(payload.get("token_type") or "Bearer").lower() != "bearer":
        raise AuthError("The token response was not a bearer token.")
    if not payload.get("access_token"):
        raise AuthError("The token response did not include an access token.")
    if require_refresh and not payload.get("refresh_token"):
        raise AuthError("The token response did not include a refresh token.")
    return payload


class TokenManager:
    """In-memory access tokens. Refresh tokens stay in the credential store."""

    def __init__(self, credentials, oauth: OAuthClient, clock=None):
        self.credentials = credentials
        self.oauth = oauth
        self.clock = clock or time.time
        self._access: dict[int, tuple[str, float]] = {}

    def access_token(self, character_id: int) -> str:
        cached = self._access.get(int(character_id))
        if cached and cached[1] - 30 > self.clock():
            return cached[0]
        return self.force_refresh(character_id)

    def force_refresh(self, character_id: int) -> str:
        refresh = self.credentials.get_refresh_token(character_id)
        if not refresh:
            raise AuthError("No refresh token is stored for this character. Authorize them again.")
        payload = self.oauth.refresh(refresh)
        new_refresh = payload.get("refresh_token") or refresh
        self.credentials.set_refresh_token(character_id, new_refresh)
        claims = validate_access_token(payload["access_token"], self.oauth.jwks(), self.oauth.client_id)
        cid = character_id_from_sub(str(claims["sub"]))
        if cid != int(character_id):
            raise AuthError("Refresh returned a different character than the one stored.")
        expires = self.clock() + float(payload.get("expires_in") or 1200)
        token = str(payload["access_token"])
        self._access[int(character_id)] = (token, expires)
        return token

    def forget(self, character_id: int) -> None:
        self._access.pop(int(character_id), None)


def run_browser_login(
    client: OAuthClient,
    port: int,
    open_browser,
    timeout: float = 300,
    scopes: list[str] | None = None,
    cancel=None,
) -> tuple[dict, dict]:
    if not client.client_id:
        raise AuthError("Save a client ID in SSO setup before authorizing a character.")
    client.load_metadata()
    server = LoopbackServer("127.0.0.1", port)
    try:
        if cancel is not None and cancel.is_set():
            raise AuthError("Login cancelled. No token was stored.")
        url = client.authorization_url(scopes)
        open_browser(url)
        if client.pending is None:
            raise AuthError("Login state was lost before the browser opened.")
        callback = server.wait_for_state(client.pending.state, timeout, cancel=cancel)
    finally:
        server.close()
    return client.finish_callback(callback)
