"""设置窗口里的两个「设置类」页面（各自作为左侧导航的一个标签页）。

- GeneralSettingsPage：通用设置（刷新节奏、开机自启、恢复默认）
- InterfaceSettingsPage：接口设置（协议插件、AI 接入包、设备图标、接口调试）
电量统计页在 history_page.py（含采样间隔），三页共同组成左侧导航的三个标签。
"""
from __future__ import annotations

import copy
import json
import os
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIntValidator, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QFrame,
                               QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QScrollArea, QSizePolicy, QVBoxLayout,
                               QWidget)

from . import autostart, backdrop, i18n, icon_store, motion, plugin_store, theme
from .paths import (CONFIG_DIR, DATA_DIR, DATA_DIR_NOTE, EXE_DIR, IS_FROZEN,
                    PLUGIN_DIR, USING_EXE_DIR)
from .probe_page import ProbePanel
from .profiles import default_config, new_profile, save_config
from .protocols import all_protocols, get_protocol
from .widgets import (Badge, Banner, BatteryBar, ElidedLabel, FlatButton, FluentSlider,
                      IconButton, IconTile, ListRow, Segmented, Surface, ToggleSwitch)

INPUT_QSS = theme.INPUT_QSS


class SectionLabel(QLabel):
    def __init__(self, text: str):
        super().__init__(text)
        self.setFont(theme.ui_font(8, QFont.Weight.DemiBold))
        self.setStyleSheet(f"color: {theme.C.TEXT_TERTIARY.name()}; padding: 4px 2px 2px 2px;")


class SectionCard(QWidget):
    """一个模块卡片：标识瓦片 + 标题 + 一句话说明 + 右上状态位，内容挂在 body 上。

    视觉规则（Fluent 2）：
    - 卡片白底 + 1px 描边（带一点模块主题色）+ 底部 3px 就地绘制的投影（不用
      QGraphicsDropShadowEffect，避免离屏渲染把子控件的绘制状态弄脏）；
    - 模块之间靠 14px 间距、各自的标识色和标题层级区分；
    - 卡片内的列表项再包一层白底细边圆角行，形成「窗口 → 卡片 → 行」三层。
    """

    SHADOW = 3                                     # 底部投影高度，需要从内边距里预留

    def __init__(self, title: str, glyph: str, subtitle: str = "", tone: str = "blue",
                 parent=None):
        super().__init__(parent)
        self._tone = theme.C.MODULE.get(tone, theme.C.ACCENT)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12 + self.SHADOW)
        outer.setSpacing(9)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(11)
        head.addWidget(IconTile(glyph, self._tone, size=32), 0, Qt.AlignmentFlag.AlignTop)

        col = QVBoxLayout()
        col.setContentsMargins(0, 1, 0, 0)
        col.setSpacing(2)
        name = QLabel(title)
        name.setFont(theme.ui_font(11, QFont.Weight.DemiBold))
        name.setStyleSheet(f"color: {theme.C.TEXT.name()}; background: transparent;")
        name.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        col.addWidget(name)
        if subtitle:
            tip = QLabel(subtitle)
            tip.setFont(theme.ui_font(7))
            tip.setWordWrap(True)
            tip.setStyleSheet(f"color: {theme.C.TEXT_TERTIARY.name()}; background: transparent;")
            tip.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            col.addWidget(tip)
        head.addLayout(col, 1)

        self._head_right = QHBoxLayout()
        self._head_right.setContentsMargins(0, 2, 0, 0)
        self._head_right.setSpacing(6)
        head.addLayout(self._head_right, 0)
        outer.addLayout(head)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 2, 0, 0)
        self.body.setSpacing(6)
        outer.addLayout(self.body)

    def header_widget(self, widget: QWidget) -> QWidget:
        """把状态胶囊之类的控件放到卡片标题右侧。"""
        self._head_right.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
        return widget

    def paintEvent(self, event):                   # noqa: N802
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QPainter, QPen
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        body = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5 - self.SHADOW)

        p.setPen(Qt.PenStyle.NoPen)                # 三层极淡投影，越靠下越实
        for dy, alpha in ((3, 9), (2, 13), (1, 17)):
            p.setBrush(QColor(0, 0, 0, alpha))
            p.drawRoundedRect(body.translated(0, dy), 10, 10)

        p.setPen(QPen(theme.tint(self._tone, 62), 1))
        p.setBrush(theme.C.CARD)
        p.drawRoundedRect(body, 10, 10)


class ProfileRow(ListRow):
    """一套接口配置：名称 + 插件/VID + 开关 + 编辑/删除。"""

    edited = Signal(str)
    toggled = Signal(str, bool)
    removed = Signal(str)

    def __init__(self, profile: dict, parent=None):
        super().__init__(parent)
        self.profile = profile
        self.setFixedHeight(56)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 6, 8, 6)
        lay.setSpacing(10)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        self.name = ElidedLabel(i18n.T(profile.get("name") or "未命名接口"))
        self.name.setFont(theme.ui_font(9, QFont.Weight.DemiBold))
        vid = profile.get("vendor_id", 0)
        proto = get_protocol(str(profile.get("protocol", "")))
        label = (i18n.TF("插件 {}", proto.id) if proto
                 else i18n.TF("{}（未注册）", profile.get('protocol', '?')))
        self.summary = ElidedLabel(f"VID {int(vid):#06x} · {label}")
        self.summary.setFont(theme.ui_font(7))
        col.addWidget(self.name)
        col.addWidget(self.summary)
        lay.addLayout(col, 1)

        self.toggle = ToggleSwitch(profile.get("enabled", True))
        self.toggle.toggled.connect(self._on_toggle)
        lay.addWidget(self.toggle, 0, Qt.AlignmentFlag.AlignVCenter)

        btn_edit = IconButton(theme.ICON["edit"], size=28, icon_size=8, tooltip=i18n.T("编辑"))
        btn_edit.clicked.connect(lambda: self.edited.emit(profile.get("id", "")))
        lay.addWidget(btn_edit)

        btn_del = IconButton(theme.ICON["delete"], size=28, icon_size=8, tooltip=i18n.T("删除"))
        btn_del.clicked.connect(lambda: self.removed.emit(profile.get("id", "")))
        lay.addWidget(btn_del)

        self._apply_state()

    def _on_toggle(self, checked: bool):
        self.profile["enabled"] = bool(checked)
        self._apply_state()
        self.toggled.emit(self.profile.get("id", ""), bool(checked))

    def _apply_state(self):
        """停用的接口整行压暗：一眼看出哪些接口在参与读取。"""
        on = bool(self.profile.get("enabled", True))
        self.set_dimmed(not on)
        self.name.setStyleSheet(f"color: {(theme.C.TEXT if on else theme.C.TEXT_SECONDARY).name()};")
        self.summary.setStyleSheet(
            f"color: {theme.rgba(theme.tint(theme.C.TEXT_TERTIARY, 255 if on else 150))};")


class IconRow(ListRow):
    """一个设备的图标行：预览 + 名称 + 上传 / 清除。图标全部由用户自己提供。"""

    upload_requested = Signal(str)
    clear_requested = Signal(str)

    def __init__(self, device: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.device = device
        self.icon_key = device.get("icon_key") or icon_store.key_of(
            device.get("vendor_id", 0), device.get("product_id", 0))
        self.setFixedHeight(52)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 6, 4)
        lay.setSpacing(10)

        self.preview = QLabel()
        self.preview.setFixedSize(34, 34)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._apply_preview(device.get("icon_path"))
        lay.addWidget(self.preview)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        name = ElidedLabel(device.get("name") or i18n.T("未知设备"))
        name.setFont(theme.ui_font(9, QFont.Weight.DemiBold))
        name.setStyleSheet(f"color: {theme.C.TEXT.name()};")
        vid = int(device.get("vendor_id") or 0)
        pid = int(device.get("real_pid") or device.get("product_id") or 0)
        state = i18n.T("已上传") if device.get("icon_path") else i18n.T("未上传")
        sub = ElidedLabel(i18n.TF("VID {} · PID {} · 图标{}",
                                  f"{vid:#06x}", f"{pid:#06x}", state))
        sub.setFont(theme.ui_font(7))
        sub.setStyleSheet(f"color: {theme.C.TEXT_TERTIARY.name()};")
        col.addWidget(name)
        col.addWidget(sub)
        lay.addLayout(col, 1)

        btn_up = IconButton(theme.ICON["upload"], size=28, icon_size=8, tooltip=i18n.T("上传图标"))
        btn_up.clicked.connect(lambda *_: self.upload_requested.emit(self.icon_key))
        lay.addWidget(btn_up)

        if device.get("icon_path"):
            btn_clear = IconButton(theme.ICON["delete"], size=28, icon_size=8, tooltip=i18n.T("清除图标"))
            btn_clear.clicked.connect(lambda *_: self.clear_requested.emit(self.icon_key))
            lay.addWidget(btn_clear)

    def _apply_preview(self, path: Optional[str]):
        if path:
            raw = QPixmap(path)
            if not raw.isNull():
                self.preview.setPixmap(raw.scaled(30, 30, Qt.AspectRatioMode.KeepAspectRatio,
                                                  Qt.TransformationMode.SmoothTransformation))
                self.preview.setStyleSheet(
                    "background: rgba(0,0,0,6); border-radius: 8px;")
                return
        self.preview.setText("—")
        self.preview.setFont(theme.ui_font(9))
        self.preview.setStyleSheet(
            f"color: {theme.C.TEXT_TERTIARY.name()}; background: rgba(0,0,0,6);"
            "border: 1px dashed rgba(0,0,0,42); border-radius: 8px;")


class SettingsPageBase(QWidget):
    """设置类页面的公共外壳：标题 + 副标题 + 滚动区 + 内容 body。"""

    back_requested = Signal()
    config_changed = Signal()

    def __init__(self, config: dict, title: str, embedded: bool = False, parent=None,
                 subtitle: str = ""):
        super().__init__(parent)
        self.config = config
        self.embedded = embedded

        root = QVBoxLayout(self)
        root.setContentsMargins(0 if embedded else 16, 0 if embedded else 12,
                                0 if embedded else 16, 0 if embedded else 12)
        root.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        self.btn_back = IconButton(theme.ICON["back"], size=28, icon_size=9,
                                   tooltip=i18n.T("返回"))
        self.btn_back.clicked.connect(lambda *_: self.back_requested.emit())
        head.addWidget(self.btn_back, 0, Qt.AlignmentFlag.AlignTop)
        if embedded:
            self.btn_back.hide()

        title_col = QVBoxLayout()
        title_col.setContentsMargins(0, 0, 0, 0)
        title_col.setSpacing(2)
        lb = QLabel(title)
        lb.setFont(theme.ui_font(13, QFont.Weight.DemiBold))
        lb.setStyleSheet(f"color: {theme.C.TEXT.name()}; background: transparent;")
        title_col.addWidget(lb)
        if subtitle:
            sb = QLabel(subtitle)
            sb.setFont(theme.ui_font(7))
            sb.setWordWrap(True)
            sb.setStyleSheet(f"color: {theme.C.TEXT_TERTIARY.name()}; background: transparent;")
            title_col.addWidget(sb)
        head.addLayout(title_col, 1)

        self.head_right = QHBoxLayout()
        self.head_right.setContentsMargins(0, 4, 0, 0)
        self.head_right.setSpacing(8)
        head.addLayout(self.head_right, 0)
        root.addLayout(head)
        root.addSpacing(14)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea{background:transparent;}"
                                  "QScrollBar:vertical{width:6px;background:transparent;margin:2px 0;}"
                                  "QScrollBar::handle:vertical{background:rgba(0,0,0,60);border-radius:3px;min-height:24px;}"
                                  "QScrollBar::add-line, QScrollBar::sub-line{height:0;}")
        self.host = QWidget()
        self.host.setStyleSheet("background: transparent;")
        self.body = QVBoxLayout(self.host)
        self.body.setContentsMargins(0, 0, 10, 8)
        self.body.setSpacing(14)
        self.scroll.setWidget(self.host)
        root.addWidget(self.scroll, 1)

    # ------------------------------------------------------------------ #
    def reload(self, config: dict):
        self.config = config
        self._reload()

    def _reload(self):                             # pragma: no cover - 子类实现
        raise NotImplementedError

    def _commit(self):
        save_config(self.config)
        self.config_changed.emit()

    def _row(self, label: str, widget: QWidget, hint: str = "") -> QVBoxLayout:
        """「左标签 + 右控件」的一行，可带右对齐的灰色说明。"""
        col = QVBoxLayout()
        col.setContentsMargins(2, 2, 2, 6)
        col.setSpacing(2)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        lb = QLabel(label)
        lb.setFont(theme.ui_font(9))
        lb.setStyleSheet(f"color: {theme.C.TEXT_SECONDARY.name()};")
        row.addWidget(lb)
        row.addStretch(1)
        row.addWidget(widget)
        col.addLayout(row)
        if hint:
            tip = QLabel(hint)
            tip.setFont(theme.ui_font(7))
            tip.setStyleSheet(f"color: {theme.C.TEXT_TERTIARY.name()};")
            tip.setAlignment(Qt.AlignmentFlag.AlignRight)
            tip.setWordWrap(True)                  # 长说明必须换行，否则会把整页顶宽
            tip.setMinimumWidth(0)
            col.addWidget(tip)
        return col

    def _divider(self) -> QFrame:
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background: rgba(0,0,0,22); border: none;")
        return line

    def preferred_height(self) -> int:
        """页面内容高度（不含面板外框留白，由 Flyout._fit 叠加）。"""
        if self.host.layout() is not None:
            self.host.layout().activate()
        hint = self.host.sizeHint().height()
        if hint <= 0:
            return 640
        return hint + 12 + 28 + 6 + 12


def _desktop_wallpaper() -> QPixmap | None:
    """读当前桌面壁纸；纯色壁纸时注册表里是空串，返回 None 由预览自己铺底。"""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop") as key:
            path = str(winreg.QueryValueEx(key, "Wallpaper")[0] or "")
        if path and os.path.exists(path):
            pm = QPixmap(path)
            if not pm.isNull():
                return pm
    except Exception:                              # noqa: BLE001
        pass
    return None


class PanelPreview(QWidget):
    """「面板不透明度」的实时预览。

    底色走 `theme.panel_fill()`——和真实弹出面板**同一个函数**，所以预览里
    看到的通透程度就是托盘面板的实际情况，不是另做一套近似值。
    背景优先用当前桌面壁纸（读不到就用中性灰 + 两团色块），这样降低不透明度
    时能直观看到「面板下面透出了什么」，而不是只看到白色变浅。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._material = "mica"
        self._opacity = 100
        self._wallpaper = _desktop_wallpaper()
        self.setFixedHeight(138)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 13, 0, 13)
        row.setSpacing(0)
        row.addStretch(1)

        self.card = Surface(radius=10)
        self.card.setFixedWidth(300)
        self.card.setFixedHeight(86)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 7)
        shadow.setColor(QColor(0, 0, 0, 48))
        self.card.setGraphicsEffect(shadow)
        row.addWidget(self.card, 3, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)

        body = QVBoxLayout(self.card)
        body.setContentsMargins(14, 11, 14, 11)
        body.setSpacing(2)
        body.addStretch(1)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        self.name = QLabel("MCHOSE A7 V3 Pro")
        self.name.setFont(theme.ui_font(9, QFont.Weight.DemiBold))
        self.name.setStyleSheet(f"color: {theme.C.TEXT.name()}; background: transparent;")
        self.percent = QLabel("86%")
        self.percent.setFont(theme.ui_font(10, QFont.Weight.DemiBold))
        self.percent.setStyleSheet(f"color: {theme.C.TEXT.name()}; background: transparent;")
        top.addWidget(self.name)
        top.addStretch(1)
        top.addWidget(self.percent)
        body.addLayout(top)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(8)
        self.sub = QLabel(i18n.T("2.4G 无线 · 示意数据"))
        self.sub.setFont(theme.ui_font(8))
        self.sub.setStyleSheet(f"color: {theme.C.TEXT_TERTIARY.name()}; background: transparent;")
        self.bar = BatteryBar(width=52, height=4)
        self.bar.set_state(86)
        bottom.addWidget(self.sub)
        bottom.addStretch(1)
        bottom.addWidget(self.bar, 0, Qt.AlignmentFlag.AlignVCenter)
        body.addLayout(bottom)
        body.addStretch(1)

        self.apply(self._material, self._opacity)

    # ------------------------------------------------------------------ #
    def apply(self, material: str, opacity: int):
        self._material = material if material in motion.MATERIALS else "solid"
        self._opacity = int(opacity)
        self.card.set_fill(theme.panel_fill(self._material, self._opacity))
        self.card.update()

    def paintEvent(self, event):                   # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0, 0, 0, 0)
        path = QPainterPath()
        path.addRoundedRect(rect.adjusted(0, 4, 0, -4), 8, 8)
        p.setClipPath(path)

        # 底层：桌面壁纸；没有壁纸就铺一层中性灰
        inner = rect.adjusted(0, 4, 0, -4)
        if self._wallpaper is not None:
            pm = self._wallpaper.scaled(inner.size().toSize(),
                                        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                        Qt.TransformationMode.SmoothTransformation)
            sx = max(0, (pm.width() - int(inner.width())) // 2)
            sy = max(0, (pm.height() - int(inner.height())) // 2)
            p.drawPixmap(inner, pm, QRectF(sx, sy, inner.width(), inner.height()))
        else:
            p.fillRect(inner, QColor("#E6E6EC"))

        # 面板下面「压着」的东西：两团低饱和块，降低不透明度时能看到它们透出来
        p.setPen(Qt.PenStyle.NoPen)
        w, h = inner.width(), inner.height()
        for x0, y0, x1, y1, color in ((0.03, 0.10, 0.20, 0.90, QColor(255, 255, 255, 62)),
                                      (0.78, 0.14, 0.97, 0.62, QColor(90, 120, 200, 46)),
                                      (0.10, 0.62, 0.30, 0.88, QColor(0, 0, 0, 22))):
            p.setBrush(color)
            p.drawRoundedRect(QRectF(inner.left() + w * x0, inner.top() + h * y0,
                                     w * (x1 - x0), h * (y1 - y0)), 7, 7)


class GeneralSettingsPage(SettingsPageBase):
    """区域一：通用设置（外观材质 / 动效 / 面板不透明度 / 语言 / 刷新间隔 / 开机自启）。"""

    language_changed = Signal()
    appearance_changed = Signal()

    #: 材质键 → 显示名（Mica / Acrylic 是官方叫法，不翻译）
    MATERIAL_NAMES = {"solid": "纯色", "mica": "Mica", "acrylic": "Acrylic"}

    def __init__(self, config: dict, embedded: bool = False, parent=None):
        super().__init__(config, i18n.T("通用设置"), embedded, parent,
                         subtitle=i18n.T("窗口材质、动效，以及刷新与开机行为"))

        # ---------------- 模块一：外观与动效 ---------------- #
        look = SectionCard(i18n.T("外观与动效"), "brush",
                           i18n.T("窗口材质与界面动效，改动立即生效"), tone="violet")
        self.material_badge = look.header_widget(Badge(i18n.T("纯色"), "neutral"))

        self.material_seg = Segmented([("solid", i18n.T("纯色")),
                                       ("mica", "Mica"),
                                       ("acrylic", "Acrylic")])
        self.material_seg.setFixedWidth(236)
        self.material_seg.changed.connect(self._on_material)
        look.body.addLayout(self._row(
            i18n.T("窗口材质"), self.material_seg,
            i18n.T("Mica 取壁纸的淡色，Acrylic 更明显；需要 Windows 11")))

        # 面板不透明度：滑块拖动即时生效，落盘做 320ms 合并，避免连续拖动反复写盘
        self.opacity_slider = FluentSlider(minimum=motion.OPACITY_MIN,
                                           maximum=motion.OPACITY_MAX, value=100)
        self.opacity_slider.setFixedWidth(172)
        # 用 QLineEdit 而不是 QSpinBox：样式表一旦接管 QSpinBox，它的文本区会被算成
        # 1px 宽（Qt 对 sub-control 的布局计算问题），数字直接看不见。
        self.opacity_value = QLineEdit(str(motion.panel_opacity()))
        self.opacity_value.setValidator(QIntValidator(motion.OPACITY_MIN,
                                                      motion.OPACITY_MAX, self))
        self.opacity_value.setAlignment(Qt.AlignmentFlag.AlignRight |
                                        Qt.AlignmentFlag.AlignVCenter)
        self.opacity_value.setFont(theme.ui_font(9))
        self.opacity_value.setFixedWidth(46)
        self.opacity_value.setStyleSheet(INPUT_QSS + "QLineEdit{padding:3px 6px;}")
        self.opacity_value.editingFinished.connect(self._on_opacity_typed)
        self.opacity_slider.valueChanged.connect(self._on_opacity)

        pct = QLabel("%")
        pct.setFont(theme.ui_font(9))
        pct.setStyleSheet(f"color: {theme.C.TEXT_TERTIARY.name()};")

        opacity_box = QWidget()
        opacity_row = QHBoxLayout(opacity_box)
        opacity_row.setContentsMargins(0, 0, 0, 0)
        opacity_row.setSpacing(6)
        opacity_row.addWidget(self.opacity_slider)
        opacity_row.addWidget(self.opacity_value)
        opacity_row.addWidget(pct)
        look.body.addLayout(self._row(
            i18n.T("电量面板不透明度"), opacity_box,
            i18n.T("拖动即预览；低于 50% 时次要文字会变淡，投影始终保持不透明")))

        self.preview = PanelPreview()
        look.body.addWidget(self.preview)

        # 拖动时每帧都会走 _on_opacity，落盘用一次性定时器合并
        self._opacity_save = QTimer(self)
        self._opacity_save.setSingleShot(True)
        self._opacity_save.setInterval(320)
        self._opacity_save.timeout.connect(self._flush_opacity)

        self.motion_seg = Segmented([("full", i18n.T("标准")),
                                     ("subtle", i18n.T("柔和")),
                                     ("off", i18n.T("无动效"))])
        self.motion_seg.setFixedWidth(206)
        self.motion_seg.changed.connect(self._on_motion)
        look.body.addLayout(self._row(
            i18n.T("动效强度"), self.motion_seg,
            i18n.T("标准约 250ms，柔和减半，关闭则界面瞬时切换")))

        self.hover_switch = ToggleSwitch(True)
        self.hover_switch.toggled.connect(
            lambda checked: self._set_ui("hover", bool(checked)))
        look.body.addLayout(self._row(
            i18n.T("悬停与按压反馈"), self.hover_switch,
            i18n.T("按钮与列表行在鼠标移入时渐显底色")))

        self.transition_switch = ToggleSwitch(True)
        self.transition_switch.toggled.connect(
            lambda checked: self._set_ui("transitions", bool(checked)))
        look.body.addLayout(self._row(
            i18n.T("窗口与页面过渡"), self.transition_switch,
            i18n.T("弹出面板淡入淡出，切换标签页时淡入")))

        self.material_note = QLabel("")
        self.material_note.setWordWrap(True)
        self.material_note.setFont(theme.ui_font(7))
        self.material_note.setStyleSheet(
            f"color: {theme.C.TEXT_TERTIARY.name()}; padding: 2px 2px 0 2px;")
        look.body.addWidget(self.material_note)
        self.body.addWidget(look)

        card = SectionCard(i18n.T("刷新与启动"), "settings",
                           i18n.T("设备清单的刷新节奏与开机行为"), tone="slate")

        # 语言：中文原文作键的轻量 i18n，切换后由主程序整窗重建
        self.combo_lang = QComboBox()
        self.combo_lang.setStyleSheet(INPUT_QSS)
        self.combo_lang.setFont(theme.ui_font(9))
        self.combo_lang.setFixedWidth(104)
        for code, name in i18n.LANGUAGES:
            self.combo_lang.addItem(name, code)
        self.combo_lang.currentIndexChanged.connect(self._on_language)
        card.body.addLayout(self._row("语言 / Language", self.combo_lang,
                                      i18n.T("切换后立即生效")))

        self.interval = QComboBox()
        self.interval.setStyleSheet(INPUT_QSS)
        self.interval.setFont(theme.ui_font(9))
        self.interval.setFixedWidth(104)
        for label, sec in (("30 秒", 30), ("1 分钟", 60), ("2 分钟", 120), ("5 分钟", 300)):
            self.interval.addItem(i18n.T(label), sec)
        self.interval.currentIndexChanged.connect(self._on_interval)
        card.body.addLayout(self._row(i18n.T("刷新间隔"), self.interval,
                                      i18n.T("设备清单与电量读数一起按此间隔刷新")))

        self.autostart_switch = ToggleSwitch(autostart.is_enabled())
        self.autostart_switch.toggled.connect(self._on_autostart)
        card.body.addLayout(self._row(i18n.T("开机自动启动"), self.autostart_switch,
                                      i18n.T("登录 Windows 后自动驻留托盘")))

        card.body.addWidget(self._divider())
        self.btn_reset = FlatButton(i18n.T("恢复默认"), glyph=theme.ICON["refresh"])
        self.btn_reset.clicked.connect(lambda *_: self._reset())
        card.body.addLayout(self._row(i18n.T("恢复默认设置"), self.btn_reset,
                                      i18n.T("清空接口配置与偏好设置，回到出厂状态")))

        self.body.addWidget(card)
        self.body.addStretch(1)
        self.reload(config)

    def _reload(self):
        lang_idx = self.combo_lang.findData(self.config.get("language", "zh"))
        self.combo_lang.blockSignals(True)
        self.combo_lang.setCurrentIndex(max(0, lang_idx))
        self.combo_lang.blockSignals(False)

        idx = self.interval.findData(int(self.config.get("refresh_interval", 60)))
        self.interval.blockSignals(True)
        self.interval.setCurrentIndex(idx if idx >= 0 else 1)
        self.interval.blockSignals(False)

        # 开机自启也可能被托盘右键菜单改过，这里始终重读注册表
        self.autostart_switch.blockSignals(True)
        self.autostart_switch.setChecked(autostart.is_enabled())
        self.autostart_switch.blockSignals(False)

        self._sync_look()

    # ------------------------------------------------------------------ #
    #: 外观与动效
    def _sync_look(self):
        ui = self.config.get("ui") or {}
        for seg, key, fallback in ((self.material_seg, "material", "mica"),
                                   (self.motion_seg, "motion", "full")):
            seg.blockSignals(True)
            seg.set_current(str(ui.get(key) or fallback), emit=False)
            seg.blockSignals(False)
        for switch, key in ((self.hover_switch, "hover"),
                            (self.transition_switch, "transitions")):
            switch.blockSignals(True)
            switch.setChecked(bool(ui.get(key, True)))
            switch.blockSignals(False)
        # 以配置为准（页面重建/切语言后仍显示配置里的值），并把它回写给全局，
        # 避免「界面显示 57% 但面板还是 100%」这种状态分叉。
        opacity = motion.clamp_opacity(ui.get("panel_opacity", motion.panel_opacity()))
        motion.set_panel_opacity(opacity)
        self.opacity_slider.blockSignals(True)
        self.opacity_slider.setValue(opacity)
        self.opacity_slider.blockSignals(False)
        self.opacity_value.setText(str(opacity))
        self.preview.apply(motion.material(), opacity)
        self._sync_material_badge()

    # ------------------------------------------------------------------ #
    #: 面板不透明度
    def _on_opacity(self, value: int):
        """拖动/输入时即时生效：内存 + 预览 + 真实面板，落盘延后合并。"""
        value = motion.set_panel_opacity(value)
        if self.opacity_slider.value() != value:
            self.opacity_slider.blockSignals(True)
            self.opacity_slider.setValue(value)
            self.opacity_slider.blockSignals(False)
        if self.opacity_value.text() != str(value):
            self.opacity_value.setText(str(value))
        self.preview.apply(motion.material(), value)
        self._opacity_save.start()

    def _on_opacity_typed(self):
        """手输数值：回车/失焦时夹取到合法区间，非法输入退回当前值。"""
        try:
            value = motion.clamp_opacity(int(self.opacity_value.text().strip()))
        except ValueError:
            value = motion.panel_opacity()
        self._on_opacity(value)

    def _flush_opacity(self):
        """停手 320ms 后落盘并通知主程序；值没变就不写。"""
        ui = self.config.setdefault("ui", {})
        value = motion.panel_opacity()
        if ui.get("panel_opacity") == value:
            return
        ui["panel_opacity"] = value
        try:
            save_config(self.config)
        except Exception:                          # noqa: BLE001
            pass
        self.appearance_changed.emit()

    def _sync_material_badge(self):
        wanted = motion.material()
        supported, why = backdrop.describe()
        if wanted != "solid" and not supported:
            self.material_badge.setText(i18n.T("已回退纯色"), "warn")
            self.material_note.setText(
                i18n.TF("当前系统不支持窗口材质（{}），按纯色显示", why))
            return
        name = i18n.T(self.MATERIAL_NAMES.get(wanted, "纯色"))
        self.material_badge.setText(name, "neutral" if wanted == "solid" else "accent")
        self.material_note.setText(i18n.T(
            "材质由 Windows 11 提供；弹出面板跟随同一套通透度，不做二次模糊"))

    def _set_ui(self, key: str, value):
        """只改外观偏好：直接落盘 + 通知界面重贴材质，不触发设备重扫。"""
        ui = self.config.setdefault("ui", {})
        if ui.get(key) == value:
            return
        ui[key] = value
        # 滑块可能刚被拖到新值但还没落盘，configure 会按配置整体覆盖，先接住它
        ui["panel_opacity"] = motion.panel_opacity()
        motion.configure(ui)
        try:
            save_config(self.config)
        except Exception:                          # noqa: BLE001
            pass
        self._sync_look()
        self.appearance_changed.emit()

    def _on_material(self, key: str):
        self._set_ui("material", key)

    def _on_motion(self, key: str):
        self._set_ui("motion", key)

    def _on_interval(self, _index: int):
        self.config["refresh_interval"] = int(self.interval.currentData())
        self._commit()

    def _on_autostart(self, checked: bool):
        if autostart.set_enabled(bool(checked)):
            return
        # 写注册表失败：把开关拨回去，避免界面与实际不一致
        self.autostart_switch.blockSignals(True)
        self.autostart_switch.setChecked(not checked)
        self.autostart_switch.blockSignals(False)
        self.autostart_switch.setToolTip(i18n.T("写入注册表失败，可能被安全软件拦截"))

    def _reset(self):
        old_lang = self.config.get("language", "zh")
        self._opacity_save.stop()                  # 别让重置前那次拖动在重置后又写回去
        self.config.clear()
        self.config.update(json.loads(json.dumps(default_config())))
        motion.configure(self.config.get("ui"))
        if self.config.get("language", "zh") != old_lang:
            i18n.set_language(self.config["language"])
            self.language_changed.emit()
        self.reload(self.config)
        self._commit()
        self.appearance_changed.emit()

    def _on_language(self, _index: int):
        code = self.combo_lang.currentData()
        if not code or code == self.config.get("language"):
            return
        self.config["language"] = code
        i18n.set_language(code)
        try:
            save_config(self.config)
        except Exception:                          # noqa: BLE001
            pass
        self.language_changed.emit()


class InterfaceSettingsPage(SettingsPageBase):
    """接口设置：四个模块各占一张卡片——接口设置 / 设备接入 / 设备图标 / 接口调试。

    每张卡片用「标识瓦片 + 标题层级 + 主题色描边」区分模块，卡片间距 14px；
    卡片内部再用品胶囊、步骤面板、列表行形成第二层结构，避免内容糊成一片。
    """

    prompt_requested = Signal()
    icons_changed = Signal()

    def __init__(self, config: dict,
                 devices_provider: Optional[Callable[[], List[dict]]] = None,
                 embedded: bool = True, parent=None):
        self._devices_provider = devices_provider or (lambda: [])
        super().__init__(config, i18n.T("接口设置"), embedded, parent,
                         subtitle=i18n.T("协议插件决定怎么读电量，接入包让新设备一次装好，图标与调试也在本页完成"))

        self.summary = Badge("—", "neutral")
        self.head_right.addWidget(self.summary, 0, Qt.AlignmentFlag.AlignVCenter)

        # ---------------- 模块一：接口设置 ---------------- #
        iface = SectionCard(i18n.T("接口设置"), "link",
                            i18n.T("每套接口 = 一个厂商协议 + 一组参数，可同时启用多个"),
                            tone="blue")
        self.iface_badge = iface.header_widget(Badge("—", "accent"))

        self.profile_box = QVBoxLayout()
        self.profile_box.setContentsMargins(0, 0, 0, 0)
        self.profile_box.setSpacing(4)
        iface.body.addLayout(self.profile_box)

        self.profile_empty = QLabel(
            i18n.T("还没有接口配置：点下面的「新增接口」，或到「设备接入」导入一个接入包"))
        self.profile_empty.setWordWrap(True)
        self.profile_empty.setFont(theme.ui_font(7))
        self.profile_empty.setStyleSheet(
            f"color: {theme.C.TEXT_TERTIARY.name()}; padding: 12px 2px;")
        self.profile_empty.hide()
        iface.body.addWidget(self.profile_empty)

        self.profile_divider = self._divider()
        iface.body.addWidget(self.profile_divider)

        add_row = QHBoxLayout()
        add_row.setContentsMargins(0, 2, 0, 0)
        add_row.setSpacing(8)
        self.btn_add = FlatButton(i18n.T("新增接口"), glyph=theme.ICON["add"])
        self.btn_add.clicked.connect(lambda *_: self._add_profile())
        add_row.addWidget(self.btn_add, 0, Qt.AlignmentFlag.AlignVCenter)
        add_row.addStretch(1)
        add_hint = QLabel(i18n.T("关掉开关的接口不参与读取，配置仍会保留"))
        add_hint.setFont(theme.ui_font(7))
        add_hint.setStyleSheet(f"color: {theme.C.TEXT_TERTIARY.name()};")
        add_row.addWidget(add_hint, 0, Qt.AlignmentFlag.AlignVCenter)
        iface.body.addLayout(add_row)

        # ---------------- 模块二：设备接入 ---------------- #
        acc = SectionCard(i18n.T("设备接入"), "device",
                          i18n.T("给还没适配的设备生成接入包，插件代码由 AI 写"),
                          tone="teal")
        acc.body.addWidget(self._steps_panel([
            (i18n.T("检测设备"),
             i18n.T("点「检测设备并生成提示词」，工具会读出这台设备的接口集合与读取要点")),
            (i18n.T("交给 AI"),
             i18n.T("把提示词粘给任意大模型，它会返回一个 .json 接入包")),
            (i18n.T("导入接入包"),
             i18n.T("插件与接口配置自动装好，无需手动改文件")),
        ], theme.C.MODULE["teal"]))

        newdev = QHBoxLayout()
        newdev.setContentsMargins(0, 2, 0, 0)
        newdev.setSpacing(8)
        self.btn_new = FlatButton(i18n.T("检测设备并生成提示词"),
                                  glyph=theme.ICON["device"], primary=True)
        self.btn_new.clicked.connect(lambda *_: self.prompt_requested.emit())
        self.btn_import = FlatButton(i18n.T("导入接入包"), glyph=theme.ICON["upload"])
        self.btn_import.clicked.connect(lambda *_: self._pick_bundle())
        newdev.addWidget(self.btn_new)
        newdev.addWidget(self.btn_import)
        newdev.addStretch(1)
        acc.body.addLayout(newdev)

        self.import_banner = Banner(tone="ok")
        self.import_hint = self.import_banner.label
        self.import_banner.hide()
        acc.body.addWidget(self.import_banner)

        # ---------------- 模块三：设备图标 ---------------- #
        icons = SectionCard(i18n.T("设备图标"), "image",
                            i18n.T("图标由你自己上传，主面板与托盘都会用它"),
                            tone="violet")
        self.icon_badge = icons.header_widget(Badge("—", "neutral"))

        self.icon_box = QVBoxLayout()
        self.icon_box.setContentsMargins(0, 0, 0, 0)
        self.icon_box.setSpacing(4)
        icons.body.addLayout(self.icon_box)

        self.icon_hint = QLabel("")
        self.icon_hint.setWordWrap(True)
        self.icon_hint.setFont(theme.ui_font(7))
        self.icon_hint.setStyleSheet(f"color: {theme.C.TEXT_TERTIARY.name()}; padding: 2px 2px 0 2px;")
        icons.body.addWidget(self.icon_hint)

        # ---------------- 模块四：接口调试 ---------------- #
        dbg = SectionCard(i18n.T("接口调试"), "terminal",
                          i18n.T("手动读一次私有集合，结果可复制给 AI 分析"),
                          tone="amber")
        self.probe_panel = ProbePanel(lambda: self.config)
        dbg.header_widget(self.probe_panel.status)
        dbg.body.addWidget(self.probe_panel)

        # ---------------- 页脚：数据与工具（低强调，不抢模块层级） ---------------- #
        foot = QWidget()
        foot.setStyleSheet("background: transparent;")
        flay = QVBoxLayout(foot)
        flay.setContentsMargins(0, 0, 0, 0)
        flay.setSpacing(8)
        flay.addWidget(self._divider())

        tools = QHBoxLayout()
        tools.setContentsMargins(0, 0, 0, 0)
        tools.setSpacing(8)
        self.btn_example = FlatButton(i18n.T("导出示例接入包"))
        self.btn_example.clicked.connect(lambda *_: self._export_example())
        self.btn_folder = FlatButton(i18n.T("打开数据目录"), glyph=theme.ICON["folder"])
        self.btn_folder.clicked.connect(lambda *_: self._open_folder())
        tools.addWidget(self.btn_example)
        tools.addWidget(self.btn_folder)
        tools.addStretch(1)
        flay.addLayout(tools)

        self.data_hint = QLabel(self._data_hint_text())
        self.data_hint.setWordWrap(True)
        self.data_hint.setFont(theme.ui_font(7))
        self.data_hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.data_hint.setStyleSheet(f"color: {theme.C.TEXT_TERTIARY.name()}; padding: 0 2px;")
        flay.addWidget(self.data_hint)

        for card in (iface, acc, icons, dbg):
            self.body.addWidget(card)
        self.body.addWidget(foot)
        self.body.addStretch(1)
        self.reload(config)

    # ------------------------------------------------------------------ #
    @staticmethod
    def _steps_panel(steps, color: QColor) -> QWidget:
        """三步行内联引导：浅色底 + 圆形序号，把「怎么接入一台新设备」讲成流程。"""
        panel = Surface(radius=8, fill=theme.tint(color, 12), border=theme.tint(color, 38))
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(7)
        num_qss = (f"color: {color.name()};"
                   f"background: {theme.tint(color, 40).name(QColor.NameFormat.HexArgb)};"
                   "border-radius: 9px;")
        for index, (title, detail) in enumerate(steps, 1):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(9)
            num = QLabel(str(index))
            num.setFixedSize(18, 18)
            num.setAlignment(Qt.AlignmentFlag.AlignCenter)
            num.setFont(theme.ui_font(7, QFont.Weight.DemiBold))
            num.setStyleSheet(num_qss)
            row.addWidget(num, 0, Qt.AlignmentFlag.AlignTop)
            txt = QLabel(f"<b>{title}</b>&nbsp;&nbsp;{detail}")
            txt.setFont(theme.ui_font(7))
            txt.setWordWrap(True)
            txt.setStyleSheet(f"color: {theme.C.TEXT_SECONDARY.name()};")
            row.addWidget(txt, 1)
            lay.addLayout(row)
        return panel

    # ------------------------------------------------------------------ #
    def _reload(self):
        while self.profile_box.count():
            item = self.profile_box.takeAt(0)
            w = item.widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()

        for prof in self.config.get("profiles", []):
            row = ProfileRow(prof)
            row.edited.connect(self._edit_profile)
            row.toggled.connect(self._toggle_profile)
            row.removed.connect(self._remove_profile)
            self.profile_box.addWidget(row)

        self._reload_icons()
        self._show_plugin_errors()
        self._update_badges()

    def _update_badges(self):
        """数量和状态都放到胶囊里，让每个模块自己说明「现在是什么情况」。"""
        profs = self.config.get("profiles", [])
        on = sum(1 for p in profs if p.get("enabled", True))
        self.iface_badge.setText(i18n.TF("{}/{} 已启用", on, len(profs)),
                                 "accent" if on else "neutral")
        self.profile_empty.setVisible(not profs)
        self.profile_divider.setVisible(bool(profs))
        self.summary.setText(i18n.TF("{} 个协议插件 · {} 套接口",
                                     len(all_protocols()), len(profs)))

    def _show_plugin_errors(self):
        """用户插件目录里加载失败的插件，直接在设置页提示出来。"""
        errors = plugin_store.errors()
        if not errors:
            if self.import_hint.text().startswith("以下插件加载失败"):
                self.import_banner.clear()
            return
        self._set_import_hint(i18n.TF("以下插件加载失败（可修正后重启）：{}",
                                      "；".join(errors[:3])), error=True)

    def _reload_icons(self):
        while self.icon_box.count():
            item = self.icon_box.takeAt(0)
            w = item.widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()

        devices = list(self._devices_provider() or [])
        self._icon_count = len(devices)
        if devices:
            for dev in devices:
                row = IconRow(dev)
                row.upload_requested.connect(self._upload_icon)
                row.clear_requested.connect(self._clear_icon)
                self.icon_box.addWidget(row)
        else:
            empty = QLabel(i18n.T("暂无可设置图标的设备：先让主面板检测到设备再回到这里"))
            empty.setWordWrap(True)
            empty.setFont(theme.ui_font(7))
            empty.setStyleSheet(f"color: {theme.C.TEXT_TERTIARY.name()}; padding: 8px 2px;")
            self.icon_box.addWidget(empty)

        count = icon_store.count()
        self.icon_badge.setText(i18n.TF("{} 张已上传", count),
                                "accent" if count else "neutral")
        self.icon_hint.setText(
            i18n.T("支持 PNG/JPG，自动裁到 128px，存在本机 assets/icons/；未上传的设备不显示图标。"))

    # ------------------------------------------------------------------ #
    def _upload_icon(self, key: str):
        path, _ = QFileDialog.getOpenFileName(
            self, i18n.T("选择设备图标"), "",
            i18n.T("图片 (*.png *.jpg *.jpeg *.webp *.bmp *.ico)"))
        if not path:
            return
        try:
            icon_store.save(key, path)
        except Exception as exc:                   # noqa: BLE001
            self.icon_hint.setText(i18n.TF("上传失败：{}", exc))
            return
        self._reload_icons()
        self.icons_changed.emit()

    def _clear_icon(self, key: str):
        if icon_store.clear(key):
            self._reload_icons()
            self.icons_changed.emit()

    def _toggle_profile(self, pid: str, enabled: bool):
        for prof in self.config.get("profiles", []):
            if prof.get("id") == pid:
                prof["enabled"] = enabled
                break
        self._commit()

    def _remove_profile(self, pid: str):
        self.config["profiles"] = [p for p in self.config.get("profiles", []) if p.get("id") != pid]
        self.reload(self.config)
        self._commit()

    def _add_profile(self):
        protos = all_protocols()
        if not protos:
            return
        current = next((p.get("protocol") for p in self.config.get("profiles", [])
                        if p.get("enabled", True)), "")
        dlg = ProtocolPicker(protos, current, self.window())
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        proto = dlg.selected()
        if proto is None:
            return
        profile = new_profile(proto.id, index=len(self.config.get("profiles", [])))
        self._open_editor(profile, is_new=True)

    def _edit_profile(self, pid: str):
        for prof in self.config.get("profiles", []):
            if prof.get("id") == pid:
                self._open_editor(copy.deepcopy(prof), is_new=False)
                return

    def _open_editor(self, profile: dict, is_new: bool):
        dlg = ProfileDialog(profile, self.window())
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.result_profile()
            existing = {p.get("id"): i for i, p in enumerate(self.config.get("profiles", []))}
            if not is_new and profile.get("id") in existing:
                self.config["profiles"][existing[profile["id"]]] = data
            else:
                self.config.setdefault("profiles", []).append(data)
            self.reload(self.config)
            self._commit()

    # ------------------------------------------------------------------ #
    def _set_import_hint(self, text: str, error: bool = False):
        self.import_banner.set_message(text, "danger" if error else "ok")

    def _pick_bundle(self):
        path, _ = QFileDialog.getOpenFileName(
            self, i18n.T("选择 AI 接入包"), "",
            i18n.T("接入包 (*.json *.py);;JSON 配置 (*.json);;协议插件 (*.py);;所有文件 (*.*)"))
        if path:
            self.import_bundle(path)

    def import_bundle(self, path: str) -> bool:
        """导入 .json 接入包 / .py 插件；成功后立刻刷新接口列表。"""
        try:
            info = plugin_store.import_file(path, self.config)
        except plugin_store.BundleError as exc:
            self._set_import_hint(i18n.TF("导入失败：{}", exc), error=True)
            return False
        except Exception as exc:                   # noqa: BLE001
            self._set_import_hint(i18n.TF("导入失败：{}", exc), error=True)
            return False

        self.reload(self.config)
        self.config_changed.emit()
        names = ("、".join(info.get("profiles") or [])
                 or i18n.T("（未附带接口配置，请手动新增接口）"))
        plugin = info.get("protocol") or "-"
        self._set_import_hint(i18n.TF("已导入：插件 {} · 接口 {}。\n若主面板还没出现设备，点一下刷新。",
                                      plugin, names))
        return True

    def _export_example(self):
        path = plugin_store.write_example(CONFIG_DIR)
        self._set_import_hint(i18n.TF("示例接入包已写入：{}", path))

    @staticmethod
    def _data_hint_text() -> str:
        """数据目录固定在 EXE 旁边；只有该目录不可写时才切到用户目录并说明原因。"""
        if USING_EXE_DIR:
            where = i18n.T("当前 EXE 所在路径") if IS_FROZEN \
                else i18n.T("程序所在目录（源码运行）")
            return i18n.TF("数据目录：{}（{}）\n用户插件：{}", DATA_DIR, where, PLUGIN_DIR)
        return (i18n.TF("数据目录：{}（{}）", DATA_DIR, DATA_DIR_NOTE) + "\n"
                + f"{i18n.T('程序目录：')}{EXE_DIR}\n"
                + f"{i18n.T('用户插件：')}{PLUGIN_DIR}")

    def _open_folder(self):
        os.startfile(DATA_DIR)                     # noqa: S606  (Windows 专用)


class ProtocolPicker(QDialog):
    """新增接口时先选一个协议插件。"""

    def __init__(self, protos, preselect: str = "", parent=None):
        super().__init__(parent)
        self._protos = protos
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setFixedWidth(420)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        surf = Surface(radius=12)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(36)
        shadow.setOffset(0, 10)
        shadow.setColor(QColor(0, 0, 0, 70))
        surf.setGraphicsEffect(shadow)
        outer.addWidget(surf)

        lay = QVBoxLayout(surf)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(8)

        title = QLabel(i18n.T("选择协议插件"))
        title.setFont(theme.ui_font(11, QFont.Weight.DemiBold))
        title.setStyleSheet(f"color: {theme.C.TEXT.name()};")
        lay.addWidget(title)

        self.combo = QComboBox()
        self.combo.setStyleSheet(INPUT_QSS)
        self.combo.setFont(theme.ui_font(9))
        for proto in protos:
            self.combo.addItem(f"{i18n.T(proto.label)} · {proto.id}", proto.id)
        if preselect:
            idx = self.combo.findData(preselect)
            if idx >= 0:
                self.combo.setCurrentIndex(idx)
        lay.addWidget(self.combo)

        self.desc = QLabel(i18n.T(protos[0].description))
        self.desc.setWordWrap(True)
        self.desc.setFont(theme.ui_font(8))
        self.desc.setStyleSheet(f"color: {theme.C.TEXT_TERTIARY.name()};")
        lay.addWidget(self.desc)
        self.combo.currentIndexChanged.connect(self._on_change)

        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = FlatButton(i18n.T("取消"))
        cancel.clicked.connect(lambda *_: self.reject())
        ok = FlatButton(i18n.T("下一步"), primary=True)
        ok.clicked.connect(lambda *_: self.accept())
        actions.addWidget(cancel)
        actions.addWidget(ok)
        lay.addLayout(actions)

    def _on_change(self, index: int):
        if 0 <= index < len(self._protos):
            self.desc.setText(i18n.T(self._protos[index].description))

    def selected(self):
        return get_protocol(self.combo.currentData())


class ProfileDialog(QDialog):
    """接口编辑对话框：字段由协议插件声明，自动生成。"""

    BASE_FIELDS = [
        ("name", "名称", "str"),
        ("vendor_id", "厂商 VID (十进制)", "int"),
        ("usage_pages", "Usage Page 列表", "intlist"),
        ("protocol", "协议插件", "choice"),
    ]

    def __init__(self, profile: dict, parent=None):
        super().__init__(parent)
        self.profile = copy.deepcopy(profile)
        self._editors: dict[str, QWidget] = {}

        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        surf = Surface(radius=12)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(40)
        shadow.setOffset(0, 12)
        shadow.setColor(QColor(0, 0, 0, 70))
        surf.setGraphicsEffect(shadow)
        outer.addWidget(surf)

        lay = QVBoxLayout(surf)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(8)

        title = QLabel(i18n.T("接口参数"))
        title.setFont(theme.ui_font(12, QFont.Weight.DemiBold))
        title.setStyleSheet(f"color: {theme.C.TEXT.name()};")
        lay.addWidget(title)

        form = QVBoxLayout()
        form.setSpacing(6)
        for key, label, kind in self.BASE_FIELDS:
            form.addLayout(self._make_row(key, i18n.T(label), kind))
        lay.addLayout(form)

        self.params_label = SectionLabel(i18n.T("协议参数"))
        lay.addWidget(self.params_label)
        self.param_box = QVBoxLayout()
        self.param_box.setSpacing(6)
        lay.addLayout(self.param_box)
        self._build_params()

        self.json_box = QPlainTextEdit()
        self.json_box.setStyleSheet(INPUT_QSS)
        self.json_box.setFont(theme.ui_font(8))
        self.json_box.setFixedHeight(150)
        self.json_box.setPlainText(json.dumps(self.profile, ensure_ascii=False, indent=2))
        self.json_box.hide()
        lay.addWidget(self.json_box)

        self.btn_json = FlatButton(i18n.T("高级参数 (JSON)"), glyph=theme.ICON["down"])
        self.btn_json.clicked.connect(lambda *_: self._toggle_json())
        lay.addWidget(self.btn_json, 0, Qt.AlignmentFlag.AlignLeft)

        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = FlatButton(i18n.T("取消"))
        cancel.clicked.connect(lambda *_: self.reject())
        ok = FlatButton(i18n.T("保存"), primary=True)
        ok.clicked.connect(lambda *_: self._accept())
        actions.addWidget(cancel)
        actions.addWidget(ok)
        lay.addLayout(actions)

        self.setFixedWidth(520)

    # ------------------------------------------------------------------ #
    def _make_row(self, key: str, label: str, kind: str):
        row = QHBoxLayout()
        row.setSpacing(10)
        lb = QLabel(label)
        lb.setFont(theme.ui_font(9))
        lb.setStyleSheet(f"color: {theme.C.TEXT_SECONDARY.name()};")
        lb.setFixedWidth(132)
        row.addWidget(lb)

        if kind == "choice":
            w = QComboBox()
            w.setStyleSheet(INPUT_QSS)
            w.setFont(theme.ui_font(9))
            for proto in all_protocols():
                w.addItem(i18n.T(proto.label), proto.id)
            idx = w.findData(self.profile.get(key, ""))
            w.setCurrentIndex(max(0, idx))
            w.currentIndexChanged.connect(lambda *_: self._on_protocol())
        elif kind == "bool":
            w = QComboBox()
            w.setStyleSheet(INPUT_QSS)
            w.setFont(theme.ui_font(9))
            w.addItem(i18n.T("是"), True)
            w.addItem(i18n.T("否"), False)
            idx = w.findData(bool(self.profile.get(key, False)))
            w.setCurrentIndex(max(0, idx))
        else:
            w = QLineEdit()
            w.setStyleSheet(INPUT_QSS)
            w.setFont(theme.ui_font(9))
            w.setText(self._format(key, kind))
        self._editors[key] = w
        row.addWidget(w, 1)
        return row

    def _build_params(self):
        while self.param_box.count():
            item = self.param_box.takeAt(0)
            layout = item.layout()
            if layout is not None:
                while layout.count():
                    child = layout.takeAt(0)
                    w = child.widget()
                    if w is not None:
                        w.hide()
                        w.setParent(None)
                        w.deleteLater()
                layout.deleteLater()
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        # 清掉已失效的编辑器引用
        self._editors = {k: v for k, v in self._editors.items()
                         if k in {f[0] for f in self.BASE_FIELDS}}

        proto = get_protocol(str(self._current_protocol()))
        if proto is None:
            self.params_label.setText(i18n.T("协议参数（插件未注册，请用 JSON 编辑）"))
            return
        self.params_label.setText(i18n.TF("协议参数 · {}", i18n.T(proto.label)))
        for param in proto.params:
            if param.advanced:
                continue
            self.param_box.addLayout(
                self._make_row(param.key, i18n.T(param.label), param.kind))

    def _current_protocol(self) -> str:
        w = self._editors.get("protocol")
        if isinstance(w, QComboBox):
            return str(w.currentData() or "")
        return str(self.profile.get("protocol", ""))

    def _on_protocol(self):
        proto = get_protocol(self._current_protocol())
        if proto is None:
            return
        for key, value in proto.defaults().items():
            self.profile.setdefault(key, value)
        self.profile["protocol"] = proto.id
        if not self.profile.get("usage_pages"):
            self.profile["usage_pages"] = list(proto.usage_pages)
        self._build_params()

    # ------------------------------------------------------------------ #
    def _format(self, key: str, kind: str) -> str:
        value = self.profile.get(key, "")
        if kind == "hex":
            try:
                number = int(value)
                return f"{number:#04x}" if number <= 0xFF else f"{number:#06x}"
            except Exception:                      # noqa: BLE001
                return str(value)
        if kind == "intlist":
            return ", ".join(str(v) for v in value) if isinstance(value, list) else str(value)
        return str(value)

    def _toggle_json(self):
        visible = not self.json_box.isVisible()
        self.json_box.setVisible(visible)
        self.btn_json.setText(i18n.T("收起高级参数") if visible
                              else i18n.T("高级参数 (JSON)"))
        self.btn_json._glyph = theme.ICON["up"] if visible else theme.ICON["down"]
        self.btn_json.update()
        self.adjustSize()
        if visible:
            self._collect()
            self.json_box.setPlainText(json.dumps(self.profile, ensure_ascii=False, indent=2))

    def _collect(self):
        for key, _label, kind in self.BASE_FIELDS:
            w = self._editors[key]
            if kind == "choice":
                self.profile[key] = w.currentData()
            elif kind == "int":
                try:
                    self.profile[key] = int(str(w.text()).strip())
                except ValueError:
                    pass
            elif kind == "intlist":
                parts = [p for p in str(w.text()).replace("，", ",").split(",") if p.strip()]
                values = []
                for part in parts:
                    try:
                        values.append(int(part.strip(), 0))
                    except ValueError:
                        pass
                if values:
                    self.profile[key] = values
            else:
                self.profile[key] = w.text().strip()

        proto = get_protocol(str(self.profile.get("protocol", "")))
        if proto is None:
            return
        for param in proto.params:
            w = self._editors.get(param.key)
            if w is None:
                continue
            if param.kind == "bool":
                self.profile[param.key] = bool(w.currentData())
            elif param.kind in ("int", "hex"):
                text = str(w.text()).strip()
                if not text:
                    continue
                try:
                    self.profile[param.key] = int(text, 0) if param.kind == "hex" else int(text)
                except ValueError:
                    pass
            elif param.kind == "intlist":
                parts = [p for p in str(w.text()).replace("，", ",").split(",") if p.strip()]
                try:
                    self.profile[param.key] = [int(p, 0) for p in parts]
                except ValueError:
                    pass
            else:
                self.profile[param.key] = w.text().strip()

    def _accept(self):
        if self.json_box.isVisible():
            try:
                data = json.loads(self.json_box.toPlainText())
            except Exception as exc:               # noqa: BLE001
                self.json_box.setStyleSheet(INPUT_QSS + "border: 1px solid #C42B1C;")
                self.json_box.setToolTip(i18n.TF("JSON 解析失败：{}", exc))
                return
            if not isinstance(data, dict):
                return
            self.profile = data
            self.accept()
            return

        self._collect()
        self.accept()

    def result_profile(self) -> dict:
        return self.profile
