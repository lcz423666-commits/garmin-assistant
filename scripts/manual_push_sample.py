import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = PROJECT_ROOT / "app"
if APP_DIR.exists() and str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from app_config import mask_identifier, sanitize_text
import garmin_monitor as gm
from activity_cleaner import normalize_activity
from garmin_storage import load_recent_normalized
from llm_helper import analyze_with_llm
from phase1_builder import build_activity_payload, build_sleep_payload
from sleep_cleaner import normalize_sleep


BJ = timezone(timedelta(hours=8))


def main():
    user = next(user for user in gm.USERS if user["name"] == "丛至")
    client = gm.login_garmin(user)
    display_name = gm.get_display_name(client, user["name"])
    now = datetime.now(BJ)

    sleep_sent = False
    sleep_used_date = None
    sleep_preview = ""
    for days_back in range(0, 8):
        target_date = (now - timedelta(days=days_back)).strftime("%Y-%m-%d")
        sleep_data = client.get_sleep_data(target_date)
        sleep_dto = (sleep_data or {}).get("dailySleepDTO", {}) or {}
        if not sleep_dto or (sleep_dto.get("sleepTimeSeconds") or 0) < 1800:
            continue

        stats = gm.safe_call(lambda: client.get_stats(target_date), {})
        hrv_data = gm.safe_call(lambda: client.get_hrv_data(target_date), {})
        body_battery = gm.safe_call(lambda: client.get_body_battery(target_date), [])
        recent_sleep = load_recent_normalized(user["name"], "sleep", limit=7, since_days=21)
        normalized_sleep = normalize_sleep(target_date, sleep_data, stats, hrv_data, body_battery)
        sleep_payload = build_sleep_payload(display_name, normalized_sleep, recent_sleep)
        sleep_text = analyze_with_llm(sleep_payload, mode="sleep")
        total_min = normalized_sleep["basic_sleep"]["total_sleep_min"]
        gm.push_to_wechat(
            user,
            "佳明睡眠晨报 - 手动样本",
            sleep_text,
            activity_type="睡眠晨报",
            distance_duration=f"{total_min // 60}小时{total_min % 60}分钟",
        )
        sleep_sent = True
        sleep_used_date = target_date
        sleep_preview = sleep_text
        break

    activities = client.get_activities(0, 10)
    activity_sent = False
    activity_used_id = None
    activity_used_date = None
    activity_preview = ""
    for activity in activities:
        activity_id = str(activity.get("activityId"))
        detail = gm.safe_call(lambda aid=activity_id: client.get_activity(aid), activity)
        splits = gm.safe_call(lambda aid=activity_id: client.get_activity_splits(aid), {})
        badges = gm.safe_call(lambda: client.get_earned_badges(), [])
        activity_date = gm.beijing_date_from_activity(activity)
        recent_activity = load_recent_normalized(user["name"], "activity", limit=12, since_days=45)
        normalized_activity, _ = normalize_activity(
            activity,
            detail,
            splits,
            badges,
            activity_date,
            recent_activity,
        )
        activity_payload = build_activity_payload(normalized_activity, display_name, recent_activity)
        activity_text = analyze_with_llm(activity_payload, mode="activity")
        gm.push_to_wechat(
            user,
            f"佳明运动快报 - 手动样本 - {normalized_activity['basic_activity']['activity_name']}",
            activity_text,
            activity_type=normalized_activity["basic_activity"]["activity_name"],
            distance_duration=(
                f"{normalized_activity['basic_activity']['distance_km']}km | "
                f"{normalized_activity['basic_activity']['duration_min']}min"
            ),
        )
        activity_sent = True
        activity_used_id = activity_id
        activity_used_date = activity_date
        activity_preview = activity_text
        break

    print("DISPLAY_NAME", sanitize_text(display_name))
    print("SLEEP_SENT", sleep_sent)
    print("SLEEP_USED_DATE", sleep_used_date)
    print("SLEEP_PREVIEW_START")
    print(sanitize_text(sleep_preview[:800]))
    print("SLEEP_PREVIEW_END")
    print("ACTIVITY_SENT", activity_sent)
    print("ACTIVITY_USED_ID", mask_identifier(activity_used_id or ""))
    print("ACTIVITY_USED_DATE", activity_used_date)
    print("ACTIVITY_PREVIEW_START")
    print(sanitize_text(activity_preview[:800]))
    print("ACTIVITY_PREVIEW_END")


if __name__ == "__main__":
    main()
