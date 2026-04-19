#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path


ROOT = Path("/root/garmin_assistant")
SCRIPTS_DIR = ROOT / "scripts"


def write_chart(path: Path, days_old: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("png", encoding="utf-8")
    ts = time.time() - days_old * 86400
    os.utime(path, (ts, ts))


def populate_fixture(base_dir: Path) -> dict[str, Path]:
    paths = {
        "old_morning": base_dir / "alice" / "morning" / "2026-04-03" / "old.png",
        "fresh_morning": base_dir / "alice" / "morning" / "2026-04-18" / "fresh.png",
        "old_activity": base_dir / "alice" / "activity" / "2026-03-18" / "old.png",
        "old_weekly": base_dir / "alice" / "weekly" / "2026-W01" / "old.png",
        "old_monthly": base_dir / "alice" / "monthly" / "2025-09" / "old.png",
        "old_smoke": base_dir / "system" / "smoke" / "2026-04-03" / "smoke.png",
    }
    write_chart(paths["old_morning"], 20)
    write_chart(paths["fresh_morning"], 1)
    write_chart(paths["old_activity"], 40)
    write_chart(paths["old_weekly"], 100)
    write_chart(paths["old_monthly"], 200)
    write_chart(paths["old_smoke"], 200)
    return paths


def assert_script_prunes_current_layout(command: list[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base_dir = Path(tmp) / "charts"
        paths = populate_fixture(base_dir)
        env = os.environ.copy()
        env["BASE_DIR"] = str(base_dir)

        subprocess.run(command, check=True, env=env, cwd=str(ROOT))

        assert not paths["old_morning"].exists(), paths["old_morning"]
        assert paths["fresh_morning"].exists(), paths["fresh_morning"]
        assert not paths["old_activity"].exists(), paths["old_activity"]
        assert not paths["old_weekly"].exists(), paths["old_weekly"]
        assert not paths["old_monthly"].exists(), paths["old_monthly"]
        assert paths["old_smoke"].exists(), paths["old_smoke"]


def main() -> int:
    assert_script_prunes_current_layout(["/bin/sh", str(SCRIPTS_DIR / "prune_chart_images.sh")])
    assert_script_prunes_current_layout(["/usr/bin/env", "bash", str(SCRIPTS_DIR / "cleanup_charts.sh")])
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
