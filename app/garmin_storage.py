"""Persistence and history helpers for Garmin phase-one data flow."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median

from app_config import load_system_config


SYSTEM_CONFIG = load_system_config()
DATA_ROOT = Path((SYSTEM_CONFIG.get("storage") or {}).get("data_root", "/root/garmin_data"))
BJ_TZ = timezone(timedelta(hours=8))


def bj_now() -> datetime:
    return datetime.now(BJ_TZ)


def safe_slug(value: str) -> str:
    return value.replace("/", "_").replace(" ", "_")


def ensure_user_dir(user_name: str) -> Path:
    path = DATA_ROOT / safe_slug(user_name)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _category_dir(user_name: str, category: str) -> Path:
    path = ensure_user_dir(user_name) / category
    path.mkdir(parents=True, exist_ok=True)
    return path


def package_path(user_name: str, category: str, record_key: str) -> Path:
    return _category_dir(user_name, category) / f"{record_key}.json"


def package_exists(user_name: str, category: str, record_key: str) -> bool:
    return package_path(user_name, category, record_key).exists()


def count_packages(user_name: str, category: str) -> int:
    return sum(1 for _ in _category_dir(user_name, category).glob("*.json"))


def load_package(user_name: str, category: str, record_key: str) -> dict | None:
    path = package_path(user_name, category, record_key)
    if not path.exists():
        return None
    return _load_json(path)


def write_layered_package(
    user_name: str,
    category: str,
    record_key: str,
    raw_data: dict,
    normalized_data: dict,
    llm_payload: dict,
    message_preview: str = "",
    metadata: dict | None = None,
) -> Path:
    package = {
        "metadata": {
            "user_name": user_name,
            "category": category,
            "record_key": record_key,
            "saved_at": bj_now().isoformat(),
            **(metadata or {}),
        },
        "raw_data": raw_data,
        "normalized_data": normalized_data,
        "llm_payload": llm_payload,
        "message_preview": message_preview,
    }
    path = package_path(user_name, category, record_key)
    path.write_text(
        json.dumps(package, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return path


def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except Exception:
        try:
            dt = datetime.strptime(value, "%Y-%m-%d")
            dt = dt.replace(tzinfo=BJ_TZ)
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BJ_TZ)
    return dt


def _package_datetime(payload: dict) -> datetime | None:
    metadata = payload.get("metadata", {}) or {}
    for key in ("recorded_at", "activity_date", "date", "saved_at"):
        dt = _parse_datetime(metadata.get(key))
        if dt:
            return dt
    return None


def load_recent_packages(
    user_name: str,
    category: str,
    limit: int = 30,
    since_days: int | None = None,
) -> list[dict]:
    directory = _category_dir(user_name, category)
    cutoff = None
    if since_days is not None:
        cutoff = bj_now() - timedelta(days=since_days)

    results = []
    for path in directory.glob("*.json"):
        payload = _load_json(path)
        if not payload:
            continue
        package_dt = _package_datetime(payload)
        if cutoff and package_dt and package_dt < cutoff:
            continue
        results.append((package_dt or bj_now(), payload))

    results.sort(key=lambda item: item[0], reverse=True)
    return [payload for _, payload in results[:limit]]


def load_recent_normalized(
    user_name: str,
    category: str,
    limit: int = 30,
    since_days: int | None = None,
) -> list[dict]:
    return [
        payload.get("normalized_data", {})
        for payload in load_recent_packages(user_name, category, limit=limit, since_days=since_days)
        if payload.get("normalized_data")
    ]


def _dig(record: dict, field_path: str):
    current = record
    for part in field_path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def recent_values(records: list[dict], field_path: str) -> list[float]:
    values = []
    for record in records:
        value = _dig(record, field_path)
        if isinstance(value, (int, float)):
            values.append(float(value))
    return values


def average(records: list[dict], field_path: str) -> float | None:
    values = recent_values(records, field_path)
    return round(mean(values), 2) if values else None


def median_value(records: list[dict], field_path: str) -> float | None:
    values = recent_values(records, field_path)
    return round(median(values), 2) if values else None
