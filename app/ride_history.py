from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

BJ_TZ = timezone(timedelta(hours=8))
DB_PATH = Path("/root/garmin_assistant/data/丛至/rides.db")

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS rides (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id     TEXT    UNIQUE NOT NULL,
    date            TEXT    NOT NULL,
    distance_km     REAL,
    duration_min    REAL,
    elevation_m     REAL,
    avg_power       REAL,
    np              REAL,
    ftp_at_time     REAL,
    if_value        REAL,
    vi              REAL,
    ef              REAL,
    tss             REAL,
    decoupling      REAL,
    avg_hr          REAL,
    avg_cadence     REAL,
    tsb_at_time     REAL,
    created_at      TEXT    NOT NULL
)
"""


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _conn() as conn:
        conn.execute(_CREATE_TABLE)


def _f(d: dict, *keys):
    """Extract first non-null numeric value from dict by key list."""
    for k in keys:
        v = d.get(k)
        if v is not None and v != "无数据":
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return None


def upsert_ride(activity_id: str, ride_data: dict, wellness_data: dict | None = None):
    """Insert or replace a ride record extracted from ride_data dict."""
    init_db()
    date_str = str(ride_data.get("日期") or "")[:10]
    with _conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO rides
            (activity_id, date, distance_km, duration_min, elevation_m,
             avg_power, np, ftp_at_time, if_value, vi, ef,
             tss, decoupling, avg_hr, avg_cadence, tsb_at_time, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                activity_id,
                date_str,
                _f(ride_data, "距离_km"),
                _f(ride_data, "移动时间_分钟"),
                _f(ride_data, "爬升_米"),
                _f(ride_data, "平均功率"),
                _f(ride_data, "标准化功率NP"),
                _f(ride_data, "当前FTP"),
                _f(ride_data, "强度因子IF"),
                _f(ride_data, "变异性指数VI"),
                _f(ride_data, "效率因子EF"),
                _f(ride_data, "TSS"),
                _f(ride_data, "有氧脱耦_%"),
                _f(ride_data, "平均心率"),
                _f(ride_data, "平均踏频"),
                _f(ride_data, "TSB"),
                datetime.now(BJ_TZ).isoformat(),
            ),
        )


def find_comparable_rides(
    distance_km: float,
    if_value: float,
    exclude_activity_id: str = "",
    limit: int = 10,
) -> list[dict]:
    """Return rides with distance ±20% and IF ±15%, newest first."""
    init_db()
    dist_lo, dist_hi = distance_km * 0.80, distance_km * 1.20
    if_lo, if_hi = if_value * 0.85, if_value * 1.15
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM rides
            WHERE distance_km BETWEEN ? AND ?
              AND if_value BETWEEN ? AND ?
              AND activity_id != ?
            ORDER BY date DESC
            LIMIT ?
            """,
            (dist_lo, dist_hi, if_lo, if_hi, exclude_activity_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_recent_rides(days: int = 60) -> list[dict]:
    init_db()
    cutoff = (datetime.now(BJ_TZ).date() - timedelta(days=days)).isoformat()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM rides WHERE date >= ? ORDER BY date DESC", (cutoff,)
        ).fetchall()
    return [dict(r) for r in rows]


def _avg(key: str, rides: list[dict]) -> float | None:
    vals = [r[key] for r in rides if r.get(key) is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def _pct(curr, hist) -> str | None:
    if curr is None or hist is None or hist == 0:
        return None
    v = round((curr - hist) / abs(hist) * 100, 1)
    return f"+{v}%" if v >= 0 else f"{v}%"


def compute_comparison(current_ride: dict, comparables: list[dict]) -> dict | None:
    """Compute delta metrics vs comparable historical rides."""
    if not comparables:
        return None

    result: dict = {
        "可对比次数": len(comparables),
        "对比说明": f"与近{len(comparables)}次类似骑行（距离±20%、强度±15%）对比",
        "最早对比日期": comparables[-1]["date"],
        "最近对比日期": comparables[0]["date"],
    }

    for label, key in [
        ("平均功率_W", "avg_power"),
        ("标准化功率NP_W", "np"),
        ("效率因子EF", "ef"),
    ]:
        curr = current_ride.get(key)
        hist = _avg(key, comparables)
        if curr and hist:
            result[label] = {"本次": curr, "历史均值": hist, "变化": _pct(curr, hist)}

    # decoupling: lower is better, so sign interpretation is reversed
    curr_dc = current_ride.get("decoupling")
    hist_dc = _avg("decoupling", comparables)
    if curr_dc is not None and hist_dc is not None:
        delta = round(curr_dc - hist_dc, 1)
        result["有氧脱耦_%"] = {
            "本次": curr_dc,
            "历史均值": hist_dc,
            "变化": f"{'+' if delta >= 0 else ''}{delta}%（{'偏高/变差' if delta > 1 else '改善' if delta < -1 else '持平'}）",
        }

    # Recent trend: newest 3 vs older ones
    if len(comparables) >= 4:
        recent3 = comparables[:3]
        older = comparables[3:]
        r_pw = _avg("avg_power", recent3)
        o_pw = _avg("avg_power", older)
        if r_pw and o_pw:
            trend = _pct(r_pw, o_pw)
            direction = "上升" if r_pw >= o_pw else "下降"
            result["近期功率趋势"] = f"近3次类似骑行均功率{direction}{abs(round((r_pw-o_pw)/o_pw*100,1))}%（{o_pw:.0f}W→{r_pw:.0f}W）"

    return result


def build_cycling_progression_summary(days: int = 60) -> dict:
    """Build a cycling progression dict for the user profile update."""
    rides = get_recent_rides(days)
    if not rides:
        return {}

    def _vals(key):
        return [r[key] for r in rides if r.get(key) is not None]

    powers = _vals("avg_power")
    if_vals = _vals("if_value")
    decouplings = _vals("decoupling")
    tss_vals = _vals("tss")
    ftps = _vals("ftp_at_time")
    distances = _vals("distance_km")

    result: dict = {"统计骑行次数": len(rides), "统计周期": f"近{days}天"}

    if powers:
        result["平均功率范围_W"] = f"{min(powers):.0f}–{max(powers):.0f}"
        result["平均功率均值_W"] = round(sum(powers) / len(powers), 1)
    if if_vals:
        result["IF范围"] = f"{min(if_vals):.2f}–{max(if_vals):.2f}"
    if tss_vals:
        result["单次TSS范围"] = f"{min(tss_vals):.0f}–{max(tss_vals):.0f}"
    if distances:
        result["骑行距离范围_km"] = f"{min(distances):.0f}–{max(distances):.0f}"
    if ftps and len(ftps) >= 2:
        # rides sorted newest-first
        ftp_change = ftps[0] - ftps[-1]
        if abs(ftp_change) >= 2:
            direction = "提升" if ftp_change > 0 else "下降"
            result["FTP变化"] = f"{direction}{abs(ftp_change):.0f}W（{ftps[-1]:.0f}→{ftps[0]:.0f}）"
        result["当前FTP"] = ftps[0]
    if decouplings:
        result["有氧脱耦均值_%"] = round(sum(decouplings) / len(decouplings), 1)

    return result
