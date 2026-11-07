"""First-launch examples. These routes are independent, not a linked pair."""

from __future__ import annotations

from everoute.models import Character, Group, Library, Route, Stop, new_id, touch
from everoute.universe import SolarSystem, SystemIndex

# Public ESI /universe/ids/ on 2026-11-07.
JITA_ID = 30000142
IGNOITTON_ID = 30002647
SISEIDE_ID = 30002539

ROUTE_TO_SISEIDE = "Jita - Ignoitton - Siseide"
ROUTE_TO_JITA = "Siseide - Ignoitton - Jita"

EXAMPLE_NOTE = (
    "Example list only. Not linked to any other route. "
    "Jump labels are planning notes on this computer. "
    "They are not sent to EVE and they were not checked against jump range."
)

ALPHA_ID = 2112000001
BRAVO_ID = 2112000002


def fallback_systems() -> list[SolarSystem]:
    return [
        SolarSystem(JITA_ID, "Jita", 0.94591, "The Forge", None),
        SolarSystem(IGNOITTON_ID, "Ignoitton", None, "", None),
        SolarSystem(SISEIDE_ID, "Siseide", None, "", None),
    ]


def sample_index() -> SystemIndex:
    return SystemIndex(fallback_systems(), {"source": "built-in verified ids"})


def _resolve(index: SystemIndex | None, name: str, system_id: int) -> SolarSystem:
    if index is not None:
        found = index.exact(name)
        if found is not None:
            return found
    return SolarSystem(system_id, name, None, "", None)


def _stop(system: SolarSystem, leg: str) -> Stop:
    return Stop(
        system_id=system.system_id,
        system_name=system.name,
        leg_type=leg,
        security=system.security,
        region_name=system.region_name,
    )


def sample_library(index: SystemIndex | None = None) -> Library:
    jita = _resolve(index, "Jita", JITA_ID)
    ignoitton = _resolve(index, "Ignoitton", IGNOITTON_ID)
    siseide = _resolve(index, "Siseide", SISEIDE_ID)
    to_siseide = Route(
        id=new_id(),
        name=ROUTE_TO_SISEIDE,
        notes=EXAMPLE_NOTE,
        stops=[
            _stop(jita, "origin"),
            _stop(ignoitton, "jump"),
            _stop(siseide, "jump"),
        ],
    )
    to_jita = Route(
        id=new_id(),
        name=ROUTE_TO_JITA,
        notes=EXAMPLE_NOTE,
        stops=[
            _stop(siseide, "origin"),
            _stop(ignoitton, "jump"),
            _stop(jita, "jump"),
        ],
    )
    touch(to_siseide)
    touch(to_jita)
    alpha = Character(
        character_id=ALPHA_ID,
        character_name="Demo Pilot Alpha",
        assigned_route_id=to_siseide.id,
        demo=True,
    )
    bravo = Character(
        character_id=BRAVO_ID,
        character_name="Demo Pilot Bravo",
        assigned_route_id=to_jita.id,
        demo=True,
    )
    groups = [
        Group(id=new_id(), name="Set A", character_ids=[ALPHA_ID]),
        Group(id=new_id(), name="Set B", character_ids=[BRAVO_ID]),
    ]
    return Library(routes=[to_siseide, to_jita], characters=[alpha, bravo], groups=groups)
