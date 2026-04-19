#!/usr/bin/env python3
import sys
from pathlib import Path

APP_DIR = Path("/root/garmin_assistant/app")
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import report_flow

assert report_flow.resolve_user_id('丛至') == 'congzhi'
assert report_flow.resolve_user_id('RegressionUser') == 'RegressionUser'
print('ok')
