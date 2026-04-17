#!/usr/bin/env python3
from __future__ import annotations

import sys
import time
from datetime import timedelta
from pathlib import Path

ROOT = Path('/root/garmin_assistant')
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / 'app') not in sys.path:
    sys.path.insert(0, str(ROOT / 'app'))

import garmin_monitor as gm  # noqa: E402
from baseline import compute_baselines  # noqa: E402
from daily_snapshot import bj_now, get_daily_path, save_daily_snapshot  # noqa: E402
from user_identity import list_enabled_user_ids, resolve_source_name, resolve_user_by_user_id  # noqa: E402


def resolve_user(user_id: str):
    return resolve_user_by_user_id(user_id)


def main() -> int:
    today = bj_now().date()
    for user_id in list_enabled_user_ids():
        display_name = resolve_source_name(user_id) or user_id
        user = resolve_user(user_id)
        api = gm.login_garmin(user)
        for day in range(30, 0, -1):
            date_str = (today - timedelta(days=day)).isoformat()
            filepath = get_daily_path(user_id, date_str)
            if filepath.exists():
                print(f'  [{display_name}] 跳过 {date_str}（已存在）', flush=True)
                continue
            save_daily_snapshot(api, user_id, date_str)
            completed = 31 - day
            print(f'  [{display_name}] {completed}/30 完成 {date_str}', flush=True)
            time.sleep(3)
        compute_baselines(user_id)
        print(f'{display_name} 回填完成', flush=True)
        time.sleep(30)
    print('全部用户回填与基线计算完成', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
