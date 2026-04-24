from __future__ import annotations

import argparse
import html
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests


BJ_TZ = timezone(timedelta(hours=8))
ENV_PATH = Path("/root/.env")
STATE_PATH = Path("/root/.icu_sleep_state.json")
TARGET_USER_NAME = "丛至"
WELLNESS_LOOKBACK_DAYS = 3
PUSH_TITLE = "😴 睡眠深度分析"


def _log(message: str, log_func=None):
    if log_func:
        log_func(message)
        return
    timestamp = datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [ICU睡眠] {message}", flush=True)


def _load_dotenv_file():
    from dotenv import load_dotenv

    load_dotenv(ENV_PATH)


def _load_runtime_settings(log_func=None):
    try:
        _load_dotenv_file()
    except Exception as exc:
        _log(f"加载 /root/.env 失败: {exc}", log_func)
        return None
    athlete_id = (os.getenv("ICU_ATHLETE_ID") or "").strip()
    api_key = (os.getenv("ICU_API_KEY") or "").strip()
    if not athlete_id:
        _log("ICU_ATHLETE_ID 未配置，跳过睡眠分析", log_func)
        return None
    if not api_key:
        _log("ICU_API_KEY 未配置，跳过睡眠分析", log_func)
        return None
    return {
        "athlete_id": athlete_id,
        "api_key": api_key,
    }


def _parse_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=BJ_TZ)
    normalized = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        if len(normalized) == 10:
            try:
                dt = datetime.strptime(normalized, "%Y-%m-%d")
            except ValueError:
                return datetime.min.replace(tzinfo=BJ_TZ)
        else:
            normalized = normalized.replace("T", " ")
            if len(normalized) >= 19:
                normalized = normalized[:19]
            try:
                dt = datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return datetime.min.replace(tzinfo=BJ_TZ)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=BJ_TZ)
    return dt.astimezone(BJ_TZ)


def _safe_float(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_tsb(ctl_value, atl_value):
    ctl = _safe_float(ctl_value)
    atl = _safe_float(atl_value)
    if ctl is None or atl is None:
        return None
    return int(round(ctl - atl))


def select_latest_wellness(entries: list[dict]) -> dict | None:
    valid_entries = []
    for entry in entries or []:
        if (_safe_float(entry.get("sleepSecs")) or 0) > 0:
            valid_entries.append(entry)
    if not valid_entries:
        return None
    return max(
        valid_entries,
        key=lambda item: _parse_datetime(
            item.get("updated") or item.get("idate") or item.get("date") or item.get("created")
        ),
    )


GARMIN_DAILY_DIR = Path("/root/garmin_assistant/data/congzhi/daily")


def _enrich_with_garmin(payload: dict) -> dict:
    """补充 Garmin daily JSON 中 ICU 没有的字段：睡眠分段、HRV基线、Body Battery、训练状态、VO2Max。"""
    sleep_date = payload.get("date")
    if not sleep_date:
        return payload

    garmin_path = GARMIN_DAILY_DIR / f"{sleep_date}.json"
    if not garmin_path.exists():
        return payload

    try:
        garmin_data = json.loads(garmin_path.read_text(encoding="utf-8")).get("data", {})
    except Exception:
        return payload

    supplement: dict = {}

    # 睡眠分段（深睡 / REM / 浅睡 / 清醒）
    sleep_dto = garmin_data.get("sleep", {}).get("dailySleepDTO", {})
    supplement["sleep_stages"] = {
        "deep_secs": sleep_dto.get("deepSleepSeconds"),
        "rem_secs": sleep_dto.get("remSleepSeconds"),
        "light_secs": sleep_dto.get("lightSleepSeconds"),
        "awake_secs": sleep_dto.get("awakeSleepSeconds"),
    }

    # HRV 基线与夜间峰值（Garmin 比 ICU 更细）
    hrv_sum = garmin_data.get("hrv_data", {}).get("hrvSummary", {})
    if hrv_sum:
        supplement["hrv_detail"] = {
            "last_night_5min_high": hrv_sum.get("lastNight5MinHigh"),
            "last_night_avg": hrv_sum.get("lastNightAvg"),
            "baseline_low_upper": hrv_sum.get("baseline", {}).get("lowUpper"),
            "baseline_balanced_low": hrv_sum.get("baseline", {}).get("balancedLow"),
            "baseline_balanced_upper": hrv_sum.get("baseline", {}).get("balancedUpper"),
            "status": hrv_sum.get("status"),
            "feedback_phrase": hrv_sum.get("feedbackPhrase"),
        }

    # Body Battery：起床电量、夜间谷底、睡眠充入量
    bb_list = garmin_data.get("body_battery", [])
    if bb_list:
        bb = bb_list[0]
        vals = [v[1] for v in bb.get("bodyBatteryValuesArray", [])
                if isinstance(v, list) and len(v) > 1 and v[1] is not None]
        sleep_events = bb.get("bodyBatteryActivityEvent", [])
        sleep_impact = next(
            (e.get("bodyBatteryImpact") for e in sleep_events if e.get("eventType") == "SLEEP"),
            None,
        )
        supplement["body_battery"] = {
            "sleep_charged": bb.get("charged"),
            "wake_up_level": vals[-1] if vals else None,
            "overnight_low": min(vals) if vals else None,
            "sleep_impact": sleep_impact,
        }

    # Garmin 训练状态（PRODUCTIVE_3 等）+ ACWR
    ts_root = garmin_data.get("training_status", {})
    latest_ts_map = ts_root.get("mostRecentTrainingStatus", {}).get("latestTrainingStatusData", {})
    if latest_ts_map:
        dev = next(iter(latest_ts_map.values()), {})
        acwr = dev.get("acuteTrainingLoadDTO", {})
        supplement["training_status"] = {
            "phrase": dev.get("trainingStatusFeedbackPhrase"),
            "acwr_pct": acwr.get("acwrPercent"),
            "acwr_status": acwr.get("acwrStatus"),
        }

    # VO2Max（骑行）
    vo2_cycling = ts_root.get("mostRecentVO2Max", {}).get("cycling", {})
    if vo2_cycling.get("vo2MaxPreciseValue"):
        supplement["vo2max_cycling"] = vo2_cycling["vo2MaxPreciseValue"]

    # 月度负荷结构（有氧低强度/高强度/无氧比例）
    lb_map = ts_root.get("mostRecentTrainingLoadBalance", {}).get("metricsTrainingLoadBalanceDTOMap", {})
    if lb_map:
        lb = next(iter(lb_map.values()), {})
        supplement["load_balance"] = {
            "aerobic_low": _safe_float(lb.get("monthlyLoadAerobicLow")),
            "aerobic_high": _safe_float(lb.get("monthlyLoadAerobicHigh")),
            "anaerobic": _safe_float(lb.get("monthlyLoadAnaerobic")),
            "feedback": lb.get("trainingBalanceFeedbackPhrase"),
        }

    payload["garmin_supplement"] = supplement
    return payload


def extract_sleep_analysis_data(entry: dict) -> dict:
    date_value = entry.get("idate") or entry.get("date") or entry.get("id")
    return {
        "date": date_value,
        "sleep": {
            "sleepSecs": entry.get("sleepSecs"),
            "sleepScore": entry.get("sleepScore"),
            "sleepQuality": entry.get("sleepQuality"),
            "avgSleepingHR": entry.get("avgSleepingHR"),
        },
        "hrv": {
            "rMSSD": entry.get("hrv"),
            "SDNN": entry.get("hrvSDNN"),
            "baevskySI": entry.get("baevskySI"),
        },
        "cardiovascular_recovery": {
            "restingHR": entry.get("restingHR"),
            "spO2": entry.get("spO2"),
            "readiness": entry.get("readiness"),
        },
        "training_load_context": {
            "ctl": entry.get("ctl"),
            "atl": entry.get("atl"),
            "rampRate": entry.get("rampRate"),
        },
        "body_metrics": {
            "weight": entry.get("weight"),
            "bodyFat": entry.get("bodyFat"),
        },
        "subjective_feedback": {
            "soreness": entry.get("soreness"),
            "fatigue": entry.get("fatigue"),
            "stress": entry.get("stress"),
            "mood": entry.get("mood"),
            "motivation": entry.get("motivation"),
        },
        "daily_activity": {
            "steps": entry.get("steps"),
            "vo2max": entry.get("vo2max"),
            "respiration": entry.get("respiration"),
        },
        "tsb": _safe_tsb(entry.get("ctl"), entry.get("atl")),
    }


def _load_state():
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict):
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _request_json(url: str, *, api_key: str, params=None, log_func=None):
    auth = ("API_KEY", api_key)
    last_error = None
    for attempt in range(2):
        try:
            response = requests.get(url, params=params, auth=auth, timeout=20)
            if response.status_code == 429 and attempt == 0:
                _log("ICU API 触发 429，2 秒后重试一次", log_func)
                time.sleep(2)
                continue
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last_error = exc
            if getattr(getattr(exc, "response", None), "status_code", None) == 429 and attempt == 0:
                _log("ICU API 触发 429，2 秒后重试一次", log_func)
                time.sleep(2)
                continue
            break
    raise last_error


def _format_sleep_observation(payload: dict) -> str:
    sleep = payload.get("sleep", {})
    hrv = payload.get("hrv", {})
    cardio = payload.get("cardiovascular_recovery", {})
    training = payload.get("training_load_context", {})
    tsb = payload.get("tsb")
    parts = []
    sleep_secs = sleep.get("sleepSecs")
    if sleep_secs:
        h = int(sleep_secs // 3600)
        m = int((sleep_secs % 3600) // 60)
        parts.append(f"睡眠{h}h{m}m")
    if sleep.get("sleepScore"):
        parts.append(f"评分{sleep['sleepScore']}")
    if hrv.get("rMSSD"):
        parts.append(f"rMSSD={hrv['rMSSD']}")
    if cardio.get("restingHR"):
        parts.append(f"静息HR={cardio['restingHR']}")
    if tsb is not None:
        parts.append(f"TSB={tsb}")
    if training.get("rampRate"):
        parts.append(f"rampRate={training['rampRate']}")
    return "，".join(parts)


def _build_analysis_prompt(
    payload: dict,
    pending_goals: list | None = None,
    profile_summary: str = "",
) -> str:
    base = (
        "你是一位运动恢复专家，根据以下 Intervals.icu 健康数据分析用户的睡眠和恢复状况。\n\n"
        f"数据：\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
    )
    if profile_summary:
        base += f"用户长期画像（供参考，不必逐条提及）：\n{profile_summary}\n\n"
    if pending_goals:
        goals_text = "\n".join(
            f"- [{g.get('source', '')}] {g.get('goal', '')}" for g in pending_goals
        )
        base += (
            f"AI 昨日给用户的建议（注意：这是 AI 建议，不是用户主动制定的计划。请在今日建议部分，"
            f"结合今天的数据客观评估用户是否执行、执行效果如何）：\n{goals_text}\n\n"
        )
    hrv = payload.get("hrv", {})
    gs = payload.get("garmin_supplement", {})
    stages = gs.get("sleep_stages", {})
    hrv_detail = gs.get("hrv_detail", {})
    bb = gs.get("body_battery", {})

    # 睡眠分段描述（有数据才纳入）
    stage_parts = []
    for label, key in [("深睡", "deep_secs"), ("REM", "rem_secs"), ("浅睡", "light_secs")]:
        v = stages.get(key)
        if v:
            h, m = int(v // 3600), int((v % 3600) // 60)
            stage_parts.append(f"{label} {h}h{m}m")
    stage_str = "、".join(stage_parts) if stage_parts else ""

    # HRV 基线区间描述
    hrv_baseline_str = ""
    if hrv_detail.get("baseline_balanced_low") and hrv_detail.get("baseline_balanced_upper"):
        hrv_baseline_str = (
            f"（个人基线平衡区间 {hrv_detail['baseline_balanced_low']}–{hrv_detail['baseline_balanced_upper']} ms，"
            f"夜间5分钟峰值 {hrv_detail.get('last_night_5min_high', '—')}）"
        )

    base += (
        "要求：\n"
        "1. 用北京时间问候，语气像朋友，自然口语化\n"
        "2. 内容分四部分但不要加标题，自然过渡：\n"
        f"   - 睡眠概况：时长是否达标（7-9h）、评分解读、睡眠心率与静息心率关系"
        + (f"；必须提及睡眠分段：{stage_str}" if stage_str else "") + "\n"
        f"   - HRV 分析：rMSSD 解读{hrv_baseline_str}、与基线对比趋势\n"
        "   - 训练-恢复平衡：CTL/ATL/TSB/rampRate 负荷增速、疲劳积累、是否适合训练"
        + (f"；结合 Body Battery（起床 {bb.get('wake_up_level','—')}%，夜间最低 {bb.get('overnight_low','—')}%）分析恢复质量" if bb.get("wake_up_level") is not None else "") + "\n"
        "   - 今日建议：根据 TSB 和 HRV 给出具体建议（休息/轻松骑/可上强度），"
        "如有 garmin_supplement.training_status，提及训练状态标签（翻译成中文）\n"
        "3. 如果上方有「AI 昨日给用户的建议」，在建议部分自然融入「AI 昨天建议你X，从今天的数据看……」，"
        "禁止说成「你昨天计划了」——这些建议来自 AI 不是用户\n"
        "4. 如果有主观评估数据（soreness/fatigue/mood），结合分析\n"
        "5. 禁止使用 markdown 加粗语法\n"
        "6. 控制在 400 字以内（融合数据增多，字数上限适当放宽）\n"
        "7. 这是独立于佳明睡眠推送的补充分析，侧重 HRV 和训练负荷关联\n"
    )
    return base


def _generate_analysis(
    payload: dict,
    pending_goals: list | None = None,
    profile_summary: str = "",
):
    from llm_helper import LLM_MODEL, client

    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": "你是一位运动恢复专家。"},
            {"role": "user", "content": _build_analysis_prompt(payload, pending_goals, profile_summary)},
        ],
    )
    return (response.choices[0].message.content or "").strip()


def _render_push_content(content: str) -> str:
    return html.escape(content or "").replace("\n", "<br/>")


def _push_message(user: dict, title: str, content: str, log_func=None) -> bool:
    token = (user.get("pushplus_token") or "").strip()
    if not token:
        _log("丛至的 PushPlus Token 缺失，跳过 ICU 睡眠推送", log_func)
        return False
    response = requests.post(
        "https://www.pushplus.plus/send",
        json={
            "token": token,
            "title": title,
            "content": _render_push_content(content),
            "template": "html",
        },
        timeout=15,
    )
    result = response.json()
    if result.get("code") == 200:
        _log(f"ICU 睡眠推送成功: {title}", log_func)
        return True
    _log(f"ICU 睡眠推送失败: {result.get('msg', '未知错误')}", log_func)
    return False


def _load_target_user():
    from app_config import load_users

    for user in load_users():
        if user.get("name") == TARGET_USER_NAME:
            return user
    return None


def check_and_push_sleep(*, user=None, test: bool = False, log_func=None) -> bool:
    target_user = user or _load_target_user()
    if not target_user or target_user.get("name") != TARGET_USER_NAME:
        return False

    settings = _load_runtime_settings(log_func)
    if not settings:
        return False

    today = datetime.now(BJ_TZ).date()
    oldest = today - timedelta(days=WELLNESS_LOOKBACK_DAYS)
    wellness = _request_json(
        f"https://intervals.icu/api/v1/athlete/{settings['athlete_id']}/wellness",
        api_key=settings["api_key"],
        params={"oldest": oldest.isoformat(), "newest": today.isoformat()},
        log_func=log_func,
    )
    latest_entry = select_latest_wellness(wellness if isinstance(wellness, list) else [])
    if not latest_entry:
        _log("最近 3 天没有可用 ICU 睡眠数据，跳过", log_func)
        return False

    payload = extract_sleep_analysis_data(latest_entry)
    payload = _enrich_with_garmin(payload)
    state = _load_state()
    payload_date = payload.get("date")
    if not test and payload_date and state.get("last_date") == payload_date:
        _log(f"ICU 睡眠日期 {payload.get('date')} 已推送过，跳过", log_func)
        return False

    pending_goals: list = []
    profile_summary: str = ""
    try:
        import sys
        import os
        sys.path.insert(0, os.path.dirname(__file__))
        from goal_tracker import get_pending_goals
        from user_profile import profile_summary_for_prompt
        pending_goals = get_pending_goals()
        profile_summary = profile_summary_for_prompt()
    except Exception as exc:
        _log(f"加载目标/画像失败（不影响推送）: {exc}", log_func)

    analysis = _generate_analysis(payload, pending_goals, profile_summary)

    if test:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        print("\n=== Pending Goals ===\n")
        import json as _json
        print(_json.dumps(pending_goals, ensure_ascii=False, indent=2))
        print("\n=== ICU Sleep Message ===\n")
        print(analysis)
        return False

    pushed = _push_message(target_user, PUSH_TITLE, analysis, log_func)
    if pushed:
        _save_state(
            {
                "last_date": payload.get("date"),
                "updated_at": datetime.now(BJ_TZ).isoformat(),
            }
        )
        # 保存 ICU 睡眠数据和分析内容，供网页报告页读取
        try:
            save_dir = Path("/root/garmin_assistant/data/congzhi/icu_sleep")
            save_dir.mkdir(parents=True, exist_ok=True)
            sleep_date = payload.get("date", datetime.now(BJ_TZ).strftime("%Y-%m-%d"))
            save_path = save_dir / f"{sleep_date}.json"
            save_path.write_text(
                json.dumps({
                    "date": sleep_date,
                    "saved_at": datetime.now(BJ_TZ).isoformat(),
                    "payload": payload,
                    "content": analysis,
                }, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            _log(f"ICU 睡眠数据已保存: {save_path}", log_func)
        except Exception as save_err:
            _log(f"ICU 睡眠数据保存失败（不影响推送）: {save_err!r}", log_func)
        # Extract forward-looking goals from analysis and log observation
        try:
            from goal_tracker import extract_goals_from_text, save_goals, append_observation
            goals = extract_goals_from_text(analysis, "ICU睡眠分析")
            save_goals(goals)
            if goals:
                _log(f"提取到 {len(goals)} 条目标计划", log_func)
            append_observation(_format_sleep_observation(payload), "ICU睡眠分析")
        except Exception as exc:
            _log(f"目标提取/观察记录失败（不影响推送）: {exc}", log_func)
    return pushed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()
    check_and_push_sleep(test=args.test)


if __name__ == "__main__":
    main()
