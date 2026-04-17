from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path('/root/garmin_assistant')
KNOWLEDGE_DIR = ROOT / 'knowledge'
LOG_DIR = ROOT / 'logs'
KNOWLEDGE_LOG_PATH = LOG_DIR / 'knowledge_prompt_usage.jsonl'
BJ_TZ = timezone(timedelta(hours=8))

CYCLING_KEYWORDS = [
    'cycling', 'biking', 'indoor_cycling', 'virtual_ride', 'road_biking',
    'mountain_biking', 'gravel_cycling', 'e_bike', 'recumbent_cycling',
    '公路骑行', '骑行', '室内骑行', '山地骑行', '单车', 'bike'
]
TRAIL_RUNNING_KEYWORDS = [
    'trail_running', 'mountain_running', '越野跑', '山地跑', 'trail run', 'trailrunning'
]
RUNNING_KEYWORDS = [
    'running', 'treadmill_running', 'track_running', 'indoor_running',
    '跑步', '跑步机', '操场跑', 'run'
]
SWIMMING_KEYWORDS = [
    'lap_swimming', 'open_water_swimming', 'pool_swimming', 'swimming',
    '泳池游泳', '公开水域游泳', '游泳', 'swim'
]
WALKING_KEYWORDS = [
    'walking', 'hiking', 'casual_walking', 'speed_walking', 'fitness_walking',
    '步行', '快走', '徒步', '远足', 'walk', 'hike'
]

ACTIVITY_SLEEP_SECTIONS = ['训练状态的解读', 'VO2 Max 的解读', '训练准备度的解读']
OTHER_ACTIVITY_SECTIONS = [
    'HRV（心率变异性）的解读',
    '静息心率的解读',
    '呼吸频率的解读',
    'Body Battery 的解读',
    '压力数据的解读',
    'VO2 Max 的解读',
    '训练状态的解读',
    '训练准备度的解读',
]

SECTION_RE = re.compile(r'(^##\s+[^\n]+.*?)(?=^##\s+|\Z)', re.MULTILINE | re.DOTALL)


def load_knowledge(filename: str) -> str:
    path = KNOWLEDGE_DIR / filename
    return path.read_text(encoding='utf-8')


def load_knowledge_section(filename: str, section_titles: list[str]) -> str:
    content = load_knowledge(filename)
    chunks: list[str] = []
    for match in SECTION_RE.finditer(content):
        block = match.group(1).strip()
        header = block.splitlines()[0]
        if any(title in header for title in section_titles):
            chunks.append(block)
    return '\n\n'.join(chunks)


def _collect_text_candidates(activity_data: Any) -> list[str]:
    candidates: list[str] = []
    if isinstance(activity_data, str):
        candidates.append(activity_data)
    if isinstance(activity_data, dict):
        for key in ['sport_type', 'activity_name', 'activity_type', 'typeKey', 'activityTypeName', 'message_type']:
            value = activity_data.get(key)
            if isinstance(value, str) and value:
                candidates.append(value)
        nested = activity_data.get('activityType')
        if isinstance(nested, dict):
            for key in ['typeKey', 'typeName', 'parentTypeId']:
                value = nested.get(key)
                if isinstance(value, str) and value:
                    candidates.append(value)
    return candidates


def get_activity_category(activity_data: Any) -> str:
    lowered = ' | '.join(_collect_text_candidates(activity_data)).lower()
    for kw in CYCLING_KEYWORDS:
        if kw.lower() in lowered:
            return 'cycling'
    for kw in TRAIL_RUNNING_KEYWORDS:
        if kw.lower() in lowered:
            return 'trail_running'
    for kw in RUNNING_KEYWORDS:
        if kw.lower() in lowered:
            return 'running'
    for kw in SWIMMING_KEYWORDS:
        if kw.lower() in lowered:
            return 'swimming'
    for kw in WALKING_KEYWORDS:
        if kw.lower() in lowered:
            return 'walking'
    return 'other'


def _estimate_tokens(*parts: str) -> int:
    text = ''.join(part for part in parts if part)
    if not text:
        return 0
    return max(1, len(text) // 2)


def record_prompt_usage(push_type: str, base_prompt: str, knowledge: str, activity_type: str | None, category: str | None, knowledge_files: list[str]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        'ts': datetime.now(BJ_TZ).isoformat(),
        'push_type': push_type,
        'activity_type': activity_type,
        'category': category,
        'knowledge_files': knowledge_files,
        'base_prompt_chars': len(base_prompt or ''),
        'knowledge_chars': len(knowledge or ''),
        'estimated_tokens': _estimate_tokens(base_prompt or '', knowledge or ''),
    }
    with KNOWLEDGE_LOG_PATH.open('a', encoding='utf-8') as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + '\n')


def build_system_prompt(push_type: str, activity_type: str | None = None, base_prompt: str = '') -> tuple[str, dict[str, Any]]:
    knowledge = ''
    knowledge_files: list[str] = []
    category = None

    if push_type in {'morning', 'evening', 'weekly', 'monthly', 'initial_7d', 'initial_30d'}:
        knowledge = load_knowledge('sleep_health.md')
        knowledge_files = ['sleep_health.md']
    elif push_type == 'activity':
        category = get_activity_category(activity_type or '')
        if category == 'cycling':
            primary = load_knowledge('cycling.md')
            secondary = load_knowledge_section('sleep_health.md', ACTIVITY_SLEEP_SECTIONS)
            knowledge = primary + '\n\n' + secondary
            knowledge_files = ['cycling.md', 'sleep_health.md#训练状态+VO2Max+训练准备度']
        elif category == 'trail_running':
            primary = load_knowledge('trail_running.md')
            secondary = load_knowledge_section('sleep_health.md', ACTIVITY_SLEEP_SECTIONS)
            knowledge = primary + '\n\n' + secondary
            knowledge_files = ['trail_running.md', 'sleep_health.md#训练状态+VO2Max+训练准备度']
        elif category == 'running':
            primary = load_knowledge('running.md')
            secondary = load_knowledge_section('sleep_health.md', ACTIVITY_SLEEP_SECTIONS)
            knowledge = primary + '\n\n' + secondary
            knowledge_files = ['running.md', 'sleep_health.md#训练状态+VO2Max+训练准备度']
        elif category == 'swimming':
            primary = load_knowledge('swimming.md')
            secondary = load_knowledge_section('sleep_health.md', ACTIVITY_SLEEP_SECTIONS)
            knowledge = primary + '\n\n' + secondary
            knowledge_files = ['swimming.md', 'sleep_health.md#训练状态+VO2Max+训练准备度']
        elif category == 'walking':
            primary = load_knowledge('walking.md')
            secondary = load_knowledge_section('sleep_health.md', ACTIVITY_SLEEP_SECTIONS)
            knowledge = primary + '\n\n' + secondary
            knowledge_files = ['walking.md', 'sleep_health.md#训练状态+VO2Max+训练准备度']
        else:
            knowledge = load_knowledge_section('sleep_health.md', OTHER_ACTIVITY_SECTIONS)
            knowledge_files = ['sleep_health.md#通用健康部分']
    else:
        knowledge = load_knowledge('sleep_health.md')
        knowledge_files = ['sleep_health.md']

    full_prompt = (base_prompt or '').strip()
    if knowledge.strip():
        full_prompt = (
            f"{full_prompt}\n\n## 专业知识参考\n\n"
            f"以下是你在分析数据时必须参考的专业知识。所有数据解读必须基于这些知识，不得自行编造判断标准。\n\n"
            f"{knowledge.strip()}"
        )

    meta = {
        'push_type': push_type,
        'activity_type': activity_type,
        'category': category,
        'knowledge_files': knowledge_files,
        'estimated_tokens': _estimate_tokens(base_prompt or '', knowledge or ''),
    }
    record_prompt_usage(push_type, base_prompt, knowledge, activity_type, category, knowledge_files)
    return full_prompt, meta
