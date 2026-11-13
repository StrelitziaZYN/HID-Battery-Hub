"""Win32 窗口材质：Mica / Acrylic（Windows 11 的原生背景）。

做法是 DWM 的标准三件套，不自己画模糊：
1. `DwmExtendFrameIntoClientArea(-1)` 把客户区纳入玻璃区；
2. `DWMWA_SYSTEMBACKDROP_TYPE` 指定 Mica(2) / Acrylic(3)；
3. 窗口本身用 `WA_TranslucentBackground` + 半透明底色，材质才透得出来。

Windows 10 上这套 API 不存在，`apply()` 会返回 "solid"，界面按纯色渲染——
宁可看起来普通，也不要出现「半透明但背后其实是黑块」的坏状态。
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Tuple

from PySide6.QtWidgets import QWidget

DWMWA_WINDOW_CORNER_PREFERENCE = 33      # Win11：圆角（2 = 圆角）
DWMWA_SYSTEMBACKDROP_TYPE = 38           # Win11 22H2+：背景材质
DWMSBT_NONE = 1
DWMSBT_MAINWINDOW = 2                    # Mica
DWMSBT_TRANSIENTWINDOW = 3               # Acrylic
DWMWA_USE_IMMERSIVE_DARK_MODE = 20

_BACKDROP_KIND = {"mica": DWMSBT_MAINWINDOW, "acrylic": DWMSBT_TRANSIENTWINDOW,
                  "solid": DWMSBT_NONE}

_dwm = None
try:                                       # 只有 Windows 才有 dwmapi
    _dwm = ctypes.WinDLL("dwmapi")
    _dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD,
                                          ctypes.c_void_p, wintypes.DWORD]
    _dwm.DwmSetWindowAttribute.restype = ctypes.c_long
    _dwm.DwmExtendFrameIntoClientArea.argtypes = [wintypes.HWND, ctypes.c_void_p]
    _dwm.DwmExtendFrameIntoClientArea.restype = ctypes.c_long
except Exception:                          # noqa: BLE001  非 Windows / 极老系统
    _dwm = None


class _MARGINS(ctypes.Structure):
    _fields_ = [("left", ctypes.c_int), ("right", ctypes.c_int),
                ("top", ctypes.c_int), ("bottom", ctypes.c_int)]


def build() -> int:
    """Windows build 号（非 Windows 返回 0）。"""
    try:
        return int(sys.getwindowsversion().build)
    except Exception:                      # noqa: BLE001
        return 0


def available() -> bool:
    """材料是否可用：只有 Win11 21H2（22000）之后才有系统级背景材质。"""
    return _dwm is not None and build() >= 22000


def _set_backdrop(hwnd: int, kind: int) -> bool:
    if _dwm is None or not hwnd:
        return False
    value = ctypes.c_int(kind)
    try:
        return _dwm.DwmSetWindowAttribute(wintypes.HWND(hwnd), DWMWA_SYSTEMBACKDROP_TYPE,
                                         ctypes.byref(value), ctypes.sizeof(value)) == 0
    except Exception:                      # noqa: BLE001
        return False


def _round_corners(hwnd: int) -> None:
    if _dwm is None or not hwnd:
        return
    value = ctypes.c_int(2)                # DWMWCP_ROUND
    try:
        _dwm.DwmSetWindowAttribute(wintypes.HWND(hwnd), DWMWA_WINDOW_CORNER_PREFERENCE,
                                   ctypes.byref(value), ctypes.sizeof(value))
    except Exception:                      # noqa: BLE001
        pass


def _extend_frame(hwnd: int) -> None:
    if _dwm is None or not hwnd:
        return
    margins = _MARGINS(-1, -1, -1, -1)
    try:
        _dwm.DwmExtendFrameIntoClientArea(wintypes.HWND(hwnd), ctypes.byref(margins))
    except Exception:                      # noqa: BLE001
        pass


def apply(window: QWidget, material: str) -> str:
    """把材质贴到窗口上，返回**实际生效**的材质（不支持时回落 "solid"）。"""
    material = material if material in _BACKDROP_KIND else "solid"
    if material == "solid":
        _set_backdrop(int(window.winId()), DWMSBT_NONE)
        return "solid"
    if not available():
        return "solid"
    hwnd = int(window.winId())             # 需要句柄：首次调用会创建原生窗口
    if not hwnd:
        return "solid"
    _extend_frame(hwnd)
    _round_corners(hwnd)
    if not _set_backdrop(hwnd, _BACKDROP_KIND[material]):
        return "solid"
    return material


def describe() -> Tuple[bool, str]:
    """(是否支持, 一句原因) —— 设置页用来给用户解释为什么回退。"""
    if _dwm is None:
        return False, "仅 Windows 支持"
    if build() < 22000:
        return False, f"需要 Windows 11 21H2 及以上（当前 {build()}）"
    return True, ""
