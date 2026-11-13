"""无 GUI 回归自检：协议注册 / 配置 / 扫描 / 设备检测 / 图标存取 / 提示词。

    python tools/selftest.py

全部通过时退出码为 0；任何一项失败会打印原因并返回 1。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QRectF, Qt                       # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter          # noqa: E402

from app import (autostart, branding, history, icon_store, plugin_store,  # noqa: E402
                 prompt_builder, scanner)
from app.icons import COLOR_CRIT, COLOR_WARN, battery_pixmap, level_color  # noqa: E402
from app.profiles import default_config, load_config          # noqa: E402
from app.protocols import all_protocols, get_protocol     # noqa: E402
from app.protocols.registry import LEGACY_IDS, resolve_id   # noqa: E402

RESULTS: list[tuple[bool, str]] = []
_APP = None                                        # QPixmap 需要有 GUI 应用实例，且必须常驻


def _ensure_app():
    global _APP
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    # 用 QApplication 而不是 QGuiApplication：控件相关的用例（设备行）需要它，
    # 而 QApplication 是 QGuiApplication 的子类，QPixmap 之类照样能用。
    from PySide6.QtWidgets import QApplication
    _APP = QApplication.instance() or QApplication([])
    return _APP


def check(name: str, fn):
    try:
        detail = fn()
        RESULTS.append((True, f"{name}  {detail or ''}".rstrip()))
    except Exception as exc:                                   # noqa: BLE001
        RESULTS.append((False, f"{name}  ->  {type(exc).__name__}: {exc}"))


def _make_png(path: str, size: int = 200) -> None:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(30, 30, 30))
    p.drawRoundedRect(QRectF(0, 0, size, size), 18, 18)
    p.end()
    image.save(path, "PNG")


# --------------------------------------------------------------------------- #
def t_registry():
    protos = all_protocols()
    ids = {p.id for p in protos}
    assert {"mchose-v2", "mchose-classic"} <= ids, f"注册表缺少协议：{ids}"
    assert resolve_id("report64") == "mchose-v2", "旧协议名未映射"
    assert set(LEGACY_IDS.values()) <= ids
    v2 = get_protocol("mchose-v2")
    assert v2.usage_pages and v2.params, "协议自描述不完整"
    return f"{len(protos)} 个协议：{', '.join(sorted(ids))}"


def t_config():
    cfg = default_config()
    prof = cfg["profiles"][0]
    assert prof["protocol"] in {p.id for p in all_protocols()}
    assert "report_id" in prof and "battery_index" in prof, "插件默认值未合并进配置"
    loaded = load_config()
    assert loaded["profiles"], "读取配置失败"
    return f"{len(cfg['profiles'])} 条默认接口"


def t_scan():
    cfg = load_config()
    devices = scanner.scan_devices(cfg)
    for dev in devices:
        assert 0 <= dev["percent"] <= 100, "电量越界"
        assert dev["vendor"], "缺少厂商名"
        assert dev["icon_path"] is None or os.path.isfile(dev["icon_path"])
    return f"{len(devices)} 台设备：" + "、".join(
        f"{d['name']} {d['percent']}%" for d in devices) if devices else "0 台（可插入接收器后重试）"


def t_detect():
    cfg = load_config()
    allv = scanner.list_connected(cfg)
    assert allv, "未检测到任何 HID 设备"
    supported = [d for d in allv if d["supported"]]
    for dev in allv:
        assert dev["interfaces"], f"{dev['name']} 没有集合信息"
        assert dev["vendor"], "缺少厂商名"
    return f"{len(allv)} 个物理设备，其中 {len(supported)} 个已支持"


def t_prompt():
    cfg = load_config()
    allv = scanner.list_connected(cfg)
    supported = next((d for d in allv if d["supported"]), None)
    unsupported = next((d for d in allv if not d["supported"]), None)

    sample = supported or unsupported
    assert sample, "没有设备可用于生成提示词"
    text = prompt_builder.build(scanner.enrich(sample, cfg) if supported else sample)
    assert "## 设备实测" in text and "## 接入包" in text
    assert sample["name"] in text, "提示词里没有设备名"
    assert sample["vendor"] in text, "提示词里没有厂商"
    assert branding.BUNDLE_KIND in text, "提示词没有要求输出接入包"
    assert "code_lines" in text, "提示词没有给出接入包结构"
    assert "设备型号" not in text, "没填型号时不该出现型号行"

    # 用户填写的型号必须进标题与设备信息，且提示词保持精简
    typed = prompt_builder.build(sample, model="MCHOSE A7 V3 Pro")
    assert "MCHOSE A7 V3 Pro" in typed.splitlines()[0], "型号没写进标题"
    assert "- 设备型号：MCHOSE A7 V3 Pro（用户填写）" in typed, "型号没写进设备信息"
    assert len(typed.splitlines()) < 80, "提示词应保持精简"
    return f"提示词 {len(text.splitlines())} 行，带型号 {len(typed.splitlines())} 行"


def t_icon_roundtrip():
    cfg = load_config()
    devices = scanner.scan_devices(cfg)
    key = devices[0]["icon_key"] if devices else icon_store.key_of(0x3837, 0x2025)
    before = icon_store.path_of(key)
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "upload.png")
        _make_png(src)
        saved = icon_store.save(key, src)
        assert os.path.isfile(saved), "图标没有落盘"
        assert saved.endswith(".png"), "未归一化成 PNG"
        img = QImage(saved)
        assert max(img.width(), img.height()) <= icon_store.MAX_SIDE, "图标未缩放"
        assert icon_store.resolve(int(key.split("-")[0], 16), int(key.split("-")[1], 16))
    if devices:
        uid = devices[0]["uid"]
        after = scanner.scan_devices(cfg)
        same = next((d for d in after if d["uid"] == uid), None)
        if same is not None:                       # 设备可能在两次扫描之间休眠
            assert same["icon_path"] == saved, "扫描结果没有带上图标"
    icon_store.clear(key)
    assert icon_store.path_of(key) is None, "清除图标失败"
    if before:                                     # 恢复用户原有图标
        icon_store.save(key, before)
    return f"键 {key}，上传→读回→清除 全部通过"


def t_history():
    """历史库：写样本 → 聚合 → 预测 → 清除，全程用临时设备 uid，不污染真实数据。"""
    uid = "TEST:SELFTEST"
    history.clear_device(uid)
    now = int(time.time())
    with history._db() as conn:
        for i in range(13):                        # 6 小时，每 30 分钟掉 5%
            ts = now - (12 - i) * 1800
            conn.execute("INSERT INTO samples(uid, name, percent, charging, ts) VALUES(?,?,?,?,?)",
                         (uid, "自检设备", 100 - i * 5, 0, ts))
        conn.execute("INSERT INTO devices(uid, name, vendor, first_ts, last_ts, last_percent) "
                     "VALUES(?,?,?,?,?,?)", (uid, "自检设备", "TEST", now - 21600, now, 40))
    try:
        pts = history.points(uid, "24h", now)
        assert len(pts) >= 5, "24 小时聚合点太少"
        st = history.stats(uid, "24h", now)
        assert st["count"] == 13 and st["min"] == 40, "统计不正确"
        pred = history.predict(uid, now)
        assert pred["ready"] and 0 < pred["hours"] < 24, f"预测异常：{pred}"
        assert abs(pred["rate"] - 10) < 1.0, f"掉电速率异常：{pred['rate']}"
        assert history.record([{"uid": uid, "name": "自检设备", "percent": 40}], 300, now) == 0, \
            "节流没生效（同一时刻重复写入了）"
    finally:
        history.clear_device(uid)
    assert not history.has_data(uid), "清除失败"
    return f"聚合 {len(pts)} 点、预测 {pred['hours']:.1f}h / {pred['rate']}%/h、清除通过"


def t_endurance():
    """30 天充放电循环 → 满电最长可用时长（合成数据，用临时库，不碰真实历史）。"""
    import os
    import shutil
    import tempfile

    from app import history

    tmp = tempfile.mkdtemp(prefix="hidbatt-endur-")
    old_sub, old_db = history.DATA_SUBDIR, history.HISTORY_DB
    history.DATA_SUBDIR = tmp
    history.HISTORY_DB = os.path.join(tmp, "history.db")
    uid = "TEST:ENDURANCE"
    now = int(time.time())
    rows = []
    # (起点电量, 终点电量, 持续小时, 结束于几小时前)
    plan = [(100, 20, 20, 80), (100, 5, 40, 30), (100, 40, 5, 15), (60, 10, 4, 8)]
    for start_pct, end_pct, hours, ends_ago in plan:
        t_end = now - ends_ago * 3600
        t_start = t_end - hours * 3600
        steps = max(4, int(hours * 2))
        for i in range(steps + 1):
            ts = int(t_start + (t_end - t_start) * i / steps)
            rows.append((uid, round(start_pct - (start_pct - end_pct) * i / steps), 0, ts))
        rows.append((uid, start_pct, 1, t_end + 60))       # 结束后插一条充电样本
    rows.sort(key=lambda r: r[3])
    try:
        with history._db() as conn:
            conn.executemany("INSERT INTO samples(uid, name, percent, charging, ts) "
                             "VALUES(?,?,?,?,?)",
                             [(uid, "续航自检", r[1], r[2], r[3]) for r in rows])
        end = history.endurance(uid, now)
        assert end["ready"], f"续航估算未就绪：{end}"
        assert end["cycles"] == 3, f"循环数应为 3（60% 起步那次被忽略），实际 {end['cycles']}"
        # 最长一次：100%→5% 用 40h，速率 2.375%/h，折算满电 ≈42.1h
        assert abs(end["hours"] - 42.1) < 1.0, f"最长续航异常：{end['hours']}"
        assert abs(end["median"] - 25.0) < 1.5, f"中位续航异常：{end['median']}"
        assert end["start"] == 100 and end["end"] == 5, f"最长循环端点异常：{end}"
        assert history.endurance("TEST:NODATA", now)["ready"] is False, "空设备不应就绪"
    finally:
        history.DATA_SUBDIR, history.HISTORY_DB = old_sub, old_db
        shutil.rmtree(tmp, ignore_errors=True)
    return f"3 次循环 · 最长 {end['hours']}h / 中位 {end['median']}h（100%→5%）"


def t_data_dir():
    """数据目录固定在 EXE / 程序目录；不可写时才回退，并在界面标注原因。"""
    import os

    from app import paths, settings_page

    assert os.path.isdir(paths.DATA_DIR), f"数据目录不存在：{paths.DATA_DIR}"
    assert (paths.DATA_DIR_NOTE == "") == paths.USING_EXE_DIR, "回退标记与说明不一致"
    assert paths.HISTORY_DB.startswith(paths.DATA_DIR), "历史库不在数据目录下"
    assert paths.PLUGIN_DIR.startswith(paths.DATA_DIR), "插件目录不在数据目录下"
    if paths.USING_EXE_DIR:
        same = os.path.normcase(os.path.abspath(paths.DATA_DIR)) == \
            os.path.normcase(os.path.abspath(paths.EXE_DIR))
        assert same, "USING_EXE_DIR 为真但目录不一致"

    old = (settings_page.DATA_DIR, settings_page.DATA_DIR_NOTE,
           settings_page.USING_EXE_DIR)
    try:                                           # 回退分支必须写清原因
        settings_page.DATA_DIR = os.path.join("X:", "用户目录", "HIDBatteryHub")
        settings_page.DATA_DIR_NOTE = "EXE 目录不可写，已切换到 X:\\用户目录\\HIDBatteryHub"
        settings_page.USING_EXE_DIR = False
        text = settings_page.InterfaceSettingsPage._data_hint_text()
        assert "已切换" in text, f"回退文案缺少原因：{text}"
    finally:
        (settings_page.DATA_DIR, settings_page.DATA_DIR_NOTE,
         settings_page.USING_EXE_DIR) = old
    mode = "EXE 目录" if paths.USING_EXE_DIR else "回退 %LOCALAPPDATA%"
    return f"{mode}：{paths.DATA_DIR}"


def t_icon_colors():
    """低电量配色与开机启动读取（不写注册表）。"""
    assert level_color(8) == COLOR_CRIT, "≤15% 应为红色"
    assert level_color(25) == COLOR_WARN, "≤30% 应为黄色"
    assert level_color(60) is None, "正常电量应使用中性色"
    assert level_color(8, charging=True) is None, "充电时不应告警"
    autostart.is_enabled()                         # 只读，不改系统状态
    pm = battery_pixmap(20, on_dark=True, size=40, dpr=1.0)
    assert not pm.isNull() and pm.width() == 40, "托盘图标绘制失败"
    return "≤15% 红 / ≤30% 黄 / 充电不告警，40px 图标正常"


def t_branding():
    """产品名单一来源：界面/托盘/打包都取同一个常量。"""
    from app import profiles

    assert branding.APP_NAME == "HID Battery Hub", branding.APP_NAME
    assert branding.EXE_STEM.isalnum(), "EXE 名不应含空格等特殊字符"
    assert branding.EXE_STEM in branding.REG_KEY_VALUE
    assert profiles.PROFILE_PATH.endswith("profiles.json")
    return f"{branding.APP_NAME} v{branding.VERSION} / {branding.EXE_STEM}.exe"


def t_flyout_tooltips():
    """弹出面板的设备行不能有 tooltip（Qt 会从子控件冒泡出一整块黑框）。"""
    from PySide6.QtWidgets import QWidget

    from app.widgets import DeviceRow

    dev = {"uid": "tooltip-test", "name": "自检设备", "percent": 62, "charging": False,
           "connection": 2, "protocol": "selftest"}
    row = DeviceRow(dict(dev))
    offenders = [row.toolTip()] + [w.toolTip() for w in row.findChildren(QWidget)]
    assert not any(offenders), f"设备行仍有 tooltip：{[t for t in offenders if t][:3]}"
    assert row.sizeHint().height() > 0
    return f"设备行及其 {len(row.findChildren(QWidget))} 个子控件均无 tooltip"


def t_bolt_border():
    """充电闪电：本体用底色挖空 + 前景色描边，深色任务栏下必须同时有亮、暗像素。"""
    pm_on = battery_pixmap(60, on_dark=True, charging=True, size=40, dpr=1.0)
    pm_off = battery_pixmap(60, on_dark=True, charging=False, size=40, dpr=1.0)
    assert pm_on.toImage() != pm_off.toImage(), "充电与放电图标完全一样"

    img = pm_on.toImage()
    lights = darks = 0
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.alpha() > 200:
                if c.lightness() > 200:
                    lights += 1
                elif c.lightness() < 60:
                    darks += 1
    assert lights > 60, f"缺少白色填充/描边（{lights}）"
    assert darks > 20, f"闪电本体（近黑）没画出来（{darks}）"

    pm_light = battery_pixmap(60, on_dark=False, charging=True, size=40, dpr=1.0)
    img = pm_light.toImage()
    light_px = sum(1 for y in range(img.height()) for x in range(img.width())
                   if img.pixelColor(x, y).alpha() > 200
                   and img.pixelColor(x, y).lightness() > 200)
    assert light_px > 20, "浅色任务栏下闪电本体的白色没画出来"
    return f"深/浅底都有描边+反色本体（亮 {lights}px / 暗 {darks}px / 浅底 {light_px}px）"


def t_flyout_resize():
    """面板列出全部设备，行数变化时窗口高度同步（内容不溢出），超量时改滚动。"""
    from app.ui import DevicesPage, Flyout

    devs = [{"uid": f"r{i}", "name": f"设备{i}", "percent": p,
             "charging": p == 97, "connection": 2}
            for i, p in enumerate([100, 97, 62])]
    page = DevicesPage()
    page.set_devices(devs)
    assert page.row_count() == 3, f"应渲染 3 行，实际 {page.row_count()}"
    assert [r.device["percent"] for r in page._rows] == [100, 97, 62], "行顺序应与扫描顺序一致"
    page.set_devices([])
    assert page.row_count() == 0, "清空后仍有残留行"

    many = [{"uid": f"m{i}", "name": f"设备{i}", "percent": 100 - i, "connection": 2}
            for i in range(12)]
    page.set_devices(many)
    assert page.row_count() == 12, f"12 台设备应全部建行，实际 {page.row_count()}"
    assert page.scroll.height() <= page.MAX_VISIBLE_ROWS * 64 + 4, "超过上限时应改为滚动"

    fly = Flyout({})
    fly.devices_page.set_devices([])
    empty_h = fly.height()
    fly.show()
    fly.devices_page.set_devices(devs)
    row_h = fly.height()
    assert row_h > empty_h, "行数变化后窗口高度没跟着变"
    assert fly.surface.height() <= fly.height() - Flyout._OUTER + 1, "面板超出外框"
    assert fly.devices_page.minimumSizeHint().height() <= fly.surface.height(), \
        f"内容溢出：需要 {fly.devices_page.minimumSizeHint().height()}px，只有 {fly.surface.height()}px"
    fly.hide()
    return f"3 台设备渲染 3 行；高度 {empty_h}→{row_h}，12 台时改滚动，内容未溢出"


def _pump(ms: int):
    """跑一小段事件循环（读取状态流转要靠定时器 + 线程回包）。"""
    app = _ensure_app()
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def t_reading_state():
    """状态文字必须能落到「已完成读取」，刷新按钮跟着转/停。"""
    from app.ui import DevicesPage

    page = DevicesPage()
    devs = [{"uid": "a", "name": "设备A", "percent": 88, "charging": True, "connection": 2}]

    page.set_reading(True)
    assert page.subtitle.text() == "正在读取设备…", page.subtitle.text()
    assert page.btn_refresh.is_spinning(), "读取时刷新按钮没有转起来"

    page.set_devices(devs)
    still = page.subtitle.text()
    page.set_reading(False)
    assert page.subtitle.text().startswith("已完成读取"), page.subtitle.text()
    assert "1 台设备" in page.subtitle.text(), page.subtitle.text()
    _pump(900)
    assert not page.btn_refresh.is_spinning(), "读完按钮还在转"

    page.set_devices([])
    page.set_reading(False)
    assert page.subtitle.text() == "已完成读取 · 未检测到设备", page.subtitle.text()

    page.set_reading(False, "读取超时，稍后自动重试")
    assert page.subtitle.text() == "读取超时，稍后自动重试", page.subtitle.text()
    return f"读取中/已完成/无设备/超时 四种状态文字正确（读中「{still}」）"


def t_scan_orchestration():
    """一轮刷新 = 枚举设备清单 + 读电量；超时也要收尾，绝不永久停在「正在读取」。"""
    import main as M

    calls: list[float] = []
    delay = {"s": 0.05}

    def fake_scan(_cfg):
        calls.append(time.monotonic())
        time.sleep(delay["s"])
        return [{"uid": "fake", "name": "假设备", "percent": 63,
                 "charging": False, "connection": 2}]

    real_scan, real_load = M.scan_devices, M.load_config
    M.scan_devices = fake_scan
    M.load_config = lambda: {"refresh_interval": 3600, "record_interval": 300,
                             "profiles": [], "notified": True}
    tray = M.TrayApp(_ensure_app())
    tray.SCAN_TIMEOUT_MS = 500
    try:
        _pump(420)                                   # 启动时那次
        assert len(calls) == 1, f"启动应读 1 次，实际 {len(calls)}"
        assert tray.flyout.devices_page.subtitle.text().startswith("已完成读取"), \
            tray.flyout.devices_page.subtitle.text()

        tray.flyout.devices_page.btn_refresh.click()  # 点刷新按钮
        assert tray.flyout.devices_page.btn_refresh.is_spinning(), "点刷新后按钮没转"
        _pump(420)
        assert len(calls) == 2, f"点刷新应触发一次完整读取，实际 {len(calls)}"
        assert tray.flyout.devices_page.subtitle.text().startswith("已完成读取")
        assert tray.flyout.devices_page.row_count() == 1, "读完没有渲染设备行"

        delay["s"] = 2.0                              # 模拟驱动阻塞
        tray.flyout.devices_page.btn_refresh.click()
        _pump(700)
        text = tray.flyout.devices_page.subtitle.text()
        assert "读取超时" in text, f"超时后没落到稳定状态：{text}"
        assert not tray._watchdog.isActive(), "超时后闸门没释放，之后会永远读不到"
        assert tray._thread is None, "卡住的线程应被挪走，好让下一轮重新发起"

        delay["s"] = 0.05                             # 恢复正常
        _pump(1600)
        tray.refresh()
        _pump(500)
        assert tray.flyout.devices_page.subtitle.text().startswith("已完成读取"), \
            tray.flyout.devices_page.subtitle.text()
        detail = f"点刷新读到 {len(calls)} 次；超时收尾正常；恢复后回到已完成读取"
    finally:
        tray.shutdown()
        tray.timer.stop()
        tray._watchdog.stop()
        tray.tray.hide()
        tray.flyout.hide()
        M.scan_devices, M.load_config = real_scan, real_load
    return detail


def t_bundle_import():
    """接入包：装插件 → 合并配置 → 可被 registry 使用 → 回滚干净。"""
    from app import profiles
    from app.protocols import protocol_ids

    pid = "selftest-bundle"
    backup = profiles.PROFILE_PATH + ".selftest.bak"
    shutil.copy2(profiles.PROFILE_PATH, backup)
    plugin_file = os.path.join(plugin_store.PLUGIN_DIR, "selftest_bundle.py")
    tmpdir = tempfile.mkdtemp()
    try:
        code = [
            "from __future__ import annotations",
            "from typing import Any, Dict",
            "from app.hid_core import BatteryReading",
            "from app.protocols.base import Param, Protocol",
            "from app.protocols.registry import register",
            "",
            "@register",
            "class SelfTest(Protocol):",
            f"    id = \"{pid}\"",
            "    label = \"自检 · 接入包\"",
            "    vendor = \"SELFTEST\"",
            "    usage_pages = (0xFF00,)",
            "    params = [Param(\"report_id\", \"ID\", \"hex\", 0)]",
            "    def query(self, dev, cfg: Dict[str, Any]) -> BatteryReading:",
            "        return BatteryReading(percent=42)",
        ]
        bundle = {
            "kind": branding.BUNDLE_KIND,
            "version": 1,
            "protocol": {"filename": "selftest_bundle.py", "code_lines": code},
            "profile": {"id": pid, "name": "自检 · 接入包", "vendor_id": 0x9999,
                        "usage_pages": [0xFF00], "protocol": pid, "models": {}},
        }
        path = os.path.join(tmpdir, "bundle.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(bundle, fh, ensure_ascii=False)

        cfg = profiles.load_config()
        before = len(cfg["profiles"])
        info = plugin_store.import_file(path, cfg)
        assert info["protocol"] == pid, f"插件 id 不对：{info}"
        assert os.path.isfile(os.path.join(plugin_store.PLUGIN_DIR, "selftest_bundle.py"))
        assert pid in protocol_ids(), "插件没有注册进注册表"
        proto = get_protocol(pid)
        assert proto is not None and proto.label.startswith("自检")
        assert len(cfg["profiles"]) == before + 1, "配置没有追加接口"
        assert [p["id"] for p in profiles.load_config()["profiles"]].count(pid) == 1, "配置未落盘"
        detail = f"{info['protocol']} → 接口 {info['profiles']}，配置 {before}→{before + 1}"
    finally:
        shutil.move(backup, profiles.PROFILE_PATH)
        if os.path.isfile(plugin_file):
            os.remove(plugin_file)
        shutil.rmtree(tmpdir, ignore_errors=True)

    disk = [p["id"] for p in profiles.load_config()["profiles"]]
    assert pid not in disk, "回滚后配置仍有测试接口"
    return detail + "，回滚干净"


def t_i18n():
    """词典完整性 + 占位符一致性 + 语言切换：英文界面不留中文键。"""
    import ast

    from app import i18n as i18n_mod

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files = [os.path.join(root, "main.py")]
    files += [os.path.join(root, "app", n) for n in os.listdir(os.path.join(root, "app"))
              if n.endswith(".py")]
    used = set()
    for path in files:
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and node.args):
                continue
            fn = node.func
            name = (fn.id if isinstance(fn, ast.Name)
                    else fn.attr if isinstance(fn, ast.Attribute) else "")
            if name not in ("T", "TF"):
                continue
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                used.add(arg.value)

    missing = sorted(k for k in used if k not in i18n_mod._EN)
    assert not missing, f"词典缺 {len(missing)} 条：{missing[:3]}"
    bad = [k for k in used if k in i18n_mod._EN and
           i18n_mod._EN[k].count("{}") != k.count("{}")]
    assert not bad, f"占位符数量不一致：{bad[:3]}"

    old = i18n_mod.lang()
    try:
        i18n_mod.set_language("en")
        assert i18n_mod.T("打开面板") == "Open Panel", "英文查词失败"
        assert i18n_mod.TF("{} 台充电中", 2) == "2 charging", "英文模板格式化失败"
        i18n_mod.set_language("zh")
        assert i18n_mod.T("打开面板") == "打开面板", "中文态不应改词"
    finally:
        i18n_mod.set_language(old)
    return f"{len(used)} 条 UI 文案，词典 {len(i18n_mod._EN)} 条，中英切换正常"


def t_motion():
    """动效档位 / 材质偏好 / 分段控件滑块 / 样式表 alpha：外观设置的底子必须可靠。"""
    from app import backdrop, motion, theme
    from app.widgets import Segmented

    old = motion.state()
    try:
        motion.configure({"motion": "off", "material": "bogus"})
        assert motion.duration(250) == 0, "「无动效」档仍给出非零时长"
        assert not motion.transitions_enabled(), "「无动效」档不该再有窗口过渡"
        assert motion.material() == motion.DEFAULTS["material"], "非法材质没回落默认值"

        motion.configure({"motion": "subtle", "material": "acrylic"})
        assert motion.duration(200) == 110, f"柔和档时长应为 110，实际 {motion.duration(200)}"
        assert motion.material() == "acrylic"

        motion.configure({"motion": "full"})
        assert motion.duration(250) == 250, "标准档时长被改动"

        # Qt 样式表只认 0..255 的 alpha：写成 0..1 会让整条背景规则失效（材质透不出来）
        assert theme.rgba(QColor(1, 2, 3, 128)) == "rgba(1,2,3,128)"
        mid = theme.mix(QColor(0, 0, 0, 0), QColor(100, 200, 40, 200), 0.5)
        assert mid.getRgb()[:3] == (50, 100, 20), f"颜色插值不对：{mid.getRgb()}"

        ui = default_config().get("ui") or {}
        assert set(motion.DEFAULTS) <= set(ui), f"默认配置缺外观项：{ui}"
        assert load_config().get("ui", {}).get("material") in motion.MATERIALS, "配置里的材质非法"

        seg = Segmented([("a", "A"), ("b", "B"), ("c", "C")])
        seg.resize(300, 32)
        seg.show()
        _pump(80)
        first = seg._thumb.left()
        seg.set_current("c")
        _pump(420)
        last = seg._thumb.left()
        assert seg.current() == "c", "分段控件选中项没切换"
        assert last > first + 40, f"选中底板没有滑过去：{first:.0f} → {last:.0f}"
        seg.close()
        detail = f"关闭=0ms / 柔和=110ms / 滑块 {first:.0f}→{last:.0f}px"
    finally:
        motion.configure(old)

    supported, why = backdrop.describe()
    return detail + "；材质" + ("可用" if supported else f"不可用（{why}）")


def t_panel_opacity():
    """面板不透明度：夹取、换算、预览与真实面板同源、滑块与输入框联动。"""
    from app import motion, settings_page, theme
    from app.ui import Flyout

    old = motion.state()
    real_save = settings_page.save_config
    settings_page.save_config = lambda *a, **k: None   # 用例不该碰真实配置文件
    try:
        # 1) 范围夹取：越界/非数字都不能把界面搞坏
        assert motion.clamp_opacity(5) == motion.OPACITY_MIN
        assert motion.clamp_opacity(500) == motion.OPACITY_MAX
        assert motion.clamp_opacity("abc") == motion.DEFAULTS["panel_opacity"]
        assert motion.clamp_opacity(63.6) == 64, "小数应四舍五入到整数百分比"

        # 2) 换算：100% 等于该材质的基准 alpha，往后按比例递减但不归零
        for material in motion.MATERIALS:
            base = theme.card_alpha(material)
            assert theme.panel_fill(material, 100).alpha() == base, f"{material} 100% 应等于基准 alpha"
            half = theme.panel_fill(material, 50).alpha()
            assert abs(half - base / 2) <= 1, f"{material} 50% 应约 {base/2}，实际 {half}"
            assert theme.panel_fill(material, 0).alpha() >= 1, "alpha 归零会让面板彻底消失"

        # 3) 真实面板与设置页预览必须同源（同一个计算函数）
        motion.configure({"material": "mica", "panel_opacity": 62})
        fly = Flyout({"ui": motion.state()})
        fly.apply_appearance()
        want = theme.panel_fill("mica", 62).alpha()
        assert fly.surface.fill().alpha() == want, "弹出面板底色没按不透明度计算"
        fly.deleteLater()

        # 4) 设置页联动：滑块 ↔ 输入框 ↔ 预览 ↔ 配置
        cfg = {"ui": dict(motion.state()), "profiles": []}
        page = settings_page.GeneralSettingsPage(cfg)
        page.show()
        _pump(60)

        page.opacity_slider.setValue(40)
        _pump(60)
        assert page.opacity_value.text() == "40", f"输入框没跟滑块同步：{page.opacity_value.text()!r}"
        assert page.preview.card.fill().alpha() == theme.panel_fill("mica", 40).alpha(), "预览没跟着滑块变"
        assert motion.panel_opacity() == 40, "拖动后应立即生效（内存态）"
        _pump(420)                                     # 防抖 320ms 后才落盘
        assert cfg["ui"].get("panel_opacity") == 40, \
            f"防抖落盘没生效：{cfg['ui'].get('panel_opacity')}"

        page.opacity_value.setText("76")
        page.opacity_value.editingFinished.emit()
        _pump(60)
        assert page.opacity_slider.value() == 76, "手输数值没同步回滑块"
        assert motion.panel_opacity() == 76

        page.opacity_value.setText("999")              # 程序化 setText 绕过 validator，靠夹取兜底
        page.opacity_value.editingFinished.emit()
        _pump(60)
        assert page.opacity_slider.value() == motion.OPACITY_MAX, "越界输入没夹到上限"

        page.opacity_value.setText("")
        page.opacity_value.editingFinished.emit()
        _pump(60)
        assert page.opacity_value.text() == str(motion.OPACITY_MAX), "空输入应退回当前值"

        page._opacity_save.stop()                      # 别让定时器在页面销毁后再写一次
        page.close()
        page.deleteLater()

        ui = default_config().get("ui") or {}
        assert "panel_opacity" in ui, "默认配置缺少 panel_opacity"
        assert ui["panel_opacity"] == motion.OPACITY_MAX, "默认应为完全不透明的 100%"
        detail = (f"{motion.OPACITY_MIN}–{motion.OPACITY_MAX}% / mica 100%→{theme.card_alpha('mica')}"
                  f" 50%→{theme.panel_fill('mica', 50).alpha()} / 联动与夹取正常")
    finally:
        settings_page.save_config = real_save
        motion.configure(old)
    return detail


def main() -> int:
    _ensure_app()
    for name, fn in (("协议插件注册", t_registry),
                     ("配置加载", t_config),
                     ("扫描已接入设备", t_scan),
                     ("检测已连接设备", t_detect),
                     ("提示词生成", t_prompt),
                     ("图标上传/清除", t_icon_roundtrip),
                     ("电量历史与预测", t_history),
                     ("30 天满电续航估算", t_endurance),
                     ("数据目录定位", t_data_dir),
                     ("低电量配色", t_icon_colors),
                     ("产品名一致性", t_branding),
                     ("设备行无 tooltip", t_flyout_tooltips),
                     ("充电闪电可分性", t_bolt_border),
                     ("面板列全部设备与自适应高度", t_flyout_resize),
                     ("读取状态收尾", t_reading_state),
                     ("刷新编排与超时恢复", t_scan_orchestration),
                     ("接入包导入", t_bundle_import),
                     ("外观与动效", t_motion),
                     ("面板不透明度", t_panel_opacity),
                     ("双语词典完整性", t_i18n)):
        check(name, fn)

    width = max(len(msg) for _, msg in RESULTS)
    for ok, msg in RESULTS:
        print(f"[{'PASS' if ok else 'FAIL'}] {msg:<{width}}")
    failed = sum(1 for ok, _ in RESULTS if not ok)
    print(f"\n{len(RESULTS) - failed}/{len(RESULTS)} 项通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
