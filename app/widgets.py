"""Fluent 风格基础控件。

动效约定：悬停/按压都是 0..1 的进度补间（`motion.Tween`），自绘时用
`theme.mix()` 在两色间插值；动效关掉时 `Tween.to()` 直接落到终值，
所以布局和可读性永远不会依赖动画跑完。
"""
from __future__ import annotations

from PySide6.QtCore import (QAbstractAnimation, QEasingCurve, Property, QPointF, QPropertyAnimation,
                            QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal)
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractButton, QHBoxLayout, QLabel, QSlider,
                               QSizePolicy, QVBoxLayout, QWidget)

from . import i18n, motion, theme


class Surface(QWidget):
    """圆角面板（弹出层、卡片共用）。"""

    def __init__(self, parent=None, radius: int = 10, fill: QColor | None = None,
                 border: QColor | None = None):
        super().__init__(parent)
        self._radius = radius
        self._fill = QColor(fill or theme.C.WINDOW)
        self._border = border or theme.C.STROKE
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def set_fill(self, color: QColor):
        """换底色（窗口材质切换时用：越透，系统的 Mica / Acrylic 越明显）。"""
        self._fill = QColor(color)
        self.update()

    def fill(self) -> QColor:
        return QColor(self._fill)

    def paintEvent(self, event):                   # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, self._radius, self._radius)
        p.fillPath(path, self._fill)
        p.setPen(QPen(self._border, 1))
        p.drawPath(path)


class IconTile(QWidget):
    """模块标识瓦片：圆角浅色底 + 单色图标，用来把相邻卡片区分开。"""

    def __init__(self, glyph: str, color: QColor | None = None, size: int = 30,
                 radius: int = 9, parent=None):
        super().__init__(parent)
        self._glyph = theme.ICON.get(glyph, glyph)
        self._color = color or theme.C.ACCENT
        self._radius = radius
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event):                   # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(theme.tint(self._color, 40), 1))
        p.setBrush(theme.tint(self._color, 20))
        p.drawRoundedRect(r, self._radius, self._radius)
        p.setPen(QPen(self._color))
        p.setFont(theme.icon_font(max(9, int(self.height() * 0.36))))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._glyph)


class Badge(QWidget):
    """状态胶囊：小圆点 + 短文字，用于「已启用 / 尚未调试 / 导入成功」。"""

    TONES = {"neutral": theme.C.TEXT_SECONDARY, "accent": theme.C.ACCENT,
             "ok": theme.C.OK, "warn": theme.C.WARN, "danger": theme.C.DANGER}

    def __init__(self, text: str = "", tone: str = "neutral", parent=None):
        super().__init__(parent)
        self._text = text
        self._tone = tone
        self._color = self.TONES.get(tone, theme.C.TEXT_SECONDARY)
        self.setFont(theme.ui_font(7))
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(20)

    # ------------------------------------------------------------------ #
    def text(self) -> str:
        return self._text

    def setText(self, text: str, tone: str | None = None):   # noqa: N802
        self._text = text
        if tone is not None:
            self.set_tone(tone)
        self.updateGeometry()
        self.update()

    def set_tone(self, tone: str):
        self._tone = tone
        self._color = self.TONES.get(tone, theme.C.TEXT_SECONDARY)
        self.update()

    def sizeHint(self):                            # noqa: N802
        fm = self.fontMetrics()
        return QSize(18 + 5 + 6 + fm.horizontalAdvance(self._text) + 9, 20)

    def minimumSizeHint(self):                     # noqa: N802
        return self.sizeHint()

    def paintEvent(self, event):                   # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        soft = (QColor(0, 0, 0, 14) if self._tone == "neutral"
                else theme.tint(self._color, 24))
        p.setPen(QPen(theme.tint(self._color, 46) if self._tone != "neutral"
                      else QColor(0, 0, 0, 24), 1))
        p.setBrush(soft)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._color)
        p.drawEllipse(QRectF(r.left() + 9, r.center().y() - 2.0, 4, 4))
        p.setPen(QPen(self._color))
        p.drawText(QRectF(r.left() + 18, r.top(), r.width() - 24, r.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._text)


class ListRow(QWidget):
    """卡片内的一条列表项：白底 + 细边框 + 悬停反馈（比纯分隔线更好辨形）。"""

    def __init__(self, parent=None, radius: int = 8):
        super().__init__(parent)
        self._radius = radius
        self._hover = 0.0
        self._dimmed = False
        self._hover_tween = motion.Tween(self, self._set_hover, base=120)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def _set_hover(self, value: float):
        self._hover = value
        self.update()

    def set_dimmed(self, dimmed: bool):
        """停用的接口整行压暗，和启用中的行一眼能分开。"""
        self._dimmed = bool(dimmed)
        self.update()

    def enterEvent(self, event):                   # noqa: N802
        self._hover_tween.to(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):                   # noqa: N802
        self._hover_tween.to(0.0)
        super().leaveEvent(event)

    def paintEvent(self, event):                   # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self._dimmed:
            fill, stroke = QColor(0, 0, 0, 4), QColor(0, 0, 0, 14)
        else:
            fill = theme.mix(QColor(255, 255, 255, 255), QColor(246, 246, 246, 255),
                             self._hover)
            stroke = theme.mix(QColor(0, 0, 0, 28), QColor(0, 0, 0, 58), self._hover)
        p.setPen(QPen(stroke, 1))
        p.setBrush(fill)
        p.drawRoundedRect(r, self._radius, self._radius)


class Banner(QWidget):
    """内联状态条：底色 + 描边 + 图标 + 说明文字（成功 / 失败 / 提示共用）。"""

    def __init__(self, parent=None, tone: str = "info"):
        super().__init__(parent)
        self._tone = tone
        self._color = Badge.TONES.get(tone, theme.C.ACCENT)
        self._glyph = "\uE946"
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(11, 8, 11, 8)
        lay.setSpacing(8)
        self.icon = QLabel()
        self.icon.setFont(theme.icon_font(8))
        self.icon.setFixedWidth(14)
        self.icon.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self.icon)

        self.label = QLabel("")
        self.label.setFont(theme.ui_font(7))
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.label, 1)
        self._apply_tone()

    def _apply_tone(self):
        self._color = Badge.TONES.get(self._tone, theme.C.ACCENT)
        self._glyph = {"ok": theme.ICON["check"], "danger": theme.ICON["info"]}.get(
            self._tone, theme.ICON["info"])
        self.icon.setText(self._glyph)
        self.icon.setStyleSheet(f"color: {self._color.name()}; background: transparent;")
        self.label.setStyleSheet(f"color: {self._color.name()}; background: transparent;")
        self.update()

    # ------------------------------------------------------------------ #
    def set_message(self, text: str, tone: str = "info"):
        was_hidden = not self.isVisible()
        self._tone = tone
        self.label.setText(text)
        self._apply_tone()
        self.show()
        if was_hidden:                             # 从无到有：淡入，别硬闪一下
            motion.fade_in(self, base=170, start=0.0)

    def text(self) -> str:
        return self.label.text()

    def clear(self):
        self.label.clear()
        self.hide()

    def paintEvent(self, event):                   # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(theme.tint(self._color, 52), 1))
        p.setBrush(theme.tint(self._color, 16))
        p.drawRoundedRect(r, 6, 6)


class HoverFrame(QWidget):
    """带悬停高亮的可点击卡片。"""

    clicked = Signal()

    def __init__(self, parent=None, radius: int = 8):
        super().__init__(parent)
        self._radius = radius
        self._hover = 0.0
        self._hover_tween = motion.Tween(self, self._set_hover, base=120)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _set_hover(self, value: float):
        self._hover = value
        self.update()

    def enterEvent(self, event):                   # noqa: N802
        self._hover_tween.to(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):                   # noqa: N802
        self._hover_tween.to(0.0)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):            # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):                   # noqa: N802
        if self._hover > 0.004:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.mix(QColor(0, 0, 0, 0), theme.C.SURFACE_HOVER, self._hover))
            p.drawRoundedRect(QRectF(self.rect()), self._radius, self._radius)


class ElidedLabel(QLabel):
    """宽度不足时自动省略，避免撑破布局。"""

    def __init__(self, text: str = "", parent=None, mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight):
        super().__init__(parent)
        self._full = text
        self._mode = mode
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def setText(self, text: str):                  # noqa: N802
        self._full = text
        self._refresh()

    def minimumSizeHint(self):                     # noqa: N802
        return QSize(0, super().minimumSizeHint().height())

    def sizeHint(self):                            # noqa: N802
        hint = super().sizeHint()
        return QSize(min(hint.width(), 160), hint.height())

    def fullText(self) -> str:                     # noqa: N802
        return self._full

    def resizeEvent(self, event):                  # noqa: N802
        self._refresh()
        super().resizeEvent(event)

    def _refresh(self):
        fm = self.fontMetrics()
        width = max(0, self.width() - 2)
        if width <= 0:
            super().setText(self._full)
        else:
            super().setText(fm.elidedText(self._full, self._mode, width))


class _SpinGlyph:
    """给「图标按钮」加旋转反馈：转起来表示后台正在读取，读完平滑收尾。

    不引入 Property，直接用 QVariantAnimation 改角度（更少坑），
    最少转 `MIN_MS` 毫秒，避免扫描很快时动画一闪而过反而看不见。
    """

    MIN_MS = 560
    TURN_MS = 720

    def _init_spin(self):
        self._angle = 0.0
        self._want_stop = False
        self._tail = None
        self._spin = QVariantAnimation(self)
        self._spin.setStartValue(0.0)
        self._spin.setEndValue(360.0)
        self._spin.setDuration(self.TURN_MS)
        self._spin.setLoopCount(-1)
        self._spin.setEasingCurve(QEasingCurve.Type.Linear)
        self._spin.valueChanged.connect(self._on_spin_tick)
        self._spin_min = QTimer(self)
        self._spin_min.setSingleShot(True)
        self._spin_min.setInterval(self.MIN_MS)
        self._spin_min.timeout.connect(self._on_spin_min)

    def _on_spin_tick(self, value):
        self._angle = float(value)
        self.update()

    def _on_spin_min(self):
        if self._want_stop:
            self._end_spin()

    def is_spinning(self) -> bool:
        return self._spin.state() == QAbstractAnimation.State.Running

    def start_spin(self):
        self._want_stop = False
        if self.is_spinning():
            return
        if self._tail is not None:
            self._tail.stop()
        self._angle = 0.0
        self._spin.start()
        self._spin_min.start()

    def stop_spin(self):
        if not self.is_spinning():
            if self._angle:
                self._angle = 0.0
                self.update()
            return
        self._want_stop = True
        if not self._spin_min.isActive():
            self._end_spin()

    def _end_spin(self):
        self._spin.stop()
        self._spin_min.stop()
        self._want_stop = False
        rest = (360.0 - self._angle) % 360.0
        if rest < 1.0:
            self._angle = 0.0
            self.update()
            return
        tail = QVariantAnimation(self)
        tail.setStartValue(self._angle)
        tail.setEndValue(360.0)
        tail.setDuration(max(90, int(200 * rest / 360.0)))
        tail.setEasingCurve(QEasingCurve.Type.OutCubic)
        tail.valueChanged.connect(self._on_spin_tick)
        tail.finished.connect(self._on_spin_tail_done)
        tail.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        self._tail = tail

    def _on_spin_tail_done(self):
        self._tail = None
        self._angle = 0.0
        self.update()

    def _apply_spin(self, p: QPainter, center: QRectF | None = None):
        """把画笔绕中心（或指定矩形中心）旋转当前角度。"""
        if not self._angle:
            return
        c = (center or QRectF(self.rect())).center()
        p.translate(c)
        p.rotate(self._angle)
        p.translate(-c)


class IconButton(_SpinGlyph, QAbstractButton):
    def __init__(self, glyph: str, parent=None, size: int = 30, icon_size: int = 10,
                 color: QColor | None = None, tooltip: str = "", spin: bool = False):
        super().__init__(parent)
        self._glyph = glyph
        self._icon_size = icon_size
        self._color = color or theme.C.TEXT_SECONDARY
        self._hover = 0.0
        self._press = 0.0
        self._hover_tween = motion.Tween(self, self._set_hover, base=120)
        self._press_tween = motion.Tween(self, self._set_press, base=90)
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.pressed.connect(lambda: self._press_tween.to(1.0))
        self.released.connect(lambda: self._press_tween.to(0.0))
        if spin:
            self._init_spin()

    def _set_hover(self, value: float):
        self._hover = value
        self.update()

    def _set_press(self, value: float):
        self._press = value
        self.update()

    def sizeHint(self):                            # noqa: N802
        return QSize(self.width(), self.height())

    def enterEvent(self, event):                   # noqa: N802
        self._hover_tween.to(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):                   # noqa: N802
        self._hover_tween.to(0.0)
        super().leaveEvent(event)

    def paintEvent(self, event):                   # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        active = max(self._hover, self._press)
        if active > 0.004:
            p.setPen(Qt.PenStyle.NoPen)
            bg = theme.mix(QColor(0, 0, 0, 0), theme.C.SURFACE_HOVER, self._hover)
            p.setBrush(theme.mix(bg, QColor(0, 0, 0, 26), self._press))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 6, 6)
        color = theme.mix(self._color, theme.C.TEXT, active)
        if not self.isEnabled():
            color = theme.tint(color, 90)
        if hasattr(self, "_spin") and self._angle:
            # 转圈时换成「开口圆环」：圆形箭头本身近似中心对称，只转头很难看出在动；
            # 圆环的开口/收尾角度一转就很明显，是明确的「正在读取」反馈。
            p.save()
            c = QRectF(self.rect()).center()
            p.translate(c)
            p.rotate(self._angle)
            d = min(self.width(), self.height()) * 0.52   # 与 9pt 图标的视觉大小相当
            ring = QRectF(-d / 2, -d / 2, d, d)
            pen = QPen(color, 1.5)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(ring, 100 * 16, 280 * 16)
            p.restore()
            return
        p.setFont(theme.icon_font(self._icon_size))
        p.setPen(QPen(color))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._glyph)


class BatteryBar(QWidget):
    def __init__(self, parent=None, width: int = 64, height: int = 4):
        super().__init__(parent)
        self.setFixedSize(width, height)
        self._percent: int | None = None
        self._charging = False
        self._ratio = 0.0
        self._ratio_tween = motion.Tween(self, self._set_ratio, base=280)

    def _set_ratio(self, value: float):
        self._ratio = value
        self.update()

    def set_state(self, percent: int | None, charging: bool = False):
        self._percent = percent
        self._charging = charging
        target = 0.0 if percent is None else max(0.0, min(1.0, percent / 100.0))
        if self._ratio_tween.value() == 0.0 and self._ratio == 0.0:
            self._ratio_tween.set(target)          # 首次出现不做过场，直接显示真实值
        else:
            self._ratio_tween.to(target)
        self.update()

    def paintEvent(self, event):                   # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.C.TRACK)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        if self._ratio > 0.004:
            ratio = max(0.04, self._ratio)
            color = theme.C.ACCENT if self._charging else theme.C.TEXT
            if self._percent is not None and self._percent <= 15 and not self._charging:
                color = theme.C.DANGER
            fill = QRectF(r.left(), r.top(), r.width() * ratio, r.height())
            p.setBrush(color)
            p.drawRoundedRect(fill, r.height() / 2, r.height() / 2)


class DeviceRow(QWidget):
    """设备行：图标（仅用户上传过才显示）+ 名称 + 连接方式 + 电量。"""

    clicked = Signal(dict)

    def __init__(self, device: dict, parent=None):
        super().__init__(parent)
        self.device = device
        self.setFixedHeight(64)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # 不要给设备行（及其子控件）设 tooltip：Qt 会把子控件的 ToolTip 事件冒泡到父控件，
        # 鼠标停在名称/电量这些 QLabel 上也会弹出一整块系统样式的黑框，面板里不需要。
        self.setToolTip("")
        self._hover = 0.0
        self._hover_tween = motion.Tween(self, self._set_hover, base=110)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(10)

        # 图标：只有用户自己上传过才显示，没有就不占位、不绘制任何剪影
        self.icon = None
        path = device.get("icon_path")
        if path:
            from PySide6.QtGui import QPixmap
            raw = QPixmap(path)
            if not raw.isNull():
                self.icon = QLabel()
                self.icon.setFixedSize(40, 40)
                self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self.icon.setPixmap(raw.scaled(40, 40, Qt.AspectRatioMode.KeepAspectRatio,
                                               Qt.TransformationMode.SmoothTransformation))
                self.icon.setStyleSheet("background: transparent;")
                lay.addWidget(self.icon)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        self.name = ElidedLabel(device.get("name") or device.get("product")
                                or i18n.T("未知设备"))
        self.name.setFont(theme.ui_font(10, QFont.Weight.DemiBold))
        self.name.setStyleSheet(f"color: {theme.C.TEXT.name()};")
        self.sub = ElidedLabel(self._subtitle(device))
        self.sub.setFont(theme.ui_font(8))
        self.sub.setStyleSheet(f"color: {theme.C.TEXT_TERTIARY.name()};")
        col.addWidget(self.name)
        col.addWidget(self.sub)
        lay.addLayout(col, 1)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(5)
        right.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        percent = device.get("percent")
        self.percent = QLabel("--" if percent is None else f"{percent}%")
        self.percent.setFont(theme.ui_font(11, QFont.Weight.DemiBold))
        self.percent.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.percent.setStyleSheet(f"color: {self._percent_color(percent)};")
        right.addWidget(self.percent)

        self.bar = BatteryBar(width=52, height=4)
        self.bar.set_state(percent, bool(device.get("charging")))
        right.addWidget(self.bar, 0, Qt.AlignmentFlag.AlignRight)
        lay.addLayout(right)

    @staticmethod
    def _percent_color(percent) -> str:
        if percent is None:
            return theme.C.TEXT_TERTIARY.name()
        if percent <= 15:
            return theme.C.DANGER.name()
        return theme.C.TEXT.name()

    @staticmethod
    def _subtitle(device: dict) -> str:
        conn = {1: i18n.T("有线 USB"), 2: i18n.T("2.4G 无线"),
                3: i18n.T("蓝牙")}.get(device.get("connection"), i18n.T("连接未知"))
        parts = [conn]
        if device.get("charging"):
            parts.append(i18n.T("充电中"))
        if device.get("protocol"):
            parts.append(i18n.TF("协议 {}", device['protocol']))
        return " · ".join(parts)

    def mouseReleaseEvent(self, event):             # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit(self.device)
        super().mouseReleaseEvent(event)

    def _set_hover(self, value: float):
        self._hover = value
        self.update()

    def enterEvent(self, event):                   # noqa: N802
        self._hover_tween.to(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):                   # noqa: N802
        self._hover_tween.to(0.0)
        super().leaveEvent(event)

    def paintEvent(self, event):                   # noqa: N802
        if self._hover > 0.004:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.mix(QColor(0, 0, 0, 0), theme.C.SURFACE_HOVER, self._hover))
            p.drawRoundedRect(QRectF(self.rect()), 8, 8)


class ToggleSwitch(QAbstractButton):
    def __init__(self, checked: bool = True, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(40, 22)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._pos = 1.0 if checked else 0.0
        self._hover = 0.0
        self._press = 0.0
        self._anim = QPropertyAnimation(self, b"handle", self)
        self._anim.setDuration(motion.duration(180))
        self._anim.setEasingCurve(motion.easing("decelerate"))
        self._hover_tween = motion.Tween(self, self._set_hover, base=120)
        self._press_tween = motion.Tween(self, self._set_press, base=90)
        self.pressed.connect(lambda: self._press_tween.to(1.0))
        self.released.connect(lambda: self._press_tween.to(0.0))
        self.toggled.connect(self._on_toggle)

    def _set_hover(self, value: float):
        self._hover = value
        self.update()

    def _set_press(self, value: float):
        self._press = value
        self.update()

    def _on_toggle(self, checked: bool):
        self._anim.stop()
        self._anim.setDuration(motion.duration(180))
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def _get_handle(self):
        return self._pos

    def _set_handle(self, value: float):
        self._pos = value
        self.update()

    handle = Property(float, _get_handle, _set_handle)

    def enterEvent(self, event):                   # noqa: N802
        self._hover_tween.to(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):                   # noqa: N802
        self._hover_tween.to(0.0)
        super().leaveEvent(event)

    def paintEvent(self, event):                   # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(1, 2, -1, -2)
        # 关闭→打开时轨道颜色跟着滑块一起渐变，不是「啪」一下变蓝
        fill = theme.mix(QColor(0, 0, 0, 12), theme.C.ACCENT, self._pos)
        stroke_off = theme.mix(QColor(0, 0, 0, 55), QColor(0, 0, 0, 85), self._hover)
        stroke = theme.mix(stroke_off, theme.C.ACCENT, self._pos)
        p.setPen(QPen(stroke, 1))
        p.setBrush(fill)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        # 按压时滑块轻微拉长（WinUI 开关的经典反馈），松手弹回
        d = r.height() - 4
        w = d + 4.0 * self._press
        x = r.left() + 2 + (r.width() - d - 4) * self._pos - 2.0 * self._press * self._pos
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.mix(QColor(90, 90, 90), QColor(255, 255, 255), self._pos))
        p.drawEllipse(QRectF(x, r.top() + 2, w, d))


class FlatButton(_SpinGlyph, QAbstractButton):
    """Fluent 次要按钮 / 强调按钮。"""

    def __init__(self, text: str, parent=None, primary: bool = False, glyph: str = "",
                 spin: bool = False):
        super().__init__(parent)
        self.setText(text)
        self._primary = primary
        self._glyph = glyph
        self._hover = 0.0
        self._press = 0.0
        self._hover_tween = motion.Tween(self, self._set_hover, base=130)
        self._press_tween = motion.Tween(self, self._set_press, base=90)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setMinimumHeight(32)
        self.setFont(theme.ui_font(9))
        self.pressed.connect(lambda: self._press_tween.to(1.0))
        self.released.connect(lambda: self._press_tween.to(0.0))
        if spin and glyph:
            self._init_spin()

    def _set_hover(self, value: float):
        self._hover = value
        self.update()

    def _set_press(self, value: float):
        self._press = value
        self.update()

    def sizeHint(self):                            # noqa: N802
        width = self.fontMetrics().horizontalAdvance(self.text()) + 26
        if self._glyph:
            width += 18
        return QSize(max(78, width), 32)

    def enterEvent(self, event):                   # noqa: N802
        self._hover_tween.to(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):                   # noqa: N802
        self._hover_tween.to(0.0)
        super().leaveEvent(event)

    def paintEvent(self, event):                   # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        enabled = self.isEnabled()
        active = max(self._hover, self._press)
        if self._primary:
            bg = theme.mix(QColor(theme.C.TEXT), QColor(48, 48, 48), self._hover)
            bg = theme.mix(bg, QColor(0, 0, 0), self._press)
            fg = QColor(255, 255, 255)
            border = QColor(0, 0, 0, 0)
        else:
            bg = theme.mix(QColor(0, 0, 0, 0), QColor(255, 255, 255, 255), self._hover)
            bg = theme.mix(bg, QColor(0, 0, 0, 18), self._press)
            fg = theme.mix(theme.C.TEXT_SECONDARY, theme.C.TEXT, active)
            border = theme.mix(theme.C.STROKE, QColor(0, 0, 0, 46), self._press)
        if not enabled:                            # 禁用：整体压淡，不再响应悬停
            fg = theme.tint(fg, 90)
            bg = theme.tint(bg, 120) if bg.alpha() else bg
            border = theme.C.STROKE
        p.setPen(QPen(border, 1))
        p.setBrush(bg)
        p.drawRoundedRect(r, 6, 6)

        fm = p.fontMetrics()
        text = self.text()
        gw = 0
        if self._glyph:
            p.setFont(theme.icon_font(9))
            gw = 18
        total = fm.horizontalAdvance(text) + gw
        start = r.center().x() - total / 2
        if self._glyph:
            box = QRectF(start, r.top(), 14, r.height())
            if hasattr(self, "_spin") and self._angle:
                # 与图标按钮一致：读取时改画旋转的开口圆环，动感明显
                p.save()
                p.translate(box.center())
                p.rotate(self._angle)
                pen = QPen(fg, 1.5)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawArc(QRectF(-6, -6, 12, 12), 100 * 16, 280 * 16)
                p.restore()
            else:
                p.setPen(fg)
                p.drawText(box, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                           self._glyph)
        p.setFont(self.font())
        p.setPen(fg)
        p.drawText(QRectF(start + gw, r.top(), total, r.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)


class DragArea(QWidget):
    """可拖拽区域：按住空白处可移动整个弹出面板（标题栏用法）。

    子控件（按钮、标签）自己处理鼠标事件，只有空白处的事件会走到这里，
    因此不需要给每个控件单独装事件过滤器。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._offset = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

    def mousePressEvent(self, event):              # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            win = self.window()
            self._offset = event.globalPosition().toPoint() - win.frameGeometry().topLeft()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):               # noqa: N802
        if self._offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):            # noqa: N802
        if self._offset is not None:
            self._offset = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            win = self.window()
            hook = getattr(win, "on_user_moved", None)
            if callable(hook):
                hook()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _SegButton(QAbstractButton):
    """分段控件里的一段：只画文字，选中底板由 Segmented 统一滑动绘制。"""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(theme.ui_font(8))
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._on = 0.0
        self._hover = 0.0
        self._on_tween = motion.Tween(self, self._set_on, base=180)
        self._hover_tween = motion.Tween(self, self._set_hover, base=120)

    def _set_on(self, value: float):
        self._on = value
        self.update()

    def _set_hover(self, value: float):
        self._hover = value
        self.update()

    def setChecked(self, checked: bool):           # noqa: N802
        super().setChecked(checked)
        self._on_tween.to(1.0 if checked else 0.0)

    def enterEvent(self, event):                   # noqa: N802
        self._hover_tween.to(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):                   # noqa: N802
        self._hover_tween.to(0.0)
        super().leaveEvent(event)

    def sizeHint(self):                            # noqa: N802
        return QSize(self.fontMetrics().horizontalAdvance(self.text()) + 26, 26)

    def paintEvent(self, event):                   # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        level = max(self._on, self._hover * 0.6)
        p.setPen(QPen(theme.mix(theme.C.TEXT_SECONDARY, theme.C.TEXT, level)))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text())


class Segmented(QWidget):
    """分段控件：互斥的多选一（24 小时 / 7 天 / 30 天）。

    选中底板是一块独立的白色圆角块，切换时用 200ms 滑动过去（Fluent 的
    "connected animation" 感觉），而不是让两块原地闪现。
    """

    changed = Signal(str)

    def __init__(self, items, parent=None):
        super().__init__(parent)
        self._buttons: dict[str, _SegButton] = {}
        self._current = ""
        self._thumb = QRectF()
        # 复用同一个动画：连点时分段控件不会堆出一串短命动画对象
        self._thumb_anim = QVariantAnimation(self)
        self._thumb_anim.valueChanged.connect(self._set_thumb)
        self._thumb_anim.finished.connect(self._settle_thumb)

        row = QHBoxLayout(self)
        row.setContentsMargins(3, 3, 3, 3)
        row.setSpacing(2)
        for key, label in items:
            btn = _SegButton(label, self)
            btn.clicked.connect(lambda _checked=False, k=key: self._select(k))
            self._buttons[key] = btn
            row.addWidget(btn, 1)

        if items:
            self._select(items[0][0], emit=False)

    # ------------------------------------------------------------------ #
    def paintEvent(self, event):                   # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 18))
        p.drawRoundedRect(QRectF(self.rect()), 8, 8)
        if self._thumb.isValid():
            p.setPen(QPen(QColor(0, 0, 0, 18), 1))
            p.setBrush(QColor(255, 255, 255, 250))
            p.drawRoundedRect(self._thumb, 6, 6)

    def current(self) -> str:
        return self._current

    def set_current(self, key: str, emit: bool = False) -> None:
        self._select(key, emit=emit)

    def resizeEvent(self, event):                  # noqa: N802
        super().resizeEvent(event)
        if self._current in self._buttons:
            self._thumb = self._thumb_rect(self._current)
            self.update()

    def _thumb_rect(self, key: str) -> QRectF:
        btn = self._buttons.get(key)
        if btn is None:
            return QRectF()
        return QRectF(btn.geometry()).adjusted(0.5, 0.5, -0.5, -0.5)

    def _set_thumb(self, rect):
        self._thumb = QRectF(rect)
        self.update()

    def _settle_thumb(self):
        if self._current in self._buttons:
            self._thumb = self._thumb_rect(self._current)
            self.update()

    def _select(self, key: str, emit: bool = True) -> None:
        if key not in self._buttons:
            return
        for k, btn in self._buttons.items():
            btn.setChecked(k == key)
        changed = key != self._current
        self._current = key
        target = self._thumb_rect(key)
        ms = motion.duration(220)
        if not self._thumb.isValid() or not self.isVisible() or ms <= 0:
            self._thumb_anim.stop()
            self._set_thumb(target)                # 首次布局/动效关闭：直接定位
        else:
            self._thumb_anim.stop()
            self._thumb_anim.setStartValue(self._thumb)
            self._thumb_anim.setEndValue(target)
            self._thumb_anim.setDuration(ms)
            self._thumb_anim.setEasingCurve(motion.easing("decelerate"))
            self._thumb_anim.start()
        if changed and emit:
            self.changed.emit(key)


class FluentSlider(QSlider):
    """Fluent 风格滑块：自绘轨道与圆钮，点轨道即跳到该位置。

    交互自己接管（原生 QSlider 点击轨道只按 pageStep 跳，手感不对），
    但取值、键盘、禁用逻辑仍然复用 QSlider，不另造一套状态机。
    悬停/按下是 0..1 的补间，动效关掉时直接落终值。

    布局：轨道两头各留 `GUTTER` 的内边距，钮走到 0% / 100% 时圆心落在
    内边距里——这样悬停放大的钮和焦点圈都不会被控件边界裁掉，也不会顶到
    右侧的数值框上（之前两端贴边，100% 时钮和输入框糊在一起）。
    """

    GUTTER = 16.0                                  # 半钮 + 焦点圈 + 一点余量，两头都不贴边
    HEIGHT = 28

    def __init__(self, parent=None, minimum: int = 0, maximum: int = 100, value: int = 0):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setRange(int(minimum), int(maximum))
        self.setSingleStep(1)
        self.setPageStep(max(1, (maximum - minimum) // 10))
        self.setValue(int(value))
        self.setFixedHeight(self.HEIGHT)
        self.setMinimumWidth(120)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._hover = 0.0
        self._hover_tween = motion.Tween(self, self._set_hover, base=110)
        self._pressed = False
        self.valueChanged.connect(lambda *_: self.update())
        self.rangeChanged.connect(lambda *_: self.update())

    # ------------------------------------------------------------------ #
    def _set_hover(self, value: float):
        self._hover = float(value)
        self.update()

    def _handle_diameter(self) -> float:
        return 14.0 + 2.0 * self._hover - (2.0 if self._pressed else 0.0)

    def _track_rect(self) -> QRectF:
        """轨道矩形：左右各留 GUTTER，圆心正好压在轨道两端。"""
        cy = self.height() / 2.0
        left = self.GUTTER
        return QRectF(left, cy - 2.0, max(1.0, self.width() - 2 * self.GUTTER), 4.0)

    def _value_at(self, x: float) -> int:
        """把鼠标的 x 换算成取值：与轨道绘制使用同一套内边距。"""
        span = max(1.0, self.width() - 2 * self.GUTTER)
        ratio = max(0.0, min(1.0, (x - self.GUTTER) / span))
        low, high = self.minimum(), self.maximum()
        return int(round(low + ratio * (high - low)))

    # ------------------------------------------------------------------ #
    def enterEvent(self, event):                   # noqa: N802
        self._hover_tween.to(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):                   # noqa: N802
        self._hover_tween.to(0.0)
        super().leaveEvent(event)

    def focusInEvent(self, event):                 # noqa: N802
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event):                # noqa: N802
        super().focusOutEvent(event)
        self.update()

    def mousePressEvent(self, event):              # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self._pressed = True
            self.setValue(self._value_at(event.position().x()))
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):               # noqa: N802
        if self._pressed and self.isEnabled():
            self.setValue(self._value_at(event.position().x()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):            # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._pressed:
            self._pressed = False
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # ------------------------------------------------------------------ #
    def paintEvent(self, event):                   # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        enabled = self.isEnabled()
        t = (self.value() - self.minimum()) / max(1, self.maximum() - self.minimum())
        d = self._handle_diameter()
        cy = self.height() / 2.0
        track = self._track_rect()
        cx = track.left() + track.width() * t

        accent = theme.C.ACCENT if enabled else QColor(120, 120, 120, 130)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 36))
        p.drawRoundedRect(track, 2, 2)

        filled = QRectF(track.left(), track.top(), max(0.0, cx - track.left()), track.height())
        if filled.width() > 0.5:
            p.setBrush(accent)
            p.drawRoundedRect(filled, 2, 2)

        handle = QRectF(cx - d / 2.0, cy - d / 2.0, d, d)
        p.setBrush(QColor(255, 255, 255) if enabled else QColor(246, 246, 246))
        p.setPen(QPen(accent, 1.6))
        p.drawEllipse(handle)
        if enabled:                                # 钮心一个小点，和 WinUI 一致
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(accent)
            p.drawEllipse(QPointF(cx, cy), 2.2 + 0.6 * self._hover, 2.2 + 0.6 * self._hover)

        if self.hasFocus():
            ring = QColor(accent)
            ring.setAlpha(90)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(ring, 2))
            p.drawEllipse(handle.adjusted(-2.5, -2.5, 2.5, 2.5))
