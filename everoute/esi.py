"""ESI autopilot waypoint calls.

POST /ui/autopilot/waypoint/ sets one destination per request.
The published contract has no maximum count, no read-back, and no batch body.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from enum import Enum

from everoute.http import HttpClient, HttpResult


class OutcomeKind(str, Enum):
    APPLIED = "applied"
    FAILED = "failed"
    UNCERTAIN = "uncertain"
    RATE_LIMITED = "rate_limited"
    NEEDS_REFRESH = "needs_refresh"
    NETWORK = "network"


@dataclass
class Outcome:
    kind: OutcomeKind
    message: str = ""
    status: int | None = None
    retry_after: float = 0.0
    error_limit_remain: int | None = None


def _header_int(result: HttpResult, name: str) -> int | None:
    raw = result.header(name)
    if raw is None or raw == "":
        return None
    try:
        return int(float(raw))
    except ValueError:
        return None


def _error_text(result: HttpResult) -> str:
    if not result.body:
        return ""
    try:
        payload = result.json()
    except Exception:
        return result.body[:240].decode("utf-8", "replace")
    if isinstance(payload, dict):
        return str(payload.get("error") or payload.get("error_description") or "")
    return ""


def interpret_waypoint_response(result: HttpResult) -> Outcome:
    status = result.status
    detail = _error_text(result)
    remain = _header_int(result, "X-ESI-Error-Limit-Remain")
    reset = _header_int(result, "X-ESI-Error-Limit-Reset")
    retry_after = _header_int(result, "Retry-After")
    if 200 <= status < 300:
        return Outcome(
            OutcomeKind.APPLIED,
            f"ESI returned {status}. Accepted. This does not prove the game client drew the waypoint.",
            status,
            error_limit_remain=remain,
        )
    if status == 401:
        return Outcome(
            OutcomeKind.NEEDS_REFRESH,
            detail or "Unauthorized.",
            status,
            error_limit_remain=remain,
        )
    if status == 420:
        wait = float(retry_after if retry_after is not None else (reset if reset is not None else 5))
        return Outcome(
            OutcomeKind.RATE_LIMITED,
            detail or "ESI error-limited this request. It was not applied.",
            status,
            retry_after=wait,
            error_limit_remain=remain,
        )
    if status in (500, 502, 503, 504):
        return Outcome(
            OutcomeKind.UNCERTAIN,
            (
                f"ESI returned {status}. It is not clear whether the waypoint was applied. "
                "It will not be retried automatically. "
                f"{detail}"
            ).strip(),
            status,
            error_limit_remain=remain,
        )
    if status in (400, 403, 404):
        hint = ""
        if status == 400:
            hint = " The character usually needs to be logged into the EVE client."
        if status == 403:
            hint = " The token may be missing esi-ui.write_waypoint.v1. Authorize this character again."
        if status == 404:
            hint = " ESI did not recognize that destination id."
        return Outcome(
            OutcomeKind.FAILED,
            f"ESI returned {status}. {detail}{hint}".strip(),
            status,
            error_limit_remain=remain,
        )
    return Outcome(
        OutcomeKind.UNCERTAIN,
        f"Unexpected ESI status {status}. {detail} It will not be retried automatically.".strip(),
        status,
        error_limit_remain=remain,
    )


class EsiClient:
    def __init__(self, http: HttpClient):
        self.http = http

    def set_waypoint(
        self,
        access_token: str,
        destination_id: int,
        *,
        add_to_beginning: bool,
        clear_other_waypoints: bool,
    ) -> Outcome:
        query = urllib.parse.urlencode(
            {
                "destination_id": str(int(destination_id)),
                "add_to_beginning": "true" if add_to_beginning else "false",
                "clear_other_waypoints": "true" if clear_other_waypoints else "false",
                "datasource": "tranquility",
            }
        )
        url = f"https://esi.evetech.net/latest/ui/autopilot/waypoint/?{query}"
        result = self.http.request(
            "POST",
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            body=b"",
            timeout=45,
        )
        return interpret_waypoint_response(result)
