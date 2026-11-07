"""Window icon files, frozen resource paths, and Windows taskbar identity."""

import struct
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QSize
from PySide6.QtGui import QImage, QIcon
from PySide6.QtWidgets import QApplication

from everoute.gui.theme import ICON_SIZES, app_icon
from everoute.paths import bundled_assets_dir, bundled_data_dir, bundled_icon_path, bundle_root
from everoute.win_taskbar import APP_USER_MODEL_ID, apply_native_window_icon, apply_windows_app_user_model_id

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "everoute" / "assets"


def ico_sizes(path: Path) -> list[tuple[int, int]]:
    data = path.read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    assert reserved == 0
    assert kind == 1
    sizes = []
    for index in range(count):
        width, height = struct.unpack_from("<BB", data, 6 + 16 * index)
        sizes.append((256 if width == 0 else width, 256 if height == 0 else height))
    return sizes


def test_svg_is_a_wordless_route_mark():
    svg = (ASSETS / "route-desk.svg").read_text(encoding="utf-8")
    assert "<text" not in svg
    assert "#0B1F3A" in svg
    assert "#1AD4F5" in svg
    assert "#F5B12A" in svg
    lowered = svg.lower()
    for banned in ("ship", "logo", "eve online", "ccp"):
        assert banned not in lowered


def test_icon_files_cover_taskbar_sizes():
    assert (ASSETS / "route-desk.svg").is_file()
    assert (ASSETS / "route-desk.ico").is_file()
    for size in ICON_SIZES:
        path = ASSETS / f"route-desk-{size}.png"
        image = QImage(str(path))
        assert not image.isNull()
        assert image.width() == size
        assert image.height() == size
    sizes = set(ico_sizes(ASSETS / "route-desk.ico"))
    assert {(16, 16), (32, 32), (48, 48), (256, 256)} <= sizes


def _counts(path: Path) -> tuple[int, int, int]:
    image = QImage(str(path))
    navy = cyan = amber = 0
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            red, green, blue, alpha = color.red(), color.green(), color.blue(), color.alpha()
            if alpha < 20:
                continue
            if red < 40 and green < 70 and 30 < blue < 100:
                navy += 1
            elif red > 190 and 120 < green < 220 and blue < 90:
                amber += 1
            elif red < 90 and green > 150 and blue > 170:
                cyan += 1
    return navy, cyan, amber


def test_small_icons_keep_navy_cyan_route_and_amber_end():
    for size in (16, 32, 48):
        navy, cyan, amber = _counts(ASSETS / f"route-desk-{size}.png")
        assert navy > 20, size
        assert cyan > 4, size
        assert amber > 4, size


def test_source_bundle_paths_point_at_package_files():
    assert bundle_root() == ROOT / "everoute"
    assert bundled_assets_dir() == ASSETS
    assert bundled_icon_path() == ASSETS / "route-desk.ico"
    assert (bundled_data_dir() / "systems.json").is_file()


def test_frozen_bundle_paths_use_meipass(tmp_path, monkeypatch):
    root = tmp_path / "everoute"
    (root / "assets").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "assets" / "route-desk.ico").write_bytes(b"ico")
    (root / "data" / "systems.json").write_text("{}", encoding="utf-8")
    for size in (16, 32):
        source = ASSETS / f"route-desk-{size}.png"
        (root / "assets" / source.name).write_bytes(source.read_bytes())
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert bundle_root() == root
    assert bundled_assets_dir() == root / "assets"
    assert bundled_icon_path() == root / "assets" / "route-desk.ico"
    assert bundled_data_dir() == root / "data"
    icon = app_icon()
    assert not icon.isNull()
    for size in (16, 32):
        pixmap = icon.pixmap(QSize(size, size))
        assert not pixmap.isNull()
        assert pixmap.width() == size
        assert pixmap.height() == size


def test_app_icon_registers_source_png_sizes():
    QApplication.instance() or QApplication([])
    icon = app_icon()
    assert isinstance(icon, QIcon)
    assert not icon.isNull()
    for size in (16, 32, 48):
        pixmap = icon.pixmap(QSize(size, size))
        assert pixmap.width() == size
        assert pixmap.height() == size


def test_windows_identity_is_shared_and_inert_off_windows():
    assert APP_USER_MODEL_ID == "eagleklawjlv.RouteDesk"
    assert apply_windows_app_user_model_id() is (sys.platform == "win32")
    assert apply_native_window_icon(object()) is False or sys.platform == "win32"
    script = (ROOT / "scripts" / "update_windows_shortcut.ps1").read_text(encoding="utf-8")
    assert APP_USER_MODEL_ID in script
    assert "TargetPath" in script
    assert "settings.json" not in script
    assert "library.json" not in script
    assert "%APPDATA%\\EveRouteDesk" in script
    windows_build = (ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")
    assert '--icon "everoute\\assets\\route-desk.ico"' in windows_build
    assert '--add-data "everoute\\assets;everoute\\assets"' in windows_build
    linux_build = (ROOT / "scripts" / "build_linux.sh").read_text(encoding="utf-8")
    assert "everoute/assets:everoute/assets" in linux_build
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "assets/*" in project


def test_main_window_uses_the_route_icon(tmp_path):
    from everoute.gui.main_window import MainWindow
    from everoute.paths import AppPaths
    from everoute.sample import sample_index
    from everoute.store import SettingsStore, Store

    QApplication.instance() or QApplication([])
    window = MainWindow(Store(AppPaths(tmp_path)), SettingsStore(AppPaths(tmp_path)), index=sample_index())
    icon = window.windowIcon()
    assert not icon.isNull()
    pixmap = icon.pixmap(QSize(32, 32))
    assert pixmap.width() == 32
    assert window._native_taskbar_icon is True
    image = pixmap.toImage()
    navy = 0
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if color.red() < 40 and color.green() < 70 and 30 < color.blue() < 100:
                navy += 1
    assert navy > 20
    window.close()


def test_gui_entry_sets_app_id_before_creating_qapplication():
    source = (ROOT / "everoute" / "__main__.py").read_text(encoding="utf-8")
    assert source.index("apply_windows_app_user_model_id()") < source.index("QApplication(")
    window = (ROOT / "everoute" / "gui" / "main_window.py").read_text(encoding="utf-8")
    assert "install_taskbar_icon" in window
    assert "setWindowIcon" in window
