#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import baseline as bl
import experiment

ROOT = Path('/root/garmin_assistant')
DATA_DIR = ROOT / 'data'

TRAINING_STATUS_CN = {
    'PRODUCTIVE': '高效',
    'MAINTAINING': '维持',
    'RECOVERY': '恢复中',
    'UNPRODUCTIVE': '低效',
    'DETRAINING': '退训练',
    'OVERREACHING': '过度负荷',
    'PEAKING': '巅峰',
    'NO_STATUS': '数据不足',
}

METRIC_CN = {
    'hrv_night_avg': 'HRV',
    'bb_wake': '起床 Body Battery',
    'sleep_score': '睡眠评分',
    'resting_hr': '静息心率',
    'deep_sleep_seconds': '深睡时长',
    'min_spo2': '最低血氧',
    'respiration_sleep_avg': '夜间呼吸频率',
}

TREND_METRICS = [
    'hrv_night_avg',
    'sleep_score',
    'bb_wake',
    'resting_hr',
    'deep_sleep_seconds',
    'min_spo2',
    'respiration_sleep_avg',
]

REVERSAL_METRICS = ['hrv_night_avg', 'bb_wake', 'sleep_score', 'resting_hr']
EXTREME_METRICS = [
    'sleep_score',
    'deep_sleep_seconds',
    'hrv_night_avg',
    'bb_wake',
    'resting_hr',
    'respiration_sleep_avg',
]


def load_json(path: Path, default: Any):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def load_daily_data(user_id: str, date_str: str) -> dict[str, Any] | None:
    return load_json(DATA_DIR / user_id / 'daily' / f'{date_str}.json', None)


def load_baselines(user_id: str) -> dict[str, Any]:
    return load_json(DATA_DIR / user_id / 'baselines.json', {})


def load_profile(user_id: str) -> dict[str, Any]:
    return load_json(DATA_DIR / user_id / 'profile.json', {})


def safe_get(value: Any, *keys: Any) -> Any:
    return bl.safe_get(value, *keys)


def to_number(value: Any) -> float | None:
    return bl.to_number(value)


def first_non_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def normalize_number(value: float | None) -> float | int | None:
    if value is None:
        return None
    if abs(value - round(value)) < 1e-9:
        return int(round(value))
    return round(value, 2)


def format_delta(value: float | None) -> str | None:
    if value is None:
        return None
    if abs(value - round(value)) < 1e-9:
        return f'{value:+.0f}'
    return f'{value:+.2f}'


def format_std_deviation(current: float, mean: float, std: float) -> str:
    multiple = (current - mean) / std
    direction = '高于' if multiple > 0 else '低于'
    return f'{direction}基线{abs(multiple):.1f}个标准差'


def baseline_metric(baselines: dict[str, Any], metric: str, section: str = 'metrics') -> dict[str, Any] | None:
    return safe_get(baselines, section, metric)


def extract_training_status_value(training_status: dict[str, Any] | None) -> str | None:
    latest = safe_get(training_status, 'mostRecentTrainingStatus', 'latestTrainingStatusData')
    if isinstance(latest, dict):
        for item in latest.values():
            if not isinstance(item, dict):
                continue
            phrase = item.get('trainingStatusFeedbackPhrase')
            if isinstance(phrase, str) and phrase:
                return phrase.split('_')[0]
    return None


def extract_readiness_score(data: dict[str, Any]) -> Any:
    morning = data.get('morning_training_readiness')
    if isinstance(morning, dict):
        return morning.get('score')
    readiness = data.get('training_readiness')
    if isinstance(readiness, list) and readiness and isinstance(readiness[0], dict):
        return readiness[0].get('score')
    if isinstance(readiness, dict):
        return readiness.get('score')
    return None


def extract_hrv_status(data: dict[str, Any]) -> Any:
    status = safe_get(data, 'sleep', 'hrvStatus')
    if isinstance(status, dict):
        return first_non_none(status.get('status'), status.get('value'), status.get('feedbackPhrase'))
    return status


def extract_vo2max(data: dict[str, Any]) -> Any:
    return first_non_none(
        safe_get(data, 'training_status', 'mostRecentVO2Max', 'generic', 'vo2MaxValue'),
        safe_get(data, 'max_metrics', 0, 'generic', 'vo2MaxValue'),
    )


def extract_fitness_age(data: dict[str, Any]) -> Any:
    return first_non_none(
        safe_get(data, 'fitnessage', 'fitnessAge'),
        safe_get(data, 'training_status', 'mostRecentVO2Max', 'generic', 'fitnessAge'),
        safe_get(data, 'max_metrics', 0, 'generic', 'fitnessAge'),
    )


def extract_endurance_score(data: dict[str, Any]) -> Any:
    return first_non_none(
        safe_get(data, 'endurance_score', 'enduranceScoreDTO', 'enduranceScore'),
        safe_get(data, 'endurance_score', 'avg'),
        safe_get(data, 'endurance_score', 'max'),
    )


def extract_hill_score(data: dict[str, Any]) -> Any:
    direct = safe_get(data, 'hill_score', 'maxScore')
    if direct is not None:
        return direct
    period = safe_get(data, 'hill_score', 'periodAvgScore')
    if isinstance(period, dict):
        values = [value for value in period.values() if value is not None]
        if values:
            return values[-1]
    return None


def extract_weekly_minutes(data: dict[str, Any], key: str) -> Any:
    items = data.get('weekly_intensity_minutes')
    if isinstance(items, list) and items and isinstance(items[0], dict):
        return items[0].get(key)
    return None


def extract_key_metrics(daily_data: dict[str, Any] | None) -> dict[str, Any] | None:
    if not daily_data:
        return None

    data = daily_data.get('data', {})
    return {
        'sleep_score': normalize_number(to_number(bl.extract_sleep_score(daily_data))),
        'sleep_duration_seconds': normalize_number(to_number(bl.extract_sleep_duration_seconds(daily_data))),
        'deep_sleep_seconds': normalize_number(to_number(bl.extract_deep_sleep_seconds(daily_data))),
        'rem_sleep_seconds': normalize_number(to_number(bl.extract_rem_sleep_seconds(daily_data))),
        'awake_count': normalize_number(to_number(bl.extract_awake_count(daily_data))),
        'hrv_night_avg': normalize_number(to_number(bl.extract_hrv_night_avg(daily_data))),
        'hrv_status': extract_hrv_status(data),
        'resting_hr': normalize_number(to_number(bl.extract_resting_hr(daily_data))),
        'bb_wake': normalize_number(to_number(bl.extract_bb_wake(daily_data))),
        'respiration_sleep_avg': normalize_number(to_number(bl.extract_respiration_sleep_avg(daily_data))),
        'min_spo2': normalize_number(to_number(bl.extract_min_spo2(daily_data))),
        'avg_spo2': normalize_number(to_number(first_non_none(
            safe_get(data, 'spo2_data', 'avgSleepSpO2'),
            safe_get(data, 'spo2_data', 'averageSpO2'),
            safe_get(data, 'sleep', 'dailySleepDTO', 'averageSpO2Value'),
        ))),
        'night_stress_avg': normalize_number(to_number(bl.extract_night_stress_avg(daily_data))),
        'overall_stress': normalize_number(to_number(first_non_none(
            safe_get(data, 'all_day_stress', 'avgStressLevel'),
            safe_get(data, 'stress_detail', 'avgStressLevel'),
        ))),
        'daily_steps': normalize_number(to_number(bl.extract_daily_steps(daily_data))),
        'floors_climbed': normalize_number(to_number(first_non_none(
            safe_get(data, 'user_summary', 'floorsAscended'),
            safe_get(data, 'floors', 0, 'floorsAscended'),
        ))),
        'training_status_value': extract_training_status_value(data.get('training_status')),
        'vo2max': normalize_number(to_number(extract_vo2max(data))),
        'fitness_age': normalize_number(to_number(extract_fitness_age(data))),
        'training_readiness_score': normalize_number(to_number(extract_readiness_score(data))),
        'endurance_score_value': normalize_number(to_number(extract_endurance_score(data))),
        'hill_score_value': normalize_number(to_number(extract_hill_score(data))),
        'cycling_ftp_value': normalize_number(to_number(safe_get(data, 'cycling_ftp', 'functionalThresholdPower'))),
        'weekly_moderate_minutes': normalize_number(to_number(extract_weekly_minutes(data, 'moderateValue'))),
        'weekly_vigorous_minutes': normalize_number(to_number(extract_weekly_minutes(data, 'vigorousValue'))),
    }


def detect_anomaly_signals(today_metrics: dict[str, Any] | None, baselines: dict[str, Any], recent_7day_baselines: dict[str, Any], yesterday_metrics: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    if not today_metrics:
        return signals

    resp = to_number(today_metrics.get('respiration_sleep_avg'))
    resp_base = baseline_metric(baselines, 'respiration_sleep_avg')
    if resp is not None and resp_base:
        mean = to_number(resp_base.get('mean'))
        std = to_number(resp_base.get('std'))
        if mean is not None and std not in (None, 0) and resp > mean + std:
            signals.append({
                'signal': '呼吸频率偏高',
                'metric': 'respiration_sleep_avg',
                'current_value': normalize_number(resp),
                'baseline_mean': normalize_number(mean),
                'baseline_std': normalize_number(std),
                'deviation': format_std_deviation(resp, mean, std),
                'severity': 'high' if resp > mean + 2 * std else 'medium',
            })

    resting = to_number(today_metrics.get('resting_hr'))
    resting_7d = baseline_metric(recent_7day_baselines, 'resting_hr', section='recent_7day')
    if resting is not None and resting_7d:
        mean = to_number(resting_7d.get('mean'))
        if mean is not None and resting > mean + 3:
            delta = resting - mean
            signals.append({
                'signal': '静息心率偏高',
                'metric': 'resting_hr',
                'current_value': normalize_number(resting),
                'baseline_7day_mean': normalize_number(mean),
                'deviation': f'高于7天均值{delta:.1f}bpm',
                'severity': 'high' if delta >= 6 else 'medium',
            })

    hrv = to_number(today_metrics.get('hrv_night_avg'))
    hrv_base = baseline_metric(baselines, 'hrv_night_avg')
    if hrv is not None and hrv_base:
        mean = to_number(hrv_base.get('mean'))
        std = to_number(hrv_base.get('std'))
        if mean is not None and std not in (None, 0) and hrv < mean - std:
            signals.append({
                'signal': 'HRV偏低',
                'metric': 'hrv_night_avg',
                'current_value': normalize_number(hrv),
                'baseline_mean': normalize_number(mean),
                'baseline_std': normalize_number(std),
                'deviation': format_std_deviation(hrv, mean, std),
                'severity': 'high' if hrv < mean - 2 * std else 'medium',
            })

    bb = to_number(today_metrics.get('bb_wake'))
    bb_base = baseline_metric(baselines, 'bb_wake')
    if bb is not None and bb_base:
        mean = to_number(bb_base.get('mean'))
        if mean not in (None, 0) and bb < mean * 0.7:
            signals.append({
                'signal': 'Body Battery起床值极低',
                'metric': 'bb_wake',
                'current_value': normalize_number(bb),
                'baseline_mean': normalize_number(mean),
                'deviation': f'仅为基线均值的{bb / mean:.0%}',
                'severity': 'high',
            })

    sleep_score = to_number(today_metrics.get('sleep_score'))
    sleep_base = baseline_metric(baselines, 'sleep_score')
    yesterday_score = to_number((yesterday_metrics or {}).get('sleep_score'))
    if sleep_score is not None and yesterday_score is not None and sleep_base:
        mean = to_number(sleep_base.get('mean'))
        if mean is not None:
            threshold = mean * 0.85
            if sleep_score < threshold and yesterday_score < threshold:
                signals.append({
                    'signal': '睡眠评分连续偏低',
                    'metric': 'sleep_score',
                    'current_value': normalize_number(sleep_score),
                    'baseline_mean': normalize_number(mean),
                    'deviation': f'连续两天低于基线均值85%阈值（{threshold:.1f}）',
                    'severity': 'medium',
                })

    stress = to_number(today_metrics.get('night_stress_avg'))
    stress_base = baseline_metric(baselines, 'night_stress_avg')
    if stress is not None and stress_base:
        mean = to_number(stress_base.get('mean'))
        if mean not in (None, 0) and stress > mean * 1.2:
            signals.append({
                'signal': '夜间压力偏高',
                'metric': 'night_stress_avg',
                'current_value': normalize_number(stress),
                'baseline_mean': normalize_number(mean),
                'deviation': f'高于基线均值{stress - mean:.1f}',
                'severity': 'high' if stress > mean * 1.4 else 'medium',
            })

    return signals


def compute_direction(values: list[float], threshold: float) -> tuple[str, float]:
    if len(values) < 2:
        return 'stable', 0.0
    split = max(1, len(values) // 2)
    first = values[:split]
    second = values[split:]
    if not second:
        return 'stable', 0.0
    change = (sum(second) / len(second)) - (sum(first) / len(first))
    if change > threshold:
        return 'rising', change
    if change < -threshold:
        return 'falling', change
    return 'stable', change


def detect_trends(user_id: str, date_str: str, baselines: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    del user_id, date_str
    trends: dict[str, Any] = {}
    status = profile.get('blood_oxygen_status')

    for metric in TREND_METRICS:
        if metric == 'min_spo2' and status == 'known_baseline':
            continue
        metric_data = baseline_metric(baselines, metric)
        if not metric_data:
            continue
        values = metric_data.get('values') or []
        if len(values) < 2:
            continue
        std = to_number(metric_data.get('std'))
        mean = to_number(metric_data.get('mean')) or 0.0
        threshold = abs(std) * 0.1 if std not in (None, 0) else max(abs(mean) * 0.03, 1.0)
        recent_3 = [float(v) for v in values[-3:]]
        recent_7 = [float(v) for v in values[-7:]]
        dir_3, change_3 = compute_direction(recent_3, threshold)
        dir_7, change_7 = compute_direction(recent_7, threshold)
        trends[metric] = {
            'metric_cn': METRIC_CN.get(metric, metric),
            'direction_3d': dir_3,
            'direction_7d': dir_7,
            'change_3d': format_delta(change_3),
            'change_7d': format_delta(change_7),
        }
    return trends


def sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def detect_trend_reversals(user_id: str, date_str: str) -> list[dict[str, Any]]:
    del date_str
    baselines = load_baselines(user_id)
    reversals: list[dict[str, Any]] = []

    for metric in REVERSAL_METRICS:
        values = (baseline_metric(baselines, metric) or {}).get('values') or []
        if len(values) < 5:
            continue
        recent = [float(v) for v in values[-7:]]
        diffs = [recent[i + 1] - recent[i] for i in range(len(recent) - 1)]
        if len(diffs) < 4:
            continue
        latest_sign = sign(diffs[-1])
        if latest_sign == 0:
            continue
        prev_signs = [sign(v) for v in diffs[:-1] if sign(v) != 0]
        if len(prev_signs) < 3:
            continue
        streak = 0
        old_sign = prev_signs[-1]
        for current_sign in reversed(prev_signs):
            if current_sign == old_sign:
                streak += 1
            else:
                break
        if streak >= 3 and old_sign != latest_sign:
            reversals.append({
                'metric': metric,
                'metric_cn': METRIC_CN.get(metric, metric),
                'streak_days': streak,
                'old_direction': '上升' if old_sign > 0 else '下降',
                'new_direction': '回升' if latest_sign > 0 else '下降',
                'latest_value': normalize_number(recent[-1]),
                'streak_start_value': normalize_number(recent[-(streak + 2)]),
            })
    return reversals


def detect_extremes(user_id: str, date_str: str, baselines: dict[str, Any]) -> list[dict[str, Any]]:
    today = extract_key_metrics(load_daily_data(user_id, date_str)) or {}
    extremes: list[dict[str, Any]] = []

    for metric in EXTREME_METRICS:
        current = to_number(today.get(metric))
        metric_data = baseline_metric(baselines, metric)
        values = (metric_data or {}).get('values') or []
        if current is None or len(values) < 3:
            continue
        recent = [float(v) for v in values[-14:]]
        mean = sum(recent) / len(recent)
        max_value = max(recent)
        min_value = min(recent)
        high_matches = sum(1 for value in recent if abs(value - current) < 1e-9)
        low_matches = high_matches
        if abs(current - max_value) < 1e-9:
            extreme_type = '最高' if high_matches == 1 else '高位'
            description = f"近2周{METRIC_CN.get(metric, metric)}{'最高' if high_matches == 1 else '处在高位'}"
            extremes.append({
                'metric': metric,
                'metric_cn': METRIC_CN.get(metric, metric),
                'type': extreme_type,
                'value': normalize_number(current),
                'period_mean': normalize_number(mean),
                'description': description,
                'is_unique_extreme': high_matches == 1,
            })
        elif abs(current - min_value) < 1e-9:
            extreme_type = '最低' if low_matches == 1 else '低位'
            description = f"近2周{METRIC_CN.get(metric, metric)}{'最低' if low_matches == 1 else '处在低位'}"
            extremes.append({
                'metric': metric,
                'metric_cn': METRIC_CN.get(metric, metric),
                'type': extreme_type,
                'value': normalize_number(current),
                'period_mean': normalize_number(mean),
                'description': description,
                'is_unique_extreme': low_matches == 1,
            })
    return extremes


def detect_training_status_change(user_id: str, date_str: str) -> dict[str, Any]:
    current_date = date.fromisoformat(date_str)
    yesterday_str = (current_date - timedelta(days=1)).isoformat()
    today_data = load_daily_data(user_id, date_str)
    yesterday_data = load_daily_data(user_id, yesterday_str)

    today_value = extract_training_status_value(safe_get(today_data, 'data', 'training_status') or {})
    yesterday_value = extract_training_status_value(safe_get(yesterday_data, 'data', 'training_status') or {})

    if today_value and yesterday_value and today_value != yesterday_value:
        return {
            'changed': True,
            'from': yesterday_value,
            'to': today_value,
            'description': f'训练状态从{TRAINING_STATUS_CN.get(yesterday_value, yesterday_value)}变为{TRAINING_STATUS_CN.get(today_value, today_value)}',
        }
    return {
        'changed': False,
        'current': today_value or yesterday_value or 'NO_STATUS',
    }


def analyze_blood_oxygen(user_id: str, today_metrics: dict[str, Any] | None) -> dict[str, Any]:
    profile = load_profile(user_id)
    status = profile.get('blood_oxygen_status', 'normal')
    baseline_range = profile.get('baseline_range') or profile.get('blood_oxygen_baseline_range')
    min_spo2 = to_number((today_metrics or {}).get('min_spo2'))

    result = {
        'status': status,
        'min_spo2_today': normalize_number(min_spo2),
        'baseline_range': baseline_range,
        'is_worse_than_baseline': False,
        'is_better_than_baseline': False,
        'should_mention': False,
    }

    if min_spo2 is None:
        return result

    if status == 'known_baseline' and isinstance(baseline_range, list) and len(baseline_range) == 2:
        low, high = baseline_range
        if min_spo2 < low:
            result['is_worse_than_baseline'] = True
            result['should_mention'] = True
        elif min_spo2 > high + 3:
            result['is_better_than_baseline'] = True
            result['should_mention'] = True
        return result

    if status == 'warned':
        result['should_mention'] = True
        return result

    if status == 'normal' and min_spo2 < 90:
        result['should_mention'] = True
    return result


def generate_notable_findings(anomaly_signals: list[dict[str, Any]], trend_reversals: list[dict[str, Any]], extremes: list[dict[str, Any]], training_status_change: dict[str, Any], blood_oxygen: dict[str, Any], trends: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    if len(anomaly_signals) >= 3:
        findings.append({
            'type': 'anomaly_alert',
            'priority': 1,
            'title': '身体状态预警',
            'description': f'{len(anomaly_signals)}项指标同时偏离个人基线',
            'signals': anomaly_signals,
        })

    for reversal in trend_reversals:
        findings.append({
            'type': 'trend_reversal',
            'priority': 2,
            'title': f"{reversal['metric_cn']}趋势转折",
            'description': f"连续{reversal['streak_days']}天{reversal['old_direction']}后首次{reversal['new_direction']}",
            'detail': reversal,
        })

    if training_status_change.get('changed'):
        findings.append({
            'type': 'training_status_change',
            'priority': 2,
            'title': '训练状态变化',
            'description': training_status_change['description'],
            'detail': training_status_change,
        })

    for extreme in extremes:
        findings.append({
            'type': 'extreme_value',
            'priority': 3,
            'title': extreme['description'],
            'description': extreme['description'],
            'detail': extreme,
        })

    if len(anomaly_signals) == 2:
        findings.append({
            'type': 'mild_anomaly',
            'priority': 3,
            'title': '两项指标偏离基线',
            'description': '有2项指标同时偏离基线，不算严重但值得留意',
            'signals': anomaly_signals,
        })

    if blood_oxygen.get('is_worse_than_baseline'):
        findings.append({
            'type': 'blood_oxygen_worse',
            'priority': 4,
            'title': '血氧比已知基线更低',
            'description': '今晚血氧比你的个人基线范围还要低',
            'detail': blood_oxygen,
        })
    elif blood_oxygen.get('is_better_than_baseline'):
        findings.append({
            'type': 'blood_oxygen_better',
            'priority': 4,
            'title': '血氧明显改善',
            'description': '今晚血氧比你的个人基线范围明显改善',
            'detail': blood_oxygen,
        })

    sustained_trends: list[dict[str, Any]] = []
    for metric, trend in trends.items():
        if trend.get('direction_7d') in ['rising', 'falling']:
            sustained_trends.append({
                'type': 'sustained_trend',
                'priority': 5,
                'title': f"{trend.get('metric_cn') or METRIC_CN.get(metric, metric)}持续{'上升' if trend['direction_7d'] == 'rising' else '下降'}",
                'description': f"近7天{trend.get('metric_cn') or METRIC_CN.get(metric, metric)}持续{'上升' if trend['direction_7d'] == 'rising' else '下降'}",
                'detail': {
                    **trend,
                    'metric': metric,
                    'metric_cn': trend.get('metric_cn') or METRIC_CN.get(metric, metric),
                    'change_7d_value': float(trend.get('change_7d', 0) or 0),
                },
            })

    sustained_trends.sort(key=lambda item: abs(float(item.get('detail', {}).get('change_7d_value', 0) or 0)), reverse=True)
    findings.extend(sustained_trends[:2])

    findings.sort(key=lambda item: item['priority'])
    findings = findings[:5]
    if not findings:
        findings.append({
            'type': 'routine',
            'priority': 10,
            'title': '数据平稳',
            'description': '今日各项数据接近个人基线，整体平稳',
        })
    return findings


def analyze(user_id: str, date_str: str) -> dict[str, Any]:
    today_data = load_daily_data(user_id, date_str)
    current_date = date.fromisoformat(date_str)
    yesterday_str = (current_date - timedelta(days=1)).isoformat()
    yesterday_data = load_daily_data(user_id, yesterday_str)
    baselines = load_baselines(user_id)
    profile = load_profile(user_id)

    today_metrics = extract_key_metrics(today_data)
    yesterday_metrics = extract_key_metrics(yesterday_data)

    anomaly_signals = detect_anomaly_signals(today_metrics, baselines, baselines, yesterday_metrics=yesterday_metrics)
    trends = detect_trends(user_id, date_str, baselines, profile)
    trend_reversals = detect_trend_reversals(user_id, date_str)
    extremes = detect_extremes(user_id, date_str, baselines)
    training_status_change = detect_training_status_change(user_id, date_str)
    blood_oxygen = analyze_blood_oxygen(user_id, today_metrics)
    experiment_status = experiment.get_experiment_status(user_id, date_str)

    notable_findings = generate_notable_findings(
        anomaly_signals,
        trend_reversals,
        extremes,
        training_status_change,
        blood_oxygen,
        trends,
    )

    return {
        'user_id': user_id,
        'user_name': profile.get('display_name'),
        'date': date_str,
        'today_metrics': today_metrics,
        'yesterday_metrics': yesterday_metrics,
        'baselines_30day': baselines.get('metrics', {}),
        'baselines_7day': baselines.get('recent_7day', {}),
        'anomaly_detection': {
            'signals_count': len(anomaly_signals),
            'signals': anomaly_signals,
        },
        'trends': trends,
        'blood_oxygen': blood_oxygen,
        'training_status': training_status_change,
        'experiment': experiment_status,
        'notable_findings': notable_findings,
        'available_data': profile.get('available_data', {}),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description='运行 Garmin 分析引擎')
    parser.add_argument('user_id')
    parser.add_argument('date')
    args = parser.parse_args()
    print(json.dumps(analyze(args.user_id, args.date), ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
