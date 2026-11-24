"""最小复现：首次从悬浮窗进入「电量统计」时导航双选中。

复现调用链（与 main.TrayApp.open_console 一致）：
    ConsoleWindow(...)            # __init__ 末尾 open_page("general")，启动 0->1 补间
    console.open_page("stats")    # 同一调用栈，无事件循环介入

预期：只有 stats 高亮；BUG 时 general 与 stats 都停在 sel=1（截图现象）。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app import theme  # noqa: E402
from app.console import ConsoleWindow  # noqa: E402
from app.profiles import load_config  # noqa: E402


def pump(app, ms=600):
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


app = QApplication(sys.argv)
app.setFont(theme.ui_font(9))
config = load_config()

console = ConsoleWindow(config, lambda: [])   # 首次创建：内部 open_page("general")
console.open_page("stats")                     # 悬浮窗点「电量统计」，同一栈
console.show()
pump(app, 700)                                 # 等补间结算

print("--- 首次从悬浮窗进入电量统计（结算后） ---")
bad = []
for key, item in console._nav.items():
    sel = item._sel_tween.value()
    print(f"{key:8s} checked={item.isChecked()!s:5s} sel={sel:.3f}")
    if item.isChecked() and sel < 0.99:
        bad.append(f"{key}: checked 但未高亮")
    if not item.isChecked() and sel > 0.01:
        bad.append(f"{key}: 未选中却仍高亮")

highlighted = [k for k, it in console._nav.items() if it._sel_tween.value() > 0.01]
if len(highlighted) > 1:
    bad.append(f"同时高亮: {highlighted}")

print("结论:", "BUG 复现 -> " + "; ".join(bad) if bad else "正常")
sys.exit(1 if bad else 0)
