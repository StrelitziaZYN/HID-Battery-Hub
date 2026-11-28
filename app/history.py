"""电量历史：SQLite 采样存储 + 区间聚合 + 剩余可用时长预测。

统计口径（v1.6 起）：一切查询按「设备身份 did」聚合，而不是按 HID uid。
同一台设备换 2.4G / 有线 / 蓝牙后 uid 会变（VID、PID、接口号都可能变），但 did 不变，因此
  * 曲线连续：切换连接方式不会把历史断成两条；
  * 总量不重复：同一轮扫描里多路通道只落一条样本，节流也按 did 判断；
  * samples 仍保留 uid / conn，作为「这条样本从哪个通道来」的明细，随时可回溯。

其它要点：
  * 每台设备按 `interval` 节流写入（同一设备两条记录之间至少间隔 interval·0.9），
    采样由主程序的扫描回调驱动，不额外开 USB 通道。
  * 时间统一存 Unix 秒（本地时区无关）；查询时按桶（bucket）取平均，避免长区间点太多。
  * 预测只用「放电中」的样本做最小二乘，充电样本会把斜率拉正，必须排除。
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple

from .device_identity import legacy_key, merge_readings
from .i18n import T
from .paths import DATA_SUBDIR, HISTORY_DB

# 合法 HID uid 的厂商段：4 位十六进制
_VID_HEAD = re.compile(r"[0-9A-Fa-f]{4}")

# 可选的记录间隔（秒）：5 / 10 / 30 / 60 分钟
INTERVALS: Tuple[int, ...] = (300, 600, 1800, 3600)

# 图表区间：key → (跨度秒, 聚合桶秒, 轴标签格式)
RANGES: Dict[str, Tuple[int, int, str]] = {
    "24h": (24 * 3600, 900, "%H:%M"),
    "7d": (7 * 86400, 3 * 3600, "%m/%d"),
    "30d": (30 * 86400, 12 * 3600, "%m/%d"),
}

RETENTION_DAYS = 90

# 续航估算窗口：用最近 30 天的充放电循环，而不是只看当天
ENDURANCE_DAYS = 30
MIN_CYCLE_HOURS = 0.5          # 一次放电至少观察半小时，否则不参与估算
MIN_CYCLE_DROP = 20            # 至少掉了 20 个点，折算才不至于被噪声放大

# 库结构版本：v2 增加 did（设备身份）与 conn（连接方式）两列，v3 修正早期回填的偏差
SCHEMA_VERSION = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    did      TEXT    NOT NULL DEFAULT '',
    uid      TEXT    NOT NULL DEFAULT '',
    name     TEXT    NOT NULL DEFAULT '',
    percent  INTEGER NOT NULL,
    charging INTEGER NOT NULL DEFAULT 0,
    conn     INTEGER,
    ts       INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS devices (
    uid          TEXT PRIMARY KEY,
    did          TEXT    NOT NULL DEFAULT '',
    name         TEXT    NOT NULL DEFAULT '',
    vendor       TEXT    NOT NULL DEFAULT '',
    first_ts     INTEGER NOT NULL DEFAULT 0,
    last_ts      INTEGER NOT NULL DEFAULT 0,
    last_percent INTEGER
);
"""

_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_samples_did_ts ON samples(did, ts);
CREATE INDEX IF NOT EXISTS idx_samples_uid_ts ON samples(uid, ts);
CREATE INDEX IF NOT EXISTS idx_devices_did ON devices(did, last_ts);
"""


@contextmanager
def _db():
    os.makedirs(DATA_SUBDIR, exist_ok=True)
    conn = sqlite3.connect(HISTORY_DB, timeout=5.0)
    try:
        conn.executescript(_SCHEMA)
        _migrate(conn)
        conn.commit()
        yield conn
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# 结构迁移：旧库只有 uid，没有 did / conn
# --------------------------------------------------------------------------- #
def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(str(r[1]) == column for r in rows)


def _migrate(conn: sqlite3.Connection) -> None:
    """把旧库升级到当前结构：补列 → 建索引 → 按名称回填身份。

    回填的意义：旧库的 uid 会随连接方式变化，按 uid 分组等于「按连接模式分开统计」；
    按「VID + 型号名」回填后，同一台设备的历史记录立即并为一条。
    """
    version = int(conn.execute("PRAGMA user_version").fetchone()[0] or 0)
    if version >= SCHEMA_VERSION and _has_column(conn, "samples", "did"):
        return

    if not _has_column(conn, "samples", "did"):
        conn.execute("ALTER TABLE samples ADD COLUMN did TEXT NOT NULL DEFAULT ''")
    if not _has_column(conn, "samples", "conn"):
        conn.execute("ALTER TABLE samples ADD COLUMN conn INTEGER")
    if not _has_column(conn, "devices", "did"):
        conn.execute("ALTER TABLE devices ADD COLUMN did TEXT NOT NULL DEFAULT ''")
    conn.executescript(_INDEXES)

    if version < 2:
        _backfill(conn)
    if version < 3:
        _fix_legacy_keys(conn)

    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


def _backfill(conn: sqlite3.Connection) -> None:
    """给 v1 库补 did：按「VID + 型号名」归并同名的历史记录。"""
    for uid, name in conn.execute(
            "SELECT uid, MAX(name) FROM samples WHERE did='' GROUP BY uid").fetchall():
        conn.execute("UPDATE samples SET did=? WHERE uid=? AND did=''",
                     (legacy_key(str(uid), str(name or "")), uid))
    for uid, name in conn.execute(
            "SELECT uid, name FROM devices WHERE did=''").fetchall():
        conn.execute("UPDATE devices SET did=? WHERE uid=? AND did=''",
                     (legacy_key(str(uid), str(name or "")), str(uid)))


def _fix_legacy_keys(conn: sqlite3.Connection) -> None:
    """v2 早期回填把非 VID 格式 uid 的首段当成了厂商号，这里就地纠正。

    只处理「uid 不是 VID:PID:... 格式」的历史记录（自检/测试设备），
    正规设备的身份不受影响。
    """
    rows = conn.execute(
        "SELECT DISTINCT did, uid FROM samples "
        "WHERE did LIKE 'nm-%' OR did LIKE 'legacy-%'").fetchall()
    for did, uid in rows:
        head = str(uid or "").split(":")[0]
        if _VID_HEAD.match(head):
            continue
        name = conn.execute(
            "SELECT MAX(name) FROM samples WHERE did=? AND uid=?", (did, uid)).fetchone()[0]
        fixed = legacy_key(str(uid), str(name or ""))
        if fixed and fixed != did:
            conn.execute("UPDATE samples SET did=? WHERE did=? AND uid=?", (fixed, did, uid))
            conn.execute("UPDATE devices SET did=? WHERE did=? AND uid=?", (fixed, did, uid))


def _adopt_legacy(conn: sqlite3.Connection, did: str, vendor_id: int, name: str) -> int:
    """首次以新身份落库时，把旧库里同名设备的历史记录搬过来。

    旧库没有真机型号，只能按「VID + 型号名」归并；第一次见到这台设备的真机身份后
    做一次搬运，历史上按连接方式分开的记录就接上了，不留两条曲线。
    返回搬迁的样本数。
    """
    if not name:
        return 0
    old = legacy_key(f"{int(vendor_id or 0):04X}", name)
    if not old or old == did:
        return 0
    if conn.execute("SELECT 1 FROM samples WHERE did=? LIMIT 1", (old,)).fetchone() is None:
        return 0
    cur = conn.execute("UPDATE samples SET did=? WHERE did=?", (did, old))
    conn.execute("UPDATE devices SET did=? WHERE did=?", (did, old))
    return cur.rowcount or 0


# --------------------------------------------------------------------------- #
# 写入
# --------------------------------------------------------------------------- #
def record(devices: List[Dict[str, Any]], interval: int = 300,
           now: Optional[int] = None) -> int:
    """按间隔把本轮扫描结果落库；返回实际写入的样本数。

    同一轮里的多路通道（有线 + 2.4G 同时在线）先按设备身份合并成一条，
    因此「总量」不会因为设备同时被两条通道读到而翻倍。
    """
    moment = int(now if now is not None else time.time())
    interval = max(60, int(interval))
    samples = merge_readings(devices)
    if not samples:
        return 0

    saved = 0
    with _db() as conn:
        for item in samples:
            did = item["did"]
            last = conn.execute(
                "SELECT MAX(last_ts) FROM devices WHERE did=?", (did,)).fetchone()
            # 节流按设备身份判断：切换连接方式后 uid 变了，也不该多写一条
            if last and last[0] is not None and moment - int(last[0]) < interval * 0.9:
                continue

            known = conn.execute("SELECT 1 FROM devices WHERE did=? LIMIT 1", (did,)).fetchone()
            if known is None:
                _adopt_legacy(conn, did, int(item.get("vid") or 0), item["name"])

            conn.execute(
                "INSERT INTO samples(did, uid, name, percent, charging, conn, ts) "
                "VALUES(?,?,?,?,?,?,?)",
                (did, item["uid"], item["name"], item["percent"],
                 1 if item["charging"] else 0, item["conn"], moment))
            conn.execute(
                "INSERT INTO devices(uid, did, name, vendor, first_ts, last_ts, last_percent) "
                "VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(uid) DO UPDATE SET did=excluded.did, name=excluded.name, "
                "last_ts=excluded.last_ts, last_percent=excluded.last_percent",
                (item["uid"], did, item["name"], item.get("vendor") or "",
                 moment, moment, item["percent"]))
            saved += 1
    return saved


def prune(days: int = RETENTION_DAYS, now: Optional[int] = None) -> int:
    """删除超过保留期的样本，避免库无限增长。"""
    cutoff = int((now if now is not None else time.time()) - days * 86400)
    with _db() as conn:
        cur = conn.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
        return cur.rowcount or 0


# --------------------------------------------------------------------------- #
# 读取
# --------------------------------------------------------------------------- #
def devices() -> List[Dict[str, Any]]:
    """有历史记录的设备，按设备身份（did）合并，最近记录时间倒序。

    同一台设备换过连接方式时，这里只出现一次；``sources`` 是历史上出现过的连接来源数，
    ``conns`` 是连接方式种类数，``last_conn`` 是最近一次使用的连接方式。
    """
    with _db() as conn:
        rows = conn.execute(
            "SELECT d.did, d.name, d.vendor, d.last_ts, d.last_percent, "
            "       (SELECT COUNT(*) FROM samples s WHERE s.did = d.did), "
            "       (SELECT COUNT(DISTINCT s.uid) FROM samples s WHERE s.did = d.did), "
            "       (SELECT COUNT(DISTINCT s.conn) FROM samples s WHERE s.did = d.did "
            "        AND s.conn IS NOT NULL), "
            "       (SELECT s.conn FROM samples s WHERE s.did = d.did ORDER BY s.ts DESC LIMIT 1) "
            "FROM devices d "
            "WHERE NOT EXISTS (SELECT 1 FROM devices x WHERE x.did = d.did AND "
            "                 (x.last_ts > d.last_ts OR (x.last_ts = d.last_ts AND x.uid < d.uid))) "
            "ORDER BY d.last_ts DESC").fetchall()
    return [{"did": r[0], "name": r[1] or T("设备"), "vendor": r[2] or "",
             "last_ts": int(r[3]), "last_percent": r[4], "samples": int(r[5]),
             "sources": int(r[6]), "conns": int(r[7]), "last_conn": r[8]}
            for r in rows]


def has_data(did: str) -> bool:
    with _db() as conn:
        return conn.execute("SELECT 1 FROM samples WHERE did=? LIMIT 1", (did,)).fetchone() is not None


def sources(did: str) -> List[Dict[str, Any]]:
    """这台设备历史上用过的连接来源明细（uid / 连接方式 / 样本数 / 最近时间）。"""
    with _db() as conn:
        rows = conn.execute(
            "SELECT uid, COUNT(*), MIN(ts), MAX(ts), "
            "       (SELECT conn FROM samples x WHERE x.uid = s.uid ORDER BY x.ts DESC LIMIT 1) "
            "FROM samples s WHERE did=? GROUP BY uid ORDER BY MAX(ts) DESC", (did,)).fetchall()
    return [{"uid": r[0], "samples": int(r[1]), "first_ts": int(r[2]),
             "last_ts": int(r[3]), "conn": r[4]} for r in rows]


def points(did: str, range_key: str = "24h", now: Optional[int] = None
           ) -> List[Tuple[int, float]]:
    """返回 [(时间戳, 平均电量)]，按 range 对应的桶聚合。"""
    span, bucket, _ = RANGES.get(range_key, RANGES["24h"])
    end = int(now if now is not None else time.time())
    start = end - span
    with _db() as conn:
        rows = conn.execute(
            "SELECT (ts / ?) * ? AS t, AVG(percent) FROM samples "
            "WHERE did=? AND ts>=? GROUP BY t ORDER BY t",
            (bucket, bucket, did, start)).fetchall()
    return [(int(r[0]), float(r[1])) for r in rows]


def stats(did: str, range_key: str = "24h", now: Optional[int] = None) -> Dict[str, Any]:
    """区间汇总。count 按时间点去重，多路通道同时在线也不会把采样次数算两遍。"""
    span, _, _ = RANGES.get(range_key, RANGES["24h"])
    end = int(now if now is not None else time.time())
    start = end - span
    with _db() as conn:
        row = conn.execute(
            "SELECT MIN(percent), MAX(percent), AVG(percent), COUNT(DISTINCT ts), MAX(ts), "
            "       COUNT(DISTINCT uid), COUNT(DISTINCT conn) "
            "FROM samples WHERE did=? AND ts>=?", (did, start)).fetchone()
    return {"min": row[0], "max": row[1],
            "avg": round(row[2], 1) if row[2] is not None else None,
            "count": int(row[3] or 0), "last_ts": int(row[4]) if row[4] else None,
            "sources": int(row[5] or 0), "conns": int(row[6] or 0)}


def spans(did: str, range_key: str = "24h", now: Optional[int] = None
          ) -> Dict[str, Any]:
    """区间内的充电时段与充放电段数量。

    ``charging`` 是 [(起, 止)] 的充电时段，供图表打底色；切换连接方式不会切断时段，
    因为样本按 did 连续排列。
    """
    span, _, _ = RANGES.get(range_key, RANGES["24h"])
    end = int(now if now is not None else time.time())
    start = end - span
    with _db() as conn:
        rows = conn.execute(
            "SELECT ts, charging FROM samples WHERE did=? AND ts>=? ORDER BY ts",
            (did, start)).fetchall()

    periods: List[Tuple[int, int]] = []
    open_ts: Optional[int] = None
    last_ts: Optional[int] = None
    discharges = 0
    in_discharge = False
    for ts, charging in rows:
        ts = int(ts)
        if charging:
            if open_ts is None:
                open_ts = ts
            if in_discharge:
                discharges += 1
                in_discharge = False
            last_ts = ts
        else:
            if open_ts is not None:
                periods.append((open_ts, ts))
                open_ts = None
            in_discharge = True
            last_ts = ts
    if open_ts is not None:
        periods.append((open_ts, min(int(last_ts or open_ts) + 60, end)))
    if in_discharge:
        discharges += 1
    return {"charging": periods, "charges": len(periods), "discharges": discharges}


def _slope(pairs: List[Tuple[int, int]]) -> Optional[float]:
    """最小二乘斜率（百分点 / 小时），样本不足返回 None。"""
    if len(pairs) < 3:
        return None
    t0 = pairs[0][0]
    xs = [(p[0] - t0) / 3600.0 for p in pairs]
    ys = [float(p[1]) for p in pairs]
    if xs[-1] - xs[0] < 0.4:                      # 跨度太小，斜率没意义
        return None
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    den = sum((x - mx) ** 2 for x in xs)
    if den <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den


def _segments(rows) -> List[List[Tuple[int, int]]]:
    """把 (ts, percent, charging) 样本按充电动作切成若干「放电段」。

    30 天窗口里一定有多次充电，整段拟合会被充电造成的锯齿拉平，
    所以必须先按充电点切开，每段内单独算斜率。
    样本已按 did 归并，切换连接方式不会把一段放电切成两段。
    """
    segs: List[List[Tuple[int, int]]] = []
    cur: List[Tuple[int, int]] = []
    for ts, pct, charging in rows:
        if charging:
            if cur:
                segs.append(cur)
                cur = []
            continue
        cur.append((int(ts), int(pct)))
    if cur:
        segs.append(cur)
    return segs


def predict(did: str, now: Optional[int] = None) -> Dict[str, Any]:
    """用近 30 天（不够则用全部）的放电样本估算还能用多久。

    速率取各放电段斜率的中位数，避免某一次异常掉电把结论带偏。
    """
    end = int(now if now is not None else time.time())
    with _db() as conn:
        rows = conn.execute(
            "SELECT ts, percent, charging FROM samples WHERE did=? AND ts>=? ORDER BY ts",
            (did, end - ENDURANCE_DAYS * 86400)).fetchall()
        if len([r for r in rows if not r[2]]) < 3:
            rows = conn.execute(
                "SELECT ts, percent, charging FROM samples WHERE did=? "
                "ORDER BY ts DESC LIMIT 400", (did,)).fetchall()[::-1]
        last = conn.execute(
            "SELECT percent, charging, ts FROM samples WHERE did=? ORDER BY ts DESC LIMIT 1",
            (did,)).fetchone()

    if last is None:
        return {"ready": False, "reason": T("还没有记录")}
    percent, charging, last_ts = int(last[0]), bool(last[1]), int(last[2])
    if charging:
        return {"ready": False, "reason": T("正在充电"), "percent": percent}

    pairs = [(int(r[0]), int(r[1])) for r in rows if not r[2]]
    segs = _segments(rows)
    rates: List[float] = []
    for seg in segs:
        if len(seg) < 3 or seg[-1][0] - seg[0][0] < 0.4 * 3600:
            continue
        slope = _slope(seg)
        if slope is not None and -slope > 0.05:
            rates.append(-slope)

    if not rates:                                   # 分段算不出来，退回整体拟合
        slope = _slope(pairs)
        if slope is not None and -slope > 0.05:
            rates = [-slope]

    if not rates:
        if len(pairs) < 3:
            return {"ready": False, "reason": T("数据不足（至少需要 3 次放电采样）"),
                    "percent": percent}
        return {"ready": False, "reason": T("近 30 天电量基本没变化"),
                "percent": percent, "rate": 0.0}

    rates.sort()
    drain = rates[len(rates) // 2]
    span_h = (pairs[-1][0] - pairs[0][0]) / 3600.0
    return {
        "ready": True,
        "hours": min(percent / drain, 999.0),
        "rate": round(drain, 2),
        "percent": percent,
        "window_h": round(span_h, 1),
        "samples": len(pairs),
        "cycles": len(rates),
        "updated": last_ts,
    }


def endurance(did: str, now: Optional[int] = None, days: int = ENDURANCE_DAYS
              ) -> Dict[str, Any]:
    """估算「每次充满电后最长能用多久」，数据取最近 days 天（默认 30 天）。

    做法：把样本按 charging 切成充电段 / 放电段，一个放电段就是一次「充电后用机」：
      * 起点电量 ≥ 90%（确实充满再用），样本 ≥ 3 个，跨度 ≥ 0.5 小时；
      * 期间掉电 ≥ 20 个点，避免把待机 1% 的抖动当成放电；
      * 该段实测掉电速率 = 掉电量 / 实测小时数，再按此速率折算到 0%，
        得到这次循环「满电理论可用时长」。
    取所有循环里折算值最大的一次作为「最长可用」，同时给出中位数作参照。
    """
    end = int(now if now is not None else time.time())
    with _db() as conn:
        rows = conn.execute(
            "SELECT ts, percent, charging FROM samples WHERE did=? AND ts>=? ORDER BY ts",
            (did, end - days * 86400)).fetchall()

    segs = _segments(rows)

    cycles: List[Dict[str, Any]] = []
    for seg in segs:
        if len(seg) < 3:
            continue
        t0, p0 = seg[0]
        t1, p1 = seg[-1]
        hours = (t1 - t0) / 3600.0
        if hours < MIN_CYCLE_HOURS or p0 < 90:
            continue
        drop = p0 - p1
        if drop < MIN_CYCLE_DROP:
            continue
        rate = drop / hours                          # %/小时
        cycles.append({"measured_h": hours, "full_h": p0 / rate, "rate": rate,
                       "start": p0, "end": p1, "ts": t0})

    if not cycles:
        return {"ready": False, "days": days,
                "reason": T("近 {} 天还没有完整记录：充满后用到 20% 以下会记一次").format(days)}

    best = max(cycles, key=lambda c: c["full_h"])
    fulls = sorted(c["full_h"] for c in cycles)
    median = fulls[len(fulls) // 2]
    return {
        "ready": True,
        "hours": round(min(best["full_h"], 720.0), 1),
        "median": round(median, 1),
        "cycles": len(cycles),
        "days": days,
        "start": best["start"],
        "end": best["end"],
        "measured_h": round(best["measured_h"], 1),
        "rate": round(best["rate"], 2),
    }


# --------------------------------------------------------------------------- #
# 清除
# --------------------------------------------------------------------------- #
def clear_device(did: str) -> int:
    """清除一台设备（跨所有连接来源）的全部记录。"""
    with _db() as conn:
        cur = conn.execute("DELETE FROM samples WHERE did=?", (did,))
        conn.execute("DELETE FROM devices WHERE did=?", (did,))
        return cur.rowcount or 0


def clear_all() -> int:
    with _db() as conn:
        cur = conn.execute("DELETE FROM samples")
        conn.execute("DELETE FROM devices")
        return cur.rowcount or 0
