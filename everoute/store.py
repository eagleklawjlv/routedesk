"""JSON persistence. Routes and jobs are stored whole; nothing is sliced on save."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from everoute.models import Character, Group, Library, Route, new_id
from everoute.paths import AppPaths
from everoute.waypoints import ApplyJob

LIBRARY_VERSION = 1


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


class Store:
    def __init__(self, paths: AppPaths):
        self.paths = paths
        self._lock = threading.Lock()

    def load(self) -> Library:
        with self._lock:
            library = self._read_library()
        return library

    def save(self, library: Library) -> None:
        payload = {
            "version": LIBRARY_VERSION,
            "routes": [route.to_dict() for route in library.routes],
            "characters": [character.to_dict() for character in library.characters],
            "groups": [group.to_dict() for group in library.groups],
        }
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        with self._lock:
            atomic_write(self.paths.library, text)

    def load_jobs(self) -> list[ApplyJob]:
        path = self.paths.jobs
        if not path.exists():
            return []
        with self._lock:
            payload = json.loads(path.read_text(encoding="utf-8"))
        return [ApplyJob.from_dict(item) for item in payload.get("jobs") or []]

    def save_jobs(self, jobs: list[ApplyJob]) -> None:
        payload = {"version": 1, "jobs": [job.to_dict() for job in jobs]}
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        with self._lock:
            atomic_write(self.paths.jobs, text)

    def _read_library(self) -> Library:
        path = self.paths.library
        if not path.exists():
            return Library()
        payload = json.loads(path.read_text(encoding="utf-8"))
        version = int(payload.get("version") or 1)
        if version != LIBRARY_VERSION:
            raise ValueError(f"Unrecognized library version {version}")
        return Library(
            routes=[Route.from_dict(item) for item in payload.get("routes") or []],
            characters=[Character.from_dict(item) for item in payload.get("characters") or []],
            groups=[Group.from_dict(item) for item in payload.get("groups") or []],
        )


def export_routes(routes: list[Route]) -> dict:
    return {
        "format": "everoute-routes",
        "version": 1,
        "routes": [route.to_dict() for route in routes],
    }


def import_routes(payload: dict) -> list[Route]:
    if not isinstance(payload, dict):
        raise ValueError("Route file must be a JSON object")
    if payload.get("format") not in (None, "everoute-routes"):
        raise ValueError("This file is not a Route Desk route export")
    version = int(payload.get("version") or 1)
    if version != 1:
        raise ValueError(f"Unrecognized route file version {version}")
    routes_raw = payload.get("routes")
    if not isinstance(routes_raw, list):
        raise ValueError("Route file has no routes list")
    imported: list[Route] = []
    for item in routes_raw:
        if not isinstance(item, dict):
            raise ValueError("A route entry was not an object")
        route = Route.from_dict(item)
        route.id = new_id()
        imported.append(route)
    return imported


class Settings:
    def __init__(
        self,
        client_id: str = "",
        callback_port: int = 45831,
        callback_host: str = "127.0.0.1",
        demo_mode: bool = True,
        allow_file_token_store: bool = False,
        seeded_sample: bool = False,
        last_route_id: str | None = None,
    ):
        self.client_id = client_id
        self.callback_port = callback_port
        self.callback_host = callback_host
        self.demo_mode = demo_mode
        self.allow_file_token_store = allow_file_token_store
        self.seeded_sample = seeded_sample
        self.last_route_id = last_route_id

    def redirect_uri(self) -> str:
        return f"http://{self.callback_host}:{int(self.callback_port)}/callback"

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "client_id": self.client_id,
            "callback_port": int(self.callback_port),
            "callback_host": self.callback_host,
            "demo_mode": bool(self.demo_mode),
            "allow_file_token_store": bool(self.allow_file_token_store),
            "seeded_sample": bool(self.seeded_sample),
            "last_route_id": self.last_route_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Settings:
        host = str(data.get("callback_host") or "127.0.0.1")
        if host not in ("127.0.0.1", "localhost"):
            host = "127.0.0.1"
        port = int(data.get("callback_port") or 45831)
        if port < 1024 or port > 65535:
            port = 45831
        return cls(
            client_id=str(data.get("client_id") or ""),
            callback_port=port,
            callback_host=host,
            demo_mode=bool(data.get("demo_mode", True)),
            allow_file_token_store=bool(data.get("allow_file_token_store", False)),
            seeded_sample=bool(data.get("seeded_sample", False)),
            last_route_id=data.get("last_route_id"),
        )


class SettingsStore:
    def __init__(self, paths: AppPaths):
        self.paths = paths
        self._lock = threading.Lock()

    def load(self) -> Settings:
        path = self.paths.settings
        if not path.exists():
            return Settings()
        return Settings.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def save(self, settings: Settings) -> None:
        text = json.dumps(settings.to_dict(), indent=2)
        with self._lock:
            atomic_write(self.paths.settings, text)
