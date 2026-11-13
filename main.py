"""HID Battery Hub —— 2.4G 设备电量监测（程序入口）。"""
from __future__ import annotations

import copy
import os
import sys

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import autostart, branding, history, i18n, theme, winapi    # noqa: E402
from app.icons import app_icon, battery_pixmap                    # noqa: E402
from app.profiles import load_config, save_config                 # noqa: E402
from app.scanner import scan_devices                              # noqa: E402
from app.ui import Flyout                                         # noqa: E402

MENU_QSS = """
QMenu {
    background: #FAFAFA;
    border: 1px solid rgba(0,0,0,30);
    border-radius: 8px;
    padding: 4px;
    color: #1A1A1A;
}
QMenu::item {
    padding: 6px 26px 6px 14px;
    border-radius: 5px;
    font-size: 12px;
}
QMenu::item:selected { background: rgba(0,0,0,18); }
QMenu::separator { height: 1px; background: rgba(0,0,0,20); margin: 4px 8px; }
"""


class ScanThread(QThread):
    """一次完整读取：枚举设备清单（发现新设备）+ 逐台读电量，两件事同一个线程里做完。"""

    done = Signal(int, list)

    def __init__(self, config: dict, token: int, parent=None):
        super().__init__(parent)
        self._config = copy.deepcopy(config)
        self._token = token

    def run(self):                                 # noqa: D102
        try:
            devices = scan_devices(self._config)
        except Exception:                          # noqa: BLE001
            devices = []
        self.done.emit(self._token, devices)


class TrayApp(QObject):
    #: 单次读取的硬超时。hidapi 在设备休眠 / 接收器半掉线时会阻塞很久，
    #: 没有这道闸门界面就会永远停在「正在读取设备…」，且之后每次刷新都被丢掉。
    SCAN_TIMEOUT_MS = 15000
    #: 允许同时「挂住」的扫描线程数上限，超过就只提示不再叠线程
    MAX_STUCK_THREADS = 2

    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        self.config = load_config()
        i18n.set_language(self.config.get("language", "zh"))
        self._thread: ScanThread | None = None
        self._stuck: list[ScanThread] = []
        self._token = 0
        self._active_token = 0
        self._pending = False
        self._reading = False                      # 真值上「正在读」，不依赖定时器状态
        self._console = None

        self._make_flyout()

        self.tray = QSystemTrayIcon(app_icon(not winapi.taskbar_is_dark()), app)
        self.tray.setToolTip(branding.APP_NAME)
        self.tray.activated.connect(self._on_tray)
        self.tray.show()

        self._build_menu()

        # 间隔只有一份（refresh_interval）：设备清单与电量读数始终在同一个周期里一起刷新。
        # 用「读完之后再排下一次」的单次定时器，避免扫描慢时与下一次触发互相顶掉。
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.refresh)

        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.setInterval(self.SCAN_TIMEOUT_MS)
        self._watchdog.timeout.connect(self._on_scan_timeout)

        self._set_reading(False)
        self._schedule_next()
        QTimer.singleShot(300, self.refresh)
        QTimer.singleShot(6000, self._prune_history)

        if not self.config.get("notified"):
            self.config["notified"] = True
            try:
                save_config(self.config)
            except Exception:                      # noqa: BLE001
                pass
            QTimer.singleShot(1400, lambda: self.tray.showMessage(
                branding.APP_NAME,
                i18n.T("已驻留系统托盘，左键点击电池图标即可查看设备电量。"),
                QSystemTrayIcon.MessageIcon.Information, 5000))

    def _make_flyout(self):
        """创建弹出面板并接好信号（首启与语言切换重建共用）。"""
        self.flyout = Flyout(self.config)
        self.flyout.request_refresh = self.refresh
        self.flyout.refresh_requested.connect(self.refresh)
        self.flyout.settings_requested.connect(lambda: self.open_console("settings"))
        self.flyout.history_requested.connect(lambda: self.open_console("stats"))
        self.flyout.device_clicked.connect(self.open_prompt)
        self.flyout.position_changed.connect(self._save_position)
        self.flyout.preview_closed.connect(self._on_preview_closed)

    # ------------------------------------------------------------------ #
    def _build_menu(self):
        menu = QMenu()
        menu.setStyleSheet(MENU_QSS)
        menu.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        act_open = QAction(i18n.T("打开面板"), menu)
        act_open.triggered.connect(lambda *_: self.flyout.popup())

        act_settings = QAction(i18n.T("设置"), menu)
        act_settings.triggered.connect(lambda *_: self.open_console("settings"))

        self.act_autostart = QAction(i18n.T("开机自启动"), menu)
        self.act_autostart.setCheckable(True)
        self.act_autostart.setChecked(autostart.is_enabled())
        self.act_autostart.triggered.connect(self._toggle_autostart)

        act_quit = QAction(i18n.T("退出"), menu)
        act_quit.triggered.connect(self.app.quit)

        menu.addAction(act_open)
        menu.addSeparator()
        menu.addAction(act_settings)
        menu.addSeparator()
        menu.addAction(self.act_autostart)
        menu.addSeparator()
        menu.addAction(act_quit)

        # 每次弹出前重读注册表，保证勾选状态与实际一致（设置页里改过也能同步）
        menu.aboutToShow.connect(self._sync_autostart)
        self.tray.setContextMenu(menu)
        self._menu = menu

    def _sync_autostart(self):
        if not hasattr(self, "act_autostart"):
            return
        self.act_autostart.blockSignals(True)
        self.act_autostart.setChecked(autostart.is_enabled())
        self.act_autostart.blockSignals(False)

    def _toggle_autostart(self, checked: bool):
        """菜单勾选 → 写/删注册表；失败则把勾选拨回去。"""
        if not autostart.set_enabled(bool(checked)):
            self._sync_autostart()
            return
        if self._console is not None:
            self._console.general_page.reload(self.config)

    # ------------------------------------------------------------------ #
    def _ensure_console(self):
        """独立窗口按需创建，关闭只是隐藏，再次打开保留上次所在页。"""
        if self._console is None:
            from app.console import ConsoleWindow

            self._console = ConsoleWindow(
                self.config, lambda: list(self.flyout.devices_page._devices))
            self._console.config_changed.connect(self._on_config_changed)
            self._console.icons_changed.connect(self.refresh)
            self._console.refresh_requested.connect(self.refresh)
            self._console.opened.connect(self.flyout.hide_animated)
            self._console.language_changed.connect(self._on_language_changed)
            # 材质/动效改动即时同步到托盘面板（面板不做 DWM 材质，但要跟着调通透度）
            self._console.appearance_changed.connect(self.flyout.apply_appearance)
            self._console.panel_changed.connect(self.flyout.apply_appearance)
            self._console.preview_requested.connect(self._on_preview_requested)
            self._console.set_status(self._status_text())
        return self._console

    # ------------------------------------------------------------------ #
    #: 预览：设置页点「预览」→ 在窗口旁边弹出真实电量面板，拖动滑块实时生效
    def _on_preview_requested(self, on: bool):
        if on:
            self.flyout.show_preview(self._console)
        else:
            self.flyout.end_preview()

    def _on_preview_closed(self):
        if self._console is not None:
            self._console.end_preview()

    def _on_language_changed(self):
        """语言切换后整窗重建：托盘菜单 / 弹出面板 / 设置窗口全部换新文案。"""
        try:
            save_config(self.config)
        except Exception:                          # noqa: BLE001
            pass
        devices = list(self.flyout.devices_page._devices)
        console_state = None
        if self._console is not None:
            console_state = (self._console.isVisible(), self._console._current,
                             self._console.geometry())
            self._console.close()
            self._console.deleteLater()
            self._console = None
        self.flyout.hide()
        self.flyout.deleteLater()
        self._make_flyout()
        if devices:
            self.flyout.devices_page.set_devices(devices)
        self._build_menu()
        if console_state is not None:
            self._ensure_console()
            visible, page, geo = console_state
            self._console.open_page(page, refresh=False)
            self._console.setGeometry(geo)
            self._console.set_status(self._status_text())
            if visible:
                self._console.show()
        self._set_reading(self._reading)

    def _status_text(self) -> str:
        devices = self.flyout.devices_page._devices
        return (i18n.TF("上次刷新 {} 台设备", len(devices)) if devices
                else i18n.T("未检测到设备"))

    def open_console(self, page: str = "general"):
        self.flyout.hide_animated()
        console = self._ensure_console()
        console.open_page(page)
        if console.isMinimized():
            console.showNormal()
        console.show()
        console.raise_()
        console.activateWindow()

    def open_prompt(self, device: dict | None = None):
        """点设备行：生成接入提示词（模态对话框，独立于弹出面板）。"""
        from app.prompt_page import PromptDialog

        self.flyout.hide_animated()
        dlg = PromptDialog(self.config, device, None)
        dlg.bundle_imported.connect(self._on_bundle_imported)
        dlg.exec()

    def _on_bundle_imported(self):
        if self._console is not None:
            self._console._on_bundle_imported()
        else:
            self._on_config_changed()

    def _reset_position(self):
        self.flyout.reset_position()
        self.flyout.popup()

    def _save_position(self):
        try:
            save_config(self.config)
        except Exception:                          # noqa: BLE001
            pass

    def _prune_history(self):
        try:
            history.prune()
        except Exception:                          # noqa: BLE001
            pass

    def shutdown(self):
        """退出前把定时器停掉、给仍在跑的扫描线程一点时间，避免销毁时崩溃。"""
        for t in (self.timer, self._watchdog):
            try:
                t.stop()
            except Exception:                      # noqa: BLE001
                pass
        for thread in [self._thread, *self._stuck]:
            if thread is not None and thread.isRunning():
                thread.wait(400)

    # ------------------------------------------------------------------ #
    def refresh_interval(self) -> int:
        """设备清单与电量共用的刷新间隔（秒）——唯一来源。"""
        try:
            return max(10, int(self.config.get("refresh_interval", 60)))
        except (TypeError, ValueError):
            return 60

    def _schedule_next(self):
        """读完这一轮再排下一轮：扫描耗时不会把下一次触发顶掉，两者始终同一节奏。"""
        self.timer.start(self.refresh_interval() * 1000)

    def _restart_timer(self):
        self._schedule_next()

    def _on_config_changed(self):
        self._restart_timer()
        self.refresh()

    def _on_tray(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self.flyout.popup()

    # ------------------------------------------------------------------ #
    def refresh(self):
        """刷新一轮：**枚举设备清单 + 读取每台电量**，两者在同一个扫描线程里一次做完。

        任何入口（定时、托盘菜单、面板刷新按钮、设置窗口）都走这里，
        所以「刷新按钮只刷了电量 / 只刷了设备」这种偏差不存在。
        """
        if self._reading:                          # 正在读：记一次欠账，读完立刻补一轮
            self._pending = True
            return

        if self._thread is not None and self._thread.isRunning():
            # 上一轮线程还卡在驱动里没回来（设备休眠/接收器半掉线）：
            # 不再叠加新线程，先给出明确状态，等下一轮再试。
            self._abandon_stuck()
            if len(self._stuck) >= self.MAX_STUCK_THREADS:
                self._set_reading(False, i18n.T("设备无响应，稍后自动重试"))
                self._schedule_next()
                return

        self._begin_scan()

    def _set_reading(self, reading: bool, notice: str = ""):
        """面板与独立窗口同步显示读取状态（都是同一个读取过程）。"""
        self.flyout.set_reading(reading, notice)
        if self._console is not None:
            self._console.set_reading(reading)

    def _begin_scan(self):
        self._token += 1
        self._active_token = self._token
        self._pending = False
        self._thread = None
        self._reading = True
        self._set_reading(True)                    # 状态文字 + 刷新按钮转圈
        self._watchdog.start(self.SCAN_TIMEOUT_MS)
        thread = ScanThread(self.config, self._token, self)
        thread.done.connect(self._on_scan_done)
        self._thread = thread
        thread.start()

    def _abandon_stuck(self):
        """把卡住的线程挪到一边（仍由父对象保活），好让下一轮能重新发起。"""
        old = self._thread
        if old is None:
            return
        self._stuck = [t for t in self._stuck if t is not old]
        self._stuck.append(old)
        self._thread = None
        old.finished.connect(lambda t=old: self._forget_thread(t))

    def _forget_thread(self, thread: ScanThread):
        self._stuck = [t for t in self._stuck if t is not thread]

    def _on_scan_done(self, token: int, devices: list):
        if token != self._active_token:
            return                                 # 超时后迟到的回包：丢弃
        self._watchdog.stop()
        self._reading = False
        self._thread = None
        try:
            self._apply(devices)
        finally:
            # 即使渲染某台设备时出错，也必须把「正在读取」收掉，否则界面会一直卡住
            self._set_reading(False)               # →「已完成读取 · N 台设备」
            self._schedule_next()
        if self._pending:                          # 读取期间又被点了一次 → 马上补一轮
            self._pending = False
            self._begin_scan()

    def _on_scan_timeout(self):
        """到点还没回来：终结这次「正在读取」状态，界面恢复可用，下一轮自动重试。

        注意：单次定时器的 timeout 发出时 isActive() 已经是 False，
        所以这里只能用 `_reading` 判断，不能再看定时器状态。
        """
        if not self._reading:
            return
        self._watchdog.stop()
        self._reading = False
        self._abandon_stuck()
        self._pending = False
        self._set_reading(False, i18n.T("读取超时，稍后自动重试"))
        self._schedule_next()

    def _apply(self, devices: list):
        self.flyout.devices_page.set_devices(devices)
        self._update_tray(devices)
        self._record(devices)

    def _record(self, devices: list):
        """按「记录间隔」累积采样；命中时若统计页正开着就顺手重画。"""
        try:
            interval = int(self.config.get("record_interval", 300))
            if history.record(devices, interval) and self._console is not None \
                    and self._console.isVisible():
                self._console.history_page.refresh()
        except Exception:                          # noqa: BLE001
            pass

    def _update_tray(self, devices: list):
        on_dark = winapi.taskbar_is_dark()
        dpr = QGuiApplication.primaryScreen().devicePixelRatio()
        known = [d for d in devices if d.get("percent") is not None]
        if known:
            lowest = min(known, key=lambda d: d["percent"])
            percent = int(lowest["percent"])
            pm = battery_pixmap(percent, on_dark=on_dark,
                                charging=bool(lowest.get("charging")), size=40, dpr=dpr)
        else:
            pm = battery_pixmap(None, on_dark=on_dark, size=40, dpr=dpr)
        self.tray.setIcon(QIcon(pm))
        self._set_tooltip(devices)
        if self._console is not None:
            self._console.set_status(self._status_text())

    def _set_tooltip(self, devices: list):
        if not devices:
            self.tray.setToolTip(i18n.TF("{} · 未检测到设备", branding.APP_NAME))
            return
        lines = [branding.APP_NAME]
        for dev in devices:
            pct = dev.get("percent")
            mark = " ⚡" if dev.get("charging") else ""
            name = dev.get('name') or i18n.T('设备')
            lines.append(f"{name}：{pct if pct is not None else '--'}%{mark}")
        self.tray.setToolTip("\n".join(lines[:6]))


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(branding.APP_NAME)
    app.setApplicationDisplayName(branding.APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(QIcon(app_icon(False)))
    app.setFont(theme.ui_font(9))

    if not QSystemTrayIcon.isSystemTrayAvailable():
        # 打包成 --windowed 后没有控制台，sys.stdout 是 None，直接 print 会抛异常退出
        try:
            print(i18n.T("系统托盘不可用"))
        except Exception:                          # noqa: BLE001
            pass
        return 1

    tray = TrayApp(app)
    app.aboutToQuit.connect(tray.shutdown)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
