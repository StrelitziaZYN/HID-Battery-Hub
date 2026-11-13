"""接入新设备：检测已连接设备 → 生成标准化提示词 → 导入 AI 返回的接入包。

界面与流程一一对应：
  ① 检测已连接的 HID 设备（下拉列表，可重新检测）
  ② 选中设备 → 生成提示词 → 复制给 AI Agent
  ③ AI 给出 JSON 接入包 → 点「导入接入包」直接装好
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QTextCursor
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QGraphicsDropShadowEffect,
                               QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QVBoxLayout,
                               QWidget)

from . import i18n, plugin_store, prompt_builder, scanner, theme
from .widgets import ElidedLabel, FlatButton, IconButton, Surface


class PromptDialog(QDialog):
    """设备接入提示词对话框。device 为空时只展示检测结果。"""

    bundle_imported = Signal()

    def __init__(self, config: Dict[str, Any], device: Optional[Dict[str, Any]] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.config = config
        self._devices: List[Dict[str, Any]] = []
        self._device: Dict[str, Any] = {}

        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setFixedWidth(660)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        surf = Surface(radius=12)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(40)
        shadow.setOffset(0, 12)
        shadow.setColor(QColor(0, 0, 0, 70))
        surf.setGraphicsEffect(shadow)
        outer.addWidget(surf)

        lay = QVBoxLayout(surf)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(6)

        # 标题
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        title = QLabel(i18n.T("接入新设备"))
        title.setFont(theme.ui_font(12, QFont.Weight.DemiBold))
        title.setStyleSheet(f"color: {theme.C.TEXT.name()};")
        head.addWidget(title)
        head.addStretch(1)
        close = IconButton(theme.ICON["close"], size=26, icon_size=8, tooltip=i18n.T("关闭"))
        close.clicked.connect(lambda *_: self.reject())
        head.addWidget(close)
        lay.addLayout(head)

        subtitle = QLabel(i18n.T("① 检测已连接设备　② 生成提示词交给 AI　③ 导入 AI 返回的接入包"))
        subtitle.setFont(theme.ui_font(8))
        subtitle.setStyleSheet(f"color: {theme.C.TEXT_TERTIARY.name()};")
        lay.addWidget(subtitle)
        lay.addSpacing(2)

        # ① 检测
        self.step1 = QLabel(i18n.T("① 已连接的 HID 设备"))
        self.step1.setFont(theme.ui_font(8, QFont.Weight.DemiBold))
        self.step1.setStyleSheet(f"color: {theme.C.TEXT_SECONDARY.name()};")
        lay.addWidget(self.step1)

        pick = QHBoxLayout()
        pick.setContentsMargins(0, 0, 0, 0)
        pick.setSpacing(6)
        self.combo = QComboBox()
        self.combo.setStyleSheet(theme.INPUT_QSS)
        self.combo.setFont(theme.ui_font(9))
        self.combo.setMinimumWidth(380)
        self.combo.currentIndexChanged.connect(lambda *_: self._on_pick())
        pick.addWidget(self.combo, 1)
        self.btn_detect = FlatButton(i18n.T("重新检测"), glyph=theme.ICON["refresh"])
        self.btn_detect.clicked.connect(lambda *_: self.detect(preselect_uid=self._device.get("uid")))
        pick.addWidget(self.btn_detect)
        lay.addLayout(pick)

        # 设备型号：名称是通用名（USB Receiver / 2.4G Dongle）时由用户补全，直接写进提示词
        model_row = QHBoxLayout()
        model_row.setContentsMargins(0, 2, 0, 0)
        model_row.setSpacing(6)
        label = QLabel(i18n.T("设备型号"))
        label.setFont(theme.ui_font(8))
        label.setStyleSheet(f"color: {theme.C.TEXT_SECONDARY.name()};")
        model_row.addWidget(label)
        self.model_edit = QLineEdit()
        self.model_edit.setFont(theme.ui_font(9))
        self.model_edit.setStyleSheet(theme.INPUT_QSS)
        self.model_edit.setPlaceholderText(
            i18n.T("名称看不出型号时填这里，例如 MCHOSE A7 V3 Pro；留空则由 AI 自行判断"))
        self.model_edit.setClearButtonEnabled(True)
        self.model_edit.textChanged.connect(lambda *_: self._rebuild())
        model_row.addWidget(self.model_edit, 1)
        lay.addLayout(model_row)

        self.summary = ElidedLabel("")
        self.summary.setFont(theme.ui_font(8))
        self.summary.setStyleSheet(f"color: {theme.C.TEXT_TERTIARY.name()}; padding: 2px 0 0 2px;")
        lay.addWidget(self.summary)
        lay.addSpacing(4)

        # ② 提示词
        step2_head = QHBoxLayout()
        step2_head.setContentsMargins(0, 0, 0, 0)
        step2 = QLabel(i18n.T("② 接入提示词"))
        step2.setFont(theme.ui_font(8, QFont.Weight.DemiBold))
        step2.setStyleSheet(f"color: {theme.C.TEXT_SECONDARY.name()};")
        step2_head.addWidget(step2)
        step2_head.addStretch(1)
        self.counter = QLabel("")
        self.counter.setFont(theme.ui_font(7))
        self.counter.setStyleSheet(f"color: {theme.C.TEXT_TERTIARY.name()};")
        step2_head.addWidget(self.counter)
        lay.addLayout(step2_head)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFixedHeight(322)
        mono = theme.ui_font(8)
        mono.setFamilies(["Cascadia Mono", "Consolas", "Microsoft YaHei UI"])
        self.text.setFont(mono)
        self.text.setStyleSheet(theme.INPUT_QSS + "QPlainTextEdit{padding:6px 8px;}")
        lay.addWidget(self.text)

        tip = QLabel(i18n.T("提示词只包含本机实测信息，并要求 AI 返回一个 JSON 接入包；"
                     "拿到后点「导入接入包」选中它，插件与接口配置会自动装好。"))
        tip.setWordWrap(True)
        tip.setFont(theme.ui_font(7))
        tip.setStyleSheet(f"color: {theme.C.TEXT_TERTIARY.name()}; padding: 2px;")
        lay.addWidget(tip)

        # 底部
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 4, 0, 0)
        self.hint = QLabel("")
        self.hint.setFont(theme.ui_font(8))
        self.hint.setStyleSheet(f"color: {theme.C.TEXT_TERTIARY.name()};")
        actions.addWidget(self.hint)
        actions.addStretch(1)
        self.btn_import = FlatButton(i18n.T("导入接入包"), glyph=theme.ICON["upload"])
        self.btn_import.clicked.connect(lambda *_: self._pick_bundle())
        actions.addWidget(self.btn_import)
        copy = FlatButton(i18n.T("复制提示词"), glyph=theme.ICON["link"], primary=True)
        copy.clicked.connect(lambda *_: self._copy())
        actions.addWidget(copy)
        lay.addLayout(actions)

        self.detect(initial=device)

    # ------------------------------------------------------------------ #
    def detect(self, preselect_uid: str = "", initial: Optional[Dict[str, Any]] = None):
        """第一步：枚举本机所有 HID 设备。"""
        self.hint.setText(i18n.T("正在检测…"))
        QGuiApplication.processEvents()
        self._devices = scanner.list_connected(self.config)
        self.combo.blockSignals(True)
        self.combo.clear()
        for idx, dev in enumerate(self._devices):
            mark = i18n.T("已支持") if dev.get("supported") else i18n.T("未接入")
            self.combo.addItem(f"{dev['name']} · {dev['vendor']} · {mark}", idx)

        target = None
        if initial:
            target = self._find(initial)
            if target is not None:
                # 只借用主面板已有的读数（避免重复查询），不据此假定「已支持」——
                # 是否已接入由本机接口配置决定，不是由面板数据决定
                target.update({k: initial[k] for k in
                               ("percent", "charging", "connection", "real_pid", "model")
                               if initial.get(k) is not None})
        elif preselect_uid:
            target = self._find({"uid": preselect_uid})

        self.step1.setText(i18n.TF("① 已连接的 HID 设备（{}）", len(self._devices)))
        if self._devices:
            idx = self._devices.index(target) if target in self._devices else 0
            self.combo.setCurrentIndex(max(0, idx))
            self.hint.setText(i18n.TF("检测到 {} 个 HID 设备", len(self._devices)))
        else:
            self.combo.blockSignals(False)
            self._device = {}
            self.text.setPlainText(
                i18n.T("未检测到任何 HID 设备，请插入接收器后点「重新检测」。"))
            self.summary.setText("")
            self.counter.setText("")
            self.hint.setText(i18n.T("没有可用设备"))
            return
        # 只在选定后生成一次，避免重复查询设备
        self.combo.blockSignals(False)
        self._on_pick()

    def _find(self, sample: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        vid = sample.get("vendor_id")
        pid = sample.get("product_id")
        product = sample.get("product_string") or sample.get("product") or ""
        for dev in self._devices:
            if vid is not None and dev.get("vendor_id") != vid:
                continue
            if pid is not None and dev.get("product_id") != pid:
                continue
            if product and dev.get("product_string") and dev["product_string"] != product:
                continue
            return dev
        return None

    def _on_pick(self):
        """第二步：选中设备 → 生成提示词。"""
        idx = self.combo.currentIndex()
        if not (0 <= idx < len(self._devices)):
            return
        dev = self._devices[idx]
        self.hint.setText(i18n.T("正在补全设备信息…"))
        QGuiApplication.processEvents()
        try:
            self._device = scanner.enrich(dev, self.config)
        except Exception:                          # noqa: BLE001
            self._device = dev

        d = self._device
        bits = [d.get("name"), d.get("vendor"), prompt_builder.conn_text(d)]
        self.summary.setText(" · ".join(b for b in bits if b))

        # 已知型号（从回包里学到的）预填，用户可随时改；改完立刻重算提示词
        self.model_edit.blockSignals(True)
        self.model_edit.setText(str(d.get("model") or ""))
        self.model_edit.blockSignals(False)
        self._rebuild()

    def _rebuild(self):
        """型号变了就重算提示词（纯字符串拼接，不碰设备）。"""
        d = self._device
        if not d:
            return
        model = self.model_edit.text().strip()
        prompt = prompt_builder.build(d, model=model)
        self.text.setPlainText(prompt)
        self.text.moveCursor(QTextCursor.MoveOperation.Start)
        self.counter.setText(i18n.TF("{} 行 · {} 字", len(prompt.splitlines()), len(prompt)))
        self.hint.setText(i18n.TF("提示词已按型号「{}」生成", model) if model
                          else i18n.T("提示词已生成"))

    def _copy(self):
        text = self.text.toPlainText()
        if not text:
            return
        QGuiApplication.clipboard().setText(text)
        self.hint.setText(i18n.T("已复制到剪贴板，粘贴给 AI Agent 即可"))

    def _pick_bundle(self):
        path, _ = QFileDialog.getOpenFileName(
            self, i18n.T("选择 AI 返回的接入包"), "",
            i18n.T("接入包 (*.json *.py);;JSON (*.json);;插件 (*.py);;所有文件 (*.*)"))
        if path:
            self.import_bundle(path)

    def import_bundle(self, path: str) -> bool:
        try:
            info = plugin_store.import_file(path, self.config)
        except Exception as exc:                   # noqa: BLE001
            self.hint.setText(i18n.TF("导入失败：{}", exc))
            self.hint.setStyleSheet(f"color: {theme.C.DANGER.name()};")
            return False

        names = ("、".join(info.get("profiles") or [])
                 or i18n.T("（未带接口配置，需在设置页新增接口）"))
        self.hint.setStyleSheet(f"color: {theme.C.OK.name()};")
        self.hint.setText(i18n.TF("已导入插件 {} · 接口 {}", info.get('protocol') or '-', names))
        self.bundle_imported.emit()
        self.detect(preselect_uid=self._device.get("uid"))
        return True

    def selected_device(self) -> Dict[str, Any]:
        return self._device
