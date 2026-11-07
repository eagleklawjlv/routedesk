"""Windows taskbar identity for source launches and frozen executables.

Windows groups a taskbar button by the process AppUserModelID. python.exe and
pythonw.exe do not have one for Route Desk, so the taskbar keeps the
interpreter icon even after QWidget.setWindowIcon. Setting the id before
QApplication, then applying the window icon, gives this process its own
button. A pinned shortcut only matches that button when it carries the same
id; the shortcut icon alone does not change a direct source launch.
"""

from __future__ import annotations

import sys

from everoute.paths import bundled_icon_path

APP_USER_MODEL_ID = "eagleklawjlv.RouteDesk"


def apply_windows_app_user_model_id() -> bool:
    """Set the process id. Call this before QApplication is constructed."""
    if sys.platform != "win32":
        return False
    import ctypes

    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    return True


def apply_native_window_icon(widget) -> bool:
    """Push the .ico into the native window after Qt has a HWND.

    Qt's setWindowIcon is the cross-platform path. On Windows the taskbar
    also reads the small and large HICONs and the window-class icon, which
    otherwise stay on the host executable.
    """
    if sys.platform != "win32":
        return False
    path = bundled_icon_path()
    if not path.is_file():
        return False
    try:
        hwnd = int(widget.winId())
    except Exception:
        return False
    if not hwnd:
        return False
    try:
        _set_win32_icons(hwnd, str(path))
    except Exception:
        return False
    return True


def _set_win32_icons(hwnd: int, ico_path: str) -> None:
    import ctypes

    user32 = ctypes.windll.user32
    image_icon = 1
    lr_loadfromfile = 0x00000010
    wm_seticon = 0x0080
    icon_small = 0
    icon_big = 1
    gcl_hicon = -14
    gcl_hiconsm = -34
    sm_cxicon = 11
    sm_cyicon = 12
    sm_cxsmicon = 49
    sm_cysmicon = 50

    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    big_w = user32.GetSystemMetrics(sm_cxicon) or 32
    big_h = user32.GetSystemMetrics(sm_cyicon) or 32
    small_w = user32.GetSystemMetrics(sm_cxsmicon) or 16
    small_h = user32.GetSystemMetrics(sm_cysmicon) or 16

    user32.LoadImageW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_uint,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    user32.LoadImageW.restype = ctypes.c_void_p
    big = user32.LoadImageW(None, ico_path, image_icon, big_w, big_h, lr_loadfromfile)
    small = user32.LoadImageW(None, ico_path, image_icon, small_w, small_h, lr_loadfromfile)
    if not big and not small:
        return

    user32.SendMessageW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    user32.SendMessageW.restype = ctypes.c_void_p
    if big:
        user32.SendMessageW(hwnd, wm_seticon, icon_big, big)
    if small:
        user32.SendMessageW(hwnd, wm_seticon, icon_small, small)

    set_class = getattr(user32, "SetClassLongPtrW", None) or getattr(user32, "SetClassLongW", None)
    if set_class is None:
        return
    set_class.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    set_class.restype = ctypes.c_void_p
    if big:
        set_class(hwnd, gcl_hicon, big)
    if small:
        set_class(hwnd, gcl_hiconsm, small)
