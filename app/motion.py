"""Fluent 动效与窗口材质：全局偏好 + 可中断补间 + 淡入淡出。

设计约束（和这个项目的其他部分保持一致）：
- 一切时长都过 `duration()`，`关闭` 档返回 0，调用方据此直接落到终态，
  保证「关掉动效」时不会残留半透明或停在中间状态；
- 补间用 `QVariantAnimation` 而不是 `Property`：本项目的控件大量用
  `paintEvent` 自绘，没有可动画的 Qt 属性，改值 + `update()` 最省事；
- 悬停用 0..1 的进度值，进出可以中途反向，不会像布尔开关那样跳。

偏好写在 `config["ui"]`：
    material        "solid" | "mica" | "acrylic"
    motion          "full" | "subtle" | "off"
    hover           bool   悬停/按压反馈
    transitions     bool   窗口与页面过渡
    panel_opacity   int    电量面板不透明度（30..100，百分比）
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QObject, QVariantAnimation
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget

MATERIALS = ("solid", "mica", "acrylic")
LEVELS = ("full", "subtle", "off")

#: 电量面板不透明度的可调范围（百分比）。下限 30% 是实测能保住文字对比度的边界，
#: 再低面板会与桌面内容糊在一起，「电量」这个主信息就看不出来了。
OPACITY_MIN = 30
OPACITY_MAX = 100

#: 与 WinUI 的 Fluent motion token 对齐：fast 150 / normal 250 / gentle 350
DEFAULTS: Dict[str, Any] = {
    "material": "mica",
    "motion": "full",
    "hover": True,
    "transitions": True,
    "panel_opacity": OPACITY_MAX,
}

_SCALE = {"full": 1.0, "subtle": 0.55, "off": 0.0}

_CURVES = {
    # FluentDecelerate (0.1, 0.9, 0.2, 1)：进场、元素出现
    "decelerate": QEasingCurve.Type.OutQuart,
    # FluentAccelerate (0.7, 0, 1, 0.5)：退场、元素消失
    "accelerate": QEasingCurve.Type.InCubic,
    # FluentStandard (0.8, 0, 0.2, 1)：状态切换、位移
    "standard": QEasingCurve.Type.InOutCubic,
}

_STATE: Dict[str, Any] = dict(DEFAULTS)


# --------------------------------------------------------------------------- #
# 偏好
def defaults() -> Dict[str, Any]:
    return dict(DEFAULTS)


def configure(ui: Any) -> Dict[str, Any]:
    """把配置里的 `ui` 段读进全局状态；非法值一律回落默认，绝不抛错。"""
    ui = ui if isinstance(ui, dict) else {}
    material = ui.get("material")
    level = ui.get("motion")
    _STATE["material"] = material if material in MATERIALS else DEFAULTS["material"]
    _STATE["motion"] = level if level in LEVELS else DEFAULTS["motion"]
    _STATE["hover"] = bool(ui.get("hover", DEFAULTS["hover"]))
    _STATE["transitions"] = bool(ui.get("transitions", DEFAULTS["transitions"]))
    _STATE["panel_opacity"] = clamp_opacity(
        ui.get("panel_opacity", DEFAULTS["panel_opacity"]))
    return dict(_STATE)


def clamp_opacity(value: Any) -> int:
    """把任意输入夹到 30..100；空值/非数字回落默认，供配置与滑块共用。"""
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return DEFAULTS["panel_opacity"]
    return max(OPACITY_MIN, min(OPACITY_MAX, number))


def state() -> Dict[str, Any]:
    return dict(_STATE)


def material() -> str:
    return str(_STATE["material"])


def level() -> str:
    return str(_STATE["motion"])


def hover_enabled() -> bool:
    return bool(_STATE["hover"])


def transitions_enabled() -> bool:
    """窗口/页面级过渡：既要开关打开，也要动效档位不是「关闭」。"""
    return bool(_STATE["transitions"]) and duration(1) > 0


def panel_opacity() -> int:
    """电量面板不透明度（30..100 的百分比）。"""
    return int(_STATE["panel_opacity"])


def set_panel_opacity(value: Any) -> int:
    """运行时改面板不透明度（只动内存，不落盘）。

    滑块拖动时用它：界面与预览立刻跟着变，写盘交给调用方做防抖，
    免得一次拖动把配置写上百遍。
    """
    _STATE["panel_opacity"] = clamp_opacity(value)
    return int(_STATE["panel_opacity"])


def scale() -> float:
    return _SCALE.get(str(_STATE["motion"]), 1.0)


def duration(base: int) -> int:
    """按档位折算时长：标准 ×1、柔和 ×0.55、关闭 = 0。"""
    factor = scale()
    if factor <= 0:
        return 0
    return max(1, round(base * factor))


def easing(kind: str = "decelerate") -> QEasingCurve:
    return QEasingCurve(_CURVES.get(kind, _CURVES["decelerate"]))


# --------------------------------------------------------------------------- #
# 补间
def animate(owner: QObject, start, end, base: int, apply: Callable[[Any], None],
            kind: str = "decelerate", done: Optional[Callable[[], None]] = None):
    """一次性补间，返回动画对象（关闭动效时返回 None，并直接落到终值）。

    `apply` 每帧收到插值后的值，调用方自己 `update()`。
    """
    ms = duration(base)
    if ms <= 0:
        apply(end)
        if done:
            done()
        return None
    anim = QVariantAnimation(owner)
    anim.setStartValue(start)
    anim.setEndValue(end)
    anim.setDuration(ms)
    anim.setEasingCurve(easing(kind))
    anim.valueChanged.connect(lambda value: apply(value))
    if done:
        anim.finished.connect(lambda: done())
    anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
    return anim


def is_alive(obj) -> bool:
    """PySide6 对象是否还有效。

    `start(DeleteWhenStopped)` 之后动画会被 C++ 侧删掉，Python 包装还在，
    再调 `.stop()` 就抛 RuntimeError；存了引用的地方先问一句更稳。
    """
    if obj is None:
        return False
    try:
        from shiboken6 import isValid

        return bool(isValid(obj))
    except Exception:                          # noqa: BLE001
        return True


class Tween(QObject):
    """可中途反向的单值补间（悬停、按压、电量条这类会来回变的值）。"""

    def __init__(self, owner: QObject, apply: Callable[[float], None],
                 base: int = 120, kind: str = "decelerate", value: float = 0.0):
        super().__init__(owner)
        self._apply = apply
        self._base = base
        self._kind = kind
        self._value = float(value)
        self._anim = QVariantAnimation(self)
        self._anim.valueChanged.connect(self._tick)
        self._anim.finished.connect(self._settle)

    # ------------------------------------------------------------------ #
    def value(self) -> float:
        return self._value

    def set(self, value: float):
        """立即落到某个值（不带动画）。"""
        self._anim.stop()
        self._value = float(value)
        self._apply(self._value)

    def to(self, target: float, base: Optional[int] = None, kind: Optional[str] = None):
        target = float(target)
        if abs(target - self._value) < 1e-4:
            return
        ms = duration(base if base is not None else self._base)
        if ms <= 0:
            self.set(target)
            return
        self._anim.stop()
        self._anim.setStartValue(self._value)
        self._anim.setEndValue(target)
        self._anim.setDuration(ms)
        self._anim.setEasingCurve(easing(kind or self._kind))
        self._anim.start()

    def _tick(self, value):
        self._value = float(value)
        self._apply(self._value)

    def _settle(self):
        self._value = float(self._anim.endValue())
        self._apply(self._value)


# --------------------------------------------------------------------------- #
# 淡入淡出
def _clear_effect(widget: QWidget):
    if widget.graphicsEffect() is not None:
        widget.setGraphicsEffect(None)             # Qt 会顺手删掉旧 effect


def fade_in(widget: QWidget, base: int = 180, start: float = 0.0,
            kind: str = "decelerate"):
    """让控件从 `start` 透明度淡入；结束就摘掉 effect，避免长期离屏渲染。

    淡出没有同名助手：目前唯一的淡出是托盘面板整窗透明度（在 `ui.Flyout` 里
    用 windowOpacity 做），控件级淡出没有真实用途，不提前留在公共模块里。
    """
    if widget.graphicsEffect() is not None:        # 已有特效（阴影等）就不掺和
        return
    if duration(base) <= 0:
        widget.show()
        return
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(max(0.0, min(1.0, start)))
    widget.setGraphicsEffect(effect)
    widget.show()
    animate(widget, max(0.0, min(1.0, start)), 1.0, base,
            effect.setOpacity, kind=kind,
            done=lambda w=widget: _clear_effect(w))
