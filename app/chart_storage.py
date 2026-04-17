from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import quote

from chart_config import CHART_BASE_URL, CHART_PUBLIC_DIR


def _clean_segment(value, field_name):
    segment = str(value).strip()
    if not segment:
        raise ValueError(f"{field_name} must not be empty")
    if segment == "..":
        raise ValueError(f"{field_name} is unsafe")
    if Path(segment).is_absolute():
        raise ValueError(f"{field_name} is unsafe")
    if "/" in segment or "\\" in segment:
        raise ValueError(f"{field_name} is unsafe")
    return segment


def build_chart_filename(user_id, message_type, topic):
    payload = f"{user_id}|{message_type}|{topic}".encode("utf-8")
    return f"{hashlib.sha256(payload).hexdigest()[:24]}.png"


def build_chart_output_path(user_id, message_type, date_str, opaque_name):
    safe_user_id = _clean_segment(user_id, "user_id")
    safe_message_type = _clean_segment(message_type, "message_type")
    safe_date_str = _clean_segment(date_str, "date_str")
    safe_opaque_name = _clean_segment(opaque_name, "opaque_name")
    return CHART_PUBLIC_DIR / safe_user_id / safe_message_type / safe_date_str / safe_opaque_name


def build_chart_public_url(user_id, message_type, date_str, opaque_name):
    if not CHART_BASE_URL:
        return None
    safe_user_id = _clean_segment(user_id, "user_id")
    safe_message_type = _clean_segment(message_type, "message_type")
    safe_date_str = _clean_segment(date_str, "date_str")
    safe_opaque_name = _clean_segment(opaque_name, "opaque_name")
    return (
        f"{CHART_BASE_URL}/charts/"
        f"{quote(safe_user_id)}/{quote(safe_message_type)}/{quote(safe_date_str)}/{quote(safe_opaque_name)}"
    )
