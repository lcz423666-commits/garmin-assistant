import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = PROJECT_ROOT / "app"
if APP_DIR.exists() and str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import garmin_monitor as gm
from activity_cleaner import normalize_activity
from garmin_storage import load_recent_normalized
from llm_helper import analyze_with_llm
from phase1_builder import build_activity_payload, build_sleep_payload, build_weekly_payload
from sleep_cleaner import normalize_sleep


BJ = timezone(timedelta(hours=8))


def main():
    user = next(user for user in gm.USERS if user["name"] == "丛至")
    client = gm.login_garmin(user)
    display_name = gm.get_display_name(client, user["name"])
    today = datetime.now(BJ).strftime("%Y-%m-%d")

    sleep_data = client.get_sleep_data(today)
    stats = gm.safe_call(lambda: client.get_stats(today), {})
    hrv_data = gm.safe_call(lambda: client.get_hrv_data(today), {})
    body_battery = gm.safe_call(lambda: client.get_body_battery(today), [])
    recent_sleep = load_recent_normalized(user["name"], "sleep", limit=7, since_days=21)
    normalized_sleep = normalize_sleep(today, sleep_data, stats, hrv_data, body_battery)
    sleep_payload = build_sleep_payload(display_name, normalized_sleep, recent_sleep)
    sleep_text = analyze_with_llm(sleep_payload, mode="sleep")

    activity = client.get_activities(0, 1)[0]
    activity_id = str(activity.get("activityId"))
    detail = gm.safe_call(lambda aid=activity_id: client.get_activity(aid), activity)
    splits = gm.safe_call(lambda aid=activity_id: client.get_activity_splits(aid), {})
    badges = gm.safe_call(lambda: client.get_earned_badges(), [])
    recent_activity = load_recent_normalized(user["name"], "activity", limit=12, since_days=45)
    normalized_activity, _ = normalize_activity(
        activity,
        detail,
        splits,
        badges,
        gm.beijing_date_from_activity(activity),
        recent_activity,
    )
    activity_payload = build_activity_payload(normalized_activity, display_name, recent_activity)
    activity_text = analyze_with_llm(activity_payload, mode="activity")

    weekly_sleep = []
    for offset in range(7):
        target_date = (datetime.now(BJ) - timedelta(days=offset)).strftime("%Y-%m-%d")
        sleep_item = client.get_sleep_data(target_date)
        sleep_dto = (sleep_item or {}).get("dailySleepDTO", {}) or {}
        if not sleep_dto or not sleep_dto.get("sleepTimeSeconds"):
            continue
        stats_item = gm.safe_call(lambda d=target_date: client.get_stats(d), {})
        hrv_item = gm.safe_call(lambda d=target_date: client.get_hrv_data(d), {})
        battery_item = gm.safe_call(lambda d=target_date: client.get_body_battery(d), [])
        weekly_sleep.append(normalize_sleep(target_date, sleep_item, stats_item, hrv_item, battery_item))

    weekly_activity = []
    rolling_history = []
    week_cutoff = (datetime.now(BJ) - timedelta(days=6)).strftime("%Y-%m-%d")
    for item in client.get_activities(0, 12):
        activity_date = gm.beijing_date_from_activity(item)
        if activity_date < week_cutoff:
            continue
        item_id = str(item.get("activityId"))
        item_detail = gm.safe_call(lambda aid=item_id: client.get_activity(aid), item)
        item_splits = gm.safe_call(lambda aid=item_id: client.get_activity_splits(aid), {})
        item_normalized, _ = normalize_activity(
            item,
            item_detail,
            item_splits,
            badges,
            activity_date,
            rolling_history,
        )
        weekly_activity.append(item_normalized)
        rolling_history.insert(0, item_normalized)

    weekly_payload = build_weekly_payload(display_name, weekly_sleep, weekly_activity)
    weekly_text = analyze_with_llm(weekly_payload, mode="weekly") if weekly_payload else "暂无周报样本"

    print("SLEEP_FORCED_ALERTS")
    print(json.dumps(sleep_payload.get("forced_alerts", []), ensure_ascii=False, indent=2))
    print("SLEEP_SAMPLE_START")
    print(sleep_text)
    print("SLEEP_SAMPLE_END")
    print("ACTIVITY_PRIORITY_ISSUES")
    print(json.dumps(activity_payload.get("priority_issues", []), ensure_ascii=False, indent=2))
    print("ACTIVITY_ALL_ALERTS")
    print(json.dumps(activity_payload.get("forced_alerts", []), ensure_ascii=False, indent=2))
    print("ACTIVITY_SAMPLE_START")
    print(activity_text)
    print("ACTIVITY_SAMPLE_END")
    print("WEEKLY_SAMPLE_START")
    print(weekly_text)
    print("WEEKLY_SAMPLE_END")


if __name__ == "__main__":
    main()
