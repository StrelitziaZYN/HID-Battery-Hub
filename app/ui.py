"""托盘弹出面板（只显示设备列表）。

设置 / 电量统计都在独立窗口 `app.console.ConsoleWindow` 里，
这个面板只做一件事：靠近托盘弹出来、失焦自动收起。
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import (QAbstractAnimation, QPoint, QPropertyAnimation,
                            QRect, Qt, QTimer, Signal)
from PySide6.QtGui import QColor, QFont, QGuiApplication
from PySide6.QtWidgets import (QApplication, QFrame, QGraphicsDropShadowEffect, QHBoxLayout,
                               QLabel, QScrollArea, QVBoxLayout, QWidget)

from . import branding, i18n, motion, theme, winapi
from .widgets import DeviceRow, DragArea, IconButton, Surface


class DevicesPage(QWidget):
    refresh_requested = Signal()
    settings_requested = Signal()
    history_requested = Signal()
    device_clicked = Signal(dict)
    size_changed = Signal()                        # 列表行数变了，宿主窗口需要重算高度

    def __init__(self, parent=None):
        super().__init__(parent)
        self._devices: list[dict] = []
        self._rows: list[DeviceRow] = []
        self._last_update: datetime | None = None
        self._reading = False                      # 是否正在读取（由主程序驱动）
        self._notice = ""                          # 读取失败时的提示，如「读取超时…」

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(0)

        # 标题区兼作拖动条：按住空白处可移动整个面板
        self.head = DragArea()
        head_col = QVBoxLayout(self.head)
        head_col.setContentsMargins(0, 0, 0, 0)
        head_col.setSpacing(2)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(2)
        self.title = QLabel(branding.APP_NAME)
        self.title.setFont(theme.ui_font(11, QFont.Weight.DemiBold))
        self.title.setStyleSheet(f"color: {theme.C.TEXT.name()};")
        head.addWidget(self.title)
        head.addStretch(1)

        self.btn_history = IconButton(theme.ICON["chart"], size=28, icon_size=9, tooltip=i18n.T("电量统计"))
        self.btn_refresh = IconButton(theme.ICON["refresh"], size=28, icon_size=9,
                                      tooltip=i18n.T("刷新（设备清单 + 电量）"), spin=True)
        self.btn_settings = IconButton(theme.ICON["settings"], size=28, icon_size=9, tooltip=i18n.T("打开设置"))
        self.btn_history.clicked.connect(lambda *_: self.history_requested.emit())
        self.btn_refresh.clicked.connect(lambda *_: self.refresh_requested.emit())
        self.btn_settings.clicked.connect(lambda *_: self.settings_requested.emit())
        head.addWidget(self.btn_history)
        head.addWidget(self.btn_refresh)
        head.addWidget(self.btn_settings)
        head_col.addLayout(head)

        self.subtitle = QLabel(i18n.T("尚未读取"))
        self.subtitle.setFont(theme.ui_font(8))
        self.subtitle.setStyleSheet(f"color: {theme.C.TEXT_TERTIARY.name()};")
        head_col.addWidget(self.subtitle)

        root.addWidget(self.head)
        root.addSpacing(6)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea{background:transparent;}"
                                  "QScrollBar:vertical{width:6px;background:transparent;margin:2px 0;}"
                                  "QScrollBar::handle:vertical{background:rgba(0,0,0,60);border-radius:3px;min-height:24px;}"
                                  "QScrollBar::add-line, QScrollBar::sub-line{height:0;}"
                                  "QScrollBar::add-page, QScrollBar::sub-page{background:transparent;}")
        self.list_host = QWidget()
        self.list_host.setStyleSheet("background: transparent;")
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(0)
        self.list_layout.addStretch(1)
        self.scroll.setWidget(self.list_host)
        root.addWidget(self.scroll, 1)

        self.empty = QWidget()
        empty_lay = QVBoxLayout(self.empty)
        empty_lay.setContentsMargins(0, 18, 0, 18)
        empty_lay.setSpacing(6)
        self.empty_title = QLabel(i18n.T("未检测到 2.4G 设备"))
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_title.setFont(theme.ui_font(10, QFont.Weight.DemiBold))
        self.empty_title.setStyleSheet(f"color: {theme.C.TEXT_SECONDARY.name()};")
        self.empty_hint = QLabel(i18n.T("请确认接收器已插入，或在设置里「接入新设备」"))
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint.setFont(theme.ui_font(8))
        self.empty_hint.setStyleSheet(f"color: {theme.C.TEXT_TERTIARY.name()};")
        empty_lay.addWidget(self.empty_title)
        empty_lay.addWidget(self.empty_hint)
        self.empty.hide()
        root.addWidget(self.empty)

        foot = QHBoxLayout()
        foot.setContentsMargins(2, 6, 2, 0)
        self.status = QLabel("—")
        self.status.setFont(theme.ui_font(8))
        self.status.setStyleSheet(f"color: {theme.C.TEXT_TERTIARY.name()};")
        foot.addWidget(self.status)
        foot.addStretch(1)
        self.btn_more = IconButton(theme.ICON["more"], size=24, icon_size=8, tooltip=i18n.T("打开设置窗口"))
        self.btn_more.clicked.connect(lambda *_: self.settings_requested.emit())
        foot.addWidget(self.btn_more)
        root.addLayout(foot)

    # ------------------------------------------------------------------ #
    #: 面板内最多「整行铺开」的台数，再多就交给滚动条，避免窗口长过屏幕
    MAX_VISIBLE_ROWS = 8

    def row_count(self) -> int:
        return len(self._rows)

    def set_devices(self, devices: list[dict], error: str = ""):
        self._devices = devices
        self._last_update = datetime.now()
        self._notice = error or ""                 # 拿到新结果 = 这一轮是成功的，旧提示作废

        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            w = item.widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self._rows = []

        stagger = self.isVisible() and motion.transitions_enabled()
        for index, dev in enumerate(devices):
            row = DeviceRow(dev)
            row.clicked.connect(lambda d: self.device_clicked.emit(d))
            self.list_layout.insertWidget(self.list_layout.count() - 1, row)
            self._rows.append(row)
            if stagger:                            # 刷新时逐行淡入，避免整列表硬闪
                QTimer.singleShot(index * 28,
                                  lambda w=row: motion.fade_in(w, base=170, start=0.0))

        if devices:
            self.scroll.show()
            self.empty.hide()
        else:
            self.scroll.hide()
            self.empty.show()

        rows = min(len(self._rows), self.MAX_VISIBLE_ROWS)
        self.scroll.setFixedHeight(max(64, rows * 64 + 4))
        self._render_texts()
        self.adjustSize()
        # 行数变了必须通知宿主窗口重新计算高度，否则新设备刷出来时会撑出窗口边界
        self.size_changed.emit()

    def set_reading(self, reading: bool, notice: str = ""):
        """主程序告知「正在读取 / 读完了」。

        读取结束（无论成功、没设备还是超时）都必须落到一个稳定状态，
        否则界面会永远停在「正在读取设备…」——这是之前最影响体感的问题。
        """
        self._reading = bool(reading)
        self._notice = "" if reading else (notice or "")
        if reading:
            self.btn_refresh.start_spin()
        else:
            self.btn_refresh.stop_spin()
        self._render_texts()

    def _render_texts(self):
        devices = self._devices
        known = [d["percent"] for d in devices if d.get("percent") is not None]
        low = min(known) if known else None
        charging = sum(1 for d in devices if d.get("charging"))
        stamp = self._last_update.strftime("%H:%M:%S") if self._last_update else "--:--:--"

        if self._reading:
            self.subtitle.setText(i18n.T("正在读取设备…"))
            self.status.setText(i18n.T("正在刷新设备清单与电量"))
            return

        if self._notice:                           # 超时 / 无响应：异常状态优先显示
            self.subtitle.setText(self._notice)
            self.status.setText(
                i18n.TF("{} 台设备 · 上次更新 {}", len(devices), stamp)
                if devices else i18n.TF("上次读取 {}", stamp))
            return

        if devices:
            bits = [i18n.TF("已完成读取 · {} 台设备", len(devices))]
            if low is not None:
                bits.append(i18n.TF("最低 {}%", low))
            self.subtitle.setText(" · ".join(bits))
            tail = [i18n.TF("{} 台充电中", charging)] if charging else []
            tail.append(i18n.TF("更新于 {}", stamp))
            self.status.setText(" · ".join(tail))
        elif self._last_update:
            self.subtitle.setText(i18n.T("已完成读取 · 未检测到设备"))
            self.status.setText(i18n.TF("上次读取 {}", stamp))
        else:
            self.subtitle.setText(i18n.T("尚未读取"))
            self.status.setText(i18n.T("等待首次读取"))

    def clear(self, text: str = "正在读取设备…"):
        """兼容旧调用：等价于「开始读取」。"""
        _ = text
        self.set_reading(True)


class Flyout(QWidget):
    """点击托盘后弹出的悬浮面板：失焦即收起。"""

    refresh_requested = Signal()
    settings_requested = Signal()
    history_requested = Signal()
    device_clicked = Signal(dict)
    position_changed = Signal()
    preview_closed = Signal()                      # 预览面板自己收起了（Esc / 外部关闭）

    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self.config = config
        motion.configure(config.get("ui"))
        self._drag_offset: QPoint | None = None
        self._closing = False
        self._was_active = False
        self._pinned = False                       # 预览模式：置顶、不抢焦点、不失焦收起
        self._anim: QPropertyAnimation | None = None
        self._fade: QPropertyAnimation | None = None

        self.setWindowTitle(branding.APP_NAME)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedWidth(372)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 18)
        outer.setSpacing(0)

        self.surface = Surface(radius=12)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(38)
        shadow.setOffset(0, 10)
        shadow.setColor(QColor(0, 0, 0, 55))
        self.surface.setGraphicsEffect(shadow)
        outer.addWidget(self.surface)

        inner = QVBoxLayout(self.surface)
        inner.setContentsMargins(0, 0, 0, 0)
        self.devices_page = DevicesPage()
        inner.addWidget(self.devices_page)

        self.devices_page.refresh_requested.connect(lambda: self.refresh_requested.emit())
        self.devices_page.settings_requested.connect(lambda: self.settings_requested.emit())
        self.devices_page.history_requested.connect(lambda: self.history_requested.emit())
        self.devices_page.device_clicked.connect(lambda d: self.device_clicked.emit(d))
        self.devices_page.size_changed.connect(self._sync_content)

        self.request_refresh = None  # 由主程序注入

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(150)
        self._hide_timer.timeout.connect(self._maybe_hide)

        # 兜底：某些情况下 Tool 窗口不会收到 WindowDeactivate，靠轮询活动窗口收起来
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(700)
        self._watchdog.timeout.connect(self._watch_focus)
        self.apply_appearance()

    # ------------------------------------------------------------------ #
    def apply_appearance(self):
        """跟随「窗口材质 + 面板不透明度」重算面板底色。

        弹出层本身不做 DWM 背景材质——它是无边框半透明窗口，系统材质会把
        卡片外的 16px 阴影留白也涂成方块，反而毁掉圆角和投影；这里改为让
        卡片本身更通透一点，视觉上与设置窗口的材质连成一套。

        透明度是「百分比 × 材质基准 alpha」，所以 Mica 下 100% 仍有轻微的
        壁纸色透出来，调到 50% 才是明显半透明——这样两档设置不会互相抵消。
        """
        self.surface.set_fill(theme.panel_fill(motion.material(), motion.panel_opacity()))

    # ------------------------------------------------------------------ #
    #: 面板外框留白（outer layout 的上下 margin）
    _OUTER = 14 + 18

    def _fit(self):
        """高度直接取内容页的真实需求，不再手写魔数（之前少算 9~10px 会挤掉底部）。"""
        page = self.devices_page
        if page.layout() is not None:
            page.layout().activate()               # 行刚增删时先把布局算出来再取 sizeHint
        need = max(page.sizeHint().height(), page.minimumSizeHint().height())
        screen = (QGuiApplication.screenAt(self.geometry().center())
                  or QGuiApplication.primaryScreen())
        limit = screen.availableGeometry().height() - 16 if screen else need + self._OUTER
        self.setFixedHeight(min(need + self._OUTER, limit))

    def _sync_content(self):
        """设备列表刷新了：高度立即跟上，面板正开着就贴着原下边缘伸缩，避免内容溢出。"""
        old = self.geometry()
        if motion.is_alive(self._anim) and self._anim.state() == QAbstractAnimation.State.Running:
            self._anim.stop()                      # 显示动画的高度是旧的，交给下面重新定位
        self._fit()
        if not self.isVisible() or self._closing:
            self.surface.update()
            return
        height = self.height()
        if height == old.height():
            self.update()
            self.surface.update()
            return
        y = old.bottom() - height + 1              # 贴着任务栏那一侧固定
        screen = (QGuiApplication.screenAt(QPoint(old.center().x(), old.bottom()))
                  or QGuiApplication.primaryScreen())
        area = screen.availableGeometry()
        y = max(area.top() + 2, min(y, area.bottom() - height - 2))
        self.setGeometry(old.left(), y, self.width(), height)
        self.update()
        self.surface.update()

    def is_showing(self) -> bool:
        return self.isVisible() and not self._closing

    def set_reading(self, reading: bool, notice: str = ""):
        """由主程序驱动：开始读 → 刷新按钮转圈；读完 → 停转并落定状态文字。"""
        self.devices_page.set_reading(reading, notice)

    # ------------------------------------------------------------------ #
    @staticmethod
    def _to_logical(rect: QRect, screen) -> QRect:
        """Win32 返回物理像素，Qt 使用逻辑像素，按屏幕缩放比换算。"""
        if rect is None:
            return QRect()
        ratio = screen.devicePixelRatio() or 1.0
        geo = screen.geometry()
        left = geo.left() + round((rect.left() - geo.left() * ratio) / ratio)
        top = geo.top() + round((rect.top() - geo.top() * ratio) / ratio)
        return QRect(left, top, round(rect.width() / ratio), round(rect.height() / ratio))

    def _locate_tray(self):
        """返回 (屏幕, 逻辑坐标下的通知区域矩形, 任务栏边缘)。"""
        tb_rect, edge = winapi.taskbar_rect()
        notify = winapi.tray_notify_rect()
        probe = notify or tb_rect
        screen = QGuiApplication.primaryScreen()
        if probe is not None:
            for s in QGuiApplication.screens():
                logical = self._to_logical(probe, s)
                if s.geometry().intersects(logical):
                    screen = s
                    break
        tray_logical = self._to_logical(notify, screen) if notify else None
        tb_logical = self._to_logical(tb_rect, screen) if tb_rect else None
        return screen, tray_logical, tb_logical, edge

    def _target_rect(self) -> QRect:
        self._fit()
        screen, notify, taskbar, edge = self._locate_tray()
        full = screen.geometry()
        w, h = self.width(), self.height()
        gap = 8

        anchor = notify if notify and not notify.isEmpty() else taskbar
        if anchor is None or anchor.isEmpty():
            return QRect(full.right() - w - 12, full.bottom() - h - 12, w, h)

        if edge == winapi.EDGE_BOTTOM:
            x = anchor.right() - w + 8
            y = anchor.top() - h - gap
        elif edge == winapi.EDGE_TOP:
            x = anchor.right() - w + 8
            y = anchor.bottom() + gap
        elif edge == winapi.EDGE_LEFT:
            x = anchor.right() + gap
            y = anchor.bottom() - h - 8
        else:
            x = anchor.left() - w - gap
            y = anchor.bottom() - h - 8

        x = max(full.left() + 4, min(x, full.right() - w - 4))
        y = max(full.top() + 4, min(y, full.bottom() - h - 4))
        return QRect(x, y, w, h)

    def _preferred_rect(self) -> QRect:
        """优先用用户拖动后的位置（窗口记忆），否则吸附到任务栏。"""
        self._fit()
        saved = self.config.get("window_pos")
        if isinstance(saved, (list, tuple)) and len(saved) == 2:
            w, h = self.width(), self.height()
            x, y = int(saved[0]), int(saved[1])
            screen = QGuiApplication.screenAt(QPoint(x + w // 2, y + h // 2))
            if screen is None:
                for candidate in QGuiApplication.screens():
                    if candidate.geometry().intersects(QRect(x, y, w, h)):
                        screen = candidate
                        break
            if screen is not None:
                area = screen.availableGeometry()
                x = max(area.left() + 2, min(x, area.right() - w - 2))
                y = max(area.top() + 2, min(y, area.bottom() - h - 2))
                return QRect(x, y, w, h)
        return self._target_rect()

    def popup(self):
        if self._pinned:                           # 预览中再点托盘：先把预览收起
            self.end_preview()
            return
        if self.is_showing():
            self.hide_animated()
            return
        if self._closing:                          # 正在淡出：直接重新显示
            self._cancel_fade()
        target = self._preferred_rect()
        animated = motion.transitions_enabled()
        start = QRect(target.x(), target.y() + (10 if target.top() < 400 else -10),
                      target.width(), target.height())
        self.setGeometry(start if animated else target)
        self.setWindowOpacity(0.0 if animated else 1.0)
        self.show()
        self.raise_()
        self.activateWindow()

        if animated:
            # 进场用 Fluent 的减速曲线：先快后慢，落位时不回弹
            if motion.is_alive(self._anim):
                self._anim.stop()
                self._anim.deleteLater()
            self._anim = QPropertyAnimation(self, b"geometry", self)
            self._anim.setDuration(motion.duration(200))
            self._anim.setStartValue(start)
            self._anim.setEndValue(target)
            self._anim.setEasingCurve(motion.easing("decelerate"))
            self._anim.start()

            fade = QPropertyAnimation(self, b"windowOpacity", self)
            fade.setDuration(motion.duration(170))
            fade.setStartValue(0.0)
            fade.setEndValue(1.0)
            fade.setEasingCurve(motion.easing("decelerate"))
            fade.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
            self._fade = fade

        if callable(self.request_refresh):
            self.request_refresh()

        self._was_active = self.isActiveWindow()
        if QGuiApplication.platformName() != "offscreen":   # 离屏渲染时没有真正的焦点
            self._watchdog.start()

    def _cancel_fade(self):
        for anim in (self._anim, self._fade):
            if motion.is_alive(anim) and anim.state() == QAbstractAnimation.State.Running:
                anim.stop()
        self._closing = False
        self.setWindowOpacity(1.0)

    # ------------------------------------------------------------------ #
    def _preview_rect(self, anchor: QWidget | None) -> QRect:
        """贴在设置窗口右侧（放不下就左侧），顶部对齐，整体不出屏。"""
        self._fit()
        w, h = self.width(), self.height()
        if anchor is None:
            return self._target_rect()
        geo = anchor.frameGeometry()
        screen = (QGuiApplication.screenAt(geo.center())
                  or QGuiApplication.primaryScreen())
        area = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        x = geo.right() + 12
        if x + w > area.right() - 6:
            x = geo.left() - w - 12
        if x < area.left() + 6:
            x = max(area.left() + 6, area.right() - w - 6)
        y = min(max(geo.top() + 8, area.top() + 6), area.bottom() - h - 6)
        return QRect(x, y, w, h)

    def show_preview(self, anchor: QWidget | None = None):
        """预览模式：从设置窗口旁边弹出真实面板，跟着「材质 / 不透明度」实时变。

        与托盘弹出的区别：不抢窗口焦点（用户还在拖滑块）、不做失焦自动收起、
        并置顶压在设置窗口之上——否则拖一次滑块面板就没了，看不见效果。
        """
        if self._closing:
            self._cancel_fade()
        if not self.isVisible():
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        # 不激活窗口就显示：焦点留在设置窗口，拖动滑块不会被打断
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._pinned = True
        self._watchdog.stop()
        self._hide_timer.stop()
        self.setGeometry(self._preview_rect(anchor))
        self.setWindowOpacity(1.0)
        self.show()
        self.raise_()
        if callable(self.request_refresh):
            self.request_refresh()

    def end_preview(self):
        """退出预览：淡出收起（动效关闭时直接隐藏）。"""
        if not self._pinned:
            return
        if not self.isVisible():
            self._on_hidden()                      # 没显示过：直接把状态收干净
            return
        self.hide_animated()

    def hide_animated(self):
        if not self.isVisible():
            return
        if not motion.transitions_enabled():
            self._on_hidden()
            return
        self._closing = True
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(motion.duration(130))
        anim.setStartValue(self.windowOpacity())
        anim.setEndValue(0.0)
        anim.setEasingCurve(motion.easing("accelerate"))
        anim.finished.connect(self._on_hidden)
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        self._fade = anim

    def _on_hidden(self):
        self._closing = False
        self._was_active = False
        self.hide()
        self.setWindowOpacity(1.0)
        self._watchdog.stop()
        if self._pinned:
            # 预览结束：撤掉置顶与「不激活显示」，并让设置页的「预览」按钮回到未选中
            self._pinned = False
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
            self.preview_closed.emit()

    # ------------------------------------------------------------------ #
    def on_user_moved(self):
        """拖动结束后记住位置，下次点击托盘直接出现在这里。"""
        if self._pinned:                           # 预览时挪动面板不覆盖托盘位置记忆
            return
        pos = self.pos()
        self.config["window_pos"] = [int(pos.x()), int(pos.y())]
        self.position_changed.emit()

    def reset_position(self):
        """清掉记忆，恢复吸附任务栏。"""
        self.config.pop("window_pos", None)
        self.position_changed.emit()

    def mousePressEvent(self, event):              # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):               # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):            # noqa: N802
        if self._drag_offset is not None:
            self._drag_offset = None
            self.on_user_moved()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _maybe_hide(self):
        if self._pinned or not self.isVisible() or self._closing:
            return
        self.hide_animated()

    def _watch_focus(self):
        """兜底监视：面板确实拿到过焦点、现在又失去焦点且鼠标不在上面 → 收起。

        加「拿到过焦点」这个前置条件，是为了避免 Windows 拒绝前台激活时把面板误关掉。
        """
        if self._pinned:                           # 预览模式不做失焦收起
            self._watchdog.stop()
            return
        if not self.is_showing():
            self._watchdog.stop()
            return
        if QApplication.activeWindow() is self:
            self._was_active = True
            return
        if self.underMouse() or not self._was_active:
            return
        self.hide_animated()

    def event(self, ev):                           # noqa: N802
        if ev.type() == ev.Type.WindowDeactivate and not self._pinned:
            self._hide_timer.start()               # 预览时失焦不收起，否则拖滑块就看不见了
        return super().event(ev)

    def keyPressEvent(self, event):                # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.end_preview() if self._pinned else self.hide_animated()
            return
        super().keyPressEvent(event)
