"""设备身份归一：把同一块电池在不同连接方式下的读数归到同一个 key。

为什么需要：
  * 2.4G 接收器 / 有线 USB / 蓝牙在系统里是三个不同的 HID 设备，VID、PID、序列号都可能不同，
    但它们背后是同一块电池。历史库若直接按 HID uid 记账，切换连接方式就会多出一条曲线，
    并且同一时刻两路都在线时同一次放电会被记两遍。
  * 迈丛 V2 协议的回包里有「真机 VID / 型号 PID」，与当前走哪条通道无关，是最可靠的身份来源。

身份优先级（越靠前越稳）：
  1. ``hw-<真机VID>-<真机PID>``  协议回包读出的型号，跨连接方式一致
  2. ``nm-<VID>-<型号名>``       协议读不到型号时按「同厂商 + 同型号」归并
  3. ``sn-<VID>-<序列号>``       连型号名都没有时的兜底

为什么不用序列号做主要依据：同一块设备走 2.4G 接收器、有线、蓝牙时，系统报告的序列号
往往本来就不同（接收器有自己的序列号）。拿它当身份会把「一台设备换连接方式」又拆成三台，
与统计口径正好相反。代价是同型号多台同时使用会被合并计数——这比把一台设备拆开更可接受。
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

_HW = "hw"
_SN = "sn"
_NM = "nm"

# 只在名字里保留字母 / 数字 / 汉字，其余（空格、括号、连接方式后缀）一律去掉
_SLUG_KEEP = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")

# 连接方式噪声词：某些固件的 product_string 会带上当前通道
_CONN_NOISE = ("2.4g", "2.4 g", "bluetooth", "bt", "wireless", "有线", "无线", "蓝牙", "接收器")


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def normalize_serial(value: Any) -> str:
    """规范化序列号；全 0 / 占位串当作「没有序列号」。"""
    text = str(value or "").strip()
    if not text:
        return ""
    if set(text) <= {"0", "-", ":", "."}:
        return ""
    return _SLUG_KEEP.sub("", text.lower())[:32]


def slugify(text: Any) -> str:
    """型号名 → 身份片段：去掉连接方式噪声与标点，保留中英文与数字。"""
    raw = str(text or "").lower()
    for noise in _CONN_NOISE:
        raw = raw.replace(noise, "")
    return _SLUG_KEEP.sub("", raw)[:40]


def base_key(dev: Dict[str, Any]) -> str:
    """单台设备的身份（与当前连接方式无关）。"""
    vid = _int(dev.get("vendor_id"))
    real_vid = _int(dev.get("real_vid")) or vid
    real_pid = _int(dev.get("real_pid"))
    if real_pid:
        return f"{_HW}-{real_vid:04X}-{real_pid:04X}"

    name = dev.get("model") or dev.get("name") or dev.get("product") or ""
    part = slugify(name)
    if part:
        return f"{_NM}-{vid:04X}-{part}"

    serial = normalize_serial(dev.get("serial_number"))
    if serial:
        return f"{_SN}-{vid:04X}-{serial}"
    return f"{_NM}-{vid:04X}-unknown"


def device_keys(devices: Iterable[Dict[str, Any]]) -> List[str]:
    """批量算身份（保持顺序）。同型号多台会得到同一个 key，即合并统计。"""
    return [base_key(dev) for dev in devices]


def device_key(dev: Dict[str, Any]) -> str:
    """单台设备的身份。"""
    return base_key(dev)


def merge_readings(devices: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把同一轮扫描里属于同一台设备的读数合并成一条，供落库使用。

    合并规则（同一块电池，两路读数只差协议延迟）：
      * ``percent`` 取最大值，避免把一路偏低的读数当成真实掉电；
      * ``charging`` 取逻辑或，任一路报告在充电即视为充电中；
      * ``conn`` 取第一条非空值，仅作为「最近一次是通过什么连上的」展示；
      * ``sources`` 记录本轮被合并了几路，方便界面说明与自检断言。

    返回项的 ``uid`` 是第一条来源的 HID 标识，作为明细留档（不参与聚合）。
    """
    items = list(devices)
    keys = device_keys(items)
    merged: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []

    for dev, key in zip(items, keys):
        percent = dev.get("percent")
        if percent is None:
            continue
        try:
            percent = int(percent)
        except (TypeError, ValueError):
            continue
        if not 0 <= percent <= 100:
            continue

        entry = merged.get(key)
        if entry is None:
            uid = str(dev.get("uid") or
                      f"{_int(dev.get('vendor_id'))}:{_int(dev.get('product_id'))}")
            merged[key] = {
                "did": key,
                "uid": uid,
                "vid": _int(dev.get("vendor_id")),
                "vendor": str(dev.get("vendor") or ""),
                "name": str(dev.get("name") or dev.get("model") or "")[:64],
                "percent": percent,
                "charging": bool(dev.get("charging")),
                "conn": dev.get("connection"),
                "sources": 1,
            }
            order.append(key)
            continue

        entry["sources"] += 1
        entry["percent"] = max(entry["percent"], percent)
        entry["charging"] = entry["charging"] or bool(dev.get("charging"))
        if entry["conn"] is None and dev.get("connection") is not None:
            entry["conn"] = dev.get("connection")
        if not entry["name"] and dev.get("name"):
            entry["name"] = str(dev.get("name"))[:64]

    return [merged[k] for k in order]


def legacy_key(uid: str, name: str = "") -> str:
    """旧库（只有 HID uid，没有真机型号）的归并身份。

    旧 uid 形如 ``3837:2025:1234``：前两段是接收器 / 有线接口的 VID:PID，
    随连接方式变化，不能直接当身份；因此按「VID + 型号名」合并，
    让同一台设备在不同连接方式下的历史记录能连成一条。
    uid 不是 ``VID:PID:...`` 格式时不猜厂商，统一落到 ``0000``，
    免得同一个来源被算成两种身份。
    """
    head = (str(uid or "").split(":")[0] or "").strip()
    vid_part = head.upper() if re.fullmatch(r"[0-9A-Fa-f]{4}", head) else "0000"
    part = slugify(name)
    if part:
        return f"{_NM}-{vid_part}-{part}"
    return f"legacy-{uid}" if uid else ""
