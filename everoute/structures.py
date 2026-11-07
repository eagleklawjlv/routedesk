"""Per-character Upwell structure search.

CCP does not offer one call that lists every structure a character can dock in.
Search uses GET /characters/{id}/search/?categories=structure (scope
esi-search.search_structures.v1). Names and solar systems use
GET /universe/structures/{id}/ (scope esi-universe.read_structures.v1).

A successful read is not stored as docking permission. The response has no
permission field, and the ACL can change after the call. Results are cached
per character and are not copied onto characters who did not resolve them.
"""

from __future__ import annotations

import json
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

from everoute.esi import Outcome, OutcomeKind, _error_text
from everoute.http import HttpResult
from everoute.messages import READ_STRUCTURES_SCOPE, SEARCH_STRUCTURES_SCOPE

STRUCTURE_SCOPES = (SEARCH_STRUCTURES_SCOPE, READ_STRUCTURES_SCOPE)
OK_TTL_SECONDS = 6 * 60 * 60
ERROR_TTL_SECONDS = 15 * 60


def missing_structure_scopes(scopes: list[str] | None) -> list[str]:
    """Empty or missing scope lists mean the token was not recorded, so try ESI."""
    if not scopes:
        return []
    return [scope for scope in STRUCTURE_SCOPES if scope not in scopes]


def visibility_label(names: list[str]) -> str:
    shown = ", ".join(names) if names else "the selected character"
    return f"Visible via {shown} — docking unverified"


@dataclass
class StructureHit:
    structure_id: int
    name: str
    system_id: int
    seen_by: list[tuple[int, str]] = field(default_factory=list)
    visibility: str = ""

    def label(self) -> str:
        if self.visibility:
            return self.visibility
        return visibility_label([name for _character_id, name in self.seen_by])


@dataclass
class CharacterProblem:
    character_id: int
    character_name: str
    kind: str
    message: str


@dataclass
class Discovery:
    hits: list[StructureHit] = field(default_factory=list)
    problems: list[CharacterProblem] = field(default_factory=list)
    query_error: str = ""

    def in_system(self, system_id: int | None) -> list[StructureHit]:
        if system_id is None:
            return list(self.hits)
        return [hit for hit in self.hits if hit.system_id == int(system_id)]


class StructureCache:
    def __init__(self, directory: Path, clock=None, ok_ttl: float = OK_TTL_SECONDS, error_ttl: float = ERROR_TTL_SECONDS):
        self.directory = Path(directory)
        self.clock = clock or time.time
        self.ok_ttl = ok_ttl
        self.error_ttl = error_ttl
        self._memory: dict[int, dict[int, dict]] = {}

    def _path(self, character_id: int) -> Path:
        return self.directory / f"{int(character_id)}.json"

    def _load(self, character_id: int) -> dict[int, dict]:
        character_id = int(character_id)
        if character_id in self._memory:
            return self._memory[character_id]
        path = self._path(character_id)
        records: dict[int, dict] = {}
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            for key, record in (payload.get("entries") or {}).items():
                records[int(key)] = record
        self._memory[character_id] = records
        return records

    def _save(self, character_id: int) -> None:
        records = self._memory.get(int(character_id), {})
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "character_id": int(character_id),
            "entries": {str(key): value for key, value in records.items()},
        }
        self._path(character_id).write_text(json.dumps(payload), encoding="utf-8")

    def get(self, character_id: int, structure_id: int) -> dict | None:
        record = self._load(character_id).get(int(structure_id))
        if not record:
            return None
        age = self.clock() - float(record.get("fetched_at") or 0)
        ttl = self.ok_ttl if record.get("ok") else self.error_ttl
        if age > ttl:
            return None
        return record

    def put(self, character_id: int, structure_id: int, record: dict) -> None:
        stored = dict(record)
        stored["fetched_at"] = self.clock()
        self._load(character_id)[int(structure_id)] = stored
        self._save(character_id)


def classify_structure_result(result: HttpResult) -> Outcome:
    from everoute.esi import interpret_waypoint_response

    outcome = interpret_waypoint_response(result)
    detail = _error_text(result)
    if result.status == 403 and "scope" in detail.lower():
        return Outcome(
            OutcomeKind.FAILED,
            "This character's token does not include the structure scopes. "
            "Add esi-search.search_structures.v1 and esi-universe.read_structures.v1 "
            "on the existing EVE application, then authorize that character again. "
            "Waypoint-only login still works for autopilot.",
            403,
        )
    if result.status == 403:
        return Outcome(
            OutcomeKind.FAILED,
            "Not visible to this character (403). It is not listed for them. " + detail,
            403,
        )
    if result.status == 404:
        return Outcome(
            OutcomeKind.FAILED,
            "ESI returned 404 for this id with this character. It is not listed for them.",
            404,
        )
    if outcome.kind == OutcomeKind.APPLIED and result.status == 200:
        return Outcome(OutcomeKind.APPLIED, "ok", 200, error_limit_remain=outcome.error_limit_remain)
    return outcome


class StructureDirectory:
    def __init__(self, http, cache: StructureCache, sleeper=None):
        self.http = http
        self.cache = cache
        self.sleeper = sleeper or (lambda _seconds: None)

    def discover(self, characters, query: str, token_for) -> Discovery:
        text = query.strip()
        if len(text) < 3:
            return Discovery(query_error="Structure search needs at least 3 characters. That is an ESI rule.")
        found: dict[int, StructureHit] = {}
        problems: list[CharacterProblem] = []
        for character in characters:
            if getattr(character, "demo", False):
                problems.append(
                    CharacterProblem(character.character_id, character.character_name, "demo", "Demo characters have no ESI token.")
                )
                continue
            missing = missing_structure_scopes(list(getattr(character, "scopes", []) or []))
            if missing:
                problems.append(
                    CharacterProblem(
                        character.character_id,
                        character.character_name,
                        "missing_scope",
                        "Missing "
                        + ", ".join(missing)
                        + ". Add those scopes on the EVE application and authorize this character again. "
                        "Existing waypoint access is unchanged.",
                    )
                )
                continue
            try:
                token = token_for(character.character_id)
            except Exception as exc:
                problems.append(CharacterProblem(character.character_id, character.character_name, "token", str(exc)))
                continue
            outcome, structure_ids = self._search(character.character_id, text, token)
            if outcome.kind != OutcomeKind.APPLIED:
                problems.append(CharacterProblem(character.character_id, character.character_name, outcome.kind.value, outcome.message))
                continue
            for structure_id in structure_ids:
                resolved = self._resolve(character.character_id, structure_id, token)
                if resolved is None:
                    continue
                if not resolved.get("ok"):
                    problems.append(
                        CharacterProblem(
                            character.character_id,
                            character.character_name,
                            str(resolved.get("kind") or "failed"),
                            str(resolved.get("message") or "Not visible to this character."),
                        )
                    )
                    continue
                hit = found.get(structure_id)
                if hit is None:
                    hit = StructureHit(
                        structure_id=structure_id,
                        name=str(resolved["name"]),
                        system_id=int(resolved["system_id"]),
                    )
                    found[structure_id] = hit
                if all(character.character_id != seen_id for seen_id, _name in hit.seen_by):
                    hit.seen_by.append((character.character_id, character.character_name))
                hit.visibility = visibility_label([name for _seen_id, name in hit.seen_by])
        hits = sorted(found.values(), key=lambda hit: hit.name.casefold())
        return Discovery(hits=hits, problems=problems)

    def resolve_id(self, structure_id: int, characters, token_for) -> Discovery:
        found: dict[int, StructureHit] = {}
        problems: list[CharacterProblem] = []
        for character in characters:
            if getattr(character, "demo", False):
                continue
            missing = missing_structure_scopes(list(getattr(character, "scopes", []) or []))
            if missing:
                problems.append(
                    CharacterProblem(
                        character.character_id,
                        character.character_name,
                        "missing_scope",
                        "Missing " + ", ".join(missing) + ". Waypoint-only access still works.",
                    )
                )
                continue
            try:
                token = token_for(character.character_id)
            except Exception as exc:
                problems.append(CharacterProblem(character.character_id, character.character_name, "token", str(exc)))
                continue
            resolved = self._resolve(character.character_id, int(structure_id), token)
            if not resolved or not resolved.get("ok"):
                problems.append(
                    CharacterProblem(
                        character.character_id,
                        character.character_name,
                        str((resolved or {}).get("kind") or "failed"),
                        str((resolved or {}).get("message") or "Not visible to this character."),
                    )
                )
                continue
            hit = found.get(int(structure_id))
            if hit is None:
                hit = StructureHit(
                    structure_id=int(structure_id),
                    name=str(resolved["name"]),
                    system_id=int(resolved["system_id"]),
                )
                found[int(structure_id)] = hit
            hit.seen_by.append((character.character_id, character.character_name))
            hit.visibility = visibility_label([name for _seen_id, name in hit.seen_by])
        return Discovery(hits=list(found.values()), problems=problems)

    def _search(self, character_id: int, query: str, token: str) -> tuple[Outcome, list[int]]:
        quoted = urllib.parse.urlencode(
            {"categories": "structure", "search": query, "datasource": "tranquility"}
        )
        url = f"https://esi.evetech.net/latest/characters/{int(character_id)}/search/?{quoted}"
        result = self._call("GET", url, token)
        outcome = classify_structure_result(result)
        if outcome.kind == OutcomeKind.RATE_LIMITED:
            self.sleeper(max(outcome.retry_after, 0))
            result = self._call("GET", url, token)
            outcome = classify_structure_result(result)
        if outcome.kind != OutcomeKind.APPLIED:
            return outcome, []
        payload = result.json() or {}
        return outcome, [int(item) for item in payload.get("structure") or []]

    def _resolve(self, character_id: int, structure_id: int, token: str) -> dict | None:
        cached = self.cache.get(character_id, structure_id)
        if cached is not None:
            return cached
        url = (
            f"https://esi.evetech.net/latest/universe/structures/{int(structure_id)}/"
            "?datasource=tranquility"
        )
        result = self._call("GET", url, token)
        outcome = classify_structure_result(result)
        if outcome.kind == OutcomeKind.RATE_LIMITED:
            self.sleeper(max(outcome.retry_after, 0))
            result = self._call("GET", url, token)
            outcome = classify_structure_result(result)
        if outcome.kind == OutcomeKind.APPLIED and result.status == 200:
            payload = result.json() or {}
            record = {
                "ok": True,
                "name": str(payload.get("name") or structure_id),
                "system_id": int(payload.get("solar_system_id") or 0),
                "kind": "ok",
                "message": "",
            }
        else:
            record = {
                "ok": False,
                "name": "",
                "system_id": 0,
                "kind": "missing_scope" if "scope" in outcome.message.lower() else outcome.kind.value,
                "message": outcome.message,
            }
        self.cache.put(character_id, structure_id, record)
        return record

    def _call(self, method: str, url: str, token: str) -> HttpResult:
        return self.http.request(
            method,
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=45,
        )
