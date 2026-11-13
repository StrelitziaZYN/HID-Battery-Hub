"""轻量双语支持：中文原文作键，英文走词典，界面在语言切换后由主程序整窗重建。

- T(s)：zh 原样返回，en 查词典（缺条目时回落中文，便于渐进补齐）；
- TF(s, *args, **kwargs)：先翻译再 format，{} 模板占位；
- set_language() / lang()：全局语言状态，"zh" | "en"。
"""
from __future__ import annotations

LANGUAGES = (("zh", "中文"), ("en", "English"))

_current = "zh"

_EN: dict[str, str] = {
    # ---- 托盘 / 主程序 ----
    "打开面板": "Open Panel",
    "设置": "Settings",
    "开机自启动": "Launch at Startup",
    "退出": "Quit",
    "已驻留系统托盘，左键点击电池图标即可查看设备电量。":
        "Now running in the system tray. Click the battery icon to check device battery levels.",
    "上次刷新 {} 台设备": "{} device(s) refreshed",
    "未检测到设备": "No devices detected",
    "设备": "Device",
    "设备无响应，稍后自动重试": "Device not responding — will retry automatically",
    "读取超时，稍后自动重试": "Read timed out — will retry automatically",
    "{} · 未检测到设备": "{} · No devices detected",
    "系统托盘不可用": "System tray is not available",

    # ---- 导航 ----
    "通用设置": "General",
    "电量统计": "Battery Stats",
    "接口设置": "Interfaces",
    "立即刷新": "Refresh Now",

    # ---- 弹出面板（DevicesPage）----
    "电量统计（按钮提示）": "Battery stats",
    "刷新（设备清单 + 电量）": "Refresh (devices + battery)",
    "打开设置": "Open settings",
    "打开设置窗口": "Open settings window",
    "尚未读取": "Not read yet",
    "未检测到 2.4G 设备": "No 2.4G devices detected",
    "请确认接收器已插入，或在设置里「接入新设备」":
        "Make sure the USB receiver is plugged in, or add a new device in Settings",
    "正在读取设备…": "Reading devices…",
    "正在刷新设备清单与电量": "Refreshing device list and battery levels",
    "{} 台设备 · 上次更新 {}": "{} device(s) · last update {}",
    "上次读取 {}": "Last read {}",
    "已完成读取 · {} 台设备": "Read complete · {} device(s)",
    "最低 {}%": "lowest {}%",
    "{} 台充电中": "{} charging",
    "更新于 {}": "updated {}",
    "已完成读取 · 未检测到设备": "Read complete · no devices detected",
    "等待首次读取": "Waiting for first read",

    # ---- 设备行 / 连接描述 ----
    "未知设备": "Unknown device",
    "有线 USB": "Wired USB",
    "2.4G 无线": "2.4G wireless",
    "蓝牙": "Bluetooth",
    "连接未知": "Connection unknown",
    "充电中": "Charging",
    "放电中": "Discharging",
    "协议 {}": "protocol {}",
    "未知（{}）": "unknown ({})",
    "电量 {}%": "battery {}%",
    "已接入协议，但本次未读到电量（可再点一次刷新）":
        "Protocol onboarded, but no battery was read this time (try Refresh again)",
    "未知 —— 尚未接入协议，需由 HID 集合与命令响应推断":
        "Unknown — not onboarded yet; must be inferred from HID collections and command responses",

    # ---- 通用设置页 ----
    "窗口材质、动效，以及刷新与开机行为":
        "Window material, motion, refresh & startup",
    "外观与动效": "Appearance & Motion",
    "窗口材质与界面动效，改动立即生效":
        "Window material and UI motion — changes apply immediately",
    "窗口材质": "Window material",
    "电量面板不透明度": "Panel opacity",
    "拖动即预览；低于 50% 时次要文字会变淡，投影始终保持不透明":
        "Drag to preview; below 50% secondary text fades, while the shadow stays solid",
    "2.4G 无线 · 示意数据": "2.4G wireless · sample data",
    "纯色": "Solid",
    "Mica 取壁纸的淡色，Acrylic 更明显；需要 Windows 11":
        "Mica takes a soft tint from the wallpaper; Acrylic is stronger. Requires Windows 11",
    "动效强度": "Motion intensity",
    "标准": "Standard",
    "柔和": "Subtle",
    "无动效": "None",
    "标准约 250ms，柔和减半，关闭则界面瞬时切换":
        "Standard ≈ 250 ms, subtle is half, off switches instantly",
    "悬停与按压反馈": "Hover & press feedback",
    "按钮与列表行在鼠标移入时渐显底色":
        "Buttons and list rows fade in their background on hover",
    "窗口与页面过渡": "Window & page transitions",
    "弹出面板淡入淡出，切换标签页时淡入":
        "The flyout fades in and out, pages fade in when switching",
    "已回退纯色": "Fell back to solid",
    "当前系统不支持窗口材质（{}），按纯色显示":
        "This system can't do window materials ({}); showing solid instead",
    "材质由 Windows 11 提供；弹出面板跟随同一套通透度，不做二次模糊":
        "Materials come from Windows 11; the flyout follows the same translucency without a second blur",
    "刷新与启动": "Refresh & Startup",
    "设备清单的刷新节奏与开机行为":
        "How often the device list refreshes, and startup behavior",
    "30 秒": "30 s",
    "1 分钟": "1 min",
    "2 分钟": "2 min",
    "5 分钟": "5 min",
    "刷新间隔": "Refresh interval",
    "设备清单与电量读数一起按此间隔刷新":
        "Device list and battery levels refresh together on this interval",
    "开机自动启动": "Start with Windows",
    "登录 Windows 后自动驻留托盘": "Stay in the tray after signing in to Windows",
    "恢复默认": "Reset to Defaults",
    "恢复默认设置": "Reset settings",
    "清空接口配置与偏好设置，回到出厂状态":
        "Clear interface profiles and preferences, back to factory state",
    "写入注册表失败，可能被安全软件拦截":
        "Failed to write the registry — possibly blocked by security software",
    "语言 / Language": "语言 / Language",
    "切换后立即生效": "Takes effect immediately",

    # ---- 接口设置页 ----
    "协议插件决定怎么读电量，接入包让新设备一次装好，图标与调试也在本页完成":
        "Protocol plugins decide how battery is read; access bundles set up new devices in one go; icons and debugging live here too",
    "{} 个协议插件 · {} 套接口": "{} protocol plugin(s) · {} profile(s)",
    "每套接口 = 一个厂商协议 + 一组参数，可同时启用多个":
        "Each profile = one vendor protocol + parameters; enable multiple at once",
    "{}/{} 已启用": "{}/{} enabled",
    "还没有接口配置：点下面的「新增接口」，或到「设备接入」导入一个接入包":
        "No profiles yet: click \"Add Profile\" below, or import an access bundle under Device Onboarding",
    "新增接口": "Add Profile",
    "关掉开关的接口不参与读取，配置仍会保留":
        "Disabled profiles are skipped when reading; their settings are kept",
    "设备接入": "Device Onboarding",
    "给还没适配的设备生成接入包，插件代码由 AI 写":
        "Generate an access bundle for unsupported devices — the plugin code is written by AI",
    "检测设备": "Detect device",
    "点「检测设备并生成提示词」，工具会读出这台设备的接口集合与读取要点":
        "Click \"Detect Device & Build Prompt\" — the tool reads this device's HID collections and what matters for reading",
    "交给 AI": "Hand to AI",
    "把提示词粘给任意大模型，它会返回一个 .json 接入包":
        "Paste the prompt into any LLM; it will return a .json access bundle",
    "导入接入包": "Import Bundle",
    "插件与接口配置自动装好，无需手动改文件":
        "The plugin and profile are installed automatically — no manual file edits",
    "检测设备并生成提示词": "Detect Device & Build Prompt",
    "设备图标": "Device Icons",
    "图标由你自己上传，主面板与托盘都会用它":
        "Upload icons yourself; they are used on the panel and in the tray",
    "{} 张已上传": "{} uploaded",
    "支持 PNG/JPG，自动裁到 128px，存在本机 assets/icons/；未上传的设备不显示图标。":
        "PNG/JPG supported, auto-cropped to 128 px, stored in assets/icons/; devices without an uploaded icon show none.",
    "暂无可设置图标的设备：先让主面板检测到设备再回到这里":
        "No devices to customize yet — let the main panel detect a device first",
    "接口调试": "Interface Debug",
    "手动读一次私有集合，结果可复制给 AI 分析":
        "Manually read the private collections once; copy the results for AI analysis",
    "导出示例接入包": "Export Example Bundle",
    "打开数据目录": "Open Data Folder",
    "数据目录：{}（{}）\n用户插件：{}": "Data folder: {} ({})\nUser plugins: {}",
    "当前 EXE 所在路径": "next to the current EXE",
    "程序所在目录（源码运行）": "program directory (running from source)",
    "程序目录：": "Program folder: ",
    "用户插件：": "User plugins: ",
    "选择设备图标": "Choose a device icon",
    "图片 (*.png *.jpg *.jpeg *.webp *.bmp *.ico)":
        "Images (*.png *.jpg *.jpeg *.webp *.bmp *.ico)",
    "上传失败：{}": "Upload failed: {}",
    "选择 AI 接入包": "Choose an AI access bundle",
    "接入包 (*.json *.py);;JSON 配置 (*.json);;协议插件 (*.py);;所有文件 (*.*)":
        "Access bundles (*.json *.py);;JSON config (*.json);;Protocol plugins (*.py);;All files (*.*)",
    "导入失败：{}": "Import failed: {}",
    "已导入：插件 {} · 接口 {}。\n若主面板还没出现设备，点一下刷新。":
        "Imported: plugin {} · profiles {}.\nIf no device shows up on the panel, click Refresh.",
    "（未附带接口配置，请手动新增接口）":
        "(no profiles in the bundle — add them manually)",
    "以下插件加载失败（可修正后重启）：{}":
        "Failed to load plugin(s) (fix and restart): {}",
    "示例接入包已写入：{}": "Example bundle written to: {}",

    # ---- 接口编辑对话框 / 协议选择 ----
    "选择协议插件": "Choose a Protocol Plugin",
    "取消": "Cancel",
    "下一步": "Next",
    "接口参数": "Profile Parameters",
    "名称": "Name",
    "厂商 VID (十进制)": "Vendor VID (decimal)",
    "Usage Page 列表": "Usage Page list",
    "协议插件": "Protocol plugin",
    "协议参数": "Protocol Parameters",
    "协议参数（插件未注册，请用 JSON 编辑）":
        "Protocol parameters (plugin not registered — edit as JSON)",
    "协议参数 · {}": "Protocol Parameters · {}",
    "高级参数 (JSON)": "Advanced (JSON)",
    "收起高级参数": "Hide advanced",
    "保存": "Save",
    "是": "Yes",
    "否": "No",
    "JSON 解析失败：{}": "JSON parse failed: {}",
    "未命名接口": "Unnamed profile",
    "插件 {}": "Plugin {}",
    "{}（未注册）": "{} (unregistered)",
    "编辑": "Edit",
    "删除": "Delete",
    "上传图标": "Upload icon",
    "清除图标": "Clear icon",
    "返回": "Back",
    "VID {} · PID {} · 图标{}": "VID {} · PID {} · icon {}",
    "已上传": "uploaded",
    "未上传": "not uploaded",

    # ---- 协议插件声明文本（label / 描述 / 参数）----
    "2.4G 设备电量监测": "2.4G device battery monitor",
    "迈丛 MCHOSE · V2 协议": "MCHOSE · V2 protocol",
    "迈丛 MCHOSE · 经典协议": "MCHOSE · Classic protocol",
    "飞智 Flydigi · NewXInput": "Flydigi · NewXInput",
    "{} · 新接口": "{} · new profile",
    "32 字节报告 + 5A A5 帧头，命令 0x01 查设备信息，电量在高 4 位状态 + 低 4 位档位。":
        "32-byte reports with a 5A A5 header; command 0x01 reads device info; battery is the high 4 bits of status plus the low 4-bit level.",
    "Feature Report + 0xFF 反码的旧款协议，电量档位 1..10。":
        "Legacy protocol using Feature Reports with 0xFF complement; battery levels 1..10.",
    "64 字节 Output Report + XOR 校验，命令 0x0900 查电量，可从回包读出型号。":
        "64-byte Output Reports with XOR checksum; command 0x0900 reads battery; the model can be parsed from the reply.",
    "厂商 · 协议": "Vendor · Protocol",
    "厂商": "Vendor",
    "一句话描述帧结构与电量命令": "One-line description of frame layout and battery command",
    "报告 ID": "Report ID",
    "报告长度": "Report size",
    "电量命令": "Battery command",
    "回包数据起点": "RX payload offset",
    "电量字节下标": "Battery byte index",
    "充电字节下标": "Charging byte index",
    "连接方式下标": "Connection byte index",
    "输出报告 ID（Apex 5 = 0x03；无编号报告填 0x00）":
        "Output report ID (Apex 5 = 0x03; use 0x00 for reportless)",
    "含报告 ID 的总字节数": "Total bytes including the report ID",
    "帧头 Magic": "Frame magic",
    "0x01 = get info": "0x01 = get info",
    "超时 (ms)": "Timeout (ms)",
    "重试次数": "Retries",
    "回包 5A 下标": "5A index in reply",
    "原始回包里 0x5A 的位置：有报告 ID 时为 1，无编号报告时为 0":
        "Position of 0x5A in the raw reply: 1 with a report ID, 0 for reportless",
    "相对 5A 起算": "Counted from the 5A byte",
    "状态字节下标": "Status byte index",
    "与电量同字节的高 4 位": "High 4 bits of the battery byte",
    "档位倍率": "Level scale",
    "低 4 位档位 × 该倍率 = 百分比（Apex 6 为 10）":
        "Low 4-bit level × this scale = percent (10 on Apex 6)",
    "状态位右移": "Status shift",
    "档位掩码": "Level mask",
    "无线编码值": "Wireless code",
    "连接字节等于该值判为 2.4G，其余 0/1 判为有线":
        "Connection byte == this value means 2.4G; 0/1 mean wired",
    "设备未响应": "Device did not respond",
    "设备未响应（0x%02X 无 5A A5 回包）": "Device did not respond (no 5A A5 reply to 0x%02X)",
    "未知协议 {}（插件未注册）": "Unknown protocol {} (plugin not registered)",
    "打开设备失败：{}": "Failed to open device: {}",
    "通信异常：{}": "Communication error: {}",

    # ---- 电量统计页 ----
    "24 小时": "24 hours",
    "7 天": "7 days",
    "30 天": "30 days",
    "续航趋势、掉电速率与采样间隔":
        "Endurance trends, drain rate & sampling interval",
    "刷新图表": "Refresh chart",
    "记录间隔": "Record interval",
    "按此间隔采样，保留 {} 天": "Sample on this interval, keep {} days",
    "100 小时以上": "100+ hours",
    "{} 分钟": "{} min",
    "{} 小时": "{} h",
    "{} 小时 {} 分": "{} h {} min",
    "{} 天 {} 小时": "{} d {} h",
    "{} 天": "{} d",
    "{}：最低 {}% · 最高 {}% · 平均 {}% · {} 次采样":
        "{}: min {}% · max {}% · avg {}% · {} samples",
    "该区间还没有采样": "No samples in this range yet",
    "预计还能用 {}": "Estimated time left: {}",
    "当前 {}% · 近 {} 天 {} 段放电 · 平均掉电 {}/小时 · 数据覆盖 {}":
        "Now {}% · {} discharge segment(s) in the last {} days · avg drain {} per hour · data covers {}",
    "暂无预测": "No prediction yet",
    "当前 {}% · 继续积累放电记录后会自动给出估算":
        "Now {}% — an estimate will appear once more discharge records accumulate",
    "需要至少 3 次放电采样，且间隔覆盖半小时以上":
        "Needs at least 3 discharge samples spanning half an hour or more",
    "满电最长可用 {}": "Full-charge endurance: {}",
    "近 {} 天 {} 次充电循环 · 中位 {} · 最长一次 {}%→{}% 实测 {} 小时":
        "{} charge cycle(s) in the last {} days · median {} · longest {}%→{}% measured {} h",
    "满电续航：待积累": "Full-charge endurance: accumulating",
    "还没有历史记录。\n程序会按下方「记录间隔」自动采样，积累几次后这里会出现曲线和预测。":
        "No history yet.\nThe app samples automatically on the \"Record interval\" below; curves and predictions appear after a few samples.",
    "清除该设备记录": "Clear This Device",
    "全部清除": "Clear All",
    "再点一次确认清除": "Click again to confirm",
    "再点一次确认": "Click again to confirm",
    "5 分钟": "5 min",
    "10 分钟": "10 min",
    "1 小时": "1 h",
    "%/小时": "%/h",
    "{} %/小时": "{} %/h",
    "当前 {}% · 近 {} 天 {} 段放电 · 平均掉电 {} · 数据覆盖 {}":
        "Now {}% · {} discharge segment(s) in the last {} days · avg drain {} · data covers {}",
    "数据目录：{}（{}）": "Data folder: {} ({})",
    "2.4G": "2.4G",
    "未知": "Unknown",

    # ---- 预测原因（history.py）----
    "还没有记录": "No records yet",
    "正在充电": "Charging now",
    "数据不足（至少需要 3 次放电采样）":
        "Not enough data (needs at least 3 discharge samples)",
    "近 30 天电量基本没变化": "Battery barely changed in the last 30 days",
    "近 {} 天还没有完整记录：充满后用到 20% 以下会记一次":
        "No complete cycle in the last {} days: one is recorded after each full charge drains below 20%",

    # ---- 接口调试（ProbePanel）----
    "尚未调试": "Not run yet",
    "开始调试": "Start Debug",
    "复制结果": "Copy Results",
    "清空": "Clear",
    "点击「开始调试」才会对已启用接口的厂商私有集合（Usage Page ≥ 0xFF00）发一次电量命令；平时不发送任何探测包。":
        "Only \"Start Debug\" sends one battery command to the enabled profiles' vendor-private collections (Usage Page ≥ 0xFF00); nothing is sent otherwise.",
    "调试结果会显示在这里（仅在点击「开始调试」后产生）":
        "Debug results will appear here (only produced after clicking \"Start Debug\")",
    "正在调试…": "Probing…",
    "有线": "Wired",
    "电量 {}% · {} · {}": "Battery {}% · {} · {}",
    "没有可调试的厂商私有集合（Usage Page ≥ 0xFF00）。\n请确认接收器已插入，或在上方启用对应厂商接口。":
        "No vendor-private collections to probe (Usage Page ≥ 0xFF00).\nMake sure the receiver is plugged in, or enable the vendor profile above.",
    "调试完成 · 0 个集合": "Done · 0 collections",
    "调试完成 · {} 个接口 · {} 个私有集合": "Done · {} profile(s) · {} private collection(s)",
    "已复制到剪贴板": "Copied to clipboard",

    # ---- 设备接入对话框（PromptDialog）----
    "接入新设备": "Add a New Device",
    "关闭": "Close",
    "① 检测已连接设备　② 生成提示词交给 AI　③ 导入 AI 返回的接入包":
        "① Scan connected devices   ② Build a prompt for AI   ③ Import the returned access bundle",
    "① 已连接的 HID 设备": "① Connected HID devices",
    "① 已连接的 HID 设备（{}）": "① Connected HID devices ({})",
    "重新检测": "Re-scan",
    "设备型号": "Device model",
    "名称看不出型号时填这里，例如 MCHOSE A7 V3 Pro；留空则由 AI 自行判断":
        "Type the exact model if the name is generic, e.g. MCHOSE A7 V3 Pro; leave blank to let the AI decide",
    "② 接入提示词": "② Access Prompt",
    "提示词只包含本机实测信息，并要求 AI 返回一个 JSON 接入包；拿到后点「导入接入包」选中它，插件与接口配置会自动装好。":
        "The prompt contains only locally measured facts and asks the AI to return a JSON access bundle; once you have it, click \"Import Bundle\" and the plugin and profiles install automatically.",
    "复制提示词": "Copy Prompt",
    "正在检测…": "Scanning…",
    "已支持": "supported",
    "未接入": "not onboarded",
    "检测到 {} 个 HID 设备": "{} HID device(s) detected",
    "未检测到任何 HID 设备，请插入接收器后点「重新检测」。":
        "No HID devices detected. Plug in the receiver and click \"Re-scan\".",
    "没有可用设备": "No devices available",
    "正在补全设备信息…": "Fetching device details…",
    "{} 行 · {} 字": "{} lines · {} chars",
    "提示词已生成": "Prompt ready",
    "提示词已按型号「{}」生成": "Prompt built for model \"{}\"",
    "已复制到剪贴板，粘贴给 AI Agent 即可": "Copied — paste it to your AI agent",
    "选择 AI 返回的接入包": "Choose the AI's access bundle",
    "接入包 (*.json *.py);;JSON (*.json);;插件 (*.py);;所有文件 (*.*)":
        "Access bundles (*.json *.py);;JSON (*.json);;Plugins (*.py);;All files (*.*)",
    "已导入插件 {} · 接口 {}": "Imported plugin {} · profiles {}",
    "（未带接口配置，需在设置页新增接口）":
        "(no profiles in the bundle — add profiles in Settings)",
}


def lang() -> str:
    """当前语言："zh" | "en"。"""
    return _current


def set_language(value: str) -> None:
    global _current
    _current = "en" if value == "en" else "zh"


def T(s: str) -> str:
    """翻译一条 UI 文案：zh 原样返回；en 查词典，缺条目回落中文。"""
    if _current == "zh":
        return s
    return _EN.get(s, s)


def TF(s: str, *args, **kwargs) -> str:
    """翻译 {} 模板后再格式化。"""
    return T(s).format(*args, **kwargs)
