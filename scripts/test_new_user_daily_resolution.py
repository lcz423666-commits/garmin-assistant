#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path('/root/garmin_assistant')
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / 'app') not in sys.path:
    sys.path.insert(0, str(ROOT / 'app'))

import daily_snapshot  # noqa: E402

user = daily_snapshot.resolve_user('WangZZ')
assert user['name'] == 'WangZZ', user
print('ok')
