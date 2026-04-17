#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path('/root/garmin_assistant')
APP_DIR = ROOT / 'app'
DATA_DIR = ROOT / 'data'
BJ_TZ = timezone(timedelta(hours=8))
CALL_DELAY_SECONDS = 1.5
RETENTION_DAYS = 35

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import garmin_monitor as gm  # noqa: E402
from user_identity import list_enabled_user_ids, resolve_user_by_user_id  # noqa: E402

SUPPORT_KEY_MAP = {
    'sleep': 'sleep_data',
    'hrv_data': 'hrv_data',
    'spo2_data': 'spo2_data',
    'respiration': 'respiration',
    'rhr_day': 'rhr_day',
    'body_battery': 'body_battery_events',
    'body_battery_events': 'body_battery_events',
    'stress_detail': 'stress_detail',
    'all_day_stress': 'all_day_stress',
    'heart_rate_detail': 'heart_rate_detail',
    'steps': 'steps',
    'floors': 'floors',
    'intensity_minutes': 'intensity_minutes',
    'weekly_intensity_minutes': 'weekly_intensity_minutes',
    'training_status': 'training_status',
    'training_readiness': 'training_readiness',
    'morning_training_readiness': 'morning_training_readiness',
    'max_metrics': 'max_metrics',
    'fitnessage': 'fitnessage',
    'endurance_score': 'endurance_score',
    'hill_score': 'hill_score',
    'cycling_ftp': 'cycling_ftp',
    'lactate_threshold': 'lactate_threshold',
    'user_summary': 'user_summary',
    'daily_events': 'daily_events',
    'lifestyle_logging': 'lifestyle_logging',
}

DATA_FETCH_MAP = {
    'sleep': {'method': 'get_sleep_data'},
    'hrv_data': {'method': 'get_hrv_data'},
    'spo2_data': {'method': 'get_spo2_data'},
    'respiration': {'method': 'get_respiration_data'},
    'rhr_day': {'method': 'get_rhr_day'},
    'body_battery': {'method': 'get_body_battery'},
    'body_battery_events': {'method': 'get_body_battery_events'},
    'stress_detail': {'method': 'get_stress_data'},
    'all_day_stress': {'method': 'get_all_day_stress'},
    'heart_rate_detail': {'method': 'get_heart_rates'},
    'steps': {'method': 'get_steps_data'},
    'floors': {'method': 'get_floors'},
    'intensity_minutes': {'method': 'get_intensity_minutes_data'},
    'weekly_intensity_minutes': {'method': 'get_weekly_intensity_minutes'},
    'training_status': {'method': 'get_training_status'},
    'training_readiness': {'method': 'get_training_readiness'},
    'morning_training_readiness': {'method': 'get_morning_training_readiness'},
    'max_metrics': {'method': 'get_max_metrics'},
    'fitnessage': {'method': 'get_fitnessage_data'},
    'endurance_score': {'method': 'get_endurance_score'},
    'hill_score': {'method': 'get_hill_score'},
    'cycling_ftp': {'method': 'get_cycling_ftp'},
    'lactate_threshold': {'method': 'get_lactate_threshold'},
    'user_summary': {'method': 'get_user_summary'},
    'daily_events': {'method': 'get_all_day_events'},
    'lifestyle_logging': {'method': 'get_lifestyle_logging_data'},
}


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


def get_profile_path(user_id: str) -> Path:
    return DATA_DIR / user_id / 'profile.json'


def get_daily_path(user_id: str, date_str: str) -> Path:
    return DATA_DIR / user_id / 'daily' / f'{date_str}.json'


def load_profile(user_id: str) -> dict[str, Any]:
    profile = load_json(get_profile_path(user_id), {})
    if not profile:
        raise FileNotFoundError(f'profile.json 不存在: {get_profile_path(user_id)}')
    return profile


def resolve_user(user_id: str) -> dict[str, Any]:
    return resolve_user_by_user_id(user_id)


def feature_enabled(profile: dict[str, Any], fetch_key: str) -> bool:
    available = profile.get('available_data') or {}
    support_key = SUPPORT_KEY_MAP.get(fetch_key, fetch_key)
    if support_key in available:
        return bool(available[support_key])
    if fetch_key == 'body_battery':
        return bool(available.get('body_battery_events'))
    return False


def call_api(api: Any, fetch_key: str, date_str: str) -> Any:
    method_name = DATA_FETCH_MAP[fetch_key]['method']
    method = getattr(api, method_name, None)
    if method_name == 'get_intensity_minutes_data' and method is None:
        method = getattr(api, 'get_intensity_minutes', None)
    if method_name == 'get_all_day_events' and method is None:
        method = getattr(api, 'get_daily_events', None)
    if method is None:
        raise AttributeError(f'方法不可用: {method_name}')

    if method_name in {'get_cycling_ftp', 'get_lactate_threshold'}:
        return method()
    if method_name in {'get_endurance_score', 'get_hill_score'}:
        return method(date_str, date_str)
    if method_name == 'get_weekly_intensity_minutes':
        current = date.fromisoformat(date_str)
        start = (current - timedelta(days=current.weekday())).isoformat()
        return method(start, date_str)
    return method(date_str)


def cleanup_old_daily_files(user_id: str, keep_days: int = RETENTION_DAYS) -> None:
    daily_dir = DATA_DIR / user_id / 'daily'
    if not daily_dir.exists():
        return
    cutoff = bj_now().date() - timedelta(days=keep_days)
    for path in daily_dir.glob('*.json'):
        try:
            file_date = date.fromisoformat(path.stem)
        except Exception:
            continue
        if file_date < cutoff:
            path.unlink(missing_ok=True)


def save_daily_snapshot(api: Any, user_id: str, date_str: str) -> Path:
    profile = load_profile(user_id)
    snapshot = {
        'date': date_str,
        'fetch_time': bj_now().isoformat(timespec='seconds'),
        'user_id': user_id,
        'data': {},
    }

    for fetch_key in DATA_FETCH_MAP:
        if not feature_enabled(profile, fetch_key):
            continue
        try:
            snapshot['data'][fetch_key] = call_api(api, fetch_key, date_str)
        except Exception:
            snapshot['data'][fetch_key] = None
        time.sleep(CALL_DELAY_SECONDS)

    path = get_daily_path(user_id, date_str)
    save_json(path, snapshot)
    cleanup_old_daily_files(user_id)
    return path


def login_for_user_id(user_id: str):
    user = resolve_user(user_id)
    return gm.login_garmin(user)


def main() -> int:
    parser = argparse.ArgumentParser(description='保存单个用户单日 Garmin 快照')
    parser.add_argument('--user-id', required=True)
    parser.add_argument('--date', required=True)
    parser.add_argument('--list-users', action='store_true')
    args = parser.parse_args()

    if args.list_users:
        for user_id in sorted(list_enabled_user_ids()):
            print(user_id)
        return 0

    api = login_for_user_id(args.user_id)
    path = save_daily_snapshot(api, args.user_id, args.date)
    print(path)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
