from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path('/root/garmin_assistant')
APP_DIR = ROOT / 'app'
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from app_config import load_users

LEGACY_SOURCE_TO_USER_ID = {'丛至': 'congzhi', '杨': 'yang', 'Kevin': 'kevin'}
LEGACY_USER_ID_TO_SOURCE_NAME = {value: key for key, value in LEGACY_SOURCE_TO_USER_ID.items()}


def resolve_user_id(source_name: str | None) -> str | None:
    if not source_name:
        return None
    return LEGACY_SOURCE_TO_USER_ID.get(source_name, source_name)


def resolve_source_name(user_id: str | None) -> str | None:
    if not user_id:
        return None
    return LEGACY_USER_ID_TO_SOURCE_NAME.get(user_id, user_id)


def list_enabled_user_ids() -> list[str]:
    seen: list[str] = []
    for user in load_users():
        user_id = resolve_user_id(user.get('name'))
        if user_id and user_id not in seen:
            seen.append(user_id)
    return seen


def resolve_user_by_user_id(user_id: str):
    source_name = resolve_source_name(user_id)
    for user in load_users():
        if user.get('name') == source_name:
            return user
    raise KeyError(f'未找到用户 {user_id}')
