from __future__ import annotations

from numbers import Real
from typing import Any


MORNING_TOPIC_PRIORITY = [
    {
        "topic": "deep_sleep_minutes",
        "history_topic": "deep_sleep_minutes",
        "title": "深睡 7 天趋势",
        "line_color": "#245A78",
        "aliases": ("deep_sleep", "deep_sleep_minutes", "深睡"),
    },
    {
        "topic": "resting_hr",
        "history_topic": "resting_hr",
        "title": "静息心率 7 天趋势",
        "line_color": "#6A4C93",
        "aliases": ("resting_hr", "静息心率", "resting heart rate", "resting heart"),
    },
    {
        "topic": "hrv",
        "history_topic": "hrv",
        "title": "HRV 7 天趋势",
        "line_color": "#3D6E68",
        "aliases": ("hrv",),
    },
    {
        "topic": "body_battery",
        "history_topic": "body_battery",
        "title": "Body Battery 7 天趋势",
        "line_color": "#B06A3A",
        "aliases": ("body battery", "body_battery"),
    },
]


def _count_history_points(history_points: Any) -> int:
    if history_points is None or isinstance(history_points, (str, bytes, dict)):
        return 0
    try:
        return len(history_points)  # type: ignore[arg-type]
    except TypeError:
        return 0


def _normalize_text(value: Any) -> str:
    return str(value or "").strip().lower().replace("（", "(").replace("）", ")")


def _joined_finding_text(finding: Any) -> str:
    if not isinstance(finding, dict):
        return _normalize_text(finding)
    parts = []
    for key in ("title", "description"):
        value = finding.get(key)
        if value:
            parts.append(_normalize_text(value))
    if not parts:
        for value in finding.values():
            if value:
                parts.append(_normalize_text(value))
    return " ".join(parts)


def _count_bucket_points(bucket: Any) -> int:
    if bucket is None or isinstance(bucket, (str, bytes, dict)):
        return 0
    try:
        bucket_len = len(bucket)  # type: ignore[arg-type]
    except TypeError:
        return 0
    if bucket_len <= 0:
        return 0
    bucket_values = list(bucket)  # type: ignore[arg-type]
    if any(isinstance(value, bool) or not isinstance(value, Real) for value in bucket_values):
        return 0
    return len(bucket_values)


def pick_morning_chart_topic(*, notable_findings, history_points):
    if not isinstance(history_points, dict):
        return None

    joined_text = " ".join(_joined_finding_text(finding) for finding in (notable_findings or []))

    for entry in MORNING_TOPIC_PRIORITY:
        if any(alias in joined_text for alias in entry["aliases"]) and _count_bucket_points(history_points.get(entry["history_topic"])) >= 5:
            return {
                "topic": entry["topic"],
                "title": entry["title"],
                "line_color": entry["line_color"],
            }

    for entry in MORNING_TOPIC_PRIORITY:
        if _count_bucket_points(history_points.get(entry["history_topic"])) >= 5:
            return {
                "topic": entry["topic"],
                "title": entry["title"],
                "line_color": entry["line_color"],
            }

    return None


def pick_activity_chart_topic(*, sport_type, history_points, metric_name):
    if _count_history_points(history_points) < 4:
        return None

    sport_label = str(sport_type).strip()
    metric_key = str(metric_name).strip()
    title = f"最近{len(history_points)}次{sport_label}训练负荷" if metric_key == "training_load" else f"最近{len(history_points)}次{sport_label}趋势"
    return {
        "topic": metric_key,
        "title": title,
        "line_color": "#315E49",
    }
