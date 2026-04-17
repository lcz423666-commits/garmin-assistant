#!/usr/bin/env python3
import sys

sys.path.insert(0, '/root/garmin_assistant/app')

from report_flow import generate_evening_report

SCENARIOS = [
    (
        '低活动',
        '李丛至',
        '2026-03-30',
        [
            {'type': 'low_activity', 'data': {'steps': 1800, 'baseline': 6500}},
        ],
    ),
    (
        '高强度训练',
        '一只自行车',
        '2026-03-30',
        [
            {'type': 'recovery_reminder', 'data': {'activity': '今天有高强度骑行训练'}},
        ],
    ),
    (
        '预警跟进',
        '李丛至',
        '2026-03-30',
        [
            {'type': 'anomaly_followup', 'data': {'morning_signals': 4}},
        ],
    ),
]

for name, user_name, date_str, triggers in SCENARIOS:
    print('\n' + '=' * 40)
    print(f'场景：{name}')
    print('=' * 40)
    print(generate_evening_report(user_name, date_str, triggers))
