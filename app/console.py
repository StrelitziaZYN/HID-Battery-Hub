"""主窗口：左侧导航三个标签页——通用设置 / 电量统计 / 接口设置。

托盘弹出面板只负责「看一眼电量」，需要操作时点齿轮/图表打开这个窗口。
"""
from __future__ import annotations

from typing import Any, Callable, Dict

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen
from PySide6.QtWidgets import (QAbstractButton, QHBoxLayout, QLabel,
                               QStackedWidget, QVBoxLayout, QWidget)

from . import backdrop, branding, i18n, motion, theme
from .history_page import HistoryPage
from .icons import app_icon
from .settings_page import GeneralSettingsPage, InterfaceSettingsPage
from .widgets import FlatButton

NAV = [
    ("general", "通用设置", "settings"),
    ("stats", "电量统计", "chart"),
    ("iface", "接口设置", "device"),
]  # 标签为中文键，渲染时经 i18n.T 翻译


class TintSurface(QWidget):
    """带底色的容器。

    不用样式表设背景：Qt 里普通 `QWidget` 的 QSS `background` 需要
    `WA_StyledBackground` 才绘制，漏了就完全不画（材质透出来后才发现）。
    这里直接自绘，颜色含 alpha，正好用来压在系统材质之上做遮罩。
    """

    def __init__(self, fill: QColor, border_right: bool = False, parent=None):
        super().__init__(parent)
        self._fill = QColor(fill)
        self._border_right = border_right

    def set_fill(self, fill: QColor):
        self._fill = QColor(fill)
        self.update()

    def fill(self) -> QColor:
        return QColor(self._fill)

    def paintEvent(self, event):                   # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), self._fill)
        if self._border_right:
            p.setPen(QPen(QColor(0, 0, 0, 18), 1))
            p.drawLine(self.width() - 1, 0, self.width() - 1, self.height())


class NavItem(QAbstractButton):
    """左侧导航项：图标 + 文字，选中时左侧一条强调色指示条。

    悬停底色与选中态都是渐变：切换标签时选中块淡入、指示条从中间长出来，
    比「上一项瞬间变灰、下一项瞬间变黑」更接近 Win11 设置页的观感。
    """

    def __init__(self, label: str, glyph: str, parent=None):
        super().__init__(parent)
        self.setText(label)
        self._glyph = glyph
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(theme.ui_font(10))
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFixedHeight(40)
        self._hover = 0.0
        self._sel = 0.0
        self._hover_tween = motion.Tween(self, self._set_hover, base=120)
        self._sel_tween = motion.Tween(self, self._set_sel, base=190)

    def _set_hover(self, value: float):
        self._hover = value
        self.update()

    def _set_sel(self, value: float):
        self._sel = value
        self.update()

    def setChecked(self, checked: bool):           # noqa: N802
        super().setChecked(checked)
        self._sel_tween.to(1.0 if checked else 0.0)

    def sizeHint(self):                            # noqa: N802
        return QSize(184, 40)

    def enterEvent(self, event):                   # noqa: N802
        self._hover_tween.to(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):                   # noqa: N802
        self._hover_tween.to(0.0)
        super().leaveEvent(event)

    def paintEvent(self, event):                   # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(8, 2, -8, -2)
        hover_only = self._hover * (1.0 - self._sel)
        alpha = 20.0 * self._sel + 10.0 * hover_only
        if alpha > 0.4:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, round(alpha)))
            p.drawRoundedRect(r, 6, 6)
        if self._sel > 0.02:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.tint(theme.C.ACCENT, round(255 * self._sel)))
            h = (r.height() - 18) * self._sel
            p.drawRoundedRect(QRectF(2, r.center().y() - h / 2, 3, h), 1.5, 1.5)

        color = theme.mix(theme.C.TEXT_SECONDARY, theme.C.TEXT,
                          max(self._sel, self._hover * 0.55))
        p.setPen(QPen(color))
        p.setFont(theme.icon_font(11))
        p.drawText(QRectF(r.left() + 12, r.top(), 22, r.height()),
                   Qt.AlignmentFlag.AlignCenter, theme.ICON.get(self._glyph, ""))
        p.setFont(self.font())
        p.drawText(QRectF(r.left() + 40, r.top(), r.width() - 46, r.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())


class ConsoleWindow(QWidget):
    """独立设置窗口（普通带边框窗口，可最小化 / 缩放）。"""

    config_changed = Signal()
    icons_changed = Signal()
    refresh_requested = Signal()
    language_changed = Signal()
    appearance_changed = Signal()
    opened = Signal()

    def __init__(self, config: Dict[str, Any],
                 devices_provider: Callable[[], list],
                 parent=None):
        super().__init__(parent)
        self.config = config
        self._devices_provider = devices_provider
        self._current = "general"
        self._seen: set[str] = set()               # 已经展示过的页：首次不做淡入，避免闪
        self._material = "solid"

        motion.configure(config.get("ui"))
        self.setWindowTitle(branding.APP_NAME)
        self.setWindowIcon(QIcon(app_icon(False)))
        self.resize(940, 820)                      # 3:2 图表需要更充裕的纵向空间
        self.setMinimumSize(800, 560)
        self.setAcceptDrops(True)
        # 半透明窗口 + 系统材质：底色由 apply_appearance() 按材质给透明度
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 整窗内容装在一个 shell 里：打开设置窗口时对 shell 做淡入，
        # 不去动 windowOpacity（那会把窗口变成分层窗口，把系统材质顶掉）。
        self.shell = QWidget()
        self.shell.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        root.addWidget(self.shell)
        shell = QHBoxLayout(self.shell)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        # ---------------- 左侧导航 ---------------- #
        self.nav = TintSurface(theme.nav_scrim("solid"), border_right=True)
        self.nav.setFixedWidth(208)
        nav_lay = QVBoxLayout(self.nav)
        nav_lay.setContentsMargins(12, 18, 12, 16)
        nav_lay.setSpacing(2)

        title = QLabel(branding.APP_NAME)
        title.setFont(theme.ui_font(12, QFont.Weight.DemiBold))
        title.setStyleSheet(f"color: {theme.C.TEXT.name()};")
        title.setWordWrap(True)
        nav_lay.addWidget(title)
        sub = QLabel(f"{i18n.T(branding.TAGLINE)} · v{branding.VERSION}")
        sub.setFont(theme.ui_font(7))
        sub.setStyleSheet(f"color: {theme.C.TEXT_TERTIARY.name()};")
        nav_lay.addWidget(sub)

        self.nav_status = QLabel("—")
        self.nav_status.setFont(theme.ui_font(7))
        self.nav_status.setWordWrap(True)
        self.nav_status.setStyleSheet(f"color: {theme.C.TEXT_TERTIARY.name()}; padding-top:4px;")
        nav_lay.addWidget(self.nav_status)
        nav_lay.addSpacing(12)

        self._nav: Dict[str, NavItem] = {}
        for key, label, glyph in NAV:
            item = NavItem(i18n.T(label), glyph)
            item.clicked.connect(lambda _checked=False, k=key: self.open_page(k))
            self._nav[key] = item
            nav_lay.addWidget(item)

        nav_lay.addStretch(1)
        self.btn_refresh = FlatButton(i18n.T("立即刷新"),
                                      glyph=theme.ICON["refresh"], spin=True)
        self.btn_refresh.clicked.connect(lambda *_: self.refresh_requested.emit())
        nav_lay.addWidget(self.btn_refresh)
        shell.addWidget(self.nav)

        # ---------------- 右侧内容 ---------------- #
        content = TintSurface(theme.scrim("solid"))
        self.content = content
        content_lay = QVBoxLayout(content)
        content_lay.setContentsMargins(26, 20, 26, 20)
        content_lay.setSpacing(0)

        self.stack = QStackedWidget()
        self.stack.setStyleSheet("background: transparent;")
        content_lay.addWidget(self.stack)

        self.general_page = GeneralSettingsPage(config, embedded=True)
        self.history_page = HistoryPage(embedded=True, config=config)
        self.iface_page = InterfaceSettingsPage(config, devices_provider, embedded=True)
        for page in (self.general_page, self.history_page, self.iface_page):
            self.stack.addWidget(page)

        self.general_page.config_changed.connect(self._on_config_changed)
        self.iface_page.config_changed.connect(self._on_config_changed)
        self.history_page.config_changed.connect(self._on_config_changed)
        self.iface_page.icons_changed.connect(self.icons_changed)
        self.iface_page.prompt_requested.connect(self.open_prompt)
        self.general_page.language_changed.connect(self.language_changed)
        self.general_page.appearance_changed.connect(self.apply_appearance)

        shell.addWidget(content, 1)
        self._pages = {"general": self.general_page,
                       "stats": self.history_page,
                       "iface": self.iface_page}
        self.apply_appearance()
        self.open_page("general", refresh=False)

    # ------------------------------------------------------------------ #
    def apply_appearance(self):
        """材质 / 动效改了：重新贴 DWM 材质并同步底色，不需要重启。"""
        motion.configure(self.config.get("ui"))
        material = backdrop.apply(self, motion.material())
        self._material = material
        self.nav.set_fill(theme.nav_scrim(material))
        self.content.set_fill(theme.scrim(material))
        self.update()
        self.appearance_changed.emit()

    def _on_config_changed(self):
        """任一页改了配置：三页都重读一次（间隔 / 自启 / 插件列表可能互相影响）。"""
        self.general_page.reload(self.config)
        self.history_page.reload()
        self.iface_page.reload(self.config)
        self.config_changed.emit()

    # ------------------------------------------------------------------ #
    def set_status(self, text: str):
        self.nav_status.setText(text)

    def set_reading(self, reading: bool):
        """与悬浮面板同步：读的时候「立即刷新」按钮转圈，读完停。"""
        if not hasattr(self.btn_refresh, "_spin"):
            return
        if reading:
            self.btn_refresh.start_spin()
        else:
            self.btn_refresh.stop_spin()

    def open_page(self, key: str, refresh: bool = True):
        if key == "settings":                      # 兼容旧入口：设置 = 通用设置页
            key = "general"
        page = self._pages.get(key)
        if page is None:
            key, page = "general", self.general_page
        self._current = key
        for name, item in self._nav.items():
            item.setChecked(name == key)
        switching = page is not self.stack.currentWidget()
        self.stack.setCurrentWidget(page)
        if switching and key in self._seen and motion.transitions_enabled() and self.isVisible():
            # 只在「再次切回」时淡入：首次展示必须立刻完整可见（截图/录屏都靠这个）
            motion.fade_in(page, base=190, start=0.35)
        self._seen.add(key)
        if refresh:
            self._reload_current()

    def _reload_current(self):
        if self._current == "general":
            self.general_page.reload(self.config)
        elif self._current == "stats":
            self.history_page.reload()
        elif self._current == "iface":
            self.iface_page.reload(self.config)

    def open_prompt(self, device: Dict[str, Any] | None = None):
        from .prompt_page import PromptDialog

        dlg = PromptDialog(self.config, device, self)
        dlg.bundle_imported.connect(self._on_bundle_imported)
        dlg.exec()

    def _on_bundle_imported(self):
        self.general_page.reload(self.config)
        self.history_page.reload()
        self.iface_page.reload(self.config)
        self.config_changed.emit()

    def import_bundle(self, path: str) -> bool:
        ok = self.iface_page.import_bundle(path)
        self.open_page("iface", refresh=False)
        return ok

    # ------------------------------------------------------------------ #
    def showEvent(self, event):                    # noqa: N802
        super().showEvent(event)
        self.apply_appearance()                    # 窗口句柄可能重建，材质要重贴
        if not getattr(self, "_faded_in", False):
            self._faded_in = True
            if motion.transitions_enabled():
                motion.fade_in(self.shell, base=200, start=0.0)
        self.general_page.reload(self.config)
        self.history_page.reload()
        self.iface_page.reload(self.config)
        self._reload_current()
        self.opened.emit()

    def dragEnterEvent(self, event):               # noqa: N802
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if any(u.toLocalFile().lower().endswith((".json", ".py")) for u in urls):
            event.acceptProposedAction()

    def dropEvent(self, event):                    # noqa: N802
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith((".json", ".py")):
                self.import_bundle(path)
                event.acceptProposedAction()
                return
