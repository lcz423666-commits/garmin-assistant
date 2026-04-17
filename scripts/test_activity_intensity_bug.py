#!/usr/bin/env python3
import sys
sys.path.insert(0, '/root/garmin_assistant')
from app import report_flow


def main() -> int:
    summary = report_flow.load_latest_activity_summary('丛至', 'congzhi', '2026-03-31')
    session = report_flow.derive_session_intensity_minutes(summary)
    assert session == 176, f'session expected 176, got {session}'

    weekly = report_flow.derive_weekly_intensity_minutes('丛至', 'congzhi', '2026-03-31', session, current_activity_id='22355774430')
    assert weekly == 216, f'weekly expected 216, got {weekly}'
    print('ok', session, weekly)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
