"""Local commands for SSO setup and character authorization.

These commands do not accept a password or client secret and do not set waypoints.
"""

from __future__ import annotations

import argparse
import sys
import webbrowser

from everoute.account import SECRET_FLAGS, configure_sso
from everoute.credentials import CredentialError, open_credential_store
from everoute.http import HttpClient
from everoute.messages import READ_STRUCTURES_SCOPE, SCOPE, SEARCH_STRUCTURES_SCOPE
from everoute.oauth import AuthError, OAuthClient, run_browser_login
from everoute.paths import AppPaths, default_paths
from everoute.store import SettingsStore, Store


def reject_secret_args(argv: list[str]) -> str | None:
    for arg in argv:
        name = arg.split("=", 1)[0].lower()
        if name in SECRET_FLAGS:
            return name
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="everoute",
        description="Configure the public EVE client ID, or authorize one character in the browser.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("setup", help="Save the public client ID and loopback callback, and turn demo mode off.")
    setup.add_argument("--client-id", help="Public client ID from the EVE developers site. Not a secret.")
    setup.add_argument("--host", choices=("127.0.0.1", "localhost"), help="Callback host. Default: 127.0.0.1.")
    setup.add_argument("--port", type=int, help="Callback port. Default: 45831, or the port already saved.")
    setup.add_argument(
        "--allow-file-token-store",
        action="store_true",
        help="If the OS credential store is unavailable, allow a private local file. Off unless you pass this.",
    )

    authorize = sub.add_parser(
        "authorize",
        help="Listen on the loopback callback, open the default browser, and save the character you approve.",
    )
    authorize.add_argument("--timeout", type=int, default=300, help="Seconds to wait for the browser redirect.")
    authorize.add_argument(
        "--with-structures",
        action="store_true",
        help="Also request structure search scopes. Add them on the existing EVE application first. Does not set waypoints.",
    )

    sub.add_parser("status", help="Show demo mode, callback URL, and saved character names. Tokens are not printed.")
    return parser


def command_setup(args: argparse.Namespace, settings_store: SettingsStore) -> int:
    client_id = args.client_id
    if not client_id:
        if not sys.stdin.isatty():
            print(
                "Pass the public client ID with --client-id. A password or client secret is not accepted.",
                file=sys.stderr,
            )
            return 2
        client_id = input("Public client ID: ")
    try:
        settings = configure_sso(
            settings_store,
            client_id=client_id,
            host=args.host,
            port=args.port,
            allow_file_token_store=True if args.allow_file_token_store else None,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print("SSO setup saved through Route Desk settings.")
    print("Demo mode: off")
    print(f"Callback URL to register exactly: {settings.redirect_uri()}")
    print(f"Scope to enable on the application: {SCOPE}")
    print("No client secret was stored. No waypoint request was sent.")
    print("Routes already on this computer were left in place.")
    print("Next, leave this command running and approve one character in the browser:")
    print("  python -m everoute authorize")
    return 0


def command_authorize(args: argparse.Namespace, paths: AppPaths, *, login=run_browser_login, credentials=None) -> int:
    settings_store = SettingsStore(paths)
    settings = settings_store.load()
    if not settings.client_id:
        print(
            "No public client ID is saved. Run: python -m everoute setup --client-id YOUR_PUBLIC_CLIENT_ID",
            file=sys.stderr,
        )
        return 2
    if settings.demo_mode:
        print("Demo mode is still on. Run setup again so demo mode is turned off.", file=sys.stderr)
        return 2
    if credentials is None:
        try:
            credentials, kind = open_credential_store(paths.tokens, settings.allow_file_token_store)
        except CredentialError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if kind == "file":
            print("Refresh tokens will use the private file store you opted into.")
    store = Store(paths)
    client = OAuthClient(HttpClient(), settings.client_id, settings.redirect_uri())
    scopes = [SCOPE]
    if getattr(args, "with_structures", False):
        scopes = [SCOPE, SEARCH_STRUCTURES_SCOPE, READ_STRUCTURES_SCOPE]
        print("Requesting waypoint scope plus structure search scopes.")
        print("Add those two structure scopes on the existing EVE application before you approve.")
        print("NPC station lists do not need this. A waypoint-only character keeps working.")
    print(f"Listening on {settings.redirect_uri()}")
    print("Opening the default browser for EVE SSO. Sign in and approve the character there.")
    print("This window has to stay open until the browser returns to the local callback. No password is read here.")
    print("This command does not set waypoints.")
    try:
        tokens, claims = login(
            client,
            int(settings.callback_port),
            webbrowser.open,
            timeout=int(args.timeout),
            scopes=scopes,
        )
    except AuthError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    from everoute.account import remember_authorized_character

    library = store.load()
    try:
        character = remember_authorized_character(library, credentials, claims, tokens)
    except AuthError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    store.save(library)
    print(f"Authorized {character.character_name} ({character.character_id}).")
    print("The character is in the local library. Restart Route Desk, or click its window, to see them.")
    print("The refresh token was stored in the credential store. It is not printed and not written into the route file.")
    return 0


def command_status(paths: AppPaths) -> int:
    settings = SettingsStore(paths).load()
    library = Store(paths).load()
    print(f"Demo mode: {'on' if settings.demo_mode else 'off'}")
    print(f"Client ID saved: {'yes' if settings.client_id else 'no'}")
    print(f"Callback: {settings.redirect_uri()}")
    if not library.characters:
        print("Characters: none")
        return 0
    print("Characters:")
    for character in library.characters:
        kind = "demo" if character.demo else "authorized"
        print(f"  {character.character_name} ({character.character_id}) {kind}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    refused = reject_secret_args(argv)
    if refused:
        print(
            f"Refusing {refused}. Route Desk does not accept a password, client secret, or token on the command line.",
            file=sys.stderr,
        )
        return 2
    parser = build_parser()
    args = parser.parse_args(argv)
    paths = default_paths()
    if args.command == "setup":
        return command_setup(args, SettingsStore(paths))
    if args.command == "authorize":
        return command_authorize(args, paths)
    if args.command == "status":
        return command_status(paths)
    parser.error(f"Unknown command {args.command}")
    return 2
