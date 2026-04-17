#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path('/root/garmin_assistant')
DATA_DIR = ROOT / 'data'
BJ_TZ = timezone(timedelta(hours=8))
USER_IDS = ['congzhi', 'yang', 'kevin']


BASELINE_CONFIG = [
    {'name': 'respiration_sleep_avg', 'description': '夜间睡眠平均呼吸频率', 'support_key': 'respiration'},
    {'name': 'resting_hr', 'description': '静息心率', 'support_key': 'rhr_day'},
    {'name': 'hrv_night_avg', 'description': '夜间HRV均值', 'support_key': 'hrv_data'},
    {'name': 'sleep_score', 'description': '睡眠评分', 'support_key': 'sleep_data'},
    {'name': 'deep_sleep_seconds', 'description': '深睡时长（秒）', 'support_key': 'sleep_data'},
    {'name': 'rem_sleep_seconds', 'description': 'REM时长（秒）', 'support_key': 'sleep_data'},
    {'name': 'sleep_duration_seconds', 'description': '总睡眠时长（秒）', 'support_key': 'sleep_data'},
    {'name': 'awake_count', 'description': '夜间清醒次数', 'support_key': 'sleep_data'},
    {'name': 'bb_wake', 'description': '起床时Body Battery', 'support_key': 'body_battery_events'},
    {'name': 'night_stress_avg', 'description': '夜间平均压力值', 'support_key': 'stress_detail'},
    {'name': 'min_spo2', 'description': '最低血氧', 'support_key': 'spo2_data'},
    {'name': 'daily_steps', 'description': '每日总步数', 'support_key': 'steps'},
]


def bj_now() -> datetime:
    return datetime.now(BJ_TZ)


def load_json(path: Path, default: Any):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def profile_path(user_id: str) -> Path:
    return DATA_DIR / user_id / 'profile.json'


def daily_dir(user_id: str) -> Path:
    return DATA_DIR / user_id / 'daily'


def baselines_path(user_id: str) -> Path:
    return DATA_DIR / user_id / 'baselines.json'


def load_profile(user_id: str) -> dict[str, Any]:
    return load_json(profile_path(user_id), {})


def save_profile(user_id: str, payload: dict[str, Any]) -> None:
    save_json(profile_path(user_id), payload)


def list_daily_files(user_id: str, limit: int = 30) -> list[Path]:
    paths = sorted(daily_dir(user_id).glob('*.json'))
    return paths[-limit:]


def safe_get(value: Any, *keys: Any) -> Any:
    current = value
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        elif isinstance(current, list) and isinstance(key, int):
            if 0 <= key < len(current):
                current = current[key]
            else:
                return None
        else:
            return None
        if current is None:
            return None
    return current


def first_metric_value(metrics_map: dict[str, Any] | None, key: str) -> Any:
    if not isinstance(metrics_map, dict):
        return None
    items = metrics_map.get(key)
    if isinstance(items, list) and items:
        return items[0].get('value')
    return None


def extract_respiration_sleep_avg(snapshot: dict[str, Any]) -> Any:
    return safe_get(snapshot, 'data', 'respiration', 'avgSleepRespirationValue') or safe_get(snapshot, 'data', 'sleep', 'dailySleepDTO', 'averageRespirationValue')


def extract_resting_hr(snapshot: dict[str, Any]) -> Any:
    value = first_metric_value(safe_get(snapshot, 'data', 'rhr_day', 'allMetrics', 'metricsMap'), 'WELLNESS_RESTING_HEART_RATE')
    return value if value is not None else safe_get(snapshot, 'data', 'user_summary', 'restingHeartRate')


def extract_hrv_night_avg(snapshot: dict[str, Any]) -> Any:
    return safe_get(snapshot, 'data', 'hrv_data', 'hrvSummary', 'lastNightAvg') or safe_get(snapshot, 'data', 'sleep', 'avgOvernightHrv')


def extract_sleep_score(snapshot: dict[str, Any]) -> Any:
    return safe_get(snapshot, 'data', 'sleep', 'dailySleepDTO', 'sleepScores', 'overall', 'value')


def extract_deep_sleep_seconds(snapshot: dict[str, Any]) -> Any:
    return safe_get(snapshot, 'data', 'sleep', 'dailySleepDTO', 'deepSleepSeconds')


def extract_rem_sleep_seconds(snapshot: dict[str, Any]) -> Any:
    return safe_get(snapshot, 'data', 'sleep', 'dailySleepDTO', 'remSleepSeconds')


def extract_sleep_duration_seconds(snapshot: dict[str, Any]) -> Any:
    return safe_get(snapshot, 'data', 'sleep', 'dailySleepDTO', 'sleepTimeSeconds')


def extract_awake_count(snapshot: dict[str, Any]) -> Any:
    return safe_get(snapshot, 'data', 'sleep', 'dailySleepDTO', 'awakeCount')


def extract_bb_wake(snapshot: dict[str, Any]) -> Any:
    sleep_end = safe_get(snapshot, 'data', 'sleep', 'dailySleepDTO', 'sleepEndTimestampGMT')
    values = safe_get(snapshot, 'data', 'body_battery', 0, 'bodyBatteryValuesArray')
    if isinstance(values, list) and sleep_end is not None:
        candidate = None
        for item in values:
            if isinstance(item, list) and len(item) >= 2:
                ts, level = item[0], item[1]
                if ts <= sleep_end:
                    candidate = level
        if candidate is not None:
            return candidate
    return safe_get(snapshot, 'data', 'user_summary', 'bodyBatteryMostRecentValue')


def extract_night_stress_avg(snapshot: dict[str, Any]) -> Any:
    return safe_get(snapshot, 'data', 'sleep', 'dailySleepDTO', 'avgSleepStress')


def extract_min_spo2(snapshot: dict[str, Any]) -> Any:
    return safe_get(snapshot, 'data', 'spo2_data', 'lowestSpO2') or safe_get(snapshot, 'data', 'sleep', 'dailySleepDTO', 'lowestSpO2Value')


def extract_daily_steps(snapshot: dict[str, Any]) -> Any:
    total = safe_get(snapshot, 'data', 'user_summary', 'totalSteps')
    if total is not None:
        return total
    steps = safe_get(snapshot, 'data', 'steps')
    if isinstance(steps, list):
        return sum(item.get('steps', 0) for item in steps if isinstance(item, dict))
    return None


EXTRACTORS: dict[str, Callable[[dict[str, Any]], Any]] = {
    'respiration_sleep_avg': extract_respiration_sleep_avg,
    'resting_hr': extract_resting_hr,
    'hrv_night_avg': extract_hrv_night_avg,
    'sleep_score': extract_sleep_score,
    'deep_sleep_seconds': extract_deep_sleep_seconds,
    'rem_sleep_seconds': extract_rem_sleep_seconds,
    'sleep_duration_seconds': extract_sleep_duration_seconds,
    'awake_count': extract_awake_count,
    'bb_wake': extract_bb_wake,
    'night_stress_avg': extract_night_stress_avg,
    'min_spo2': extract_min_spo2,
    'daily_steps': extract_daily_steps,
}


def to_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except Exception:
        return None
    return number


def summarize(values: list[float]) -> dict[str, Any]:
    if not values:
        return {}

    rounded_values = [round(v, 2) for v in values]
    filtered = list(values)
    filtered_count = 0

    if len(values) >= 5:
        sorted_vals = sorted(values)
        q1 = sorted_vals[len(sorted_vals) // 4]
        q3 = sorted_vals[3 * len(sorted_vals) // 4]
        iqr = q3 - q1
        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr
        candidate = [v for v in values if lower <= v <= upper]
        if len(candidate) >= len(values) * 0.5:
            filtered = candidate
            filtered_count = len(values) - len(filtered)

    return {
        'mean': round(statistics.mean(filtered), 2),
        'std': round(statistics.stdev(filtered), 2) if len(filtered) >= 2 else None,
        'min': round(min(filtered), 2),
        'max': round(max(filtered), 2),
        'values': rounded_values,
        'filtered_count': filtered_count,
    }


def compute_baselines(user_id: str) -> dict[str, Any]:
    profile = load_profile(user_id)
    available = profile.get('available_data') or {}
    files = list_daily_files(user_id, limit=30)
    snapshots = [load_json(path, {}) for path in files]

    metrics: dict[str, Any] = {}
    recent_7day: dict[str, Any] = {}
    days_with_any_data = 0

    for config in BASELINE_CONFIG:
        if not available.get(config['support_key']):
            continue
        extractor = EXTRACTORS[config['name']]
        values: list[float] = []
        for snapshot in snapshots:
            number = to_number(extractor(snapshot))
            if number is not None:
                values.append(number)
        if values:
            metrics[config['name']] = summarize(values)
            recent_7day[config['name']] = summarize(values[-7:])
            days_with_any_data = max(days_with_any_data, len(values))

    payload = {
        'updated_at': bj_now().isoformat(timespec='seconds'),
        'days_of_data': days_with_any_data,
        'metrics': metrics,
        'recent_7day': recent_7day,
    }
    save_json(baselines_path(user_id), payload)
    update_blood_oxygen_status(user_id, baselines=payload)
    return payload


def get_recent_metric_values(user_id: str, metric_name: str, days: int) -> list[float | None]:
    files = list_daily_files(user_id, limit=days)
    extractor = EXTRACTORS[metric_name]
    values: list[float | None] = []
    for snapshot_path in files:
        snapshot = load_json(snapshot_path, {})
        values.append(to_number(extractor(snapshot)))
    return values


def update_blood_oxygen_status(user_id: str, baselines: dict[str, Any] | None = None) -> None:
    profile = load_profile(user_id)
    status = profile.get('blood_oxygen_status', 'normal')

    recent_14 = get_recent_metric_values(user_id, 'min_spo2', days=14)
    valid_14 = [v for v in recent_14 if v is not None]
    recent_3 = get_recent_metric_values(user_id, 'min_spo2', days=3)
    valid_3 = [v for v in recent_3 if v is not None]

    if status == 'normal' and len(valid_14) >= 10 and all(v < 90 for v in valid_14):
        profile['blood_oxygen_status'] = 'known_baseline'
        profile['blood_oxygen_baseline_range'] = [int(min(valid_14)), int(max(valid_14))]
        save_profile(user_id, profile)
        return

    if status == 'normal':
        if len(valid_3) >= 3 and all(v < 90 for v in valid_3):
            profile['blood_oxygen_status'] = 'warned'
            profile['blood_oxygen_warned_date'] = bj_now().date().isoformat()
            save_profile(user_id, profile)
            return

    if status == 'warned':
        warned_date = profile.get('blood_oxygen_warned_date')
        if warned_date:
            days_since = (bj_now().date() - datetime.fromisoformat(warned_date).date()).days
            if days_since >= 7:
                recent_7 = get_recent_metric_values(user_id, 'min_spo2', days=7)
                valid_7 = [v for v in recent_7 if v is not None]
                if valid_7:
                    profile['blood_oxygen_status'] = 'known_baseline'
                    profile['blood_oxygen_baseline_range'] = [int(min(valid_7)), int(max(valid_7))]
                    save_profile(user_id, profile)
                    return

    save_profile(user_id, profile)


def main() -> int:
    parser = argparse.ArgumentParser(description='计算 Garmin 用户基线')
    parser.add_argument('--user-id', choices=USER_IDS)
    args = parser.parse_args()

    targets = [args.user_id] if args.user_id else USER_IDS
    for user_id in targets:
        payload = compute_baselines(user_id)
        print(user_id, payload.get('days_of_data'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
