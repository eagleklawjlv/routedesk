"""Saved routes, characters, and groups.

A route is one named, ordered list of stops. Routes are not paired.
Nothing in this module caps how many routes or stops can be stored.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

LEG_ORIGIN = "origin"
LEG_GATE = "gate"
LEG_JUMP = "jump"
LEG_CYNO = "cyno"
TRAVEL_LEGS = (LEG_GATE, LEG_JUMP, LEG_CYNO)


def new_id() -> str:
    return str(uuid.uuid4())


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


KIND_SYSTEM = "system"
KIND_STATION = "station"
KIND_STRUCTURE = "structure"


@dataclass
class Stop:
    system_id: int
    system_name: str
    leg_type: str = LEG_GATE
    security: float | None = None
    region_name: str = ""
    destination_id: int | None = None
    destination_name: str = ""
    destination_kind: str = KIND_SYSTEM
    seen_by: list[int] = field(default_factory=list)
    visibility: str = ""

    def __post_init__(self) -> None:
        if self.destination_id is None:
            self.destination_id = int(self.system_id)
        else:
            self.destination_id = int(self.destination_id)
        if not self.destination_name:
            self.destination_name = self.system_name
        if not self.destination_kind:
            self.destination_kind = KIND_SYSTEM

    def to_dict(self) -> dict:
        return {
            "system_id": int(self.system_id),
            "system_name": self.system_name,
            "leg_type": self.leg_type,
            "security": self.security,
            "region_name": self.region_name,
            "destination_id": int(self.destination_id),
            "destination_name": self.destination_name,
            "destination_kind": self.destination_kind,
            "seen_by": [int(character_id) for character_id in self.seen_by],
            "visibility": self.visibility,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Stop:
        if "system_id" not in data or "system_name" not in data:
            raise ValueError("A stop needs system_id and system_name")
        security = data.get("security")
        system_id = int(data["system_id"])
        system_name = str(data["system_name"])
        if "destination_id" in data and data["destination_id"] is not None:
            destination_id = int(data["destination_id"])
        else:
            destination_id = system_id
        if destination_id == system_id:
            kind = str(data.get("destination_kind") or KIND_SYSTEM)
        else:
            kind = str(data.get("destination_kind") or KIND_STATION)
        return cls(
            system_id=system_id,
            system_name=system_name,
            leg_type=str(data.get("leg_type") or LEG_GATE),
            security=None if security is None else float(security),
            region_name=str(data.get("region_name") or ""),
            destination_id=destination_id,
            destination_name=str(data.get("destination_name") or system_name),
            destination_kind=kind,
            seen_by=[int(character_id) for character_id in data.get("seen_by") or []],
            visibility=str(data.get("visibility") or ""),
        )


@dataclass
class Route:
    id: str
    name: str
    stops: list[Stop] = field(default_factory=list)
    notes: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "notes": self.notes,
            "updated_at": self.updated_at,
            "stops": [stop.to_dict() for stop in self.stops],
        }

    @classmethod
    def from_dict(cls, data: dict) -> Route:
        stops = [Stop.from_dict(item) for item in data.get("stops") or []]
        if stops:
            stops[0].leg_type = LEG_ORIGIN
        return cls(
            id=str(data.get("id") or new_id()),
            name=str(data.get("name") or "Untitled route"),
            stops=stops,
            notes=str(data.get("notes") or ""),
            updated_at=str(data.get("updated_at") or ""),
        )


def touch(route: Route) -> None:
    route.updated_at = utc_now()


def duplicate_route(route: Route) -> Route:
    copy = Route.from_dict(route.to_dict())
    copy.id = new_id()
    copy.name = f"{route.name} (copy)"
    touch(copy)
    return copy


def reverse_route(route: Route) -> Route:
    """Return a new route. The source route is left unchanged and is not linked."""
    stops = [Stop.from_dict(stop.to_dict()) for stop in route.stops]
    edges = [stop.leg_type for stop in stops[1:]]
    flipped = list(reversed(stops))
    flipped_edges = list(reversed(edges))
    if flipped:
        flipped[0].leg_type = LEG_ORIGIN
        for stop, edge in zip(flipped[1:], flipped_edges):
            stop.leg_type = edge
    notes = "Independent copy with the stops reversed. Not linked to the source route."
    if route.notes:
        notes = f"{notes} {route.notes}"
    copy = Route(id=new_id(), name=f"{route.name} (reversed)", stops=flipped, notes=notes)
    touch(copy)
    return copy


def move_stop(stops: list[Stop], index: int, delta: int) -> None:
    target = index + delta
    if index < 0 or target < 0 or index >= len(stops) or target >= len(stops):
        return
    stops[index], stops[target] = stops[target], stops[index]
    if stops:
        stops[0].leg_type = LEG_ORIGIN


def remove_stops(stops: list[Stop], indexes: list[int]) -> None:
    for index in sorted(set(indexes), reverse=True):
        if 0 <= index < len(stops):
            del stops[index]
    if stops:
        stops[0].leg_type = LEG_ORIGIN


@dataclass
class Character:
    character_id: int
    character_name: str
    assigned_route_id: str | None = None
    demo: bool = False
    scopes: list[str] = field(default_factory=list)
    authorized_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "character_id": int(self.character_id),
            "character_name": self.character_name,
            "assigned_route_id": self.assigned_route_id,
            "demo": bool(self.demo),
            "scopes": list(self.scopes),
            "authorized_at": self.authorized_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Character:
        # Refresh tokens are never a library field. Ignore them if a file has one.
        return cls(
            character_id=int(data["character_id"]),
            character_name=str(data["character_name"]),
            assigned_route_id=data.get("assigned_route_id"),
            demo=bool(data.get("demo")),
            scopes=[str(item) for item in data.get("scopes") or []],
            authorized_at=data.get("authorized_at"),
        )


@dataclass
class Group:
    id: str
    name: str
    character_ids: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "character_ids": [int(character_id) for character_id in self.character_ids],
        }

    @classmethod
    def from_dict(cls, data: dict) -> Group:
        return cls(
            id=str(data.get("id") or new_id()),
            name=str(data.get("name") or "Group"),
            character_ids=[int(character_id) for character_id in data.get("character_ids") or []],
        )


def assign_route(
    characters: list[Character], character_ids: list[int], route_id: str | None
) -> None:
    wanted = set(character_ids)
    for character in characters:
        if character.character_id in wanted:
            character.assigned_route_id = route_id


@dataclass
class Library:
    routes: list[Route] = field(default_factory=list)
    characters: list[Character] = field(default_factory=list)
    groups: list[Group] = field(default_factory=list)

    def route(self, route_id: str | None) -> Route | None:
        if not route_id:
            return None
        for route in self.routes:
            if route.id == route_id:
                return route
        return None

    def route_by_name(self, name: str) -> Route | None:
        for route in self.routes:
            if route.name == name:
                return route
        return None

    def character(self, character_id: int) -> Character | None:
        for character in self.characters:
            if character.character_id == int(character_id):
                return character
        return None
