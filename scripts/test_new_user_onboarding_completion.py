#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

state_path = Path('/root/garmin_assistant/state/.garmin_monitor_state_WangZZ.json')
baseline_path = Path('/root/garmin_assistant/data/WangZZ/baselines.json')
profile_path = Path('/root/garmin_assistant/data/WangZZ/profile.json')

a = json.loads(state_path.read_text())
b = json.loads(baseline_path.read_text())
p = json.loads(profile_path.read_text())

assert a.get('initial_daily_backfill_completed_at'), a
assert (a.get('initial_daily_backfill_days') or 0) >= 7, a
assert (b.get('days_of_data') or 0) >= 7, b
assert isinstance(p.get('available_data'), dict) and p.get('available_data'), p
print('ok')
