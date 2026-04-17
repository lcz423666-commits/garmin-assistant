#!/usr/bin/env python3
"""Collect Garmin assistant review samples using the current DeepSeek V3.2 pipeline."""

from __future__ import annotations

import json
import re
import sys
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = PROJECT_ROOT / "app"
if APP_DIR.exists() and str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import garmin_monitor as gm
from app_config import REVIEW_SAMPLES_DIR, sanitize_export_value, sanitize_text as shared_sanitize_text
from activity_cleaner import normalize_activity
from llm_helper import analyze_with_llm
from phase1_builder import build_activity_payload, build_sleep_payload, build_weekly_payload
from sleep_cleaner import normalize_sleep


OUTPUT_DIR = REVIEW_SAMPLES_DIR
SLEEP_DIR = OUTPUT_DIR / "sleep"
CYCLING_DIR = OUTPUT_DIR / "cycling"
WEEKLY_DIR = OUTPUT_DIR / "weekly"
BJ_TZ = timezone(timedelta(hours=8))
REDACTED = "***REDACTED***"
SKIP = object()
TARGET_SLEEP = 5
TARGET_CYCLING = 5
TARGET_WEEKLY = 1
SLEEP_LOOKBACK_DAYS = 21
ACTIVITY_FETCH_LIMIT = 30

EMAIL_RE = re.compile(r"\b([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
TOKEN_IN_TEXT_RE = re.compile(
    r"(?i)\b(?:sk-[A-Za-z0-9_-]{8,}|"
    r"(?:api[_-]?key|token|secret|session[_-]?token|pushplus[_-]?token)\s*[:=]\s*[^\s,;]+)\b"
)
BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._-]+\b")

COORD_KEYS = {
    "startlatitude",
    "startlongitude",
    "endlatitude",
    "endlongitude",
}
REDACTED_KEYS = {
    "userid",
    "user_id",
    "userprofilepk",
    "ownerid",
    "owner_id",
    "userprofileid",
    "user_profile_id",
    "userdailysummaryid",
    "profileid",
    "profile_id",
    "deviceid",
    "device_id",
    "sessionid",
    "session_id",
    "sessiontoken",
    "session_token",
    "pushplustoken",
    "pushplus_token",
    "uuid",
    "ownerdisplayname",
    "ownerprofileimageurl",
    "ownerprofileimageurlsmall",
    "ownerprofileimageurlmedium",
    "ownerprofileimageurllarge",
    "garmin_email",
    "garmin_password",
}
SENSITIVE_KEYWORDS = (
    "token",
    "secret",
    "password",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
)
CYCLING_TYPE_KEYS = {
    "cycling",
    "road_biking",
    "mountain_biking",
    "indoor_cycling",
    "virtual_ride",
    "gravel_cycling",
    "bmx",
}


@dataclass
class SleepCandidate:
    user_name: str
    display_name: str
    date: str
    normalized: dict
    history: list[dict]
    source: str


@dataclass
class ActivityCandidate:
    user_name: str
    display_name: str
    date: str
    activity_id: str
    activity_name: str
    normalized: dict
    history: list[dict]
    source: str


def bj_now() -> datetime:
    return datetime.now(BJ_TZ)


def mask_email(match):
    local = match.group(1)
    domain = match.group(2)
    if len(local) <= 2:
        masked_local = "*" * len(local)
    elif len(local) <= 4:
        masked_local = local[0] + "***" + local[-1]
    else:
        masked_local = local[:2] + "***" + local[-2:]
    return f"{masked_local}@{domain}"


def sanitize_text(text):
    return shared_sanitize_text(text)


def is_coord_key(key):
    key_lower = key.lower()
    return key_lower in COORD_KEYS or key_lower.endswith("latitude") or key_lower.endswith("longitude")


def should_redact_key(key):
    key_lower = key.lower()
    if key_lower in REDACTED_KEYS:
        return True
    return any(keyword in key_lower for keyword in SENSITIVE_KEYWORDS)


def sanitize(value, parent_key=""):
    return sanitize_export_value(value, parent_key)


def safe_slug(value: str) -> str:
    return value.replace("/", "_").replace(" ", "_")


def write_json(path: Path, payload: dict):
    path.write_text(
        json.dumps(sanitize(deepcopy(payload)), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def write_markdown(path: Path, text: str):
    path.write_text(sanitize_text(text).rstrip() + "\n", encoding="utf-8")


def safe_call(func, default):
    try:
        return func()
    except Exception:
        return default


def activity_type_key(activity: dict) -> str:
    return ((activity.get("activityType") or {}).get("typeKey") or "").lower()


def is_cycling_activity(activity: dict) -> bool:
    return activity_type_key(activity) in CYCLING_TYPE_KEYS


def sleep_priority(candidate: SleepCandidate):
    payload = build_sleep_payload(candidate.display_name, candidate.normalized, candidate.history)
    alert_score = 1 if payload.get("forced_alerts") else 0
    score = candidate.normalized.get("basic_sleep", {}).get("sleep_score") or 0
    return (alert_score, candidate.date, score)


def activity_priority(candidate: ActivityCandidate):
    payload = build_activity_payload(candidate.normalized, candidate.display_name, candidate.history)
    alert_count = len(payload.get("priority_issues") or [])
    tss = candidate.normalized.get("load_recovery", {}).get("training_stress_score") or 0
    return (alert_count, candidate.date, tss)


def summarize_sleep_feature(normalized: dict, llm_payload: dict) -> str:
    score = normalized.get("basic_sleep", {}).get("sleep_score") or 0
    if score >= 80:
        prefix = "睡眠恢复较好"
    elif score >= 70:
        prefix = "睡眠恢复中等"
    else:
        prefix = "睡眠恢复偏紧"
    alerts = llm_payload.get("forced_alerts") or []
    if alerts:
        titles = "，".join(alert["title"] for alert in alerts[:2])
        return f"{prefix}，{titles}"
    return f"{prefix}，无明显异常"


def summarize_cycling_feature(normalized: dict, llm_payload: dict) -> str:
    tss = normalized.get("load_recovery", {}).get("training_stress_score") or 0
    if tss >= 150:
        prefix = "骑行高负荷"
    elif tss >= 90:
        prefix = "骑行中高强度"
    else:
        prefix = "骑行轻松恢复骑"
    issues = llm_payload.get("priority_issues") or []
    if issues:
        titles = "，".join(issue["title"] for issue in issues[:2])
        return f"{prefix}，{titles}"
    return f"{prefix}，无明显异常"


def summarize_weekly_feature(llm_payload: dict) -> str:
    notable = llm_payload.get("notable_change") or ""
    if notable:
        return notable.rstrip("。")
    return "周报样本"


def build_sample_package(sample_type: str, user_name: str, display_name: str, date_key: str, normalized: dict, llm_payload: dict, final_message: str, source: str, feature: str) -> dict:
    return {
        "metadata": {
            "sample_type": sample_type,
            "user_name": user_name,
            "display_name": display_name,
            "date": date_key,
            "generated_at": bj_now().isoformat(),
            "source": source,
            "model_chain": "current_script + DeepSeek V3.2",
            "feature": feature,
        },
        "normalized_data": normalized,
        "llm_payload": llm_payload,
        "final_message": final_message,
    }


def fetch_sleep_candidates(client, user_name: str, display_name: str) -> list[SleepCandidate]:
    today = bj_now().date()
    raw_records = []
    for offset in range(SLEEP_LOOKBACK_DAYS):
        target_date = (today - timedelta(days=offset)).strftime("%Y-%m-%d")
        sleep_data = safe_call(lambda d=target_date: client.get_sleep_data(d), {})
        sleep_dto = (sleep_data or {}).get("dailySleepDTO", {}) or {}
        if not sleep_dto or (sleep_dto.get("sleepTimeSeconds") or 0) < 1800:
            continue
        stats = safe_call(lambda d=target_date: client.get_stats(d), {})
        hrv_data = safe_call(lambda d=target_date: client.get_hrv_data(d), {})
        body_battery = safe_call(lambda d=target_date: client.get_body_battery(d), [])
        normalized = normalize_sleep(target_date, sleep_data, stats, hrv_data, body_battery)
        raw_records.append((target_date, normalized))

    candidates = []
    raw_records.sort(key=lambda item: item[0], reverse=True)
    for date_key, normalized in raw_records:
        history = [
            item_normalized
            for item_date, item_normalized in raw_records
            if item_date < date_key
        ][:7]
        candidates.append(
            SleepCandidate(
                user_name=user_name,
                display_name=display_name,
                date=date_key,
                normalized=normalized,
                history=history,
                source="dry_run_current_chain",
            )
        )
    return candidates


def fetch_activity_candidates(client, user_name: str, display_name: str):
    activities = safe_call(lambda: client.get_activities(0, ACTIVITY_FETCH_LIMIT), [])
    badges = safe_call(lambda: client.get_earned_badges(), [])
    cycling_candidates = []
    weekly_history = []
    recent_history = []
    week_cutoff = (bj_now().date() - timedelta(days=6)).strftime("%Y-%m-%d")

    for activity in activities:
        activity_id = str(activity.get("activityId"))
        detail = safe_call(lambda aid=activity_id: client.get_activity(aid), activity)
        splits_raw = safe_call(lambda aid=activity_id: client.get_activity_splits(aid), {})
        activity_date = gm.beijing_date_from_activity(activity)
        normalized, _ = normalize_activity(
            activity=activity,
            detail=detail,
            splits_raw=splits_raw,
            badges=badges,
            activity_date=activity_date,
            recent_history=recent_history,
        )
        history_snapshot = list(recent_history)
        recent_history.insert(0, normalized)
        if activity_date >= week_cutoff:
            weekly_history.append(normalized)
        if not is_cycling_activity(activity):
            continue
        cycling_candidates.append(
            ActivityCandidate(
                user_name=user_name,
                display_name=display_name,
                date=activity_date,
                activity_id=activity_id,
                activity_name=normalized.get("basic_activity", {}).get("activity_name") or activity.get("activityName") or "",
                normalized=normalized,
                history=history_snapshot,
                source="dry_run_current_chain",
            )
        )
    return cycling_candidates, weekly_history


def choose_weekly_user(sleep_by_user: dict[str, list[dict]], activity_by_user: dict[str, list[dict]]):
    best_name = None
    best_score = -1
    for user in gm.USERS:
        name = user["name"]
        score = len(sleep_by_user.get(name, [])) * 2 + len(activity_by_user.get(name, [])) * 3
        if score > best_score:
            best_name = name
            best_score = score
    return best_name


def collect_samples():
    for path in (OUTPUT_DIR, SLEEP_DIR, CYCLING_DIR, WEEKLY_DIR):
        path.mkdir(parents=True, exist_ok=True)
    for path in (SLEEP_DIR, CYCLING_DIR, WEEKLY_DIR):
        for file in path.glob("*.json"):
            file.unlink()
    index_path = OUTPUT_DIR / "index.md"
    if index_path.exists():
        index_path.unlink()

    sleep_candidates = []
    cycling_candidates = []
    sleep_histories_for_weekly = {}
    activity_histories_for_weekly = {}

    clients = {}
    display_names = {}
    for user in gm.USERS:
        client = gm.login_garmin(user)
        clients[user["name"]] = client
        display_name = gm.get_display_name(client, user["name"])
        display_names[user["name"]] = display_name

        user_sleep_candidates = fetch_sleep_candidates(client, user["name"], display_name)
        sleep_candidates.extend(user_sleep_candidates)
        sleep_histories_for_weekly[user["name"]] = [
            candidate.normalized
            for candidate in user_sleep_candidates
            if candidate.date >= (bj_now().date() - timedelta(days=6)).strftime("%Y-%m-%d")
        ][:7]

        user_cycling_candidates, weekly_activity_history = fetch_activity_candidates(client, user["name"], display_name)
        cycling_candidates.extend(user_cycling_candidates)
        activity_histories_for_weekly[user["name"]] = weekly_activity_history[:20]

    selected_sleep = sorted(sleep_candidates, key=sleep_priority, reverse=True)[:TARGET_SLEEP]
    selected_cycling = sorted(cycling_candidates, key=activity_priority, reverse=True)[:TARGET_CYCLING]

    created_files = []
    index_lines = [
        "# Garmin Review Samples",
        "",
        f"- 生成时间：{bj_now().strftime('%Y-%m-%d %H:%M:%S %Z')}",
        "- 来源：现有脚本链路 dry-run，分析模型为 DeepSeek V3.2",
        "- 每个 JSON 文件包含：normalized_data / llm_payload / final_message",
        "",
    ]

    sleep_exports = []
    for idx, candidate in enumerate(selected_sleep, start=1):
        llm_payload = build_sleep_payload(candidate.display_name, candidate.normalized, candidate.history)
        final_message = analyze_with_llm(llm_payload, mode="sleep")
        feature = summarize_sleep_feature(candidate.normalized, llm_payload)
        filename = f"{idx:02d}_{safe_slug(candidate.user_name)}_{candidate.date}.json"
        path = SLEEP_DIR / filename
        package = build_sample_package(
            "sleep",
            candidate.user_name,
            candidate.display_name,
            candidate.date,
            candidate.normalized,
            llm_payload,
            final_message,
            candidate.source,
            feature,
        )
        write_json(path, package)
        created_files.append(str(path))
        sleep_exports.append((filename, feature, final_message))
        index_lines.append(f"- sleep/{filename} | 睡眠晨报 | {candidate.date} | {feature}")

    index_lines.append("")

    cycling_exports = []
    for idx, candidate in enumerate(selected_cycling, start=1):
        llm_payload = build_activity_payload(candidate.normalized, candidate.display_name, candidate.history)
        final_message = analyze_with_llm(llm_payload, mode="activity")
        feature = summarize_cycling_feature(candidate.normalized, llm_payload)
        filename = f"{idx:02d}_{safe_slug(candidate.user_name)}_{candidate.date}.json"
        path = CYCLING_DIR / filename
        package = build_sample_package(
            "cycling",
            candidate.user_name,
            candidate.display_name,
            candidate.date,
            candidate.normalized,
            llm_payload,
            final_message,
            candidate.source,
            feature,
        )
        write_json(path, package)
        created_files.append(str(path))
        cycling_exports.append((filename, feature, final_message))
        index_lines.append(f"- cycling/{filename} | 骑行运动快报 | {candidate.date} | {feature}")

    index_lines.append("")

    weekly_exports = []
    weekly_user = choose_weekly_user(sleep_histories_for_weekly, activity_histories_for_weekly)
    if weekly_user:
        sleep_history = sleep_histories_for_weekly.get(weekly_user, [])
        activity_history = activity_histories_for_weekly.get(weekly_user, [])
        display_name = display_names[weekly_user]
        llm_payload = build_weekly_payload(display_name, sleep_history, activity_history)
        if llm_payload:
            final_message = analyze_with_llm(llm_payload, mode="weekly")
            week_key = bj_now().strftime("%G-W%V")
            feature = summarize_weekly_feature(llm_payload)
            filename = f"01_{safe_slug(weekly_user)}_{week_key}.json"
            path = WEEKLY_DIR / filename
            package = {
                "metadata": {
                    "sample_type": "weekly",
                    "user_name": weekly_user,
                    "display_name": display_name,
                    "date": week_key,
                    "generated_at": bj_now().isoformat(),
                    "source": "dry_run_current_chain",
                    "model_chain": "current_script + DeepSeek V3.2",
                    "feature": feature,
                },
                "normalized_data": {
                    "sleep_history": sleep_history,
                    "activity_history": activity_history,
                },
                "llm_payload": llm_payload,
                "final_message": final_message,
            }
            write_json(path, package)
            created_files.append(str(path))
            weekly_exports.append((filename, feature, final_message))
            index_lines.append(f"- weekly/{filename} | 趋势周报 | {week_key} | {feature}")

    write_markdown(index_path, "\n".join(index_lines))
    created_files.append(str(index_path))

    summary = {
        "created_files": created_files,
        "index_path": str(index_path),
        "sleep_exports": sleep_exports,
        "cycling_exports": cycling_exports,
        "weekly_exports": weekly_exports,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    collect_samples()
