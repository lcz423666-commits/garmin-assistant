#!/usr/bin/env python3
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/root/garmin_assistant/app")
sys.path.insert(0, "/root/garmin_assistant/scripts")

import garmin_monitor as gm
import add_user_and_onboard as add_user


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        rate_file = Path(tmp) / "garmin_rate_limit.json"
        gm.RATE_LIMIT_STATE_FILE = str(rate_file)
        gm._rate_limit_backoff = {}
        gm.record_rate_limit_backoff("WangZZ", now_ts=1000.0)
        until = gm.get_rate_limit_backoff_until("WangZZ", now_ts=1001.0)
        assert until is not None and int(until) == 8200, until

        user = {"name": "WangZZ"}
        blocked = add_user.check_rate_limit_before_onboarding(user, now_ts=1001.0)
        assert blocked is not None, blocked
        assert "429" in blocked or "限流" in blocked, blocked

        gm.clear_rate_limit_backoff("WangZZ")
        assert gm.get_rate_limit_backoff_until("WangZZ", now_ts=1001.0) is not None
        assert add_user.check_rate_limit_before_onboarding(user, now_ts=1001.0) is not None
        assert gm.get_rate_limit_backoff_until("WangZZ", now_ts=8201.0) is None
        assert add_user.check_rate_limit_before_onboarding(user, now_ts=8201.0) is None
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
