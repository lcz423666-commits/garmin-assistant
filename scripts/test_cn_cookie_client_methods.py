#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path('/root/garmin_assistant')
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / 'app') not in sys.path:
    sys.path.insert(0, str(ROOT / 'app'))

from garmin_cookie_client import GarminCookieClient  # noqa: E402

required = [
    'connectapi',
    'get_body_battery_events',
    'get_steps_data',
    'get_intensity_minutes_data',
    'get_training_status',
    'get_stress_data',
    'get_all_day_stress',
    'get_heart_rates',
    'get_spo2_data',
    'get_rhr_day',
]
missing = [name for name in required if not hasattr(GarminCookieClient, name)]
assert not missing, missing
print('ok')
