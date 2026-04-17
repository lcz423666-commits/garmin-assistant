#!/usr/bin/env python3
"""Export sanitized Garmin sleep/activity samples for debugging."""

import copy
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = PROJECT_ROOT / "app"
if APP_DIR.exists() and str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import garmin_monitor as gm
from app_config import DEBUG_DIR, mask_identifier, sanitize_export_value, sanitize_text as shared_sanitize_text

OUTPUT_DIR = DEBUG_DIR
REDACTED = "***REDACTED***"
SKIP = object()

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


def bj_now():
    return datetime.now(timezone(timedelta(hours=8)))


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


def safe_call(func, default):
    try:
        return func()
    except Exception:
        return default


def write_json(path, payload):
    sanitized = sanitize(payload)
    path.write_text(
        json.dumps(sanitized, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def write_text(path, content):
    path.write_text(sanitize_text(content).rstrip() + "\n", encoding="utf-8")


def build_sleep_sample(client, user, display_name, target_date):
    sleep = client.get_sleep_data(target_date)
    sleep_dto = sleep.get("dailySleepDTO", {})
    if not sleep_dto or not sleep_dto.get("sleepTimeSeconds"):
        return None

    sleep_seconds = sleep_dto.get("sleepTimeSeconds", 0)
    if sleep_seconds < 1800:
        return None

    scores = sleep_dto.get("sleepScores", {})
    stats = safe_call(lambda: client.get_stats(target_date), {})
    hrv_data = safe_call(lambda: client.get_hrv_data(target_date), {})
    hrv_summary = hrv_data.get("hrvSummary", {}) if isinstance(hrv_data, dict) else {}
    activities_raw = safe_call(lambda: client.get_activities(0, 3), [])

    activities_enriched = []
    for activity in activities_raw:
        activity_id = activity.get("activityId")
        entry = {
            "date": gm.beijing_date_from_activity(activity),
            "type": activity.get("activityName", ""),
            "distance_km": round(activity.get("distance", 0) / 1000, 1),
            "duration_min": round(activity.get("duration", 0) / 60),
            "avg_hr": activity.get("averageHR"),
            "max_hr": activity.get("maxHR"),
            "elevation_gain_m": activity.get("elevationGain"),
            "min_temperature_c": activity.get("minTemperature"),
            "max_temperature_c": activity.get("maxTemperature"),
            "aerobic_training_effect": activity.get("aerobicTrainingEffect"),
            "anaerobic_training_effect": activity.get("anaerobicTrainingEffect"),
        }

        detail = safe_call(lambda aid=activity_id: client.get_activity(aid), {})
        if isinstance(detail, dict):
            summary = detail.get("summaryDTO", {})
            entry["training_effect_label"] = summary.get("trainingEffectLabel")
            entry["aerobic_te_message"] = summary.get("aerobicTrainingEffectMessage")
            entry["training_load"] = summary.get("activityTrainingLoad")
            if not entry["min_temperature_c"]:
                entry["min_temperature_c"] = summary.get("minTemperature")
            if not entry["max_temperature_c"]:
                entry["max_temperature_c"] = summary.get("maxTemperature")

        hr_zones = safe_call(lambda aid=activity_id: client.get_activity_hr_in_timezones(aid), [])
        if hr_zones:
            entry["hr_zones_secs"] = [
                {
                    "zone": zone.get("zoneNumber"),
                    "secs": round(zone.get("secsInZone", 0)),
                    "lower_bpm": zone.get("zoneLowBoundary"),
                }
                for zone in hr_zones
            ]

        activities_enriched.append(entry)

    body_battery_raw = safe_call(lambda: client.get_body_battery(target_date), [])
    body_battery_summary = {}
    if body_battery_raw:
        body_battery = body_battery_raw[0]
        body_battery_summary = {
            "date": body_battery.get("date"),
            "charged": body_battery.get("charged"),
            "drained": body_battery.get("drained"),
            "activity_events": [
                {
                    "type": event.get("eventType"),
                    "battery_impact": event.get("bodyBatteryImpact"),
                    "feedback": event.get("feedbackType"),
                }
                for event in body_battery.get("bodyBatteryActivityEvent", [])
            ],
            "dynamic_feedback": (
                body_battery.get("bodyBatteryDynamicFeedbackEvent", {}) or {}
            ).get("bodyBatteryLevel"),
        }

    full_data = {
        "sleep": {
            "total_hours": round(sleep_seconds / 3600, 2),
            "deep_sleep_min": round(sleep_dto.get("deepSleepSeconds", 0) / 60),
            "rem_sleep_min": round(sleep_dto.get("remSleepSeconds", 0) / 60),
            "light_sleep_min": round(sleep_dto.get("lightSleepSeconds", 0) / 60),
            "awake_min": round(sleep_dto.get("awakeSleepSeconds", 0) / 60),
            "avg_spo2_percent": sleep_dto.get("averageSpO2Value"),
            "lowest_spo2_percent": sleep_dto.get("lowestSpO2Value"),
            "avg_sleep_hr": sleep_dto.get("avgHeartRate"),
            "sleep_scores": {
                "overall": scores.get("overall", {}).get("value"),
                "overall_qualifier": scores.get("overall", {}).get("qualifierKey"),
                "deep_pct": scores.get("deepPercentage", {}).get("value"),
                "deep_qualifier": scores.get("deepPercentage", {}).get("qualifierKey"),
                "rem_pct": scores.get("remPercentage", {}).get("value"),
                "rem_qualifier": scores.get("remPercentage", {}).get("qualifierKey"),
            },
            "all_fields": {key: value for key, value in sleep_dto.items() if value is not None},
        },
        "hrv": {
            "last_night_avg": hrv_summary.get("lastNightAvg"),
            "weekly_avg": hrv_summary.get("weeklyAvg"),
            "status": hrv_summary.get("status"),
            "baseline_balanced_low": (hrv_summary.get("baseline", {}) or {}).get("balancedLow"),
            "baseline_balanced_upper": (hrv_summary.get("baseline", {}) or {}).get("balancedUpper"),
            "all_fields": hrv_summary,
        },
        "resting_heart_rate": stats.get("restingHeartRate"),
        "daily_stats": {key: value for key, value in stats.items() if value is not None} if stats else {},
        "body_battery": body_battery_summary,
    }

    message_payload = sanitize(copy.deepcopy(full_data))
    data_json = json.dumps(message_payload, ensure_ascii=False, default=str)
    message = (
        f"以下是{display_name}今日的佳明睡眠与恢复数据（日期：{target_date}），请进行全面深度分析。\n\n"
        f"{data_json}\n\n"
        "请覆盖所有有价值的数据维度，不要遗漏任何指标。"
    )

    hours = sleep_seconds // 3600
    minutes = (sleep_seconds % 3600) // 60
    raw_sample = {
        "meta": {
            "source_user": user["name"],
            "display_name": display_name,
            "sample_date": target_date,
            "exported_at": bj_now().isoformat(),
            "sleep_duration_text": f"{hours}小时{minutes}分钟",
        },
        "sleep_raw": sleep,
        "sleep_dto": sleep_dto,
        "hrv_raw": hrv_data,
        "hrv_summary": hrv_summary,
        "daily_stats": stats,
        "body_battery_raw": body_battery_raw,
        "body_battery_summary": body_battery_summary,
        "recent_activities_raw": activities_raw,
        "recent_activities_enriched": activities_enriched,
        "full_data_for_llm": full_data,
    }
    return raw_sample, message


def build_activity_sample(client, user, display_name, activity):
    activity_id = str(activity.get("activityId"))
    activity_date = gm.beijing_date_from_activity(activity)
    detail = safe_call(lambda: client.get_activity(activity_id), activity)
    if not isinstance(detail, dict):
        detail = activity

    distance_km = round(activity.get("distance", 0) / 1000, 1)
    duration_min = round(activity.get("duration", 0) / 60)
    activity_name = (
        activity.get("activityName")
        or (activity.get("activityType") or {}).get("typeKey", "未知运动")
    )

    full_data = {}
    for source in (activity, detail):
        for key, value in source.items():
            if value is not None and key not in full_data:
                full_data[key] = value

    for speed_key in ("averageSpeed", "maxSpeed"):
        if speed_key in full_data:
            full_data[speed_key] = round(full_data[speed_key] * 3.6, 1)

    splits_raw = safe_call(lambda: client.get_activity_splits(activity_id), {})
    if isinstance(splits_raw, dict):
        lap_dtos = splits_raw.get("lapDTOs", [])
        keep = (
            "lapIndex",
            "startTimeGMT",
            "distance",
            "duration",
            "movingDuration",
            "elevationGain",
            "elevationLoss",
            "minElevation",
            "averageSpeed",
            "averageMovingSpeed",
            "maxSpeed",
            "calories",
            "averageHR",
            "maxHR",
            "averageBikeCadence",
            "maxBikeCadence",
            "averageTemperature",
            "maxTemperature",
            "minTemperature",
            "averagePower",
            "maxPower",
            "normalizedPower",
            "totalWork",
            "leftBalance",
            "rightBalance",
        )
        full_data["splits"] = [
            {key: value for key, value in lap.items() if key in keep}
            for lap in lap_dtos
        ]

    badges_raw = safe_call(lambda: client.get_earned_badges(), [])
    if badges_raw:
        full_data["recent_badges"] = [
            {
                "name": badge.get("badgeName") or badge.get("badgeKey"),
                "earned_date": badge.get("badgeEarnedDate") or badge.get("earnedDate"),
                "category": badge.get("badgeCategoryId"),
            }
            for badge in badges_raw[:10]
        ]

    full_data_before_clean = copy.deepcopy(full_data)
    full_data_after_clean = gm.clean_activity(copy.deepcopy(full_data))

    message_payload = sanitize(copy.deepcopy(full_data_after_clean))
    data_json = json.dumps(message_payload, ensure_ascii=False, default=str)
    date_hint = f"（活动日期：{activity_date}）" if activity_date else ""
    today = bj_now().strftime("%Y-%m-%d")
    message = (
        f"以下是{display_name}的一条运动记录{date_hint}，请进行全面深度分析。\n\n"
        f"今天的日期是：{today}。数据中的 startTimeLocal 字段记录了该活动的具体日期时间，"
        f"如果活动日期与今天相同请称为今天，前一天称为昨天，以此类推。\n"
        f"注意：averageSpeed 和 maxSpeed 字段单位已换算为 km/h。\n"
        f"注意：max20MinPower 是20分钟最大平均功率（功能性阈值参考值），maxAvgPower_20 是20秒最大平均功率，两者含义完全不同，分析20分钟功率时必须用 max20MinPower 字段。\n\n"
        f"{data_json}\n\n"
        "请覆盖所有有价值的数据维度，不要遗漏任何指标，包括勋章成就、个人记录突破等。"
    )

    raw_sample = {
        "meta": {
            "source_user": user["name"],
            "display_name": display_name,
            "activity_id": activity_id,
            "activity_name": activity_name,
            "activity_date": activity_date,
            "exported_at": bj_now().isoformat(),
            "distance_duration": f"{distance_km}km | {duration_min}min",
        },
        "activity_summary": activity,
        "activity_detail": detail,
        "splits_raw": splits_raw,
        "badges_raw_top10": badges_raw[:10] if isinstance(badges_raw, list) else badges_raw,
        "full_data_before_clean": full_data_before_clean,
        "full_data_after_clean": full_data_after_clean,
    }
    return raw_sample, message


def write_placeholder_files(activity_missing, sleep_missing):
    timestamp = bj_now().isoformat()
    if activity_missing:
        write_json(
            OUTPUT_DIR / "activity_raw_sample.json",
            {
                "status": "no_recent_activity_found",
                "exported_at": timestamp,
            },
        )
        write_text(
            OUTPUT_DIR / "activity_llm_message.txt",
            "未找到可导出的最近运动样本。",
        )
    if sleep_missing:
        write_json(
            OUTPUT_DIR / "sleep_raw_sample.json",
            {
                "status": "no_recent_sleep_found",
                "exported_at": timestamp,
            },
        )
        write_text(
            OUTPUT_DIR / "sleep_llm_message.txt",
            "未找到可导出的最近睡眠样本。",
        )


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    gm.feishu_write_run_log = lambda *args, **kwargs: None

    sleep_result = None
    activity_result = None

    for user in gm.USERS:
        print(f"[INFO] Checking user: {user['name']}")
        try:
            client = gm.login_garmin(user)
            display_name = gm.get_display_name(client, user["name"])
        except Exception as exc:
            print(f"[WARN] Login failed for {user['name']}: {sanitize_text(str(exc))}")
            continue

        if sleep_result is None:
            for days_back in range(0, 7):
                target_date = (bj_now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
                try:
                    sleep_result = build_sleep_sample(client, user, display_name, target_date)
                except Exception as exc:
                    print(f"[WARN] Sleep export failed for {user['name']} {target_date}: {sanitize_text(str(exc))}")
                    sleep_result = None
                if sleep_result:
                    print(f"[INFO] Sleep sample selected: {user['name']} {target_date}")
                    break

        if activity_result is None:
            activities = safe_call(lambda: client.get_activities(0, 10), [])
            for activity in activities:
                try:
                    activity_result = build_activity_sample(client, user, display_name, activity)
                except Exception as exc:
                    activity_id = activity.get("activityId")
                    print(
                        f"[WARN] Activity export failed for {user['name']} "
                        f"{mask_identifier(str(activity_id))}: {sanitize_text(str(exc))}"
                    )
                    activity_result = None
                if activity_result:
                    meta = activity_result[0]["meta"]
                    print(
                        f"[INFO] Activity sample selected: {user['name']} "
                        f"{meta['activity_name']} {meta['activity_date']}"
                    )
                    break

        if sleep_result and activity_result:
            break

    if activity_result:
        raw_sample, message = activity_result
        write_json(OUTPUT_DIR / "activity_raw_sample.json", raw_sample)
        write_text(OUTPUT_DIR / "activity_llm_message.txt", message)

    if sleep_result:
        raw_sample, message = sleep_result
        write_json(OUTPUT_DIR / "sleep_raw_sample.json", raw_sample)
        write_text(OUTPUT_DIR / "sleep_llm_message.txt", message)

    write_placeholder_files(activity_missing=activity_result is None, sleep_missing=sleep_result is None)

    if activity_result is None and sleep_result is None:
        raise SystemExit("No activity or sleep samples could be exported.")

    print("[INFO] Export finished.")


if __name__ == "__main__":
    main()
