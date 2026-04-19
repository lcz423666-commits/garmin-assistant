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
            f"昨日制定的今日计划（请在建议部分自然融入，评估执行方向是否正确）：\n{goals_text}\n\n"
        )
    base += (
        "要求：\n"
        "1. 用北京时间问候，语气像朋友，自然口语化\n"
        "2. 内容分四部分但不要加标题，自然过渡：\n"
        "   - 睡眠概况（时长是否达标7-9h、评分解读、睡眠心率与静息心率的关系）\n"
        "   - HRV 分析（rMSSD 水平解读、与个人基线对比趋势、SDNN 补充说明、Baevsky 压力指数如果异常则提醒）\n"
        "   - 训练-恢复平衡（结合 CTL/ATL/TSB/rampRate 分析：负荷增速是否过快、疲劳积累程度、当前状态是否适合训练）\n"
        "   - 今日恢复建议（根据 TSB 和 HRV 给出具体建议：该休息/轻松骑/可以上强度）\n"
        "3. 如果有昨日计划，在建议部分自然融入「你昨天计划了X，结合今天数据来看……」\n"
        "4. 如果有主观评估数据（soreness/fatigue/mood），结合分析\n"
        "5. 禁止使用 markdown 加粗语法\n"
        "6. 控制在 350 字以内\n"
        "7. 这是独立于佳明睡眠推送的补充分析，侧重 HRV 和训练负荷关联，不要重复基础睡眠信息\n"
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
    state = _load_state()
    payload_date = payload.get("date")
    if not test and payload_date and state.get("last_date") == payload_date:
        _log(f"ICU 睡眠日期 {payload.get('date')} 已推送过，跳过", log_func)
        return False

    # Load today's pending goals and user profile for richer analysis
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
        except Exception as save_err:
            _log(f"ICU 睡眠数据保存失败（不影响推送）: {save_err}", log_func)
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
