"""Launch Route Desk, or run a local SSO command."""

from __future__ import annotations

import os
import sys
from pathlib import Path

CLI_COMMANDS = {"setup", "authorize", "status"}


def extract_data_dir(argv: list[str]) -> tuple[str | None, list[str]]:
    data_dir = None
    rest: list[str] = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--data-dir" and index + 1 < len(argv):
            data_dir = argv[index + 1]
            index += 2
            continue
        if arg.startswith("--data-dir="):
            data_dir = arg.split("=", 1)[1]
            index += 1
            continue
        rest.append(arg)
        index += 1
    return data_dir, rest


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    data_dir, rest = extract_data_dir(argv)
    if data_dir:
        os.environ["EVEROUTE_DATA_DIR"] = data_dir
    if rest and rest[0] in CLI_COMMANDS:
        from everoute.cli import main as cli_main

        return cli_main(rest)

    import argparse

    parser = argparse.ArgumentParser(
        prog="everoute",
        description="Route Desk, a local EVE waypoint editor.",
        epilog="SSO from the command line: python -m everoute setup --client-id PUBLIC_CLIENT_ID, then python -m everoute authorize. Launching with no command opens the window and does not change demo mode.",
    )
    parser.add_argument("--data-dir", help="Folder for routes, settings, and the universe cache.")
    parser.add_argument(
        "--capture-dir",
        help="Open the window, write PNG screenshots, and exit. For local verification.",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Show demo mode for this window only. Does not change the saved setting.",
    )
    args = parser.parse_args(argv)

    try:
        from PySide6.QtGui import QFont
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("PySide6 is not installed. Run: pip install -r requirements.txt", file=sys.stderr)
        return 1

    from everoute.gui.main_window import MainWindow
    from everoute.gui.theme import app_icon, apply_theme
    from everoute.paths import AppPaths, default_paths
    from everoute.store import SettingsStore, Store

    app = QApplication.instance() or QApplication(sys.argv)
    font = QFont("Segoe UI", 10)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    app.setFont(font)
    apply_theme(app)
    app.setWindowIcon(app_icon())
    paths = default_paths()
    window = MainWindow(Store(AppPaths(paths.root)), SettingsStore(AppPaths(paths.root)))
    if args.demo:
        window.use_session_demo()
    window.show()
    if args.capture_dir:
        window.capture_story(Path(args.capture_dir))
        return 0
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
