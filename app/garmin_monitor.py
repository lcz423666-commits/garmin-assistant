"""
佳明健康数据监控 + 第一阶段微信陪伴消息脚本
"""

from __future__ import annotations

import html
import json
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from numbers import Real
from pathlib import Path

ROOT_DIR = Path('/root/garmin_assistant')
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
SCRIPTS_DIR = ROOT_DIR / 'scripts'
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import requests

from activity_cleaner import clean_activity, normalize_activity
from app_config import load_system_config, load_users, mask_identifier, sanitize_text
from chart_decider import pick_activity_chart_topic, pick_morning_chart_topic
from chart_renderer import render_line_chart, render_sleep_structure_chart
from chart_storage import build_chart_filename, build_chart_output_path, build_chart_public_url
from user_identity import resolve_user_id
from garmin_storage import (
    count_packages,
    load_package,
    load_recent_normalized,
    load_recent_packages,
    package_path,
    package_exists,
    write_layered_package,
)
from llm_helper import analyze_with_llm
from report_flow import (
    check_evening_triggers,
    enrich_activity_payload,
    generate_evening_report,
    generate_new_morning_report,
    update_known_activity_issues,
    in_evening_window,
)
from phase1_builder import (
    build_activity_payload,
    build_cold_start_snapshot,
    build_initial_7d_summary_payload,
    build_initial_summary_payload,
    build_user_baseline_payload,
    build_weekly_payload,
    build_sleep_payload,
)
from sleep_cleaner import normalize_sleep
from weekly_monthly import generate_monthly_report, generate_weekly_report


SYSTEM_CONFIG = load_system_config()
MONITOR_CONFIG = SYSTEM_CONFIG.get("monitor") or {}
FEISHU_CONFIG = SYSTEM_CONFIG.get("feishu") or {}
PUSHPLUS_CONFIG = SYSTEM_CONFIG.get("pushplus") or {}

DEFAULT_CHECK_INTERVAL = int(MONITOR_CONFIG.get("check_interval_seconds", 1200))
HIGH_FREQUENCY_INTERVAL = int(MONITOR_CONFIG.get("high_frequency_interval_seconds", 300))
REGULAR_INTERVAL = int(MONITOR_CONFIG.get("regular_interval_seconds", DEFAULT_CHECK_INTERVAL))
HIGH_FREQUENCY_START_HOUR = int(MONITOR_CONFIG.get("high_frequency_start_hour", 5))
HIGH_FREQUENCY_END_HOUR = int(MONITOR_CONFIG.get("high_frequency_end_hour", 9))
PID_FILE = os.path.expanduser(MONITOR_CONFIG.get("pid_file", "~/.garmin_monitor.pid"))
STATE_DIR = os.path.expanduser(MONITOR_CONFIG.get("state_dir", "/root/garmin_assistant/state"))
HEALTH_RUNTIME_FILE = os.path.join(STATE_DIR, "health_runtime.json")
RATE_LIMIT_STATE_FILE = os.path.join(STATE_DIR, "garmin_rate_limit.json")
TOKEN_BASE_DIR = MONITOR_CONFIG.get("token_base_dir", "/root/garmin_tokens")
BJ_TZ = timezone(timedelta(hours=8))
WEEKLY_REPORT_WEEKDAY = int(MONITOR_CONFIG.get("weekly_report_weekday", 0))
WEEKLY_REPORT_HOUR = int(MONITOR_CONFIG.get("weekly_report_hour", 9))
MONTHLY_REPORT_DAY = int(MONITOR_CONFIG.get("monthly_report_day", 1))
MONTHLY_REPORT_HOUR = int(MONITOR_CONFIG.get("monthly_report_hour", 9))
INITIAL_BACKFILL_DAYS = int(MONITOR_CONFIG.get("initial_backfill_days", 30))
MIN_INITIAL_BACKFILL_DAYS = int(MONITOR_CONFIG.get("min_initial_backfill_days", 7))
INITIAL_SUMMARY_CATEGORY = "initial_summary"
INITIAL_7D_SUMMARY_RECORD_KEY = "initial_7d_summary"
INITIAL_7D_SUMMARY_TITLE = "佳明健康助手7天初步分析"
INITIAL_SUMMARY_RECORD_KEY = "initial_30d_summary"
INITIAL_SUMMARY_TITLE = "佳明健康助手30天状态总结"
BASELINE_CATEGORY = "baseline"
BASELINE_RECORD_KEY = "current_30d_baseline"

FEISHU_APP_ID = FEISHU_CONFIG.get("app_id")
FEISHU_APP_SECRET = FEISHU_CONFIG.get("app_secret")
FEISHU_APP_TOKEN = FEISHU_CONFIG.get("app_token")
FEISHU_PUSH_TABLE_ID = FEISHU_CONFIG.get("push_table_id")
FEISHU_LOG_TABLE_ID = FEISHU_CONFIG.get("log_table_id")
PUSHPLUS_ADMIN_TOKEN = PUSHPLUS_CONFIG.get("admin_token", "")

DEFAULT_STATE = {
    "last_sleep_date": None,
    "last_activity_ids": [],
    "last_weekly_report_key": None,
    "last_monthly_report_key": None,
    "initial_onboarding_status": None,
    "initial_backfill_completed_at": None,
    "initial_backfill_sleep_days": 0,
    "initial_backfill_activity_count": 0,
    "initial_daily_backfill_completed_at": None,
    "initial_daily_backfill_days": 0,
    "initial_baselines_days_of_data": 0,
    "cold_start_stage": "observe",
    "effective_sleep_days": 0,
    "effective_activity_days": 0,
    "cold_start_snapshot": {},
    "initial_7d_summary_sent_at": None,
    "initial_7d_summary_record_key": None,
    "initial_30d_summary_sent_at": None,
    "initial_30d_summary_record_key": None,
    "initial_summary_sent_at": None,
    "initial_summary_record_key": None,
    "last_successful_cycle_at": None,
    "last_cycle_error": None,
    "last_sleep_check_at": None,
    "last_sleep_check_date": None,
    "last_sleep_check_result": None,
    "last_sleep_check_reason": None,
    "last_sleep_push_date": None,
    "last_sleep_push_at": None,
    "last_sleep_push_result": None,
    "last_sleep_push_reason": None,
    "last_sleep_push_title": None,
    "last_evening_push_date": None,
    "last_evening_push_at": None,
    "last_evening_push_type": None,
    "last_evening_push_result": None,
    "last_evening_push_reason": None,
    "last_evening_push_title": None,
}


def load_enabled_users():
    return [user for user in load_users() if user.get("enabled", True)]


def sync_runtime_users(users, clients, display_names, fail_counts):
    active_names = {user["name"] for user in users}
    for user in users:
        name = user["name"]
        clients.setdefault(name, None)
        display_names.setdefault(name, name)
        fail_counts.setdefault(name, 0)

    for cache in (clients, display_names, fail_counts):
        for name in list(cache.keys()):
            if name not in active_names:
                cache.pop(name, None)


def _default_health_runtime():
    return {
        "updated_at": None,
        "cycle_started_at": None,
        "cycle_finished_at": None,
        "process": {},
        "enabled_users": [],
        "loaded_users": [],
        "per_user": {},
    }


def load_health_runtime():
    payload = _default_health_runtime()
    if os.path.exists(HEALTH_RUNTIME_FILE):
        try:
            stored = json.loads(open(HEALTH_RUNTIME_FILE, "r", encoding="utf-8").read())
            if isinstance(stored, dict):
                payload.update(stored)
                payload["process"] = stored.get("process") or {}
                payload["per_user"] = stored.get("per_user") or {}
        except Exception:
            pass
    return payload


def save_health_runtime(payload):
    os.makedirs(os.path.dirname(HEALTH_RUNTIME_FILE), exist_ok=True)
    with open(HEALTH_RUNTIME_FILE, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)


def prune_health_runtime_users(payload, enabled_users):
    if not isinstance(enabled_users, list):
        return
    per_user = payload.setdefault("per_user", {})
    if not isinstance(per_user, dict):
        payload["per_user"] = {}
        return
    active_names = {str(name) for name in enabled_users if str(name)}
    for user_name in list(per_user.keys()):
        if user_name not in active_names:
            per_user.pop(user_name, None)


def update_health_runtime(*, root_updates=None, user_updates=None):
    payload = load_health_runtime()
    if root_updates:
        payload.update(root_updates)
    if user_updates:
        per_user = payload.setdefault("per_user", {})
        for user_name, updates in user_updates.items():
            user_payload = per_user.setdefault(user_name, {})
            user_payload.update(updates)
    if root_updates and "enabled_users" in root_updates:
        prune_health_runtime_users(payload, root_updates.get("enabled_users"))
    payload["updated_at"] = bj_now().isoformat()
    save_health_runtime(payload)
    return payload


def bj_now() -> datetime:
    return datetime.now(BJ_TZ)


PROCESS_STARTED_AT = bj_now().isoformat()
PROCESS_STARTED_TS = time.time()
MONITOR_CODE_PATHS = [
    '/root/garmin_assistant/app/garmin_monitor.py',
    '/root/garmin_assistant/app/chart_config.py',
    '/root/garmin_assistant/app/chart_storage.py',
    '/root/garmin_assistant/app/chart_renderer.py',
    '/root/garmin_assistant/app/chart_decider.py',
    '/root/garmin_assistant/app/report_flow.py',
    '/root/garmin_assistant/app/llm_helper.py',
    '/root/garmin_assistant/weekly_monthly.py',
    '/root/garmin_assistant/analysis.py',
    '/root/garmin_assistant/experiment.py',
]


def sources_newer_than(process_started_ts: float, file_paths: list[str]) -> list[str]:
    updated = []
    for file_path in file_paths:
        try:
            if os.path.getmtime(file_path) > process_started_ts:
                updated.append(file_path)
        except OSError:
            continue
    return updated


def build_sleep_package_metadata(recorded_at: str, llm_payload: dict | None = None) -> dict:
    message_type = 'sleep_morning'
    if isinstance(llm_payload, dict):
        message_type = sanitize_text(llm_payload.get('message_type') or message_type)[:1000] or 'sleep_morning'
    return {
        'message_type': message_type,
        'recorded_at': recorded_at,
    }


def should_reload_for_code_update() -> list[str]:
    return sources_newer_than(PROCESS_STARTED_TS, MONITOR_CODE_PATHS)


def day_recorded_at(date_str: str) -> str:
    return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=BJ_TZ, hour=12).isoformat()


def month_day_label(date_str: str) -> str:
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return f"{dt.month}月{dt.day}日"
    except Exception:
        return date_str


def _activity_time_candidates(activity):
    if not isinstance(activity, dict):
        return []
    candidates = [activity]
    for key in ("summaryDTO", "summary"):
        nested = activity.get(key)
        if isinstance(nested, dict):
            candidates.append(nested)
    return candidates


def _activity_time_value(activity, key: str) -> str:
    for candidate in _activity_time_candidates(activity):
        value = candidate.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _parse_activity_time(value: str, *, tz) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("T", " ").strip()
    if len(normalized) >= 19:
        normalized = normalized[:19]
    try:
        return datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)
    except Exception:
        return None


def beijing_date_from_activity(activity):
    gmt_str = _activity_time_value(activity, "startTimeGMT")
    if gmt_str:
        gmt_dt = _parse_activity_time(gmt_str, tz=timezone.utc)
        if gmt_dt:
            return (gmt_dt + timedelta(hours=8)).strftime("%Y-%m-%d")
    local_str = _activity_time_value(activity, "startTimeLocal")
    if local_str:
        return local_str.replace("T", " ")[:10]
    return ""


def activity_recorded_at(activity):
    local_str = _activity_time_value(activity, "startTimeLocal")
    if local_str:
        local_dt = _parse_activity_time(local_str, tz=BJ_TZ)
        if local_dt:
            return local_dt.isoformat()
    gmt_str = _activity_time_value(activity, "startTimeGMT")
    if gmt_str:
        gmt_dt = _parse_activity_time(gmt_str, tz=timezone.utc)
        if gmt_dt:
            return gmt_dt.astimezone(BJ_TZ).isoformat()
    activity_date = beijing_date_from_activity(activity)
    if activity_date:
        return day_recorded_at(activity_date)
    return bj_now().isoformat()


def log(msg, user_name=None):
    timestamp = bj_now().strftime("%Y-%m-%d %H:%M:%S")
    prefix = f"[{user_name}] " if user_name else ""
    print(f"[{timestamp}] {prefix}{sanitize_text(str(msg))}", flush=True)


def get_token_dir(user):
    safe_email = user["garmin_email"].replace("@", "_at_").replace(".", "_")
    return os.path.join(TOKEN_BASE_DIR, safe_email)


def get_state_file(user):
    safe_name = user["name"].replace("/", "_").replace(" ", "_")
    return os.path.join(STATE_DIR, f".garmin_monitor_state_{safe_name}.json")


def load_state(user):
    state = dict(DEFAULT_STATE)
    state_file = get_state_file(user)
    if os.path.exists(state_file):
        try:
            state.update(json.loads(open(state_file, "r", encoding="utf-8").read()))
        except Exception:
            pass
    if state.get("initial_summary_sent_at") and not state.get("initial_30d_summary_sent_at"):
        state["initial_30d_summary_sent_at"] = state["initial_summary_sent_at"]
    if state.get("initial_summary_record_key") and not state.get("initial_30d_summary_record_key"):
        state["initial_30d_summary_record_key"] = state["initial_summary_record_key"]
    if state.get("initial_onboarding_status") == "skipped_existing_user":
        state["cold_start_stage"] = "mature"
    if state.get("initial_30d_summary_sent_at"):
        state["cold_start_stage"] = "mature"
    return state


def save_state(user, state):
    state_file = get_state_file(user)
    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    if state.get("initial_30d_summary_sent_at"):
        state["initial_summary_sent_at"] = state["initial_30d_summary_sent_at"]
    if state.get("initial_30d_summary_record_key"):
        state["initial_summary_record_key"] = state["initial_30d_summary_record_key"]
    with open(state_file, "w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)


def update_sleep_health_state(
    state,
    *,
    date_str: str,
    check_result: str,
    check_reason: str | None = None,
    push_result: str | None = None,
    push_reason: str | None = None,
    push_title: str | None = None,
):
    state["last_sleep_check_at"] = bj_now().isoformat()
    state["last_sleep_check_date"] = date_str
    state["last_sleep_check_result"] = check_result
    state["last_sleep_check_reason"] = check_reason
    if push_result is not None:
        state["last_sleep_push_date"] = date_str
        state["last_sleep_push_at"] = bj_now().isoformat()
        state["last_sleep_push_result"] = push_result
        state["last_sleep_push_reason"] = push_reason
        state["last_sleep_push_title"] = push_title


def update_evening_push_state(
    state,
    *,
    date_str: str | None = None,
    push_type: str | None = None,
    push_result: str | None = None,
    push_reason: str | None = None,
    push_title: str | None = None,
):
    if date_str is not None:
        state["last_evening_push_date"] = date_str
    state["last_evening_push_at"] = bj_now().isoformat()
    if push_type is not None:
        state["last_evening_push_type"] = push_type
    if push_result is not None:
        state["last_evening_push_result"] = push_result
    state["last_evening_push_reason"] = push_reason
    state["last_evening_push_title"] = push_title


_feishu_token_cache = {"token": None, "expires_at": 0}


def feishu_enabled() -> bool:
    return bool(
        FEISHU_CONFIG.get("enabled", True)
        and FEISHU_APP_ID
        and FEISHU_APP_SECRET
        and FEISHU_APP_TOKEN
        and FEISHU_PUSH_TABLE_ID
        and FEISHU_LOG_TABLE_ID
    )


def get_feishu_token():
    if not feishu_enabled():
        return None
    now = time.time()
    if _feishu_token_cache["token"] and now < _feishu_token_cache["expires_at"]:
        return _feishu_token_cache["token"]
    try:
        response = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
            timeout=10,
        )
        data = response.json()
        token = data.get("tenant_access_token")
        expire = data.get("expire", 7200)
        _feishu_token_cache["token"] = token
        _feishu_token_cache["expires_at"] = now + expire - 300
        return token
    except Exception as exc:
        log(f"飞书 token 获取失败: {exc}")
        return None


def find_user_by_name(user_name: str):
    for user in load_users():
        if user.get("name") == user_name:
            return user
    return None


def current_check_strategy(now: datetime | None = None) -> tuple[int, str]:
    current = now or bj_now()
    if HIGH_FREQUENCY_START_HOUR <= current.hour < HIGH_FREQUENCY_END_HOUR:
        return HIGH_FREQUENCY_INTERVAL, "晨报高频时段"
    return REGULAR_INTERVAL, "常规时段"


def log_current_check_strategy(now: datetime | None = None):
    interval_seconds, label = current_check_strategy(now)
    log(f"当前处于{label}，轮询间隔 {interval_seconds // 60} 分钟")
    return interval_seconds, label


def normalize_message_type(*, title: str = "", activity_type: str = "") -> str:
    source = f"{title} {activity_type}"
    if "7天初步分析" in source:
        return "7天初步分析"
    if "30天状态总结" in source or "第一次用户分析" in source:
        return "30天状态总结"
    if "周报" in source or "过去7天" in source or "固定总结" in source:
        return "周报"
    if "睡眠" in source and "晨报" in source:
        return "睡眠晨报"
    if "运动快报" in source:
        return "运动快报"
    if activity_type and activity_type not in {"趋势周报"}:
        return "运动快报"
    return ""


ALLOWED_RUN_LOG_EVENT_TYPES = {
    "推送成功",
    "推送失败",
    "晨报巡检",
    "跟踪巡检",
    "日终巡检",
    "首次接入",
    "用户新增",
}


def normalize_run_log_event_type(event_type: str) -> str:
    text = sanitize_text(event_type or "")
    lowered = text.lower()
    if text in ALLOWED_RUN_LOG_EVENT_TYPES:
        return text
    if "晨报巡检" in text or "晨报检查" in text:
        return "晨报巡检"
    if "followup" in lowered or "跟踪" in text:
        return "跟踪巡检"
    if "日终巡检" in text:
        return "日终巡检"
    if "首次接入" in text or "onboarding" in lowered:
        return "首次接入"
    if "用户新增" in text or "新增用户" in text:
        return "用户新增"
    if "成功" in text:
        return "推送成功"
    if any(keyword in text for keyword in ("失败", "异常", "失效", "连接")) or any(
        keyword in lowered for keyword in ("login", "token", "auth")
    ):
        return "推送失败"
    return ""


def normalize_error_code(detail: str | None = None, *, reason_code: str | None = None) -> str:
    if reason_code:
        mapping = {
            "auth_error": "token_invalid",
            "push_failed": "push_failed",
            "analysis_error": "llm_failed",
            "no_data": "no_data",
        }
        return mapping.get(reason_code, "unknown")

    raw_text = sanitize_text(detail or "")
    lowered = raw_text.lower()
    if not lowered:
        return ""
    if "token_invalid" in lowered or ("token" in lowered and any(key in lowered for key in ("401", "403", "失效", "expired", "invalid"))):
        return "token_invalid"
    if "login_failed" in lowered or ("login" in lowered and "fail" in lowered) or ("登录" in raw_text and "失败" in raw_text):
        return "login_failed"
    if "push_failed" in lowered or "推送失败" in raw_text or "推送异常" in raw_text or "pushplus" in lowered:
        return "push_failed"
    if "llm" in lowered or "deepseek" in lowered or "analysis_error" in lowered or "分析生成失败" in raw_text:
        return "llm_failed"
    if "no_data" in lowered or "未返回完整睡眠数据" in raw_text or "暂无" in raw_text:
        return "no_data"
    return "unknown"


def resolve_user_stage(*, user=None, user_name: str = "", state: dict | None = None, user_stage: str = "") -> str:
    if user_stage:
        return sanitize_text(user_stage)[:1000]
    try:
        if state is None:
            if user is None and user_name:
                user = find_user_by_name(user_name)
            if user is not None:
                state = load_state(user)
        if not state:
            return ""
        return sanitize_text(build_cold_start_context(state).get("user_stage") or "")[:1000]
    except Exception:
        return ""


def resolve_first_onboarding_flag(user_stage: str, is_first_onboarding: str = "") -> str:
    if is_first_onboarding in {"是", "否"}:
        return is_first_onboarding
    if not user_stage:
        return ""
    return "是" if user_stage in {"observation", "early"} else "否"


def resolve_push_mode(user: dict | None) -> str:
    if not isinstance(user, dict):
        return "self"
    mode = sanitize_text(str(user.get("push_mode") or "self")).strip().lower()
    return mode if mode in {"self", "friend"} else "self"


def render_pushplus_html(content: str, chart_image_url: str | None = None) -> str:
    body = html.escape(sanitize_text(content or ""), quote=False).replace("\r\n", "\n").replace("\r", "\n")
    body = body.replace("\n", "<br>")
    image_url = str(chart_image_url or "").strip()
    if not image_url:
        return body
    safe_image_url = html.escape(image_url, quote=True)
    return (
        f'<div style="margin-bottom:16px;"><img src="{safe_image_url}" alt="趋势图" '
        'style="width:100%;max-width:540px;height:auto;display:block;" /></div>'
        f'<div>{body}</div>'
    )


def _normalize_chart_values(values):
    if values is None or isinstance(values, (str, bytes, dict)):
        return None
    try:
        series = list(values)
    except TypeError:
        return None
    if not series:
        return None
    normalized = []
    for value in series:
        if isinstance(value, bool) or not isinstance(value, Real):
            return None
        normalized.append(float(value))
    return normalized


def _normalize_chart_labels(labels, value_count: int):
    if value_count <= 0:
        return []
    if labels is None or isinstance(labels, (str, bytes, dict)):
        return [str(index + 1) for index in range(value_count)]
    try:
        label_list = list(labels)
    except TypeError:
        return [str(index + 1) for index in range(value_count)]
    if len(label_list) != value_count:
        return [str(index + 1) for index in range(value_count)]
    return [str(label) for label in label_list]


def _safe_nested_number(payload, *keys):
    current = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    if current is None or isinstance(current, bool) or not isinstance(current, Real):
        return None
    return float(current)


def _short_date_label(date_str: str | None) -> str:
    value = sanitize_text(date_str or "")
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%m/%d")
    except Exception:
        return value[-5:] if len(value) >= 5 else value


def maybe_build_morning_chart(
    user_id: str,
    date_str: str,
    normalized: dict,
    recent_history: list[dict],
    llm_payload: dict | None = None,
    *,
    raw_sleep_data: dict | None = None,
) -> str | None:
    try:
        sleep_dto = ((raw_sleep_data or {}).get("dailySleepDTO") or {}) if isinstance(raw_sleep_data, dict) else {}
        total_sleep_seconds = _safe_nested_number(sleep_dto, "sleepTimeSeconds")
        deep_sleep_seconds = _safe_nested_number(sleep_dto, "deepSleepSeconds")
        light_sleep_seconds = _safe_nested_number(sleep_dto, "lightSleepSeconds")
        rem_sleep_seconds = _safe_nested_number(sleep_dto, "remSleepSeconds")
        awake_sleep_seconds = _safe_nested_number(sleep_dto, "awakeSleepSeconds")
        if total_sleep_seconds is None:
            total_sleep_seconds = _safe_nested_number(normalized, "basic_sleep", "total_sleep_min")
            total_sleep_seconds = total_sleep_seconds * 60 if total_sleep_seconds is not None else None
        if deep_sleep_seconds is None:
            deep_sleep_minutes = _safe_nested_number(normalized, "basic_sleep", "deep_sleep_min")
            deep_sleep_seconds = deep_sleep_minutes * 60 if deep_sleep_minutes is not None else None
        if total_sleep_seconds is None or deep_sleep_seconds is None:
            return None
        if light_sleep_seconds is None or rem_sleep_seconds is None or awake_sleep_seconds is None:
            return None

        opaque_name = build_chart_filename(user_id, "morning", "sleep_structure")
        output_path = build_chart_output_path(user_id, "morning", date_str, opaque_name)
        render_sleep_structure_chart(
            output_path=output_path,
            total_sleep_minutes=total_sleep_seconds / 60,
            deep_sleep_minutes=deep_sleep_seconds / 60,
            light_sleep_minutes=light_sleep_seconds / 60,
            rem_sleep_minutes=rem_sleep_seconds / 60,
            awake_minutes=awake_sleep_seconds / 60,
        )
        return build_chart_public_url(user_id, "morning", date_str, opaque_name)
    except Exception as exc:
        log(f"晨报睡眠结构图生成失败: {exc}", user_id)
        return None


def maybe_build_activity_chart(user_id: str, activity_date: str, normalized: dict, recent_history: list[dict], llm_payload: dict | None = None) -> str | None:
    try:
        current_sport_type = sanitize_text(((normalized or {}).get("basic_activity") or {}).get("sport_type") or "")
        same_sport_history = []
        fallback_history = []
        for item in recent_history or []:
            history_sport = sanitize_text(((item or {}).get("basic_activity") or {}).get("sport_type") or "")
            fallback_history.append(item)
            if history_sport and current_sport_type and history_sport != current_sport_type:
                continue
            same_sport_history.append(item)
            if len(same_sport_history) >= 6:
                break
        if len(same_sport_history) >= 3:
            selected_history = same_sport_history
            chart_sport_label = ((llm_payload or {}).get("sport_type") or ((normalized or {}).get("basic_activity") or {}).get("activity_name") or "训练")
        else:
            selected_history = fallback_history[:6]
            chart_sport_label = "训练"

        candidate_metrics = [
            ("training_load", lambda item: _safe_nested_number(item, "load_recovery", "activity_training_load")),
            ("training_load", lambda item: _safe_nested_number(item, "load_recovery", "training_stress_score")),
            ("duration", lambda item: _safe_nested_number(item, "basic_activity", "duration_min")),
        ]
        chosen_metric = None
        values = []
        labels = []
        ordered_items = [*reversed(selected_history), normalized]
        for metric_name, extractor in candidate_metrics:
            metric_values = []
            metric_labels = []
            for item in ordered_items:
                metric_value = extractor(item)
                if metric_value is None:
                    continue
                metric_values.append(metric_value)
                item_date = sanitize_text(((item or {}).get("basic_activity") or {}).get("date") or activity_date)
                metric_labels.append(_short_date_label(item_date))
            if len(metric_values) >= 4:
                chosen_metric = metric_name
                values = metric_values
                labels = metric_labels
                break
        if not chosen_metric:
            return None

        decision = pick_activity_chart_topic(
            sport_type=chart_sport_label,
            history_points=values,
            metric_name=chosen_metric,
        )
        if not decision:
            return None

        values = _normalize_chart_values(values)
        if not values:
            return None

        labels = _normalize_chart_labels(labels, len(values))
        opaque_name = build_chart_filename(user_id, "activity", decision["topic"])
        output_path = build_chart_output_path(user_id, "activity", activity_date, opaque_name)
        render_line_chart(
            output_path=output_path,
            title=decision["title"],
            x_labels=labels,
            y_values=values,
            highlight_index=len(values) - 1,
            mean_value=sum(values) / len(values),
            line_color=decision["line_color"],
        )
        return build_chart_public_url(user_id, "activity", activity_date, opaque_name)
    except Exception as exc:
        log(f"运动趋势图生成失败: {exc}", user_id)
        return None


def maybe_build_report_chart(user_id: str, message_type: str, date_key: str, chart_history: dict | None) -> str | None:
    try:
        if not isinstance(chart_history, dict):
            return None
        points = chart_history.get("history_points")
        if not isinstance(points, list):
            return None
        labels = []
        values = []
        for point in points:
            if not isinstance(point, dict):
                continue
            value = point.get("value")
            if isinstance(value, bool) or not isinstance(value, Real):
                continue
            labels.append(sanitize_text(str(point.get("label") or point.get("date") or len(labels) + 1)))
            values.append(float(value))
        min_points = 10 if message_type == "monthly" else 4
        if len(values) < min_points:
            return None
        topic = sanitize_text(chart_history.get("topic") or "trend")[:1000] or "trend"
        title = sanitize_text(chart_history.get("title") or "趋势")[:1000] or "趋势"
        line_color = sanitize_text(chart_history.get("line_color") or "#245A78")[:20] or "#245A78"
        labels = _normalize_chart_labels(labels, len(values))
        opaque_name = build_chart_filename(user_id, message_type, topic)
        output_path = build_chart_output_path(user_id, message_type, date_key, opaque_name)
        render_line_chart(
            output_path=output_path,
            title=title,
            x_labels=labels,
            y_values=values,
            highlight_index=len(values) - 1,
            mean_value=sum(values) / len(values),
            line_color=line_color,
        )
        return build_chart_public_url(user_id, message_type, date_key, opaque_name)
    except Exception as exc:
        log(f"{message_type} 趋势图生成失败: {exc}", user_id)
        return None


def build_pushplus_payload(user, title: str, content: str, chart_image_url: str | None = None):
    push_mode = resolve_push_mode(user)
    payload = {
        "title": title,
        "content": render_pushplus_html(content, chart_image_url=chart_image_url),
        "template": "html",
    }
    target = None
    if push_mode == "self":
        token = user.get("pushplus_token")
        if not token:
            raise ValueError("self 模式缺少 pushplus_token")
        payload["token"] = token
        target = token
    elif push_mode == "friend":
        if not PUSHPLUS_ADMIN_TOKEN:
            raise ValueError("friend 模式缺少管理员 PushPlus token 配置")
        friend_token = user.get("friend_token")
        if not friend_token:
            raise ValueError("friend 模式缺少 friend_token")
        payload["token"] = PUSHPLUS_ADMIN_TOKEN
        payload["to"] = friend_token
        target = friend_token
    else:
        raise ValueError(f"未知的推送模式: {push_mode}")
    return payload, {
        "push_mode": push_mode,
        "push_target": mask_identifier(str(target or "")),
    }


def feishu_write_push_record(
    user_name,
    activity_type,
    distance_duration,
    status,
    content,
    *,
    title: str = "",
    user=None,
    user_stage: str = "",
    is_first_onboarding: str = "",
    failure_reason: str = "",
    push_mode: str = "",
):
    try:
        token = get_feishu_token()
        if not token:
            return
        now = bj_now()
        resolved_user_stage = resolve_user_stage(user=user, user_name=user_name, user_stage=user_stage)
        resolved_message_type = normalize_message_type(title=title, activity_type=activity_type)
        resolved_failure_reason = ""
        if status != "成功":
            resolved_failure_reason = sanitize_text(failure_reason or content)[:1000]
        fields = {
            "日期": now.strftime("%Y-%m-%d"),
            "时间": now.strftime("%H:%M:%S"),
            "用户": user_name,
            "活动类型": (activity_type + (f" [{push_mode}]" if push_mode else ""))[:1000],
            "距离/时长": distance_duration,
            "推送状态": sanitize_text(status)[:1000],
            "推送内容全文": sanitize_text(content)[:50000],
            "消息类型": resolved_message_type,
            "消息标题": (sanitize_text(title) + (f" [{push_mode}]" if push_mode else ""))[:1000],
            "用户阶段": resolved_user_stage,
            "是否首次接入阶段": resolve_first_onboarding_flag(resolved_user_stage, is_first_onboarding),
            "失败原因": resolved_failure_reason,
        }
        response = requests.post(
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}/tables/{FEISHU_PUSH_TABLE_ID}/records",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"fields": fields},
            timeout=15,
        )
        result = response.json()
        if result.get("code") != 0:
            log(f"飞书推送记录写入失败: {result.get('msg')}", user_name)
    except Exception as exc:
        log(f"飞书推送记录写入异常: {exc}", user_name)


def feishu_write_run_log(
    user_name,
    event_type,
    detail,
    *,
    message_type: str = "",
    user_stage: str = "",
    error_code: str = "",
    user=None,
):
    try:
        token = get_feishu_token()
        if not token:
            return
        now = bj_now()
        resolved_event_type = normalize_run_log_event_type(event_type)
        if not resolved_event_type:
            return
        detail_text = sanitize_text(detail)[:10000]
        is_inspection_event = resolved_event_type in {"晨报巡检", "跟踪巡检", "日终巡检"}
        resolved_user = "" if is_inspection_event else sanitize_text(user_name)[:1000]
        resolved_message_type = "" if is_inspection_event else sanitize_text(message_type)[:1000]
        resolved_user_stage = ""
        if not is_inspection_event:
            resolved_user_stage = resolve_user_stage(user=user, user_name=user_name, user_stage=user_stage)
        resolved_error_code = sanitize_text(error_code)[:1000]
        if not resolved_error_code and any(key in detail_text for key in ("错误", "异常", "失败", "失效")):
            resolved_error_code = sanitize_text(normalize_error_code(detail))[:1000]
        if is_inspection_event or resolved_event_type == "推送成功":
            resolved_error_code = ""
        fields = {
            "日期": now.strftime("%Y-%m-%d"),
            "时间": now.strftime("%H:%M:%S"),
            "事件类型": resolved_event_type,
            "用户": resolved_user,
            "详情": detail_text,
            "关联消息类型": resolved_message_type,
            "关联用户阶段": resolved_user_stage,
            "错误原因标准化": resolved_error_code,
        }
        response = requests.post(
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}/tables/{FEISHU_LOG_TABLE_ID}/records",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"fields": fields},
            timeout=15,
        )
        result = response.json()
        if result.get("code") != 0:
            log(f"飞书运行日志写入失败: {result.get('msg')}", user_name)
    except Exception as exc:
        log(f"飞书运行日志写入异常: {exc}", user_name)


def login_garmin(user):
    token_dir = get_token_dir(user)
    os.makedirs(token_dir, exist_ok=True)

    token_files = {
        file for file in os.listdir(token_dir)
        if not file.startswith(".")
    }
    required_token_files = {"oauth1_token.json", "oauth2_token.json"}
    if not required_token_files.issubset(token_files):
        raise RuntimeError(
            "Missing uploaded OAuth token files; local token upload is required before server login"
        )

    from garminconnect import Garmin

    client = Garmin(
        user["garmin_email"],
        user["garmin_password"],
        is_cn=user.get("garmin_is_cn", False),
    )

    try:
        log("尝试加载上传 token", user["name"])
        client.login(tokenstore=token_dir)
        log("Token 加载成功，跳过登录", user["name"])
        return client
    except Exception as exc:
        raise RuntimeError(
            f"Uploaded OAuth token is invalid or expired; local re-login and upload required: {exc}"
        ) from exc

def is_auth_error(exc):
    message = str(exc).lower()
    # 429 是限流，不是认证失败，不能清除 token 目录
    if "429" in message or "too many requests" in message:
        return False
    return any(key in message for key in ("401", "authentication", "auth", "token", "unauthorized", "login"))


def is_rate_limit_error(exc):
    message = str(exc).lower()
    return "429" in message or "too many requests" in message


# 限流退避：{用户名或全局键: 解除限流的时间戳}
_rate_limit_backoff: dict[str, float] = {}
RATE_LIMIT_BACKOFF_SECONDS = 7200  # 429 后退避 2 小时
RATE_LIMIT_GLOBAL_KEY = "__global__"


def _load_rate_limit_state() -> dict[str, float]:
    try:
        with open(RATE_LIMIT_STATE_FILE, 'r', encoding='utf-8') as file:
            data = json.load(file)
        if not isinstance(data, dict):
            return {}
        cleaned = {}
        now_ts = time.time()
        for key, value in data.items():
            try:
                ts = float(value)
            except Exception:
                continue
            if ts > now_ts:
                cleaned[str(key)] = ts
        return cleaned
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def _save_rate_limit_state(state: dict[str, float]) -> None:
    os.makedirs(os.path.dirname(RATE_LIMIT_STATE_FILE), exist_ok=True)
    with open(RATE_LIMIT_STATE_FILE, 'w', encoding='utf-8') as file:
        json.dump(state, file, ensure_ascii=False, indent=2)


def get_rate_limit_backoff_until(name: str = '', now_ts: float | None = None) -> float | None:
    global _rate_limit_backoff
    now_ts = now_ts or time.time()
    persisted = _load_rate_limit_state()
    if persisted:
        _rate_limit_backoff.update(persisted)
    expired = [key for key, value in _rate_limit_backoff.items() if value <= now_ts]
    for key in expired:
        _rate_limit_backoff.pop(key, None)
    if expired:
        _save_rate_limit_state(_rate_limit_backoff)

    candidates = []
    if name and name in _rate_limit_backoff:
        candidates.append(_rate_limit_backoff[name])
    if RATE_LIMIT_GLOBAL_KEY in _rate_limit_backoff:
        candidates.append(_rate_limit_backoff[RATE_LIMIT_GLOBAL_KEY])
    return max(candidates) if candidates else None


def record_rate_limit_backoff(name: str, now_ts: float | None = None, seconds: int = RATE_LIMIT_BACKOFF_SECONDS) -> float:
    global _rate_limit_backoff
    now_ts = now_ts or time.time()
    until = now_ts + seconds
    if name:
        _rate_limit_backoff[name] = until
    _rate_limit_backoff[RATE_LIMIT_GLOBAL_KEY] = until
    _save_rate_limit_state(_rate_limit_backoff)
    return until


def clear_rate_limit_backoff(name: str = '') -> None:
    global _rate_limit_backoff
    changed = False
    if name and name in _rate_limit_backoff:
        _rate_limit_backoff.pop(name, None)
        changed = True
    if changed:
        _save_rate_limit_state(_rate_limit_backoff)


def get_display_name(client, fallback_name):
    try:
        profile = client.get_full_name()
        if profile:
            return profile
    except Exception:
        pass
    try:
        profile = client.get_profile()
        for field in ("displayName", "fullName", "userName"):
            value = profile.get(field)
            if value:
                return value
    except Exception:
        pass
    return fallback_name


def push_to_wechat(
    user,
    title: str,
    content: str,
    activity_type: str = "",
    distance_duration: str = "",
    chart_image_url: str | None = None,
):
    url = "https://www.pushplus.plus/send"
    push_started_at = time.perf_counter()
    try:
        data, push_meta = build_pushplus_payload(user, title, content, chart_image_url=chart_image_url)
        response = requests.post(url, json=data, timeout=10)
        result = response.json()
        resolved_message_type = normalize_message_type(title=title, activity_type=activity_type)
        resolved_user_stage = resolve_user_stage(user=user)
        push_mode = push_meta.get("push_mode") or resolve_push_mode(user)
        if result.get("code") == 200:
            push_elapsed = time.perf_counter() - push_started_at
            log(f"✅ 微信推送成功: {title} | mode={push_mode} | PushPlus耗时 {push_elapsed:.2f}s", user["name"])
            feishu_write_push_record(
                user["name"],
                activity_type or title,
                distance_duration,
                "成功",
                content,
                title=title,
                user=user,
                user_stage=resolved_user_stage,
                push_mode=push_mode,
            )
            feishu_write_run_log(
                user["name"],
                "推送成功",
                (f"已成功推送「{title}」" + (f"，{distance_duration}" if distance_duration else "") + f"（模式:{push_mode}）"),
                message_type=resolved_message_type,
                user_stage=resolved_user_stage,
                user=user,
            )
            return True
        else:
            push_elapsed = time.perf_counter() - push_started_at
            message = sanitize_text(str(result.get("msg", "未知错误")))
            log(f"❌ 推送失败({push_elapsed:.2f}s, mode={push_mode}): {message}", user["name"])
            feishu_write_push_record(
                user["name"],
                activity_type or title,
                distance_duration,
                "失败",
                f"推送失败: {message}",
                title=title,
                user=user,
                user_stage=resolved_user_stage,
                failure_reason=message,
                push_mode=push_mode,
            )
            feishu_write_run_log(
                user["name"],
                "推送失败",
                f"推送「{title}」失败：{message}（模式:{push_mode}）",
                message_type=resolved_message_type,
                user_stage=resolved_user_stage,
                error_code="push_failed",
                user=user,
            )
            return False
    except Exception as exc:
        push_elapsed = time.perf_counter() - push_started_at
        push_mode = resolve_push_mode(user)
        log(f"❌ 推送异常({push_elapsed:.2f}s, mode={push_mode}): {exc}", user["name"])
        resolved_message_type = normalize_message_type(title=title, activity_type=activity_type)
        resolved_user_stage = resolve_user_stage(user=user)
        feishu_write_push_record(
            user["name"],
            activity_type or title,
            distance_duration,
            "失败",
            f"推送异常: {exc}",
            title=title,
            user=user,
            user_stage=resolved_user_stage,
            failure_reason=str(exc),
            push_mode=push_mode,
        )
        feishu_write_run_log(
            user["name"],
            "推送失败",
            f"推送「{title}」异常：{exc}（模式:{push_mode}）",
            message_type=resolved_message_type,
            user_stage=resolved_user_stage,
            error_code="push_failed",
            user=user,
        )
        return False


def safe_call(func, default):
    try:
        return func()
    except Exception:
        return default


def mark_silent_metadata(metadata: dict, reason: str) -> dict:
    result = dict(metadata)
    result.update(
        {
            "silent": True,
            "no_push": True,
            "no_push_reason": reason,
        }
    )
    return result


def build_push_candidate(
    *,
    title: str,
    content: str,
    activity_type: str,
    distance_duration: str,
    recorded_at: str,
    chart_image_url: str | None = None,
    category: str | None = None,
    record_key: str | None = None,
) -> dict:
    return {
        "title": title,
        "content": content,
        "activity_type": activity_type,
        "distance_duration": distance_duration,
        "recorded_at": recorded_at,
        "chart_image_url": chart_image_url,
        "category": category,
        "record_key": record_key,
    }


def push_candidate(user, candidate: dict) -> bool:
    return push_to_wechat(
        user,
        candidate["title"],
        candidate["content"],
        activity_type=candidate["activity_type"],
        distance_duration=candidate["distance_duration"],
        chart_image_url=candidate.get("chart_image_url"),
    )


def clear_candidate_silent_flag(user, candidate: dict):
    category = candidate.get("category")
    record_key = candidate.get("record_key")
    if not category or not record_key:
        return
    path = package_path(user["name"], category, record_key)
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        metadata = payload.get("metadata") or {}
        metadata.pop("silent", None)
        metadata.pop("no_push", None)
        metadata.pop("no_push_reason", None)
        payload["metadata"] = metadata
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    except Exception as exc:
        log(f"更新数据包推送标记失败: {exc}", user["name"])


def check_and_send_evening_push(client, user, display_name, state):
    today = bj_now().strftime("%Y-%m-%d")
    if not in_evening_window():
        return {"checked": False, "pushed": False, "trigger_type": None}
    if state.get("last_evening_push_date") == today and state.get("last_evening_push_result") == "success":
        return {"checked": True, "pushed": False, "trigger_type": state.get("last_evening_push_type")}

    triggers = check_evening_triggers(client, user["name"], today)
    if not triggers:
        return {"checked": True, "pushed": False, "trigger_type": None}

    priority_order = {
        "anomaly_followup": 1,
        "extreme_fatigue": 2,
        "recovery_reminder": 3,
        "high_stress_day": 4,
        "low_activity": 5,
    }
    ordered = sorted(triggers, key=lambda item: priority_order.get(item.get("type"), 99))
    primary_type = ordered[0].get("type")
    message = generate_evening_report(display_name, today, ordered)
    candidate = build_push_candidate(
        title=f"佳明晚间提醒（{month_day_label(today)}）",
        content=message,
        activity_type="晚间提醒",
        distance_duration=primary_type or "晚间跟进",
        recorded_at=bj_now().isoformat(),
        category="evening",
        record_key=today,
    )
    metadata = {
        "message_type": "evening_push",
        "recorded_at": bj_now().isoformat(),
        "trigger_type": primary_type,
        "trigger_types": [item.get("type") for item in ordered],
    }
    write_layered_package(
        user["name"],
        "evening",
        today,
        raw_data={"triggers": ordered},
        normalized_data={"triggers": ordered},
        llm_payload={"message_type": "evening_push", "triggers": ordered},
        message_preview=message,
        metadata=metadata,
    )
    pushed = push_candidate(user, candidate)
    if pushed:
        update_evening_push_state(
            state,
            date_str=today,
            push_type=primary_type,
            push_result="success",
            push_reason=None,
            push_title=candidate["title"],
        )
    else:
        update_evening_push_state(
            state,
            push_type=primary_type,
            push_result="failed",
            push_reason="push_to_wechat_failed",
            push_title=candidate["title"],
        )
    save_state(user, state)
    return {"checked": True, "pushed": pushed, "trigger_type": primary_type}


def candidate_recorded_at(candidate: dict | None) -> datetime | None:
    if not candidate:
        return None
    value = candidate.get("recorded_at")
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BJ_TZ)
    return dt


def should_push_activity_candidate(candidate: dict | None, *, now: datetime | None = None) -> bool:
    if not candidate or candidate.get("category") != "activity":
        return True
    recorded_dt = candidate_recorded_at(candidate)
    if not recorded_dt:
        return True
    current = now or bj_now()
    return recorded_dt.astimezone(BJ_TZ).date() >= current.date()


def choose_latest_daily_candidate(*candidates: dict | None) -> dict | None:
    available = [candidate for candidate in candidates if candidate]
    if not available:
        return None
    return max(
        available,
        key=lambda candidate: (
            candidate_recorded_at(candidate) or datetime.min.replace(tzinfo=BJ_TZ),
            candidate.get("title", ""),
        ),
    )


def choose_latest_activity_candidate(*candidates: dict | None) -> dict | None:
    return choose_latest_daily_candidate(*candidates)


def push_initial_onboarding_candidates(
    user,
    *,
    sleep_candidate: dict | None = None,
    activity_candidate: dict | None = None,
) -> bool:
    pushed_any = False
    for candidate in (sleep_candidate, activity_candidate):
        if not candidate:
            continue
        pushed = push_candidate(user, candidate)
        if pushed:
            pushed_any = True
            clear_candidate_silent_flag(user, candidate)
    return pushed_any


def suppress_current_weekly_report(user, state):
    week_key = bj_now().strftime("%G-W%V")
    state["last_weekly_report_key"] = week_key
    save_state(user, state)
    log(f"首次接入后已跳过本周周报触发: {week_key}", user["name"])


def refresh_user_baseline(user, display_name, source: str):
    sleep_history = load_recent_normalized(user["name"], "sleep", limit=45, since_days=30)
    activity_history = load_recent_normalized(user["name"], "activity", limit=120, since_days=30)
    baseline_payload = build_user_baseline_payload(display_name, sleep_history, activity_history)
    if not baseline_payload:
        return None

    write_layered_package(
        user["name"],
        BASELINE_CATEGORY,
        BASELINE_RECORD_KEY,
        raw_data={
            "sleep_history_count": len(sleep_history),
            "activity_history_count": len(activity_history),
            "source": source,
        },
        normalized_data=baseline_payload,
        llm_payload=baseline_payload,
        metadata={
            "message_type": "user_30d_baseline",
            "recorded_at": bj_now().isoformat(),
            "window_days": 30,
            "source": source,
            "main_sport_type": baseline_payload["main_sport_positioning"].get("main_sport_type"),
            "current_profile": baseline_payload["main_sport_positioning"].get("current_profile"),
            "specialized_track": baseline_payload["main_sport_positioning"].get("specialized_track"),
        },
    )
    log(
        (
            "已刷新30天用户基线: "
            f"{baseline_payload['main_sport_positioning'].get('current_profile')}"
        ),
        user["name"],
    )
    return baseline_payload


def load_current_baseline(user_name: str) -> dict | None:
    payload = load_package(user_name, BASELINE_CATEGORY, BASELINE_RECORD_KEY)
    if not payload:
        return None
    return payload.get("llm_payload") or payload.get("normalized_data")


def _sleep_record_key(payload: dict) -> str | None:
    metadata = payload.get("metadata", {}) or {}
    record_key = metadata.get("record_key")
    if isinstance(record_key, str) and len(record_key) == 10:
        return record_key
    recorded_at = metadata.get("recorded_at")
    if isinstance(recorded_at, str) and len(recorded_at) >= 10:
        return recorded_at[:10]
    return None


def load_effective_sleep_history(user_name: str, limit: int) -> tuple[list[dict], list[str]]:
    packages = load_recent_packages(user_name, "sleep", limit=limit, since_days=120)
    sleep_history = []
    sleep_dates = []
    for payload in packages:
        normalized = payload.get("normalized_data")
        if normalized:
            sleep_history.append(normalized)
            record_key = _sleep_record_key(payload)
            if record_key:
                sleep_dates.append(record_key)
    return sleep_history, sleep_dates


def load_effective_activity_history(user_name: str, start_date: str | None = None, limit: int = 200) -> list[dict]:
    activity_history = load_recent_normalized(user_name, "activity", limit=limit, since_days=120)
    if not start_date:
        return activity_history
    return [
        record
        for record in activity_history
        if (record.get("basic_activity", {}).get("date") or "") >= start_date
    ]


def determine_cold_start_stage(state: dict) -> str:
    if state.get("initial_onboarding_status") == "skipped_existing_user" or state.get("initial_30d_summary_sent_at"):
        return "mature"
    effective_sleep_days = state.get("effective_sleep_days", 0) or 0
    if effective_sleep_days >= INITIAL_BACKFILL_DAYS:
        return "ready_30d"
    if effective_sleep_days >= MIN_INITIAL_BACKFILL_DAYS:
        return "ready_7d"
    if effective_sleep_days >= 3:
        return "stage_3d"
    return "observe"


def refresh_cold_start_state(user, state):
    if state.get("initial_onboarding_status") == "skipped_existing_user":
        state["cold_start_stage"] = "mature"
        save_state(user, state)
        return

    effective_sleep_days = count_packages(user["name"], "sleep")
    activity_history = load_recent_normalized(user["name"], "activity", limit=240, since_days=120)
    effective_activity_days = len(
        {
            record.get("basic_activity", {}).get("date")
            for record in activity_history
            if record.get("basic_activity", {}).get("date")
        }
    )
    snapshot_sleep_history, snapshot_sleep_dates = load_effective_sleep_history(user["name"], limit=7)
    snapshot_activity_history = load_effective_activity_history(
        user["name"],
        min(snapshot_sleep_dates) if snapshot_sleep_dates else None,
        limit=120,
    )

    state["effective_sleep_days"] = effective_sleep_days
    state["effective_activity_days"] = effective_activity_days
    state["cold_start_snapshot"] = build_cold_start_snapshot(snapshot_sleep_history, snapshot_activity_history)
    state["cold_start_stage"] = determine_cold_start_stage(state)
    save_state(user, state)


def build_cold_start_context(state: dict) -> dict:
    effective_sleep_days = state.get("effective_sleep_days", 0) or 0
    effective_activity_days = state.get("effective_activity_days", 0) or 0
    if effective_sleep_days < MIN_INITIAL_BACKFILL_DAYS:
        user_stage = "observation"
        stage_summary = "当前仍在观察期"
        comparison_scope = "先看这几天"
        stage_prompt_hint = "克制，不装懂，不做强基线结论"
    elif effective_sleep_days < INITIAL_BACKFILL_DAYS:
        user_stage = "early"
        stage_summary = "当前处于初步识别阶段"
        comparison_scope = "最近一周"
        stage_prompt_hint = "可以参考最近7天，但强调初步判断"
    else:
        user_stage = "mature"
        stage_summary = "当前已进入成熟基线阶段"
        comparison_scope = "最近30天"
        stage_prompt_hint = "可正式引用30天基线和主运动定位"
    return {
        "cold_start_stage": state.get("cold_start_stage"),
        "user_stage": user_stage,
        "stage_summary": stage_summary,
        "comparison_scope": comparison_scope,
        "stage_prompt_hint": stage_prompt_hint,
        "effective_sleep_days": effective_sleep_days,
        "effective_activity_days": effective_activity_days,
    }


def apply_stage_context(llm_payload: dict, stage_context: dict) -> dict:
    llm_payload["user_stage"] = stage_context["user_stage"]
    llm_payload["stage_summary"] = stage_context["stage_summary"]
    llm_payload["stage_context"] = stage_context
    user_stage = stage_context["user_stage"]
    if user_stage != "mature":
        llm_payload.pop("baseline_view", None)
        if isinstance(llm_payload.get("reason_points"), list):
            llm_payload["reason_points"] = [
                point
                for point in llm_payload["reason_points"]
                if "最近30天" not in point and "30天常态" not in point and "最近基线" not in point
            ][:4]
    if user_stage == "observation":
        if llm_payload.get("message_type") == "sleep_morning":
            llm_payload["trend_summary"] = "从目前这几天看，这晚先当作一个新的观察点。"
        elif llm_payload.get("message_type") == "activity_brief":
            llm_payload["trend_summary"] = "从目前这几次训练看，这次先主要看它本身的负荷和完成度。"
    elif user_stage == "early":
        if llm_payload.get("message_type") == "sleep_morning":
            trend_summary = llm_payload.get("trend_summary") or ""
            llm_payload["trend_summary"] = (
                trend_summary.replace("没有明显掉出常态", "没有明显偏离最近几天的范围")
                .replace("自己的常态区间", "最近几天的大致范围")
                .replace("放到最近一周看，", "最近一周看，")
            )
        elif llm_payload.get("message_type") == "activity_brief":
            trend_summary = llm_payload.get("trend_summary") or ""
            llm_payload["trend_summary"] = (
                trend_summary.replace("和你最近基线相比", "放进最近几次训练里看")
                .replace("熟悉区间内", "最近几次训练的范围里")
            )
    return llm_payload


def should_run_initial_onboarding(user, state):
    status = state.get("initial_onboarding_status")
    if status in {"completed", "skipped_existing_user", "backfill_completed"}:
        return False
    if status == "in_progress" and not state.get("initial_backfill_completed_at"):
        return True
    if state.get("initial_backfill_completed_at"):
        return False

    has_existing_state = bool(
        state.get("last_sleep_date") or state.get("last_activity_ids") or state.get("last_weekly_report_key")
    )
    has_existing_history = any(
        count_packages(user["name"], category) > 0
        for category in ("sleep", "activity", "weekly", INITIAL_SUMMARY_CATEGORY)
    )
    if has_existing_state or has_existing_history:
        state["initial_onboarding_status"] = "skipped_existing_user"
        state["cold_start_stage"] = "mature"
        save_state(user, state)
        return False
    return True


def backfill_sleep_window(client, user, display_name, start_date, end_date):
    user_name = user["name"]
    recent_sleep_history = load_recent_normalized(user_name, "sleep", limit=21, since_days=INITIAL_BACKFILL_DAYS + 14)
    stored_dates = []

    current_date = start_date
    while current_date <= end_date:
        date_str = current_date.strftime("%Y-%m-%d")
        if package_exists(user_name, "sleep", date_str):
            stored_dates.append(date_str)
            current_date += timedelta(days=1)
            continue

        try:
            sleep_data = client.get_sleep_data(date_str)
            sleep_dto = (sleep_data or {}).get("dailySleepDTO", {}) or {}
            if not sleep_dto or not sleep_dto.get("sleepTimeSeconds"):
                current_date += timedelta(days=1)
                continue
            if sleep_dto.get("sleepTimeSeconds", 0) < 1800:
                current_date += timedelta(days=1)
                continue

            stats = safe_call(lambda ds=date_str: client.get_stats(ds), {})
            hrv_data = safe_call(lambda ds=date_str: client.get_hrv_data(ds), {})
            body_battery = safe_call(lambda ds=date_str: client.get_body_battery(ds), [])
            raw_data = {
                "sleep_data": sleep_data,
                "daily_stats": stats,
                "hrv_data": hrv_data,
                "body_battery": body_battery,
            }
            normalized = normalize_sleep(date_str, sleep_data, stats, hrv_data, body_battery)
            llm_payload = build_sleep_payload(display_name, normalized, recent_sleep_history[:7])
            write_layered_package(
                user_name,
                "sleep",
                date_str,
                raw_data=raw_data,
                normalized_data=normalized,
                llm_payload=llm_payload,
                metadata=mark_silent_metadata(
                    {
                        "message_type": "sleep_morning",
                        "recorded_at": day_recorded_at(date_str),
                        "source": "initial_onboarding_backfill",
                    },
                    "initial_onboarding_backfill",
                ),
            )
            recent_sleep_history.insert(0, normalized)
            stored_dates.append(date_str)
        except Exception as exc:
            log(f"历史睡眠回填失败 {date_str}: {exc}", user_name)
            if is_auth_error(exc):
                raise
        current_date += timedelta(days=1)

    return stored_dates


def fetch_activities_in_window(client, start_date_str, end_date_str):
    activities = []
    seen_ids = set()
    offset = 0
    page_size = 20

    while True:
        batch = client.get_activities(offset, page_size)
        if not batch:
            break

        should_stop = False
        for activity in batch:
            activity_id = str(activity.get("activityId"))
            if not activity_id or activity_id in seen_ids:
                continue
            activity_date = beijing_date_from_activity(activity)
            if not activity_date:
                continue
            if activity_date < start_date_str:
                should_stop = True
                continue
            if activity_date > end_date_str:
                continue
            activities.append(activity)
            seen_ids.add(activity_id)

        if should_stop or len(batch) < page_size:
            break
        offset += len(batch)

    activities.sort(key=lambda item: (activity_recorded_at(item), str(item.get("activityId"))))
    return activities


def backfill_activity_window(client, user, display_name, start_date, end_date):
    user_name = user["name"]
    recent_activity_history = load_recent_normalized(user_name, "activity", limit=20, since_days=INITIAL_BACKFILL_DAYS + 30)
    start_date_str = start_date.strftime("%Y-%m-%d")
    end_date_str = end_date.strftime("%Y-%m-%d")
    badges = safe_call(lambda: client.get_earned_badges(), [])
    stored_ids = []

    for activity in fetch_activities_in_window(client, start_date_str, end_date_str):
        activity_id = str(activity.get("activityId"))
        if package_exists(user_name, "activity", activity_id):
            stored_ids.append(activity_id)
            continue

        try:
            detail = safe_call(lambda aid=activity_id: client.get_activity(aid), activity)
            splits_raw = safe_call(lambda aid=activity_id: client.get_activity_splits(aid), {})
            activity_date = beijing_date_from_activity(activity)

            normalized, payload_source = normalize_activity(
                activity=activity,
                detail=detail,
                splits_raw=splits_raw,
                badges=badges,
                activity_date=activity_date,
                recent_history=recent_activity_history,
            )
            llm_payload = build_activity_payload(normalized, display_name, recent_activity_history)
            raw_data = {
                "activity_summary": activity,
                "activity_detail": detail,
                "splits_raw": splits_raw,
                "recent_badges": badges[:10] if isinstance(badges, list) else badges,
            }
            write_layered_package(
                user_name,
                "activity",
                activity_id,
                raw_data=raw_data,
                normalized_data=normalized,
                llm_payload=llm_payload,
                metadata=mark_silent_metadata(
                    {
                        "message_type": "activity_brief",
                        "recorded_at": activity_recorded_at(detail if isinstance(detail, dict) else activity),
                        "activity_date": activity_date,
                        "activity_name": normalized["basic_activity"]["activity_name"],
                        "payload_source_keys": list(payload_source.keys()),
                        "source": "initial_onboarding_backfill",
                    },
                    "initial_onboarding_backfill",
                ),
            )
            recent_activity_history.insert(0, normalized)
            stored_ids.append(activity_id)
        except Exception as exc:
            log(f"历史运动回填失败 {activity_id}: {exc}", user_name)
            if is_auth_error(exc):
                raise

    return stored_ids


def run_initial_backfill(client, user, display_name, state):
    today = bj_now().date()
    end_date = today - timedelta(days=1)
    start_30d = today - timedelta(days=INITIAL_BACKFILL_DAYS)
    sleep_dates = backfill_sleep_window(client, user, display_name, start_30d, end_date)
    activity_ids = backfill_activity_window(client, user, display_name, start_30d, end_date)

    sleep_count_30d = count_packages(user["name"], "sleep")
    if sleep_count_30d < MIN_INITIAL_BACKFILL_DAYS:
        log("30天睡眠历史不足，补拉最近7天兜底", user["name"])
        start_7d = today - timedelta(days=MIN_INITIAL_BACKFILL_DAYS)
        sleep_dates = sorted(set(sleep_dates + backfill_sleep_window(client, user, display_name, start_7d, end_date)))
        activity_ids = list(
            dict.fromkeys(activity_ids + backfill_activity_window(client, user, display_name, start_7d, end_date))
        )
        sleep_count_30d = count_packages(user["name"], "sleep")

    state["initial_backfill_completed_at"] = bj_now().isoformat()
    state["initial_backfill_sleep_days"] = sleep_count_30d
    state["initial_backfill_activity_count"] = len(activity_ids)
    save_state(user, state)


def load_json_file(path, default=None):
    if default is None:
        default = {}
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def runtime_user_id(user) -> str:
    return resolve_user_id(user.get("name")) or user.get("name")


def profile_file_for_user_id(user_id: str) -> Path:
    return ROOT_DIR / "data" / user_id / "profile.json"


def baselines_file_for_user_id(user_id: str) -> Path:
    return ROOT_DIR / "data" / user_id / "baselines.json"


def needs_onboarding_artifacts_completion(user, state) -> bool:
    if state.get("initial_onboarding_status") == "skipped_existing_user":
        return False
    user_id = runtime_user_id(user)
    profile = load_json_file(profile_file_for_user_id(user_id), {})
    baselines = load_json_file(baselines_file_for_user_id(user_id), {})
    available_data = profile.get("available_data") if isinstance(profile, dict) else None
    if not isinstance(available_data, dict) or not available_data:
        return True
    if not state.get("initial_daily_backfill_completed_at"):
        return True
    if (state.get("initial_daily_backfill_days") or 0) < MIN_INITIAL_BACKFILL_DAYS:
        return True
    if (baselines.get("days_of_data") or 0) < MIN_INITIAL_BACKFILL_DAYS:
        return True
    return False


def finalize_onboarding_artifacts(client, user, display_name, state):
    import baseline as baseline_module
    import daily_snapshot
    import probe_data

    user_id = runtime_user_id(user)
    user_dir = ROOT_DIR / "data" / user_id
    daily_dir = user_dir / "daily"
    user_dir.mkdir(parents=True, exist_ok=True)
    daily_dir.mkdir(parents=True, exist_ok=True)

    profile_path = profile_file_for_user_id(user_id)
    existing_profile = load_json_file(profile_path, {})
    available_data = existing_profile.get("available_data") if isinstance(existing_profile, dict) else None
    if not isinstance(available_data, dict) or not available_data:
        probe_user = dict(user)
        probe_user["_probe_user_id"] = user_id
        probe_user["_probe_display_name"] = display_name or user.get("name")
        today = bj_now().date()
        outcome = probe_data.probe_user(
            probe_user,
            probe_date=today.isoformat(),
            request_date=(today - timedelta(days=1)).isoformat(),
            client=client,
        )
        profile_payload = outcome["profile"]
        if isinstance(existing_profile, dict) and existing_profile.get("known_activity_issues"):
            profile_payload["known_activity_issues"] = existing_profile["known_activity_issues"]
        probe_data.write_profile(probe_user, profile_payload)
        log("已初始化新用户 profile 探测", user["name"])

    today = bj_now().date()
    completed_days = 0
    for day in range(INITIAL_BACKFILL_DAYS, 0, -1):
        date_str = (today - timedelta(days=day)).isoformat()
        snapshot_path = daily_snapshot.get_daily_path(user_id, date_str)
        if not snapshot_path.exists():
            daily_snapshot.save_daily_snapshot(client, user_id, date_str)
        if snapshot_path.exists():
            completed_days += 1

    baselines = baseline_module.compute_baselines(user_id)
    state["initial_daily_backfill_completed_at"] = bj_now().isoformat()
    state["initial_daily_backfill_days"] = completed_days
    state["initial_baselines_days_of_data"] = baselines.get("days_of_data", 0) or 0
    save_state(user, state)
    return baselines


def generate_initial_7d_summary(user, display_name, state):
    sleep_history, sleep_dates = load_effective_sleep_history(user["name"], limit=7)
    activity_history = load_effective_activity_history(
        user["name"],
        min(sleep_dates) if sleep_dates else None,
        limit=120,
    )
    llm_payload = build_initial_7d_summary_payload(display_name, sleep_history, activity_history)
    if not llm_payload:
        return False

    analysis = analyze_with_llm(llm_payload, mode="initial_7d")
    write_layered_package(
        user["name"],
        INITIAL_SUMMARY_CATEGORY,
        INITIAL_7D_SUMMARY_RECORD_KEY,
        raw_data={
            "sleep_history_count": len(sleep_history),
            "activity_history_count": len(activity_history),
            "source": "cold_start_7d",
        },
        normalized_data={
            "sleep_history_count": len(sleep_history),
            "activity_history_count": len(activity_history),
            "last_7d_sleep_history": sleep_history,
            "last_7d_activity_history": activity_history,
        },
        llm_payload=llm_payload,
        message_preview=analysis,
        metadata={
            "message_type": "initial_7d_summary",
            "recorded_at": bj_now().isoformat(),
            "window_days": 7,
        },
    )
    pushed = push_to_wechat(
        user,
        INITIAL_7D_SUMMARY_TITLE,
        analysis,
        activity_type=INITIAL_7D_SUMMARY_TITLE,
        distance_duration=f"{len(activity_history)} 次运动",
    )
    if not pushed:
        return False

    state["initial_7d_summary_sent_at"] = bj_now().isoformat()
    state["initial_7d_summary_record_key"] = INITIAL_7D_SUMMARY_RECORD_KEY
    save_state(user, state)
    log("已发送7天初步分析", user["name"])
    return True


def generate_initial_30d_summary(user, display_name, state):
    sleep_history, sleep_dates = load_effective_sleep_history(user["name"], limit=INITIAL_BACKFILL_DAYS)
    activity_history = load_effective_activity_history(
        user["name"],
        min(sleep_dates) if sleep_dates else None,
        limit=200,
    )
    llm_payload = build_initial_summary_payload(display_name, sleep_history, activity_history)
    if not llm_payload:
        return False

    analysis = analyze_with_llm(llm_payload, mode="initial_30d")
    write_layered_package(
        user["name"],
        INITIAL_SUMMARY_CATEGORY,
        INITIAL_SUMMARY_RECORD_KEY,
        raw_data={
            "sleep_history_count": len(sleep_history),
            "activity_history_count": len(activity_history),
            "source": "cold_start_30d",
        },
        normalized_data={
            "sleep_history_count": len(sleep_history),
            "activity_history_count": len(activity_history),
            "last_30d_sleep_history": sleep_history,
            "last_30d_activity_history": activity_history,
        },
        llm_payload=llm_payload,
        message_preview=analysis,
        metadata={
            "message_type": "initial_30d_summary",
            "recorded_at": bj_now().isoformat(),
            "window_days": INITIAL_BACKFILL_DAYS,
        },
    )
    pushed = push_to_wechat(
        user,
        INITIAL_SUMMARY_TITLE,
        analysis,
        activity_type=INITIAL_SUMMARY_TITLE,
        distance_duration=f"{len(activity_history)} 次运动",
    )
    if not pushed:
        return False

    state["initial_30d_summary_sent_at"] = bj_now().isoformat()
    state["initial_30d_summary_record_key"] = INITIAL_SUMMARY_RECORD_KEY
    state["initial_onboarding_status"] = "completed"
    state["cold_start_stage"] = "mature"
    save_state(user, state)
    log("已发送30天状态总结", user["name"])
    return True


def check_cold_start_milestones(user, display_name, state):
    if state.get("initial_onboarding_status") == "skipped_existing_user" or state.get("initial_30d_summary_sent_at"):
        return False
    effective_sleep_days = state.get("effective_sleep_days", 0) or 0
    if effective_sleep_days >= INITIAL_BACKFILL_DAYS and not state.get("initial_30d_summary_sent_at"):
        return generate_initial_30d_summary(user, display_name, state)
    if effective_sleep_days >= MIN_INITIAL_BACKFILL_DAYS and not state.get("initial_7d_summary_sent_at"):
        return generate_initial_7d_summary(user, display_name, state)
    return False


def ensure_initial_onboarding(client, user, display_name, state):
    if not should_run_initial_onboarding(user, state):
        return False

    state["initial_onboarding_status"] = "in_progress"
    save_state(user, state)

    if not state.get("initial_backfill_completed_at"):
        run_initial_backfill(client, user, display_name, state)
        log("已完成新用户历史回填", user["name"])
    if needs_onboarding_artifacts_completion(user, state):
        finalize_onboarding_artifacts(client, user, display_name, state)
        log("已完成新用户 daily/baseline 初始化", user["name"])
    state["initial_onboarding_status"] = "backfill_completed"
    save_state(user, state)

    return True


def check_sleep(
    client,
    user,
    display_name,
    state,
    user_baseline=None,
    cold_start_context=None,
    *,
    push: bool = True,
    no_push_reason: str | None = None,
):
    today = bj_now().strftime("%Y-%m-%d")
    if state.get("last_sleep_date") == today:
        existing_package = load_package(user["name"], "sleep", today)
        existing_metadata = (existing_package or {}).get("metadata") or {}
        inferred_success = bool(existing_package) and not existing_metadata.get("silent") and not existing_metadata.get("no_push")
        update_sleep_health_state(
            state,
            date_str=today,
            check_result=(
                "already_success"
                if (
                    (state.get("last_sleep_push_date") == today and state.get("last_sleep_push_result") == "success")
                    or inferred_success
                )
                else "already_processed"
            ),
            check_reason="today_already_processed",
            push_result="success" if inferred_success else None,
            push_reason=None if inferred_success else None,
            push_title=f"佳明睡眠晨报（{month_day_label(today)}）" if inferred_success else None,
        )
        save_state(user, state)
        return {"updated": False, "pushed": False, "candidate": None}

    try:
        cycle_started_at = time.perf_counter()
        sleep_fetch_started_at = time.perf_counter()
        sleep_data = client.get_sleep_data(today)
        sleep_fetch_sec = time.perf_counter() - sleep_fetch_started_at
        sleep_dto = (sleep_data or {}).get("dailySleepDTO", {}) or {}
        if not sleep_dto or not sleep_dto.get("sleepTimeSeconds"):
            log(f"睡眠数据暂无（dailySleepDTO 为空），跳过 | Garmin抓取 {sleep_fetch_sec:.2f}s", user["name"])
            update_sleep_health_state(
                state,
                date_str=today,
                check_result="no_data",
                check_reason="garmin_daily_sleep_dto_empty",
            )
            save_state(user, state)
            return {"updated": False, "pushed": False, "candidate": None}
        if sleep_dto.get("sleepTimeSeconds", 0) < 1800:
            log(f"睡眠时长 {sleep_dto.get('sleepTimeSeconds')}s 不足 30 分钟，跳过 | Garmin抓取 {sleep_fetch_sec:.2f}s", user["name"])
            update_sleep_health_state(
                state,
                date_str=today,
                check_result="insufficient_sleep",
                check_reason="sleep_seconds_below_1800",
            )
            save_state(user, state)
            return {"updated": False, "pushed": False, "candidate": None}

        garmin_aux_started_at = time.perf_counter()
        stats = safe_call(lambda: client.get_stats(today), {})
        hrv_data = safe_call(lambda: client.get_hrv_data(today), {})
        body_battery = safe_call(lambda: client.get_body_battery(today), [])
        garmin_fetch_sec = sleep_fetch_sec + (time.perf_counter() - garmin_aux_started_at)

        raw_data = {
            "sleep_data": sleep_data,
            "daily_stats": stats,
            "hrv_data": hrv_data,
            "body_battery": body_battery,
        }

        recent_sleep_history = load_recent_normalized(user["name"], "sleep", limit=7, since_days=21)
        recent_sleep_packages = load_recent_packages(user["name"], "sleep", limit=3, since_days=21)
        normalized = normalize_sleep(today, sleep_data, stats, hrv_data, body_battery)
        legacy_llm_payload = build_sleep_payload(
            display_name,
            normalized,
            recent_sleep_history,
            user_baseline=user_baseline,
            recent_message_packages=recent_sleep_packages,
        )
        if cold_start_context:
            legacy_llm_payload = apply_stage_context(legacy_llm_payload, cold_start_context)
        llm_started_at = time.perf_counter()
        analysis_result = None
        try:
            morning_flow = generate_new_morning_report(client, user["name"], display_name, today)
            analysis = morning_flow["message"]
            llm_payload = morning_flow["llm_payload"]
            analysis_result = morning_flow.get("analysis_result")
        except Exception as exc:
            log(f"晨报新流程失败，回退旧流程: {exc}", user["name"])
            analysis = analyze_with_llm(legacy_llm_payload, mode="sleep")
            llm_payload = legacy_llm_payload
        llm_sec = time.perf_counter() - llm_started_at
        if analysis_result:
            raw_data["analysis_result"] = analysis_result
            log(
                f"晨报新流程生效: {llm_payload.get('message_type', 'unknown')} | analysis_date={analysis_result.get('date')}",
                user["name"],
            )

        total_min = normalized["basic_sleep"]["total_sleep_min"]
        distance_duration = f"{total_min // 60}小时{total_min % 60}分钟"
        recorded_at = day_recorded_at(today)
        chart_image_url = maybe_build_morning_chart(
            resolve_user_id(user["name"]) or user["name"],
            today,
            normalized,
            recent_sleep_history,
            llm_payload=llm_payload if isinstance(llm_payload, dict) else None,
            raw_sleep_data=sleep_data,
        )
        candidate = build_push_candidate(
            title=f"佳明睡眠晨报（{month_day_label(today)}）",
            content=analysis,
            activity_type="睡眠晨报",
            distance_duration=distance_duration,
            recorded_at=recorded_at,
            chart_image_url=chart_image_url,
            category="sleep",
            record_key=today,
        )
        metadata = build_sleep_package_metadata(recorded_at, llm_payload)
        if not push:
            metadata = mark_silent_metadata(metadata, no_push_reason or "suppressed_sleep_push")
        write_layered_package(
            user["name"],
            "sleep",
            today,
            raw_data=raw_data,
            normalized_data=normalized,
            llm_payload=llm_payload,
            message_preview=analysis,
            metadata=metadata,
        )

        log(
            f"检测到新睡眠数据: {total_min // 60}小时{total_min % 60}分钟，评分 {normalized['basic_sleep'].get('sleep_score')}",
            user["name"],
        )
        push_sec = 0.0
        pushed = False
        if push:
            push_started_at = time.perf_counter()
            pushed = push_candidate(user, candidate)
            push_sec = time.perf_counter() - push_started_at
        total_elapsed_sec = time.perf_counter() - cycle_started_at
        log(
            f"晨报链路耗时: Garmin抓取 {garmin_fetch_sec:.2f}s | LLM {llm_sec:.2f}s | PushPlus {push_sec:.2f}s | 总计 {total_elapsed_sec:.2f}s",
            user["name"],
        )
        if not push:
            update_sleep_health_state(
                state,
                date_str=today,
                check_result="suppressed",
                check_reason=no_push_reason or "suppressed_sleep_push",
                push_result="suppressed",
                push_reason=no_push_reason or "suppressed_sleep_push",
                push_title=candidate["title"],
            )
            state["last_sleep_date"] = today
        elif pushed:
            update_sleep_health_state(
                state,
                date_str=today,
                check_result="success",
                check_reason="sleep_push_success",
                push_result="success",
                push_reason=None,
                push_title=candidate["title"],
            )
            state["last_sleep_date"] = today
        else:
            update_sleep_health_state(
                state,
                date_str=today,
                check_result="push_failed",
                check_reason="push_to_wechat_failed",
                push_result="failed",
                push_reason="push_to_wechat_failed",
                push_title=candidate["title"],
            )
        save_state(user, state)
        return {
            "updated": True,
            "pushed": pushed,
            "candidate": candidate,
        }
    except Exception as exc:
        log(f"睡眠数据检查异常: {exc}", user["name"])
        update_sleep_health_state(
            state,
            date_str=today,
            check_result="error",
            check_reason=str(exc),
        )
        save_state(user, state)
        if is_auth_error(exc):
            raise
    return {"updated": False, "pushed": False, "candidate": None}


def check_activities(
    client,
    user,
    display_name,
    state,
    user_baseline=None,
    cold_start_context=None,
    *,
    push_limit: int | None = None,
    no_push_reason: str | None = None,
):
    try:
        activities = client.get_activities(0, 5)
        known_ids = set(state.get("last_activity_ids", []))
        recent_activity_history = load_recent_normalized(user["name"], "activity", limit=12, since_days=45)
        recent_activity_packages = load_recent_packages(user["name"], "activity", limit=4, since_days=45)
        has_new_activity = False
        unseen_activity_ids = [
            str(activity.get("activityId"))
            for activity in activities
            if str(activity.get("activityId")) not in known_ids
        ]
        if push_limit is None:
            push_activity_ids = set(unseen_activity_ids)
        else:
            push_activity_ids = set(unseen_activity_ids[: max(push_limit, 0)])
        latest_candidate = None
        pushed_count = 0

        for activity in activities:
            activity_id = str(activity.get("activityId"))
            if activity_id in known_ids:
                continue
            should_push = activity_id in push_activity_ids

            detail = safe_call(lambda aid=activity_id: client.get_activity(aid), activity)
            splits_raw = safe_call(lambda aid=activity_id: client.get_activity_splits(aid), {})
            badges = safe_call(lambda: client.get_earned_badges(), [])
            activity_time_source = detail if isinstance(detail, dict) else activity
            activity_date = beijing_date_from_activity(activity_time_source) or beijing_date_from_activity(activity)

            normalized, payload_source = normalize_activity(
                activity=activity,
                detail=detail,
                splits_raw=splits_raw,
                badges=badges,
                activity_date=activity_date,
                recent_history=recent_activity_history,
            )
            llm_payload = build_activity_payload(
                normalized,
                display_name,
                recent_activity_history,
                user_baseline=user_baseline,
                recent_message_packages=recent_activity_packages,
            )
            if cold_start_context:
                llm_payload = apply_stage_context(llm_payload, cold_start_context)
            llm_payload = enrich_activity_payload(
                llm_payload,
                user["name"],
                activity_date,
                activity_summary=activity if isinstance(activity, dict) else None,
                activity_id=activity_id,
            )
            analysis = analyze_with_llm(llm_payload, mode="activity")
            update_known_activity_issues({"丛至": "congzhi", "杨": "yang", "Kevin": "kevin"}.get(user["name"], user["name"]), llm_payload)

            raw_data = {
                "activity_summary": activity,
                "activity_detail": detail,
                "splits_raw": splits_raw,
                "recent_badges": badges[:10] if isinstance(badges, list) else badges,
            }
            recorded_at = activity_recorded_at(activity_time_source)
            distance_duration = (
                f"{normalized['basic_activity']['distance_km']}km | "
                f"{normalized['basic_activity']['duration_min']}min"
            )
            chart_image_url = maybe_build_activity_chart(
                resolve_user_id(user["name"]) or user["name"],
                activity_date,
                normalized,
                recent_activity_history,
                llm_payload=llm_payload if isinstance(llm_payload, dict) else None,
            )
            candidate = build_push_candidate(
                title=f"佳明运动快报（{month_day_label(activity_date)}）- {normalized['basic_activity']['activity_name']}",
                content=analysis,
                activity_type=normalized["basic_activity"]["activity_name"],
                distance_duration=distance_duration,
                recorded_at=recorded_at,
                chart_image_url=chart_image_url,
                category="activity",
                record_key=activity_id,
            )
            latest_candidate = choose_latest_activity_candidate(latest_candidate, candidate)
            if should_push and not should_push_activity_candidate(candidate):
                should_push = False
            metadata = {
                "message_type": "activity_brief",
                "recorded_at": recorded_at,
                "activity_date": activity_date,
                "activity_name": normalized["basic_activity"]["activity_name"],
                "payload_source_keys": list(payload_source.keys()),
            }
            if not should_push:
                metadata = mark_silent_metadata(metadata, no_push_reason or "suppressed_activity_push")
            write_layered_package(
                user["name"],
                "activity",
                activity_id,
                raw_data=raw_data,
                normalized_data=normalized,
                llm_payload=llm_payload,
                message_preview=analysis,
                metadata=metadata,
            )

            log(
                f"检测到新运动: {normalized['basic_activity']['activity_name']} | {distance_duration} | {activity_date}",
                user["name"],
            )
            if should_push:
                push_candidate(user, candidate)
                pushed_count += 1
            known_ids.add(activity_id)
            recent_activity_history.insert(0, normalized)
            recent_activity_packages.insert(
                0,
                {
                    "normalized_data": normalized,
                    "llm_payload": llm_payload,
                    "message_preview": analysis,
                    "metadata": metadata,
                },
            )
            has_new_activity = True

        state["last_activity_ids"] = list(known_ids)[-20:]
        save_state(user, state)
        return {
            "updated": has_new_activity,
            "pushed_count": pushed_count,
            "latest_candidate": latest_candidate,
        }
    except Exception as exc:
        log(f"运动数据检查异常: {exc}", user["name"])
        if is_auth_error(exc):
            raise
    return {
        "updated": False,
        "pushed_count": 0,
        "latest_candidate": None,
    }


def check_weekly_report(user, display_name, state, cold_start_context=None):
    now = bj_now()
    week_key = now.strftime("%G-W%V")
    weekly_title = "佳明过去7天固定总结"
    user_id = {"丛至": "congzhi", "杨": "yang", "Kevin": "kevin"}.get(user["name"], user["name"])
    if state.get("last_weekly_report_key") == week_key:
        return
    if now.weekday() != WEEKLY_REPORT_WEEKDAY or now.hour < WEEKLY_REPORT_HOUR:
        return

    end_date = (now - timedelta(days=1)).date()
    analysis_text, payload = generate_weekly_report(user_id, end_date)
    chart_image_url = maybe_build_report_chart(
        resolve_user_id(user["name"]) or user["name"],
        "weekly",
        week_key,
        payload.get("chart_history"),
    )
    payload["chart_image_url"] = chart_image_url
    write_layered_package(
        user["name"],
        "weekly",
        week_key,
        raw_data={
            "source": "task6_weekly_report_v2",
            "end_date": end_date.isoformat(),
        },
        normalized_data=payload,
        llm_payload=payload,
        message_preview=analysis_text,
        metadata={
            "message_type": "weekly_report_v2",
            "recorded_at": now.isoformat(),
            "window_label": "past_7d_fixed_summary_v2",
            "window_days": 7,
        },
    )
    push_to_wechat(
        user,
        weekly_title,
        analysis_text,
        activity_type=weekly_title,
        distance_duration=f"{len(payload.get('days', []))} 天数据",
        chart_image_url=chart_image_url,
    )
    state["last_weekly_report_key"] = week_key
    save_state(user, state)
    log("推送过去7天固定总结", user["name"])


def check_monthly_report(user, display_name, state):
    now = bj_now()
    user_id = {"丛至": "congzhi", "杨": "yang", "Kevin": "kevin"}.get(user["name"], user["name"])
    if now.day != MONTHLY_REPORT_DAY or now.hour < MONTHLY_REPORT_HOUR:
        return

    target_year = now.year
    target_month = now.month - 1
    if target_month == 0:
        target_year -= 1
        target_month = 12
    month_key = f"{target_year:04d}-{target_month:02d}"
    if state.get("last_monthly_report_key") == month_key:
        return

    analysis_text, payload = generate_monthly_report(user_id, target_year, target_month)
    monthly_title = f"佳明月度健康报告（{target_year}年{target_month}月）"
    chart_image_url = maybe_build_report_chart(
        resolve_user_id(user["name"]) or user["name"],
        "monthly",
        month_key,
        payload.get("chart_history"),
    )
    payload["chart_image_url"] = chart_image_url
    write_layered_package(
        user["name"],
        "monthly",
        month_key,
        raw_data={
            "source": "task6_monthly_report_v2",
            "year": target_year,
            "month": target_month,
        },
        normalized_data=payload,
        llm_payload=payload,
        message_preview=analysis_text,
        metadata={
            "message_type": "monthly_report_v2",
            "recorded_at": now.isoformat(),
            "window_label": "previous_calendar_month",
        },
    )
    push_to_wechat(
        user,
        monthly_title,
        analysis_text,
        activity_type=monthly_title,
        distance_duration=f"{payload.get('days_of_data', 0)} 天数据",
        chart_image_url=chart_image_url,
    )
    state["last_monthly_report_key"] = month_key
    save_state(user, state)
    log(f"推送月报 {month_key}", user["name"])


def run_user_cycle(client, user, display_name, state):
    initial_onboarding_ran = ensure_initial_onboarding(client, user, display_name, state)
    if not initial_onboarding_ran and needs_onboarding_artifacts_completion(user, state):
        finalize_onboarding_artifacts(client, user, display_name, state)
        log("检测到新用户收尾未完成，已自动补齐 daily/baseline", user["name"])
    refresh_cold_start_state(user, state)

    if not package_exists(user["name"], BASELINE_CATEGORY, BASELINE_RECORD_KEY):
        refresh_user_baseline(user, display_name, source="baseline_bootstrap")
    current_baseline = load_current_baseline(user["name"])
    cold_start_context = build_cold_start_context(state)

    if initial_onboarding_ran:
        sleep_result = check_sleep(
            client,
            user,
            display_name,
            state,
            user_baseline=current_baseline,
            cold_start_context=cold_start_context,
            push=False,
            no_push_reason="initial_onboarding_daily_limit",
        )
        activity_result = check_activities(
            client,
            user,
            display_name,
            state,
            user_baseline=current_baseline,
            cold_start_context=cold_start_context,
            push_limit=0,
            no_push_reason="initial_onboarding_daily_limit",
        )
    else:
        sleep_result = check_sleep(
            client,
            user,
            display_name,
            state,
            user_baseline=current_baseline,
            cold_start_context=cold_start_context,
        )
        activity_result = check_activities(
            client,
            user,
            display_name,
            state,
            user_baseline=current_baseline,
            cold_start_context=cold_start_context,
        )

    if sleep_result["updated"] or activity_result["updated"] or not package_exists(
        user["name"], BASELINE_CATEGORY, BASELINE_RECORD_KEY
    ):
        refresh_user_baseline(
            user,
            display_name,
            source="initial_onboarding_refresh" if initial_onboarding_ran else "daily_refresh",
        )

    refresh_cold_start_state(user, state)
    summary_sent = check_cold_start_milestones(user, display_name, state)
    daily_candidate_pushed = False

    if initial_onboarding_ran:
        suppress_current_weekly_report(user, state)
        latest_sleep_candidate = sleep_result.get("candidate")
        latest_activity_candidate = activity_result.get("latest_candidate")
        latest_daily_candidate = choose_latest_daily_candidate(
            latest_sleep_candidate,
            latest_activity_candidate,
        )
        daily_candidate_pushed = push_initial_onboarding_candidates(
            user,
            sleep_candidate=latest_sleep_candidate,
            activity_candidate=latest_activity_candidate,
        )
    else:
        check_weekly_report(user, display_name, state, cold_start_context=build_cold_start_context(state))
        check_monthly_report(user, display_name, state)

    evening_result = check_and_send_evening_push(client, user, display_name, state)
    icu_cycling_pushed = False
    icu_sleep_pushed = False
    if user.get("name") == "丛至":
        try:
            from icu_cycling import check_and_push_cycling
            from icu_sleep import check_and_push_sleep

            icu_cycling_pushed = bool(
                check_and_push_cycling(
                    user=user,
                    log_func=lambda message: log(message, user["name"]),
                )
            )
            icu_sleep_pushed = bool(
                check_and_push_sleep(
                    user=user,
                    log_func=lambda message: log(message, user["name"]),
                )
            )
        except Exception as exc:
            log(f"ICU 模块执行失败（不影响主流程）: {exc}", user["name"])

    return {
        "initial_onboarding_ran": initial_onboarding_ran,
        "sleep_updated": sleep_result["updated"],
        "activity_updated": activity_result["updated"],
        "summary_sent": bool(summary_sent),
        "daily_candidate_pushed": daily_candidate_pushed,
        "latest_daily_candidate": choose_latest_daily_candidate(
            sleep_result.get("candidate"),
            activity_result.get("latest_candidate"),
        ),
        "evening_pushed": evening_result.get("pushed", False),
        "evening_trigger_type": evening_result.get("trigger_type"),
        "icu_cycling_pushed": icu_cycling_pushed,
        "icu_sleep_pushed": icu_sleep_pushed,
    }


def process_user(user, clients, display_names, fail_counts):
    name = user["name"]

    state = load_state(user)
    try:
        if clients.get(name) is None:
            try:
                clients[name] = login_garmin(user)
            except Exception as login_exc:
                message = str(login_exc)
                if "限流退避中" in message:
                    log(sanitize_text(message), name)
                    return
                log(f"佳明连接失败: {login_exc}", name)
                if is_rate_limit_error(login_exc):
                    backoff_until = record_rate_limit_backoff(name)
                    resume = datetime.fromtimestamp(backoff_until, BJ_TZ).strftime("%H:%M")
                    log(f"触发 429 限流，将在 {resume} 后重试", name)
                feishu_write_run_log(
                    name,
                    "推送失败",
                    f"Garmin 登录失败，当前周期未能继续：{sanitize_text(str(login_exc))}",
                    user_stage=resolve_user_stage(user=user),
                    error_code=normalize_error_code(str(login_exc)),
                    user=user,
                )
                raise
            fail_counts[name] = 0
            clear_rate_limit_backoff(name)
            display_names[name] = get_display_name(clients[name], name)
            log(f"佳明显示名: {display_names[name]}", name)

        client = clients[name]
        display_name = display_names.get(name, name)
        run_user_cycle(client, user, display_name, state)
        state = load_state(user)
        state["last_successful_cycle_at"] = bj_now().isoformat()
        state["last_cycle_error"] = None
        save_state(user, state)
        update_health_runtime(
            user_updates={
                name: {
                    "last_successful_cycle_at": state["last_successful_cycle_at"],
                    "last_cycle_error": None,
                    "last_sleep_check_date": state.get("last_sleep_check_date"),
                    "last_sleep_check_result": state.get("last_sleep_check_result"),
                    "last_sleep_check_reason": state.get("last_sleep_check_reason"),
                    "last_sleep_push_date": state.get("last_sleep_push_date"),
                    "last_sleep_push_result": state.get("last_sleep_push_result"),
                    "last_sleep_push_reason": state.get("last_sleep_push_reason"),
                    "last_sleep_push_title": state.get("last_sleep_push_title"),
                    "last_evening_push_date": state.get("last_evening_push_date"),
                    "last_evening_push_type": state.get("last_evening_push_type"),
                }
            }
        )
    except Exception as exc:
        log(f"本轮检查异常: {exc}", name)
        state["last_cycle_error"] = str(exc)
        save_state(user, state)
        update_health_runtime(
            user_updates={
                name: {
                    "last_cycle_error": str(exc),
                    "last_sleep_check_date": state.get("last_sleep_check_date"),
                    "last_sleep_check_result": state.get("last_sleep_check_result"),
                    "last_sleep_check_reason": state.get("last_sleep_check_reason"),
                    "last_sleep_push_date": state.get("last_sleep_push_date"),
                    "last_sleep_push_result": state.get("last_sleep_push_result"),
                    "last_sleep_push_reason": state.get("last_sleep_push_reason"),
                    "last_sleep_push_title": state.get("last_sleep_push_title"),
                    "last_evening_push_date": state.get("last_evening_push_date"),
                    "last_evening_push_type": state.get("last_evening_push_type"),
                }
            }
        )
        if is_auth_error(exc):
            token_dir = get_token_dir(user)
            try:
                import shutil

                shutil.rmtree(token_dir, ignore_errors=True)
                os.makedirs(token_dir, exist_ok=True)
                log("已清除失效 token，下次将重新登录", name)
            except Exception:
                pass
        clients[name] = None
        fail_counts[name] = fail_counts.get(name, 0) + 1
        if fail_counts[name] > 3:
            log("连续失败 3 次，下轮跳过本用户（等待重试）", name)
            fail_counts[name] = 0


def main_loop():
    users = load_enabled_users()
    update_health_runtime(
        root_updates={
            "process": {
                "pid": os.getpid(),
                "started_at": PROCESS_STARTED_AT,
                "alive": True,
            },
            "enabled_users": [user["name"] for user in users],
            "loaded_users": [user["name"] for user in users],
            "cycle_started_at": None,
            "cycle_finished_at": None,
        }
    )
    log("佳明健康监控已启动（第一阶段消息模式）")
    log(f"监控用户: {', '.join(user['name'] for user in users)}")
    log("已启用分时段轮询：05:00-09:00 每5分钟，其他时间每20分钟")
    log_current_check_strategy()
    log("Token 缓存目录已配置")
    log("")

    clients = {user["name"]: None for user in users}
    display_names = {user["name"]: user["name"] for user in users}
    fail_counts = {user["name"]: 0 for user in users}
    last_user_names = [user["name"] for user in users]

    while True:
        updated_files = should_reload_for_code_update()
        if updated_files:
            log(f"检测到代码更新，退出当前进程等待下一轮启动: {', '.join(updated_files)}")
            cleanup(None, None)
        users = load_enabled_users()
        current_user_names = [user["name"] for user in users]
        sync_runtime_users(users, clients, display_names, fail_counts)
        update_health_runtime(
            root_updates={
                "process": {
                    "pid": os.getpid(),
                    "started_at": PROCESS_STARTED_AT,
                    "alive": True,
                },
                "enabled_users": current_user_names,
                "loaded_users": current_user_names,
                "cycle_started_at": bj_now().isoformat(),
            }
        )
        if current_user_names != last_user_names:
            log(f"监控用户更新: {', '.join(current_user_names)}")
            last_user_names = current_user_names
        for user in users:
            process_user(user, clients, display_names, fail_counts)
        update_health_runtime(
            root_updates={
                "process": {
                    "pid": os.getpid(),
                    "started_at": PROCESS_STARTED_AT,
                    "alive": True,
                },
                "enabled_users": current_user_names,
                "loaded_users": current_user_names,
                "cycle_finished_at": bj_now().isoformat(),
            }
        )
        interval_seconds, _ = log_current_check_strategy()
        log(f"所有用户处理完毕，下次检查在 {interval_seconds // 60} 分钟后...")
        time.sleep(interval_seconds)


def cleanup(signum, frame):
    update_health_runtime(
        root_updates={
            "process": {
                "pid": os.getpid(),
                "started_at": PROCESS_STARTED_AT,
                "alive": False,
                "stopped_at": bj_now().isoformat(),
            }
        }
    )
    log("\n正在退出...")
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)
    sys.exit(0)


if __name__ == "__main__":
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, "r", encoding="utf-8") as file:
                old_pid = file.read().strip()
            if old_pid and os.path.exists(f"/proc/{old_pid}"):
                print(
                    f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 已有进程 PID={old_pid} 在运行，本次启动退出。",
                    flush=True,
                )
                sys.exit(0)
        except Exception:
            pass

    os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)
    with open(PID_FILE, "w", encoding="utf-8") as file:
        file.write(str(os.getpid()))

    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

    try:
        main_loop()
    except KeyboardInterrupt:
        cleanup(None, None)
