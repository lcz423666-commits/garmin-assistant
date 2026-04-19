from __future__ import annotations

import argparse
import json
import math
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from icu_turning_points import (
    detect_decoupling_points,
    detect_high_power_segments,
    detect_power_decay,
    stream_map_from_payload,
)


BJ_TZ = timezone(timedelta(hours=8))
ICU_BASE = "https://intervals.icu/api/v1"
AMAP_REGEOCODE_URL = "https://restapi.amap.com/v3/geocode/regeo"
OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
WIND_SEGMENT_KM = 2.0
ENV_PATH = Path("/root/.env")
STATE_PATH = Path("/root/.icu_cycling_state.json")
TARGET_USER_NAME = "丛至"
ACTIVITIES_LOOKBACK_DAYS = 7
PUSH_TITLE = "🚴 骑行专项分析"


def _log(message: str, log_func=None):
    if log_func:
        log_func(message)
        return
    timestamp = datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [ICU骑行] {message}", flush=True)


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
        _log("ICU_ATHLETE_ID 未配置，跳过骑行分析", log_func)
        return None
    if not api_key:
        _log("ICU_API_KEY 未配置，跳过骑行分析", log_func)
        return None
    return {
        "athlete_id": athlete_id,
        "api_key": api_key,
    }


def _parse_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=BJ_TZ)
    normalized = value.strip().replace("Z", "+00:00")
    if normalized.endswith("+0000"):
        normalized = normalized[:-5] + "+00:00"
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        compact = normalized.replace("T", " ")
        if len(compact) >= 19:
            compact = compact[:19]
        try:
            dt = datetime.strptime(compact, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return datetime.min.replace(tzinfo=BJ_TZ)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=BJ_TZ)
    return dt.astimezone(BJ_TZ)


def _pick(source: dict, *keys, default=None):
    for key in keys:
        if key in source and source.get(key) not in (None, ""):
            return source.get(key)
    return default


def _safe_float(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value):
    numeric = _safe_float(value)
    if numeric is None:
        return None
    return int(round(numeric))


def _safe_tsb(ctl_value, atl_value):
    ctl = _safe_float(ctl_value)
    atl = _safe_float(atl_value)
    if ctl is None or atl is None:
        return None
    return int(round(ctl - atl))


def _safe_minutes(seconds_value):
    seconds = _safe_int(seconds_value)
    if seconds is None:
        return None
    return seconds // 60


def _safe_round(value, digits=1):
    numeric = _safe_float(value)
    if numeric is None:
        return None
    return round(numeric, digits)


def _normalize_if(value):
    numeric = _safe_float(value)
    if numeric is None:
        return None
    if numeric > 2:
        numeric = numeric / 100
    return round(numeric, 2)


def _interval_duration_seconds(interval: dict):
    for key in ("elapsed_secs", "elapsed_time", "moving_time", "secs"):
        numeric = _safe_int(interval.get(key))
        if numeric is not None:
            return numeric
    start_time = _safe_int(interval.get("start_time"))
    end_time = _safe_int(interval.get("end_time"))
    if start_time is not None and end_time is not None and end_time >= start_time:
        return end_time - start_time
    return None


def select_latest_ride(activities: list[dict]) -> dict | None:
    rides = []
    for activity in activities or []:
        activity_type = str(_pick(activity, "type", "activity_type", default="")).lower()
        if activity_type in ("ride", "virtualride"):
            rides.append(activity)
    if not rides:
        return None
    return max(
        rides,
        key=lambda item: _parse_datetime(
            _pick(item, "start_date_local", "start_date", "updated", "created", default="")
        ),
    )


def extract_work_intervals(detail: dict) -> list[dict]:
    intervals = []
    for interval in detail.get("icu_intervals") or []:
        if str(interval.get("type", "")).upper() != "WORK":
            continue
        intervals.append(
            {
                "序号": len(intervals) + 1,
                "时长_秒": _interval_duration_seconds(interval),
                "平均功率": _pick(interval, "average_watts", "icu_average_watts"),
                "NP": _pick(interval, "weighted_average_watts", "icu_weighted_avg_watts"),
                "平均心率": interval.get("average_heartrate"),
                "平均踏频": interval.get("average_cadence"),
                "脱耦_%": interval.get("decoupling"),
                "功率区间": interval.get("zone"),
            }
        )
    return intervals


def extract_ride_data(activity: dict) -> dict:
    icu_ctl = _pick(activity, "icu_ctl", "ctl")
    icu_atl = _pick(activity, "icu_atl", "atl")
    distance_m = _safe_float(_pick(activity, "icu_distance", "distance"))
    joules_above_ftp = _safe_float(activity.get("icu_joules_above_ftp"))
    tsb_value = None
    ctl_value = _safe_float(icu_ctl)
    atl_value = _safe_float(icu_atl)
    if ctl_value is not None and atl_value is not None:
        tsb_value = round(ctl_value - atl_value, 1)
    return {
        "名称": activity.get("name"),
        "日期": _pick(activity, "start_date_local", "start_date"),
        "距离_km": round(distance_m / 1000, 1) if distance_m is not None else None,
        "移动时间_分钟": _safe_minutes(activity.get("moving_time")),
        "总用时_分钟": _safe_minutes(activity.get("elapsed_time")),
        "爬升_米": activity.get("total_elevation_gain"),
        "平均踏频": activity.get("average_cadence"),
        "卡路里": activity.get("calories"),
        "平均功率": _pick(activity, "icu_average_watts", "average_watts"),
        "标准化功率NP": _pick(activity, "icu_weighted_avg_watts", "weighted_average_watts"),
        "强度因子IF": _normalize_if(_pick(activity, "icu_intensity", "intensity")),
        "变异性指数VI": _pick(activity, "icu_variability_index", "variability_index"),
        "效率因子EF": _pick(activity, "icu_efficiency_factor", "efficiency_factor"),
        "功率心率比": _pick(activity, "icu_power_hr", "power_hr"),
        "Z2功率心率比": _pick(activity, "icu_power_hr_z2", "power_hr_z2"),
        "当前FTP": _pick(activity, "icu_ftp", "ftp"),
        "FTP以上做功_kJ": round(joules_above_ftp / 1000, 1) if joules_above_ftp is not None else None,
        "W_bal最大消耗": _pick(activity, "icu_max_wbal_depletion", "max_wbal_depletion"),
        "最大功率": activity.get("max_watts"),
        "是否实测功率": activity.get("device_watts"),
        "滚动FTP": _pick(activity, "icu_rolling_ftp", "rolling_ftp"),
        "FTP变化量": _pick(activity, "icu_rolling_ftp_delta", "rolling_ftp_delta"),
        "左右平衡": activity.get("avg_lr_balance"),
        "功率区间时间_秒": activity.get("icu_zone_times"),
        "功率区间定义_%FTP": activity.get("icu_power_zones"),
        "平均心率": activity.get("average_heartrate"),
        "最大心率": activity.get("max_heartrate"),
        "有氧脱耦_%": activity.get("decoupling"),
        "心率区间时间_秒": activity.get("icu_hr_zone_times"),
        "心率恢复": activity.get("icu_hrr"),
        "TSS": _pick(activity, "icu_training_load", "training_load"),
        "功率TSS": activity.get("power_load"),
        "TRIMP": activity.get("trimp"),
        "活动后CTL_体能": icu_ctl,
        "活动后ATL_疲劳": icu_atl,
        "TSB": tsb_value,
        "天气温度_C": activity.get("average_weather_temp"),
        "体感温度_C": activity.get("average_feels_like"),
        "风速_m_s": activity.get("average_wind_speed"),
        "逆风占比_%": activity.get("headwind_percent"),
        "顺风占比_%": activity.get("tailwind_percent"),
        "降雨_mm": activity.get("max_rain"),
    }


def extract_wellness_data(wellness: dict) -> dict:
    return {
        "rampRate_负荷增速": wellness.get("rampRate"),
        "VO2Max": wellness.get("vo2max"),
        "静息心率": wellness.get("restingHR"),
        "HRV_rMSSD": wellness.get("hrv"),
        "体重_kg": wellness.get("weight"),
    }


def replace_none_with_no_data(value):
    if isinstance(value, dict):
        return {key: replace_none_with_no_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [replace_none_with_no_data(item) for item in value]
    if value is None:
        return "无数据"
    return value


def _format_story_duration(seconds_value) -> str | None:
    seconds = _safe_int(seconds_value)
    if seconds is None:
        return None
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}小时{minutes:02d}分{secs:02d}秒"
    if minutes:
        return f"{minutes}分{secs:02d}秒"
    return f"{secs}秒"


def _nearest_distance_index(distance_stream: list, target_km) -> int | None:
    target_m = _safe_float(target_km)
    if target_m is None:
        return None
    target_m *= 1000
    best_index = None
    best_diff = None
    for index, raw_value in enumerate(distance_stream or []):
        distance_m = _safe_float(raw_value)
        if distance_m is None:
            continue
        diff = abs(distance_m - target_m)
        if best_diff is None or diff < best_diff:
            best_index = index
            best_diff = diff
    return best_index


def resolve_location(lat, lng, log_func=None):
    key = (os.getenv("AMAP_API_KEY") or "").strip()
    if not key:
        return None

    try:
        response = requests.get(
            AMAP_REGEOCODE_URL,
            params={
                "key": key,
                "location": f"{lng},{lat}",
                "radius": 200,
                "extensions": "base",
                "output": "JSON",
            },
            timeout=5,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != "1":
            return None
        regeocode = payload.get("regeocode") or {}
        addr_component = regeocode.get("addressComponent") or {}
        district = str(addr_component.get("district") or "").strip()
        township = str(addr_component.get("township") or "").strip()
        street_number = addr_component.get("streetNumber") or {}
        street = ""
        if isinstance(street_number, dict):
            street = str(street_number.get("street") or "").strip()

        parts = [part for part in (district, township or street) if part]
        if parts:
            return f"{''.join(parts)}附近"

        formatted = str(regeocode.get("formatted_address") or "").strip()
        return formatted or None
    except Exception as exc:
        _log(f"高德逆地理编码失败，按无地点继续: {exc}", log_func)
        return None


def _resolve_location_from_index(streams: dict, index: int | None, cache: dict, log_func=None):
    if index is None:
        return None
    latlng_stream = streams.get("latlng") or []
    if index < 0 or index >= len(latlng_stream):
        return None
    point = latlng_stream[index]
    if not isinstance(point, (list, tuple)) or len(point) < 2:
        return None
    lat = _safe_float(point[0])
    lng = _safe_float(point[1])
    if lat is None or lng is None:
        return None

    cache_key = (round(lat, 5), round(lng, 5))
    if cache_key not in cache:
        cache[cache_key] = resolve_location(lat, lng, log_func=log_func)
    return cache.get(cache_key)


def fetch_streams(activity_id: str, *, api_key: str, types=None, log_func=None) -> dict[str, list]:
    if types is None:
        types = ["watts", "heartrate", "cadence", "distance", "velocity_smooth", "latlng", "time"]
    try:
        payload = _request_json(
            f"{ICU_BASE}/activity/{activity_id}/streams.json",
            api_key=api_key,
            params={"types": ",".join(types)},
            log_func=log_func,
        )
    except Exception as exc:
        _log(f"拉取 ICU streams 失败，按无故事点继续: {exc}", log_func)
        return {}

    stream_map = stream_map_from_payload(payload)
    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict) or item.get("type") != "latlng":
                continue
            lat_values = item.get("data")
            lng_values = item.get("data2")
            if not isinstance(lat_values, list) or not isinstance(lng_values, list):
                continue
            pair_count = min(len(lat_values), len(lng_values))
            stream_map["latlng"] = [
                [lat_values[index], lng_values[index]]
                for index in range(pair_count)
            ]

    return {
        stream_type: list(stream_map.get(stream_type, []))
        for stream_type in types
    }


def build_story_points(streams: dict, ftp, *, detectors=None, log_func=None) -> dict:
    story_points = {
        "高功率段": [],
        "脱钩点": [],
        "衰减段": None,
    }
    if not isinstance(streams, dict) or not streams:
        return story_points

    detector_map = detectors or {
        "detect_high_power_segments": detect_high_power_segments,
        "detect_decoupling_points": detect_decoupling_points,
        "detect_power_decay": detect_power_decay,
    }

    def _run_detector(name: str, *args, **kwargs):
        detector = detector_map.get(name)
        if not callable(detector):
            return []
        try:
            return detector(*args, **kwargs)
        except Exception as exc:
            _log(f"{name} 执行失败，按无故事点继续: {exc}", log_func)
            return []

    ftp_value = _safe_float(ftp) or 240.0
    distance_stream = streams.get("distance") or []
    location_cache: dict[tuple[float, float], str | None] = {}

    high_power_segments = _run_detector("detect_high_power_segments", streams, ftp_value)
    for segment in high_power_segments[:2]:
        midpoint_km = ((_safe_float(segment.get("start_km")) or 0) + (_safe_float(segment.get("end_km")) or 0)) / 2
        midpoint_index = _nearest_distance_index(distance_stream, midpoint_km)
        story_points["高功率段"].append(
            {
                "起止里程_km": [
                    _safe_round(segment.get("start_km")),
                    _safe_round(segment.get("end_km")),
                ],
                "持续时间": _format_story_duration(segment.get("duration_seconds")),
                "平均功率_瓦": _safe_round(segment.get("avg_power")),
                "FTP百分比": _safe_int((_safe_float(segment.get("pct_ftp")) or 0) * 100),
                "平均心率": _safe_int(segment.get("avg_hr")),
                "地点": _resolve_location_from_index(streams, midpoint_index, location_cache, log_func=log_func),
            }
        )

    decoupling_points = _run_detector("detect_decoupling_points", streams)
    for point in decoupling_points[:1]:
        point_index = _nearest_distance_index(distance_stream, point.get("distance_km"))
        story_points["脱钩点"].append(
            {
                "里程_km": _safe_round(point.get("distance_km")),
                "前半段HR_W基线": _safe_round(point.get("baseline_ratio"), 2),
                "当前HR_W": _safe_round(point.get("current_ratio"), 2),
                "上升百分比": _safe_round(point.get("increase_pct")),
                "此时功率": _safe_int(point.get("power")),
                "此时心率": _safe_int(point.get("heartrate")),
                "地点": _resolve_location_from_index(streams, point_index, location_cache, log_func=log_func),
            }
        )

    decay_segments = _run_detector("detect_power_decay", streams)
    if decay_segments:
        best_decay = max(
            decay_segments,
            key=lambda item: _safe_float(item.get("drop_pct")) or 0,
        )
        start_index = _nearest_distance_index(distance_stream, best_decay.get("range_start_km"))
        end_index = _nearest_distance_index(distance_stream, best_decay.get("range_end_km"))
        story_points["衰减段"] = {
            "前段功率": _safe_int(best_decay.get("before_avg_power")),
            "后段功率": _safe_int(best_decay.get("after_avg_power")),
            "下降百分比": _safe_round(best_decay.get("drop_pct")),
            "里程范围_km": [
                _safe_round(best_decay.get("range_start_km")),
                _safe_round(best_decay.get("range_end_km")),
            ],
            "起点地点": _resolve_location_from_index(streams, start_index, location_cache, log_func=log_func),
            "终点地点": _resolve_location_from_index(streams, end_index, location_cache, log_func=log_func),
        }

    return story_points


def _has_story_points_data(story_points: dict | None) -> bool:
    if not isinstance(story_points, dict):
        return False
    if story_points.get("高功率段") not in (None, [], "无数据"):
        return True
    if story_points.get("脱钩点") not in (None, [], "无数据"):
        return True
    return story_points.get("衰减段") not in (None, {}, "无数据")


# ── Phase 2: Wind Analysis ──────────────────────────────────────────────────

def compute_bearing(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Compass bearing from (lat1,lng1) to (lat2,lng2), degrees [0, 360)."""
    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    delta_lng = math.radians(lng2 - lng1)
    x = math.sin(delta_lng) * math.cos(lat2_r)
    y = math.cos(lat1_r) * math.sin(lat2_r) - math.sin(lat1_r) * math.cos(lat2_r) * math.cos(delta_lng)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def classify_wind_relation(bearing: float, wind_direction: float) -> tuple[str, float]:
    """
    wind_direction: meteorological FROM direction (degrees).
    Returns (label, angle_diff) where angle_diff in [0, 180].
    0-45 → 顺风, 135-180 → 逆风, middle → 侧风.
    """
    wind_to = (wind_direction + 180) % 360
    diff = abs(bearing - wind_to) % 360
    if diff > 180:
        diff = 360 - diff
    if diff <= 45:
        return "顺风", round(diff, 1)
    if diff >= 135:
        return "逆风", round(diff, 1)
    return "侧风", round(diff, 1)


_WMO_CODE_CN = {
    0: "晴", 1: "少云", 2: "多云", 3: "阴",
    45: "雾", 48: "雾凇",
    51: "轻毛毛雨", 53: "毛毛雨", 55: "浓毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "冰粒",
    80: "阵雨", 81: "中阵雨", 82: "强阵雨",
    85: "阵雪", 86: "强阵雪",
    95: "雷暴", 96: "雷暴伴冰雹", 99: "强雷暴伴冰雹",
}


def _wmo_code_to_cn(code) -> str:
    c = _safe_int(code)
    if c is None:
        return "未知"
    return _WMO_CODE_CN.get(c, f"天气码{c}")


def fetch_open_meteo_weather(lat: float, lng: float, date_str: str, log_func=None) -> dict | None:
    """
    拉取指定日期和位置的逐小时气象数据（风向、风速、温度、湿度、天气状况、降水）。
    返回 {hour(int) -> {wind_direction, wind_speed_kmh, temp_c, feels_like_c, humidity_pct, weather_cn, precipitation_mm}}
    """
    from datetime import date as date_cls

    try:
        activity_date = date_cls.fromisoformat(date_str)
    except Exception:
        return None

    today = datetime.now(BJ_TZ).date()
    days_ago = (today - activity_date).days

    hourly_vars = "winddirection_10m,windspeed_10m,temperature_2m,apparent_temperature,relativehumidity_2m,weathercode,precipitation"

    if days_ago <= 7:
        url = OPEN_METEO_FORECAST_URL
        params = {
            "latitude": round(lat, 4),
            "longitude": round(lng, 4),
            "hourly": hourly_vars,
            "past_days": days_ago + 1,
            "forecast_days": 1,
            "wind_speed_unit": "kmh",
            "timezone": "Asia/Shanghai",
        }
    else:
        url = OPEN_METEO_ARCHIVE_URL
        params = {
            "latitude": round(lat, 4),
            "longitude": round(lng, 4),
            "start_date": date_str,
            "end_date": date_str,
            "hourly": hourly_vars,
            "wind_speed_unit": "kmh",
            "timezone": "Asia/Shanghai",
        }

    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        _log(f"Open-Meteo 气象数据拉取失败，跳过天气分析: {exc}", log_func)
        return None

    hourly = data.get("hourly") or {}
    times         = hourly.get("time") or []
    directions    = hourly.get("winddirection_10m") or []
    speeds        = hourly.get("windspeed_10m") or []
    temps         = hourly.get("temperature_2m") or []
    feels         = hourly.get("apparent_temperature") or []
    humidities    = hourly.get("relativehumidity_2m") or []
    weathercodes  = hourly.get("weathercode") or []
    precipitations= hourly.get("precipitation") or []

    result = {}
    for i, t in enumerate(times):
        if not str(t).startswith(date_str):
            continue
        try:
            hour = int(str(t)[11:13])
        except Exception:
            continue
        result[hour] = {
            "wind_direction":  float(directions[i])    if i < len(directions)    and directions[i]    is not None else None,
            "wind_speed_kmh":  float(speeds[i])        if i < len(speeds)        and speeds[i]        is not None else None,
            "temp_c":          float(temps[i])         if i < len(temps)         and temps[i]         is not None else None,
            "feels_like_c":    float(feels[i])         if i < len(feels)         and feels[i]         is not None else None,
            "humidity_pct":    int(humidities[i])      if i < len(humidities)    and humidities[i]    is not None else None,
            "weather_cn":      _wmo_code_to_cn(weathercodes[i] if i < len(weathercodes) else None),
            "precipitation_mm":float(precipitations[i])if i < len(precipitations)and precipitations[i]is not None else 0.0,
        }

    return result if result else None


def build_weather_segments(
    streams: dict,
    activity_start_dt: datetime,
    hourly_weather: dict,
    log_func=None,
) -> list[dict]:
    """每 WIND_SEGMENT_KM 计算一个分段，包含航向、风力关系和气象数据。"""
    latlng = streams.get("latlng") or []
    distance = streams.get("distance") or []
    time_stream = streams.get("time") or []

    if not latlng or not distance:
        return []

    n = min(len(latlng), len(distance))
    if n < 2:
        return []

    total_dist_m = _safe_float(distance[n - 1]) or 0
    total_km = total_dist_m / 1000
    if total_km < WIND_SEGMENT_KM:
        return []

    segments = []
    km_mark = WIND_SEGMENT_KM

    while km_mark <= total_km + 0.1:
        prev_km = km_mark - WIND_SEGMENT_KM
        prev_idx = _nearest_distance_index(distance, prev_km)
        curr_idx = _nearest_distance_index(distance, km_mark)

        if prev_idx is None or curr_idx is None or prev_idx >= curr_idx:
            km_mark += WIND_SEGMENT_KM
            continue

        if curr_idx >= len(latlng) or prev_idx >= len(latlng):
            km_mark += WIND_SEGMENT_KM
            continue

        p1 = latlng[prev_idx]
        p2 = latlng[curr_idx]

        if not isinstance(p1, (list, tuple)) or len(p1) < 2:
            km_mark += WIND_SEGMENT_KM
            continue
        if not isinstance(p2, (list, tuple)) or len(p2) < 2:
            km_mark += WIND_SEGMENT_KM
            continue

        lat1, lng1 = _safe_float(p1[0]), _safe_float(p1[1])
        lat2, lng2 = _safe_float(p2[0]), _safe_float(p2[1])

        if None in (lat1, lng1, lat2, lng2):
            km_mark += WIND_SEGMENT_KM
            continue

        if abs(lat2 - lat1) < 1e-5 and abs(lng2 - lng1) < 1e-5:
            km_mark += WIND_SEGMENT_KM
            continue

        bearing = compute_bearing(lat1, lng1, lat2, lng2)

        mid_idx = (prev_idx + curr_idx) // 2
        if time_stream and mid_idx < len(time_stream):
            elapsed_secs = _safe_int(time_stream[mid_idx]) or 0
        else:
            elapsed_secs = 0

        hour = (activity_start_dt + timedelta(seconds=elapsed_secs)).hour
        w = (hourly_weather or {}).get(hour) or {}

        wind_dir = _safe_float(w.get("wind_direction"))
        wind_spd = _safe_float(w.get("wind_speed_kmh"))

        seg = {"里程段_km": f"{prev_km:.0f}-{km_mark:.0f}"}

        if wind_dir is not None and wind_spd is not None:
            label, angle_diff = classify_wind_relation(bearing, wind_dir)
            seg.update({
                "航向_度":   round(bearing, 1),
                "风向_度":   round(wind_dir, 1),
                "风速_kmh":  round(wind_spd, 1),
                "风力关系":  label,
                "偏差角_度": angle_diff,
            })

        temp = _safe_float(w.get("temp_c"))
        feels = _safe_float(w.get("feels_like_c"))
        humidity = w.get("humidity_pct")
        weather_cn = w.get("weather_cn")
        precip = _safe_float(w.get("precipitation_mm"))

        if temp is not None:
            seg["温度_C"] = round(temp, 1)
        if feels is not None and abs((feels or 0) - (temp or 0)) >= 2:
            seg["体感温度_C"] = round(feels, 1)
        if humidity is not None:
            seg["湿度_%"] = humidity
        if weather_cn and weather_cn not in ("晴", "少云", "未知"):
            seg["天气"] = weather_cn
        if precip and precip > 0:
            seg["降水_mm"] = round(precip, 1)

        segments.append(seg)
        km_mark += WIND_SEGMENT_KM

    return segments


def summarize_weather_data(segments: list[dict], hourly_weather: dict, ride_hours: list[int]) -> dict | None:
    """汇总分段数据，生成风力统计 + 整体气象概况。"""
    if not segments:
        return None

    wind_counts = {"顺风": 0, "侧风": 0, "逆风": 0}
    speeds = []
    for seg in segments:
        label = seg.get("风力关系")
        if label in wind_counts:
            wind_counts[label] += 1
        spd = seg.get("风速_kmh")
        if spd is not None:
            speeds.append(spd)

    total = len(segments)
    dominant_wind = max(wind_counts, key=lambda k: wind_counts[k])
    avg_speed = round(sum(speeds) / len(speeds), 1) if speeds else None

    # 整体气象概况：取骑行期间各小时的均值/范围
    temps, feels_list, humidities, precip_total = [], [], [], 0.0
    weather_conditions: list[str] = []
    for h in ride_hours:
        w = (hourly_weather or {}).get(h) or {}
        t = _safe_float(w.get("temp_c"))
        f = _safe_float(w.get("feels_like_c"))
        hu = w.get("humidity_pct")
        p = _safe_float(w.get("precipitation_mm")) or 0.0
        wc = w.get("weather_cn")
        if t is not None:
            temps.append(t)
        if f is not None:
            feels_list.append(f)
        if hu is not None:
            humidities.append(hu)
        precip_total += p
        if wc and wc not in ("未知",) and wc not in weather_conditions:
            weather_conditions.append(wc)

    weather_overview: dict = {}
    if temps:
        weather_overview["气温范围_C"] = [round(min(temps), 1), round(max(temps), 1)]
        weather_overview["平均气温_C"] = round(sum(temps) / len(temps), 1)
    if feels_list:
        avg_feels = round(sum(feels_list) / len(feels_list), 1)
        avg_temp = round(sum(temps) / len(temps), 1) if temps else None
        if avg_temp is None or abs(avg_feels - avg_temp) >= 2:
            weather_overview["平均体感温度_C"] = avg_feels
    if humidities:
        weather_overview["平均湿度_%"] = round(sum(humidities) / len(humidities))
    if weather_conditions:
        weather_overview["天气状况"] = weather_conditions
    if precip_total > 0:
        weather_overview["总降水_mm"] = round(precip_total, 1)

    return {
        "分段详情": segments,
        "顺风段数": wind_counts["顺风"],
        "侧风段数": wind_counts["侧风"],
        "逆风段数": wind_counts["逆风"],
        "总段数": total,
        "主导风力关系": dominant_wind,
        "平均风速_kmh": avg_speed,
        "气象概况": weather_overview,
    }


def build_weather_analysis(
    streams: dict,
    activity_start_iso: str,
    activity_date: str,
    total_duration_minutes: int | None = None,
    log_func=None,
) -> dict | None:
    """Phase 2 天气分析入口，返回包含风力分段和气象概况的完整字典。"""
    latlng = streams.get("latlng") or []
    if not latlng:
        _log("streams 中无 latlng 数据，跳过天气分析", log_func)
        return None

    start_lat, start_lng = None, None
    for point in latlng:
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            start_lat = _safe_float(point[0])
            start_lng = _safe_float(point[1])
            if start_lat is not None and start_lng is not None:
                break

    if start_lat is None or start_lng is None:
        _log("latlng 流无有效坐标，跳过天气分析", log_func)
        return None

    hourly_weather = fetch_open_meteo_weather(start_lat, start_lng, activity_date, log_func=log_func)
    if not hourly_weather:
        _log("Open-Meteo 未返回有效气象数据，跳过天气分析", log_func)
        return None

    activity_start_dt = _parse_datetime(activity_start_iso)
    segments = build_weather_segments(streams, activity_start_dt, hourly_weather, log_func=log_func)

    # 计算活动覆盖的小时列表（用于气象概况）
    start_hour = activity_start_dt.hour
    duration_hours = max(1, int(math.ceil((total_duration_minutes or 60) / 60)))
    ride_hours = list({(start_hour + i) % 24 for i in range(duration_hours + 1)})

    summary = summarize_weather_data(segments, hourly_weather, ride_hours)
    if summary is None:
        _log("天气分段计算结果为空，跳过天气分析", log_func)
        return None

    _log(
        f"天气分析完成: {summary['总段数']} 段，"
        f"顺风 {summary['顺风段数']} / 侧风 {summary['侧风段数']} / 逆风 {summary['逆风段数']}，"
        f"均速 {summary['平均风速_kmh']} km/h，"
        f"气象: {summary.get('气象概况', {})}",
        log_func,
    )
    return summary


# ── End Phase 2 ─────────────────────────────────────────────────────────────


def fetch_same_day_wellness(*, athlete_id: str, api_key: str, activity_date: str, log_func=None) -> dict:
    if not activity_date:
        return {}
    url = f"{ICU_BASE}/athlete/{athlete_id}/wellness/{activity_date}"
    try:
        payload = _request_json(url, api_key=api_key, log_func=log_func)
        if isinstance(payload, list):
            return payload[0] if payload else {}
        return payload if isinstance(payload, dict) else {}
    except Exception as exc:
        _log(f"ICU wellness 拉取失败，按无数据继续: {exc}", log_func)
        return {}


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


def _build_analysis_prompt(payload: dict) -> str:
    ride_data = payload["ride_data"]
    work_intervals = payload["work_intervals"]
    wellness_data = payload["wellness_data"]
    story_points = payload.get("story_points") or {"高功率段": [], "脱钩点": [], "衰减段": "无数据"}
    weather_analysis = payload.get("weather_analysis")

    weather_section = ""
    if weather_analysis and isinstance(weather_analysis, dict) and weather_analysis.get("总段数", 0) > 0:
        weather_section = (
            f"\n\n天气与风力分析数据：\n{json.dumps(weather_analysis, ensure_ascii=False, indent=2)}\n"
        )

    weather_prompt_part = ""
    if weather_section:
        weather_prompt_part = (
            "第三部分 · 天气与环境影响分析（3-4句，仅当有 weather_analysis 数据时写）\n"
            "根据传入的天气分析数据，综合评估本次骑行的气象环境对表现的影响。必须遵守：\n"
            "- 先说整体气象背景（温度、湿度、天气状况），判断环境是否对表现有明显正面或负面影响\n"
            "- 如果气温偏高（>28°C）或湿度偏高（>75%），要说明其对心率漂移、体感疲劳和散热的影响\n"
            "- 如果体感温度和实际温度差距明显（>3°C），要说明原因（热浪、大风）\n"
            "- 说明整体风力情况：哪些路段是顺风/逆风/侧风，风速是否显著（>15 km/h 才值得单独点出）\n"
            "- 把逆风段、侧风段和功率/心率数据联系起来，判断风阻是否是后期表现下滑的外部因素\n"
            "- 如果有降水，说明降水对路面、安全或体感的影响\n"
            "- 如果天气条件整体温和（无极端气温、无强风、无降水），可以一句话带过，不要无中生有夸大影响\n\n"
        )
        story_part_number = "第四部分"
        load_part_number = "第五部分"
        advice_part_number = "第六部分"
    else:
        story_part_number = "第三部分"
        load_part_number = "第四部分"
        advice_part_number = "第五部分"

    return (
        "你是一位资深骑行教练和运动科学顾问。根据以下 Intervals.icu 数据，为用户做一次全面的骑行表现分析。"
        "用户只看这条推送，不会去看其他平台，所以你的分析需要完整覆盖这次骑行的所有关键维度。\n\n"
        f"本次骑行数据：\n{json.dumps(ride_data, ensure_ascii=False, indent=2)}\n\n"
        f"间歇段数据（补充参考）：\n{json.dumps(work_intervals, ensure_ascii=False, indent=2) if work_intervals else '本次骑行无结构化间歇段'}\n\n"
        f"运动员当日状态：\n{json.dumps(wellness_data, ensure_ascii=False, indent=2)}\n\n"
        f"story_points（拐点故事点）：\n{json.dumps(story_points, ensure_ascii=False, indent=2)}"
        f"{weather_section}\n"
        "请按以下结构输出分析，每部分自然过渡不加标题，语气像一个懂训练的骑行朋友，专业但不学术：\n\n"
        "第一部分 · 骑行概况与整体判断（2-3句）\n"
        "基于距离、爬升、时长，给出这次骑行的整体定性判断。\n\n"
        "第二部分 · 功率与效率深度分析（5-7句）\n"
        "需要解读以下所有可用指标，把数据融入分析，不要罗列：IF、NP 与平均功率、VI、有氧脱耦、EF、W'bal 最大消耗、FTP变化量、左右平衡。\n\n"
        f"{weather_prompt_part}"
        f"{story_part_number} · 骑行故事点分析（3-5句，如果有 story_points 数据则写，没有则跳过）\n"
        "根据传入的 story_points 数据，讲述这次骑行中的关键转折。必须严格遵守以下要求：\n"
        "- 每个故事点都必须明确说出地名，不能省略。高功率段、脱钩点、衰减段起点、衰减段终点，4 个地名必须全部出现在文案里。\n"
        "- 地名用口语方式自然表达，比如\"骑到辽阳县隆昌镇那边\"，不要说\"经纬度\"或\"坐标\"。\n"
        "- 如果故事点在里程或地点上有因果衔接，必须明确指出这种关联。\n"
        "- 高功率段：地名 + 里程 + FTP 百分比 + 持续时间 + 是主动进攻还是爬坡判断。\n"
        "- 脱钩点：地名 + 里程 + HR/W 上升幅度 + 身体状态判断。\n"
        "- 衰减段：起点地名 + 终点地名 + 里程范围 + 功率下降幅度 + 与脱钩点的因果关系。\n"
        "注意：如果输出中漏掉任何一个传入的地名，视为失败。\n\n"
        f"{load_part_number} · 训练负荷与训练阶段评估（4-5句）\n"
        "基于 TSS、TSB、CTL、ATL、rampRate 和 VO2Max 给出训练阶段判断标签，并解释当前所处状态。\n\n"
        f"{advice_part_number} · 接下来 24-48 小时训练建议（3-4句）\n"
        "给具体可执行建议，不要只说注意休息。结合 TSB、rampRate 和心率恢复判断是完全休息、恢复骑、正常训练还是可以上强度。\n\n"
        "整体要求：\n"
        "- 用北京时间问候开头\n"
        "- 语气像一个懂训练的骑行朋友，专业但口语化，不要学术腔\n"
        "- 禁止使用 markdown 加粗（** **）语法\n"
        "- 不要罗列原始数据表格，把数据融入分析语句中自然引用\n"
        "- 内容完整优先，不要为了控制字数删掉有价值的分析；如果内容充实，写到 900 字也没问题\n"
        "- 输出纯文本，不要任何 HTML 标签或 markdown 格式\n"
    )


def _generate_analysis(payload: dict):
    from llm_helper import LLM_MODEL, client

    response = client.chat.completions.create(
        model=LLM_MODEL,
        max_tokens=1500,
        messages=[
            {"role": "system", "content": "你是一位资深骑行教练。"},
            {"role": "user", "content": _build_analysis_prompt(payload)},
        ],
    )
    return _trim_if_runaway(
        (response.choices[0].message.content or "").strip(),
        has_story_points=_has_story_points_data(payload.get("story_points")),
        has_weather=bool(payload.get("weather_analysis")),
    )


def _trim_if_runaway(text: str, *, has_story_points: bool, has_weather: bool = False) -> str:
    """仅当输出明显超长（>1200字）时才做一次压缩，保留所有有价值内容。"""
    content = (text or "").strip()
    if not content or len(content) <= 1200:
        return content

    from llm_helper import LLM_MODEL, client

    if has_weather:
        dims = "骑行概况、功率效率、天气环境、骑行故事点、训练负荷、训练建议"
    elif has_story_points:
        dims = "骑行概况、功率效率、骑行故事点、训练负荷、训练建议"
    else:
        dims = "骑行概况、功率效率、训练负荷、训练建议"

    response = client.chat.completions.create(
        model=LLM_MODEL,
        max_tokens=1200,
        messages=[
            {
                "role": "system",
                "content": (
                    "你是一位资深骑行教练和精炼编辑。"
                    f"请把以下骑行分析改写到 800-1000 字，保留所有有价值的分析内容，"
                    f"只删除明显重复、无意义的铺垫和套话。"
                    f"必须保留这些分析维度：{dims}。"
                    "不要加标题，不要使用 markdown，不要丢失关键数字和地名。"
                ),
            },
            {
                "role": "user",
                "content": f"当前约 {len(content)} 字，请精简到 800-1000 字：\n\n{content}",
            },
        ],
    )
    candidate = (response.choices[0].message.content or "").strip()
    return candidate if candidate else content

def _push_message(user: dict, title: str, content: str, log_func=None) -> bool:
    token = (user.get("pushplus_token") or "").strip()
    if not token:
        _log("丛至的 PushPlus Token 缺失，跳过 ICU 骑行推送", log_func)
        return False
    response = requests.post(
        "https://www.pushplus.plus/send",
        json={
            "token": token,
            "title": title,
            "content": content,
        },
        timeout=15,
    )
    result = response.json()
    if result.get("code") == 200:
        _log(f"ICU 骑行推送成功: {title}", log_func)
        return True
    _log(f"ICU 骑行推送失败: {result.get('msg', '未知错误')}", log_func)
    return False


def _load_target_user():
    from app_config import load_users

    for user in load_users():
        if user.get("name") == TARGET_USER_NAME:
            return user
    return None


def check_and_push_cycling(*, user=None, test: bool = False, log_func=None) -> bool:
    target_user = user or _load_target_user()
    if not target_user or target_user.get("name") != TARGET_USER_NAME:
        return False

    settings = _load_runtime_settings(log_func)
    if not settings:
        return False

    today = datetime.now(BJ_TZ).date()
    oldest = today - timedelta(days=ACTIVITIES_LOOKBACK_DAYS)
    activities = _request_json(
        f"{ICU_BASE}/athlete/{settings['athlete_id']}/activities",
        api_key=settings["api_key"],
        params={"oldest": oldest.isoformat(), "newest": today.isoformat()},
        log_func=log_func,
    )
    latest_ride = select_latest_ride(activities if isinstance(activities, list) else [])
    if not latest_ride:
        _log("最近 7 天没有新的 ICU 骑行活动，跳过", log_func)
        return False

    activity_id = str(_pick(latest_ride, "id", "activity_id", default=""))
    state = _load_state()
    if not test and state.get("last_activity_id") == activity_id:
        _log(f"ICU 骑行活动 {activity_id} 已推送过，跳过", log_func)
        return False

    detail = _request_json(
        f"{ICU_BASE}/activity/{activity_id}",
        api_key=settings["api_key"],
        params={"intervals": "true"},
        log_func=log_func,
    )
    detail_payload = detail if isinstance(detail, dict) else {}
    streams = fetch_streams(activity_id, api_key=settings["api_key"], log_func=log_func)
    story_points_raw = build_story_points(
        streams,
        _pick(detail_payload, "icu_ftp", "ftp", default=240),
        log_func=log_func,
    )
    ride_data = extract_ride_data(detail_payload)
    activity_date = str(ride_data.get("日期") or "")[:10]

    # Phase 2: weather analysis
    activity_start_iso = str(_pick(latest_ride, "start_date_local", "start_date") or "")
    total_duration_min = _safe_int(_pick(detail_payload, "elapsed_time", "moving_time"))
    weather_analysis_raw = build_weather_analysis(
        streams,
        activity_start_iso,
        activity_date,
        total_duration_minutes=total_duration_min,
        log_func=log_func,
    )

    wellness_raw = fetch_same_day_wellness(
        athlete_id=settings["athlete_id"],
        api_key=settings["api_key"],
        activity_date=activity_date,
        log_func=log_func,
    )
    payload = {
        "ride_data": replace_none_with_no_data(ride_data),
        "work_intervals": replace_none_with_no_data(extract_work_intervals(detail_payload)),
        "wellness_data": replace_none_with_no_data(extract_wellness_data(wellness_raw)),
        "story_points": replace_none_with_no_data(story_points_raw),
        "weather_analysis": replace_none_with_no_data(weather_analysis_raw) if weather_analysis_raw else None,
    }
    analysis = _generate_analysis(payload)

    if test:
        print(json.dumps(payload["ride_data"], ensure_ascii=False, indent=2))
        print("\n=== ICU Cycling Work Intervals ===\n")
        print(json.dumps(payload["work_intervals"], ensure_ascii=False, indent=2))
        print("\n=== ICU Cycling Wellness Data ===\n")
        print(json.dumps(payload["wellness_data"], ensure_ascii=False, indent=2))
        print("\n=== ICU Cycling Story Points ===\n")
        print(json.dumps(payload["story_points"], ensure_ascii=False, indent=2))
        print("\n=== ICU Cycling Weather Analysis ===\n")
        print(json.dumps(payload["weather_analysis"], ensure_ascii=False, indent=2))
        print("\n=== ICU Cycling Message ===\n")
        print(analysis)
        return False

    pushed = _push_message(target_user, PUSH_TITLE, analysis, log_func)
    if pushed:
        _save_state(
            {
                "last_activity_id": activity_id,
                "updated_at": datetime.now(BJ_TZ).isoformat(),
            }
        )
    return pushed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()
    check_and_push_cycling(test=args.test)


if __name__ == "__main__":
    main()
