"""Rule summaries and llm payload builders for the Garmin assistant."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from garmin_storage import average, median_value, recent_values


BJ_TZ = timezone(timedelta(hours=8))


def get_greeting(now: datetime | None = None) -> str:
    now = now or datetime.now(BJ_TZ)
    hour = now.hour
    if 5 <= hour <= 10:
        return "早上好"
    if 11 <= hour <= 13:
        return "中午好"
    if 14 <= hour <= 17:
        return "下午好"
    return "晚上好"


def build_salutation(greeting: str, user_name: str | None) -> str:
    if user_name:
        return f"{greeting}，{user_name}。"
    return f"{greeting}。"


def _round(value, digits=1):
    if value in (None, ""):
        return None
    return round(float(value), digits)


def _ratio_text(current, baseline, lower=0.9, upper=1.1):
    if current is None or baseline in (None, 0):
        return "未知"
    ratio = current / baseline
    if ratio >= upper:
        return "偏高"
    if ratio <= lower:
        return "偏低"
    return "接近常态"


def _format_minutes_text(total_minutes):
    if total_minutes in (None, ""):
        return None
    minutes = int(round(float(total_minutes)))
    hours, remain = divmod(minutes, 60)
    if hours <= 0:
        return f"{remain}分钟"
    if remain == 0:
        return f"{hours}小时"
    return f"{hours}小时{remain}分钟"


def _has_logged_water_intake(load: dict | None) -> bool:
    if not isinstance(load, dict):
        return False
    water_consumed = load.get("water_consumed_ml")
    return isinstance(water_consumed, (int, float)) and water_consumed > 0


def _has_significant_stop_break(basic: dict | None) -> bool:
    if not isinstance(basic, dict):
        return False
    stop_duration = basic.get("stop_duration_min")
    duration = basic.get("duration_min")
    if not isinstance(stop_duration, (int, float)) or not isinstance(duration, (int, float)) or duration <= 0:
        return False

    sport_type = str(basic.get("sport_type") or "").lower()
    if sport_type in {"walking", "hiking", "trail_running", "mountaineering", "snowshoeing"}:
        absolute_threshold = 35
        ratio_threshold = 0.22
    elif sport_type in {"cycling", "road_biking", "mountain_biking", "indoor_cycling", "running"}:
        absolute_threshold = 25
        ratio_threshold = 0.18
    else:
        absolute_threshold = 30
        ratio_threshold = 0.20

    return stop_duration >= absolute_threshold and (stop_duration / duration) >= ratio_threshold


def _unique_texts(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _recent_alerts_from_package(package: dict | None, field_names: tuple[str, ...]) -> list[dict]:
    if not package:
        return []
    llm_payload = package.get("llm_payload") or {}
    alerts = []
    for field_name in field_names:
        value = llm_payload.get(field_name) or []
        if not isinstance(value, list):
            continue
        alerts.extend(item for item in value if isinstance(item, dict))
    unique_alerts = []
    seen_keys = set()
    for alert in alerts:
        key = alert.get("rule") or alert.get("title")
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        unique_alerts.append(alert)
    return unique_alerts


SLEEP_ALERT_MEMORY_MAP = {
    "lowest_spo2_low": "上次提到最低血氧偏低",
    "awake_count_high": "上次提到夜间清醒偏多",
    "avg_sleep_stress_high": "上次提到夜间压力偏高",
    "hrv_below_baseline": "上次提到恢复压力偏大",
    "resting_hr_above_7d_avg": "上次提到静息心率偏高",
    "body_battery_at_wake_low": "上次提到早上恢复不够",
}


ACTIVITY_ALERT_MEMORY_MAP = {
    "hydration_gap_high": "上次提到补水缺口明显",
    "late_fade": "上次提到后半程衰减明显",
    "left_right_balance_off": "上次提到左右发力有偏差",
    "training_load_high": "上次提到训练负荷偏重",
    "long_rest_stop": "上次提到中途停留偏长",
    "cadence_low": "上次提到踏频偏低",
    "hr_power_relation_anomaly": "上次提到心率和功率关系不太顺",
}


def _alert_memory_phrases(package: dict | None, mapping: dict[str, str], field_names: tuple[str, ...]) -> list[str]:
    phrases = []
    for alert in _recent_alerts_from_package(package, field_names):
        rule = alert.get("rule") or ""
        title = alert.get("title") or ""
        phrases.append(mapping.get(rule) or (f"上次提到{title}" if title else ""))
    return _unique_texts(phrases)


def _sleep_coach_guidance(normalized: dict) -> dict:
    coach = normalized.get("sleep_coach") or {}
    baseline_need = coach.get("baseline_need_min")
    actual_need = coach.get("actual_need_min")
    next_need = coach.get("next_need_min")
    target_need = next_need or actual_need
    if target_need in (None, "") and baseline_need in (None, ""):
        return {}

    delta_from_baseline = coach.get("target_vs_baseline_min")
    need_status = "normal"
    need_trend_summary = ""
    if delta_from_baseline is not None:
        if delta_from_baseline >= 25:
            need_status = "higher_than_baseline"
            need_trend_summary = (
                f"结合你当前偏高的恢复需求，今晚更合适的睡眠目标是 {_format_minutes_text(target_need)} 左右。"
            )
        elif delta_from_baseline <= -20:
            need_status = "lower_than_baseline"
            need_trend_summary = (
                f"今晚给到你的睡眠建议是：尽量保证 {_format_minutes_text(target_need)} 左右的睡眠，当前恢复需求没有额外抬高。"
            )
        else:
            need_trend_summary = (
                f"今晚给到你的睡眠建议是：尽量保证 {_format_minutes_text(target_need)} 左右的睡眠，这会更有利于把当前的恢复做完整。"
            )
    elif target_need not in (None, ""):
        need_trend_summary = (
            f"今晚给到你的睡眠建议是：尽量保证 {_format_minutes_text(target_need)} 左右的睡眠，这会更有利于把当前的恢复做完整。"
        )

    return {
        "baseline_need_min": baseline_need,
        "actual_need_min": actual_need,
        "next_need_min": next_need,
        "target_need_min": target_need,
        "target_need_text": _format_minutes_text(target_need),
        "need_feedback": coach.get("feedback"),
        "next_need_feedback": coach.get("next_feedback"),
        "need_status": need_status,
        "target_vs_baseline_min": delta_from_baseline,
        "need_trend_summary": need_trend_summary,
    }


def _sleep_continuity_context(
    normalized: dict,
    recent_history: list[dict],
    recent_message_packages: list[dict] | None,
    spo2_alert_context: dict | None = None,
) -> dict:
    spo2_alert_context = spo2_alert_context or {}
    recent_message_packages = recent_message_packages or []
    latest_package = recent_message_packages[0] if recent_message_packages else None
    previous_normalized = recent_history[0] if recent_history else ((latest_package or {}).get("normalized_data") or {})
    previous_basic = previous_normalized.get("basic_sleep", {}) if isinstance(previous_normalized, dict) else {}
    previous_recovery = previous_normalized.get("recovery_status", {}) if isinstance(previous_normalized, dict) else {}
    current_basic = normalized.get("basic_sleep", {})
    current_recovery = normalized.get("recovery_status", {})

    recent_issue_memory = _alert_memory_phrases(latest_package, SLEEP_ALERT_MEMORY_MAP, ("forced_alerts",))
    if not recent_issue_memory and previous_basic.get("total_sleep_min") and previous_basic["total_sleep_min"] < 390:
        recent_issue_memory = ["上次提到睡眠时长偏紧"]

    continuity_summary = ""
    recent_slice = [normalized, *recent_history[:2]]
    previous_slice = recent_history[:3]
    if len(recent_slice) >= 3 and len(previous_slice) >= 2:
        recent_score = average(recent_slice, "basic_sleep.sleep_score")
        previous_score = average(previous_slice, "basic_sleep.sleep_score")
        recent_battery = average(recent_slice, "recovery_status.body_battery_at_wake")
        previous_battery = average(previous_slice, "recovery_status.body_battery_at_wake")
        if (
            recent_score is not None
            and previous_score is not None
            and recent_score >= previous_score + 4
        ) or (
            recent_battery is not None
            and previous_battery is not None
            and recent_battery >= previous_battery + 8
        ):
            continuity_summary = "最近3天恢复在慢慢回升。"
        elif (
            recent_score is not None
            and previous_score is not None
            and recent_score <= previous_score - 4
        ) or (
            recent_battery is not None
            and previous_battery is not None
            and recent_battery <= previous_battery - 8
        ):
            continuity_summary = "最近3天恢复有点往下走，还不能太早放松。"
    if not continuity_summary:
        week_slice = [normalized, *recent_history[:6]]
        durations = [
            record.get("basic_sleep", {}).get("total_sleep_min")
            for record in week_slice
            if isinstance(record.get("basic_sleep", {}).get("total_sleep_min"), (int, float))
        ]
        if len(durations) >= 5:
            duration_range = max(durations) - min(durations)
            if duration_range <= 90:
                continuity_summary = "最近一周睡眠比前面更稳一些。"
            elif duration_range >= 150:
                continuity_summary = "最近一周睡眠波动还是偏大。"

    improvement_after_last_advice = ""
    previous_alert_rules = {
        alert.get("rule")
        for alert in _recent_alerts_from_package(latest_package, ("forced_alerts",))
        if alert.get("rule")
    }
    if "lowest_spo2_low" in previous_alert_rules:
        previous_spo2 = previous_recovery.get("lowest_spo2")
        current_spo2 = current_recovery.get("lowest_spo2")
        if isinstance(previous_spo2, (int, float)) and isinstance(current_spo2, (int, float)) and current_spo2 >= max(90, previous_spo2 + 2):
            improvement_after_last_advice = "上次提到最低血氧偏低，这次没有再往下掉。"
    if not improvement_after_last_advice:
        previous_sleep = previous_basic.get("total_sleep_min")
        current_sleep = current_basic.get("total_sleep_min")
        if isinstance(previous_sleep, (int, float)) and isinstance(current_sleep, (int, float)) and current_sleep >= previous_sleep + 40:
            improvement_after_last_advice = "上次提醒先把休息补回来，这两天睡眠时长已经补上来一些。"
    if not improvement_after_last_advice and "awake_count_high" in previous_alert_rules:
        previous_awake = previous_recovery.get("awake_count")
        current_awake = current_recovery.get("awake_count")
        if isinstance(previous_awake, (int, float)) and isinstance(current_awake, (int, float)) and current_awake < previous_awake:
            improvement_after_last_advice = "上次提到夜里容易被打断，这次清醒次数少了一些。"

    if spo2_alert_context.get("guidance_level") == "escalated" and spo2_alert_context.get("continuity_hint"):
        improvement_after_last_advice = ""
        continuity_summary = spo2_alert_context["continuity_hint"]

    continuity_sentence_hint = improvement_after_last_advice or continuity_summary or ""
    if not continuity_sentence_hint and recent_issue_memory:
        continuity_sentence_hint = f"{recent_issue_memory[0]}，这次也还要继续盯着看。"

    return {
        "recent_issue_memory": recent_issue_memory[:2],
        "continuity_summary": continuity_summary,
        "improvement_after_last_advice": improvement_after_last_advice,
        "continuity_sentence_hint": continuity_sentence_hint,
    }


def _activity_continuity_context(
    normalized: dict,
    recent_history: list[dict],
    recent_message_packages: list[dict] | None,
) -> dict:
    recent_message_packages = recent_message_packages or []
    latest_package = recent_message_packages[0] if recent_message_packages else None
    previous_normalized = recent_history[0] if recent_history else ((latest_package or {}).get("normalized_data") or {})
    previous_load = previous_normalized.get("load_recovery", {}) if isinstance(previous_normalized, dict) else {}
    previous_sport = previous_normalized.get("sport_specific", {}) if isinstance(previous_normalized, dict) else {}
    current_load = normalized.get("load_recovery", {})
    current_sport = normalized.get("sport_specific", {})
    current_has_logged_water = _has_logged_water_intake(current_load)
    previous_has_logged_water = _has_logged_water_intake(previous_load)

    recent_issue_memory = _alert_memory_phrases(
        latest_package,
        ACTIVITY_ALERT_MEMORY_MAP,
        ("priority_issues", "forced_alerts"),
    )
    if not (current_has_logged_water and previous_has_logged_water):
        recent_issue_memory = [item for item in recent_issue_memory if "补水" not in str(item)]

    continuity_summary = ""
    recent_loads = [
        record.get("load_recovery", {}).get("training_stress_score")
        for record in [normalized, *recent_history[:2]]
        if isinstance(record.get("load_recovery", {}).get("training_stress_score"), (int, float))
    ]
    if len(recent_loads) >= 3:
        top_load = max(recent_loads)
        if sum(recent_loads) and top_load / sum(recent_loads) >= 0.5:
            continuity_summary = "最近几次训练负荷还是偏集中。"
        elif max(recent_loads) - min(recent_loads) <= 25:
            continuity_summary = "最近几次训练节奏更连贯了一些。"

    improvement_after_last_advice = ""
    previous_alert_rules = {
        alert.get("rule")
        for alert in _recent_alerts_from_package(latest_package, ("priority_issues", "forced_alerts"))
        if alert.get("rule")
    }
    current_hydration_gap = current_sport.get("hydration_gap_ml")
    previous_hydration_gap = previous_sport.get("hydration_gap_ml")
    if (
        current_has_logged_water
        and previous_has_logged_water
        and "hydration_gap_high" in previous_alert_rules
        and isinstance(current_hydration_gap, (int, float))
        and isinstance(previous_hydration_gap, (int, float))
        and current_hydration_gap <= previous_hydration_gap - 150
    ):
        improvement_after_last_advice = "上次提到补水缺口明显，这次补水记录看起来更完整了一些。"
    elif current_sport.get("pacing_flag") != "late_fade" and previous_sport.get("pacing_flag") == "late_fade":
        improvement_after_last_advice = "上次提到后半程衰减明显，这次后半程掉速没那么明显。"
    if not improvement_after_last_advice and "left_right_balance_off" in previous_alert_rules:
        current_balance = current_sport.get("balance_offset_pct")
        previous_balance = previous_sport.get("balance_offset_pct")
        if isinstance(current_balance, (int, float)) and isinstance(previous_balance, (int, float)) and current_balance <= previous_balance - 2:
            improvement_after_last_advice = "上次提到左右发力有点偏，这次两侧更接近了一些。"
    if not improvement_after_last_advice:
        current_tss = current_load.get("training_stress_score")
        previous_tss = previous_load.get("training_stress_score")
        if isinstance(current_tss, (int, float)) and isinstance(previous_tss, (int, float)) and abs(current_tss - previous_tss) <= 15:
            continuity_summary = continuity_summary or "最近几次训练负荷的节奏比较接近。"

    continuity_sentence_hint = improvement_after_last_advice or continuity_summary or ""
    if not continuity_sentence_hint and recent_issue_memory:
        continuity_sentence_hint = f"{recent_issue_memory[0]}，这次也适合继续对着看。"

    return {
        "recent_issue_memory": recent_issue_memory[:2],
        "continuity_summary": continuity_summary,
        "improvement_after_last_advice": improvement_after_last_advice,
        "continuity_sentence_hint": continuity_sentence_hint,
    }


def _vs_average_text(current, avg_value, *, delta_ratio=0.1, delta_abs=None):
    if current in (None, "") or avg_value in (None, "", 0):
        return "未知"
    current = float(current)
    avg_value = float(avg_value)
    if delta_abs is not None:
        if current >= avg_value + delta_abs:
            return "高于30天常态"
        if current <= avg_value - delta_abs:
            return "低于30天常态"
        return "接近30天常态"
    if current >= avg_value * (1 + delta_ratio):
        return "高于30天常态"
    if current <= avg_value * (1 - delta_ratio):
        return "低于30天常态"
    return "接近30天常态"


def _sleep_baseline_view(normalized: dict, user_baseline: dict | None) -> dict | None:
    if not user_baseline:
        return None
    sleep_baseline = user_baseline.get("sleep_recovery_baseline") or {}
    if not sleep_baseline:
        return None

    basic = normalized.get("basic_sleep", {})
    recovery = normalized.get("recovery_status", {})
    duration_vs = _vs_average_text(basic.get("total_sleep_min"), sleep_baseline.get("avg_sleep_min"), delta_abs=40)
    score_vs = _vs_average_text(basic.get("sleep_score"), sleep_baseline.get("avg_sleep_score"), delta_abs=5)
    hrv_vs = _vs_average_text(recovery.get("hrv_last_night"), sleep_baseline.get("avg_hrv"), delta_abs=6)
    battery_vs = _vs_average_text(
        recovery.get("body_battery_at_wake"),
        sleep_baseline.get("avg_body_battery_at_wake"),
        delta_abs=8,
    )
    baseline_summary = (
        f"按你最近30天的睡眠恢复基线看，这晚睡眠时长{duration_vs}，"
        f"睡眠评分{score_vs}，HRV {hrv_vs}，起床 body battery {battery_vs}。"
    )
    return {
        "sleep_duration_vs_30d": duration_vs,
        "sleep_score_vs_30d": score_vs,
        "hrv_vs_30d": hrv_vs,
        "body_battery_vs_30d": battery_vs,
        "sleep_recovery_baseline": sleep_baseline,
        "baseline_summary": baseline_summary,
    }


def _activity_baseline_view(normalized: dict, user_baseline: dict | None) -> dict | None:
    if not user_baseline:
        return None
    general_baseline = user_baseline.get("general_sport_baseline") or {}
    main_positioning = user_baseline.get("main_sport_positioning") or {}
    cycling_baseline = user_baseline.get("cycling_specific_baseline") or {}

    basic = normalized.get("basic_activity", {})
    load = normalized.get("load_recovery", {})
    sport = normalized.get("sport_specific", {})
    duration_vs = _vs_average_text(
        basic.get("duration_min"),
        cycling_baseline.get("avg_duration_min") or general_baseline.get("avg_duration_min"),
        delta_abs=20,
    )
    load_vs = _vs_average_text(
        load.get("training_stress_score"),
        cycling_baseline.get("avg_training_stress_score") or general_baseline.get("avg_training_stress_score"),
        delta_abs=20,
    )
    if_vs = _vs_average_text(
        sport.get("intensity_factor"),
        cycling_baseline.get("avg_intensity_factor"),
        delta_abs=0.06,
    )
    baseline_summary = (
        f"按你最近30天的运动基线看，这次时长{duration_vs}，"
        f"训练负荷{load_vs}，专项强度 {if_vs}。"
    )
    return {
        "general_sport_baseline": general_baseline,
        "main_sport_positioning": main_positioning,
        "cycling_specific_baseline": cycling_baseline if cycling_baseline.get("enabled") else None,
        "session_duration_vs_30d": duration_vs,
        "session_load_vs_30d": load_vs,
        "session_intensity_vs_30d": if_vs,
        "baseline_summary": baseline_summary,
    }

def _sleep_spo2_alert_context(normalized: dict, recent_history: list[dict]) -> dict:
    week_slice = [normalized, *recent_history[:6]]
    low_records = []
    for record in week_slice:
        recovery = record.get("recovery_status", {}) if isinstance(record, dict) else {}
        lowest_spo2 = recovery.get("lowest_spo2") if isinstance(recovery, dict) else None
        low_records.append(
            {
                "lowest_spo2": lowest_spo2,
                "is_low": isinstance(lowest_spo2, (int, float)) and lowest_spo2 < 90,
            }
        )

    recent_week_low_nights = sum(1 for item in low_records if item["is_low"])
    consecutive_low_nights = 0
    for item in low_records:
        if item["is_low"]:
            consecutive_low_nights += 1
        else:
            break

    is_current_low = bool(low_records and low_records[0]["is_low"])
    is_consecutive_abnormal = consecutive_low_nights >= 2
    is_recurrent_low = recent_week_low_nights >= 3 or is_consecutive_abnormal

    continuity_hint = ""
    if is_current_low and is_recurrent_low:
        continuity_hint = "接下来几天把这项信号持续盯住，尤其留意它会不会和打鼾、憋醒或白天困倦一起出现。"

    guidance_level = "normal"
    escalation_advice = ""
    if is_current_low and is_recurrent_low:
        guidance_level = "escalated"
        escalation_advice = (
            "这次不要只写继续观察，建议明确提醒用户留意打鼾、憋醒或喘醒、白天困倦、晨起头痛这些伴随表现。"
        )
    elif is_current_low:
        guidance_level = "observe"
        escalation_advice = "这次按偶发低血氧处理，可以继续给观察建议。"

    return {
        "recent_week_low_nights": recent_week_low_nights,
        "consecutive_low_nights": consecutive_low_nights,
        "is_consecutive_abnormal": is_consecutive_abnormal,
        "is_recurrent_low": is_recurrent_low,
        "guidance_level": guidance_level,
        "template_mode": (
            "persistent_issue"
            if guidance_level == "escalated"
            else "single_night_issue" if guidance_level == "observe" else ""
        ),
        "recent_occurrence_text": (
            f"最近一周已经第{recent_week_low_nights}次出现"
            if guidance_level == "escalated" and recent_week_low_nights
            else ""
        ),
        "consecutive_occurrence_text": (
            f"已经连续第{consecutive_low_nights}晚出现"
            if guidance_level == "escalated" and consecutive_low_nights >= 2
            else ""
        ),
        "possible_cause": "可能和夜间呼吸受阻有关" if guidance_level == "escalated" else "",
        "symptom_watch_items": ["打鼾", "憋醒/喘醒", "白天困倦", "晨起头痛"] if guidance_level == "escalated" else [],
        "continuity_hint": continuity_hint,
        "escalation_advice": escalation_advice,
    }


def _sleep_forced_alerts(
    normalized: dict,
    recent_history: list[dict],
    spo2_alert_context: dict | None = None,
) -> list[dict]:
    recovery = normalized["recovery_status"]
    recent_avg_awake = average(recent_history, "recovery_status.awake_count")
    recent_avg_hrv = average(recent_history, "recovery_status.hrv_last_night")
    recent_avg_battery = average(recent_history, "recovery_status.body_battery_at_wake")
    spo2_alert_context = spo2_alert_context or _sleep_spo2_alert_context(normalized, recent_history)

    alerts = []
    if (recovery.get("lowest_spo2") or 100) < 90:
        if spo2_alert_context.get("guidance_level") == "escalated":
            title = "最低血氧连续偏低"
            impact = f"说明这项变化更像持续出现的信号，{spo2_alert_context.get('possible_cause')}。"
            advice = (
                "这几天不要再只按单晚异常处理。除了继续留意睡姿、鼻部通畅和卧室通风，"
                "也建议顺手留意有没有打鼾、憋醒或喘醒、白天困倦、晨起头痛；"
                "如果这些情况也反复出现，建议考虑做一次睡眠呼吸相关检查或咨询医生。"
            )
            severity = 6
        else:
            title = "最低血氧偏低"
            impact = "说明夜里出现过一次短时血氧下探，先按偶发波动看，但不适合直接忽略。"
            advice = "今晚把睡姿、鼻部通畅和卧室通风顺手盯一下，看看后面几晚还有没有再掉下去。"
            severity = 5
        alerts.append(
            {
                "rule": "lowest_spo2_low",
                "title": title,
                "evidence": f"最低血氧 {recovery.get('lowest_spo2')}%。",
                "impact": impact,
                "advice": advice,
                "severity": severity,
                "guidance_level": spo2_alert_context.get("guidance_level"),
                "recent_week_low_nights": spo2_alert_context.get("recent_week_low_nights"),
                "consecutive_low_nights": spo2_alert_context.get("consecutive_low_nights"),
                "is_consecutive_abnormal": spo2_alert_context.get("is_consecutive_abnormal"),
                "possible_cause": spo2_alert_context.get("possible_cause"),
                "symptom_watch_items": spo2_alert_context.get("symptom_watch_items"),
                "escalation_advice": spo2_alert_context.get("escalation_advice"),
            }
        )
    if recovery.get("awake_count") is not None:
        awake_threshold = 3
        if recent_avg_awake is not None:
            awake_threshold = max(awake_threshold, int(round(recent_avg_awake + 1)))
        if recovery["awake_count"] >= awake_threshold:
            alerts.append(
                {
                    "rule": "awake_count_high",
                    "title": "夜间清醒次数偏多",
                    "evidence": f"夜间清醒 {recovery.get('awake_count')} 次。",
                    "impact": "睡眠会被切碎，恢复连续性容易被打断。",
                    "advice": "今晚优先压低睡前兴奋度，避免太晚进食和继续刷屏，让后半夜更完整。",
                    "severity": 4,
                }
            )
    if (recovery.get("avg_sleep_stress") or 0) >= 25:
        alerts.append(
            {
                "rule": "avg_sleep_stress_high",
                "title": "夜间压力偏高",
                "evidence": f"夜间平均压力 {recovery.get('avg_sleep_stress')}。",
                "impact": "说明虽然睡着了，但身体没有完全进入轻松修复状态。",
                "advice": "今天别把训练强度往上推，晚上把恢复顺序放前面，尤其是晚饭后别再拉高兴奋度。",
                "severity": 4,
            }
        )
    hrv_last_night = recovery.get("hrv_last_night")
    baseline_low = recovery.get("hrv_baseline_low")
    weekly_avg = recovery.get("hrv_weekly_avg")
    if hrv_last_night is not None:
        hrv_is_low = False
        if baseline_low is not None and hrv_last_night < baseline_low:
            hrv_is_low = True
        if recent_avg_hrv is not None and hrv_last_night <= recent_avg_hrv - 7:
            hrv_is_low = True
        if weekly_avg is not None and hrv_last_night <= weekly_avg - 7:
            hrv_is_low = True
        if hrv_is_low:
            alerts.append(
                {
                    "rule": "hrv_below_baseline",
                    "title": "HRV 明显低于基线",
                    "evidence": (
                        f"HRV {hrv_last_night}，低于平衡下沿 {baseline_low}。"
                        if baseline_low is not None
                        else f"HRV {hrv_last_night}，明显低于近期均值。"
                    ),
                    "impact": "恢复系统的余量没有平时充足，今天不适合硬顶强度。",
                    "advice": "今天把重点放在恢复和节奏控制，尤其别临时加一段高强度。",
                    "severity": 5,
                }
            )
    resting_hr = recovery.get("resting_hr")
    resting_hr_avg = recovery.get("resting_hr_7d_avg")
    if resting_hr is not None and resting_hr_avg is not None and resting_hr >= resting_hr_avg + 3:
        alerts.append(
            {
                "rule": "resting_hr_above_7d_avg",
                "title": "静息心率高于近 7 天均值",
                "evidence": f"静息心率 {resting_hr}，近 7 天均值 {resting_hr_avg}。",
                "impact": "通常意味着身体还没有完全回到轻松状态。",
                "advice": "今天把训练上限压住，先看身体对日间活动的反馈，不要一上来就冲。",
                "severity": 4,
            }
        )
    battery_at_wake = recovery.get("body_battery_at_wake")
    if battery_at_wake is not None:
        battery_is_low = battery_at_wake < 55
        if recent_avg_battery is not None and battery_at_wake <= recent_avg_battery - 15:
            battery_is_low = True
        if battery_is_low:
            alerts.append(
                {
                    "rule": "body_battery_at_wake_low",
                    "title": "起床体能恢复不足",
                    "evidence": f"起床 body battery {battery_at_wake}。",
                    "impact": "说明这一晚恢复没能把电量明显补满。",
                    "advice": "今天优先把强度换成稳态和恢复，把高刺激留给身体更满的时候。",
                    "severity": 5,
                }
            )
    return sorted(alerts, key=lambda item: item["severity"], reverse=True)


def _sleep_alert_opening_hint(alert: dict, recovery: dict | None = None) -> str:
    recovery = recovery or {}
    rule = alert.get("rule")
    if rule == "lowest_spo2_low":
        lowest_spo2 = recovery.get("lowest_spo2")
        spo2_text = (
            f"最低血氧降到{int(round(float(lowest_spo2)))}%"
            if isinstance(lowest_spo2, (int, float))
            else "最低血氧偏低"
        )
        if alert.get("guidance_level") == "escalated":
            recent_week_low_nights = alert.get("recent_week_low_nights")
            consecutive_low_nights = alert.get("consecutive_low_nights")
            if recent_week_low_nights and consecutive_low_nights and consecutive_low_nights >= 2:
                return (
                    f"{spo2_text}，最近一周已经第{recent_week_low_nights}次出现，"
                    f"而且已经连续第{consecutive_low_nights}晚偏低"
                )
            if recent_week_low_nights:
                return f"{spo2_text}，最近一周已经第{recent_week_low_nights}次出现"
            return f"{spo2_text}，这次不能再只当作单次波动"
        return f"{spo2_text}，这个点今晚仍然值得留意"
    if rule == "hrv_below_baseline":
        return "HRV 这项恢复指标压在了低位"
    if rule == "awake_count_high":
        return "夜里被打断得有点多"
    if rule == "avg_sleep_stress_high":
        return "夜里身体一直没完全放松下来"
    if rule == "resting_hr_above_7d_avg":
        return "静息心率比最近几天偏高"
    if rule == "body_battery_at_wake_low":
        return "起床时电量没补到位"
    title = alert.get("title") or "这项提醒"
    return f"{title}这点要单独拎出来看"


def build_sleep_payload(
    user_name: str,
    normalized: dict,
    recent_history: list[dict],
    user_baseline: dict | None = None,
    recent_message_packages: list[dict] | None = None,
) -> dict:
    basic = normalized["basic_sleep"]
    recovery = normalized["recovery_status"]
    trends = normalized["continuous_trends"]
    aux = normalized["auxiliary"]
    total_sleep_text = _format_minutes_text(basic.get("total_sleep_min"))
    deep_sleep_text = _format_minutes_text(basic.get("deep_sleep_min"))
    rem_sleep_text = _format_minutes_text(basic.get("rem_sleep_min"))
    light_sleep_text = _format_minutes_text(basic.get("light_sleep_min"))
    awake_text = _format_minutes_text(basic.get("awake_min"))

    recent_avg_duration = average(recent_history, "basic_sleep.total_sleep_min")
    recent_avg_score = average(recent_history, "basic_sleep.sleep_score")
    recent_avg_hrv = average(recent_history, "recovery_status.hrv_last_night")
    recent_avg_battery = average(recent_history, "recovery_status.body_battery_at_wake")
    spo2_alert_context = _sleep_spo2_alert_context(normalized, recent_history)
    forced_alerts = _sleep_forced_alerts(normalized, recent_history, spo2_alert_context)
    baseline_view = _sleep_baseline_view(normalized, user_baseline)
    continuity_context = _sleep_continuity_context(
        normalized,
        recent_history,
        recent_message_packages,
        spo2_alert_context,
    )
    sleep_coach_guidance = _sleep_coach_guidance(normalized)

    strong_recovery = (
        (basic.get("sleep_score") or 0) >= 80
        and (basic.get("total_sleep_min") or 0) >= 450
        and (recovery.get("body_battery_at_wake") or 0) >= 75
    )
    top_alert_opening = _sleep_alert_opening_hint(forced_alerts[0], recovery) if forced_alerts else ""
    if strong_recovery and not forced_alerts:
        overall_judgement = "昨晚整体恢复是偏好的，今天的底子比较稳。"
    elif strong_recovery and forced_alerts:
        overall_judgement = f"昨晚整体恢复不错，但{top_alert_opening}。"
    elif (basic.get("sleep_score") or 0) < 70 or (basic.get("total_sleep_min") or 0) < 390:
        if forced_alerts:
            overall_judgement = f"昨晚恢复没有完全拉起来，而且{top_alert_opening}。"
        else:
            overall_judgement = "昨晚恢复没有完全拉起来，今天更适合把节奏收住。"
    else:
        if forced_alerts:
            overall_judgement = f"昨晚恢复整体是正向的，不过{top_alert_opening}。"
        else:
            overall_judgement = "昨晚恢复中等偏稳，不算糟，但也不是完全无忧。"

    reason_points = []
    if basic.get("total_sleep_min") is not None:
        reason_points.append(
            f"总睡眠 {total_sleep_text}，睡眠评分 {basic.get('sleep_score')}。"
        )
    if basic.get("deep_sleep_min") is not None and basic.get("rem_sleep_min") is not None:
        reason_points.append(
            f"深睡 {deep_sleep_text}，REM {rem_sleep_text}，结构算完整。"
        )
    if recovery.get("hrv_last_night") is not None:
        if recovery.get("hrv_baseline_low") is not None and recovery.get("hrv_baseline_high") is not None:
            reason_points.append(
                f"HRV {recovery.get('hrv_last_night')}，你的平衡区间大约在 {recovery.get('hrv_baseline_low')}-{recovery.get('hrv_baseline_high')}。"
            )
        else:
            reason_points.append(
                f"HRV {recovery.get('hrv_last_night')}，接近周均 {recovery.get('hrv_weekly_avg')}。"
            )
    if recovery.get("body_battery_at_wake") is not None:
        reason_points.append(
            f"起床时身体电量恢复到 {recovery.get('body_battery_at_wake')}。"
        )
    if baseline_view:
        reason_points.append(baseline_view["baseline_summary"])
    reason_points = reason_points[:4]

    if (recovery.get("body_battery_at_wake") or 0) >= 85:
        highlight_point = (
            f"起床时身体电量恢复到 {recovery.get('body_battery_at_wake')}，"
            "说明这一晚的恢复质量是比较扎实的。"
        )
    elif (basic.get("deep_sleep_min") or 0) >= 100:
        highlight_point = f"这晚的亮点是深睡拉到了 {deep_sleep_text}，修复质量是够的。"
    else:
        highlight_point = "这晚最大的好消息是，睡眠结构没有散，恢复基础还在。"

    if forced_alerts:
        issue_point = (
            f"{forced_alerts[0]['title']}是这晚最需要单独盯住的一点，"
            f"{forced_alerts[0]['evidence']} {forced_alerts[0]['impact']}"
        )
        bound_advice = forced_alerts[0]["advice"]
    elif (recovery.get("avg_sleep_stress") or 0) >= 20:
        issue_point = f"问题点在于夜间平均压力还有 {recovery.get('avg_sleep_stress')}，恢复不是完全放松型。"
        bound_advice = "今天别把自己安排得太满，尤其别临时叠加强度或晚间再拉高兴奋度。"
    else:
        issue_point = "问题点不算突出，但这不代表今天适合把恢复账户一次性花掉。"
        bound_advice = "今天更适合顺着状态往下走，训练和工作都别突然超配。"

    if recent_avg_duration and basic.get("total_sleep_min") is not None:
        if basic["total_sleep_min"] >= recent_avg_duration + 40:
            trend_summary = "和最近一周相比，这晚睡得更足，恢复是往回补的。"
        elif basic["total_sleep_min"] <= recent_avg_duration - 40:
            trend_summary = "和最近一周相比，这晚时长偏短，恢复余量还没有往上走。"
        else:
            trend_summary = "和最近一周相比，这晚整体还算稳定，没有明显掉出常态。"
    elif recovery.get("hrv_weekly_avg") is not None and recovery.get("hrv_last_night") is not None:
        if recovery["hrv_last_night"] < recovery["hrv_weekly_avg"] - 5:
            trend_summary = "放到最近一周看，恢复需求还是偏高一些。"
        else:
            trend_summary = "放到最近一周看，你的恢复还在自己惯常的区间里。"
    else:
        trend_summary = "最近几天的连续背景还在积累中，这晚可以先当作稳定样本。"
    if continuity_context.get("continuity_sentence_hint"):
        trend_summary = continuity_context["continuity_sentence_hint"]
    if spo2_alert_context.get("guidance_level") == "observe":
        trend_summary = "整体来看，这晚恢复还算稳定，但最低血氧这一下探，今晚仍然值得留意。"
    elif spo2_alert_context.get("guidance_level") == "escalated":
        trend_summary = "接下来几天把这项信号持续盯住，尤其留意它会不会和打鼾、憋醒或白天困倦一起出现。"

    if sleep_coach_guidance.get("need_trend_summary"):
        bound_advice = f"{bound_advice} {sleep_coach_guidance['need_trend_summary']}"

    if forced_alerts:
        advice_focus = "problem_first"
    elif strong_recovery:
        advice_focus = "keep_steady"
    elif (basic.get("total_sleep_min") or 0) < 390:
        advice_focus = "recovery_priority"
    else:
        advice_focus = "normal_day"

    greeting = get_greeting()
    if greeting == "早上好":
        salutation = build_salutation(greeting, user_name)
        preferred_opening = f"{salutation}\n\n{overall_judgement}"
    else:
        salutation = f"{user_name}，" if user_name else ""
        preferred_opening = f"{user_name}，{overall_judgement}" if user_name else overall_judgement

    must_mention = [alert["title"] for alert in forced_alerts]
    return {
        "message_type": "sleep_morning",
        "date": normalized["date"],
        "greeting": greeting,
        "user_name": user_name,
        "salutation": salutation,
        "preferred_opening": preferred_opening,
        "length_target": "400-650字，最多650字",
        "overall_judgement": overall_judgement,
        "reason_points": reason_points,
        "highlight_point": highlight_point,
        "issue_point": issue_point,
        "issue_bound_advice": bound_advice,
        "trend_summary": trend_summary,
        "continuity_context": continuity_context,
        "spo2_alert_context": spo2_alert_context,
        "sleep_coach_guidance": sleep_coach_guidance,
        "today_advice_focus": advice_focus,
        "forced_alerts": forced_alerts,
        "must_mention": must_mention,
        "baseline_view": baseline_view,
        "key_metrics": {
            "total_sleep_min": basic.get("total_sleep_min"),
            "total_sleep_text": total_sleep_text,
            "sleep_score": basic.get("sleep_score"),
            "deep_sleep_min": basic.get("deep_sleep_min"),
            "deep_sleep_text": deep_sleep_text,
            "rem_sleep_min": basic.get("rem_sleep_min"),
            "rem_sleep_text": rem_sleep_text,
            "light_sleep_min": basic.get("light_sleep_min"),
            "light_sleep_text": light_sleep_text,
            "awake_min": basic.get("awake_min"),
            "awake_text": awake_text,
            "awake_count": recovery.get("awake_count"),
            "avg_sleep_stress": recovery.get("avg_sleep_stress"),
            "hrv_last_night": recovery.get("hrv_last_night"),
            "hrv_weekly_avg": recovery.get("hrv_weekly_avg"),
            "resting_hr": recovery.get("resting_hr"),
            "resting_hr_7d_avg": recovery.get("resting_hr_7d_avg"),
            "body_battery_at_wake": recovery.get("body_battery_at_wake"),
            "lowest_spo2": recovery.get("lowest_spo2"),
            "sleep_need_actual_min": trends.get("sleep_need_actual_min"),
        },
        "context": {
            "recent_avg_sleep_min": recent_avg_duration,
            "recent_avg_sleep_score": recent_avg_score,
            "recent_avg_hrv": recent_avg_hrv,
            "recent_avg_body_battery_at_wake": recent_avg_battery,
            "sleep_feedback_code": aux.get("sleep_feedback_code"),
            "sleep_personalized_insight": aux.get("sleep_personalized_insight"),
            "dynamic_feedback": aux.get("dynamic_feedback"),
        },
    }


def _zone_summary(values: list[float], labels: list[str], endurance_cutoff: int) -> str:
    if not values:
        return "暂无足够区间信息。"
    total = sum(values)
    if total <= 0:
        return "暂无足够区间信息。"
    dominant = [labels[i] for i, value in enumerate(values) if value / total >= 0.18]
    low_share = sum(values[:endurance_cutoff]) / total
    high_share = sum(values[endurance_cutoff:]) / total
    if low_share >= 0.7:
        return f"主体落在{'-'.join(labels[:endurance_cutoff])}，整体更偏耐力稳态。"
    if high_share >= 0.35:
        return f"有比较明确的 {dominant[-1] if dominant else labels[-1]} 以上高强度刺激，强度不算轻。"
    return f"主要分布在{'、'.join(dominant or labels[:endurance_cutoff])}，强度结构比较均衡。"


def _thirds_analysis(splits: list[dict]) -> dict:
    if not splits or len(splits) < 3:
        return {
            "note": "分段样本不多，整体节奏看起来比较平稳。",
            "flag": "stable",
        }

    chunk = max(1, len(splits) // 3)
    thirds = [splits[:chunk], splits[chunk : chunk * 2], splits[chunk * 2 :]]

    def _avg(rows, key):
        nums = [row.get(key) for row in rows if isinstance(row.get(key), (int, float))]
        return sum(nums) / len(nums) if nums else None

    power = [_avg(rows, "averagePower") for rows in thirds]
    hr = [_avg(rows, "averageHR") for rows in thirds]
    speed = [_avg(rows, "averageSpeed") for rows in thirds]

    if power[0] and power[-1] and hr[0] and hr[-1]:
        if power[-1] < power[0] * 0.9 and hr[-1] >= hr[0] * 1.05:
            return {
                "note": "后段心率维持较高但功率明显回落，疲劳迹象比较明确。",
                "flag": "late_fade",
            }
        if speed[0] and speed[-1] and speed[-1] < speed[0] * 0.92:
            return {
                "note": "后程速度和输出一起往下掉，这次后段是有衰减的。",
                "flag": "late_fade",
            }
        if power[-1] >= power[0] * 0.98:
            return {
                "note": "前后段功率差不大，整体节奏比较稳，没有明显后程崩盘。",
                "flag": "stable",
            }
    return {
        "note": "前中后段输出有波动，但还没有到明显崩掉的程度。",
        "flag": "mixed",
    }


def summarize_activity_background(current: dict, recent_history: list[dict]) -> dict:
    load_med = median_value(recent_history, "load_recovery.training_stress_score")
    duration_med = median_value(recent_history, "basic_activity.duration_min")
    intensity_med = median_value(recent_history, "sport_specific.intensity_factor")
    recent_density = len(recent_history[:7])

    load_now = current["load_recovery"].get("training_stress_score")
    duration_now = current["basic_activity"].get("duration_min")
    intensity_now = current.get("sport_specific", {}).get("intensity_factor")
    recent_hard = [
        record
        for record in recent_history[:2]
        if (record.get("load_recovery", {}).get("training_stress_score") or 0) >= 120
        or (record.get("sport_specific", {}).get("intensity_factor") or 0) >= 0.8
    ]

    return {
        "vs_user_typical_load": _ratio_text(load_now, load_med),
        "vs_user_typical_duration": _ratio_text(duration_now, duration_med),
        "vs_user_typical_intensity": _ratio_text(intensity_now, intensity_med),
        "is_above_recent_baseline": bool(load_med and load_now and load_now >= load_med * 1.2),
        "recent_training_density": recent_density,
        "is_back_to_back_hard_day": bool(recent_hard),
        "recent_typical_load": load_med,
        "recent_typical_duration": duration_med,
        "recent_typical_intensity": intensity_med,
    }


def summarize_cycling_specific(full_data: dict, splits: list[dict]) -> dict:
    avg_power = full_data.get("avgPower") or full_data.get("averagePower")
    normalized_power = full_data.get("normPower") or full_data.get("normalizedPower")
    avg_cadence = full_data.get("averageBikingCadenceInRevPerMinute") or full_data.get("averageBikeCadence")
    max_cadence = full_data.get("maxBikingCadenceInRevPerMinute") or full_data.get("maxBikeCadence")
    left_balance = full_data.get("avgLeftBalance")
    hydration_gap = None
    if full_data.get("waterEstimated") is not None and full_data.get("waterConsumed") is not None:
        hydration_gap = round(full_data["waterEstimated"] - full_data["waterConsumed"], 1)

    balance_offset = None
    balance_status = None
    balance_note = None
    if left_balance is not None:
        right_balance = round(100 - left_balance, 2)
        balance_offset = round(abs(left_balance - right_balance), 2)
        if balance_offset <= 4:
            balance_status = "balanced"
            balance_note = "左右发力整体比较均衡。"
        elif balance_offset <= 8:
            balance_status = "mild_left_bias" if left_balance > right_balance else "mild_right_bias"
            balance_note = "左右发力有轻微偏侧，但还在可接受范围。"
        else:
            balance_status = "obvious_left_bias" if left_balance > right_balance else "obvious_right_bias"
            balance_note = "左右发力偏侧比较明显，疲劳时更容易把问题放大。"

    cadence_status = None
    cadence_note = None
    if avg_cadence is not None:
        if avg_cadence < 72:
            cadence_status = "low"
            cadence_note = "平均踏频偏低，腿部扭矩压力会更重。"
        elif avg_cadence < 80:
            cadence_status = "moderate"
            cadence_note = "踏频不高，更偏力量型踩踏。"
        else:
            cadence_status = "steady"
            cadence_note = "踏频保持得还比较顺。"

    power_zones = [full_data.get(f"powerTimeInZone_{i}", 0) or 0 for i in range(1, 8)]
    hr_zones = [full_data.get(f"hrTimeInZone_{i}", 0) or 0 for i in range(1, 6)]
    power_summary = _zone_summary(power_zones, ["Z1", "Z2", "Z3", "Z4", "Z5", "Z6", "Z7"], 3)
    hr_summary = _zone_summary(hr_zones, ["Z1", "Z2", "Z3", "Z4", "Z5"], 3)

    pacing = _thirds_analysis(splits)
    power_variability_ratio = None
    variability_note = None
    if avg_power and normalized_power:
        power_variability_ratio = round(normalized_power / avg_power, 2)
        if power_variability_ratio >= 1.18:
            variability_note = "NP 明显高于平均功率，这次输出波动和冲击段都不少。"
        else:
            variability_note = "NP 和平均功率差距不大，说明整体输出比较稳。"

    hr_power_relation_note = None
    intensity_factor = full_data.get("intensityFactor")
    avg_hr = full_data.get("averageHR")
    max_hr = full_data.get("maxHR")
    if intensity_factor is not None and avg_hr and max_hr:
        hr_ratio = avg_hr / max_hr if max_hr else None
        hr_power_metric_text = f"平均心率 {avg_hr}，最大心率 {max_hr}，IF {round(float(intensity_factor), 2)}"
        if avg_power is not None:
            hr_power_metric_text += f"，平均功率 {avg_power}W"
        if hr_ratio is not None and intensity_factor < 0.72 and hr_ratio >= 0.82:
            hr_power_relation_note = f"{hr_power_metric_text}，心率抬得比功率强度更明显，可能有热、疲劳或恢复不足的影响。"
        elif hr_ratio is not None and intensity_factor >= 0.82 and hr_ratio <= 0.7:
            hr_power_relation_note = f"{hr_power_metric_text}，功率不低但心率响应偏保守，这类情况后面要继续观察。"

    return {
        "avg_power": avg_power,
        "max_power": full_data.get("maxPower"),
        "normalized_power": normalized_power,
        "intensity_factor": intensity_factor,
        "max_20min_power": full_data.get("max20MinPower"),
        "max_20sec_power": full_data.get("maxAvgPower_20"),
        "avg_cadence": avg_cadence,
        "max_cadence": max_cadence,
        "avg_left_balance": left_balance,
        "power_zone_summary": power_summary,
        "hr_zone_summary": hr_summary,
        "hydration_gap_ml": hydration_gap,
        "balance_offset_pct": balance_offset,
        "balance_status": balance_status,
        "pacing_trend": pacing["note"],
        "pacing_flag": pacing["flag"],
        "power_variability_ratio": power_variability_ratio,
        "power_variability_note": variability_note,
        "left_right_balance_note": balance_note,
        "cadence_status": cadence_status,
        "cadence_note": cadence_note,
        "hr_power_relation_note": hr_power_relation_note,
    }


def _activity_forced_alerts(normalized: dict, recent_history: list[dict]) -> list[dict]:
    basic = normalized["basic_activity"]
    load = normalized["load_recovery"]
    sport = normalized.get("sport_specific", {})
    alerts = []

    current_balance_offset = sport.get("balance_offset_pct") or 0
    recent_consecutive_balance = 0
    for record in recent_history[:3]:
        if (record.get("sport_specific", {}).get("balance_offset_pct") or 0) > 6:
            recent_consecutive_balance += 1
        else:
            break
    if current_balance_offset > 6 or (4 <= current_balance_offset <= 6):
        alerts.append(
            {
                "rule": "left_right_balance_off",
                "title": "左右平衡偏差明显",
                "evidence": (
                    f"左侧占比 {sport.get('avg_left_balance')}%，左右差大约 {sport.get('balance_offset_pct')}%。"
                ),
                "impact": "这是值得留意的可优化点，短期未必就是问题，但如果反复出现，单侧发力和疲劳累积要一起看。",
                "advice": "后面几次如果还这样，再重点留意单侧发力和疲劳时的动作稳定，不用这一次就下重结论。",
                "severity": 4 if current_balance_offset > 6 and recent_consecutive_balance >= 2 else (3 if current_balance_offset > 6 else 1),
                "priority_group": 2 if current_balance_offset > 6 else 3,
                "priority_rank": 1,
            }
        )
    if (sport.get("hydration_gap_ml") or 0) >= 300:
        alerts.append(
            {
                "rule": "hydration_gap_high",
                "title": "补水缺口明显",
                "evidence": f"预估补水缺口约 {sport.get('hydration_gap_ml')}ml。",
                "impact": "这种缺口会直接影响后半段输出和恢复体感。",
                "advice": "这次结束后优先把水和电解质补足，下一次同等时长骑行要把补水节奏前置。",
                "severity": 5,
                "priority_group": 1,
                "priority_rank": 3,
            }
        )
    if (
        (load.get("training_stress_score") or 0) >= 150
        or (load.get("activity_training_load") or 0) >= 130
        or (sport.get("intensity_factor") or 0) >= 0.82
    ):
        alerts.append(
            {
                "rule": "training_load_high",
                "title": "强度或负荷显著偏高",
                "evidence": (
                    f"TSS {load.get('training_stress_score')}，训练负荷 {load.get('activity_training_load')}，"
                    f"IF {sport.get('intensity_factor')}。"
                ),
                "impact": "这不是轻松骑，恢复窗口需要主动留出来。",
                "advice": "接下来 24-48 小时把高强度安排往后放，先把补水、碳水和睡眠补齐。",
                "severity": 5,
                "priority_group": 1,
                "priority_rank": 1,
            }
        )
    if sport.get("pacing_flag") == "late_fade":
        alerts.append(
            {
                "rule": "late_fade",
                "title": "后程输出明显下滑",
                "evidence": sport.get("pacing_trend"),
                "impact": "说明后半段的持续输出能力被打断了。",
                "advice": "后面如果想把长骑质量做高，前段保守一点，补给也要更早介入。",
                "severity": 4,
                "priority_group": 1,
                "priority_rank": 4,
            }
        )
    stop_duration = basic.get("stop_duration_min")
    if _has_significant_stop_break(basic):
        stop_duration_text = _format_minutes_text(stop_duration)
        alerts.append(
            {
                "rule": "long_rest_stop",
                "title": "中途停留时间偏长",
                "evidence": f"中途停留约 {stop_duration_text}。",
                "impact": "这让它更接近分段完成的长骑，而不是一堂连续输出的耐力课。",
                "advice": "如果目标是做连续耐力，下次把中途停留压短；如果本来就是休闲长骑，这次就按休闲长骑理解。",
                "severity": 4,
                "priority_group": 1,
                "priority_rank": 2,
            }
        )
    if sport.get("avg_cadence") is not None and sport.get("avg_cadence") < 72:
        alerts.append(
            {
                "rule": "cadence_low",
                "title": "踏频明显偏低",
                "evidence": f"平均踏频 {sport.get('avg_cadence')}。",
                "impact": "更偏力量型踩踏，腿部局部疲劳会更重。",
                "advice": "恢复骑时可以有意识把踏频抬一点，让腿部压力分散开。",
                "severity": 3,
                "priority_group": 2,
                "priority_rank": 2,
            }
        )
    if sport.get("hr_power_relation_note"):
        alerts.append(
            {
                "rule": "hr_power_relation_anomaly",
                "title": "心率和功率关系有异常提示",
                "evidence": sport.get("hr_power_relation_note"),
                "impact": "这种情况值得和天气、疲劳、恢复状态一起看。",
                "advice": "下一次类似训练继续观察这个关系，避免在身体反馈不佳时还硬推强度。",
                "severity": 3,
                "priority_group": 2,
                "priority_rank": 3,
            }
        )
    return sorted(
        alerts,
        key=lambda item: (
            item.get("priority_group", 3),
            item.get("priority_rank", 9),
            -item["severity"],
        ),
    )


def build_activity_payload(
    normalized: dict,
    user_name: str,
    recent_history: list[dict],
    user_baseline: dict | None = None,
    recent_message_packages: list[dict] | None = None,
) -> dict:
    basic = normalized["basic_activity"]
    load = normalized["load_recovery"]
    sport = normalized.get("sport_specific", {})
    background = normalized["continuous_background"]
    highlights = normalized["achievement_summary"]
    duration_text = _format_minutes_text(basic.get("duration_min"))
    moving_duration_text = _format_minutes_text(basic.get("moving_duration_min"))
    elapsed_duration_text = _format_minutes_text(basic.get("elapsed_duration_min"))
    stop_duration_text = _format_minutes_text(basic.get("stop_duration_min"))
    significant_stop_break = _has_significant_stop_break(basic)
    if not significant_stop_break:
        stop_duration_text = None
    forced_alerts = _activity_forced_alerts(normalized, recent_history)
    priority_issues = forced_alerts[:2]
    secondary_issues = forced_alerts[2:]
    baseline_view = _activity_baseline_view(normalized, user_baseline)
    continuity_context = _activity_continuity_context(normalized, recent_history, recent_message_packages)

    tss = load.get("training_stress_score") or 0
    intensity_factor = sport.get("intensity_factor") or 0
    if tss >= 150 or intensity_factor >= 0.82:
        training_summary = "这次训练属于偏重刺激，不是轻松刷一趟就能过去的级别。"
    elif tss >= 90 or intensity_factor >= 0.75:
        training_summary = "这次训练是中高强度的有效刺激，质量不错，但恢复不能随便带过。"
    else:
        training_summary = "这次训练整体更偏稳态耐力，负荷不算夸张。"

    reason_points = [
        (
            f"全程 {basic.get('distance_km')}km / {duration_text} / "
            f"爬升 {basic.get('elevation_gain_m')}m。"
        ),
    ]
    key_metric_points = []
    if sport.get("avg_power") is not None:
        key_metric_points.append(f"平均功率 {sport.get('avg_power')}W")
    if basic.get("avg_hr") is not None:
        key_metric_points.append(f"平均心率 {basic.get('avg_hr')}")
    if sport.get("avg_cadence") is not None:
        key_metric_points.append(f"平均踏频 {sport.get('avg_cadence')}")
    if key_metric_points:
        reason_points.append("，".join(key_metric_points) + "。")
    reason_points.append(
        f"TSS {load.get('training_stress_score')}，训练负荷 {load.get('activity_training_load')}，"
        f"IF {sport.get('intensity_factor')}。"
    )
    if sport.get("normalized_power") and sport.get("avg_power"):
        reason_points.append(
            f"NP {sport.get('normalized_power')}W，对比平均功率 {sport.get('avg_power')}W，"
            f"输出波动比大约 {sport.get('power_variability_ratio')}。"
        )
    if sport.get("power_zone_summary"):
        reason_points.append(
            f"功率结构上，{sport.get('power_zone_summary')} 心率结构看，{sport.get('hr_zone_summary')}"
        )
    if baseline_view:
        reason_points.append(baseline_view["baseline_summary"])
    reason_points = reason_points[:4]

    if sport.get("pacing_flag") == "stable":
        if sport.get("avg_power") is not None and basic.get("avg_hr") is not None:
            highlight_point = f"这次最值得肯定的地方是节奏挺稳，平均功率 {sport.get('avg_power')}W、平均心率 {basic.get('avg_hr')}，前后段输出没有明显崩掉。"
        else:
            highlight_point = "这次最值得肯定的地方是节奏挺稳，前后段输出没有明显崩掉，长时间专注度是在线的。"
    elif sport.get("power_variability_ratio") and sport.get("power_variability_ratio") >= 1.18:
        highlight_point = "这次亮点在于你把有波动的输出顶住了，说明不仅能骑完，还能扛住变化。"
    else:
        highlight_point = "这次亮点是整体训练结构比较清楚，不是无效堆时间。"

    if priority_issues:
        issue_point = f"需要明确点出来的是，{priority_issues[0]['evidence']} {priority_issues[0]['impact']}"
        issue_bound_advice = priority_issues[0]["advice"]
        secondary_issue_point = None
        if len(priority_issues) > 1:
            secondary_issue_point = f"{priority_issues[1]['evidence']} {priority_issues[1]['impact']}"
    elif sport.get("cadence_note"):
        issue_point = sport.get("cadence_note")
        issue_bound_advice = "恢复骑时可以把节奏踩顺一点，别一直用力量型踩踏去顶。"
        secondary_issue_point = None
    else:
        issue_point = "专项上没有特别硬的红灯，但恢复安排还是要跟这次刺激匹配。"
        issue_bound_advice = "今晚优先把补给和休息做好，别把这次训练的余震拖到下一次。"
        secondary_issue_point = None

    if background.get("is_back_to_back_hard_day"):
        trend_summary = "放进最近几次训练里看，这更像连续偏重训练中的又一次刺激。"
    elif background.get("is_above_recent_baseline"):
        trend_summary = "和你最近基线相比，这次属于更重的一次。"
    elif background.get("recent_training_density", 0) >= 4:
        trend_summary = "最近训练密度不低，这次虽然不是最重，但叠加恢复压力要一起算。"
    else:
        trend_summary = "和你最近几次训练相比，这次大体还在熟悉区间内。"
    if continuity_context.get("continuity_sentence_hint"):
        trend_summary = continuity_context["continuity_sentence_hint"]

    if priority_issues:
        advice_focus = "problem_first"
    elif tss >= 150 or intensity_factor >= 0.82:
        advice_focus = "recover_first"
    else:
        advice_focus = "keep_steady"

    badge_note = None
    if highlights.get("has_recent_milestone_badge"):
        badge_note = "这次附近还有里程碑类成就，可以作为一句小彩蛋带一下。"

    required_metric_mentions = []
    if sport.get("avg_power") is not None:
        required_metric_mentions.append(f"平均功率 {sport.get('avg_power')}W")
    if basic.get("avg_hr") is not None:
        required_metric_mentions.append(f"平均心率 {basic.get('avg_hr')}")
    if sport.get("avg_cadence") is not None:
        required_metric_mentions.append(f"平均踏频 {sport.get('avg_cadence')}")

    return {
        "message_type": "activity_brief",
        "date": basic.get("date"),
        "user_name": user_name,
        "activity_name": basic.get("activity_name"),
        "sport_type": basic.get("sport_type"),
        "length_target": "450-700字，最多700字",
        "training_summary": training_summary,
        "reason_points": reason_points,
        "highlight_point": highlight_point,
        "issue_point": issue_point,
        "secondary_issue_point": secondary_issue_point,
        "issue_bound_advice": issue_bound_advice,
        "trend_summary": trend_summary,
        "continuity_context": continuity_context,
        "cycling_specific_summary": {
            "if": sport.get("intensity_factor"),
            "tss": load.get("training_stress_score"),
            "activity_load": load.get("activity_training_load"),
            "np_vs_avg_power": {
                "normalized_power": sport.get("normalized_power"),
                "avg_power": sport.get("avg_power"),
                "ratio": sport.get("power_variability_ratio"),
            },
            "left_right_balance_note": sport.get("left_right_balance_note"),
            "cadence_note": sport.get("cadence_note"),
            "power_zone_summary": sport.get("power_zone_summary"),
            "hr_zone_summary": sport.get("hr_zone_summary"),
            "hydration_gap_ml": sport.get("hydration_gap_ml"),
            "pacing_trend": sport.get("pacing_trend"),
            "stop_duration_min": basic.get("stop_duration_min") if significant_stop_break else None,
            "stop_duration_text": stop_duration_text,
        },
        "today_advice_focus": advice_focus,
        "priority_issues": priority_issues,
        "secondary_issues": secondary_issues,
        "forced_alerts": forced_alerts,
        "must_mention": [alert["title"] for alert in priority_issues],
        "required_metric_mentions": required_metric_mentions,
        "data_support_rules": [
            "只要正文写到功率稳定、心率更活跃、负担偏高、节奏不错等判断，就必须把对应关键数字一起写出来。",
            "骑行快报默认尽量保留平均功率和平均心率，数字要自然嵌在句子里。",
        ],
        "baseline_view": baseline_view,
        "key_metrics": {
            "distance_km": basic.get("distance_km"),
            "duration_min": basic.get("duration_min"),
            "duration_text": duration_text,
            "moving_duration_min": basic.get("moving_duration_min"),
            "moving_duration_text": moving_duration_text,
            "elapsed_duration_min": basic.get("elapsed_duration_min"),
            "elapsed_duration_text": elapsed_duration_text,
            "stop_duration_min": basic.get("stop_duration_min") if significant_stop_break else None,
            "stop_duration_text": stop_duration_text,
            "elevation_gain_m": basic.get("elevation_gain_m"),
            "training_stress_score": load.get("training_stress_score"),
            "activity_training_load": load.get("activity_training_load"),
            "avg_hr": basic.get("avg_hr"),
            "avg_power": sport.get("avg_power"),
            "normalized_power": sport.get("normalized_power"),
            "intensity_factor": sport.get("intensity_factor"),
            "avg_cadence": sport.get("avg_cadence"),
            "hydration_gap_ml": sport.get("hydration_gap_ml"),
        },
        "context": {
            "vs_user_typical_load": background.get("vs_user_typical_load"),
            "vs_user_typical_duration": background.get("vs_user_typical_duration"),
            "vs_user_typical_intensity": background.get("vs_user_typical_intensity"),
            "recent_typical_load": background.get("recent_typical_load"),
            "recent_typical_duration": background.get("recent_typical_duration"),
            "recent_typical_intensity": background.get("recent_typical_intensity"),
            "power_variability_note": sport.get("power_variability_note"),
            "hr_power_relation_note": sport.get("hr_power_relation_note"),
            "badge_note": badge_note,
        },
    }


def _weekly_sleep_summary(sleeps: list[dict]) -> dict:
    return {
        "avg_sleep_min": average(sleeps, "basic_sleep.total_sleep_min"),
        "avg_sleep_score": average(sleeps, "basic_sleep.sleep_score"),
        "avg_hrv": average(sleeps, "recovery_status.hrv_last_night"),
        "avg_body_battery_at_wake": average(sleeps, "recovery_status.body_battery_at_wake"),
        "avg_resting_hr": average(sleeps, "recovery_status.resting_hr"),
    }


def _weekly_training_summary(activities: list[dict]) -> dict:
    total_duration = sum(record.get("basic_activity", {}).get("duration_min", 0) or 0 for record in activities)
    total_load = sum(record.get("load_recovery", {}).get("training_stress_score", 0) or 0 for record in activities)
    sports = Counter(
        record.get("basic_activity", {}).get("sport_type")
        for record in activities
        if record.get("basic_activity", {}).get("sport_type")
    )
    common_sport = sports.most_common(1)[0][0] if sports else None
    return {
        "activity_count": len(activities),
        "total_duration_min": round(total_duration, 1),
        "total_training_stress_score": round(total_load, 1),
        "common_sport_type": common_sport,
        "avg_training_stress_score": round(total_load / len(activities), 1) if activities else None,
    }


def _weekly_sport_name(value: str | None) -> str:
    mapping = {
        "road_biking": "骑行",
        "cycling": "骑行",
        "mountain_biking": "山地骑行",
        "indoor_cycling": "室内骑行",
        "virtual_ride": "虚拟骑行",
        "gravel_cycling": "砾石骑行",
        "running": "跑步",
        "trail_running": "越野跑",
        "strength_training": "力量训练",
    }
    if not value:
        return "训练"
    return mapping.get(value, value)


def _weekly_date_text(date_str: str | None) -> str | None:
    if not date_str:
        return None
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return f"{dt.month}月{dt.day}日"
    except Exception:
        return date_str


def _weekly_activity_focus(activities: list[dict]) -> dict:
    sessions = []
    for record in activities:
        basic = record.get("basic_activity", {})
        load = record.get("load_recovery", {})
        tss = load.get("training_stress_score") or 0
        sessions.append(
            {
                "date": basic.get("date"),
                "date_text": _weekly_date_text(basic.get("date")),
                "name": basic.get("activity_name") or _weekly_sport_name(basic.get("sport_type")),
                "duration_min": basic.get("duration_min") or 0,
                "tss": round(tss, 1),
            }
        )

    ranked_sessions = sorted(sessions, key=lambda item: item["tss"], reverse=True)
    total_load = round(sum(item["tss"] for item in ranked_sessions), 1)
    top_one = ranked_sessions[0] if ranked_sessions else None
    top_two = ranked_sessions[:2]
    top_one_share = round((top_one["tss"] / total_load) * 100) if top_one and total_load else None
    top_two_share = round((sum(item["tss"] for item in top_two) / total_load) * 100) if top_two and total_load else None
    hard_sessions = [item for item in ranked_sessions if item["tss"] >= 80]
    ordered_sessions = sorted(sessions, key=lambda item: item["date"] or "")
    return {
        "sessions": ranked_sessions,
        "ordered_sessions": ordered_sessions,
        "top_one": top_one,
        "top_one_share_pct": top_one_share,
        "top_two_share_pct": top_two_share,
        "hard_session_count": len(hard_sessions),
    }


def build_weekly_payload(user_name: str, sleep_history: list[dict], activity_history: list[dict]) -> dict | None:
    if not sleep_history and not activity_history:
        return None

    sleep_summary = _weekly_sleep_summary(sleep_history)
    training_summary = _weekly_training_summary(activity_history)
    activity_focus = _weekly_activity_focus(activity_history)
    avg_sleep_text = _format_minutes_text(sleep_summary.get("avg_sleep_min"))
    total_duration_text = _format_minutes_text(training_summary.get("total_duration_min"))
    common_sport_text = _weekly_sport_name(training_summary.get("common_sport_type"))

    avg_sleep_score = sleep_summary.get("avg_sleep_score") or 0
    avg_hrv = sleep_summary.get("avg_hrv")
    avg_battery = sleep_summary.get("avg_body_battery_at_wake") or 0
    activity_count = training_summary["activity_count"]
    total_load = training_summary["total_training_stress_score"] or 0
    top_session = activity_focus.get("top_one")
    top_session_share = activity_focus.get("top_one_share_pct")
    top_two_share = activity_focus.get("top_two_share_pct")
    ordered_sessions = activity_focus.get("ordered_sessions") or []
    after_top_sessions = []
    if top_session:
        after_top_sessions = [
            session for session in ordered_sessions
            if session.get("date") and top_session.get("date") and session["date"] > top_session["date"]
        ]
    after_top_load = round(sum(session["tss"] for session in after_top_sessions), 1) if after_top_sessions else 0
    top_session_ref = None
    if top_session and top_session.get("date_text") and top_session.get("name"):
        top_session_ref = f"{top_session['date_text']}那次{top_session['name']}"

    overall_summary = "过去7天整体状态还算稳，能练也能恢复。"
    if avg_sleep_score < 72 and total_load >= 250:
        overall_summary = "过去7天训练顶上去了一些，但恢复没有完全跟上。"
    elif avg_sleep_score >= 80 and avg_battery >= 85 and activity_count >= 3:
        overall_summary = "过去7天整体状态不错，恢复和训练都比较在线。"
    elif activity_count <= 1:
        overall_summary = "过去7天整体更像调整阶段，状态没有被硬顶。"
    elif activity_count >= 3 and total_load >= 150:
        overall_summary = "过去7天训练频率是稳的，身体也基本接得住。"

    if sleep_history:
        recovery_summary = (
            f"睡眠和恢复这边，过去7天平均睡了 {avg_sleep_text}，"
            f"睡眠评分大约 {sleep_summary.get('avg_sleep_score')} 分。"
        )
        if avg_hrv is not None and avg_battery:
            recovery_summary += (
                f"HRV 平均在 {avg_hrv} 左右，"
                f"起床 body battery 多数时候能回到 {avg_battery}。"
            )
        if avg_sleep_score < 72:
            recovery_summary += "恢复不算差，但回得还不够满。"
        elif avg_sleep_score >= 80 and avg_battery >= 85:
            recovery_summary += "这一周的恢复底子是比较稳的。"
        else:
            recovery_summary += "整体还是在自己的常态区间里。"
    else:
        recovery_summary = "睡眠和恢复这边，过去7天可用数据不多，但看起来没有明显掉出常态。"

    if activity_count == 0:
        training_rhythm_summary = "训练这边过去7天更像休整节奏，没有明显的训练负荷。"
    else:
        training_rhythm_summary = (
            f"训练这边，过去7天一共做了 {activity_count} 次{common_sport_text}，"
            f"总时长大约 {total_duration_text}。"
        )
        if total_load >= 300:
            training_rhythm_summary += f"总训练压力来到 {total_load}，整体不算轻。"
        elif total_load >= 150:
            training_rhythm_summary += f"总训练压力大约 {total_load}，节奏是连续的。"
        else:
            training_rhythm_summary += f"总训练压力大约 {total_load}，整体还算克制。"
        if top_session and top_session_share and top_session_share >= 45:
            training_rhythm_summary += (
                f"其中主要负荷压在 {top_session['date_text']} 那次{top_session['name']}上，"
                f"单次大约占了过去7天训练压力的 {top_session_share}%。"
            )
        elif top_two_share and top_two_share >= 65:
            training_rhythm_summary += f"主要负荷集中在前两次重点训练上，大约占了过去7天的 {top_two_share}%。"

    recovery_match_summary = "恢复基本跟上了训练，过去7天的完成度是靠得住的。"
    if avg_sleep_score < 72 and total_load >= 250:
        recovery_match_summary = "恢复没有完全跟上训练，说明过去7天的刺激已经逼近上限。"
    elif avg_sleep_score >= 80 and avg_battery >= 85 and total_load >= 150:
        recovery_match_summary = "恢复是跟得上的，所以过去7天几次训练基本都完成得比较稳。"
    elif avg_sleep_score >= 76 and total_load >= 150:
        recovery_match_summary = "恢复总体跟得上训练，但过去7天的余量不算特别宽裕。"

    post_peak_summary = None
    if top_session and after_top_sessions:
        if after_top_load <= max(50, (top_session.get("tss") or 0) * 0.5):
            post_peak_summary = (
                f"{top_session_ref}之后，后面几天更像在恢复和维持节奏，"
                "没有继续把负荷往上推。"
            )
        else:
            post_peak_summary = (
                f"{top_session_ref}之后，后面几天还有继续推进，"
                "所以过去7天的累积压力其实是往上叠的。"
            )

    headroom_summary = "接下来7天还有一点加量空间，但不适合一下子加太多。"
    if avg_sleep_score < 72 and total_load >= 250:
        headroom_summary = "接下来7天不太适合继续加量，先把恢复拉回来更重要。"
    elif avg_sleep_score >= 80 and avg_battery >= 85 and total_load < 160:
        headroom_summary = "接下来7天是有加一点量的空间的，但最好只加一个重点训练。"
    elif activity_count >= 3 and total_load >= 150:
        headroom_summary = "接下来7天额外加量空间不算大，更适合先把现在的节奏守稳。"

    weekly_focus_fact = ""
    weekly_focus_meaning = ""
    notable_change = "过去7天最值得注意的一件事，是这一周整体没有明显失衡。"
    if avg_sleep_score < 72 and total_load >= 250:
        notable_change = "过去7天最值得注意的一件事，是训练已经堆上去了，恢复却没完全跟上。"
    elif total_load >= 300:
        notable_change = "过去7天最值得注意的一件事，是这周训练量偏满，后半周恢复不能省。"
    elif avg_sleep_score >= 80 and avg_battery >= 85 and activity_count >= 3:
        notable_change = "过去7天最值得注意的一件事，是恢复底子稳，训练节奏接得住。"
    elif activity_count <= 1:
        notable_change = "过去7天最值得注意的一件事，是这周更像主动回收，不是继续硬顶。"
    elif top_session and top_session_share and top_session_share >= 45:
        weekly_focus_fact = f"主要训练压力集中在{top_session_ref}上。"
        weekly_focus_meaning = "这说明这一周的训练刺激更多来自少数重点课，不是均匀分布。"
        notable_change = "过去7天最值得注意的一件事，是训练刺激明显集中在少数重点课上。"
    elif activity_count >= 3 and total_load >= 150 and avg_sleep_score >= 76:
        notable_change = "过去7天最值得注意的一件事，是这周完成度不错，但额外加量空间不大。"

    next_week_advice = "接下来7天继续按现在的节奏走，最多只加一个重点训练日。"
    if avg_sleep_score < 72 and total_load >= 250:
        next_week_advice = "接下来7天别把高强度连着排，至少给自己留一天明显的缓冲。"
    elif total_load >= 300:
        next_week_advice = "接下来7天如果还想维持这个训练量，先把睡眠和补给稳住，再上强度。"
    elif avg_sleep_score < 72:
        next_week_advice = "接下来7天先把睡眠拉稳，再谈加量。"
    elif activity_count <= 1:
        next_week_advice = "接下来7天可以把训练节奏慢慢接回来，但别一下子加太多。"
    elif top_session and top_session_share and top_session_share >= 45:
        next_week_advice = "接下来7天如果还想加量，别把第二个重点训练日挤在最重那次训练旁边。"
    elif activity_count >= 3 and total_load >= 150:
        next_week_advice = "接下来7天维持现在的频率就够了，真要加量也先只加一点。"

    return {
        "message_type": "weekly_report",
        "window_label": "过去7天固定总结",
        "user_name": user_name,
        "length_target": "600-900字，最多900字",
        "overall_summary": overall_summary,
        "recovery_summary": recovery_summary,
        "training_rhythm_summary": training_rhythm_summary,
        "recovery_match_summary": recovery_match_summary,
        "post_peak_summary": post_peak_summary,
        "headroom_summary": headroom_summary,
        "notable_change": notable_change,
        "weekly_focus_fact": weekly_focus_fact,
        "weekly_focus_meaning": weekly_focus_meaning,
        "next_week_advice": next_week_advice,
        "key_metrics": {
            "avg_sleep_min": sleep_summary.get("avg_sleep_min"),
            "avg_sleep_text": avg_sleep_text,
            "avg_sleep_score": sleep_summary.get("avg_sleep_score"),
            "avg_hrv": sleep_summary.get("avg_hrv"),
            "avg_body_battery_at_wake": sleep_summary.get("avg_body_battery_at_wake"),
            "activity_count": training_summary["activity_count"],
            "total_duration_min": training_summary["total_duration_min"],
            "total_duration_text": total_duration_text,
            "total_training_stress_score": training_summary["total_training_stress_score"],
            "avg_training_stress_score": training_summary["avg_training_stress_score"],
            "common_sport_type": training_summary["common_sport_type"],
            "common_sport_text": common_sport_text,
            "top_session": top_session,
            "top_session_ref": top_session_ref,
            "top_session_share_pct": top_session_share,
            "top_two_share_pct": top_two_share,
        },
    }


def _initial_sleep_pattern(sleeps: list[dict]) -> dict:
    durations = [
        record.get("basic_sleep", {}).get("total_sleep_min")
        for record in sleeps
        if isinstance(record.get("basic_sleep", {}).get("total_sleep_min"), (int, float))
    ]
    scores = [
        record.get("basic_sleep", {}).get("sleep_score")
        for record in sleeps
        if isinstance(record.get("basic_sleep", {}).get("sleep_score"), (int, float))
    ]
    batteries = [
        record.get("recovery_status", {}).get("body_battery_at_wake")
        for record in sleeps
        if isinstance(record.get("recovery_status", {}).get("body_battery_at_wake"), (int, float))
    ]

    duration_range = round(max(durations) - min(durations), 1) if len(durations) >= 2 else None
    stable_nights = 0
    avg_duration = average(sleeps, "basic_sleep.total_sleep_min")
    if avg_duration is not None:
        stable_nights = sum(1 for value in durations if abs(value - avg_duration) <= 45)

    recovery_good_nights = sum(
        1
        for record in sleeps
        if (record.get("basic_sleep", {}).get("sleep_score") or 0) >= 80
        and (record.get("recovery_status", {}).get("body_battery_at_wake") or 0) >= 75
    )
    recovery_tight_nights = sum(
        1
        for record in sleeps
        if (record.get("basic_sleep", {}).get("sleep_score") or 0) < 70
        or (record.get("recovery_status", {}).get("body_battery_at_wake") or 100) < 55
    )

    if not sleeps:
        rhythm_note = "目前可用的睡眠样本还不多，先不急着下太满的结论。"
    elif duration_range is not None and duration_range <= 90 and stable_nights >= max(4, int(len(durations) * 0.6)):
        rhythm_note = "睡眠时长整体比较稳定，作息节奏没有明显飘。"
    elif duration_range is not None and duration_range >= 150:
        rhythm_note = "睡眠时长波动有点大，恢复质量更容易跟着起伏。"
    else:
        rhythm_note = "睡眠节奏大体还在一个区间里，但稳定性还可以继续观察。"

    return {
        "duration_range_min": duration_range,
        "stable_nights": stable_nights,
        "recovery_good_nights": recovery_good_nights,
        "recovery_tight_nights": recovery_tight_nights,
        "rhythm_note": rhythm_note,
        "avg_sleep_min": avg_duration,
        "avg_sleep_text": _format_minutes_text(avg_duration),
        "avg_sleep_score": average(sleeps, "basic_sleep.sleep_score"),
        "avg_hrv": average(sleeps, "recovery_status.hrv_last_night"),
        "avg_body_battery_at_wake": average(sleeps, "recovery_status.body_battery_at_wake"),
    }


def _initial_training_pattern(activities: list[dict]) -> dict:
    summary = _weekly_training_summary(activities)
    focus = _weekly_activity_focus(activities)
    long_sessions = sum(
        1 for record in activities if (record.get("basic_activity", {}).get("duration_min") or 0) >= 120
    )
    cycling_sessions = sum(
        1
        for record in activities
        if _weekly_sport_name(record.get("basic_activity", {}).get("sport_type")) == "骑行"
    )
    activity_count = summary.get("activity_count") or 0

    if activity_count == 0:
        rhythm_note = "目前看这段时间训练不算多，更像在维持或调整。"
    elif focus.get("top_one_share_pct") and focus["top_one_share_pct"] >= 35:
        rhythm_note = "训练负荷有一定集中度，主要压力会落在少数几次重点训练上。"
    elif activity_count >= 6:
        rhythm_note = "训练节奏比较连续，不是只靠一两次训练撑起整体状态。"
    else:
        rhythm_note = "训练节奏有在持续，但目前还不是特别密集的推进型节奏。"

    if activity_count and cycling_sessions / activity_count >= 0.6:
        sport_note = "运动主体比较明确，基本还是以骑行为主。"
    elif summary.get("common_sport_type"):
        sport_note = f"主要运动类型目前更偏{_weekly_sport_name(summary.get('common_sport_type'))}。"
    else:
        sport_note = "运动类型还在继续积累样本。"

    return {
        "activity_count": activity_count,
        "total_duration_min": summary.get("total_duration_min"),
        "total_duration_text": _format_minutes_text(summary.get("total_duration_min")),
        "total_training_stress_score": summary.get("total_training_stress_score"),
        "avg_training_stress_score": summary.get("avg_training_stress_score"),
        "common_sport_type": summary.get("common_sport_type"),
        "common_sport_text": _weekly_sport_name(summary.get("common_sport_type")),
        "top_one": focus.get("top_one"),
        "top_one_share_pct": focus.get("top_one_share_pct"),
        "top_two_share_pct": focus.get("top_two_share_pct"),
        "hard_session_count": focus.get("hard_session_count"),
        "long_session_count": long_sessions,
        "cycling_session_count": cycling_sessions,
        "rhythm_note": rhythm_note,
        "sport_note": sport_note,
    }


def build_cold_start_snapshot(sleep_history: list[dict], activity_history: list[dict]) -> dict:
    sleep_pattern = _initial_sleep_pattern(sleep_history)
    training_pattern = _initial_training_pattern(activity_history)
    avg_sleep_min = sleep_pattern.get("avg_sleep_min") or 0
    avg_sleep_score = sleep_pattern.get("avg_sleep_score") or 0
    avg_battery = sleep_pattern.get("avg_body_battery_at_wake") or 0
    activity_count = training_pattern.get("activity_count") or 0
    common_sport_text = training_pattern.get("common_sport_text") or "运动"

    if avg_sleep_min >= 420 and avg_sleep_score >= 78:
        sleep_duration_status = "睡眠大体够"
    elif avg_sleep_min >= 390 and avg_sleep_score >= 72:
        sleep_duration_status = "睡眠暂时够用"
    elif sleep_history:
        sleep_duration_status = "睡眠可能还不太够"
    else:
        sleep_duration_status = "睡眠样本还不够"

    if avg_sleep_score >= 78 and avg_battery >= 70:
        recovery_stability = "恢复暂时比较稳"
    elif sleep_history and (avg_sleep_score < 72 or avg_battery < 60):
        recovery_stability = "恢复还不太稳"
    elif sleep_history:
        recovery_stability = "恢复中等偏稳"
    else:
        recovery_stability = "恢复样本还不够"

    if activity_count == 0:
        sport_tendency = "目前还看不出明显运动倾向"
    elif training_pattern.get("cycling_session_count", 0) / activity_count >= 0.6:
        sport_tendency = "目前先看出骑行倾向"
    elif training_pattern.get("common_sport_type"):
        sport_tendency = f"目前先看出{common_sport_text}倾向"
    else:
        sport_tendency = "运动倾向还在继续积累样本"

    return {
        "sleep_duration_status": sleep_duration_status,
        "recovery_stability": recovery_stability,
        "sport_tendency": sport_tendency,
        "sleep_rhythm_note": sleep_pattern.get("rhythm_note"),
        "training_rhythm_note": training_pattern.get("rhythm_note"),
        "sample_nights": len(sleep_history),
        "activity_count": activity_count,
        "common_sport_text": common_sport_text,
    }


def build_initial_7d_summary_payload(user_name: str, sleep_history: list[dict], activity_history: list[dict]) -> dict | None:
    if not sleep_history and not activity_history:
        return None

    sleep_pattern = _initial_sleep_pattern(sleep_history)
    training_pattern = _initial_training_pattern(activity_history)
    snapshot = build_cold_start_snapshot(sleep_history, activity_history)
    avg_sleep_score = sleep_pattern.get("avg_sleep_score") or 0
    avg_sleep_min = sleep_pattern.get("avg_sleep_min") or 0
    avg_battery = sleep_pattern.get("avg_body_battery_at_wake") or 0
    activity_count = training_pattern.get("activity_count") or 0
    total_load = training_pattern.get("total_training_stress_score") or 0
    top_one_share = training_pattern.get("top_one_share_pct") or 0
    common_sport_text = training_pattern.get("common_sport_text") or "运动"

    opening = f"{user_name}，先把接入后的第一份 7 天初步观察给你。"

    overall_summary = "从目前这7天看，已经能先看出一点整体轮廓，但这还只是初步认识。"
    if avg_sleep_score >= 78 and avg_sleep_min >= 420 and activity_count >= 2:
        overall_summary = "从目前这7天看，睡眠和训练已经有一点节奏感，恢复暂时也能跟上。"
    elif avg_sleep_score < 72 and activity_count >= 2:
        overall_summary = "从目前这7天看，训练已经开始动起来了，但恢复还没有完全跟上。"
    elif activity_count == 0:
        overall_summary = "从目前这7天看，这段时间更像先在建立作息和日常状态，训练样本还不多。"
    elif top_one_share >= 40:
        overall_summary = "从目前这7天看，训练不是没有，但负荷更多压在一两次重点课上。"

    if sleep_history:
        sleep_recovery_summary = (
            f"睡眠和恢复这边，现阶段先做初步判断：最近 {len(sleep_history)} 晚里，"
            f"平均睡眠大约 {sleep_pattern.get('avg_sleep_text')}，睡眠评分大约 {sleep_pattern.get('avg_sleep_score')} 分。"
        )
        if sleep_pattern.get("avg_hrv") is not None:
            sleep_recovery_summary += f"HRV 平均在 {sleep_pattern.get('avg_hrv')} 左右，"
        if sleep_pattern.get("avg_body_battery_at_wake") is not None:
            sleep_recovery_summary += f"起床 body battery 大多回到 {sleep_pattern.get('avg_body_battery_at_wake')}。"
        if avg_sleep_min >= 420 and avg_sleep_score >= 78 and avg_battery >= 70:
            sleep_recovery_summary += "目前看睡得基本够，恢复也相对稳一点。"
        elif avg_sleep_min < 390 or avg_sleep_score < 72 or avg_battery < 60:
            sleep_recovery_summary += "目前看主要还是睡得不太够，回充也还不算稳定。"
        else:
            sleep_recovery_summary += "目前看睡眠不算差，但恢复稳定性还需要再观察几天。"
        sleep_recovery_summary += sleep_pattern.get("rhythm_note")
    else:
        sleep_recovery_summary = "睡眠和恢复这边，目前样本还不多，这部分先不急着说得太满。"

    if activity_count:
        training_summary = (
            f"训练和运动这边，目前记录到 {activity_count} 次{common_sport_text}，"
            f"总时长大约 {training_pattern.get('total_duration_text')}。"
        )
        if total_load:
            training_summary += f"累计训练压力大约 {total_load}。"
        training_summary += training_pattern.get("sport_note")
        if top_one_share >= 40:
            training_summary += f"目前看负荷有一定集中度，最重的一次大约占了这段时间训练压力的 {top_one_share}%。"
        else:
            training_summary += training_pattern.get("rhythm_note")
    else:
        training_summary = "训练和运动这边，样本还不算多，暂时先把它当成一个初步轮廓。"

    notable_feature = "目前最值得注意的一点，是整体轮廓已经能先看出来了，但样本还不算多，后面会更准。"
    if avg_sleep_score < 72 or avg_battery < 60:
        notable_feature = "目前最值得注意的一点，是恢复的起伏还比较明显，睡着了不一定就等于回得很满。"
    elif sleep_pattern.get("duration_range_min") is not None and sleep_pattern.get("duration_range_min") >= 150:
        notable_feature = "目前最值得注意的一点，是作息和睡眠时长还不太稳，这会直接影响恢复表现。"
    elif top_one_share >= 40:
        notable_feature = "目前最值得注意的一点，是训练负荷会偏集中在一两次重点训练上。"
    elif activity_count and training_pattern.get("cycling_session_count", 0) / activity_count >= 0.6:
        notable_feature = "目前最值得注意的一点，是运动主体已经开始偏向骑行了。"

    next_focus = "接下来我会继续看睡眠恢复和训练节奏，等样本再多一点，再把哪些是阶段波动、哪些是稳定特征分开。"
    if avg_sleep_score < 72 or avg_sleep_min < 390 or avg_battery < 60:
        next_focus = "接下来我会先重点盯睡眠时长、夜间恢复和起床体能，看看恢复能不能先稳下来。"
    elif top_one_share >= 40:
        next_focus = "接下来我会重点看负荷是不是总压在少数几次训练上，以及每次重点课后的恢复有没有跟上。"
    elif activity_count == 0:
        next_focus = "接下来我会先继续积累睡眠和运动样本，看看你的日常节奏会不会更清楚一点。"

    return {
        "message_type": "initial_7d_summary",
        "user_name": user_name,
        "length_target": "400-700字，最多700字",
        "opening": opening,
        "overall_summary": overall_summary,
        "sleep_recovery_summary": sleep_recovery_summary,
        "training_summary": training_summary,
        "notable_feature": notable_feature,
        "next_focus": next_focus,
        "coverage_note": (
            f"目前基于最近 {len(sleep_history)} 晚睡眠和 {activity_count} 次运动做初步判断。"
        ),
        "tone_anchor": [
            "从目前这7天看",
            "现阶段先做初步判断",
            "目前样本还不多",
            "后面会更准",
        ],
        "cold_start_snapshot": snapshot,
        "key_metrics": {
            "sleep_count": len(sleep_history),
            "avg_sleep_min": sleep_pattern.get("avg_sleep_min"),
            "avg_sleep_text": sleep_pattern.get("avg_sleep_text"),
            "avg_sleep_score": sleep_pattern.get("avg_sleep_score"),
            "avg_hrv": sleep_pattern.get("avg_hrv"),
            "avg_body_battery_at_wake": sleep_pattern.get("avg_body_battery_at_wake"),
            "stable_nights": sleep_pattern.get("stable_nights"),
            "recovery_good_nights": sleep_pattern.get("recovery_good_nights"),
            "recovery_tight_nights": sleep_pattern.get("recovery_tight_nights"),
            "activity_count": activity_count,
            "total_duration_min": training_pattern.get("total_duration_min"),
            "total_duration_text": training_pattern.get("total_duration_text"),
            "total_training_stress_score": training_pattern.get("total_training_stress_score"),
            "avg_training_stress_score": training_pattern.get("avg_training_stress_score"),
            "common_sport_type": training_pattern.get("common_sport_type"),
            "common_sport_text": common_sport_text,
            "top_one": training_pattern.get("top_one"),
            "top_one_share_pct": top_one_share,
            "hard_session_count": training_pattern.get("hard_session_count"),
            "long_session_count": training_pattern.get("long_session_count"),
        },
    }


def build_initial_summary_payload(user_name: str, sleep_history: list[dict], activity_history: list[dict]) -> dict | None:
    if not sleep_history and not activity_history:
        return None

    sleep_pattern = _initial_sleep_pattern(sleep_history)
    training_pattern = _initial_training_pattern(activity_history)
    avg_sleep_score = sleep_pattern.get("avg_sleep_score") or 0
    avg_sleep_min = sleep_pattern.get("avg_sleep_min") or 0
    avg_battery = sleep_pattern.get("avg_body_battery_at_wake") or 0
    total_load = training_pattern.get("total_training_stress_score") or 0
    activity_count = training_pattern.get("activity_count") or 0
    top_one_share = training_pattern.get("top_one_share_pct")
    common_sport_text = training_pattern.get("common_sport_text")

    opening = f"{user_name}，先把接入后的第一份 30 天状态给你。"

    overall_summary = "从过去30天看，你最近这段时间睡眠和训练大体能接上，整体节奏没有明显乱。"
    if avg_sleep_score >= 78 and avg_battery >= 70 and activity_count >= 4:
        overall_summary = "从过去30天看，你最近睡眠基本够、恢复也比较稳，训练频率也已经起来了。"
    elif avg_sleep_score < 72 and total_load >= 180:
        overall_summary = "从过去30天看，训练做得不少，但睡眠和恢复没有完全跟上。"
    elif activity_count <= 2:
        overall_summary = "从过去30天看，这段时间更像在维持或调整，训练刺激不算特别多。"
    elif top_one_share and top_one_share >= 35:
        overall_summary = "从过去30天看，你最近有稳定训练，但负荷更多压在少数几次重点课上。"

    if sleep_history:
        sleep_recovery_summary = (
            f"睡眠和恢复这边，目前平均睡眠大约 {sleep_pattern.get('avg_sleep_text')}，"
            f"睡眠评分大约 {sleep_pattern.get('avg_sleep_score')} 分。"
        )
        if sleep_pattern.get("avg_hrv") is not None:
            sleep_recovery_summary += f"HRV 平均在 {sleep_pattern.get('avg_hrv')} 左右，"
        if sleep_pattern.get("avg_body_battery_at_wake") is not None:
            sleep_recovery_summary += (
                f"起床 body battery 大多回到 {sleep_pattern.get('avg_body_battery_at_wake')}。"
            )
        if avg_sleep_min >= 420 and avg_sleep_score >= 78:
            sleep_recovery_summary += "目前看睡眠时长是够的，恢复也比较稳。"
        elif avg_sleep_min < 390 or avg_sleep_score < 72 or avg_battery < 60:
            sleep_recovery_summary += "目前看主要问题还是睡得偏少，回充也不算特别稳。"
        else:
            sleep_recovery_summary += "目前看睡眠不算太差，但恢复稳定性还可以再往上提一点。"
        sleep_recovery_summary += sleep_pattern.get("rhythm_note")
    else:
        sleep_recovery_summary = "睡眠和恢复这边，目前样本还不多，先把它当成初步轮廓，后面再继续细化。"

    if activity_count:
        training_summary = (
            f"训练和运动这边，过去30天一共记录到 {activity_count} 次{common_sport_text}，"
            f"总时长大约 {training_pattern.get('total_duration_text')}。"
        )
        if total_load:
            training_summary += f"累计训练压力大约 {total_load}。"
        training_summary += training_pattern.get("sport_note")
        if top_one_share and top_one_share >= 35:
            training_summary += f"目前看负荷分布不算均匀，最重的一次大约占了这30天训练压力的 {top_one_share}%。"
        elif training_pattern.get("long_session_count", 0) >= 2:
            training_summary += "这说明训练频率是连着的，而且已经有比较明确的耐力型训练安排。"
        else:
            training_summary += training_pattern.get("rhythm_note")
    else:
        training_summary = "训练和运动这边，目前可用样本不多，暂时更像在先建立你的基础画像。"

    notable_feature = "最近30天最值得注意的一点，是整体节奏还算稳，但还需要更多数据把你的常态看得更清楚。"
    if avg_sleep_score < 72 or avg_battery < 60:
        notable_feature = "最近30天最值得注意的一点，是恢复质量还不算稳定，睡着了不一定等于回得很满。"
    elif top_one_share and top_one_share >= 35:
        notable_feature = "最近30天最值得注意的一点，是训练负荷会偏集中在少数几次重点训练上，尤其像单次长骑或重课。"
    elif activity_count >= 4 and common_sport_text == "骑行":
        notable_feature = "最近30天最值得注意的一点，是你这段时间的训练主体很明确，基本都围绕骑行在展开。"
    elif avg_sleep_score >= 80 and avg_battery >= 75:
        notable_feature = "最近30天最值得注意的一点，是恢复整体比较稳，这让训练节奏更容易连续接下去。"

    next_focus = "接下来我会继续看你的睡眠恢复、训练节奏和负荷分布，慢慢把哪些是稳定特征、哪些只是阶段波动分开。"
    if avg_sleep_score < 72 or avg_sleep_min < 390 or avg_battery < 60:
        next_focus = "接下来我会先重点盯睡眠时长、夜间恢复和起床体能，先看恢复底子能不能更稳一点。"
    elif top_one_share and top_one_share >= 35:
        next_focus = "接下来我会重点看训练负荷是不是总压在少数几次重点训练上，以及恢复有没有及时跟上。"
    elif total_load >= 220 and avg_sleep_score < 78:
        next_focus = "接下来我会重点看恢复能不能跟上训练推进，尤其是重课后的两天状态会不会明显下滑。"

    return {
        "message_type": "initial_30d_summary",
        "user_name": user_name,
        "length_target": "600-1000字，最多1000字",
        "opening": opening,
        "overall_summary": overall_summary,
        "sleep_recovery_summary": sleep_recovery_summary,
        "training_summary": training_summary,
        "notable_feature": notable_feature,
        "next_focus": next_focus,
        "coverage_note": (
            f"过去30天共记录到 {len(sleep_history)} 晚睡眠、{activity_count} 次运动。"
        ),
        "tone_anchor": [
            "从过去30天看",
            "目前看",
            "初步判断",
            "后面还会结合更多数据继续细化",
        ],
        "key_metrics": {
            "sleep_count": len(sleep_history),
            "avg_sleep_min": sleep_pattern.get("avg_sleep_min"),
            "avg_sleep_text": sleep_pattern.get("avg_sleep_text"),
            "avg_sleep_score": sleep_pattern.get("avg_sleep_score"),
            "avg_hrv": sleep_pattern.get("avg_hrv"),
            "avg_body_battery_at_wake": sleep_pattern.get("avg_body_battery_at_wake"),
            "stable_nights": sleep_pattern.get("stable_nights"),
            "recovery_good_nights": sleep_pattern.get("recovery_good_nights"),
            "recovery_tight_nights": sleep_pattern.get("recovery_tight_nights"),
            "activity_count": activity_count,
            "total_duration_min": training_pattern.get("total_duration_min"),
            "total_duration_text": training_pattern.get("total_duration_text"),
            "total_training_stress_score": training_pattern.get("total_training_stress_score"),
            "avg_training_stress_score": training_pattern.get("avg_training_stress_score"),
            "common_sport_type": training_pattern.get("common_sport_type"),
            "common_sport_text": common_sport_text,
            "top_one": training_pattern.get("top_one"),
            "top_one_share_pct": top_one_share,
            "hard_session_count": training_pattern.get("hard_session_count"),
            "long_session_count": training_pattern.get("long_session_count"),
        },
    }


SPORT_BUCKET_LABELS = {
    "cycling": "骑行",
    "running": "跑步",
    "swimming": "游泳",
    "other": "其他运动",
}


def _sum_field(records: list[dict], field_path: str) -> float:
    values = recent_values(records, field_path)
    return round(sum(values), 1) if values else 0.0


def _safe_share(value: float, total: float) -> float:
    if total <= 0:
        return 0.0
    return round(value / total, 4)


def _sport_bucket(sport_type: str | None) -> str:
    value = (sport_type or "").lower()
    if value in {
        "cycling",
        "road_biking",
        "mountain_biking",
        "indoor_cycling",
        "virtual_ride",
        "gravel_cycling",
        "bmx",
    }:
        return "cycling"
    if value in {
        "running",
        "trail_running",
        "treadmill_running",
        "ultra_run",
        "indoor_running",
        "obstacle_run",
        "virtual_run",
    }:
        return "running"
    if value in {
        "lap_swimming",
        "open_water_swimming",
        "swimming",
    }:
        return "swimming"
    return "other"


def _bucket_totals(activities: list[dict]) -> dict[str, dict]:
    result = {
        bucket: {
            "activity_count": 0,
            "total_duration_min": 0.0,
            "total_training_stress_score": 0.0,
        }
        for bucket in SPORT_BUCKET_LABELS
    }

    for record in activities:
        basic = record.get("basic_activity", {})
        load = record.get("load_recovery", {})
        bucket = _sport_bucket(basic.get("sport_type"))
        result[bucket]["activity_count"] += 1
        result[bucket]["total_duration_min"] += basic.get("duration_min") or 0.0
        result[bucket]["total_training_stress_score"] += load.get("training_stress_score") or 0.0

    for bucket, stats in result.items():
        stats["total_duration_min"] = round(stats["total_duration_min"], 1)
        stats["total_duration_text"] = _format_minutes_text(stats["total_duration_min"])
        stats["total_training_stress_score"] = round(stats["total_training_stress_score"], 1)
        stats["sport_bucket"] = bucket
        stats["sport_label"] = SPORT_BUCKET_LABELS[bucket]
    return result


def build_sleep_recovery_baseline(sleep_history: list[dict]) -> dict:
    pattern = _initial_sleep_pattern(sleep_history)
    sample_nights = len(sleep_history)
    avg_sleep_min = pattern.get("avg_sleep_min") or 0
    avg_sleep_score = pattern.get("avg_sleep_score") or 0
    avg_battery = pattern.get("avg_body_battery_at_wake") or 0
    stable_nights = pattern.get("stable_nights") or 0
    tight_nights = pattern.get("recovery_tight_nights") or 0

    if avg_sleep_min >= 420:
        sleep_duration_status = "睡眠基本够"
    elif avg_sleep_min >= 390:
        sleep_duration_status = "睡眠勉强够"
    else:
        sleep_duration_status = "睡眠偏少"

    if (
        sample_nights >= 10
        and avg_sleep_score >= 78
        and avg_battery >= 70
        and stable_nights >= max(6, int(sample_nights * 0.45))
        and tight_nights <= max(3, int(sample_nights * 0.25))
    ):
        recovery_stability = "恢复比较稳"
    elif avg_sleep_score < 72 or avg_battery < 60 or tight_nights >= max(4, int(sample_nights * 0.35)):
        recovery_stability = "恢复波动偏大"
    else:
        recovery_stability = "恢复中等偏稳"

    baseline_summary = f"{sleep_duration_status}，{recovery_stability}。"
    return {
        "window_days": 30,
        "sample_nights": sample_nights,
        "avg_sleep_min": pattern.get("avg_sleep_min"),
        "avg_sleep_text": pattern.get("avg_sleep_text"),
        "avg_sleep_score": pattern.get("avg_sleep_score"),
        "avg_hrv": pattern.get("avg_hrv"),
        "avg_body_battery_at_wake": pattern.get("avg_body_battery_at_wake"),
        "stable_nights": stable_nights,
        "recovery_good_nights": pattern.get("recovery_good_nights"),
        "recovery_tight_nights": tight_nights,
        "sleep_duration_status": sleep_duration_status,
        "recovery_stability": recovery_stability,
        "baseline_summary": baseline_summary,
        "rhythm_note": pattern.get("rhythm_note"),
    }


def build_general_sport_baseline(activity_history: list[dict]) -> dict:
    summary = _weekly_training_summary(activity_history)
    focus = _weekly_activity_focus(activity_history)
    activity_count = summary.get("activity_count") or 0
    active_days = len(
        {
            record.get("basic_activity", {}).get("date")
            for record in activity_history
            if record.get("basic_activity", {}).get("date")
        }
    )
    total_duration_min = summary.get("total_duration_min") or 0
    total_load = summary.get("total_training_stress_score") or 0
    avg_duration_min = round(total_duration_min / activity_count, 1) if activity_count else None
    avg_load = round(total_load / activity_count, 1) if activity_count else None
    frequency_per_week = round(activity_count / 30 * 7, 1) if activity_count else 0
    top_one_share = focus.get("top_one_share_pct") or 0
    top_two_share = focus.get("top_two_share_pct") or 0

    if activity_count >= 8 or active_days >= 8:
        training_frequency = "训练频率较高"
    elif activity_count >= 4:
        training_frequency = "训练频率中等"
    elif activity_count >= 1:
        training_frequency = "训练频率偏低"
    else:
        training_frequency = "最近运动很少"

    if top_one_share >= 40 or top_two_share >= 70:
        load_distribution = "负荷偏集中"
    elif top_one_share >= 28 or top_two_share >= 55:
        load_distribution = "负荷略集中"
    else:
        load_distribution = "负荷分布较均匀"

    if total_load >= 600:
        load_level = "训练负荷较高"
    elif total_load >= 250:
        load_level = "训练负荷中等"
    elif total_load > 0:
        load_level = "训练负荷偏轻"
    else:
        load_level = "暂时没有明显训练负荷"

    summary_text = f"{training_frequency}，{load_level}，{load_distribution}。"
    return {
        "window_days": 30,
        "activity_count": activity_count,
        "active_days": active_days,
        "frequency_per_week": frequency_per_week,
        "total_duration_min": total_duration_min,
        "total_duration_text": _format_minutes_text(total_duration_min),
        "total_training_stress_score": total_load,
        "avg_duration_min": avg_duration_min,
        "avg_duration_text": _format_minutes_text(avg_duration_min),
        "avg_training_stress_score": avg_load,
        "training_frequency": training_frequency,
        "load_level": load_level,
        "load_distribution": load_distribution,
        "top_session": focus.get("top_one"),
        "top_session_share_pct": focus.get("top_one_share_pct"),
        "top_two_share_pct": focus.get("top_two_share_pct"),
        "hard_session_count": focus.get("hard_session_count"),
        "summary_text": summary_text,
    }


def build_main_sport_positioning(activity_history: list[dict], general_baseline: dict) -> dict:
    activity_count = general_baseline.get("activity_count") or 0
    total_duration = general_baseline.get("total_duration_min") or 0
    total_load = general_baseline.get("total_training_stress_score") or 0
    bucket_totals = _bucket_totals(activity_history)

    scored = []
    for bucket, stats in bucket_totals.items():
        if stats["activity_count"] <= 0:
            continue
        count_share = _safe_share(stats["activity_count"], activity_count)
        duration_share = _safe_share(stats["total_duration_min"], total_duration)
        load_share = _safe_share(stats["total_training_stress_score"], total_load)
        score = round(count_share * 0.4 + duration_share * 0.35 + load_share * 0.25, 4)
        scored.append(
            {
                **stats,
                "count_share": count_share,
                "duration_share": duration_share,
                "load_share": load_share,
                "score": score,
            }
        )

    scored.sort(key=lambda item: item["score"], reverse=True)
    top = scored[0] if scored else None
    second = scored[1] if len(scored) > 1 else None

    main_sport_type = "健康维持型"
    current_profile = "健康维持型用户"
    specialized_track = "general"
    specialized_track_status = "inactive"

    if not top or (activity_count <= 2 and total_duration < 180 and total_load < 120):
        main_sport_type = "健康维持型"
        current_profile = "健康维持型用户"
    else:
        lead_gap = round(top["score"] - (second["score"] if second else 0), 4)
        dominant = (
            top["score"] >= 0.55
            or (
                top["activity_count"] >= 4
                and top["count_share"] >= 0.5
                and top["duration_share"] >= 0.45
                and lead_gap >= 0.12
            )
        )
        if top["sport_bucket"] == "other" and (not dominant or total_load < 160):
            main_sport_type = "健康维持型"
            current_profile = "健康维持型用户"
        elif not dominant:
            main_sport_type = "多运动混合型"
            current_profile = "多运动混合型用户"
        elif top["sport_bucket"] == "cycling":
            main_sport_type = "骑行为主"
            current_profile = (
                "以骑行为主的训练型用户"
                if top["activity_count"] >= 5 or top["total_training_stress_score"] >= 300
                else "以骑行为主的规律运动用户"
            )
            specialized_track = "cycling"
            specialized_track_status = "active"
        elif top["sport_bucket"] == "running":
            main_sport_type = "跑步为主"
            current_profile = "跑步为主的规律运动用户"
            specialized_track = "running"
            specialized_track_status = "pending"
        elif top["sport_bucket"] == "swimming":
            main_sport_type = "游泳为主"
            current_profile = "游泳为主的规律运动用户"
            specialized_track = "swimming"
            specialized_track_status = "pending"
        else:
            main_sport_type = "多运动混合型"
            current_profile = "多运动混合型用户"

    top_reason = []
    if top:
        top_reason = [
            f"{top['sport_label']}近30天共 {top['activity_count']} 次，时长 {top['total_duration_text']}。",
            f"它在次数占比 {round(top['count_share'] * 100)}%、时长占比 {round(top['duration_share'] * 100)}%、负荷占比 {round(top['load_share'] * 100)}%。",
        ]

    return {
        "window_days": 30,
        "main_sport_type": main_sport_type,
        "current_profile": current_profile,
        "specialized_track": specialized_track,
        "specialized_track_status": specialized_track_status,
        "is_temporary_label": True,
        "bucket_stats": scored,
        "reason_points": top_reason,
    }


def build_cycling_specific_baseline(activity_history: list[dict], main_positioning: dict) -> dict:
    cycling_records = [
        record
        for record in activity_history
        if _sport_bucket(record.get("basic_activity", {}).get("sport_type")) == "cycling"
    ]
    if not cycling_records:
        return {
            "enabled": False,
            "specialized_track_status": "inactive",
            "baseline_summary": "最近30天没有足够骑行样本，暂不进入骑行专项线。",
        }

    summary = _weekly_training_summary(cycling_records)
    focus = _weekly_activity_focus(cycling_records)
    activity_count = summary.get("activity_count") or 0
    total_duration = summary.get("total_duration_min") or 0
    total_load = summary.get("total_training_stress_score") or 0
    avg_duration = round(total_duration / activity_count, 1) if activity_count else None
    avg_tss = round(total_load / activity_count, 1) if activity_count else None
    avg_if = average(cycling_records, "sport_specific.intensity_factor")
    avg_cadence = average(cycling_records, "sport_specific.avg_cadence")
    long_ride_count = sum(
        1 for record in cycling_records if (record.get("basic_activity", {}).get("duration_min") or 0) >= 120
    )
    high_load_count = sum(
        1
        for record in cycling_records
        if (record.get("load_recovery", {}).get("training_stress_score") or 0) >= 120
        or (record.get("sport_specific", {}).get("intensity_factor") or 0) >= 0.78
    )
    late_fade_count = sum(
        1 for record in cycling_records if record.get("sport_specific", {}).get("pacing_flag") == "late_fade"
    )
    stable_pacing_count = sum(
        1 for record in cycling_records if record.get("sport_specific", {}).get("pacing_flag") == "stable"
    )

    if (avg_if or 0) >= 0.78 or high_load_count >= max(2, int(activity_count * 0.4)):
        riding_style = "偏强度推进"
    elif long_ride_count >= max(2, int(activity_count * 0.25)) or (avg_duration or 0) >= 100:
        riding_style = "偏耐力型"
    else:
        riding_style = "耐力和强度混合型"

    if (focus.get("top_one_share_pct") or 0) >= 35:
        load_pattern = "单次长骑或重点课更集中"
    elif (focus.get("top_two_share_pct") or 0) >= 60:
        load_pattern = "主要压在两次重点骑行上"
    else:
        load_pattern = "负荷分布相对均匀"

    cadence_note = None
    if avg_cadence is not None:
        if avg_cadence < 72:
            cadence_note = "平均踏频偏低，更偏力量型踩踏。"
        elif avg_cadence < 80:
            cadence_note = "平均踏频中等，偏稳态力量输出。"
        else:
            cadence_note = "平均踏频比较顺，转速型特征更明显。"

    baseline_summary = f"最近30天骑行共 {activity_count} 次，整体 {riding_style}，{load_pattern}。"
    return {
        "enabled": main_positioning.get("specialized_track") == "cycling",
        "specialized_track_status": main_positioning.get("specialized_track_status"),
        "cycling_activity_count": activity_count,
        "cycling_active_days": len(
            {
                record.get("basic_activity", {}).get("date")
                for record in cycling_records
                if record.get("basic_activity", {}).get("date")
            }
        ),
        "total_duration_min": total_duration,
        "total_duration_text": _format_minutes_text(total_duration),
        "total_training_stress_score": total_load,
        "avg_duration_min": avg_duration,
        "avg_duration_text": _format_minutes_text(avg_duration),
        "avg_training_stress_score": avg_tss,
        "avg_intensity_factor": avg_if,
        "avg_cadence": avg_cadence,
        "long_ride_count": long_ride_count,
        "high_load_ride_count": high_load_count,
        "late_fade_count": late_fade_count,
        "stable_pacing_count": stable_pacing_count,
        "riding_style": riding_style,
        "load_pattern": load_pattern,
        "top_session": focus.get("top_one"),
        "top_session_share_pct": focus.get("top_one_share_pct"),
        "cadence_note": cadence_note,
        "baseline_summary": baseline_summary,
    }


def build_user_baseline_payload(user_name: str, sleep_history: list[dict], activity_history: list[dict]) -> dict | None:
    if not sleep_history and not activity_history:
        return None

    sleep_baseline = build_sleep_recovery_baseline(sleep_history)
    general_sport_baseline = build_general_sport_baseline(activity_history)
    main_sport_positioning = build_main_sport_positioning(activity_history, general_sport_baseline)
    cycling_specific_baseline = build_cycling_specific_baseline(activity_history, main_sport_positioning)

    return {
        "message_type": "user_30d_baseline",
        "user_name": user_name,
        "window_days": 30,
        "sleep_recovery_baseline": sleep_baseline,
        "general_sport_baseline": general_sport_baseline,
        "main_sport_positioning": main_sport_positioning,
        "cycling_specific_baseline": cycling_specific_baseline,
        "baseline_summary": {
            "main_sport_type": main_sport_positioning.get("main_sport_type"),
            "current_profile": main_sport_positioning.get("current_profile"),
            "specialized_track": main_sport_positioning.get("specialized_track"),
            "sleep_baseline_summary": sleep_baseline.get("baseline_summary"),
            "general_sport_summary": general_sport_baseline.get("summary_text"),
            "cycling_summary": cycling_specific_baseline.get("baseline_summary"),
        },
    }
