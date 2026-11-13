"""配置加载 / 保存 / 升级。

配置结构（v2）：
    {
      "config_version": 2,
      "refresh_interval": 60,
      "ui": {
        "material": "mica",          # solid | mica | acrylic（窗口材质）
        "motion": "full",            # full | subtle | off（动效强度）
        "hover": true,               # 悬停与按压反馈
        "transitions": true,         # 窗口与页面过渡
        "panel_opacity": 100         # 电量面板不透明度（30..100，百分比）
      },
      "profiles": [
        {
          "id": "mchose-v2",
          "name": "迈丛 MCHOSE · V2 协议",
          "vendor_id": 14391,
          "enabled": true,
          "usage_pages": [65281, 65408, 65291],
          "protocol": "mchose-v2",     # ← 指向 app/protocols/ 下的插件 id
          "models": {"16422": "A7 V3 Pro"},
          ...协议参数（由插件声明默认值，扁平存放）
        }
      ]
    }

协议参数不再写死在本文件，而是由插件 `ProtocolBase.params` 提供，
因此新增厂商时这里完全不需要改动。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from .i18n import T, TF
from .paths import APP_DIR, CONFIG_DIR

PROFILE_PATH = os.path.join(CONFIG_DIR, "profiles.json")

CONFIG_VERSION = 2

# 内置默认接口：只描述「厂商 + 用哪个插件 + 型号名映射」，参数由插件默认值补齐
_SEED = [
    {
        "id": "mchose-v2",
        "name": "迈丛 MCHOSE · V2 协议",
        "vendor_id": 14391,
        "enabled": True,
        "usage_pages": [0xFF01, 0xFF80, 0xFF0B],
        "protocol": "mchose-v2",
        "models": {
            "16422": "A7 V3 Pro",
            "8229": "G98 V3",
            "9504": "G98 V3",
            "16418": "A5 V3",
            "16419": "K7 V2",
            "16420": "L7 Ultra",
            "16421": "AX5 V2",
        },
    },
    {
        "id": "mchose-classic",
        "name": "迈丛 MCHOSE · 经典协议",
        "vendor_id": 14391,
        "enabled": False,
        "usage_pages": [0xFF01, 0xFF0B],
        "protocol": "mchose-classic",
        "models": {},
        # 旧款字段偏移与插件默认值不同，显式覆盖
        "battery_index": 8,
        "charging_index": 9,
    },
    {
        "id": "flydigi-newxinput",
        "name": "飞智 Flydigi · NewXInput",
        "vendor_id": 0x37D7,
        "enabled": True,
        # 只认手柄本体（Apex 5 = 0x2501），排除同 VID 的充电底座 CD2（0x6001）
        "product_ids": [0x2501],
        "usage_pages": [0xFFA0],
        "protocol": "flydigi-newxinput",
        "models": {},
    },
]


def _with_defaults(profile: Dict[str, Any]) -> Dict[str, Any]:
    """用插件声明的默认值补齐参数（插件未注册时原样返回）。"""
    from .protocols import get_protocol

    proto = get_protocol(str(profile.get("protocol", "")))
    if proto is None:
        return profile
    merged = {k: v for k, v in proto.defaults().items()}
    merged.update(profile)
    merged.setdefault("usage_pages", [p for p in proto.usage_pages])
    merged.setdefault("models", {})
    return merged


def _ui_defaults() -> Dict[str, Any]:
    """外观与动效默认值（单一来源在 motion.py，避免两处各写一份）。"""
    from .motion import defaults

    return defaults()


def default_config() -> Dict[str, Any]:
    return {
        "config_version": CONFIG_VERSION,
        "refresh_interval": 60,
        "record_interval": 300,
        "language": "zh",
        "ui": _ui_defaults(),
        "profiles": [_with_defaults(dict(p)) for p in _SEED],
    }


def ensure_config() -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    if not os.path.exists(PROFILE_PATH):
        save_config(default_config())


def _migrate(data: Dict[str, Any]) -> bool:
    """把 v1（protocol=report64 / feature_xor）升级到 v2 插件 id。"""
    from .protocols.registry import LEGACY_IDS

    changed = False
    for prof in data.get("profiles", []):
        pid = prof.get("protocol", "")
        if pid in LEGACY_IDS:
            prof["protocol"] = LEGACY_IDS[pid]
            changed = True
        if prof.get("protocol") == "mchose-v2":
            # v1 的 usage page 列表包含 0xFF0B，这里保留用户的实际值
            prof.setdefault("usage_pages", [0xFF01, 0xFF80, 0xFF0B])
    if int(data.get("config_version", 1)) < CONFIG_VERSION:
        data["config_version"] = CONFIG_VERSION
        changed = True
    return changed


def load_config() -> Dict[str, Any]:
    ensure_config()
    try:
        with open(PROFILE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:                              # noqa: BLE001
        data = default_config()

    if _migrate(data):
        try:
            save_config(data)
        except Exception:                          # noqa: BLE001
            pass

    data.setdefault("profiles", [])
    data.setdefault("refresh_interval", 60)
    data.setdefault("record_interval", 300)
    data.setdefault("config_version", CONFIG_VERSION)
    # 老配置没有 ui 段：就地补齐，用户下次改设置时自然写回文件
    ui = data.get("ui")
    data["ui"] = dict(ui) if isinstance(ui, dict) else {}
    for key, value in _ui_defaults().items():
        data["ui"].setdefault(key, value)
    data["profiles"] = [_with_defaults(p) for p in data["profiles"]]
    return data


def save_config(data: Dict[str, Any]) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = PROFILE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, PROFILE_PATH)


def profiles(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [p for p in data.get("profiles", []) if p.get("enabled", True)]


def new_profile(protocol_id: str, vendor_id: int = 0, index: int = 0) -> Dict[str, Any]:
    """按插件默认值创建一条新接口配置。"""
    from .protocols import get_protocol

    proto = get_protocol(protocol_id)
    base = {
        "id": f"{protocol_id}-{index + 1}",
        "name": TF("{} · 新接口", T(proto.label) if proto else protocol_id),
        "vendor_id": int(vendor_id),
        "enabled": True,
        "usage_pages": list(proto.usage_pages) if proto else [],
        "protocol": protocol_id,
        "models": {},
    }
    return _with_defaults(base)
