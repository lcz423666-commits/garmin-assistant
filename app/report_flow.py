#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

APP_DIR = Path('/root/garmin_assistant/app')
ROOT_DIR = Path('/root/garmin_assistant')
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import analysis
import baseline as baseline_module
from llm_helper import client, LLM_MODEL, clean_output
from knowledge_helper import build_system_prompt

BJ_TZ = timezone(timedelta(hours=8))
DATA_DIR = ROOT_DIR / 'data'
SOURCE_TO_USER_ID = {'丛至': 'congzhi', '杨': 'yang', 'Kevin': 'kevin'}
ACTIVITY_LOG_DIR = ROOT_DIR / 'logs' / 'activity_llm_payloads'

MEANINGFUL_ACTIVITY_INTENSITY_MINUTES = 20
MEANINGFUL_ACTIVITY_TRAINING_LOAD = 15
MEANINGFUL_ACTIVITY_DURATION_MIN = 30
MEANINGFUL_ACTIVITY_MIN_DISTANCE_KM = 1.0
MEANINGFUL_ACTIVITY_MIN_AVG_HR = 95
WHO_WEEKLY_TARGET_MINUTES = 150
EVENING_SNAPSHOT_REFRESH_HOUR = 18

NEW_MORNING_SYSTEM_PROMPT = """你是用户的私人健康分析师，基于佳明手表数据每天为用户提供一条个性化健康分析。

## 你会收到什么

你会收到一个 JSON 数据，包含：
- today_metrics：今天的关键健康指标数值
- yesterday_metrics：昨天的指标（用于对比）
- baselines_30day / baselines_7day：个人30天和7天基线统计
- anomaly_detection：异常信号检测结果（代码已预先计算）
- trends：各指标的3天和7天趋势方向
- notable_findings：今日最值得说的发现列表（按优先级排序，代码已预先计算）
- blood_oxygen：血氧状态和处理建议
- training_status：训练状态信息
- experiment：当前观察实验状态（可能是 new / in_progress / completed）
- available_data：该用户手表支持哪些数据

## 你的核心任务

从 notable_findings 中选择优先级最高的1-2个发现作为今天的焦点，围绕焦点写一段有深度的健康分析。

## 绝对禁止

1. 禁止用以下模板化开头（这些开头已经被用了几个月，用户已经厌烦）：
   - "昨晚整体恢复不差，但有一个明确提醒不能忽略"
   - "昨晚恢复没有完全拉起来"
   - "昨晚恢复中等偏稳，不算糟，但也不是完全无忧"
   - "昨晚整体恢复是偏好的，今天的底子比较稳"
   - 任何以"昨晚恢复"开头的句子
   - 任何以"昨晚整体"开头的句子

2. 禁止面面俱到地罗列所有指标。不要把睡眠时长、深睡、REM、HRV、BB、压力等数据全说一遍。用户打开佳明App就能看到这些数字，你的价值不是复述数据。

3. 当 blood_oxygen.should_mention 为 false 时，禁止在文案中提及血氧。不要说"最低血氧降到多少"、"连续第几晚偏低"。这个用户的血氧已经是已知的个人基线特征，不需要每天重复。

4. 禁止使用 markdown 格式（不要加粗、不要标题、不要列表符号）。

5. 禁止给出佳明手表无法验证的建议（如：睡前不看手机、多喝水、早点吃晚饭、冥想、泡脚等）。所有建议必须是能被手表数据验证的。

6. 关于步数数据：除非最近完整7天里出现明显极低、并且很像佩戴不完整的记录，否则不要主动把步数波动写成晨报重点。像 6000 到 10000 这种常见范围不算“波动很大”。当天早晨的步数还没走完，不能拿来和完整天数并列判断波动。

7. 不要用“恭喜你”“值得表扬”“太棒了”这类夸奖式表达，也不要评价用户行为本身。只陈述数据本身体现出的积极性或风险。

8. 关于数据缺失的处理：如果某项数据为 null、0 或明显不合理（比如活动强度分钟数为0但用户刚完成了一次训练），不要在文案中提及这项数据。宁可少说一个维度，也不要输出错误的数据。

9. 输出格式要求：不要在文案中包含分隔线（---）、字数统计（如“约370字”）、或任何元标注。文案结尾应该是一个完整的、有实际内容的句子，不要用单独一两个词收尾。

## 写作要求

### 开头
直接切入今天最值得说的焦点。开头第一句就应该让用户知道"今天有什么不同"。

### 焦点优先级
按 notable_findings 的 priority 选择焦点：
- priority 1（身体状态预警）：这是最重要的，必须作为主焦点。说清楚哪几项指标同时偏离了基线，这意味着什么（可能是感冒前兆、过度疲劳、或综合压力反应），建议留意身体感受（嗓子、鼻塞、乏力等感觉）。
- priority 2（趋势转折/训练状态变化）：作为主焦点或次焦点。趋势转折要说清楚"连续X天走低后反弹"或"连续上升后掉头"。
- priority 3（极值/轻度异常）：作为次焦点提及。
- priority 4-5（血氧变化/持续趋势）：作为背景信息带一句。

### 内容结构
不要分标题段落。用自然的口语把以下内容融为一体：
1. 焦点分析（占60-70%篇幅）：围绕今天最值得说的发现展开。关键是做跨指标关联——把几个指标联系起来分析，而不是一个一个单独说。
2. 背景信息（占20%篇幅）：其他指标的简短状态，只提和焦点相关的。
3. 前瞻建议（占10-20%篇幅）：基于今天的数据对今天的安排给出建议。

### 跨指标关联分析
这是你最大的价值。佳明App把每个指标分开展示，用户看不到指标之间的关联。你要帮他们看到：
- "呼吸频率偏快 + 静息心率升高 + HRV下降"出现在一起时，通常意味着身体在应对某种内部挑战
- "深睡增加 + HRV回升 + BB充满"出现在一起时，说明恢复系统在高效运转
- "训练状态变为低效 + 近7天HRV持续下降"出现在一起时，可能需要减量
- 用你的判断力去发现数据之间的关联，不要机械套用

### 语气
- 像一个专业但亲切的朋友，不像医生、不像健身教练
- 北京时间问候（早上好+用户名）
- 自然口语化
- 用户名使用 JSON 中的 user_name 字段

### 长度
300-400字。这个长度适合微信阅读。

### 特殊场景处理

当 notable_findings 中只有 priority 10（routine/数据平稳）时：
- 不要硬找问题说。数据平稳本身就是一个好消息
- 可以说"今天各项数据都在你的正常范围内"
- 可以提一两个趋势方向作为观察点
- 这种时候文案可以短一些，200-250字即可

当 anomaly_detection.signals_count >= 3 时：
- 这是"身体状态预警"场景，必须作为主焦点
- 但语气不是恐吓，而是"提前留意"
- 具体说出哪几个指标偏离了，不要笼统说"多项指标异常"
- 建议用户留意身体感受：嗓子不适、鼻塞、乏力、头痛等
- 建议当天暂缓高强度训练

当 training_status.changed 为 true 时：
- 说明训练状态发生了变化
- 如果从高效变为低效或过度负荷，这是一个重要的警示
- 如果从低效变为高效，这是一个好消息


当 experiment 不为空时：
- status = "new"：把实验自然介绍进去，语气是“我来帮你留意”，不要像在给用户布置任务
- status = "new"：如果 payload 里已经有 `values_so_far`，说明今天这次有效观测已经记进去了，可以写“今天先开始观察”，但不要写“从明天开始”
- status = "in_progress"：只按 `values_so_far` 的实际条数描述进度；如果目前只积累了 2 天有效数据，就写“这两次观察”或“目前这几次观察”，不要硬说“第5天”“最后一天”，也不要写成完整 5 天平均
- status = "completed"：用2-3句话收尾，告诉用户这几天观察到了什么规律或结果
- 只有 status = "completed" 才能总结整个观察期；如果有效数据天数少于 `duration_days`，要明确写“这段观察里拿到的有效数据”，不要冒充满额 5 天
- 关于极值措辞：只有当 `notable_findings` 明确给出“最高/最低”时，才可以直接写“最高/最低”；如果 payload 给的是“高位/低位”，禁止擅自升级成“最高/最低”
- 实验内容永远是陪伴式表达，不要写“请你坚持”“请你配合”“建议你完成实验”
"""

EVENING_SYSTEM_PROMPT = """你是用户的私人健康分析师。现在是晚间7点，根据用户今天一整天的数据给出一条简短的晚间提醒。

## 触发场景

你会收到一个 triggers 列表，说明为什么今天需要晚间推送。根据触发原因来组织内容：

### low_activity（今天活动量很少）
- 不要说教，不要批评用户"运动太少"
- 像朋友一样轻松提一句
- 可以说"今天整体是个低活动日，如果晚饭后有空出去走走，对今晚睡眠会有帮助"
- 不要说"你应该去运动"

### recovery_reminder（今天有过高强度训练）
- 提醒恢复的重要性
- 建议今晚优先保证睡眠时长
- 可以说"明天晨报我来看看身体消化这次训练的情况"
- 制造用户明天想看晨报的期待感

### extreme_fatigue（Body Battery 极低）
- 明确建议今晚早休息
- 语气关切但不恐慌

### high_stress_day（今天压力偏高）
- 提醒放松，但不要说"冥想"、"不看手机"这种大部分人做不到的事
- 可以说"尽量让晚上轻松一些"

### anomaly_followup（今天晨报有预警的跟进）
- 关心用户今天的身体感受
- 虽然用户无法回复，但"今天感觉怎么样"这种关心本身有价值
- 提醒今晚睡眠很重要
- 可以说"明天我来看看各项指标有没有回到你的基线范围"

## 写作要求
- 简短，150-200字
- 不重复今天晨报已经说过的具体内容
- 语气轻松自然
- 面向今晚和明天，不回顾今天的数据细节
- 只基于佳明实际采集的数据给建议
- 北京时间问候（"晚上好" + 用户名）
- 禁止 markdown 格式
- 如果有多个触发原因，选最重要的1-2个展开，不要全说

## 特别注意
- 晚间推送不是每天都有的。用户收到晚间推送说明今天有值得说的情况，所以内容要有实际价值
- 制造"明天想看晨报"的期待感——比如"明天晨报我来看看今晚的恢复数据"
- 关于数据缺失的处理：如果某项数据为 null、0 或明显不合理，不要在文案中提及这项数据。宁可少说一个维度，也不要输出错误的数据。
- 输出格式要求：不要在文案中包含分隔线（---）、字数统计（如“约370字”）、或任何元标注。文案结尾应该是一个完整的、有实际内容的句子，不要用单独一两个词收尾。
"""


def load_profile_json(user_id: str) -> dict[str, Any]:
    path = DATA_DIR / user_id / 'profile.json'
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def save_profile_json(user_id: str, payload: dict[str, Any]) -> None:
    path = DATA_DIR / user_id / 'profile.json'
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding='utf-8')


def extract_known_activity_issues(user_id: str) -> dict[str, Any]:
    profile = load_profile_json(user_id)
    issues = profile.get('known_activity_issues') or {}
    if isinstance(issues, list):
        return {'rules': issues, 'counts': {rule: 1 for rule in issues}}
    if isinstance(issues, dict):
        return issues
    return {'rules': [], 'counts': {}}


def update_known_activity_issues(user_id: str, llm_payload: dict[str, Any]) -> None:
    profile = load_profile_json(user_id)
    known = extract_known_activity_issues(user_id)
    counts = dict(known.get('counts') or {})
    current_rules = []
    for item in (llm_payload.get('priority_issues') or []) + (llm_payload.get('secondary_issues') or []):
        rule = item.get('rule')
        if not rule:
            continue
        counts[rule] = counts.get(rule, 0) + 1
        current_rules.append(rule)
    profile['known_activity_issues'] = {
        'rules': sorted(counts.keys()),
        'repeat_rules': sorted(rule for rule, count in counts.items() if count >= 2),
        'counts': counts,
        'last_seen_rules': current_rules,
    }
    save_profile_json(user_id, profile)


def log_activity_llm_payload(user_id: str, date_str: str, payload: dict[str, Any]) -> Path:
    ACTIVITY_LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = ACTIVITY_LOG_DIR / f'{user_id}_{date_str}.json'
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding='utf-8')
    return path




def _to_float(value: Any) -> float | None:
    try:
        if value in (None, ''):
            return None
        return float(value)
    except Exception:
        return None


def _week_start(date_str: str):
    current = datetime.fromisoformat(date_str).date()
    return current - timedelta(days=current.weekday())


def _activity_dir_candidates(source_name: str, user_id: str) -> list[Path]:
    profile = load_profile_json(user_id)
    candidates = [
        DATA_DIR / source_name / 'activity',
        DATA_DIR / (profile.get('display_name') or '') / 'activity',
        DATA_DIR / user_id / 'activity',
    ]
    result: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if not key or key in seen:
            continue
        seen.add(key)
        if path.exists():
            result.append(path)
    return result


def load_latest_activity_summary(source_name: str, user_id: str, date_str: str) -> dict[str, Any] | None:
    latest_obj = None
    latest_recorded = ''
    for activity_dir in _activity_dir_candidates(source_name, user_id):
        for path in activity_dir.glob('*.json'):
            if '.bak_' in path.name:
                continue
            try:
                payload = json.loads(path.read_text(encoding='utf-8'))
            except Exception:
                continue
            meta = payload.get('metadata') or {}
            if meta.get('activity_date') != date_str:
                continue
            recorded_at = meta.get('recorded_at') or meta.get('saved_at') or ''
            if recorded_at >= latest_recorded:
                latest_recorded = recorded_at
                latest_obj = payload
    if not latest_obj:
        return None
    raw_data = latest_obj.get('raw_data') or {}
    summary = raw_data.get('activity_summary') if isinstance(raw_data, dict) else None
    if isinstance(summary, dict) and summary:
        return summary
    if isinstance(raw_data, dict) and raw_data:
        return raw_data
    return None


def derive_session_intensity_minutes(activity_summary: dict[str, Any] | None) -> int | None:
    if not isinstance(activity_summary, dict):
        return None
    moderate = _to_float(activity_summary.get('moderateIntensityMinutes')) or 0.0
    vigorous = _to_float(activity_summary.get('vigorousIntensityMinutes')) or 0.0
    total = int(round(moderate + vigorous * 2))
    return total if total > 0 else None


def _iter_same_day_activity_summaries(api: Any, source_name: str, user_id: str, date_str: str):
    seen_ids: set[str] = set()
    for activity_dir in _activity_dir_candidates(source_name, user_id):
        for path in activity_dir.glob('*.json'):
            if '.bak_' in path.name:
                continue
            try:
                payload = json.loads(path.read_text(encoding='utf-8'))
            except Exception:
                continue
            meta = payload.get('metadata') or {}
            if meta.get('activity_date') != date_str:
                continue
            record_key = str(meta.get('record_key') or path.stem)
            if record_key in seen_ids:
                continue
            raw_data = payload.get('raw_data') or {}
            summary = raw_data.get('activity_summary') if isinstance(raw_data, dict) else None
            if not isinstance(summary, dict) or not summary:
                continue
            seen_ids.add(record_key)
            yield summary

    try:
        for activity in fetch_today_activities(api, date_str):
            activity_id = str(activity.get('activityId') or '')
            if activity_id and activity_id in seen_ids:
                continue
            if activity_id:
                seen_ids.add(activity_id)
            if isinstance(activity, dict) and activity:
                yield activity
    except Exception:
        return


def _extract_meaningful_activity_signal(activity_summary: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(activity_summary, dict) or not activity_summary:
        return None

    session_minutes = derive_session_intensity_minutes(activity_summary) or 0
    training_load = _to_float(activity_summary.get('activityTrainingLoad'))
    if training_load is None:
        training_load = _to_float(activity_summary.get('trainingStressScore'))
    duration_sec = _to_float(activity_summary.get('movingDuration'))
    if duration_sec is None or duration_sec <= 0:
        duration_sec = _to_float(activity_summary.get('duration')) or 0.0
    duration_min = round(float(duration_sec) / 60, 1) if duration_sec else 0.0
    distance_km = round(((_to_float(activity_summary.get('distance')) or 0.0) / 1000), 2)
    avg_hr = _to_float(activity_summary.get('averageHR')) or 0.0
    activity_name = (
        activity_summary.get('activityName')
        or activity_summary.get('activityType', {}).get('typeKey')
        or activity_summary.get('activityType', {}).get('typeId')
        or 'activity'
    )

    signal = {
        'activity_name': activity_name,
        'session_intensity_minutes': session_minutes,
        'training_load': training_load,
        'duration_min': duration_min,
        'distance_km': distance_km,
        'average_hr': avg_hr,
    }
    if session_minutes >= MEANINGFUL_ACTIVITY_INTENSITY_MINUTES:
        signal['reason'] = 'session_intensity_minutes'
        return signal
    if training_load is not None and training_load >= MEANINGFUL_ACTIVITY_TRAINING_LOAD:
        signal['reason'] = 'training_load'
        return signal
    if (
        duration_min >= MEANINGFUL_ACTIVITY_DURATION_MIN
        and (distance_km >= MEANINGFUL_ACTIVITY_MIN_DISTANCE_KM or avg_hr >= MEANINGFUL_ACTIVITY_MIN_AVG_HR)
    ):
        signal['reason'] = 'sustained_session'
        return signal
    return None


def detect_meaningful_same_day_activity(api: Any, source_name: str, user_id: str, date_str: str) -> dict[str, Any] | None:
    for activity_summary in _iter_same_day_activity_summaries(api, source_name, user_id, date_str):
        signal = _extract_meaningful_activity_signal(activity_summary)
        if signal:
            return signal
    return None


def _log_low_activity_skip(source_name: str, date_str: str, daily_steps: int | float | None, signal: dict[str, Any]) -> None:
    timestamp = datetime.now(BJ_TZ).strftime('%Y-%m-%d %H:%M:%S')
    print(
        f"[{timestamp}] [{source_name}] 晚间 low_activity 已跳过: date={date_str} | steps={daily_steps} | "
        f"activity={signal.get('activity_name')} | reason={signal.get('reason')} | "
        f"intensity_min={signal.get('session_intensity_minutes')} | "
        f"training_load={signal.get('training_load')} | duration_min={signal.get('duration_min')}"
    )


def _log_evening_snapshot_refresh(source_name: str, date_str: str, previous_fetch_time: str | None, refreshed_fetch_time: str | None) -> None:
    timestamp = datetime.now(BJ_TZ).strftime('%Y-%m-%d %H:%M:%S')
    print(
        f"[{timestamp}] [{source_name}] 晚间刷新 daily 快照: date={date_str} | "
        f"previous_fetch_time={previous_fetch_time} | refreshed_fetch_time={refreshed_fetch_time}"
    )


def _snapshot_needs_evening_refresh(today_data: dict[str, Any] | None, date_str: str) -> bool:
    if not today_data:
        return True
    fetch_time_raw = today_data.get('fetch_time')
    if not isinstance(fetch_time_raw, str) or not fetch_time_raw:
        return True
    try:
        fetch_time = datetime.fromisoformat(fetch_time_raw)
    except Exception:
        return True
    fetch_time = fetch_time.astimezone(BJ_TZ)
    return fetch_time.date().isoformat() != date_str or fetch_time.hour < EVENING_SNAPSHOT_REFRESH_HOUR


def load_evening_daily_data(api: Any, source_name: str, user_id: str, date_str: str) -> dict[str, Any] | None:
    today_data = analysis.load_daily_data(user_id, date_str)
    if not _snapshot_needs_evening_refresh(today_data, date_str):
        return today_data

    previous_fetch_time = today_data.get('fetch_time') if isinstance(today_data, dict) else None
    try:
        import daily_snapshot

        daily_snapshot.save_daily_snapshot(api, user_id, date_str)
        refreshed_data = analysis.load_daily_data(user_id, date_str)
        refreshed_fetch_time = refreshed_data.get('fetch_time') if isinstance(refreshed_data, dict) else None
        _log_evening_snapshot_refresh(source_name, date_str, previous_fetch_time, refreshed_fetch_time)
        if refreshed_data:
            return refreshed_data
    except Exception:
        return today_data

    return today_data


def _extract_daily_weekly_total(user_id: str, date_str: str) -> tuple[int | None, bool]:
    daily_data = analysis.load_daily_data(user_id, date_str)
    if not daily_data:
        return None, False
    metrics = analysis.extract_key_metrics(daily_data) or {}
    moderate = metrics.get('weekly_moderate_minutes') or 0
    vigorous = metrics.get('weekly_vigorous_minutes') or 0
    weekly_total = int(round(float(moderate) + float(vigorous) * 2)) if (moderate or vigorous) else 0
    data = daily_data.get('data') or {}
    includes_activity_data = bool((data.get('user_summary') or {}).get('includesActivityData'))
    return (weekly_total if weekly_total > 0 else None), includes_activity_data


def _stored_same_day_session_minutes(source_name: str, user_id: str, date_str: str, exclude_activity_id: str | None = None) -> int | None:
    total = 0
    found = False
    for activity_dir in _activity_dir_candidates(source_name, user_id):
        for path in activity_dir.glob('*.json'):
            if '.bak_' in path.name:
                continue
            try:
                payload = json.loads(path.read_text(encoding='utf-8'))
            except Exception:
                continue
            meta = payload.get('metadata') or {}
            if meta.get('activity_date') != date_str:
                continue
            record_key = meta.get('record_key')
            if exclude_activity_id and str(record_key) == str(exclude_activity_id):
                continue
            raw_data = payload.get('raw_data') or {}
            summary = raw_data.get('activity_summary') if isinstance(raw_data, dict) else None
            if not isinstance(summary, dict) or not summary:
                continue
            session_minutes = derive_session_intensity_minutes(summary)
            if session_minutes:
                total += session_minutes
                found = True
    return total if found else None


def derive_weekly_intensity_minutes(source_name: str, user_id: str, date_str: str, session_minutes: int | None, current_activity_id: str | None = None) -> int | None:
    week_start = _week_start(date_str)
    current_total, current_includes_activity = _extract_daily_weekly_total(user_id, date_str)

    prior_total = None
    daily_dir = DATA_DIR / user_id / 'daily'
    if daily_dir.exists():
        for day_path in sorted(daily_dir.glob('*.json'), reverse=True):
            day_str = day_path.stem
            if day_str >= date_str:
                continue
            try:
                if _week_start(day_str) != week_start:
                    continue
            except Exception:
                continue
            weekly_total, _includes_activity = _extract_daily_weekly_total(user_id, day_str)
            if weekly_total:
                prior_total = weekly_total
                break

    same_day_stored_total = _stored_same_day_session_minutes(source_name, user_id, date_str, exclude_activity_id=current_activity_id)

    candidates: list[int] = []
    if current_includes_activity and current_total:
        candidates.append(current_total)

    carry_in_total = (prior_total or 0)
    if not current_includes_activity and current_total:
        carry_in_total = max(carry_in_total, current_total)

    derived_total = carry_in_total + (same_day_stored_total or 0) + (session_minutes or 0)
    if derived_total > 0:
        candidates.append(derived_total)

    if not candidates and current_total and not session_minutes and not same_day_stored_total:
        candidates.append(current_total)

    return max(candidates) if candidates else None


def build_who_target_context(weekly_intensity_minutes: int | None) -> dict[str, Any]:
    if weekly_intensity_minutes is None:
        return {
            'weekly_intensity_minutes': None,
            'who_target': WHO_WEEKLY_TARGET_MINUTES,
            'who_completion_pct': None,
            'who_gap_minutes': None,
            'who_should_mention': False,
        }

    total_minutes = int(round(float(weekly_intensity_minutes)))
    who_gap_minutes = max(WHO_WEEKLY_TARGET_MINUTES - total_minutes, 0)
    return {
        'weekly_intensity_minutes': total_minutes,
        'who_target': WHO_WEEKLY_TARGET_MINUTES,
        'who_completion_pct': round(total_minutes / WHO_WEEKLY_TARGET_MINUTES * 100) if total_minutes else None,
        'who_gap_minutes': who_gap_minutes,
        'who_should_mention': who_gap_minutes > 0,
    }


def _format_progress_number(value: float | int | None) -> float | int | None:
    if value is None:
        return None
    rounded = round(float(value), 2)
    if abs(rounded - round(rounded)) < 1e-9:
        return int(round(rounded))
    return rounded


def _first_metric_value(*values: Any) -> float | None:
    for value in values:
        numeric = _to_float(value)
        if numeric is not None:
            return numeric
    return None


def build_performance_highlights(
    today_metrics: dict[str, Any] | None,
    yesterday_metrics: dict[str, Any] | None,
    activity_summary: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    today_metrics = today_metrics or {}
    yesterday_metrics = yesterday_metrics or {}
    activity_summary = activity_summary or {}
    highlights: list[dict[str, Any]] = []

    current_vo2 = _first_metric_value(
        activity_summary.get('vO2MaxValue'),
        activity_summary.get('vo2MaxValue'),
        today_metrics.get('vo2max'),
    )
    previous_vo2 = _first_metric_value(yesterday_metrics.get('vo2max'))
    if current_vo2 is not None and previous_vo2 is not None and current_vo2 > previous_vo2:
        highlights.append(
            {
                'metric': 'vo2max',
                'label': 'VO2 Max',
                'previous': _format_progress_number(previous_vo2),
                'current': _format_progress_number(current_vo2),
                'delta': _format_progress_number(current_vo2 - previous_vo2),
                'summary': f"VO2 Max 从{_format_progress_number(previous_vo2)}提升到{_format_progress_number(current_vo2)}",
            }
        )

    current_ftp = _first_metric_value(
        activity_summary.get('functionalThresholdPower'),
        activity_summary.get('ftp'),
        today_metrics.get('cycling_ftp_value'),
    )
    previous_ftp = _first_metric_value(yesterday_metrics.get('cycling_ftp_value'))
    if current_ftp is not None and previous_ftp is not None and current_ftp > previous_ftp:
        highlights.append(
            {
                'metric': 'cycling_ftp',
                'label': 'FTP',
                'previous': _format_progress_number(previous_ftp),
                'current': _format_progress_number(current_ftp),
                'delta': _format_progress_number(current_ftp - previous_ftp),
                'summary': f"FTP 从{_format_progress_number(previous_ftp)}提升到{_format_progress_number(current_ftp)}",
            }
        )

    return highlights

def bj_now() -> datetime:
    return datetime.now(BJ_TZ)


def resolve_user_id(source_name: str) -> str | None:
    if not source_name:
        return None
    return SOURCE_TO_USER_ID.get(source_name, source_name)


def user_data_dir(user_id: str) -> Path:
    path = DATA_DIR / user_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def latest_analysis_path(user_id: str) -> Path:
    return user_data_dir(user_id) / 'latest_analysis.json'


def save_latest_analysis(user_id: str, analysis_result: dict[str, Any]) -> Path:
    path = latest_analysis_path(user_id)
    path.write_text(json.dumps(analysis_result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return path


def load_latest_analysis(user_id: str, date_str: str | None = None) -> dict[str, Any] | None:
    path = latest_analysis_path(user_id)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None
    if date_str and payload.get('date') != date_str:
        return None
    return payload


def _soften_extreme_value_phrase(text: Any) -> Any:
    if not isinstance(text, str):
        return text

    def replace(match: re.Match[str]) -> str:
        count, unit, metric, direction = match.groups()
        level = '高位' if direction == '最高' else '低位'
        return f"{metric}处在近{count}{unit}{level}"

    return re.sub(r'^近(\d+)(天|周|月)(.+?)(最高|最低)$', replace, text)


def _soften_extreme_value_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    softened: list[dict[str, Any]] = []
    for finding in findings:
        item = deepcopy(finding)
        if item.get('type') == 'extreme_value':
            item['title'] = _soften_extreme_value_phrase(item.get('title'))
            item['description'] = _soften_extreme_value_phrase(item.get('description'))
            detail = item.get('detail')
            if isinstance(detail, dict):
                item['detail'] = {
                    key: _soften_extreme_value_phrase(value) if key == 'description' else value
                    for key, value in detail.items()
                }
        softened.append(item)
    return softened


def build_morning_prompt(analysis_result: dict[str, Any]) -> str:
    payload = deepcopy(analysis_result)
    return (
        f"以下是 {payload['user_name']} 在 {payload['date']} 的健康数据分析结果。"
        f"请根据系统 Prompt 的要求，为这位用户写一条晨报推送。\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def build_evening_prompt(user_name: str, date_str: str, triggers: list[dict[str, Any]]) -> str:
    return (
        f"以下是 {user_name} 在 {date_str} 晚间的推送数据。请根据系统 Prompt 的要求写一条晚间提醒。\n\n"
        f"触发原因：\n{json.dumps(triggers, ensure_ascii=False, indent=2)}"
    )


def call_custom_llm(system_prompt: str, user_prompt: str, push_type: str = 'morning', activity_type: str | None = None) -> str:
    full_system_prompt, _knowledge_meta = build_system_prompt(push_type, activity_type=activity_type, base_prompt=system_prompt)
    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {'role': 'system', 'content': full_system_prompt},
            {'role': 'user', 'content': user_prompt},
        ],
    )
    return clean_output(response.choices[0].message.content)


def generate_new_morning_report(api: Any, source_name: str, display_name: str, date_str: str) -> dict[str, Any]:
    user_id = resolve_user_id(source_name)
    if not user_id:
        raise KeyError(f'未找到映射 user_id: {source_name}')

    import daily_snapshot

    daily_snapshot.save_daily_snapshot(api, user_id, date_str)
    baselines = baseline_module.compute_baselines(user_id)
    analysis_result = analysis.analyze(user_id, date_str)
    if display_name:
        analysis_result['user_name'] = display_name
    save_latest_analysis(user_id, analysis_result)

    user_prompt = build_morning_prompt(analysis_result)
    message = call_custom_llm(NEW_MORNING_SYSTEM_PROMPT, user_prompt, push_type='morning')
    llm_payload = {
        'message_type': 'sleep_morning_v2',
        'user_id': user_id,
        'date': date_str,
        'system_prompt': NEW_MORNING_SYSTEM_PROMPT,
        'user_prompt': user_prompt,
        'analysis_result': analysis_result,
        'baselines_days_of_data': baselines.get('days_of_data'),
    }
    return {
        'user_id': user_id,
        'message': message,
        'analysis_result': analysis_result,
        'llm_payload': llm_payload,
    }


def enrich_activity_payload(llm_payload: dict[str, Any], source_name: str, date_str: str, activity_summary: dict[str, Any] | None = None, activity_id: str | None = None) -> dict[str, Any]:
    user_id = resolve_user_id(source_name)
    if not user_id:
        return llm_payload

    analysis_result = analysis.analyze(user_id, date_str)
    today_metrics = analysis_result.get('today_metrics') or {}
    current_activity_summary = activity_summary or load_latest_activity_summary(source_name, user_id, date_str)
    session_intensity_minutes = derive_session_intensity_minutes(current_activity_summary)
    weekly_intensity_minutes = derive_weekly_intensity_minutes(
        source_name,
        user_id,
        date_str,
        session_intensity_minutes,
        current_activity_id=activity_id,
    )
    if weekly_intensity_minutes in (None, 0):
        weekly_intensity_minutes = None
    who_target_context = build_who_target_context(weekly_intensity_minutes)
    performance_highlights = build_performance_highlights(
        today_metrics=today_metrics,
        yesterday_metrics=analysis_result.get('yesterday_metrics') or {},
        activity_summary=current_activity_summary,
    )
    extra_training_data = {
        'training_status': analysis_result.get('training_status'),
        'vo2max': today_metrics.get('vo2max'),
        'fitness_age': today_metrics.get('fitness_age'),
        'endurance_score': today_metrics.get('endurance_score_value'),
        'hill_score': today_metrics.get('hill_score_value'),
        'cycling_ftp': today_metrics.get('cycling_ftp_value'),
        'session_intensity_minutes': session_intensity_minutes,
        'performance_highlights': performance_highlights,
        **who_target_context,
        'anomaly_alert': (analysis_result.get('anomaly_detection', {}).get('signals_count', 0) >= 3),
    }
    enriched = deepcopy(llm_payload)
    known_activity_issues = extract_known_activity_issues(user_id)
    repeat_rules = set(known_activity_issues.get('repeat_rules') or [])
    if repeat_rules:
        filtered_priority = []
        suppressed_titles = set()
        suppressed_rules = set()
        for item in enriched.get('priority_issues') or []:
            rule = item.get('rule')
            severity = item.get('severity') or 0
            if rule in repeat_rules and severity < 5:
                suppressed_rules.add(rule)
                title = item.get('title')
                if title:
                    suppressed_titles.add(title)
                continue
            filtered_priority.append(item)
        enriched['priority_issues'] = filtered_priority
        enriched['secondary_issues'] = [
            item for item in (enriched.get('secondary_issues') or [])
            if item.get('rule') not in repeat_rules
        ]
        enriched['forced_alerts'] = [
            item for item in (enriched.get('forced_alerts') or [])
            if item.get('rule') not in repeat_rules or (item.get('severity') or 0) >= 5
        ]
        enriched['must_mention'] = [
            item for item in (enriched.get('must_mention') or [])
            if item not in suppressed_titles
        ]
        if suppressed_rules:
            if any(rule in suppressed_rules for rule in ['left_right_balance_off', 'hr_power_relation_anomaly']):
                enriched['issue_point'] = ''
            if 'cadence_low' in suppressed_rules:
                enriched['secondary_issue_point'] = ''
            continuity_context = dict(enriched.get('continuity_context') or {})
            recent_memory = continuity_context.get('recent_issue_memory') or []
            blocked_tokens = ['左右', '踏频', '心率和功率']
            continuity_context['recent_issue_memory'] = [
                item for item in recent_memory
                if not any(token in str(item) for token in blocked_tokens)
            ]
            enriched['continuity_context'] = continuity_context
            cycling_summary = dict(enriched.get('cycling_specific_summary') or {})
            if 'left_right_balance_off' in suppressed_rules:
                cycling_summary.pop('left_right_balance_note', None)
            if 'cadence_low' in suppressed_rules:
                cycling_summary.pop('cadence_note', None)
            enriched['cycling_specific_summary'] = cycling_summary
        enriched['suppressed_known_issues'] = sorted(repeat_rules)
    enriched['extra_training_data'] = extra_training_data
    enriched['performance_priority_point'] = performance_highlights[0]['summary'] if performance_highlights else None
    enriched['known_activity_issues'] = known_activity_issues
    enriched['activity_analysis_context'] = {
        'user_id': user_id,
        'activity_date': date_str,
        'latest_analysis_date': analysis_result.get('date'),
    }
    log_activity_llm_payload(user_id, date_str, enriched)
    return enriched


def fetch_today_activities(api: Any, date_str: str) -> list[dict[str, Any]]:
    activities = api.get_activities(0, 10) or []
    results = []
    for activity in activities:
        start = activity.get('startTimeLocal') or activity.get('startTimeGMT') or ''
        if isinstance(start, str) and start[:10] == date_str:
            results.append(activity)
    return results


def extract_current_body_battery(body_battery_payload: Any) -> int | None:
    if not isinstance(body_battery_payload, list) or not body_battery_payload:
        return None
    values = body_battery_payload[0].get('bodyBatteryValuesArray')
    if not isinstance(values, list) or not values:
        return None
    valid = [item[1] for item in values if isinstance(item, list) and len(item) >= 2 and item[1] is not None]
    if not valid:
        return None
    return int(valid[-1])


def calculate_high_stress_percentage(stress_data: dict[str, Any]) -> float | None:
    values = stress_data.get('stressValuesArray')
    if isinstance(values, list) and values:
        valid = [item[1] for item in values if isinstance(item, list) and len(item) >= 2 and isinstance(item[1], (int, float)) and item[1] >= 0]
        if valid:
            high = [value for value in valid if value > 50]
            return len(high) / len(valid)
    avg_level = stress_data.get('avgStressLevel')
    if isinstance(avg_level, (int, float)):
        return 1.0 if avg_level > 50 else 0.0
    return None


def check_evening_triggers(api: Any, source_name: str, date_str: str) -> list[dict[str, Any]]:
    user_id = resolve_user_id(source_name)
    if not user_id:
        return []

    triggers: list[dict[str, Any]] = []
    today_data = load_evening_daily_data(api, source_name, user_id, date_str)
    baselines = analysis.load_baselines(user_id)
    if not today_data:
        return triggers

    today_metrics = analysis.extract_key_metrics(today_data) or {}
    daily_steps = today_metrics.get('daily_steps')
    steps_baseline = ((baselines.get('metrics') or {}).get('daily_steps') or {}).get('mean')
    meaningful_activity_signal = None
    try:
        meaningful_activity_signal = detect_meaningful_same_day_activity(api, source_name, user_id, date_str)
    except Exception:
        meaningful_activity_signal = None
    if daily_steps is not None and daily_steps < 3000:
        if meaningful_activity_signal:
            _log_low_activity_skip(source_name, date_str, daily_steps, meaningful_activity_signal)
        else:
            triggers.append({'type': 'low_activity', 'data': {'steps': daily_steps, 'baseline': steps_baseline}})

    try:
        for act in fetch_today_activities(api, date_str):
            tss = act.get('trainingStressScore') or act.get('activityTrainingLoad') or 0
            if tss and float(tss) > 150:
                triggers.append({'type': 'recovery_reminder', 'data': {'activity': '今天有高强度训练'}})
                break
    except Exception:
        pass

    try:
        current_bb = extract_current_body_battery(api.get_body_battery(date_str))
        if current_bb is not None and current_bb < 15:
            triggers.append({'type': 'extreme_fatigue', 'data': {'current_bb': current_bb}})
    except Exception:
        pass

    try:
        stress_data = None
        if hasattr(api, 'get_all_day_stress'):
            stress_data = api.get_all_day_stress(date_str)
        if not stress_data and hasattr(api, 'get_stress_data'):
            stress_data = api.get_stress_data(date_str)
        if isinstance(stress_data, dict):
            high_stress_pct = calculate_high_stress_percentage(stress_data)
            if high_stress_pct is not None and high_stress_pct > 0.5:
                triggers.append({'type': 'high_stress_day', 'data': {'high_stress_percentage': round(high_stress_pct * 100)}})
    except Exception:
        pass

    morning_analysis = load_latest_analysis(user_id, date_str=date_str)
    if morning_analysis:
        signals_count = (morning_analysis.get('anomaly_detection') or {}).get('signals_count', 0)
        if signals_count >= 3:
            triggers.append({'type': 'anomaly_followup', 'data': {'morning_signals': signals_count}})

    return triggers


def generate_evening_report(user_name: str, date_str: str, triggers: list[dict[str, Any]]) -> str:
    prompt = build_evening_prompt(user_name, date_str, triggers)
    return call_custom_llm(EVENING_SYSTEM_PROMPT, prompt, push_type='evening')


def in_evening_window(now: datetime | None = None) -> bool:
    current = now or bj_now()
    total = current.hour * 60 + current.minute
    return 18 * 60 + 45 <= total <= 19 * 60 + 15
