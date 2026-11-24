"""真实窗口栈下验证「首次从悬浮窗进电量统计」的导航选中态。

走的入口与 app/ui.py 的悬浮窗图表按钮一致：
    Flyout.history_requested → TrayApp.open_console("stats")
与离屏自检不同，这里用真实 Windows 平台（真实字体、DWM 材质、真实补间帧），
跑完把侧边栏裁图存到 tools/shots/，并打印每个导航项的 checked/_sel。

    python tools/check_live_nav.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import theme  # noqa: E402
from main import TrayApp  # noqa: E402

SHOTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shots")


def pump(app, ms):
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def count_bars(image):
    """统计侧边栏里的强调色竖条段数（同一行连续算一段）。"""
    rows = set()
    for y in range(image.height()):
        for x in range(image.width()):
            c = image.pixelColor(x, y)
            if c.blue() > 150 and c.blue() - c.red() > 60 and c.blue() - c.green() > 40:
                rows.add(y)
                break
    groups, cur = [], []
    for y in sorted(rows):
        if cur and y - cur[-1] > 2:
            groups.append(cur)
            cur = []
        cur.append(y)
    if cur:
        groups.append(cur)
    return [(g[0], g[-1]) for g in groups]


def main():
    app = QApplication(sys.argv)
    app.setFont(theme.ui_font(9))
    ta = TrayApp(app)

    # 与悬浮窗「电量统计」按钮完全相同的入口（首次创建窗口 + 同一调用栈切页）
    ta.open_console("stats")
    pump(app, 900)

    console = ta._console
    print("console visible:", console.isVisible(),
          "| page:", console.stack.currentWidget().__class__.__name__)
    bad = []
    for key, item in console._nav.items():
        sel = item._sel_tween.value()
        print(f"  {key:8s} checked={item.isChecked()!s:5s} sel={sel:.3f}")
        if item.isChecked() != (sel > 0.5):
            bad.append(f"{key} 选中态与高亮不一致")
        if not item.isChecked() and sel > 0.01:
            bad.append(f"{key} 未选中却仍高亮")

    nav_w = console.nav.width()
    shot = console.grab().toImage()
    crop = shot.copy(QRect(0, 0, nav_w, min(400, shot.height())))
    os.makedirs(SHOTS, exist_ok=True)
    path = os.path.join(SHOTS, "live_nav_stats.png")
    crop.save(path)
    bars = count_bars(crop)
    print("nav 蓝条段:", bars, "-> 数量", len(bars))
    if len(bars) != 1:
        bad.append(f"侧边栏蓝条数量应为 1，实际 {len(bars)}")
    print("截图:", path)

    # 再切回通用页，确认常规切换不受影响
    console.open_page("general")
    pump(app, 700)
    lit = [k for k, it in console._nav.items() if it._sel_tween.value() > 0.01]
    print("切回 general 后高亮:", lit, "(应为 ['general'])")
    if lit != ["general"]:
        bad.append(f"切回 general 后高亮异常：{lit}")

    print("结论:", "PASS" if not bad else "FAIL -> " + "; ".join(bad))
    console.close()
    pump(app, 200)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
