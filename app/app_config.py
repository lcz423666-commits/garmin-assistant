"""Shared config loading and redaction helpers for the Garmin assistant."""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path


PROJECT_ROOT = Path(os.environ.get("GARMIN_ASSISTANT_ROOT", "/root/garmin_assistant"))
APP_DIR = PROJECT_ROOT / "app"
CONFIG_ROOT = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
TOKENS_DIR = PROJECT_ROOT / "tokens"
REVIEW_SAMPLES_DIR = PROJECT_ROOT / "review_samples"
DEBUG_DIR = PROJECT_ROOT / "debug"
LOGS_DIR = PROJECT_ROOT / "logs"
STATE_DIR = PROJECT_ROOT / "state"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
BACKUP_DIR = PROJECT_ROOT / "backup"
SYSTEM_CONFIG_PATH = CONFIG_ROOT / "system.json"
USERS_CONFIG_PATH = CONFIG_ROOT / "users.json"
REDACTED = "***REDACTED***"
SKIP = object()

EMAIL_RE = re.compile(r"\b([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._=-]+\b")
TOKEN_DIR_RE = re.compile(r"/root/(?:garmin_tokens|garmin_assistant/tokens)/[A-Za-z0-9_./-]+")
JWT_RE = re.compile(r"\beyJ[A-Za-z0-9._=-]{20,}\b")
UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\b"
)
SECRET_ASSIGN_RE = re.compile(
    r"(?i)\b("
    r"api[_-]?key|pushplus[_-]?token|tenant_access_token|access[_-]?token|refresh[_-]?token|"
    r"oauth[_-]?token(?:[_-]?secret)?|app_secret|password|secret|authorization"
    r")(\s*[:=]\s*)([^\s,;]+)"
)
SECRET_VALUE_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")

COORD_KEYS = {
    "startlatitude",
    "startlongitude",
    "endlatitude",
    "endlongitude",
}
REDACTED_KEYS = {
    "userid",
    "user_id",
    "userprofilepk",
    "ownerid",
    "owner_id",
    "userprofileid",
    "user_profile_id",
    "userdailysummaryid",
    "profileid",
    "profile_id",
    "deviceid",
    "device_id",
    "sessionid",
    "session_id",
    "sessiontoken",
    "session_token",
    "pushplustoken",
    "pushplus_token",
    "friend_token",
    "garmin_email",
    "garmin_password",
    "activityid",
    "activity_id",
    "garmin_guid",
    "guid",
    "uuid",
    "ownerdisplayname",
    "ownerprofileimageurl",
    "ownerprofileimageurlsmall",
    "ownerprofileimageurlmedium",
    "ownerprofileimageurllarge",
}
SENSITIVE_KEYWORDS = (
    "token",
    "secret",
    "password",
    "authorization",
    "cookie",
)


def _load_json(path: Path):
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, (dict, list)):
        raise ValueError(f"配置文件格式错误: {path}")
    return data


@lru_cache(maxsize=1)
def load_system_config() -> dict:
    data = _load_json(SYSTEM_CONFIG_PATH)
    if not isinstance(data, dict):
        raise ValueError(f"system.json 顶层必须是对象: {SYSTEM_CONFIG_PATH}")
    return data


def load_users() -> list[dict]:
    data = _load_json(USERS_CONFIG_PATH)
    users = data.get("users") if isinstance(data, dict) else data
    if not isinstance(users, list):
        raise ValueError(f"users.json 顶层必须是 users 数组: {USERS_CONFIG_PATH}")

    enabled_users = []
    for index, raw_user in enumerate(users, start=1):
        if not isinstance(raw_user, dict):
            raise ValueError(f"users.json 第 {index} 个用户配置不是对象")
        if not raw_user.get("enabled", True):
            continue

        push_mode = (raw_user.get("push_mode") or "self").strip().lower()
        if push_mode not in {"self", "friend"}:
            raise ValueError(f"users.json 第 {index} 个启用用户 push_mode 无效: {push_mode}")

        normalized = {
            "name": raw_user.get("name"),
            "garmin_email": raw_user.get("garmin_email"),
            "garmin_password": raw_user.get("garmin_password"),
            "garmin_is_cn": bool(raw_user.get("garmin_is_cn", False)),
            "push_mode": push_mode,
            "pushplus_token": raw_user.get("pushplus_token"),
            "friend_token": raw_user.get("friend_token"),
            "enabled": True,
        }
        missing = [key for key in ("name", "garmin_email", "garmin_password") if not normalized.get(key)]
        if push_mode == "self" and not normalized.get("pushplus_token"):
            missing.append("pushplus_token")
        if push_mode == "friend" and not normalized.get("friend_token"):
            missing.append("friend_token")
        if missing:
            raise ValueError(f"users.json 第 {index} 个启用用户缺少字段: {', '.join(missing)}")
        enabled_users.append(normalized)
    return enabled_users


def mask_email(value: str) -> str:
    match = EMAIL_RE.fullmatch(value or "")
    if not match:
        return value
    local = match.group(1)
    domain = match.group(2)
    if len(local) <= 2:
        masked_local = "*" * len(local)
    elif len(local) <= 4:
        masked_local = local[0] + "***" + local[-1]
    else:
        masked_local = local[:2] + "***" + local[-2:]
    return f"{masked_local}@{domain}"


def mask_identifier(value: str, prefix: int = 3, suffix: int = 2) -> str:
    if not value:
        return value
    if len(value) <= prefix + suffix:
        return REDACTED
    return f"{value[:prefix]}***{value[-suffix:]}"


def sanitize_text(text):
    if not isinstance(text, str):
        return text

    text = EMAIL_RE.sub(lambda match: mask_email(match.group(0)), text)
    text = TOKEN_DIR_RE.sub("/root/garmin_tokens/***", text)
    text = BEARER_RE.sub(f"Bearer {REDACTED}", text)
    text = SECRET_ASSIGN_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}", text)
    text = SECRET_VALUE_RE.sub(REDACTED, text)
    text = JWT_RE.sub(REDACTED, text)
    text = UUID_RE.sub(REDACTED, text)
    return text


def is_coord_key(key: str) -> bool:
    key_lower = key.lower()
    return key_lower in COORD_KEYS or key_lower.endswith("latitude") or key_lower.endswith("longitude")


def should_redact_key(key: str) -> bool:
    key_lower = key.lower()
    if key_lower in REDACTED_KEYS:
        return True
    return any(keyword in key_lower for keyword in SENSITIVE_KEYWORDS)


def sanitize_export_value(value, parent_key: str = ""):
    if parent_key:
        if is_coord_key(parent_key):
            return SKIP
        if should_redact_key(parent_key):
            return REDACTED

    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            cleaned = sanitize_export_value(item, key)
            if cleaned is SKIP:
                continue
            result[key] = cleaned
        return result

    if isinstance(value, list):
        cleaned_list = []
        for item in value:
            cleaned = sanitize_export_value(item, parent_key)
            if cleaned is SKIP:
                continue
            cleaned_list.append(cleaned)
        return cleaned_list

    if isinstance(value, str):
        return sanitize_text(value)

    return value
