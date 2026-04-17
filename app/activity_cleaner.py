"""
activity_cleaner.py
清洗佳明运动原始 JSON 数据，删除对 LLM 分析无意义的字段，
按运动类型进行差异化处理，减少 token 消耗。
"""

from __future__ import annotations

from copy import deepcopy

from phase1_builder import summarize_activity_background, summarize_cycling_specific

# ============================================================
# 通用删除字段（所有运动类型）
# ============================================================
UNIVERSAL_REMOVE = frozenset({
    # 用户身份
    "ownerId", "ownerFullName", "ownerDisplayName",
    "ownerProfileImageUrl", "ownerProfileImageUrlSmall", "ownerProfileImageUrlMedium", "ownerProfileImageUrlLarge",
    "userRoles", "userProfileId",
    # 隐私权限
    "privacy", "accessControlRuleDTO",
    # 设备元数据
    "metadataDTO", "deviceId", "manufacturer", "activityUUID",
    # 重复 DTO（关键字段已在主脚本中选取）
    "activityTypeDTO", "eventTypeDTO", "timeZoneUnitDTO", "summaryDTO",
    # 平台时间/系统字段
    "timeZoneId", "beginTimestamp",
    # 坐标（隐私且对 LLM 分析无意义）
    "startLatitude", "startLongitude", "endLatitude", "endLongitude",
    # 地图/多媒体标记
    "hasPolyline", "hasHeatMap", "hasImages", "hasVideo", "hasSplits",
    # 无实质分析价值的布尔状态位
    "favorite", "pr", "elevationCorrected", "atpActivity", "purposeful",
    "manualActivity", "qualifyingDive", "decoDive", "autoCalcCalories",
    "parent", "userPro", "lapCount",
    # 潜水摘要（非潜水运动为空，潜水时保留详细字段）
    "summarizedDiveInfo",
})

# ============================================================
# 按运动类型额外删除的字段
# ============================================================

# 游泳：无需功率、踏频等骑车专属字段
_SWIM_REMOVE = frozenset({
    "averagePower", "maxPower", "normalizedPower", "totalWork",
    "averageBikeCadence", "maxBikeCadence",
    "leftBalance", "rightBalance",
    "avgLeftTorqueEffectiveness", "avgRightTorqueEffectiveness",
    "avgLeftPedalSmoothness", "avgRightPedalSmoothness",
    "avgGrit", "avgFlow",
})

# 骑行：无需游泳圈数字段
_CYCLING_REMOVE = frozenset({
    "poolLength", "unitOfPoolLength", "averageSwolf",
    "avgStrokes", "maxSwolf", "strokes",
})

CYCLING_TYPE_KEYS = frozenset({
    "cycling",
    "road_biking",
    "mountain_biking",
    "indoor_cycling",
    "virtual_ride",
    "gravel_cycling",
    "bmx",
})


def is_cycling_type(type_key: str) -> bool:
    value = (type_key or "").lower()
    if value in CYCLING_TYPE_KEYS:
        return True
    return any(token in value for token in ("cycling", "biking", "ride"))

# 跑步：去掉游泳、骑行专属字段
_RUNNING_REMOVE = frozenset({
    "poolLength", "unitOfPoolLength", "averageSwolf",
    "avgStrokes", "maxSwolf", "strokes",
    "averagePower", "maxPower", "normalizedPower", "totalWork",
    "averageBikeCadence", "maxBikeCadence",
    "leftBalance", "rightBalance",
    "avgGrit", "avgFlow",
})

# 力量/健身房训练：去掉速度、距离、踏频字段
_STRENGTH_REMOVE = frozenset({
    "poolLength", "unitOfPoolLength", "averageSwolf",
    "avgStrokes", "maxSwolf", "strokes",
    "averageBikeCadence", "maxBikeCadence",
    "leftBalance", "rightBalance",
    "avgGrit", "avgFlow",
})

# 潜水：去掉速度、踏频、功率等无关字段
_DIVING_REMOVE = frozenset({
    "averageBikeCadence", "maxBikeCadence",
    "averagePower", "maxPower", "normalizedPower", "totalWork",
    "leftBalance", "rightBalance",
    "poolLength", "unitOfPoolLength", "averageSwolf",
    "avgStrokes", "maxSwolf", "strokes",
    "avgGrit", "avgFlow",
})

_ACTIVITY_TYPE_MAP = {
    # 跑步类
    "running": _RUNNING_REMOVE,
    "trail_running": _RUNNING_REMOVE,
    "treadmill_running": _RUNNING_REMOVE,
    "ultra_run": _RUNNING_REMOVE,
    "indoor_running": _RUNNING_REMOVE,
    "obstacle_run": _RUNNING_REMOVE,
    "virtual_run": _RUNNING_REMOVE,
    # 骑行类
    "cycling": _CYCLING_REMOVE,
    "road_biking": _CYCLING_REMOVE,
    "mountain_biking": _CYCLING_REMOVE,
    "indoor_cycling": _CYCLING_REMOVE,
    "virtual_ride": _CYCLING_REMOVE,
    "gravel_cycling": _CYCLING_REMOVE,
    "bmx": _CYCLING_REMOVE,
    # 游泳类
    "lap_swimming": _SWIM_REMOVE,
    "open_water_swimming": _SWIM_REMOVE,
    "swimming": _SWIM_REMOVE,
    # 力量/健身类
    "strength_training": _STRENGTH_REMOVE,
    "fitness_equipment": _STRENGTH_REMOVE,
    "gym_and_fitness_equipment": _STRENGTH_REMOVE,
    "indoor_cardio": _STRENGTH_REMOVE,
    "hiit": _STRENGTH_REMOVE,
    "yoga": _STRENGTH_REMOVE,
    "pilates": _STRENGTH_REMOVE,
    # 潜水类
    "diving": _DIVING_REMOVE,
    "single_gas_diving": _DIVING_REMOVE,
    "multi_gas_diving": _DIVING_REMOVE,
    "gauge_diving": _DIVING_REMOVE,
    "apnea_diving": _DIVING_REMOVE,
    "apnea_hunting": _DIVING_REMOVE,
}


def _get_activity_type_key(data: dict) -> str:
    """从数据中提取运动类型 key（小写）"""
    act_type = data.get("activityType", {})
    if isinstance(act_type, dict):
        key = act_type.get("typeKey", "")
        if key:
            return key.lower()
    # 退而求其次用 activityTypeDTO
    act_type_dto = data.get("activityTypeDTO", {})
    if isinstance(act_type_dto, dict):
        key = act_type_dto.get("typeKey", "")
        if key:
            return key.lower()
    return ""


def clean_activity(data: dict) -> dict:
    """
    清洗单条运动数据，返回副本（不修改原数据）。
    步骤：
    1. 删除通用无效字段
    2. 按运动类型删除额外字段
    """
    result = {k: v for k, v in data.items() if k not in UNIVERSAL_REMOVE}

    type_key = _get_activity_type_key(data)
    extra_remove = _ACTIVITY_TYPE_MAP.get(type_key, frozenset())
    if extra_remove:
        result = {k: v for k, v in result.items() if k not in extra_remove}

    return result


def _minutes(value):
    if value in (None, ""):
        return None
    return round(float(value) / 60, 1)


def _speed_to_kmh(value):
    if value in (None, ""):
        return None
    return round(float(value) * 3.6, 1)


def _round(value, digits=1):
    if value in (None, ""):
        return None
    return round(float(value), digits)


def _badge_summary(badges):
    badges = badges or []
    names = [
        badge.get("badgeName") or badge.get("badgeKey")
        for badge in badges[:10]
        if badge.get("badgeName") or badge.get("badgeKey")
    ]
    return {
        "recent_badge_names": names[:5],
        "has_recent_milestone_badge": any("milestone" in name.lower() for name in names),
        "badge_count_recent": len(names),
    }


def build_full_activity_record(activity: dict, detail: dict, splits_raw: dict | None, badges: list | None) -> dict:
    full_data = {}
    for source in (activity or {}, detail or {}):
        for key, value in source.items():
            if value is not None and key not in full_data:
                full_data[key] = value

    for speed_key in ("averageSpeed", "maxSpeed"):
        if speed_key in full_data:
            full_data[speed_key] = _speed_to_kmh(full_data[speed_key])

    if isinstance(splits_raw, dict):
        lap_dtos = splits_raw.get("lapDTOs", []) or []
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
        cleaned_splits = []
        for lap in lap_dtos:
            row = {key: value for key, value in lap.items() if key in keep}
            for speed_key in ("averageSpeed", "averageMovingSpeed", "maxSpeed"):
                if speed_key in row:
                    row[speed_key] = _speed_to_kmh(row[speed_key])
            if "duration" in row:
                row["duration"] = _minutes(row["duration"])
            if "movingDuration" in row:
                row["movingDuration"] = _minutes(row["movingDuration"])
            cleaned_splits.append(row)
        full_data["splits"] = cleaned_splits

    if badges:
        full_data["recent_badges"] = [
            {
                "name": badge.get("badgeName") or badge.get("badgeKey"),
                "earned_date": badge.get("badgeEarnedDate") or badge.get("earnedDate"),
                "category": badge.get("badgeCategoryId"),
            }
            for badge in badges[:10]
        ]

    return full_data


def normalize_activity(
    activity: dict,
    detail: dict,
    splits_raw: dict | None,
    badges: list | None,
    activity_date: str,
    recent_history: list[dict],
) -> tuple[dict, dict]:
    full_data = build_full_activity_record(activity, detail, splits_raw, badges)
    cleaned_for_payload = clean_activity(deepcopy(full_data))
    splits = cleaned_for_payload.get("splits", []) or []
    sport_type = ((activity or {}).get("activityType") or {}).get("typeKey", "").lower()

    normalized = {
        "date": activity_date,
        "basic_activity": {
            "activity_id": str((activity or {}).get("activityId") or (detail or {}).get("activityId") or ""),
            "date": activity_date,
            "start_time_local": (activity or {}).get("startTimeLocal") or (detail or {}).get("startTimeLocal"),
            "sport_type": sport_type or "unknown",
            "activity_name": (activity or {}).get("activityName")
            or ((activity or {}).get("activityType") or {}).get("typeKey", "未知运动"),
            "distance_km": round(((activity or {}).get("distance") or 0) / 1000, 1),
            "duration_min": _minutes((activity or {}).get("duration")) or 0,
            "moving_duration_min": _minutes((activity or {}).get("movingDuration")),
            "elapsed_duration_min": _minutes((activity or {}).get("elapsedDuration")),
            "stop_duration_min": _round(
                (_minutes((activity or {}).get("elapsedDuration")) or 0)
                - (_minutes((activity or {}).get("movingDuration")) or 0),
                1,
            ),
            "elevation_gain_m": (activity or {}).get("elevationGain"),
            "avg_speed_kmh": cleaned_for_payload.get("averageSpeed"),
            "max_speed_kmh": cleaned_for_payload.get("maxSpeed"),
            "calories": (activity or {}).get("calories"),
            "avg_hr": (activity or {}).get("averageHR"),
            "max_hr": (activity or {}).get("maxHR"),
        },
        "load_recovery": {
            "training_stress_score": cleaned_for_payload.get("trainingStressScore"),
            "activity_training_load": cleaned_for_payload.get("activityTrainingLoad"),
            "aerobic_te": cleaned_for_payload.get("aerobicTrainingEffect"),
            "anaerobic_te": cleaned_for_payload.get("anaerobicTrainingEffect"),
            "training_effect_label": cleaned_for_payload.get("trainingEffectLabel"),
            "moderate_minutes": cleaned_for_payload.get("moderateIntensityMinutes"),
            "vigorous_minutes": cleaned_for_payload.get("vigorousIntensityMinutes"),
            "estimated_recovery_time": cleaned_for_payload.get("recoveryTime"),
            "water_estimated_ml": cleaned_for_payload.get("waterEstimated"),
            "water_consumed_ml": cleaned_for_payload.get("waterConsumed"),
            "temperature_min": cleaned_for_payload.get("minTemperature"),
            "temperature_max": cleaned_for_payload.get("maxTemperature"),
        },
        "sport_specific": {},
        "continuous_background": {},
        "achievement_summary": _badge_summary(badges),
    }

    if is_cycling_type(sport_type):
        normalized["sport_specific"] = summarize_cycling_specific(cleaned_for_payload, splits)

    normalized["continuous_background"] = summarize_activity_background(normalized, recent_history)
    return normalized, cleaned_for_payload
