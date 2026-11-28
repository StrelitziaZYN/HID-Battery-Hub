"""扫描层：枚举 HID 集合 → 聚合成设备 → 用协议插件读电量 → 绑定用户图标。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import icon_store
from .hid_core import HidInterface, enumerate_interfaces, guess_device_type, load_caps
from .protocols import get_protocol, query_device

# 已知厂商名（HID 描述符里 manufacturer_string 为空时兜底）
VENDOR_NAMES = {
    0x3837: "MCHOSE 迈丛",
    0x37D7: "Flydigi 飞智",
    0x046D: "Logitech 罗技",
    0x1532: "Razer 雷蛇",
    0x05AC: "Apple",
    0x0B05: "ASUS 华硕",
    0x1B1C: "Corsair 美商海盗船",
    0x258A: "SINO WEALTH",
    0x05E3: "GENESYS",
    0x0416: "Winbond",
}


def vendor_name(vendor_id: int, manufacturer: str = "") -> str:
    name = (manufacturer or "").strip()
    return name or VENDOR_NAMES.get(int(vendor_id or 0), f"VID {int(vendor_id or 0):04X}")


def _clean_product(name: str) -> str:
    text = (name or "").strip()
    return " ".join(text.split()) or "未知设备"


def _model_name(profile: Dict[str, Any], real_pid: Optional[int], fallback: str) -> str:
    if real_pid is not None:
        hit = (profile.get("models") or {}).get(str(real_pid))
        if hit:
            return str(hit)
    name = _clean_product(fallback)
    for noise in ("MCHOSE", "2.4G", "2.4 G"):
        name = name.replace(noise, "")
    name = " ".join(name.split())
    return name or (f"PID {real_pid:04X}" if real_pid else "未知型号")


def icon_for(device: Dict[str, Any]) -> Optional[str]:
    """按「VID-型号PID」找用户上传的图标。"""
    pid = device.get("real_pid") or device.get("product_id")
    return icon_store.resolve(device.get("vendor_id", 0), pid)


def _pid_ok(profile: Dict[str, Any], interface: HidInterface) -> bool:
    """接口配置里可选的 product_ids 白名单（同一 VID 下有多个产品时用来精确匹配）。"""
    pids = [int(x) for x in (profile.get("product_ids") or [])]
    return not pids or interface.product_id in pids


# --------------------------------------------------------------------------- #
# 已接入协议的设备（主面板列表）
# --------------------------------------------------------------------------- #
def scan_devices(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    interfaces = enumerate_interfaces()
    profile_list = [p for p in config.get("profiles", []) if p.get("enabled", True)]
    results: List[Dict[str, Any]] = []
    seen: set = set()

    for profile in profile_list:
        vid = int(profile.get("vendor_id", 0))
        pages = [int(x) for x in profile.get("usage_pages", [])]
        proto = get_protocol(str(profile.get("protocol", "")))
        cands = [i for i in interfaces
                 if i.vendor_id == vid and _pid_ok(profile, i)
                 and (not pages or i.usage_page in pages)]
        pref = list(proto.usage_pages) if proto else []
        cands.sort(key=lambda i: (pref.index(i.usage_page) if i.usage_page in pref else 99,
                                  pages.index(i.usage_page) if i.usage_page in pages else 99))

        for iface in cands:
            uid = iface.uid
            if uid in seen:
                continue
            reading = query_device(iface, profile)
            if not reading.ok:
                continue
            seen.add(uid)

            model = _model_name(profile, reading.real_pid, iface.product_string)
            product = _clean_product(iface.product_string)
            device = {
                "uid": uid,
                "name": f"MCHOSE {model}" if iface.vendor_id == 0x3837 else product,
                "model": model,
                "product": product,
                "vendor": vendor_name(iface.vendor_id, iface.manufacturer),
                "device_type": guess_device_type(iface.product_string),
                "vendor_id": iface.vendor_id,
                "product_id": iface.product_id,
                "real_vid": reading.real_vid,
                "real_pid": reading.real_pid,
                "percent": reading.percent,
                "charging": bool(reading.charging),
                "connection": reading.connection,
                "profile_id": profile.get("id", ""),
                "profile_name": profile.get("name", ""),
                "protocol": profile.get("protocol", ""),
                "usage_page": iface.usage_page,
                "path": iface.path,
                "serial_number": iface.serial_number,
                "latency_ms": round(reading.latency_ms, 1),
                "raw": reading.raw.hex(" ") if reading.raw else "",
                "error": "",
            }
            device["icon_key"] = icon_store.key_of(
                device["vendor_id"], device["real_pid"] or device["product_id"])
            device["icon_path"] = icon_for(device)
            results.append(device)
    return results


# --------------------------------------------------------------------------- #
# 全部已连接的 HID 设备（接入新设备的第一步：先检测）
# --------------------------------------------------------------------------- #
def list_connected(config: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """列出所有物理 HID 设备（不论是否已接入协议）。

    只读描述符与报告长度，不发任何协议命令，因此不会打扰设备。
    """
    profiles = [p for p in (config or {}).get("profiles", []) if p.get("enabled", True)]
    groups: Dict[tuple, Dict[str, Any]] = {}
    raw_ifaces: Dict[tuple, List[HidInterface]] = {}

    for iface in enumerate_interfaces():
        key = (iface.vendor_id, iface.product_id, iface.serial_number or iface.product_string)
        group = groups.get(key)
        if group is None:
            group = groups[key] = {
                "uid": f"{iface.vendor_id:04X}:{iface.product_id:04X}:"
                       f"{iface.serial_number or iface.interface_number}",
                "name": _clean_product(iface.product_string),
                "product": _clean_product(iface.product_string),
                "vendor": vendor_name(iface.vendor_id, iface.manufacturer),
                "vendor_id": iface.vendor_id,
                "product_id": iface.product_id,
                "serial_number": iface.serial_number,
                "interfaces": [],
                "product_string": iface.product_string,
                "manufacturer": iface.manufacturer,
            }
            raw_ifaces[key] = []
        if len(group["interfaces"]) < 12:
            caps = load_caps(iface.path)
            group["interfaces"].append({
                "usage_page": iface.usage_page,
                "usage": iface.usage,
                "interface": iface.interface_number,
                "input_len": caps.input_len if caps else 0,
                "output_len": caps.output_len if caps else 0,
                "feature_len": caps.feature_len if caps else 0,
                "path": iface.path,
            })
        raw_ifaces[key].append(iface)

    devices: List[Dict[str, Any]] = []
    for key, group in groups.items():
        ifaces = raw_ifaces[key]
        profile = None
        for prof in profiles:
            proto = get_protocol(str(prof.get("protocol", "")))
            if int(prof.get("vendor_id", 0)) != group["vendor_id"] or proto is None:
                continue
            if any(proto.matches(i) and _pid_ok(prof, i) for i in ifaces):
                profile = prof
                break

        group["supported"] = profile is not None
        group["protocol"] = profile.get("protocol", "") if profile else ""
        group["profile_name"] = profile.get("name", "") if profile else ""
        group["percent"] = None
        group["charging"] = None
        group["connection"] = None
        group["device_type"] = guess_device_type(group["product"])
        group["icon_key"] = icon_store.key_of(group["vendor_id"], group["product_id"])
        group["icon_path"] = icon_store.path_of(group["icon_key"])
        devices.append(group)

    devices.sort(key=lambda d: (not d["supported"], d["vendor"], d["name"]))
    return devices


def enrich(device: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """给检测结果补上电量 / 连接方式（仅当该 VID 已接入协议）。"""
    if not device.get("supported"):
        return device

    profile = next((p for p in config.get("profiles", [])
                    if p.get("enabled", True)
                    and p.get("protocol") == device.get("protocol")
                    and int(p.get("vendor_id", 0)) == device["vendor_id"]), None)
    if profile is None:
        return device

    proto = get_protocol(str(profile.get("protocol", "")))
    for iface in enumerate_interfaces():
        if iface.vendor_id != device["vendor_id"] or iface.product_id != device["product_id"]:
            continue
        if proto is not None and not proto.matches(iface):
            continue
        reading = query_device(iface, profile)
        if not reading.ok:
            continue
        device = dict(device)
        device.update({
            "percent": reading.percent,
            "charging": bool(reading.charging),
            "connection": reading.connection,
            "real_vid": reading.real_vid,
            "real_pid": reading.real_pid,
            "model": _model_name(profile, reading.real_pid, iface.product_string),
            "raw": reading.raw.hex(" ") if reading.raw else "",
            "latency_ms": round(reading.latency_ms, 1),
        })
        device["icon_key"] = icon_store.key_of(device["vendor_id"],
                                               reading.real_pid or device["product_id"])
        device["icon_path"] = icon_store.path_of(device["icon_key"])
        return device
    return device


def connection_label(connection, supported: bool = True) -> str:
    if not supported or connection is None:
        return "未读取（尚未接入协议）"
    return {1: "有线 USB", 2: "2.4G 无线", 3: "蓝牙"}.get(connection, f"未知（{connection}）")


# --------------------------------------------------------------------------- #
# 调试用：把某厂商所有私有集合的回包打出来
# --------------------------------------------------------------------------- #
def probe_interfaces(vendor_id: int, profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    proto = get_protocol(str(profile.get("protocol", "")))
    for iface in enumerate_interfaces():
        if vendor_id and iface.vendor_id != vendor_id:
            continue
        if iface.usage_page < 0xFF00:
            continue
        reading = query_device(iface, profile)
        out.append({
            "usage_page": iface.usage_page,
            "usage": iface.usage,
            "product": iface.product_string,
            "product_id": iface.product_id,
            "ok": reading.ok,
            "percent": reading.percent,
            "charging": reading.charging,
            "connection": reading.connection,
            "error": reading.error,
            "raw": reading.raw.hex(" ") if reading.raw else "",
            "protocol": proto.id if proto else "",
        })
    return out
