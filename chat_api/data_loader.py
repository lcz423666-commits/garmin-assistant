"""从 ICU（Intervals.icu）和 Garmin 数据文件加载丛至的健康数据，供对话上下文使用。
优先级：ICU 睡眠/恢复数据 > Garmin daily 数据（作为补充）
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

DATA_DIR = Path("/root/garmin_assistant/data/congzhi")
REPORTS_DIR = Path("/root/garmin_assistant/reports/daily")
BASELINES_FILE = DATA_DIR / "baselines.json"
ICU_SLEEP_DIR = DATA_DIR / "icu_sleep"
ICU_CYCLING_DIR = DATA_DIR / "icu_cycling"
USER_ID = "congzhi"
DISPLAY_NAME = "丛至"


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


# ── Garmin daily 数据 ────────────────────────────────────────

def load_daily(target_date: date) -> dict | None:
    path = DATA_DIR / "daily" / f"{target_date}.json"
    raw = _read_json(path)
    if raw:
        return raw.get("data", raw)
    return None


def load_baselines() -> dict | None:
    return _read_json(BASELINES_FILE)


# ── ICU 睡眠数据 ─────────────────────────────────────────────

def load_icu_sleep_latest(days: int = 3) -> dict | None:
    """读取最近 N 天内最新的 ICU 睡眠文件，返回 {date, payload, content}。"""
    if not ICU_SLEEP_DIR.exists():
        return None
    today = date.today()
    for i in range(days):
        d = today - timedelta(days=i)
        path = ICU_SLEEP_DIR / f"{d}.json"
        raw = _read_json(path)
        if raw and raw.get("payload"):
            return raw
    return None


def load_icu_sleep_by_date(target_date: str) -> dict | None:
    path = ICU_SLEEP_DIR / f"{target_date}.json"
    return _read_json(path)


def load_icu_sleep_reports(days: int = 30) -> list[dict]:
    """返回最近 N 天的 ICU 睡眠分析列表。"""
    if not ICU_SLEEP_DIR.exists():
        return []
    today = date.today()
    result = []
    for i in range(days):
        d = today - timedelta(days=i)
        raw = _read_json(ICU_SLEEP_DIR / f"{d}.json")
        if raw and raw.get("content"):
            content = raw["content"]
            result.append({
                "date": str(d),
                "title": "ICU 睡眠分析",
                "conclusion": content[:60] + "..." if len(content) > 60 else content,
                "type": "icu_sleep",
            })
    return result


# ── ICU 骑行数据 ─────────────────────────────────────────────

def load_icu_cycling_reports(days: int = 30) -> list[dict]:
    """返回最近 N 天保存的 ICU 骑行分析列表。"""
    if not ICU_CYCLING_DIR.exists():
        return []
    today = date.today()
    cutoff = today - timedelta(days=days)
    result = []
    for f in sorted(ICU_CYCLING_DIR.glob("*.json"), reverse=True):
        raw = _read_json(f)
        if not raw:
            continue
        try:
            ride_date = date.fromisoformat(raw["date"])
        except Exception:
            continue
        if ride_date < cutoff:
            break
        content = raw.get("content", "")
        result.append({
            "date": raw["date"],
            "activity_id": raw.get("activity_id", ""),
            "title": raw.get("title") or "ICU 骑行分析",
            "conclusion": content[:60] + "..." if content else "",
            "type": "icu_cycling",
        })
    return result


def load_icu_cycling_detail(activity_id: str) -> dict | None:
    if not ICU_CYCLING_DIR.exists():
        return None
    for f in ICU_CYCLING_DIR.glob("*.json"):
        if activity_id in f.name:
            return _read_json(f)
    return None


# ── 指标提取 ─────────────────────────────────────────────────

def _extract_icu_sleep_metrics(icu: dict) -> dict:
    """从 ICU 睡眠 payload 提取关键指标。"""
    p = icu.get("payload") or icu
    metrics = {}

    sleep = p.get("sleep") or {}
    secs = sleep.get("sleepSecs")
    if secs:
        metrics["sleep_seconds"] = int(secs)
    score = sleep.get("sleepScore")
    if score:
        metrics["sleep_score"] = int(score)
    quality = sleep.get("sleepQuality")
    if quality is not None:
        metrics["sleep_quality"] = quality
    avg_hr = sleep.get("avgSleepingHR")
    if avg_hr:
        metrics["sleep_avg_hr"] = round(float(avg_hr), 1)

    hrv = p.get("hrv") or {}
    rmssd = hrv.get("rMSSD")
    if rmssd:
        metrics["hrv_rmssd"] = round(float(rmssd), 1)

    cv = p.get("cardiovascular_recovery") or {}
    rhr = cv.get("restingHR")
    if rhr:
        metrics["resting_hr"] = round(float(rhr), 1)
    spo2 = cv.get("spO2")
    if spo2:
        metrics["spo2_avg"] = round(float(spo2), 1)
    readiness = cv.get("readiness")
    if readiness is not None:
        metrics["readiness"] = readiness

    tl = p.get("training_load_context") or {}
    ctl = tl.get("ctl")
    atl = tl.get("atl")
    if ctl is not None:
        metrics["ctl"] = round(float(ctl), 1)
    if atl is not None:
        metrics["atl"] = round(float(atl), 1)
    tsb = p.get("tsb")
    if tsb is not None:
        metrics["tsb"] = int(tsb)
    ramp = tl.get("rampRate")
    if ramp is not None:
        metrics["ramp_rate"] = round(float(ramp), 1)

    subj = p.get("subjective_feedback") or {}
    for key in ("fatigue", "soreness", "stress", "mood", "motivation"):
        val = subj.get(key)
        if val is not None:
            metrics[key] = val

    act = p.get("daily_activity") or {}
    vo2 = act.get("vo2max")
    if vo2:
        metrics["vo2max"] = round(float(vo2), 1)

    return {k: v for k, v in metrics.items() if v is not None}


def _extract_garmin_metrics(daily: dict) -> dict:
    """从 Garmin daily 数据提取指标（作为 ICU 的补充）。"""
    metrics = {}

    # HRV（ICU 没有时用）
    hrv = daily.get("hrv_data") or {}
    if isinstance(hrv, dict):
        summary = hrv.get("hrvSummary") or {}
        metrics["hrv_rmssd"] = summary.get("rmssd") or summary.get("lastNightAvg") or summary.get("lastNight5MinHigh")
        metrics["hrv_status"] = summary.get("status")
        metrics["hrv_weekly_avg"] = summary.get("weeklyAvg")

    # 睡眠（ICU 没有时用）
    sleep = daily.get("sleep") or {}
    if isinstance(sleep, dict):
        sd = sleep.get("dailySleepDTO") or sleep
        scores = sd.get("sleepScores") or {}
        metrics["sleep_score"] = scores.get("overall", {}).get("value") if isinstance(scores, dict) else None
        metrics["sleep_seconds"] = sd.get("sleepTimeSeconds")
        metrics["deep_sleep_seconds"] = sd.get("deepSleepSeconds")
        metrics["rem_sleep_seconds"] = sd.get("remSleepSeconds")

    # 静息心率
    rhr = daily.get("rhr_day") or {}
    if isinstance(rhr, dict):
        try:
            rhr_list = rhr["allMetrics"]["metricsMap"]["WELLNESS_RESTING_HEART_RATE"]
            metrics["resting_hr"] = int(rhr_list[0]["value"]) if rhr_list else None
        except (KeyError, IndexError, TypeError):
            metrics["resting_hr"] = rhr.get("restingHeartRate")

    # 身体电量
    bb = daily.get("body_battery")
    if isinstance(bb, list) and bb:
        bb = bb[0]
    if isinstance(bb, dict):
        metrics["body_battery_high"] = bb.get("charged")
        metrics["body_battery_low"] = bb.get("drained")

    # 训练状态
    ts = daily.get("training_status") or {}
    if isinstance(ts, list) and ts:
        ts = ts[0]
    if isinstance(ts, dict):
        most_recent = ts.get("mostRecentTrainingStatus") or {}
        latest_map = most_recent.get("latestTrainingStatusData") or {}
        if latest_map:
            device_data = next(iter(latest_map.values()), {})
            metrics["training_status"] = device_data.get("trainingStatusFeedbackPhrase", "")
            acute = device_data.get("acuteTrainingLoadDTO") or {}
            metrics["training_load"] = acute.get("dailyTrainingLoadAcute")
        else:
            metrics["training_status"] = ts.get("trainingStatus") or ts.get("trainingStatusType")
            metrics["training_load"] = ts.get("trainingLoadAcute")

    # FTP
    ftp = daily.get("cycling_ftp") or {}
    if isinstance(ftp, dict):
        metrics["cycling_ftp"] = ftp.get("functionalThresholdPower")

    # 耐力分
    endurance = daily.get("endurance_score") or {}
    if isinstance(endurance, dict):
        gm = endurance.get("groupMap") or {}
        for dk in sorted(gm.keys(), reverse=True):
            val = gm[dk]
            avg = val.get("groupAverage") if isinstance(val, dict) else None
            if avg is not None:
                metrics["endurance_score"] = round(avg)
                break

    # SPO2
    spo2 = daily.get("spo2_data") or {}
    if isinstance(spo2, dict):
        lowest = spo2.get("lowestSpO2")
        avg_sleep = spo2.get("avgSleepSpO2")
        if lowest:
            metrics["spo2_min"] = lowest
            metrics["spo2_avg"] = round(avg_sleep, 1) if avg_sleep else None

    return {k: v for k, v in metrics.items() if v is not None}


def _extract_key_metrics(daily: dict) -> dict:
    """合并 ICU + Garmin 指标，ICU 数据优先。"""
    garmin = _extract_garmin_metrics(daily) if daily else {}
    icu_raw = load_icu_sleep_latest(days=2)
    icu = _extract_icu_sleep_metrics(icu_raw) if icu_raw else {}
    # ICU 覆盖 Garmin，Garmin 补充 ICU 没有的字段
    merged = {**garmin, **icu}
    return merged


# ── 对话上下文构建 ───────────────────────────────────────────

def build_context_for_chat() -> str:
    """构建喂给 LLM 的健康数据上下文字符串。"""
    today = date.today()
    garmin_today = load_daily(today) or load_daily(today - timedelta(days=1))
    garmin_metrics = _extract_garmin_metrics(garmin_today) if garmin_today else {}

    icu_raw = load_icu_sleep_latest(days=2)
    icu_metrics = _extract_icu_sleep_metrics(icu_raw) if icu_raw else {}
    icu_date = icu_raw.get("date", "") if icu_raw else ""
    icu_analysis = icu_raw.get("content", "") if icu_raw else ""

    m = {**garmin_metrics, **icu_metrics}

    lines = [f"# 用户健康数据上下文（{today}，北京时间）\n"]
    lines.append(f"用户：{DISPLAY_NAME}，运动爱好以骑行为主。\n")

    lines.append("## 今日关键指标")
    if m.get("hrv_rmssd"):
        lines.append(f"- HRV(RMSSD): {m['hrv_rmssd']} ms  "
                     f"状态: {m.get('hrv_status', '来自ICU')}")
    if m.get("sleep_seconds"):
        h, mn = m["sleep_seconds"] // 3600, (m["sleep_seconds"] % 3600) // 60
        lines.append(f"- 睡眠时长: {h}小时{mn}分钟")
    if m.get("sleep_score"):
        lines.append(f"- 睡眠评分: {m['sleep_score']}  质量: {m.get('sleep_quality', '-')}")
    if m.get("resting_hr"):
        lines.append(f"- 静息心率: {m['resting_hr']} bpm")
    if m.get("spo2_avg"):
        lines.append(f"- 夜间血氧: {m.get('spo2_min', '-')}%  均值 {m['spo2_avg']}%")
    if m.get("readiness") is not None:
        lines.append(f"- 恢复就绪度: {m['readiness']}")
    if m.get("ctl") is not None:
        lines.append(f"- CTL(慢性训练负荷): {m['ctl']}  ATL: {m.get('atl', '-')}  TSB: {m.get('tsb', '-')}")
    if m.get("ramp_rate") is not None:
        lines.append(f"- 负荷增速(Ramp Rate): {m['ramp_rate']}")
    if m.get("body_battery_high"):
        lines.append(f"- 身体电量: 最高 {m['body_battery_high']}% / 最低 {m.get('body_battery_low', '-')}%")
    if m.get("training_status"):
        lines.append(f"- 训练状态: {m['training_status']}")
    if m.get("cycling_ftp"):
        lines.append(f"- 骑行 FTP: {m['cycling_ftp']} W")
    if m.get("vo2max"):
        lines.append(f"- VO2Max: {m['vo2max']}")

    subj_fields = [("fatigue","疲劳"), ("soreness","酸痛"), ("stress","压力"), ("mood","情绪"), ("motivation","动力")]
    subj_parts = [f"{cn}={m[en]}" for en, cn in subj_fields if m.get(en) is not None]
    if subj_parts:
        lines.append(f"- 主观感受: {', '.join(subj_parts)}")

    if icu_analysis:
        lines.append(f"\n## 最近 ICU 睡眠分析（{icu_date}）")
        lines.append(icu_analysis[:600] + ("..." if len(icu_analysis) > 600 else ""))

    baselines = load_baselines()
    if baselines:
        metrics_bl = baselines.get("metrics") or {}
        bl_lines = []
        for k, v in metrics_bl.items():
            if isinstance(v, dict) and v.get("mean") is not None:
                bl_lines.append(f"- {k}: 均值 {v['mean']:.1f}")
        if bl_lines:
            lines.append("\n## 30天 Garmin 基线（正常水平参考）")
            lines.extend(bl_lines[:8])

    return "\n".join(lines)


# ── 报告列表与详情 ────────────────────────────────────────────

def _congzhi_first_line(raw: dict) -> str:
    msgs = raw.get("all_push_messages") or []
    mine = [m for m in msgs if m.get("user") == DISPLAY_NAME]
    if mine:
        content = mine[0].get("content", "")
        return content.split("\n")[0][:60]
    return ""


def load_reports_list(days: int = 30) -> list[dict]:
    """合并 ICU 睡眠报告 + ICU 骑行报告 + Garmin 日报，按日期倒序。"""
    result_map: dict[str, dict] = {}

    # ICU 睡眠
    for r in load_icu_sleep_reports(days):
        result_map[r["date"] + "_sleep"] = r

    # ICU 骑行
    for r in load_icu_cycling_reports(days):
        key = r["date"] + "_" + r.get("activity_id", "cycling")
        result_map[key] = r

    # Garmin 日报（补充没有 ICU 数据的日期）
    today = date.today()
    for i in range(days):
        d = today - timedelta(days=i)
        ds = str(d)
        # 如果这天已经有 ICU 睡眠报告，跳过
        if ds + "_sleep" in result_map:
            continue
        json_path = REPORTS_DIR / f"{ds}.json"
        md_path = REPORTS_DIR / f"{ds}.md"
        raw = _read_json(json_path)
        if raw:
            msgs = raw.get("all_push_messages") or []
            mine = [m for m in msgs if m.get("user") == DISPLAY_NAME]
            if mine:
                result_map[ds + "_garmin"] = {
                    "date": ds,
                    "title": "每日报告",
                    "conclusion": _congzhi_first_line(raw),
                    "type": "garmin",
                }
        elif md_path.exists():
            result_map[ds + "_garmin"] = {
                "date": ds,
                "title": "每日报告",
                "conclusion": "",
                "type": "garmin",
            }

    # 按日期倒序
    items = sorted(result_map.values(), key=lambda x: x["date"], reverse=True)
    return items


def load_report_detail(target_date: str, report_type: str = "") -> dict | None:
    """返回指定日期报告的详细内容。"""
    # ICU 睡眠
    if report_type == "icu_sleep" or not report_type:
        raw = load_icu_sleep_by_date(target_date)
        if raw and raw.get("content"):
            return {
                "date": target_date,
                "type": "icu_sleep",
                "conclusion": "",
                "push_messages": [{"content": raw["content"], "message_type": "ICU 睡眠分析"}],
            }

    # ICU 骑行
    if report_type == "icu_cycling":
        for f in ICU_CYCLING_DIR.glob(f"{target_date}_*.json") if ICU_CYCLING_DIR.exists() else []:
            raw = _read_json(f)
            if raw:
                return {
                    "date": target_date,
                    "type": "icu_cycling",
                    "conclusion": raw.get("title", ""),
                    "push_messages": [{"content": raw.get("content", ""), "message_type": "ICU 骑行分析"}],
                }

    # Garmin 日报
    json_path = REPORTS_DIR / f"{target_date}.json"
    md_path = REPORTS_DIR / f"{target_date}.md"
    raw = _read_json(json_path)
    if raw:
        all_msgs = raw.get("all_push_messages") or []
        mine = [m for m in all_msgs if m.get("user") == DISPLAY_NAME]
        return {
            "date": target_date,
            "type": "garmin",
            "conclusion": _congzhi_first_line(raw),
            "push_messages": mine,
        }
    if md_path.exists():
        return {
            "date": target_date,
            "type": "garmin",
            "conclusion": "",
            "push_messages": [{"content": md_path.read_text(encoding="utf-8")}],
        }
    return None
