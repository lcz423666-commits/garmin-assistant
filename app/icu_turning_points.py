from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Any


STREAM_TYPES = ("watts", "heartrate", "cadence", "distance", "velocity_smooth")


@dataclass
class WindowStat:
    start_index: int
    end_index: int
    start_distance_m: float
    end_distance_m: float
    avg_power: float
    avg_heartrate: float
    hr_power_ratio: float
    valid_points: int
    midpoint_power: float
    midpoint_heartrate: float


def coerce_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def stream_map_from_payload(payload: dict | list) -> dict[str, list]:
    if isinstance(payload, dict):
        return {
            str(key): list(value)
            for key, value in payload.items()
            if isinstance(value, list)
        }

    stream_map: dict[str, list] = {}
    for item in payload or []:
        if not isinstance(item, dict):
            continue
        stream_type = str(item.get("type") or "").strip()
        data = item.get("data")
        if stream_type and isinstance(data, list):
            stream_map[stream_type] = data
    return stream_map


def align_streams(payload: dict | list, stream_types: tuple[str, ...] = STREAM_TYPES) -> dict[str, list]:
    stream_map = stream_map_from_payload(payload)
    available = [stream_map.get(name, []) for name in stream_types if stream_map.get(name)]
    if not available:
        return {name: [] for name in stream_types}
    shortest = min(len(values) for values in available)
    return {
        name: list(stream_map.get(name, []))[:shortest]
        for name in stream_types
    }


def _window_stats(
    streams: dict | list,
    *,
    window_sec: int = 300,
    step_sec: int = 30,
) -> list[WindowStat]:
    aligned = align_streams(streams)
    watts = aligned["watts"]
    heartrate = aligned["heartrate"]
    distance = aligned["distance"]
    total_points = min(len(watts), len(heartrate), len(distance))
    if total_points < window_sec:
        return []

    stats: list[WindowStat] = []
    for start in range(0, total_points - window_sec + 1, step_sec):
        end = start + window_sec
        valid_rows = []
        for offset in range(start, end):
            power = coerce_float(watts[offset])
            hr = coerce_float(heartrate[offset])
            dist = coerce_float(distance[offset])
            if power in (None, 0) or hr in (None, 0) or dist is None:
                continue
            valid_rows.append((offset, power, hr, dist))
        if not valid_rows:
            continue

        midpoint_row = valid_rows[len(valid_rows) // 2]
        avg_power = mean(row[1] for row in valid_rows)
        avg_heartrate = mean(row[2] for row in valid_rows)
        stats.append(
            WindowStat(
                start_index=start,
                end_index=end,
                start_distance_m=valid_rows[0][3],
                end_distance_m=valid_rows[-1][3],
                avg_power=avg_power,
                avg_heartrate=avg_heartrate,
                hr_power_ratio=avg_heartrate / avg_power,
                valid_points=len(valid_rows),
                midpoint_power=midpoint_row[1],
                midpoint_heartrate=midpoint_row[2],
            )
        )
    return stats


def detect_high_power_segments(
    streams: dict | list,
    ftp: float | int | None,
    min_duration: int = 120,
    window_sec: int = 300,
    step_sec: int = 30,
    *,
    return_debug: bool = False,
):
    ftp_value = coerce_float(ftp)
    if ftp_value in (None, 0):
        result = ([], {"windows": []})
        return result if return_debug else result[0]

    windows = _window_stats(streams, window_sec=window_sec, step_sec=step_sec)
    qualifying = [
        window
        for window in windows
        if window.valid_points >= min_duration and window.avg_power > ftp_value * 0.95
    ]
    if not qualifying:
        result = ([], {"windows": windows, "qualifying_windows": []})
        return result if return_debug else result[0]

    merged_ranges: list[list[WindowStat]] = [[qualifying[0]]]
    for window in qualifying[1:]:
        last_window = merged_ranges[-1][-1]
        if window.start_index <= last_window.end_index:
            merged_ranges[-1].append(window)
        else:
            merged_ranges.append([window])

    aligned = align_streams(streams)
    watts = aligned["watts"]
    heartrate = aligned["heartrate"]
    distance = aligned["distance"]

    segments = []
    for group in merged_ranges:
        range_start = group[0].start_index
        range_end = group[-1].end_index
        valid_rows = []
        above_threshold_runs: list[list[tuple[float, float, float]]] = []
        current_run: list[tuple[float, float, float]] = []
        for index in range(range_start, range_end):
            power = coerce_float(watts[index])
            hr = coerce_float(heartrate[index])
            dist = coerce_float(distance[index])
            if power in (None, 0) or hr in (None, 0) or dist is None:
                if current_run:
                    above_threshold_runs.append(current_run)
                    current_run = []
                continue
            row = (power, hr, dist)
            valid_rows.append(row)
            if power > ftp_value * 0.95:
                current_run.append(row)
            elif current_run:
                above_threshold_runs.append(current_run)
                current_run = []
        if current_run:
            above_threshold_runs.append(current_run)
        if len(valid_rows) < min_duration:
            continue
        longest_run = max(above_threshold_runs, key=len, default=[])
        segment_rows = longest_run if len(longest_run) >= min_duration else valid_rows
        avg_power = mean(row[0] for row in segment_rows)
        avg_hr = mean(row[1] for row in segment_rows)
        segments.append(
            {
                "start_km": round(segment_rows[0][2] / 1000, 1),
                "end_km": round(segment_rows[-1][2] / 1000, 1),
                "duration_seconds": len(segment_rows),
                "avg_power": round(avg_power, 1),
                "avg_hr": round(avg_hr, 1),
                "pct_ftp": round(avg_power / ftp_value, 2),
            }
        )

    segments = sorted(segments, key=lambda item: item["avg_power"], reverse=True)[:2]
    result = (
        segments,
        {
            "windows": windows,
            "qualifying_windows": qualifying,
        },
    )
    return result if return_debug else result[0]


def detect_decoupling_points(
    streams: dict | list,
    window_sec: int = 300,
    min_warmup_sec: int = 1800,
    step_sec: int = 30,
    *,
    return_debug: bool = False,
):
    aligned = align_streams(streams)
    total_points = len(aligned["watts"])
    windows = _window_stats(aligned, window_sec=window_sec, step_sec=step_sec)
    if total_points < min_warmup_sec or not windows:
        result = ([], {"windows": windows, "baseline_ratio": None, "candidates": []})
        return result if return_debug else result[0]

    halfway = total_points // 2
    minimum_valid_points = max(120, int(window_sec * 0.6))
    first_half = [
        window
        for window in windows
        if window.end_index <= halfway and window.valid_points >= minimum_valid_points
    ]
    second_half = [
        window
        for window in windows
        if window.start_index >= max(halfway, min_warmup_sec)
        and window.valid_points >= minimum_valid_points
    ]
    if not first_half or not second_half:
        result = ([], {"windows": windows, "baseline_ratio": None, "candidates": []})
        return result if return_debug else result[0]

    baseline_ratio = mean(window.hr_power_ratio for window in first_half)
    baseline_avg_power = mean(window.avg_power for window in first_half)
    minimum_candidate_power = max(100.0, baseline_avg_power * 0.6)
    candidates = []
    for window in second_half:
        if window.avg_power < minimum_candidate_power:
            continue
        increase_pct = ((window.hr_power_ratio / baseline_ratio) - 1) * 100
        if increase_pct >= 15:
            candidates.append((window, increase_pct))
    if not candidates:
        result = (
            [],
            {"windows": windows, "baseline_ratio": baseline_ratio, "candidates": []},
        )
        return result if return_debug else result[0]

    best_window, best_increase = max(candidates, key=lambda item: item[1])
    points = [
        {
            "distance_km": round(best_window.end_distance_m / 1000, 1),
            "power": round(best_window.avg_power, 1),
            "heartrate": round(best_window.avg_heartrate, 1),
            "baseline_ratio": round(baseline_ratio, 3),
            "current_ratio": round(best_window.hr_power_ratio, 3),
            "increase_pct": round(best_increase, 1),
        }
    ]
    result = (
        points,
        {
            "windows": windows,
            "baseline_ratio": baseline_ratio,
            "candidates": candidates,
        },
    )
    return result if return_debug else result[0]


def detect_power_decay(
    streams: dict | list,
    min_total_sec: int = 3600,
    *,
    return_debug: bool = False,
):
    aligned = align_streams(streams)
    valid_rows = []
    for index, power_value in enumerate(aligned["watts"]):
        power = coerce_float(power_value)
        distance = coerce_float(aligned["distance"][index]) if index < len(aligned["distance"]) else None
        if power in (None, 0) or distance is None:
            continue
        valid_rows.append((power, distance))
    if len(valid_rows) < min_total_sec:
        result = ([], {"thirds": []})
        return result if return_debug else result[0]

    third_size = len(valid_rows) // 3
    if third_size == 0:
        result = ([], {"thirds": []})
        return result if return_debug else result[0]

    first = valid_rows[:third_size]
    middle = valid_rows[third_size : third_size * 2]
    last = valid_rows[third_size * 2 :]
    thirds = [first, middle, last]
    labels = ("first_to_middle", "middle_to_last")
    pairs = ((first, middle), (middle, last))
    segments = []
    for label, (before_rows, after_rows) in zip(labels, pairs):
        if not before_rows or not after_rows:
            continue
        before_avg = mean(row[0] for row in before_rows)
        after_avg = mean(row[0] for row in after_rows)
        if before_avg <= 0:
            continue
        drop_pct = ((before_avg - after_avg) / before_avg) * 100
        if drop_pct > 15:
            segments.append(
                {
                    "transition": label,
                    "before_avg_power": round(before_avg, 1),
                    "after_avg_power": round(after_avg, 1),
                    "drop_pct": round(drop_pct, 1),
                    "range_start_km": round(after_rows[0][1] / 1000, 1),
                    "range_end_km": round(after_rows[-1][1] / 1000, 1),
                }
            )
    result = (
        segments,
        {
            "thirds": [
                {
                    "start_km": round(rows[0][1] / 1000, 1),
                    "end_km": round(rows[-1][1] / 1000, 1),
                    "avg_power": round(mean(row[0] for row in rows), 1),
                }
                for rows in thirds
                if rows
            ],
        },
    )
    return result if return_debug else result[0]
