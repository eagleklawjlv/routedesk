"""User-facing copy. Tests lock the phrases that describe limits honestly."""

from __future__ import annotations

SCOPE = "esi-ui.write_waypoint.v1"
SEARCH_STRUCTURES_SCOPE = "esi-search.search_structures.v1"
READ_STRUCTURES_SCOPE = "esi-universe.read_structures.v1"
STRUCTURE_SCOPES = (SEARCH_STRUCTURES_SCOPE, READ_STRUCTURES_SCOPE)

STRUCTURE_LIMITATION = (
    "Upwell structures are not a public station list. ESI has no call that enumerates every "
    "structure a pilot can dock in, and the structure-info response has no docking-permission field. "
    "A hit means that character could resolve the structure at search time. The label is "
    "“Visible via name — docking unverified”. Access lists change, a fully public structure list "
    "omits anything that excludes even one pilot, and a private structure found for one character "
    "is not treated as available to the others. NPC stations in a system come from public data "
    "and do not need extra scopes."
)

STORAGE_NOTE = (
    "Saved routes and stops are limited only by memory and disk on this computer. "
    "Route Desk does not enforce a maximum number of routes or midpoints."
)

DEMO_BANNER = (
    "DEMO / TEST MODE — no EVE account is contacted. Applies are simulated on this computer. "
    "Turn this off in Mode after you save your own SSO client ID."
)

LIVE_BANNER = (
    "Live mode. Apply sends one ESI request per stop for each checked character. "
    "A 204 means ESI accepted that request. It does not prove the game client showed the waypoint."
)

SSO_SETUP_INTRO = (
    "Route Desk runs on this computer. It does not include a CCP application client ID, "
    "and it never asks for your EVE password or a client secret.\n\n"
    "Create your own application at https://developers.eveonline.com/applications\n\n"
    "Choose a native / public client that uses Authorization Code with PKCE. "
    "If the portal issues a client secret, that is the wrong application type. "
    "Do not paste a secret into this program. Route Desk will not send one.\n\n"
    "Register the callback URL shown on the next page, exactly.\n"
    f"Enable only this scope: {SCOPE}\n"
    "That scope lets the program set autopilot waypoints for the character you choose on the "
    "EVE login page. Each character is authorized on their own. No other scope is requested."
)

NO_SECRET_NOTE = (
    "No client secret is stored or transmitted. Refresh tokens go to the operating system "
    "credential store when it is available. Access tokens stay in memory."
)

ABOUT_TEXT = """Route Desk
Local desktop tool for saving named EVE routes and setting them as autopilot waypoints on characters you authorize.

{STORAGE_NOTE}

ESI POST /ui/autopilot/waypoint/ sets one destination per request. The published endpoint has no maximum count and no way to read the current autopilot back. This program stores your whole route and sends every stop, in order, unless you cancel or a request fails. It does not silently drop stops.

Player reports describe an in-game warning that route optimization becomes very slow above about 12 waypoints. That is a game-client performance warning, not an ESI schema limit and not a limit in Route Desk. A long list can make the EVE client slow. The saved route is still kept whole.

There is no transaction. If stop 4 fails, stops 1–3 stay in the client. Nothing in ESI rolls them back. A retry does not send stops that already returned a success status. A timeout is marked uncertain and is not retried automatically, because a blind retry could duplicate a waypoint the client already accepted.

Gate, jump, and cyno labels are planning notes on this computer. They are not sent. They are not a jump-range, fuel, or cyno check. The program does not fly your ship or inject input into the EVE client.

Login happens in your browser on EVE's site. This program only receives the loopback redirect. Demo / test mode does not call ESI or SSO.

EVE Online and related marks are trademarks of CCP hf. Route Desk is not affiliated with CCP.
"""

CALLBACK_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Route Desk</title>
<style>
  body { margin: 0; background: #121418; color: #e7e2d6;
         font: 16px/1.5 "Segoe UI", "Liberation Sans", sans-serif; }
  main { max-width: 36rem; margin: 4rem auto; padding: 0 1.25rem; }
  h1 { font-size: 1.25rem; color: #e0a045; }
</style>
</head>
<body>
<main>
  <h1>Route Desk</h1>
  <p>The sign-in redirect was received. You can close this tab and return to the application.</p>
</main>
</body>
</html>
"""


def abbreviate_names(names: list[str], edges: int = 3) -> str:
    """Shorten a list for a dialog. Display only — callers must still send every stop."""
    if len(names) <= edges * 2:
        return " → ".join(names)
    head = " → ".join(names[:edges])
    tail = " → ".join(names[-edges:])
    hidden = len(names) - (edges * 2)
    return f"{head} → … {hidden} more (still sent) … → {tail}"


def waypoint_contract(count: int) -> str:
    text = (
        f"All {count} stops are stored and will be sent in order, one ESI request at a time. "
        "Nothing is dropped to fit a quota. "
        "POST /ui/autopilot/waypoint/ does not publish a maximum count. "
        "It also cannot read the autopilot back or set the whole list in one transaction. "
        "204 means that single request was accepted, not that the client window was checked. "
        "The character should be logged into the game. "
        "Gate, jump, and cyno labels stay on this computer and are not part of the request. "
        "This does not check jump range, fuel, or cynos, and it does not fly the ship."
    )
    if count > 12:
        text += (
            " Player reports say the EVE client warns that optimizing more than about 12 waypoints "
            "can be very slow. That warning is not a cap in this program. "
            "Route Desk still sends every stop unless you cancel or a request fails."
        )
    return text
