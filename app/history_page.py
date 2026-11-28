"""电量统计页：设备选择 + 24 小时 / 7 天 / 30 天图表 + 续航估算 + 采样间隔 + 清除记录。

统计按「设备身份」聚合：同一台设备换 2.4G / 有线 / 蓝牙后仍是同一条曲线，
切换连接方式不会多出一条设备、也不会把同一次放电算两遍。

续航估算基于最近 30 天的充放电记录，给两个数：「预计还能用」按近期掉电速度算，
「满电最长可用」按 30 天内每次充满后放电循环折算取最长。
图表固定 3:2 宽高比（宽度随窗口变化，高度按比例反推），
页面放进滚动区，窗口变小时自动出现滚动条而不压缩图表比例。
"""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QScrollArea,
                               QVBoxLayout, QWidget)

from . import history, i18n, theme
from .chart import LineChart
from .profiles import save_config
from .scanner import connection_label
from .widgets import DragArea, FlatButton, IconButton, Segmented, Surface

RANGE_LABELS = [("24h", "24 小时"), ("7d", "7 天"), ("30d", "30 天")]
RECORD_OPTIONS = (("5 分钟", 300), ("10 分钟", 600),
                  ("30 分钟", 1800), ("1 小时", 3600))


def _duration(hours: float) -> str:
    if hours >= 100:
        return i18n.T("100 小时以上")
    total = int(round(hours * 60))
    hh, mm = divmod(total, 60)
    if hh <= 0:
        return i18n.TF("{} 分钟", max(mm, 1))
    if mm == 0:
        return i18n.TF("{} 小时", hh)
    return i18n.TF("{} 小时 {} 分", hh, mm)


def _span(hours: float) -> str:
    """续航估算用：超过 48 小时改用「天 + 小时」，避免一律显示 100 小时以上。"""
    total = int(round(hours))
    if total >= 48:
        days, rest = divmod(total, 24)
        return i18n.TF("{} 天 {} 小时", days, rest) if rest else i18n.TF("{} 天", days)
    return _duration(hours)


class HistoryPage(QWidget):
    back_requested = Signal()
    config_changed = Signal()

    def __init__(self, embedded: bool = False, config: dict | None = None, parent=None):
        super().__init__(parent)
        self.config = config if config is not None else {}
        self._did = ""
        self._range = "24h"
        self._confirm_did = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(0 if embedded else 16, 0 if embedded else 12,
                                0 if embedded else 16, 0 if embedded else 14)
        root.setSpacing(0)

        head = DragArea() if not embedded else QWidget()
        head_lay = QHBoxLayout(head)
        head_lay.setContentsMargins(0, 0, 0, 0)
        head_lay.setSpacing(4)
        self.btn_back = IconButton(theme.ICON["back"], size=28, icon_size=9, tooltip=i18n.T("返回"))
        self.btn_back.clicked.connect(lambda *_: self.back_requested.emit())
        head_lay.addWidget(self.btn_back)
        if embedded:
            self.btn_back.hide()
        title_col = QVBoxLayout()
        title_col.setContentsMargins(0, 0, 0, 0)
        title_col.setSpacing(2)
        title = QLabel(i18n.T("电量统计"))
        title.setFont(theme.ui_font(13, QFont.Weight.DemiBold))
        title.setStyleSheet(f"color: {theme.C.TEXT.name()}; background: transparent;")
        title_col.addWidget(title)
        subtitle = QLabel(i18n.T("续航趋势、掉电速率与采样间隔"))
        subtitle.setFont(theme.ui_font(7))
        subtitle.setStyleSheet(f"color: {theme.C.TEXT_TERTIARY.name()}; background: transparent;")
        title_col.addWidget(subtitle)
        head_lay.addLayout(title_col, 1)
        self.btn_refresh = IconButton(theme.ICON["refresh"], size=28, icon_size=9, tooltip=i18n.T("刷新图表"))
        self.btn_refresh.clicked.connect(lambda *_: self.refresh())
        head_lay.addWidget(self.btn_refresh)
        root.addWidget(head)
        root.addSpacing(10)

        # ---------------- 可滚动内容 ---------------- #
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
        self.body.setContentsMargins(0, 0, 8, 0)
        self.body.setSpacing(0)
        self.scroll.setWidget(self.host)
        root.addWidget(self.scroll, 1)

        self.combo = QComboBox()
        self.combo.setStyleSheet(theme.INPUT_QSS)
        self.combo.setFont(theme.ui_font(9))
        self.combo.currentIndexChanged.connect(self._on_device_changed)
        self.body.addWidget(self.combo)
        self.body.addSpacing(8)

        self.seg = Segmented([(key, i18n.T(label)) for key, label in RANGE_LABELS])
        self.seg.setFixedHeight(32)
        self.seg.changed.connect(self._on_range_changed)
        self.body.addWidget(self.seg)
        self.body.addSpacing(8)

        # 采样设置（原「设置 → 电量统计」）放在图表上方，进页即可见
        rec = QHBoxLayout()
        rec.setContentsMargins(2, 0, 2, 0)
        rec.setSpacing(6)
        rec_label = QLabel(i18n.T("记录间隔"))
        rec_label.setFont(theme.ui_font(8))
        rec_label.setStyleSheet(f"color: {theme.C.TEXT_SECONDARY.name()};")
        self.record = QComboBox()
        self.record.setStyleSheet(theme.INPUT_QSS)
        self.record.setFont(theme.ui_font(8))
        self.record.setFixedWidth(96)
        for label, sec in RECORD_OPTIONS:
            self.record.addItem(i18n.T(label), sec)
        self.record.currentIndexChanged.connect(self._on_record_interval)
        rec_hint = QLabel(i18n.TF("按此间隔采样，保留 {} 天", history.RETENTION_DAYS))
        rec_hint.setFont(theme.ui_font(7))
        rec_hint.setStyleSheet(f"color: {theme.C.TEXT_TERTIARY.name()};")
        rec.addWidget(rec_label)
        rec.addWidget(self.record)
        rec.addStretch(1)
        rec.addWidget(rec_hint)
        self.body.addLayout(rec)
        self.body.addSpacing(8)

        self.chart = LineChart(ratio=(3, 2))       # 3:2，宽度变高度跟着变
        self.body.addWidget(self.chart)
        self.body.addSpacing(6)

        self.stats = QLabel("—")
        self.stats.setFont(theme.ui_font(8))
        self.stats.setStyleSheet(f"color: {theme.C.TEXT_TERTIARY.name()};")
        self.body.addWidget(self.stats)
        self.body.addSpacing(8)

        self.card = Surface(radius=10, fill=QColor(0, 0, 0, 10),
                            border=QColor(0, 0, 0, 16))
        card_lay = QVBoxLayout(self.card)
        card_lay.setContentsMargins(12, 9, 12, 9)
        card_lay.setSpacing(2)
        self.predict_title = QLabel("—")
        self.predict_title.setFont(theme.ui_font(10, QFont.Weight.DemiBold))
        self.predict_title.setStyleSheet(f"color: {theme.C.TEXT.name()};")
        self.predict_sub = QLabel("")
        self.predict_sub.setWordWrap(True)
        self.predict_sub.setFont(theme.ui_font(7))
        self.predict_sub.setStyleSheet(f"color: {theme.C.TEXT_TERTIARY.name()};")
        card_lay.addWidget(self.predict_title)
        card_lay.addWidget(self.predict_sub)

        self.card_sep = QFrame()
        self.card_sep.setFixedHeight(1)
        self.card_sep.setStyleSheet("background: rgba(0,0,0,18); border: none;")
        card_lay.addSpacing(8)
        card_lay.addWidget(self.card_sep)
        card_lay.addSpacing(8)

        self.endurance_title = QLabel("—")
        self.endurance_title.setFont(theme.ui_font(10, QFont.Weight.DemiBold))
        self.endurance_title.setStyleSheet(f"color: {theme.C.TEXT.name()};")
        self.endurance_sub = QLabel("")
        self.endurance_sub.setWordWrap(True)
        self.endurance_sub.setFont(theme.ui_font(7))
        self.endurance_sub.setStyleSheet(f"color: {theme.C.TEXT_TERTIARY.name()};")
        card_lay.addWidget(self.endurance_title)
        card_lay.addWidget(self.endurance_sub)
        self.body.addWidget(self.card)
        self.body.addSpacing(10)

        self.empty = QLabel(i18n.T("还没有历史记录。\n程序会按下方「记录间隔」自动采样，"
                            "积累几次后这里会出现曲线和预测。"))
        self.empty.setWordWrap(True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setFont(theme.ui_font(8))
        self.empty.setStyleSheet(f"color: {theme.C.TEXT_TERTIARY.name()};")
        self.empty.hide()
        self.body.addWidget(self.empty)

        self.body.addStretch(1)

        foot = QHBoxLayout()
        foot.setContentsMargins(0, 6, 0, 0)
        foot.setSpacing(6)
        self.btn_clear = FlatButton(i18n.T("清除该设备记录"), glyph=theme.ICON["delete"])
        self.btn_clear.clicked.connect(self._clear_device)
        self.btn_clear_all = FlatButton(i18n.T("全部清除"))
        self.btn_clear_all.clicked.connect(self._clear_all)
        foot.addWidget(self.btn_clear)
        foot.addWidget(self.btn_clear_all)
        foot.addStretch(1)
        root.addLayout(foot)

        self._confirm_timer = QTimer(self)
        self._confirm_timer.setSingleShot(True)
        self._confirm_timer.setInterval(4000)
        self._confirm_timer.timeout.connect(self._reset_confirm)

        self.reload()

    # ------------------------------------------------------------------ #
    def reload(self) -> None:
        """打开页面时重建设备列表并重绘。设备按身份合并，换过连接方式也只出现一次。"""
        self._reset_confirm()
        self._sync_record_interval()
        devices = history.devices()
        self.combo.blockSignals(True)
        self.combo.clear()
        for dev in devices:
            label = dev["name"]
            if dev.get("last_conn") is not None:
                label = f"{label} · {i18n.T(connection_label(dev['last_conn']))}"
            self.combo.addItem(label, dev["did"])
        self.combo.blockSignals(False)

        if not devices:
            self._did = ""
            self._show_empty(True)
            return

        idx = 0
        for i, dev in enumerate(devices):
            if dev["did"] == self._did:
                idx = i
                break
        self.combo.setCurrentIndex(idx)
        self._did = self.combo.itemData(idx) or devices[0]["did"]
        self._show_empty(False)
        self.refresh()

    def _sync_record_interval(self) -> None:
        try:
            current = int(self.config.get("record_interval", 300))
        except (TypeError, ValueError):
            current = 300
        idx = self.record.findData(current)
        self.record.blockSignals(True)
        self.record.setCurrentIndex(idx if idx >= 0 else 0)
        self.record.blockSignals(False)

    def refresh(self) -> None:
        """按当前设备与区间重画（主程序每次采样后调用）。"""
        did = self._did
        if not did:
            self._show_empty(True)
            return
        span, _bucket, fmt = history.RANGES[self._range]
        now = int(time.time())
        pts = history.points(did, self._range, now)
        seg = history.spans(did, self._range, now)
        self.chart.set_data(pts, span, fmt, now=now, charging_spans=seg["charging"])

        st = history.stats(did, self._range, now)
        if st["count"]:
            span_text = i18n.T(dict(RANGE_LABELS)[self._range])
            text = i18n.TF(
                "{}：最低 {}% · 最高 {}% · 平均 {}% · {} 次采样",
                span_text, st['min'], st['max'], st['avg'], st['count'])
            extra = []
            if seg["charges"]:
                extra.append(i18n.TF("{} 次充电", seg["charges"]))
            if st["conns"] >= 2:
                extra.append(i18n.TF("已合并 {} 种连接方式", st["conns"]))
            if extra:
                text = " · ".join([text, *extra])
            self.stats.setText(text)
        else:
            self.stats.setText(i18n.T("该区间还没有采样"))
        self._update_prediction(did, now)

    def _update_prediction(self, did: str, now: int) -> None:
        info = history.predict(did, now)
        if info.get("ready"):
            self.predict_title.setText(i18n.TF("预计还能用 {}", _duration(info['hours'])))
            self.predict_sub.setText(i18n.TF(
                "当前 {}% · 近 {} 天 {} 段放电 · 平均掉电 {} · 数据覆盖 {}",
                info['percent'], history.ENDURANCE_DAYS, info.get('cycles', 1),
                i18n.TF("{} %/小时", info['rate']), _span(info['window_h'])))
        else:
            self.predict_title.setText(str(info.get("reason") or i18n.T("暂无预测")))
            if info.get("percent") is not None:
                self.predict_sub.setText(i18n.TF(
                    "当前 {}% · 继续积累放电记录后会自动给出估算", info['percent']))
            else:
                self.predict_sub.setText(
                    i18n.T("需要至少 3 次放电采样，且间隔覆盖半小时以上"))

        end = history.endurance(did, now)
        if end.get("ready"):
            self.endurance_title.setText(i18n.TF("满电最长可用 {}", _span(end['hours'])))
            self.endurance_sub.setText(i18n.TF(
                "近 {} 天 {} 次充电循环 · 中位 {} · 最长一次 {}%→{}% 实测 {} 小时",
                end['days'], end['cycles'], _span(end['median']),
                end['start'], end['end'], f"{end['measured_h']:g}"))
        else:
            self.endurance_title.setText(i18n.T("满电续航：待积累"))
            self.endurance_sub.setText(str(end.get("reason") or ""))

    def _show_empty(self, empty: bool) -> None:
        for w in (self.combo, self.seg, self.chart, self.stats, self.card):
            w.setVisible(not empty)
        self.btn_clear.setEnabled(not empty)
        self.btn_clear_all.setEnabled(bool(history.devices()))
        self.empty.setVisible(empty)

    # ------------------------------------------------------------------ #
    def _on_device_changed(self, index: int) -> None:
        did = self.combo.itemData(index)
        if did:
            self._did = str(did)
            self._reset_confirm()
            self.refresh()

    def _on_range_changed(self, key: str) -> None:
        self._range = key
        self.refresh()

    def _on_record_interval(self, _index: int) -> None:
        try:
            value = int(self.record.currentData())
        except (TypeError, ValueError):
            return
        self.config["record_interval"] = value
        try:
            save_config(self.config)
        except Exception:                          # noqa: BLE001
            pass
        self.config_changed.emit()

    def _clear_device(self) -> None:
        if not self._did:
            return
        if self._confirm_did != self._did:
            self._confirm_did = self._did
            self.btn_clear.setText(i18n.T("再点一次确认清除"))
            self._confirm_timer.start()
            return
        history.clear_device(self._did)          # 连同该设备所有连接来源的记录一起清除
        self._reset_confirm()
        self.reload()

    def _clear_all(self) -> None:
        if self._confirm_did != "*":
            self._confirm_did = "*"
            self.btn_clear_all.setText(i18n.T("再点一次确认"))
            self._confirm_timer.start()
            return
        history.clear_all()
        self._reset_confirm()
        self._did = ""
        self.reload()

    def _reset_confirm(self) -> None:
        self._confirm_did = ""
        self.btn_clear.setText(i18n.T("清除该设备记录"))
        self.btn_clear_all.setText(i18n.T("全部清除"))
        self.btn_clear.setEnabled(bool(self._did))
        self.btn_clear_all.setEnabled(bool(history.devices()))

    # ------------------------------------------------------------------ #
    def preferred_height(self) -> int:
        """页面内容高度（不含面板外框留白，由 Flyout._fit 叠加）。"""
        lay = self.layout()
        if lay is not None:
            lay.activate()
        need = lay.sizeHint().height() if lay is not None else 0
        return max(need, 430)
