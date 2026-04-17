#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import baseline as bl

ROOT = Path('/root/garmin_assistant')
DATA_DIR = ROOT / 'data'

EXPERIMENT_LIBRARY = [
    {
        'id': 'sleep_continuity',
        'name': '睡眠连续性观察',
        'tracking_metric': 'awake_count',
        'tracking_metric_cn': '夜间清醒次数',
        'duration_days': 5,
        'intro_text': '接下来5天我会重点帮你留意夜间清醒次数的变化规律，看看哪些天睡得更连贯',
        'good_direction': 'decrease',
    },
    {
        'id': 'deep_sleep_boost',
        'name': '深睡趋势观察',
        'tracking_metric': 'deep_sleep_seconds',
        'tracking_metric_cn': '深睡时长',
        'duration_days': 5,
        'intro_text': '你最近深睡时长偏短，接下来5天我帮你追踪一下深睡的变化趋势',
        'good_direction': 'increase',
    },
    {
        'id': 'recovery_quality',
        'name': '恢复质量观察',
        'tracking_metric': 'bb_wake',
        'tracking_metric_cn': '起床Body Battery',
        'duration_days': 5,
        'intro_text': '最近起床时的Body Battery偏低，接下来5天我重点关注一下恢复质量的走势',
        'good_direction': 'increase',
    },
    {
        'id': 'spo2_pattern',
        'name': '血氧模式观察',
        'tracking_metric': 'min_spo2',
        'tracking_metric_cn': '最低血氧',
        'duration_days': 5,
        'intro_text': '接下来5天我帮你详细记录一下血氧的变化模式，看看有没有哪些天会明显不同',
        'good_direction': 'increase',
    },
    {
        'id': 'stress_pattern',
        'name': '压力模式观察',
        'tracking_metric': 'night_stress_avg',
        'tracking_metric_cn': '夜间压力均值',
        'duration_days': 5,
        'intro_text': '你最近白天压力偏高的天数比较多，接下来5天我帮你观察一下压力和睡眠之间的关系',
        'good_direction': 'decrease',
    },
    {
        'id': 'hrv_recovery',
        'name': 'HRV恢复观察',
        'tracking_metric': 'hrv_night_avg',
        'tracking_metric_cn': '夜间HRV',
        'duration_days': 5,
        'intro_text': '你的HRV最近一周在持续走低，接下来5天我重点跟踪它的恢复情况',
        'good_direction': 'increase',
    },
    {
        'id': 'rhr_trend_watch',
        'name': '静息心率趋势观察',
        'tracking_metric': 'resting_hr',
        'tracking_metric_cn': '静息心率',
        'duration_days': 5,
        'intro_text': '你的静息心率最近一周在缓慢上升，接下来5天我帮你重点追踪一下这个趋势',
        'good_direction': 'decrease',
    },
    {
        'id': 'respiration_watch',
        'name': '呼吸频率趋势观察',
        'tracking_metric': 'respiration_sleep_avg',
        'tracking_metric_cn': '夜间呼吸频率',
        'duration_days': 5,
        'intro_text': '你的夜间呼吸频率最近有上升趋势，接下来5天我帮你观察一下它和其他指标的关联',
        'good_direction': 'decrease',
    },
]


EXTRACTORS = {
    'awake_count': bl.extract_awake_count,
    'deep_sleep_seconds': bl.extract_deep_sleep_seconds,
    'bb_wake': bl.extract_bb_wake,
    'min_spo2': bl.extract_min_spo2,
    'night_stress_avg': bl.extract_night_stress_avg,
    'hrv_night_avg': bl.extract_hrv_night_avg,
}


def load_json(path: Path, default: Any):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def save_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def profile_path(user_id: str) -> Path:
    return DATA_DIR / user_id / 'profile.json'


def baselines_path(user_id: str) -> Path:
    return DATA_DIR / user_id / 'baselines.json'


def latest_analysis_path(user_id: str) -> Path:
    return DATA_DIR / user_id / 'latest_analysis.json'


def daily_path(user_id: str, date_str: str) -> Path:
    return DATA_DIR / user_id / 'daily' / f'{date_str}.json'


def load_profile(user_id: str) -> dict[str, Any]:
    return load_json(profile_path(user_id), {})


def save_profile(user_id: str, profile: dict[str, Any]) -> None:
    save_json(profile_path(user_id), profile)


def load_baselines(user_id: str) -> dict[str, Any]:
    return load_json(baselines_path(user_id), {})


def load_latest_analysis(user_id: str) -> dict[str, Any] | None:
    return load_json(latest_analysis_path(user_id), None)


def list_recent_snapshots(user_id: str, days: int = 7, before_date: str | None = None) -> list[dict[str, Any]]:
    daily_dir = DATA_DIR / user_id / 'daily'
    paths = sorted(daily_dir.glob('*.json'))
    if before_date:
        cutoff = parse_date(before_date)
        paths = [path for path in paths if parse_date(path.stem) < cutoff]
    paths = paths[-days:]
    return [load_json(path, {}) for path in paths]


def get_metric_from_snapshot(snapshot: dict[str, Any], metric: str) -> float | None:
    extractor = EXTRACTORS.get(metric)
    if not extractor:
        return None
    return bl.to_number(extractor(snapshot))


def get_today_metric_value(user_id: str, date_str: str, metric: str) -> float | None:
    return get_metric_from_snapshot(load_json(daily_path(user_id, date_str), {}), metric)


def recent_metric_values(user_id: str, metric: str, days: int = 7, before_date: str | None = None) -> list[float]:
    values = []
    for snapshot in list_recent_snapshots(user_id, days=days, before_date=before_date):
        value = get_metric_from_snapshot(snapshot, metric)
        if value is not None:
            values.append(value)
    return values


def mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def observation_count(current: dict[str, Any]) -> int:
    return len(current.get('daily_values') or [])


def record_experiment_observation(
    profile: dict[str, Any],
    current: dict[str, Any],
    user_id: str,
    date_str: str,
) -> tuple[dict[str, Any], dict[str, Any], float | None]:
    today_value = get_today_metric_value(user_id, date_str, current['tracking_metric'])
    profile['current_experiment'] = current
    rounded_value = round(today_value, 2) if today_value is not None else None
    if rounded_value is None:
        return profile, current, None

    dates = list(current.get('daily_dates') or [])
    values = list(current.get('daily_values') or [])
    if date_str not in dates:
        dates.append(date_str)
        values.append(rounded_value)
        current['daily_dates'] = dates
        current['daily_values'] = values
    return profile, current, rounded_value


def calculate_high_stress_percentage(snapshot: dict[str, Any]) -> float | None:
    data = snapshot.get('data', {}) if isinstance(snapshot, dict) else {}
    stress_data = data.get('all_day_stress') or data.get('stress_detail') or {}
    values = stress_data.get('stressValuesArray') if isinstance(stress_data, dict) else None
    if isinstance(values, list) and values:
        valid = [item[1] for item in values if isinstance(item, list) and len(item) >= 2 and isinstance(item[1], (int, float)) and item[1] >= 0]
        if valid:
            high = [value for value in valid if value > 50]
            return len(high) / len(valid)
    return None


def is_experiment_finished(current: dict[str, Any], today_str: str | None = None) -> bool:
    if not current:
        return True
    today = parse_date(today_str) if today_str else date.today()
    start = parse_date(current['start_date'])
    return (today - start).days + 1 > int(current.get('duration_days', 0))


def check_trigger_condition(exp: dict[str, Any], user_id: str, baselines: dict[str, Any], latest_analysis: dict[str, Any] | None) -> bool:
    recent_7 = baselines.get('recent_7day', {})
    metrics = baselines.get('metrics', {})
    profile = load_profile(user_id)

    if exp['id'] == 'sleep_continuity':
        awake_mean = (recent_7.get('awake_count') or {}).get('mean')
        return awake_mean is not None and awake_mean > 2.5
    if exp['id'] == 'deep_sleep_boost':
        recent_mean = (recent_7.get('deep_sleep_seconds') or {}).get('mean')
        base_mean = (metrics.get('deep_sleep_seconds') or {}).get('mean')
        return recent_mean is not None and base_mean is not None and recent_mean < base_mean * 0.85
    if exp['id'] == 'recovery_quality':
        recent_mean = (recent_7.get('bb_wake') or {}).get('mean')
        return recent_mean is not None and recent_mean < 65
    if exp['id'] == 'spo2_pattern':
        return profile.get('blood_oxygen_status') == 'warned'
    if exp['id'] == 'stress_pattern':
        high_days = 0
        for snapshot in list_recent_snapshots(user_id, days=7):
            pct = calculate_high_stress_percentage(snapshot)
            if pct is not None and pct > 0.4:
                high_days += 1
        return high_days >= 4
    if exp['id'] == 'hrv_recovery':
        if latest_analysis:
            trend = ((latest_analysis.get('trends') or {}).get('hrv_night_avg') or {}).get('direction_7d')
            return trend == 'falling'
        return False
    if exp['id'] == 'rhr_trend_watch':
        if latest_analysis:
            trend = ((latest_analysis.get('trends') or {}).get('resting_hr') or {}).get('direction_7d')
            return trend == 'rising'
        return False
    if exp['id'] == 'respiration_watch':
        if latest_analysis:
            trend = ((latest_analysis.get('trends') or {}).get('respiration_sleep_avg') or {}).get('direction_7d')
            return trend == 'rising'
        return False
    return False


def select_experiment(user_id: str, on_date: str | None = None) -> dict[str, Any] | None:
    today = parse_date(on_date) if on_date else date.today()
    profile = load_profile(user_id)
    current = profile.get('current_experiment')
    if current and not is_experiment_finished(current, today.isoformat()):
        return None

    last_end = profile.get('last_experiment_end_date')
    if last_end:
        if (today - parse_date(last_end)).days < 3:
            return None

    last_experiment_id = profile.get('last_experiment_id')
    latest_analysis = load_latest_analysis(user_id)
    baselines = load_baselines(user_id)

    should_try = today.weekday() == 0 or bool(last_end and (today - parse_date(last_end)).days >= 3)
    if not should_try:
        return None

    for exp in EXPERIMENT_LIBRARY:
        if exp['id'] == last_experiment_id:
            continue
        if check_trigger_condition(exp, user_id, baselines, latest_analysis):
            values = recent_metric_values(user_id, exp['tracking_metric'], days=7, before_date=today.isoformat())
            return {
                **exp,
                'start_date': today.isoformat(),
                'pre_experiment_values': [round(v, 2) for v in values],
                'pre_experiment_mean': round(mean(values), 2) if values else None,
                'daily_values': [],
                'daily_dates': [],
            }
    return None


def finish_experiment(user_id: str, current: dict[str, Any], summary: str, end_date: str) -> None:
    profile = load_profile(user_id)
    history = profile.get('experiment_history') or []
    history.append({
        'id': current['id'],
        'name': current['name'],
        'start_date': current['start_date'],
        'end_date': end_date,
        'result_summary': summary,
    })
    profile['experiment_history'] = history[-12:]
    profile['last_experiment_id'] = current['id']
    profile['last_experiment_end_date'] = end_date
    profile['current_experiment'] = None
    save_profile(user_id, profile)


def is_improving(current: dict[str, Any]) -> bool:
    values = current.get('daily_values') or []
    if not values:
        return False
    pre_mean = current.get('pre_experiment_mean')
    latest = values[-1]
    if pre_mean is None:
        return False
    if current.get('good_direction') == 'increase':
        return latest > pre_mean
    return latest < pre_mean


def generate_experiment_summary(current: dict[str, Any]) -> str:
    values = current.get('daily_values') or []
    observed_days = observation_count(current)
    if not values:
        return f"这段观察里，{current['tracking_metric_cn']} 暂时没有积累到足够数据。"
    current_mean = round(mean(values), 2)
    pre_mean = current.get('pre_experiment_mean')
    if observed_days >= int(current.get('duration_days', 0) or 0):
        period_prefix = f"{current['duration_days']}天观察期内"
    else:
        period_prefix = f"这段观察里共拿到{observed_days}天有效数据"
    if pre_mean is None:
        return f"{period_prefix}，{current['tracking_metric_cn']} 的平均值是 {current_mean}。"
    diff = round(current_mean - pre_mean, 2)
    if abs(diff) < 1e-6:
        return f"{period_prefix}，{current['tracking_metric_cn']} 平均 {current_mean}，和观察前基本一致。"
    improved = (diff > 0 and current.get('good_direction') == 'increase') or (diff < 0 and current.get('good_direction') == 'decrease')
    direction_text = '有所改善' if improved else '没有明显改善'
    return (
        f"{period_prefix}，{current['tracking_metric_cn']} 平均 {current_mean}，"
        f"相比观察前7天均值 {pre_mean} {direction_text}。"
    )


def get_experiment_status(user_id: str, date_str: str) -> dict[str, Any] | None:
    profile = load_profile(user_id)
    current = profile.get('current_experiment')

    if not current:
        new_exp = select_experiment(user_id, on_date=date_str)
        if new_exp:
            profile, new_exp, today_value = record_experiment_observation(profile, new_exp, user_id, date_str)
            save_profile(user_id, profile)
            observed_days = observation_count(new_exp)
            return {
                'status': 'new',
                'experiment': new_exp,
                'intro_text': new_exp['intro_text'],
                'day_number': observed_days,
                'observed_days': observed_days,
                'total_days': new_exp['duration_days'],
                'remaining_days': max(int(new_exp['duration_days']) - observed_days, 0),
                'today_value': today_value,
                'values_so_far': new_exp.get('daily_values') or [],
                'pre_mean': new_exp.get('pre_experiment_mean'),
            }
        return None

    start = parse_date(current['start_date'])
    current_day = parse_date(date_str)
    if current_day < start:
        return None

    elapsed_days = (current_day - start).days + 1
    if elapsed_days > int(current['duration_days']):
        summary = generate_experiment_summary(current)
        finish_experiment(user_id, current, summary, date_str)
        observed_days = observation_count(current)
        return {
            'status': 'completed',
            'experiment': current,
            'day_number': observed_days,
            'observed_days': observed_days,
            'elapsed_days': elapsed_days,
            'summary': summary,
        }

    profile = load_profile(user_id)
    current = profile.get('current_experiment') or current
    profile, current, today_value = record_experiment_observation(profile, current, user_id, date_str)
    save_profile(user_id, profile)
    observed_days = observation_count(current)

    return {
        'status': 'in_progress',
        'experiment': current,
        'day_number': observed_days,
        'observed_days': observed_days,
        'elapsed_days': elapsed_days,
        'total_days': current['duration_days'],
        'remaining_days': max(int(current['duration_days']) - observed_days, 0),
        'today_value': round(today_value, 2) if today_value is not None else None,
        'values_so_far': current.get('daily_values') or [],
        'pre_mean': current.get('pre_experiment_mean'),
        'trend_vs_baseline': 'improved' if is_improving(current) else 'same_or_worse',
    }


if __name__ == '__main__':
    import sys
    user_id = sys.argv[1]
    print(json.dumps(select_experiment(user_id, on_date=sys.argv[2] if len(sys.argv) > 2 else None), ensure_ascii=False, indent=2))
