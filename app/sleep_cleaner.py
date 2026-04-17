"""Sleep normalization for the phase-one Garmin assistant."""

from __future__ import annotations


def _to_min(seconds):
    if seconds in (None, ""):
        return None
    return int(round(float(seconds) / 60))


def _diff_minutes(current, baseline):
    if current in (None, "") or baseline in (None, ""):
        return None
    return int(round(float(current) - float(baseline)))


def normalize_sleep(
    target_date: str,
    sleep_data: dict,
    stats: dict,
    hrv_data: dict,
    body_battery_list: list,
) -> dict:
    sleep_dto = (sleep_data or {}).get("dailySleepDTO", {}) or {}
    hrv_summary = (hrv_data or {}).get("hrvSummary", {}) or {}
    scores = sleep_dto.get("sleepScores", {}) or {}
    sleep_need = sleep_dto.get("sleepNeed", {}) or {}
    next_sleep_need = sleep_dto.get("nextSleepNeed", {}) or {}
    body_battery_event = (stats or {}).get("bodyBatteryDynamicFeedbackEvent", {}) or {}

    return {
        "date": target_date,
        "basic_sleep": {
            "date": target_date,
            "total_sleep_min": _to_min(sleep_dto.get("sleepTimeSeconds")) or 0,
            "deep_sleep_min": _to_min(sleep_dto.get("deepSleepSeconds")) or 0,
            "rem_sleep_min": _to_min(sleep_dto.get("remSleepSeconds")) or 0,
            "light_sleep_min": _to_min(sleep_dto.get("lightSleepSeconds")) or 0,
            "awake_min": _to_min(sleep_dto.get("awakeSleepSeconds")) or 0,
            "sleep_score": (scores.get("overall", {}) or {}).get("value"),
            "sleep_score_level": (scores.get("overall", {}) or {}).get("qualifierKey"),
        },
        "recovery_status": {
            "avg_sleep_hr": sleep_dto.get("avgHeartRate"),
            "resting_hr": (stats or {}).get("restingHeartRate"),
            "resting_hr_7d_avg": (stats or {}).get("lastSevenDaysAvgRestingHeartRate"),
            "hrv_last_night": hrv_summary.get("lastNightAvg"),
            "hrv_weekly_avg": hrv_summary.get("weeklyAvg"),
            "hrv_status": hrv_summary.get("status"),
            "hrv_baseline_low": (hrv_summary.get("baseline", {}) or {}).get("balancedLow"),
            "hrv_baseline_high": (hrv_summary.get("baseline", {}) or {}).get("balancedUpper"),
            "avg_sleep_stress": sleep_dto.get("avgSleepStress"),
            "awake_count": sleep_dto.get("awakeCount"),
            "avg_spo2": sleep_dto.get("averageSpO2Value"),
            "lowest_spo2": sleep_dto.get("lowestSpO2Value"),
            "body_battery_at_wake": (stats or {}).get("bodyBatteryAtWakeTime"),
            "body_battery_charged": (stats or {}).get("bodyBatteryChargedValue"),
            "body_battery_current": (stats or {}).get("bodyBatteryMostRecentValue"),
        },
        "continuous_trends": {
            "sleep_need_baseline_min": sleep_need.get("baseline"),
            "sleep_need_actual_min": sleep_need.get("actual"),
            "sleep_need_feedback": sleep_need.get("feedback"),
            "next_sleep_need_min": next_sleep_need.get("actual"),
            "average_stress_level": (stats or {}).get("averageStressLevel"),
            "body_battery_highest": (stats or {}).get("bodyBatteryHighestValue"),
            "body_battery_lowest": (stats or {}).get("bodyBatteryLowestValue"),
            "body_battery_during_sleep": (stats or {}).get("bodyBatteryDuringSleep"),
            "stress_percentage": (stats or {}).get("stressPercentage"),
            "rest_stress_percentage": (stats or {}).get("restStressPercentage"),
        },
        "sleep_coach": {
            "baseline_need_min": sleep_need.get("baseline"),
            "actual_need_min": sleep_need.get("actual"),
            "actual_vs_baseline_min": _diff_minutes(sleep_need.get("actual"), sleep_need.get("baseline")),
            "feedback": sleep_need.get("feedback"),
            "next_need_min": next_sleep_need.get("actual"),
            "next_vs_baseline_min": _diff_minutes(next_sleep_need.get("actual"), sleep_need.get("baseline")),
            "target_need_min": next_sleep_need.get("actual") or sleep_need.get("actual"),
            "target_vs_baseline_min": _diff_minutes(
                next_sleep_need.get("actual") or sleep_need.get("actual"),
                sleep_need.get("baseline"),
            ),
            "next_feedback": next_sleep_need.get("feedback"),
        },
        "auxiliary": {
            "avg_respiration": sleep_dto.get("averageRespirationValue"),
            "lowest_respiration": sleep_dto.get("lowestRespirationValue"),
            "highest_respiration": sleep_dto.get("highestRespirationValue"),
            "sleep_feedback_code": sleep_dto.get("sleepScoreFeedback"),
            "sleep_personalized_insight": sleep_dto.get("sleepScorePersonalizedInsight"),
            "dynamic_feedback": body_battery_event.get("bodyBatteryLevel"),
        },
    }
