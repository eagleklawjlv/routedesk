"""HTTPS client pinned to CCP hosts used by SSO, ESI, and the static data export."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

ALLOWED_HOSTS = frozenset(
    {
        "login.eveonline.com",
        "esi.evetech.net",
        "developers.eveonline.com",
    }
)

USER_AGENT = "RouteDesk/0.1 (personal desktop waypoint tool; no telemetry)"


class HttpError(Exception):
    pass


class NetworkError(HttpError):
    pass


class RequestTimeout(HttpError):
    pass


class UrlNotAllowed(HttpError):
    pass


class Redirect(Exception):
    def __init__(self, status: int, url: str):
        super().__init__(f"{status} {url}")
        self.status = status
        self.url = url


@dataclass
class HttpResult:
    status: int
    headers: dict[str, str]
    body: bytes
    url: str

    def json(self):
        if not self.body:
            return None
        return json.loads(self.body.decode("utf-8"))

    def header(self, name: str) -> str | None:
        target = name.lower()
        for key, value in self.headers.items():
            if key.lower() == target:
                return value
        return None


def assert_allowed(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise UrlNotAllowed("Refusing a non-HTTPS URL")
    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise UrlNotAllowed(f"Refusing unexpected host {host or '(missing)'}")


class _RejectRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Redirect(code, newurl)


class UrllibTransport:
    def __init__(self, timeout: float = 45.0):
        self.timeout = timeout
        self._opener = urllib.request.build_opener(_RejectRedirect)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        timeout: float | None = None,
    ) -> HttpResult:
        hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if headers:
            hdrs.update(headers)
        request = urllib.request.Request(url, data=body, headers=hdrs, method=method)
        try:
            with self._opener.open(request, timeout=timeout or self.timeout) as response:
                raw_headers = {key: value for key, value in response.headers.items()}
                payload = response.read()
                return HttpResult(response.status, raw_headers, payload, response.geturl())
        except Redirect:
            raise
        except urllib.error.HTTPError as exc:
            payload = exc.read() if exc.fp is not None else b""
            raw_headers = {key: value for key, value in exc.headers.items()} if exc.headers else {}
            return HttpResult(exc.code, raw_headers, payload, url)
        except (socket.timeout, TimeoutError) as exc:
            raise RequestTimeout(str(exc)) from exc
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise RequestTimeout(str(reason)) from exc
            raise NetworkError(str(reason)) from exc


class HttpClient:
    def __init__(self, transport=None, max_redirects: int = 3):
        self.transport = transport or UrllibTransport()
        self.max_redirects = max_redirects

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        timeout: float | None = None,
    ) -> HttpResult:
        current = url
        redirects = 0
        while True:
            assert_allowed(current)
            try:
                return self.transport.request(
                    method, current, headers=headers, body=body, timeout=timeout
                )
            except Redirect as exc:
                redirects += 1
                if redirects > self.max_redirects:
                    raise HttpError("Too many redirects") from exc
                if method.upper() != "GET":
                    raise HttpError("Refusing to follow a redirect on a token or write request")
                assert_allowed(exc.url)
                current = exc.url
                body = None

    def get_json(self, url: str, *, timeout: float | None = None):
        result = self.request("GET", url, timeout=timeout)
        if result.status != 200:
            raise HttpError(f"GET failed with {result.status}: {result.body[:240]!r}")
        return result.json()

    def post_json(self, url: str, payload, *, timeout: float | None = None):
        body = json.dumps(payload).encode("utf-8")
        result = self.request(
            "POST",
            url,
            headers={"Content-Type": "application/json"},
            body=body,
            timeout=timeout,
        )
        if result.status not in (200, 201):
            raise HttpError(f"POST failed with {result.status}: {result.body[:240]!r}")
        return result.json()

    def post_form(self, url: str, form: dict[str, str], *, timeout: float | None = None) -> HttpResult:
        if any(str(key).lower() == "client_secret" for key in form):
            raise HttpError("Refusing to send a client secret")
        encoded = urllib.parse.urlencode(form).encode("utf-8")
        if b"client_secret" in encoded:
            raise HttpError("Refusing to send a client secret")
        return self.request(
            "POST",
            url,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body=encoded,
            timeout=timeout,
        )

    def get_bytes(self, url: str, *, timeout: float | None = None) -> bytes:
        result = self.request(
            "GET",
            url,
            headers={"Accept": "application/octet-stream,*/*"},
            timeout=timeout,
        )
        if result.status != 200:
            raise HttpError(f"Download failed with {result.status}")
        return result.body
