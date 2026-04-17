#!/usr/bin/env python3
import json
import sys

sys.path.insert(0, '/root/garmin_assistant')

from analysis import analyze

for user_id in ['congzhi', 'yang', 'kevin']:
    print(f'\n{"=" * 60}')
    print(f'用户：{user_id}')
    print(f'{"=" * 60}')

    result = analyze(user_id, '2026-03-28')

    print(f'\n异常信号数：{result["anomaly_detection"]["signals_count"]}')
    for s in result['anomaly_detection']['signals']:
        baseline = s.get('baseline_mean', s.get('baseline_7day_mean', 'N/A'))
        print(f'  {s["signal"]}: 当前={s["current_value"]}, 基线={baseline}')

    print('\n趋势：')
    for metric, trend in result.get('trends', {}).items():
        print(f'  {metric}: 3天={trend.get("direction_3d", "?")}, 7天={trend.get("direction_7d", "?")}')

    print('\n今日发现（按优先级）：')
    for finding in result.get('notable_findings', []):
        print(f'  [{finding["priority"]}] {finding["title"]}: {finding["description"]}')

    bo = result.get('blood_oxygen', {})
    print(f'\n血氧状态：{bo.get("status", "?")}, 今日={bo.get("min_spo2_today", "?")}, 需要提及={bo.get("should_mention", "?")}')

    with open(f'/root/garmin_assistant/data/{user_id}/analysis_test.json', 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print('\n完整结果已保存到 analysis_test.json')
