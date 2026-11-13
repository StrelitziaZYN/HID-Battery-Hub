"""根据「已检测到的设备信息 + 用户填写的型号」生成接入提示词。

只给两样东西：本机实测到的设备事实、接入包格式。怎么查资料、试哪些命令、如何判定帧结构，
由接收这段提示词的 Agent 自己决定；型号由用户在对话框里填写（名称看不出型号时尤其必要）。
"""
from __future__ import annotations

from typing import Any, Dict, List

from . import branding, i18n

_PLUGIN_SKELETON = '''@register
class VendorProtocol(Protocol):
    id = "vendor-protocol"                 # 小写连字符，与文件名一致
    label = "厂商 · 协议"
    vendor = "厂商"
    description = "一帧查询，按偏移取电量"
    usage_pages = (0xFF00,)
    params = [Param("report_id", "报告 ID", "hex", 0x00)]   # 所有魔数进 params

    def query(self, dev, cfg: Dict[str, Any]) -> BatteryReading:   # 发一帧、收一帧、解析
        ...'''


def conn_text(device: Dict[str, Any]) -> str:
    """连接状态的一句话描述（提示词与界面共用）。"""
    supported = bool(device.get("supported", True))
    conn = device.get("connection")
    if supported and conn is not None:
        label = {1: i18n.T("有线 USB"), 2: i18n.T("2.4G 无线"),
                 3: i18n.T("蓝牙")}.get(conn, i18n.TF("未知（{}）", conn))
        bits = [label]
        if device.get("percent") is not None:
            bits.append(i18n.TF("电量 {}%", device['percent']))
        bits.append(i18n.T("充电中") if device.get("charging") else i18n.T("放电中"))
        return " · ".join(bits)
    if supported:
        return i18n.T("已接入协议，但本次未读到电量（可再点一次刷新）")
    return i18n.T("未知 —— 尚未接入协议，需由 HID 集合与命令响应推断")


def _identity_lines(device: Dict[str, Any], model: str) -> List[str]:
    vid = int(device.get("vendor_id") or 0)
    pid = int(device.get("product_id") or 0)
    lines = []
    if model:
        lines.append(f"- 设备型号：{model}（用户填写）")
    lines += [
        f"- 设备名称：{device.get('product') or device.get('name') or '未知设备'}",
        f"- 厂商：{device.get('vendor') or f'VID {vid:04X}'}",
        f"- VID : PID：0x{vid:04X} : 0x{pid:04X}",
        f"- 序列号：{device.get('serial_number') or '-'}",
        f"- 连接状态：{conn_text(device)}",
        f"- HID 集合数：{len(device.get('interfaces') or [])}",
    ]
    return lines


def _interface_lines(device: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    for idx, item in enumerate(device.get("interfaces") or [], start=1):
        lines.append(
            f"{idx}) Page 0x{int(item.get('usage_page') or 0):04X} · "
            f"Usage 0x{int(item.get('usage') or 0):04X} · "
            f"IN {item.get('input_len') or 0} / OUT {item.get('output_len') or 0} / "
            f"FEAT {item.get('feature_len') or 0}"
        )
    return lines or ["（没有读到集合信息）"]


def _pages_of(device: Dict[str, Any]) -> str:
    """实测到的 Usage Page（去重保序）；有厂商私有页就只列私有页，别把键盘页写进配置。"""
    seen: List[int] = []
    for item in device.get("interfaces") or []:
        page = int(item.get("usage_page") or 0)
        if page not in seen:
            seen.append(page)
    vendor = [p for p in seen if p >= 0xFF00]
    return ", ".join(str(p) for p in (vendor or seen)) or "0"


def _bundle_example(device: Dict[str, Any]) -> str:
    """接入包示例：手写紧凑格式，避免 json.dumps 把数组拆成多行撑长提示词。"""
    vendor = device.get("vendor") or "厂商"
    return (
        "{\n"
        f'  "kind": "{branding.BUNDLE_KIND}",\n'
        '  "version": 1,\n'
        '  "protocol": {"filename": "vendor_protocol.py",\n'
        '               "code_lines": ["完整插件源码，按行拆成字符串数组"]},\n'
        f'  "profile": {{"id": "vendor-protocol", "name": "{vendor} · 协议名",\n'
        f'              "vendor_id": {int(device.get("vendor_id") or 0)}, '
        f'"product_ids": [{int(device.get("product_id") or 0)}],\n'
        f'              "usage_pages": [{_pages_of(device)}],\n'
        '              "protocol": "vendor-protocol"}\n'
        "}"
    )


def build(device: Dict[str, Any], model: str = "") -> str:
    """生成可整段复制给 AI Agent 的接入提示词。model 为用户填写的型号（可空）。"""
    model = (model or "").strip()
    name = model or device.get("product") or device.get("name") or "未知设备"
    lines = [
        f"# 任务：让「{name}」在 {branding.APP_NAME} 显示电量",
        "",
        "接入这台设备，交一个可直接导入的接入包。方法自定；信息不足时先来问我，不要猜。",
        "没验证过的帧结构不要当成结论。",
        "",
        "## 设备实测",
        "",
        *_identity_lines(device, model),
        "",
        "HID 集合：",
        "",
        *_interface_lines(device),
        "",
        "## 接入包",
        "",
        "一个 JSON，命名 `<厂商>_<协议>.json`：",
        "",
        "```json",
        _bundle_example(device),
        "```",
        "",
        "- `protocol.filename` 与插件 `id` 对应（`vendor-protocol` ↔ `vendor_protocol.py`）。",
        "- `profile` 里其余扁平字段 = 插件 `params` 的 key（报告 ID、命令字、偏移…），"
        "上面的 `vendor_id` / `product_ids` / `usage_pages` 用实测值。",
        "- 一套协议覆盖多个型号时用 `profiles` 数组代替单个 `profile`。",
        "",
        "插件接口：",
        "",
        "```python",
        _PLUGIN_SKELETON,
        "```",
        "",
        "- 失败 `raise ProtocolError(...)`；电量归一化成 0–100 整数，读不到给 `None`。",
        "- 只用标准库 + `app.hid_core` / `app.protocols.base` / `app.protocols.registry`；"
        "不联网、不碰文件；一个文件一个类。",
        "",
    ]
    return "\n".join(lines)
