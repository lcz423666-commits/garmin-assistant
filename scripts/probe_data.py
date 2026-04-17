#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import time
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path('/root/garmin_assistant')
APP_DIR = ROOT / 'app'
DATA_DIR = ROOT / 'data'
REPORTS_DIR = ROOT / 'reports'
SCRIPTS_DIR = ROOT / 'scripts'
BJ_TZ = timezone(timedelta(hours=8))
DELAY_SECONDS = 2

if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import garmin_monitor as gm  # noqa: E402

USER_TARGETS = [
    {'source_name': '丛至', 'user_id': 'congzhi', 'display_name': '李丛至', 'garmin_email': '645042220@qq.com'},
    {'source_name': '杨', 'user_id': 'yang', 'display_name': '杨', 'garmin_email': 'yangqihao@vip.qq.com'},
    {'source_name': 'Kevin', 'user_id': 'kevin', 'display_name': 'Kevin', 'garmin_email': '656727039@qq.com'},
]

SKIN_TEMPERATURE_ENDPOINTS = [
    '/wellness-service/wellness/daily/skinTemp/{date}',
    '/wellness-service/wellness/daily/skinTemp?calendarDate={date}',
    '/wellness-service/wellness/daily/skin-temperature/{date}',
    '/wellness-service/wellness/daily/skin-temperature?calendarDate={date}',
    '/wellness-service/wellness/skinTemp?date={date}',
    '/wellness-service/wellness/skintemp?date={date}',
    '/wellness-service/wellness/skinTemperature?date={date}',
    '/wellness-service/wellness/dailySkinTemp?date={date}',
    '/wellness-service/wellness/healthStatus?date={date}',
    '/wellness-service/wellness/health-status?date={date}',
    '/wellness-service/wellness/daily/health-status?date={date}',
]

PROBE_LIST = [
    {'name': 'respiration', 'method': 'get_respiration_data', 'description': '呼吸频率'},
    {'name': 'training_status', 'method': 'get_training_status', 'description': '训练状态'},
    {'name': 'max_metrics', 'method': 'get_max_metrics', 'description': 'VO2 Max / 体能年龄'},
    {'name': 'stress_detail', 'method': 'get_stress_data', 'description': '详细压力曲线'},
    {'name': 'heart_rate_detail', 'method': 'get_heart_rates', 'description': '详细心率'},
    {'name': 'body_battery_events', 'method': 'get_body_battery_events', 'description': 'BB事件'},
    {'name': 'steps', 'method': 'get_steps_data', 'description': '步数'},
    {'name': 'intensity_minutes', 'method': 'get_intensity_minutes_data', 'description': '强度分钟数'},
    {'name': 'hrv_data', 'method': 'get_hrv_data', 'description': 'HRV完整数据'},
    {'name': 'skin_temperature', 'method': 'probe_skin_temperature', 'description': '皮肤温度'},
    {'name': 'endurance_score', 'method': 'get_endurance_score', 'description': '耐力评分'},
    {'name': 'hill_score', 'method': 'get_hill_score', 'description': '爬坡评分'},
    {'name': 'training_readiness', 'method': 'get_training_readiness', 'description': '训练准备度'},
    {'name': 'morning_training_readiness', 'method': 'get_morning_training_readiness', 'description': '晨间训练准备度'},
    {'name': 'cycling_ftp', 'method': 'get_cycling_ftp', 'description': '骑行FTP功率阈值'},
    {'name': 'race_predictions', 'method': 'get_race_predictions', 'description': '比赛成绩预测'},
    {'name': 'lactate_threshold', 'method': 'get_lactate_threshold', 'description': '乳酸阈值'},
    {'name': 'all_day_stress', 'method': 'get_all_day_stress', 'description': '全天压力概览'},
    {'name': 'weekly_intensity_minutes', 'method': 'get_weekly_intensity_minutes', 'description': '本周强度分钟数汇总'},
    {'name': 'fitnessage', 'method': 'get_fitnessage_data', 'description': '体能年龄单独接口'},
    {'name': 'sleep_data', 'method': 'get_sleep_data', 'description': '睡眠数据'},
    {'name': 'spo2_data', 'method': 'get_spo2_data', 'description': '血氧数据'},
    {'name': 'rhr_day', 'method': 'get_rhr_day', 'description': '当日静息心率'},
    {'name': 'user_summary', 'method': 'get_user_summary', 'description': '用户当日综合摘要'},
    {'name': 'lifestyle_logging', 'method': 'get_lifestyle_logging_data', 'description': '生活方式日志'},
    {'name': 'floors', 'method': 'get_floors', 'description': '楼层数据'},
    {'name': 'daily_events', 'method': 'get_all_day_events', 'description': '日间事件'},
]

IGNORED_EMPTY_KEYS = {
    'calendarDate', 'date', 'startDate', 'endDate', 'unitKey', 'userProfileId', 'userId', 'bodyBatteryVersion', 'message'
}


def bj_now() -> datetime:
    return datetime.now(BJ_TZ)


def ensure_layout() -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    for target in USER_TARGETS:
        (DATA_DIR / target['user_id'] / 'daily').mkdir(parents=True, exist_ok=True)


def load_target_users() -> list[dict[str, Any]]:
    users = gm.load_users()
    by_name = {user['name']: deepcopy(user) for user in users}
    resolved = []
    for target in USER_TARGETS:
        user = by_name.get(target['source_name'])
        if not user:
            raise RuntimeError(f"未在 users.json 中找到用户: {target['source_name']}")
        user['_probe_user_id'] = target['user_id']
        user['_probe_display_name'] = target['display_name']
        resolved.append(user)
    return resolved


def make_json_safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): make_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [make_json_safe(item) for item in value]
    return str(value)


def has_meaningful_content(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return any(has_meaningful_content(item) for item in value)
    if isinstance(value, dict):
        if not value:
            return False
        for key, item in value.items():
            if key in IGNORED_EMPTY_KEYS:
                continue
            if has_meaningful_content(item):
                return True
        return False
    return True


def classify_probe_result(result: Any) -> tuple[bool, str]:
    if result is None:
        return False, 'API返回空'
    if isinstance(result, list) and not result:
        return False, 'API返回空列表'
    if isinstance(result, dict) and not result:
        return False, 'API返回空对象'
    if has_meaningful_content(result):
        return True, 'OK'
    return False, 'API仅返回元数据或空值'


def probe_skin_temperature(client: Any, date_str: str) -> Any:
    errors = []
    for template in SKIN_TEMPERATURE_ENDPOINTS:
        endpoint = template.format(date=date_str)
        try:
            result = client.connectapi(endpoint)
            success, _ = classify_probe_result(result)
            if success:
                return {'endpoint': endpoint, 'payload': result}
            errors.append(f'{endpoint} -> 空返回')
        except Exception as exc:
            errors.append(f'{endpoint} -> {exc}')
    raise RuntimeError('未找到可用皮肤温度接口；' + ' | '.join(errors[:3]))


def week_start(date_str: str) -> str:
    d = date.fromisoformat(date_str)
    return (d - timedelta(days=d.weekday())).isoformat()


def call_probe_method(client: Any, method_name: str, date_str: str) -> Any:
    if method_name == 'probe_skin_temperature':
        return probe_skin_temperature(client, date_str)
    method = getattr(client, method_name, None)
    if method is None:
        raise AttributeError(f'当前 garminconnect 版本缺少方法 {method_name}')
    if method_name in {'get_endurance_score', 'get_hill_score', 'get_race_predictions'}:
        return method(date_str, date_str)
    if method_name == 'get_weekly_intensity_minutes':
        return method(week_start(date_str), date_str)
    if method_name == 'get_cycling_ftp':
        return method()
    if method_name == 'get_lactate_threshold':
        try:
            return method()
        except TypeError:
            return method(latest=True)
    return method(date_str)


def probe_user(user: dict[str, Any], probe_date: str, request_date: str, client: Any | None = None) -> dict[str, Any]:
    client = client or gm.login_garmin(user)
    display_name = gm.get_display_name(client, user.get('_probe_display_name') or user['name'])
    available_data: dict[str, bool] = {}
    sample_data: dict[str, Any] = {}
    probe_details: dict[str, dict[str, Any]] = {}
    summary_lines: list[str] = []

    for probe in PROBE_LIST:
        probe_name = probe['name']
        method_name = probe['method']
        description = probe['description']
        try:
            result = call_probe_method(client, method_name, request_date)
            success, message = classify_probe_result(result)
            available_data[probe_name] = success
            probe_details[probe_name] = {
                'method': method_name,
                'description': description,
                'status': 'success' if success else 'empty',
                'message': message,
                'request_date': request_date,
            }
            if success:
                sample_data[probe_name] = make_json_safe(result)
                summary_lines.append(f'  ✅ {description}')
            else:
                summary_lines.append(f'  ❌ {description}（{message}）')
        except Exception as exc:
            available_data[probe_name] = False
            probe_details[probe_name] = {
                'method': method_name,
                'description': description,
                'status': 'error',
                'message': str(exc),
                'request_date': request_date,
            }
            summary_lines.append(f'  ❌ {description}（{exc}）')
        time.sleep(DELAY_SECONDS)

    profile = {
        'user_id': user['_probe_user_id'],
        'display_name': display_name,
        'garmin_email': user['garmin_email'],
        'probe_date': probe_date,
        'request_date': request_date,
        'available_data': available_data,
        'sample_data': sample_data,
        'probe_details': probe_details,
        'blood_oxygen_status': 'normal',
        'blood_oxygen_baseline_range': None,
        'blood_oxygen_warned_date': None,
    }
    return {
        'profile': profile,
        'summary_lines': summary_lines,
        'available_count': sum(1 for value in available_data.values() if value),
        'total_count': len(PROBE_LIST),
    }


def write_profile(user: dict[str, Any], profile: dict[str, Any]) -> Path:
    profile_path = DATA_DIR / user['_probe_user_id'] / 'profile.json'
    profile_path.write_text(json.dumps(make_json_safe(profile), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return profile_path


def print_report(results: list[dict[str, Any]]) -> None:
    print('=== 佳明数据探测报告 ===')
    print()
    for item in results:
        user = item['user']
        print(f"{item['display_name']} ({user['garmin_email']}):")
        for line in item['summary_lines']:
            print(line)
        print(f"  可用数据：{item['available_count']}/{item['total_count']}")
        print()


def main() -> int:
    ensure_layout()
    today = bj_now().date()
    probe_date = today.isoformat()
    request_date = (today - timedelta(days=1)).isoformat()
    users = load_target_users()
    results: list[dict[str, Any]] = []

    for user in users:
        outcome = probe_user(user, probe_date=probe_date, request_date=request_date)
        write_profile(user, outcome['profile'])
        results.append({
            'user': user,
            'display_name': outcome['profile']['display_name'],
            'summary_lines': outcome['summary_lines'],
            'available_count': outcome['available_count'],
            'total_count': outcome['total_count'],
        })

    print_report(results)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
