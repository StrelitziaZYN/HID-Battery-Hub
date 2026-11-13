"""产品名称与版本：所有界面、托盘、打包脚本都从这里取，改名只改一处。"""
from __future__ import annotations

APP_NAME = "HID Battery Hub"
APP_SHORT = "HIDBatteryHub"
TAGLINE = "2.4G 设备电量监测"
VERSION = "1.5"

# EXE / 图标 / 注册表项统一用无空格标识
EXE_STEM = "HIDBatteryHub"
REG_KEY_VALUE = "HIDBatteryHub"

# 数据目录名（EXE 同目录可写时直接用 EXE 目录，否则退回 %LOCALAPPDATA%\<这个>）
DATA_FOLDER = "HIDBatteryHub"

# 接入包（AI 产出的 JSON）标识
BUNDLE_KIND = "hid-battery-hub-profile"


def window_title(suffix: str = "") -> str:
    return f"{APP_NAME} · {suffix}" if suffix else APP_NAME


def version_tuple() -> tuple[int, int, int, int]:
    """把 VERSION 补成 Windows 版本资源要求的四段数字（1.0 → 1.0.0.0）。"""
    parts: list[int] = []
    for piece in VERSION.split(".")[:4]:
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts)  # type: ignore[return-value]

