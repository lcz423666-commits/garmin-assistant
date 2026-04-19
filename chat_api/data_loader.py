"""从现有数据文件加载丛至的健康数据，供对话上下文使用。"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

DATA_DIR = Path("/root/garmin_assistant/data/congzhi")
REPORTS_DIR = Path("/root/garmin_assistant/reports/daily")
BASELINES_FILE = DATA_DIR / "baselines.json"
USER_ID = "congzhi"
DISPLAY_NAME = "丛至"


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_daily(target_date: date) -> dict | None:
    path = DATA_DIR / "daily" / f"{target_date}.json"
    raw = _read_json(path)
    if raw:
        return raw.get("data", raw)
    return None


def load_baselines() -> dict | None:
    return _read_json(BASELINES_FILE)


def load_recent_days(n: int = 7) -> list[dict]:
    today = date.today()
    result = []
    for i in range(n):
        d = today - timedelta(days=i)
        daily = load_daily(d)
        if daily:
            result.append({"date": str(d), "data": daily})
    return result


def _extract_key_metrics(daily: dict) -> dict:
    """从一天数据中提取关键指标，避免把整个 JSON 喂给 LLM。"""
    metrics = {}

    # HRV
    hrv = daily.get("hrv_data") or {}
    if isinstance(hrv, dict):
        summary = hrv.get("hrvSummary") or hrv.get("lastNight") or {}
        if summary:
            metrics["hrv_rmssd"] = summary.get("rmssd") or summary.get("lastNightAvg") or summary.get("lastNight5MinHigh")
            metrics["hrv_status"] = summary.get("status")

    # 睡眠
    sleep = daily.get("sleep") or {}
    if isinstance(sleep, dict):
        sd = sleep.get("dailySleepDTO") or sleep
        metrics["sleep_score"] = sd.get("sleepScores", {}).get("overall", {}).get("value") if isinstance(sd.get("sleepScores"), dict) else None
        metrics["sleep_seconds"] = sd.get("sleepTimeSeconds")
        metrics["deep_sleep_seconds"] = sd.get("deepSleepSeconds")
        metrics["rem_sleep_seconds"] = sd.get("remSleepSeconds")

    # 静息心率
    rhr = daily.get("rhr_day") or {}
    if isinstance(rhr, dict):
        metrics["resting_hr"] = rhr.get("restingHeartRate")

    # 身体电量
    bb = daily.get("body_battery") or {}
    if isinstance(bb, dict):
        metrics["body_battery_high"] = bb.get("charged")
        metrics["body_battery_low"] = bb.get("drained")

    # 训练状态
    ts = daily.get("training_status") or {}
    if isinstance(ts, list) and ts:
        ts = ts[0]
    if isinstance(ts, dict):
        metrics["training_status"] = ts.get("trainingStatus") or ts.get("trainingStatusType")
        metrics["training_load"] = ts.get("trainingLoadAcute") or ts.get("latestCyclingLoadAcute")

    # FTP / 骑行
    ftp = daily.get("cycling_ftp") or {}
    if isinstance(ftp, dict):
        metrics["cycling_ftp"] = ftp.get("functionalThresholdPower")

    # VO2Max / 耐力分
    endurance = daily.get("endurance_score") or {}
    if isinstance(endurance, dict):
        metrics["endurance_score"] = endurance.get("overallScore")

    # 乳酸阈值
    lt = daily.get("lactate_threshold") or {}
    if isinstance(lt, dict):
        metrics["lactate_threshold_hr"] = lt.get("heartRate") or lt.get("lactateThresholdHeartRateDTO", {}).get("heartRate") if isinstance(lt.get("lactateThresholdHeartRateDTO"), dict) else None

    # SPO2
    spo2 = daily.get("spo2_data") or {}
    if isinstance(spo2, dict):
        readings = spo2.get("spO2HourlyAverages") or []
        if readings:
            values = [r.get("value") or r.get("spo2Reading") for r in readings if isinstance(r, dict)]
            values = [v for v in values if v and v > 0]
            if values:
                metrics["spo2_min"] = min(values)
                metrics["spo2_avg"] = round(sum(values) / len(values), 1)

    return {k: v for k, v in metrics.items() if v is not None}


def build_context_for_chat() -> str:
    """构建喂给 LLM 的健康数据上下文字符串。"""
    today = date.today()
    today_data = load_daily(today)
    yesterday_data = load_daily(today - timedelta(days=1))
    baselines = load_baselines()

    lines = [f"# 用户健康数据上下文（{today}，北京时间）\n"]
    lines.append(f"用户：{DISPLAY_NAME}，运动爱好以骑行为主。\n")

    # 今日指标
    if today_data:
        m = _extract_key_metrics(today_data)
        lines.append("## 今日关键指标")
        if m.get("hrv_rmssd"):
            lines.append(f"- HRV(RMSSD): {m['hrv_rmssd']}  状态: {m.get('hrv_status', '未知')}")
        if m.get("sleep_seconds"):
            h = m["sleep_seconds"] // 3600
            mn = (m["sleep_seconds"] % 3600) // 60
            lines.append(f"- 睡眠时长: {h}小时{mn}分钟  深睡: {(m.get('deep_sleep_seconds') or 0)//60}分钟  REM: {(m.get('rem_sleep_seconds') or 0)//60}分钟")
        if m.get("sleep_score"):
            lines.append(f"- 睡眠评分: {m['sleep_score']}")
        if m.get("resting_hr"):
            lines.append(f"- 静息心率: {m['resting_hr']} bpm")
        if m.get("body_battery_high"):
            lines.append(f"- 身体电量: 最高 {m['body_battery_high']}% / 最低 {m['body_battery_low']}%")
        if m.get("training_status"):
            lines.append(f"- 训练状态: {m['training_status']}")
        if m.get("training_load"):
            lines.append(f"- 训练负荷: {m['training_load']}")
        if m.get("cycling_ftp"):
            lines.append(f"- 骑行 FTP: {m['cycling_ftp']} W")
        if m.get("spo2_min"):
            lines.append(f"- 夜间血氧: 最低 {m['spo2_min']}%  平均 {m.get('spo2_avg', '-')}%")
    else:
        lines.append("## 今日数据\n今日数据尚未采集。")

    # 昨日对比
    if yesterday_data:
        ym = _extract_key_metrics(yesterday_data)
        lines.append("\n## 昨日关键指标")
        if ym.get("hrv_rmssd"):
            lines.append(f"- HRV: {ym['hrv_rmssd']}  睡眠: {(ym.get('sleep_seconds') or 0)//3600}h{((ym.get('sleep_seconds') or 0)%3600)//60}m  静息心率: {ym.get('resting_hr', '-')} bpm")

    # 基线
    if baselines:
        metrics_bl = baselines.get("metrics") or {}
        lines.append("\n## 30天基线（用户正常水平）")
        for k, v in metrics_bl.items():
            if isinstance(v, dict) and "mean" in v:
                lines.append(f"- {k}: 均值 {v['mean']:.1f}  范围 [{v.get('low', '?')}, {v.get('high', '?')}]")

    return "\n".join(lines)


def _congzhi_first_line(raw: dict) -> str:
    """从报告中取丛至第一条消息的首句作摘要。"""
    msgs = raw.get("all_push_messages") or []
    mine = [m for m in msgs if m.get("user") == DISPLAY_NAME]
    if mine:
        content = mine[0].get("content", "")
        first = content.split("\n")[0][:60]
        return first
    return ""


def load_reports_list(days: int = 30) -> list[dict]:
    """返回最近 N 天的报告摘要列表（只含丛至的数据）。"""
    today = date.today()
    result = []
    for i in range(days):
        d = today - timedelta(days=i)
        json_path = REPORTS_DIR / f"{d}.json"
        md_path = REPORTS_DIR / f"{d}.md"
        raw = _read_json(json_path)
        if raw:
            result.append({
                "date": str(d),
                "conclusion": _congzhi_first_line(raw),
                "has_detail": True,
            })
        elif md_path.exists():
            result.append({
                "date": str(d),
                "conclusion": "",
                "has_detail": True,
            })
    return result


def load_report_detail(target_date: str) -> dict | None:
    """返回指定日期报告的详细内容（只含丛至的推送）。"""
    json_path = REPORTS_DIR / f"{target_date}.json"
    md_path = REPORTS_DIR / f"{target_date}.md"
    raw = _read_json(json_path)
    if raw:
        all_msgs = raw.get("all_push_messages") or []
        mine = [m for m in all_msgs if m.get("user") == DISPLAY_NAME]
        return {
            "date": target_date,
            "conclusion": _congzhi_first_line(raw),
            "push_messages": mine,
        }
    if md_path.exists():
        return {
            "date": target_date,
            "conclusion": "",
            "push_messages": [{"content": md_path.read_text(encoding="utf-8")}],
        }
    return None
