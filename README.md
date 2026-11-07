# Route Desk

Route Desk is a desktop program for planning named EVE Online routes and setting those stops as autopilot waypoints on characters you authorize yourself.

It runs on your computer. It is not a website, and it does not ship with an EVE application client ID. You register your own public client at [EVE Developers](https://developers.eveonline.com/applications). The program does not fly your ship, type into the EVE client, create cynos, or check jump range. Gate, jump, and cyno labels are notes on this computer.

EVE Online and the EVE logo are trademarks of CCP hf. Route Desk is not affiliated with CCP.

Copyright 2026 eagleklawjlv. All rights reserved. This archive does not grant an open-source license. See [Copyright](#copyright).

## Install and run

Python 3.11 or newer.

Windows (PowerShell), from this folder:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m everoute
```

Linux or macOS:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m everoute
```

The window opens in demo mode until you save your own client ID and turn demo mode off. Demo mode does not contact EVE. You can edit routes, search systems, and simulate an apply.

Your routes, character list, and client ID are stored only on your machine:

| System | Folder |
| --- | --- |
| Windows | `%APPDATA%\EveRouteDesk` |
| macOS | `~/Library/Application Support/EveRouteDesk` |
| Linux | `~/.local/share/everoute-desk` |

Override that folder with `EVEROUTE_DATA_DIR`. Refresh tokens are not in those JSON files. They go to the operating system credential store when it is available.

## Authorize your own characters

Route Desk will not accept your EVE password or a client secret. `--client-secret`, `--password`, and `--token` are refused.

1. Create a **native / public** application at https://developers.eveonline.com/applications. If the portal issues a client secret, that is the wrong application type. Do not paste a secret into this program.
2. Register this callback exactly: `http://127.0.0.1:45831/callback`. The listener binds only to loopback.
3. Enable the base scope below. Add the optional structure scopes only if you want structure search.
4. From this folder, with the virtual environment active:

```powershell
python -m everoute setup --client-id YOUR_PUBLIC_CLIENT_ID --host 127.0.0.1 --port 45831
python -m everoute authorize
python -m everoute
```

`setup` saves the public client ID, sets the callback, and turns demo mode off. It does not open a browser and it does not set waypoints. `authorize` keeps this window listening, opens your default browser, and waits until you approve one character on CCP's site. Repeat `authorize` for each character. `python -m everoute` with no command opens the window and does not turn demo mode back on.

`python -m everoute status` prints demo mode, whether a client ID is saved, the callback, and character names. It does not print the client ID or any token.

The sign-in is the [native PKCE flow](https://developers.eveonline.com/docs/services/sso/): random `state`, S256 code challenge, token exchange with the public client ID and code verifier only, then JWT checks (RS256, issuer `login.eveonline.com` or `https://login.eveonline.com`, audience contains your client ID and `EVE Online`, expiry, and the waypoint scope). SSO metadata is read from `https://login.eveonline.com/.well-known/oauth-authorization-server` and rejected if the host is not `login.eveonline.com`.

In the window, each login button lists the scopes it will request before the browser opens. **Authorize character** requests only the base scope. **Reauthorize for structure search** requests the base scope plus the two optional structure scopes. Character checkboxes do not choose who is updated. Select the character on the EVE page. If Required scopes shows only the waypoint scope, that login was the waypoint-only button. `publicData` is added by EVE and is not requested by name.

## ESI scopes

| Scope | Role |
| --- | --- |
| `esi-ui.write_waypoint.v1` | **Base. Required** to set autopilot waypoints. This is the only scope needed for routes, NPC stations, and waypoint apply. |
| `esi-search.search_structures.v1` | **Optional.** Lets one character search Upwell structure names that character can already see. |
| `esi-universe.read_structures.v1` | **Optional.** Resolves a structure id to a name and solar system for that same character. |

Do not enable corporation-structure, asset, mail, or Director scopes for this program. NPC station names come from public data and do not need the optional scopes. A waypoint-only token keeps working if you never add the structure scopes.

After you add the optional scopes on the **existing** application, reauthorize that character:

```powershell
python -m everoute authorize --with-structures
```

Or use **Reauthorize for structure search** in the window. You do not create a new client ID for this.

A structure result is labeled “Visible via character — docking unverified”. ESI does not return a docking-permission flag, and access lists change. A structure resolved for one character is not treated as available to the others. There is no ESI call that lists every structure a pilot can dock in. The public structure-id list omits any structure whose access list excludes even one pilot, and it does not include names or systems.

## Routes and waypoints

Save any number of independent routes. Duplicate and **Save reversed copy** make a new unlinked route. Stops can be a solar system, an NPC station in that system, or a structure id. Apply sends the saved destination id. The waypoint call is `POST https://esi.evetech.net/latest/ui/autopilot/waypoint/` with one destination per request:

- `add_to_beginning=false`, so stops stay in the order you saved
- `clear_other_waypoints=true` only on the first confirmed stop of a **replace**
- `clear_other_waypoints=false` for later stops, and for every stop in **append**

There is no published maximum on that endpoint. This program does not trim a route. Player reports describe an in-game warning that route optimization becomes very slow above about 12 waypoints. That is a client performance warning, not a limit here. Cancel stops later requests. The request already in flight can still finish.

ESI returns **204** when it accepts one request. That does not prove the game client showed the waypoint. The character should be logged in. ESI cannot read the autopilot back, and it cannot roll back a partial apply. A retry does not resend stops already accepted. A timeout or a 5xx is marked uncertain and is not retried automatically. Requests for one character are serial.

Leg labels are not sent. They do not check jump range, fuel, or cynos.

## Universe data

`everoute/data/systems.json` is a compact extract of solar system names, security, and regions from CCP's public static data export, build 3670638. `everoute/data/stations.json` is NPC station ids and names in those systems: system membership from that export, names from public `POST /universe/names`. **Tools → Refresh public universe cache** can download a later export. No character token is used for that data.

## Privacy

No telemetry. No EVE password. No client secret in the program or in this source archive. Routes and character names stay in the local data folder above. Refresh tokens stay in Windows Credential Manager, macOS Keychain, or a Secret Service daemon. If that store is unavailable, setup can opt in to a private file; leave that off unless you need it. The loopback page does not echo the authorization code. Outbound HTTPS is limited to `login.eveonline.com`, `esi.evetech.net`, and `developers.eveonline.com`.

## Tests

```sh
pip install -r requirements-dev.txt
python -m pytest -q
```

## Optional local build

No Windows executable is included. On Windows, `scripts/build_windows.ps1` creates a virtual environment and runs PyInstaller. If that script succeeds on your machine, the result is `dist\EveRouteDesk\EveRouteDesk.exe`, with `everoute/assets/route-desk.ico` embedded as the executable icon. `scripts/build_linux.sh` is the same kind of optional folder build for Linux and packs the same icon assets for the window. The interface uses PySide6 (Qt, LGPL). Running from source does not require you to redistribute a frozen Qt build.

A source launch (`python -m everoute` or `pythonw -m everoute`) sets the Windows AppUserModelID `eagleklawjlv.RouteDesk` before the window opens and uses that icon for the window. That is what replaces the Python taskbar icon. The shortcut icon is only the pinned button. `scripts/update_windows_shortcut.ps1` points an existing shortcut at `route-desk.ico` and the same id. It does not change the shortcut target and it does not read routes, settings, or tokens.

## Third-party notices

- EVE Online game data, ESI, and SSO are provided by CCP hf. Use of that data is subject to CCP's developer terms. Route Desk is not affiliated with CCP.
- PySide6 and Qt are licensed under the GNU Lesser General Public License version 3. Copyright The Qt Company and contributors.
- PyJWT is used to check EVE access-token signatures. The `cryptography` package verifies those RS256 signatures.
- `keyring` stores refresh tokens in the operating system credential store.
- Solar system and NPC station data in `everoute/data/` is derived from CCP's public static data export and public ESI name lookups.

## Copyright

Copyright 2026 eagleklawjlv. All rights reserved.

No open-source license has been selected for Route Desk. This source archive does not grant permission to copy, modify, or redistribute the program beyond what you are already allowed to do with a private copy. Third-party components above remain under their own licenses.
