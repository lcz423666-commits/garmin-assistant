from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

BJ_TZ = timezone(timedelta(hours=8))
DATA_DIR = Path("/root/garmin_assistant/data/丛至")
PROFILE_PATH = DATA_DIR / "user_long_term_profile.json"

_DEFAULT_PROFILE: dict = {
    "last_updated": "",
    "training_patterns": [],
    "sleep_patterns": [],
    "recovery_characteristics": [],
    "personal_observations": [],
}


def load_profile() -> dict:
    if not PROFILE_PATH.exists():
        return dict(_DEFAULT_PROFILE)
    try:
        return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return dict(_DEFAULT_PROFILE)


def save_profile(profile: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    profile["last_updated"] = datetime.now(BJ_TZ).date().isoformat()
    PROFILE_PATH.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def profile_summary_for_prompt() -> str:
    """Return a concise summary for injecting into analysis prompts."""
    p = load_profile()
    parts = []
    for key, label in [
        ("training_patterns", "训练规律"),
        ("sleep_patterns", "睡眠特征"),
        ("recovery_characteristics", "恢复模式"),
        ("personal_observations", "已知个人规律"),
    ]:
        items = p.get(key) or []
        if items:
            parts.append(f"{label}：{'；'.join(items)}")
    return "\n".join(parts)
