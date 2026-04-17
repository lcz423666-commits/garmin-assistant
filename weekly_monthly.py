#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from numbers import Real
from pathlib import Path
from typing import Any

ROOT = Path('/root/garmin_assistant')
APP = ROOT / 'app'
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

import analysis
from report_flow import call_custom_llm

WEEKLY_PROMPT_V2 = """你是用户的私人健康分析师。以下是用户过去7天的健康数据周报输入，请写一条自然口语的周报。

在现有的睡眠恢复总结和训练总结基础上，本周周报需要额外包含：
1. 训练状态：本周的训练状态是什么，如果本周发生了变化要说明。
2. VO2 Max：如果有变化，提及它是上升还是下降。
3. 体能年龄：如果有数据，自然提一句。
4. WHO 活动强度达标情况：说明本周活动强度分钟数达到了世界卫生组织建议的多少。
4a. 活动强度分钟数必须用“分钟”为单位表达，不要换算成小时。正确：“132分钟”。错误：“2小时12分钟”。
5. 关键指标周对比：和上周相比，哪些指标在变好、哪些在变差。用方向描述，不要堆数字。
6. 本周最值得注意的一天：指出具体哪天最有故事性，简要说明原因。
7. 实验总结：如果本周有实验结束，给出结论。

要求：
- 保持自然口语风格
- 新增内容自然融入，不要生硬分段
- 总长度控制在500-600字
- 禁止 markdown 格式
- 北京时间问候
- 开头不要提具体是星期几或什么时间段，不要写“周日晚上”“这会儿是周一早上”之类的句子，直接进入过去7天的回顾
- 训练状态是佳明系统根据数据自动判定的，不要暗示这是用户主动安排或主动选择的结果
- 关于步数数据：如果用户的日均步数波动很大（标准差接近或超过均值），更可能是有些天手表没有佩戴完整一天，而不是用户真的完全没活动。遇到这种情况不要直接下“活动量不足”的结论，可以改说“部分天数的步数记录偏低，可能和佩戴时长有关”
- 不要用“恭喜你”“值得表扬”“太棒了”“做足了功课”“安排得很聪明”这类夸奖或评价用户行为的话，只陈述数据本身的积极或消极变化
- 关于数据缺失的处理：如果某项数据为 null、0 或明显不合理（比如活动强度分钟数为0但用户刚完成了一次训练），不要在文案中提及这项数据。宁可少说一个维度，也不要输出错误的数据。
- 输出格式要求：不要在文案中包含分隔线（---）、字数统计（如“约370字”）、或任何元标注。文案结尾应该是一个完整的、有实际内容的句子，不要用单独一两个词收尾。
"""

MONTHLY_PROMPT_V2 = """你是用户的私人健康分析师。以下是用户上个月的健康数据月度总结。请根据数据写一份月报。

内容需要自然融合：
1. 体能变化：VO2 Max 和体能年龄。
2. 训练概况：训练了多少次、多少小时，训练状态分布如何。
3. 睡眠与恢复趋势：月均睡眠评分、深睡、HRV、Body Battery 的整体水平。
3a. 如果提到活动强度分钟数，也必须用“分钟”为单位表达，不要换算成小时。正确：“132分钟”。错误：“2小时12分钟”。
4. 和上月对比：哪些指标变好了、哪些变差了。
5. 身体规律发现：patterns 中的规律要自然说出来。
6. 下月建议：给出1-2个具体方向。

要求：
- 自然口语，像朋友间的月度回顾
- 600-800字
- 禁止 markdown 格式
- 北京时间问候
- 重点突出变化和趋势，不要堆砌数字
- 关于步数数据：如果用户的日均步数波动很大（标准差接近或超过均值），更可能是有些天手表没有佩戴完整一天，而不是用户真的完全没活动。遇到这种情况不要直接下“活动量不足”的结论，可以改说“部分天数的步数记录偏低，可能和佩戴时长有关”
- 不要用“恭喜你”“值得表扬”“太棒了”“做足了功课”“安排得很聪明”这类夸奖或评价用户行为的话，只陈述数据本身的积极或消极变化
- 关于数据缺失的处理：如果某项数据为 null、0 或明显不合理（比如活动强度分钟数为0但用户刚完成了一次训练），不要在文案中提及这项数据。宁可少说一个维度，也不要输出错误的数据。
语气参考标准：和晨报保持一致。你是一个专业但亲切的朋友，在做月度回顾。不是在写健康公众号文章，不是在写年终总结，不要用夸张的感叹句。平实、有洞察、像两个认真的人在聊天。
- 输出格式要求：不要在文案中包含分隔线（---）、字数统计（如“约370字”）、或任何元标注。文案结尾应该是一个完整的、有实际内容的句子，不要用单独一两个词收尾。
"""


def load_json(path: Path, default: Any):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def load_profile(user_id: str) -> dict[str, Any]:
    return load_json(ROOT / 'data' / user_id / 'profile.json', {})


def list_daily_files(user_id: str) -> list[Path]:
    return sorted((ROOT / 'data' / user_id / 'daily').glob('*.json'))


def load_last_n_days(user_id: str, n: int, end_date: date) -> list[dict[str, Any]]:
    result = []
    for path in list_daily_files(user_id):
        try:
            d = date.fromisoformat(path.stem)
        except Exception:
            continue
        if d <= end_date:
            result.append(load_json(path, {}))
    return result[-n:]


def load_month_data(user_id: str, year: int, month: int) -> list[dict[str, Any]]:
    result = []
    for path in list_daily_files(user_id):
        try:
            d = date.fromisoformat(path.stem)
        except Exception:
            continue
        if d.year == year and d.month == month:
            result.append(load_json(path, {}))
    return result


def mean_of(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


REPORT_CHART_PRIORITY = [
    {
        "metric": "sleep_score",
        "label": "睡眠评分",
        "unit": "分",
        "line_color": "#245A78",
    },
    {
        "metric": "bb_wake",
        "label": "起床Body Battery",
        "unit": "分",
        "line_color": "#B06A3A",
    },
    {
        "metric": "hrv_night_avg",
        "label": "夜间HRV",
        "unit": "ms",
        "line_color": "#3D6E68",
    },
    {
        "metric": "resting_hr",
        "label": "静息心率",
        "unit": "次/分",
        "line_color": "#6A4C93",
    },
    {
        "metric": "vo2max",
        "label": "VO2 Max",
        "unit": "",
        "line_color": "#2F5A8A",
    },
]


def _short_date_label(date_value: str) -> str:
    try:
        return parse_date(date_value).strftime("%m/%d")
    except Exception:
        return date_value[-5:] if isinstance(date_value, str) and len(date_value) >= 5 else str(date_value)


def _coerce_chart_value(raw_value: Any, transform=None) -> float | None:
    if raw_value is None or isinstance(raw_value, bool) or not isinstance(raw_value, Real):
        return None
    value = float(raw_value)
    if transform is not None:
        try:
            value = float(transform(value))
        except Exception:
            return None
    if value != value:
        return None
    return round(value, 1)


def _build_report_chart_history(days_data: list[dict[str, Any]], *, window_label: str, min_points: int) -> dict[str, Any] | None:
    for candidate in REPORT_CHART_PRIORITY:
        points = []
        observed_points = 0
        for day in days_data:
            metrics = analysis.extract_key_metrics(day) or {}
            raw_value = metrics.get(candidate["metric"])
            value = _coerce_chart_value(raw_value, candidate.get("transform"))
            if value is not None:
                observed_points += 1
            points.append(
                {
                    "date": day.get("date"),
                    "label": _short_date_label(day.get("date") or ""),
                    "value": value,
                }
            )
        if observed_points < min_points:
            continue
        return {
            "topic": candidate["metric"],
            "title": f"过去{window_label}{candidate['label']}趋势",
            "line_color": candidate["line_color"],
            "series_label": candidate["label"],
            "value_unit": candidate["unit"],
            "history_points": points,
            "observed_points": observed_points,
            "window_label": window_label,
        }
    return None


def extract_month_metric(days: list[dict[str, Any]], metric: str) -> list[float]:
    values = []
    for day in days:
        metrics = analysis.extract_key_metrics(day)
        value = metrics.get(metric) if metrics else None
        if isinstance(value, (int, float)):
            values.append(float(value))
    return values


def calculate_week_averages(days_data: list[dict[str, Any]]) -> dict[str, float]:
    out = {}
    for metric in ['sleep_score', 'hrv_night_avg', 'bb_wake', 'resting_hr', 'deep_sleep_seconds', 'respiration_sleep_avg']:
        vals = []
        for day in days_data:
            metrics = analysis.extract_key_metrics(day)
            val = metrics.get(metric) if metrics else None
            if isinstance(val, (int, float)):
                vals.append(float(val))
        if vals:
            out[metric] = sum(vals) / len(vals)
    return out


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def discover_patterns(month_days: list[dict[str, Any]]) -> list[str]:
    patterns = []
    weekday_scores = []
    weekend_scores = []
    for day in month_days:
        metrics = analysis.extract_key_metrics(day)
        score = metrics.get('sleep_score') if metrics else None
        if score is None:
            continue
        dt = parse_date(day['date'])
        if dt.weekday() < 5:
            weekday_scores.append(score)
        else:
            weekend_scores.append(score)
    if weekday_scores and weekend_scores:
        wd_avg = sum(weekday_scores) / len(weekday_scores)
        we_avg = sum(weekend_scores) / len(weekend_scores)
        diff = we_avg - wd_avg
        if abs(diff) > 5:
            direction = '好于' if diff > 0 else '差于'
            patterns.append(f'周末睡眠评分平均{direction}工作日约{abs(round(diff))}分')

    training_next_bb = []
    rest_next_bb = []
    for i in range(len(month_days) - 1):
        today = month_days[i]
        tomorrow = month_days[i + 1]
        today_metrics = analysis.extract_key_metrics(today)
        tomorrow_metrics = analysis.extract_key_metrics(tomorrow)
        if not today_metrics or not tomorrow_metrics:
            continue
        tomorrow_bb = tomorrow_metrics.get('bb_wake')
        if tomorrow_bb is None:
            continue
        has_activity = (today_metrics.get('daily_steps') or 0) > 8000
        if has_activity:
            training_next_bb.append(tomorrow_bb)
        else:
            rest_next_bb.append(tomorrow_bb)
    if training_next_bb and rest_next_bb:
        t_avg = sum(training_next_bb) / len(training_next_bb)
        r_avg = sum(rest_next_bb) / len(rest_next_bb)
        diff = t_avg - r_avg
        if abs(diff) > 8 and diff < 0:
            patterns.append(f'运动日之后的起床Body Battery平均比休息日之后低{abs(round(diff))}分')

    day_deep_sleep = {i: [] for i in range(7)}
    for day in month_days:
        metrics = analysis.extract_key_metrics(day)
        deep = metrics.get('deep_sleep_seconds') if metrics else None
        if deep:
            dt = parse_date(day['date'])
            day_deep_sleep[dt.weekday()].append(deep)
    day_names = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
    best_day = None
    best_avg = 0
    worst_day = None
    worst_avg = float('inf')
    for i, values in day_deep_sleep.items():
        if len(values) >= 2:
            avg = sum(values) / len(values)
            if avg > best_avg:
                best_avg = avg
                best_day = i
            if avg < worst_avg:
                worst_avg = avg
                worst_day = i
    if best_day is not None and worst_day is not None and best_day != worst_day:
        diff_min = round((best_avg - worst_avg) / 60)
        if diff_min > 10:
            patterns.append(f'{day_names[best_day]}的深睡通常比{day_names[worst_day]}多约{diff_min}分钟')
    return patterns


def find_most_notable_day(user_id: str, days_data: list[dict[str, Any]], baselines: dict[str, Any]) -> dict[str, Any] | None:
    most_notable = None
    max_deviation_score = 0.0
    check_metrics = ['hrv_night_avg', 'bb_wake', 'resting_hr', 'respiration_sleep_avg', 'sleep_score']

    for day in days_data:
        metrics = analysis.extract_key_metrics(day)
        if not metrics:
            continue
        deviation_score = 0.0
        for metric in check_metrics:
            value = metrics.get(metric)
            baseline = (baselines.get('metrics', {}) or {}).get(metric, {})
            b_mean = baseline.get('mean')
            b_std = baseline.get('std')
            if value is not None and b_mean is not None and b_std not in (None, 0):
                deviation_score += abs(float(value) - float(b_mean)) / float(b_std)
        if deviation_score > max_deviation_score:
            max_deviation_score = deviation_score
            most_notable = {
                'date': day['date'],
                'deviation_score': round(deviation_score, 1),
                'description': '多项指标偏离基线程度最大的一天',
            }
    return most_notable


def build_weekly_report_data(user_id: str, end_date: date) -> dict[str, Any]:
    days_data = load_last_n_days(user_id, 7, end_date)
    profile = load_profile(user_id)
    training_statuses = []
    vo2max_values = []
    latest_intensity = None
    endurance_scores = []
    hill_scores = []
    baselines = load_json(ROOT / 'data' / user_id / 'baselines.json', {})
    most_notable_day = find_most_notable_day(user_id, days_data, baselines)
    for day in days_data:
        metrics = analysis.extract_key_metrics(day)
        if not metrics:
            continue
        if metrics.get('training_status_value'):
            training_statuses.append({'date': day['date'], 'status': metrics['training_status_value']})
        if metrics.get('vo2max') is not None:
            vo2max_values.append(metrics['vo2max'])
        moderate = metrics.get('weekly_moderate_minutes') or 0
        vigorous = metrics.get('weekly_vigorous_minutes') or 0
        if moderate or vigorous:
            total_equivalent = moderate + vigorous * 2
            latest_intensity = {
                'moderate': moderate,
                'vigorous': vigorous,
                'total_equivalent': total_equivalent,
                'who_target': 150,
                'completion_pct': round(total_equivalent / 150 * 100),
            }
        if metrics.get('endurance_score_value') is not None:
            endurance_scores.append(metrics['endurance_score_value'])
        if metrics.get('hill_score_value') is not None:
            hill_scores.append(metrics['hill_score_value'])

    fitness_age = None
    for day in reversed(days_data):
        metrics = analysis.extract_key_metrics(day)
        if metrics and metrics.get('fitness_age') is not None:
            fitness_age = metrics['fitness_age']
            break

    this_week_metrics = calculate_week_averages(days_data)
    last_week_data = load_last_n_days(user_id, 7, end_date - timedelta(days=7))
    last_week_metrics = calculate_week_averages(last_week_data)
    week_comparison = {}
    for metric in ['sleep_score', 'hrv_night_avg', 'bb_wake', 'resting_hr', 'deep_sleep_seconds', 'respiration_sleep_avg']:
        this_val = this_week_metrics.get(metric)
        last_val = last_week_metrics.get(metric)
        if this_val is not None and last_val is not None:
            diff = round(this_val - last_val, 1)
            week_comparison[metric] = {
                'this_week': round(this_val, 1),
                'last_week': round(last_val, 1),
                'change': diff,
                'direction': '↑' if diff > 0 else '↓' if diff < 0 else '→',
            }

    experiment_summary = None
    for exp in reversed(profile.get('experiment_history', [])):
        exp_end = parse_date(exp['end_date'])
        if (end_date - timedelta(days=7)) <= exp_end <= end_date:
            experiment_summary = exp
            break

    chart_history = _build_report_chart_history(days_data, window_label="7天", min_points=4)

    return {
        'user_name': profile.get('display_name'),
        'end_date': end_date.isoformat(),
        'training_statuses': training_statuses,
        'vo2max_change': round(vo2max_values[-1] - vo2max_values[0], 1) if len(vo2max_values) >= 2 else None,
        'vo2max_latest': vo2max_values[-1] if vo2max_values else None,
        'fitness_age': fitness_age,
        'intensity_minutes': latest_intensity,
        'endurance_score_latest': endurance_scores[-1] if endurance_scores else None,
        'hill_score_latest': hill_scores[-1] if hill_scores else None,
        'week_comparison': week_comparison,
        'most_notable_day': most_notable_day,
        'experiment_summary': experiment_summary,
        'chart_history': chart_history,
        'chart_image_url': None,
        'days': [day['date'] for day in days_data],
    }


def build_monthly_report_data(user_id: str, year: int, month: int) -> dict[str, Any]:
    month_days = load_month_data(user_id, year, month)
    prev_year, prev_month = (year, month - 1) if month > 1 else (year - 1, 12)
    prev_month_days = load_month_data(user_id, prev_year, prev_month)
    profile = load_profile(user_id)
    vo2max_values = extract_month_metric(month_days, 'vo2max')
    fitness_age_values = extract_month_metric(month_days, 'fitness_age')
    sleep_metrics = {
        'avg_score': mean_of(extract_month_metric(month_days, 'sleep_score')),
        'avg_deep_sleep_min': round(mean_of(extract_month_metric(month_days, 'deep_sleep_seconds')) / 60, 1) if extract_month_metric(month_days, 'deep_sleep_seconds') else None,
        'avg_duration_hours': round(mean_of(extract_month_metric(month_days, 'sleep_duration_seconds')) / 3600, 1) if extract_month_metric(month_days, 'sleep_duration_seconds') else None,
    }
    recovery_metrics = {
        'avg_hrv': mean_of(extract_month_metric(month_days, 'hrv_night_avg')),
        'avg_bb_wake': mean_of(extract_month_metric(month_days, 'bb_wake')),
        'avg_rhr': mean_of(extract_month_metric(month_days, 'resting_hr')),
    }
    month_comparison = {
        'sleep_score': {'this': sleep_metrics['avg_score'], 'prev': mean_of(extract_month_metric(prev_month_days, 'sleep_score'))},
        'hrv': {'this': recovery_metrics['avg_hrv'], 'prev': mean_of(extract_month_metric(prev_month_days, 'hrv_night_avg'))},
        'bb_wake': {'this': recovery_metrics['avg_bb_wake'], 'prev': mean_of(extract_month_metric(prev_month_days, 'bb_wake'))},
    }
    status_counts = {}
    activity_days = 0
    total_steps = 0
    for day in month_days:
        metrics = analysis.extract_key_metrics(day)
        if not metrics:
            continue
        if metrics.get('training_status_value'):
            status_counts[metrics['training_status_value']] = status_counts.get(metrics['training_status_value'], 0) + 1
        if (metrics.get('daily_steps') or 0) > 8000:
            activity_days += 1
        total_steps += metrics.get('daily_steps') or 0
    training_summary = {
        'total_activities': activity_days,
        'total_duration_hours': round(activity_days * 0.8, 1),
        'activity_types': {},
        'avg_daily_steps': round(total_steps / len(month_days), 1) if month_days else None,
    }
    patterns = discover_patterns(month_days)
    chart_history = _build_report_chart_history(month_days, window_label=f"{len(month_days)}天", min_points=10)
    return {
        'user_name': profile.get('display_name'),
        'year': year,
        'month': month,
        'days_of_data': len(month_days),
        'vo2max_start': vo2max_values[0] if vo2max_values else None,
        'vo2max_end': vo2max_values[-1] if vo2max_values else None,
        'fitness_age': fitness_age_values[-1] if fitness_age_values else None,
        'training_summary': training_summary,
        'training_status_distribution': status_counts,
        'sleep_metrics': sleep_metrics,
        'recovery_metrics': recovery_metrics,
        'month_comparison': month_comparison,
        'patterns': patterns,
        'experiment_history': [e for e in profile.get('experiment_history', []) if parse_date(e['end_date']).year == year and parse_date(e['end_date']).month == month],
        'chart_history': chart_history,
        'chart_image_url': None,
    }


def generate_weekly_report(user_id: str, end_date: date) -> tuple[str, dict[str, Any]]:
    payload = build_weekly_report_data(user_id, end_date)
    user_prompt = f"以下是 {payload['user_name']} 截止 {payload['end_date']} 的周报数据。请写一条过去7天周报。\n\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    return call_custom_llm(WEEKLY_PROMPT_V2, user_prompt, push_type='weekly'), payload


def generate_monthly_report(user_id: str, year: int, month: int) -> tuple[str, dict[str, Any]]:
    payload = build_monthly_report_data(user_id, year, month)
    user_prompt = f"以下是 {payload['user_name']} 的 {year}年{month}月 月报数据。请写一条月报。\n\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    return call_custom_llm(MONTHLY_PROMPT_V2, user_prompt, push_type='monthly'), payload
