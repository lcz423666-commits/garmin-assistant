#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

with tempfile.TemporaryDirectory() as config_root:
    root = Path(config_root)
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "system.json").write_text(
        json.dumps(
            {
                "monitor": {
                    "state_dir": str(root / "state"),
                    "pid_file": str(root / "state" / "garmin_monitor.pid"),
                    "token_base_dir": str(root / "tokens"),
                },
                "storage": {"data_root": str(root / "data")},
                "feishu": {},
                "pushplus": {},
                "llm": {},
            }
        ),
        encoding="utf-8",
    )
    (root / "config" / "users.json").write_text('{"users":[]}', encoding="utf-8")
    os.environ["GARMIN_ASSISTANT_ROOT"] = str(root)
    import garmin_monitor as gm  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        runtime_file = Path(tmp) / "health_runtime.json"
        gm.HEALTH_RUNTIME_FILE = str(runtime_file)
        runtime_file.write_text(
            json.dumps(
                {
                    "updated_at": None,
                    "cycle_started_at": None,
                    "cycle_finished_at": None,
                    "process": {},
                    "enabled_users": ["active", "other"],
                    "loaded_users": ["active", "other"],
                    "per_user": {
                        "active": {"last_cycle_error": "old"},
                        "stale": {"last_cycle_error": "removed"},
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        gm.update_health_runtime(
            root_updates={
                "enabled_users": ["active"],
                "loaded_users": ["active"],
            },
            user_updates={"active": {"last_cycle_error": None}},
        )

        payload = json.loads(runtime_file.read_text(encoding="utf-8"))
        assert set(payload["per_user"].keys()) == {"active"}, payload["per_user"]
        assert payload["per_user"]["active"]["last_cycle_error"] is None, payload["per_user"]
        assert payload["enabled_users"] == ["active"], payload
        assert payload["loaded_users"] == ["active"], payload
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
