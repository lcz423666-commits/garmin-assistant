"""佳明健康助手 — Function Calling 工具集（ICU + Garmin 双源）。"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

BJ_TZ = timezone(timedelta(hours=8))
ICU_SLEEP_DIR = Path("/root/garmin_assistant/data/congzhi/icu_sleep")
ICU_CYCLING_DIR = Path("/root/garmin_assistant/data/congzhi/icu_cycling")
GARMIN_ACTIVITY_DIR = Path("/root/garmin_assistant/data/丛至/activity")
REPORTS_DIR = Path("/root/garmin_assistant/reports/daily")

# ── OpenAI 格式的工具定义 ─────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_activities",
            "description": "列出最近 N 天的骑行/运动记录摘要（日期、类型、时长、距离、TSS、IF、平均心率）",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "查几天内的记录，默认 7，最多 60", "default": 7}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_activity",
            "description": "获取某次骑行的详细数据：功率、TSS、IF、NP、decoupling、效率因子、心率分区等",
            "parameters": {
                "type": "object",
                "properties": {
                    "activity_id": {"type": "string", "description": "活动 ID（从 list_activities 返回的 id 字段）"},
                    "date": {"type": "string", "description": "日期 YYYY-MM-DD，若没有 activity_id 则按日期查最近一次"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_sleep",
            "description": "获取指定日期的睡眠数据：时长、评分、深睡/REM分段、HRV、静息心率、Body Battery",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "日期 YYYY-MM-DD"},
                },
                "required": ["date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_wellness_trend",
            "description": "获取最近 N 天的健康趋势（CTL/ATL/TSB/Ramp Rate/HRV/静息心率/睡眠评分），用于分析训练状态变化",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "查几天，默认 14，最多 90", "default": 14}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_past_analysis",
            "description": "读取过往某天的 AI 分析报告原文（睡眠分析 / 骑行分析 / Garmin 日报）",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "日期 YYYY-MM-DD"},
                    "type": {
                        "type": "string",
                        "enum": ["sleep", "cycling", "daily"],
                        "description": "报告类型：sleep=ICU睡眠分析，cycling=ICU骑行分析，daily=Garmin日报",
                        "default": "daily",
                    },
                },
                "required": ["date"],
            },
        },
    },
]


# ── 工具实现 ──────────────────────────────────────────────────

def _icu_settings():
    from dotenv import load_dotenv
    load_dotenv(Path("/root/.env"))
    return {
        "athlete_id": (os.getenv("ICU_ATHLETE_ID") or "").strip(),
        "api_key": (os.getenv("ICU_API_KEY") or "").strip(),
    }


def _icu_get(path: str, params=None) -> dict | list | None:
    import requests
    s = _icu_settings()
    if not s["athlete_id"] or not s["api_key"]:
        return None
    url = f"https://intervals.icu/api/v1{path.format(athlete_id=s['athlete_id'])}"
    try:
        r = requests.get(url, params=params, auth=("API_KEY", s["api_key"]), timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def _safe_round(v, n=1):
    try:
        return round(float(v), n) if v is not None else None
    except Exception:
        return None


# ── tool: list_activities ─────────────────────────────────────

def list_activities(days: int = 7) -> list:
    days = min(int(days), 60)
    today = datetime.now(BJ_TZ).date()
    oldest = today - timedelta(days=days)
    acts = _icu_get("/athlete/{athlete_id}/activities",
                    params={"oldest": oldest.isoformat(), "newest": today.isoformat()})
    if not isinstance(acts, list):
        return acts or []
    result = []
    for a in acts:
        result.append({
            "id": a.get("id"),
            "date": (a.get("start_date_local") or "")[:10],
            "name": a.get("name"),
            "type": a.get("type"),
            "duration_min": _safe_round((a.get("moving_time") or 0) / 60),
            "distance_km": _safe_round((a.get("distance") or 0) / 1000),
            "tss": a.get("icu_training_load"),
            "intensity_factor": _safe_round((a.get("icu_intensity") or 0) / 100, 2) if a.get("icu_intensity") else None,
            "avg_hr": a.get("average_heartrate"),
            "avg_watts": a.get("average_watts"),
        })
    return result


# ── tool: get_activity ────────────────────────────────────────

def get_activity(activity_id: str = None, date: str = None) -> dict:
    s = _icu_settings()
    # 如果没有 ID，按日期找
    if not activity_id and date:
        acts = _icu_get("/athlete/{athlete_id}/activities",
                        params={"oldest": date, "newest": date})
        if isinstance(acts, list) and acts:
            activity_id = acts[0]["id"]
    if not activity_id:
        return {"error": "未找到活动，请提供 activity_id 或有效日期"}

    detail = _icu_get(f"/activity/{activity_id}")
    if not isinstance(detail, dict):
        return {"error": "活动详情获取失败"}

    act_date = (detail.get("start_date_local") or "")[:10]

    # 补充 Garmin 预生成点评
    garmin_notes = {}
    if GARMIN_ACTIVITY_DIR.exists() and act_date:
        for p in GARMIN_ACTIVITY_DIR.iterdir():
            try:
                gd = json.load(open(p))
                nd = gd.get("normalized_data", {})
                if nd.get("basic_activity", {}).get("date") == act_date:
                    lr = nd.get("load_recovery", {})
                    sp = nd.get("sport_specific", {})
                    for k in ["aerobic_te", "anaerobic_te", "training_effect_label", "estimated_recovery_time"]:
                        if lr.get(k) is not None:
                            garmin_notes[k] = lr[k]
                    for k in ["pacing_trend", "hr_power_relation_note", "power_variability_note",
                              "left_right_balance_note", "max_20min_power"]:
                        if sp.get(k):
                            garmin_notes[k] = sp[k]
                    break
            except Exception:
                continue

    result = {
        "id": detail.get("id"),
        "date": act_date,
        "name": detail.get("name"),
        "type": detail.get("type"),
        "duration_min": _safe_round((detail.get("moving_time") or 0) / 60),
        "distance_km": _safe_round((detail.get("distance") or 0) / 1000),
        "tss": detail.get("icu_training_load"),
        "intensity_factor": _safe_round((detail.get("icu_intensity") or 0) / 100, 2) if detail.get("icu_intensity") else None,
        "avg_watts": detail.get("average_watts"),
        "normalized_power": detail.get("weighted_average_watts"),
        "avg_hr": detail.get("average_heartrate"),
        "max_hr": detail.get("max_heartrate"),
        "ftp_at_time": detail.get("icu_ftp"),
        "decoupling_pct": _safe_round(detail.get("decoupling")),
        "efficiency_factor": _safe_round(detail.get("icu_efficiency_factor"), 3),
        "variability_index": _safe_round(detail.get("icu_variability_index"), 3),
        "calories": detail.get("calories"),
        "elevation_gain_m": detail.get("total_elevation_gain"),
    }
    if garmin_notes:
        result["garmin_supplement"] = garmin_notes
    return result


# ── tool: get_sleep ───────────────────────────────────────────

def get_sleep(date: str) -> dict:
    # 优先用本地已保存的双源融合文件
    saved = ICU_SLEEP_DIR / f"{date}.json"
    if saved.exists():
        try:
            d = json.loads(saved.read_text(encoding="utf-8"))
            payload = d.get("payload", {})
            gs = payload.get("garmin_supplement", {})
            stages = gs.get("sleep_stages", {})

            def secs_to_hm(s):
                if not s:
                    return None
                return f"{int(s//3600)}h{int((s%3600)//60)}m"

            return {
                "date": date,
                "sleep_score": payload.get("sleep", {}).get("sleepScore"),
                "total_sleep": secs_to_hm(payload.get("sleep", {}).get("sleepSecs")),
                "deep_sleep": secs_to_hm(stages.get("deep_secs")),
                "rem_sleep": secs_to_hm(stages.get("rem_secs")),
                "light_sleep": secs_to_hm(stages.get("light_secs")),
                "hrv_rmssd": payload.get("hrv", {}).get("rMSSD"),
                "hrv_5min_peak": gs.get("hrv_detail", {}).get("last_night_5min_high"),
                "hrv_baseline": f"{gs.get('hrv_detail',{}).get('baseline_balanced_low')}–{gs.get('hrv_detail',{}).get('baseline_balanced_upper')} ms",
                "resting_hr": payload.get("cardiovascular_recovery", {}).get("restingHR"),
                "body_battery_wake": gs.get("body_battery", {}).get("wake_up_level"),
                "body_battery_overnight_low": gs.get("body_battery", {}).get("overnight_low"),
                "ctl": payload.get("training_load_context", {}).get("ctl"),
                "atl": payload.get("training_load_context", {}).get("atl"),
                "tsb": payload.get("tsb"),
                "analysis_summary": (d.get("content") or "")[:300] or None,
            }
        except Exception:
            pass

    # fallback: 直接查 ICU wellness API
    entry = _icu_get(f"/athlete/{{athlete_id}}/wellness/{date}")
    if not isinstance(entry, dict) or "error" in entry:
        return {"error": f"{date} 暂无睡眠数据"}
    return {
        "date": date,
        "sleep_score": entry.get("sleepScore"),
        "sleep_secs": entry.get("sleepSecs"),
        "hrv_rmssd": entry.get("hrv"),
        "resting_hr": entry.get("restingHR"),
        "ctl": _safe_round(entry.get("ctl")),
        "atl": _safe_round(entry.get("atl")),
        "tsb": int(round((entry.get("ctl") or 0) - (entry.get("atl") or 0))),
    }


# ── tool: get_wellness_trend ──────────────────────────────────

def get_wellness_trend(days: int = 14) -> list:
    days = min(int(days), 90)
    today = datetime.now(BJ_TZ).date()
    oldest = today - timedelta(days=days)
    entries = _icu_get("/athlete/{athlete_id}/wellness",
                       params={"oldest": oldest.isoformat(), "newest": today.isoformat()})
    if not isinstance(entries, list):
        return []
    result = []
    for w in entries:
        if not w.get("sleepSecs") and not w.get("hrv") and not w.get("ctl"):
            continue
        result.append({
            "date": w.get("idate") or w.get("date") or w.get("id"),
            "ctl": _safe_round(w.get("ctl")),
            "atl": _safe_round(w.get("atl")),
            "tsb": int(round((w.get("ctl") or 0) - (w.get("atl") or 0))),
            "ramp_rate": _safe_round(w.get("rampRate")),
            "hrv_rmssd": w.get("hrv"),
            "resting_hr": w.get("restingHR"),
            "sleep_score": w.get("sleepScore"),
            "sleep_hours": _safe_round((w.get("sleepSecs") or 0) / 3600) if w.get("sleepSecs") else None,
        })
    return sorted(result, key=lambda x: x["date"])


# ── tool: get_past_analysis ───────────────────────────────────

def get_past_analysis(date: str, type: str = "daily") -> dict:
    if type == "sleep":
        p = ICU_SLEEP_DIR / f"{date}.json"
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            return {"date": date, "type": "sleep", "analysis": d.get("content") or "暂无内容"}
        return {"error": f"{date} 无睡眠分析记录"}

    if type == "cycling":
        for p in ICU_CYCLING_DIR.glob(f"{date}_*.json"):
            d = json.loads(p.read_text(encoding="utf-8"))
            return {"date": date, "type": "cycling",
                    "activity_id": d.get("activity_id"),
                    "title": d.get("title", ""),
                    "analysis": d.get("content") or "暂无内容"}
        return {"error": f"{date} 无骑行分析记录"}

    if type == "daily":
        p = REPORTS_DIR / f"{date}.json"
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            msgs = d.get("all_push_messages", [])
            mine = next((m for m in msgs if m.get("user") == "丛至"), None)
            return {"date": date, "type": "daily",
                    "analysis": mine.get("content", "暂无") if mine else "暂无"}
        return {"error": f"{date} 无日报记录"}

    return {"error": f"未知类型 {type}"}


# ── 工具分发器 ────────────────────────────────────────────────

TOOL_REGISTRY = {
    "list_activities": list_activities,
    "get_activity": get_activity,
    "get_sleep": get_sleep,
    "get_wellness_trend": get_wellness_trend,
    "get_past_analysis": get_past_analysis,
}


def execute_tool(name: str, args: dict) -> dict | list:
    fn = TOOL_REGISTRY.get(name)
    if not fn:
        return {"error": f"未知工具 {name}"}
    try:
        return fn(**args)
    except Exception as e:
        return {"error": str(e)}
