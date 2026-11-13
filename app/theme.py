"""Fluent 视觉常量与小工具。"""
from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QFontDatabase
from PySide6.QtWidgets import QLabel, QWidget


class C:
    # 底色（半透明，叠在窗口模糊层上）
    WINDOW = QColor(249, 249, 249, 246)
    SURFACE = QColor(255, 255, 255, 235)
    SURFACE_HOVER = QColor(0, 0, 0, 8)
    SURFACE_PRESSED = QColor(0, 0, 0, 14)
    STROKE = QColor(0, 0, 0, 20)
    STROKE_STRONG = QColor(0, 0, 0, 38)

    TEXT = QColor(26, 26, 26)
    TEXT_SECONDARY = QColor(96, 96, 96)
    TEXT_TERTIARY = QColor(138, 138, 138)

    ACCENT = QColor(0, 103, 192)
    ACCENT_SOFT = QColor(0, 103, 192, 26)
    DANGER = QColor(196, 43, 28)
    WARN = QColor(157, 93, 0)
    OK = QColor(15, 123, 15)

    TRACK = QColor(0, 0, 0, 26)

    # 卡片 / 分组（Fluent 2 的「层」：窗口灰 → 卡片白 → 分组更浅）
    CARD = QColor(255, 255, 255, 242)
    CARD_STROKE = QColor(0, 0, 0, 16)
    CARD_SHADOW = QColor(0, 0, 0, 34)
    GROUP = QColor(0, 0, 0, 5)
    GROUP_STROKE = QColor(0, 0, 0, 10)
    ROW_HOVER = QColor(0, 0, 0, 5)

    RADIUS = 8
    RADIUS_SMALL = 6

    # 模块标识色：只用在图标瓦片 / 强调条上，正文仍走中性色，避免花
    MODULE = {
        "blue": QColor(0, 103, 192),
        "teal": QColor(3, 131, 135),
        "violet": QColor(113, 87, 182),
        "amber": QColor(157, 93, 0),
        "slate": QColor(90, 96, 106),
    }


_UI_FAMILIES = ["Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei"]
_DISPLAY_FAMILIES = ["Segoe UI Variable Display", "Segoe UI", "Microsoft YaHei UI"]


def ui_font(size: int = 10, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    f = QFont()
    f.setFamilies(_UI_FAMILIES)
    f.setPointSize(size)
    f.setWeight(weight)
    return f


def display_font(size: int = 20, weight: QFont.Weight = QFont.Weight.DemiBold) -> QFont:
    f = QFont()
    f.setFamilies(_DISPLAY_FAMILIES)
    f.setPointSize(size)
    f.setWeight(weight)
    return f


_ICON_FONT: str | None = None


def icon_font(size: int = 10) -> QFont:
    global _ICON_FONT
    if _ICON_FONT is None:
        families = set(QFontDatabase.families())
        for name in ("Segoe Fluent Icons", "Segoe MDL2 Assets"):
            if name in families:
                _ICON_FONT = name
                break
        else:
            _ICON_FONT = ""
    f = QFont(_ICON_FONT or "Segoe UI")
    f.setPointSize(size)
    return f


def icon_label(glyph: str, size: int = 10, color: QColor | None = None) -> QLabel:
    lb = QLabel(glyph)
    lb.setFont(icon_font(size))
    lb.setStyleSheet(f"color: {color.name() if color else C.TEXT.name()}; background: transparent;")
    from PySide6.QtCore import Qt as _Qt
    lb.setAttribute(_Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    return lb


# Fluent 图标码位
ICON = {
    "refresh": "\uE72C",
    "settings": "\uE713",
    "back": "\uE72B",
    "add": "\uE710",
    "delete": "\uE74D",
    "edit": "\uE70F",
    "check": "\uE73E",
    "close": "\uE711",
    "bolt": "\uE945",
    "chevron": "\uE76C",
    "down": "\uE70D",
    "up": "\uE70E",
    "more": "\uE712",
    "info": "\uE946",
    "link": "\uE71B",
    "device": "\uE7F4",
    "upload": "\uE898",
    "image": "\uE8B9",
    "chart": "\uE9D2",
    "history": "\uE81C",
    "pin": "\uE718",
    "terminal": "\uE756",
    "copy": "\uE8C8",
    "folder": "\uE838",
    "brush": "\uE790",
    "preview": "\uE890",
}


def tint(color: QColor, alpha: int) -> QColor:
    """同色不同透明度（用于瓦片底、胶囊底、悬停态）。"""
    c = QColor(color)
    c.setAlpha(max(0, min(255, alpha)))
    return c


def mix(a: QColor, b: QColor, t: float) -> QColor:
    """按进度 t（0..1）在两色之间插值，含 alpha——悬停/按压的渐变都靠它。"""
    t = max(0.0, min(1.0, float(t)))
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
        round(a.alpha() + (b.alpha() - a.alpha()) * t),
    )


def rgba(color: QColor) -> str:
    """QColor → `rgba(r,g,b,a)` 字符串，供样式表用。

    注意 alpha 必须写 0..255 的整数：Qt 样式表不认 0..1 的浮点透明度，
    写成 0.588 会被当成无效值、整条背景规则退化成不透明，材质就透不出来。
    """
    return (f"rgba({color.red()},{color.green()},{color.blue()},"
            f"{max(0, min(255, color.alpha()))})")


# --------------------------------------------------------------------------- #
# 窗口材质（Mica / Acrylic）下的底色
# 系统材质只会透出「窗口底色」的透明度，所以底色越透材质越明显；
# Acrylic 的壁纸色彩更强，压一层更实的遮罩，保证正文对比度不掉。
_SCRIM = {"solid": (249, 249, 249, 255), "mica": (249, 249, 249, 132),
          "acrylic": (250, 250, 250, 158)}
_NAV_SCRIM = {"solid": (243, 243, 243, 255), "mica": (243, 243, 243, 108),
              "acrylic": (243, 243, 243, 132)}
#: 卡片与弹出面板的底色透明度：材质开启时略透，让壁纸色透出来一点
_CARD_ALPHA = {"solid": 246, "mica": 234, "acrylic": 216}


def scrim(material: str) -> QColor:
    r, g, b, a = _SCRIM.get(material, _SCRIM["solid"])
    return QColor(r, g, b, a)


def nav_scrim(material: str) -> QColor:
    r, g, b, a = _NAV_SCRIM.get(material, _NAV_SCRIM["solid"])
    return QColor(r, g, b, a)


def card_alpha(material: str) -> int:
    return _CARD_ALPHA.get(material, _CARD_ALPHA["solid"])


def panel_fill(material: str, opacity: int = 100) -> QColor:
    """弹出面板的底色：材质基准 alpha × 用户设定的不透明度。

    面板本体与设置页里的实时预览都走这一个函数，避免「预览和实际不一样」。
    `opacity` 是 0..100 的百分比；低于 30% 时文字会糊在桌面内容里，
    范围限制由 `motion.OPACITY_MIN` 负责，这里只做兜底夹取。
    """
    ratio = max(0, min(100, int(opacity))) / 100
    alpha = max(1, min(255, round(card_alpha(material) * ratio)))
    return tint(C.WINDOW, alpha)


def apply_shadow_color(widget: QWidget, color: QColor) -> None:
    widget.setStyleSheet(widget.styleSheet() + f"color: {color.name()};")


# 通用输入控件样式（设置页、对话框共用）
INPUT_QSS = """
QLineEdit, QComboBox, QPlainTextEdit {
    background: rgba(255,255,255,235);
    border: 1px solid rgba(0,0,0,32);
    border-bottom: 1px solid rgba(0,0,0,70);
    border-radius: 5px;
    padding: 4px 8px;
    color: #1A1A1A;
    selection-background-color: #0067C0;
}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {
    border-bottom: 1px solid #0067C0;
    background: #FFFFFF;
}
QComboBox::drop-down { border: none; width: 18px; }
QComboBox QAbstractItemView {
    background: #FFFFFF; border: 1px solid rgba(0,0,0,30); border-radius: 6px;
    selection-background-color: rgba(0,103,192,25); color: #1A1A1A; outline: none;
}
"""
