#!/usr/bin/env python3
import json
import sys
import time
from pathlib import Path

ROOT = Path('/root/garmin_assistant')
APP = ROOT / 'app'
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(APP))

import analysis
from llm_helper import analyze_with_llm
from report_flow import build_morning_prompt, call_custom_llm, enrich_activity_payload, NEW_MORNING_SYSTEM_PROMPT

USERS = [
    {"user_id": "congzhi", "source_name": "丛至", "label": "丛至"},
    {"user_id": "yang", "source_name": "杨", "label": "杨"},
    {"user_id": "kevin", "source_name": "Kevin", "label": "Kevin"},
]


def load_profile_name(user_id: str, fallback: str) -> str:
    p = ROOT / 'data' / user_id / 'profile.json'
    if not p.exists():
        return fallback
    try:
        data = json.loads(p.read_text())
    except Exception:
        return fallback
    return data.get('display_name') or fallback


def find_latest_activity(user):
    candidates = [
        ROOT / 'data' / user['source_name'] / 'activity',
        ROOT / 'data' / load_profile_name(user['user_id'], user['source_name']) / 'activity',
        ROOT / 'data' / user['user_id'] / 'activity',
    ]
    latest_obj = None
    latest_meta = None
    latest_path = None
    latest_recorded = ''
    seen = set()
    for activity_dir in candidates:
        if str(activity_dir) in seen or not activity_dir.exists():
            continue
        seen.add(str(activity_dir))
        for path in activity_dir.glob('*.json'):
            if '.bak_' in path.name:
                continue
            try:
                payload = json.loads(path.read_text())
            except Exception:
                continue
            meta = payload.get('metadata') or {}
            recorded_at = meta.get('recorded_at') or meta.get('saved_at') or ''
            if recorded_at >= latest_recorded:
                latest_recorded = recorded_at
                latest_path = path
                latest_obj = payload
                latest_meta = meta
    return latest_path, latest_obj, latest_meta


results = []
for user in USERS:
    daily_dir = ROOT / 'data' / user['user_id'] / 'daily'
    daily_files = sorted([p.stem for p in daily_dir.glob('*.json')])
    recent_dates = daily_files[-3:]
    for date_str in recent_dates:
        try:
            analysis_result = analysis.analyze(user['user_id'], date_str)
            analysis_result['user_name'] = load_profile_name(user['user_id'], user['label'])
            prompt = build_morning_prompt(analysis_result)
            response = call_custom_llm(NEW_MORNING_SYSTEM_PROMPT, prompt, push_type='morning')
            top = (analysis_result.get('notable_findings') or [{}])[0]
            results.append({
                'user': user['label'],
                'user_id': user['user_id'],
                'type': 'morning',
                'date': date_str,
                'top_finding': top.get('title', '无') if isinstance(top, dict) else '无',
                'content': response,
            })
        except Exception as exc:
            results.append({
                'user': user['label'],
                'user_id': user['user_id'],
                'type': 'morning',
                'date': date_str,
                'top_finding': 'ERROR',
                'content': f'ERROR: {exc}',
            })
        time.sleep(2)

    try:
        path, payload, meta = find_latest_activity(user)
        if payload and meta:
            enriched = enrich_activity_payload(payload.get('llm_payload') or {}, user['source_name'], meta['activity_date'])
            response = analyze_with_llm(enriched, mode='activity')
            results.append({
                'user': user['label'],
                'user_id': user['user_id'],
                'type': 'activity',
                'date': meta['activity_date'],
                'activity_name': meta.get('activity_name'),
                'content': response,
            })
        else:
            results.append({
                'user': user['label'],
                'user_id': user['user_id'],
                'type': 'activity',
                'date': None,
                'activity_name': None,
                'content': 'ERROR: no recent activity found',
            })
    except Exception as exc:
        results.append({
            'user': user['label'],
            'user_id': user['user_id'],
            'type': 'activity',
            'date': None,
            'activity_name': None,
            'content': f'ERROR: {exc}',
        })
    time.sleep(2)

out_path = ROOT / 'data' / 'batch_test_output.json'
out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding='utf-8')
print(out_path)
for item in results:
    print('=' * 70)
    print(item['user'], item['type'], item.get('date'), item.get('activity_name', ''))
    print('top_finding:', item.get('top_finding'))
    print(item['content'])
