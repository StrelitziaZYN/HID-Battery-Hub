"""开发用：把界面渲染成 PNG 便于检查（不影响正式运行）。

    python tools/shot.py              # 默认状态（未上传图标）
    python tools/shot.py --demo-icon  # 额外生成一张「已上传图标」的效果图
    python tools/shot.py --lang en    # 英文界面（文件名带 _en 后缀）
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import i18n, icon_store, theme  # noqa: E402
from app.profiles import load_config  # noqa: E402
from app.prompt_page import PromptDialog  # noqa: E402
from app.protocols import all_protocols  # noqa: E402
from app.scanner import scan_devices  # noqa: E402
from app.console import ConsoleWindow  # noqa: E402
from app.settings_page import ProtocolPicker  # noqa: E402
from app.ui import Flyout  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shots")


def _settle(app: QApplication, ms: int = 320):
    """等进场动效跑完再截图，否则会拍到淡入到一半的半透明界面。"""
    import time

    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def _demo_icon(path: str) -> None:
    """生成一张示例图，模拟用户上传的照片（仅用于截图，不进入正式资源）。"""
    image = QImage(96, 96, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(38, 38, 40))
    p.drawRoundedRect(QRectF(2, 22, 92, 52), 9, 9)
    p.setBrush(QColor(200, 200, 205))
    for row in range(2):
        for col in range(6):
            p.drawRoundedRect(QRectF(10 + col * 13, 32 + row * 14, 10, 10), 2, 2)
    p.end()
    image.save(path, "PNG")


def main() -> int:
    demo = "--demo-icon" in sys.argv
    lang = "zh"
    if "--lang" in sys.argv:
        idx = sys.argv.index("--lang")
        if idx + 1 < len(sys.argv):
            lang = sys.argv[idx + 1]
    i18n.set_language(lang)
    suffix = "" if lang == "zh" else f"_{lang}"

    os.makedirs(OUT, exist_ok=True)
    app = QApplication(sys.argv)
    app.setFont(theme.ui_font(9))

    config = load_config()
    flyout = Flyout(config)
    devices = scan_devices(config)
    flyout.devices_page.set_devices(devices)
    flyout.show()
    _settle(app)
    flyout.grab().save(os.path.join(OUT, f"devices{suffix}.png"))

    console = ConsoleWindow(config, lambda: list(devices))
    console.show()
    _settle(app, 420)
    for key, name in (("stats", "history"), ("general", "general"), ("iface", "settings")):
        console.open_page(key)
        _settle(app, 260)
        console.grab().save(os.path.join(OUT, name + suffix + ".png"))
    console.close()

    picker = ProtocolPicker(all_protocols(), "mchose-v2")
    picker.show()
    app.processEvents()
    picker.grab().save(os.path.join(OUT, f"protocol_picker{suffix}.png"))
    picker.close()

    if devices:
        dlg = PromptDialog(config, devices[0])
        dlg.show()
        app.processEvents()
        dlg.grab().save(os.path.join(OUT, f"prompt_dialog{suffix}.png"))
        dlg.close()

    if demo and devices:
        tmp = os.path.join(OUT, "_upload_demo.png")
        _demo_icon(tmp)
        key = devices[0].get("icon_key")
        saved = icon_store.save(key, tmp)
        os.remove(tmp)
        try:
            flyout.devices_page.set_devices(scan_devices(config))
            app.processEvents()
            flyout.grab().save(os.path.join(OUT, "devices_icon.png"))
            console2 = ConsoleWindow(config, lambda: list(devices))
            console2.show()
            console2.open_page("settings")
            app.processEvents()
            console2.grab().save(os.path.join(OUT, "settings_icon.png"))
            console2.close()
        finally:
            icon_store.clear(key)
            print("demo icon cleaned:", saved)
    return 0


if __name__ == "__main__":
    sys.exit(main())
