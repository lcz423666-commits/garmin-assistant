from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta

from dotenv import load_dotenv

from icu_cycling import (
    BJ_TZ,
    _load_runtime_settings,
    _parse_datetime,
    _pick,
    _request_json,
    select_latest_ride,
)
from icu_turning_points import (
    STREAM_TYPES,
    align_streams,
    coerce_float,
    detect_decoupling_points,
    detect_high_power_segments,
    detect_power_decay,
)


load_dotenv("/root/.env")

ICU_BASE = "https://intervals.icu/api/v1"


def _km_text(distance_m: float | None) -> str:
    if distance_m is None:
        return "无数据"
    return f"{distance_m / 1000:.1f}"


def _duration_text(seconds: int | None) -> str:
    if seconds is None:
        return "无数据"
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes} 分 {secs} 秒"
    return f"{secs} 秒"


def _format_ratio(value: float | None) -> str:
    if value is None:
        return "无数据"
    return f"{value:.2f}"


def fetch_latest_ride_id() -> str:
    settings = _load_runtime_settings()
    if not settings:
        raise RuntimeError("ICU 运行配置不可用")
    today = datetime.now(BJ_TZ).date()
    oldest = today - timedelta(days=7)
    activities = _request_json(
        f"{ICU_BASE}/athlete/{settings['athlete_id']}/activities",
        api_key=settings["api_key"],
        params={
            "oldest": oldest.isoformat(),
            "newest": today.isoformat(),
        },
    )
    latest_ride = select_latest_ride(activities if isinstance(activities, list) else [])
    if not latest_ride:
        raise RuntimeError("最近没有可用的 ICU 骑行活动")
    return str(_pick(latest_ride, "id", "activity_id", default=""))


def fetch_activity_detail(activity_id: str) -> dict:
    settings = _load_runtime_settings()
    if not settings:
        raise RuntimeError("ICU 运行配置不可用")
    detail = _request_json(
        f"{ICU_BASE}/activity/{activity_id}",
        api_key=settings["api_key"],
        params={"intervals": "true"},
    )
    if not isinstance(detail, dict):
        raise RuntimeError(f"活动详情返回异常: {type(detail).__name__}")
    return detail


def fetch_streams(activity_id: str) -> dict[str, list]:
    settings = _load_runtime_settings()
    if not settings:
        raise RuntimeError("ICU 运行配置不可用")
    payload = _request_json(
        f"{ICU_BASE}/activity/{activity_id}/streams.json",
        api_key=settings["api_key"],
        params={"types": ",".join(STREAM_TYPES)},
    )
    return align_streams(payload)


def format_report(activity_info: dict, type_a: list[dict], type_b: list[dict], type_c: list[dict]) -> str:
    lines = [
        "====== ICU 骑行拐点检测报告 ======",
        f"活动名称：{activity_info.get('name') or '无数据'}",
        f"活动时间：{activity_info.get('start_time_text') or '无数据'}",
        f"总距离：{activity_info.get('distance_km', '无数据')} km",
        f"总时长：{_duration_text(activity_info.get('duration_seconds'))}",
        f"FTP：{activity_info.get('ftp') or '无数据'}W",
        "",
        "------ 类型A · 持续高功率段 ------",
    ]
    if not type_a:
        lines.append("未检测到满足阈值的持续高功率段")
    else:
        for index, segment in enumerate(type_a, start=1):
            lines.extend(
                [
                    f"[{index}] 里程 {segment['start_km']:.1f}-{segment['end_km']:.1f} km（持续 {_duration_text(segment['duration_seconds'])}）",
                    f"    平均功率 {segment['avg_power']:.1f}W（{segment['pct_ftp'] * 100:.0f}% FTP）",
                    f"    平均心率 {segment['avg_hr']:.1f} bpm",
                    "    判断：主动进攻或持续爬坡输出段",
                ]
            )

    lines.extend(["", "------ 类型B · 功率心率脱钩点 ------"])
    if not type_b:
        lines.append("未检测到显著的功率心率脱钩点")
    else:
        for index, point in enumerate(type_b, start=1):
            lines.extend(
                [
                    f"[{index}] 里程 {point['distance_km']:.1f} km 处",
                    f"    前半段基线 HR/W = {point['baseline_ratio']:.2f}",
                    f"    当前 HR/W = {point['current_ratio']:.2f}（上升 {point['increase_pct']:.1f}%）",
                    f"    此时功率 {point['power']:.1f}W，心率 {point['heartrate']:.1f} bpm",
                    "    判断：身体开始进入疲劳状态",
                ]
            )

    lines.extend(["", "------ 类型C · 功率衰减段 ------"])
    if not type_c:
        lines.append("未检测到显著的功率衰减段")
    else:
        for index, segment in enumerate(type_c, start=1):
            lines.extend(
                [
                    f"[{index}] 前段平均功率 {segment['before_avg_power']:.1f}W",
                    f"    后段平均功率 {segment['after_avg_power']:.1f}W（下降 {segment['drop_pct']:.1f}%）",
                    f"    里程 {segment['range_start_km']:.1f}-{segment['range_end_km']:.1f} km",
                    "    判断：后程明显掉速",
                ]
            )

    lines.extend(["", "------ 算法汇总建议 ------", "本次骑行最值得关注的点："])
    if type_b:
        lines.append(
            f"- {type_b[0]['distance_km']:.1f} km 处出现明显脱钩，后半程疲劳信号已经比较清楚"
        )
    if type_a:
        lines.append(
            f"- {type_a[0]['start_km']:.1f}-{type_a[0]['end_km']:.1f} km 有最强的持续高功率输出，说明这次高质量刺激是清晰存在的"
        )
    if type_c:
        lines.append(
            f"- {type_c[0]['range_start_km']:.1f}-{type_c[0]['range_end_km']:.1f} km 出现功率衰减，后程配速与体能管理值得重点复盘"
        )
    if not any((type_a, type_b, type_c)):
        lines.append("- 这次没有识别出特别突出的故事点，可能整体更接近均匀稳态骑行")
    return "\n".join(lines)


def _print_verbose(
    streams: dict[str, list],
    *,
    high_power_debug: dict,
    decoupling_debug: dict,
    decay_debug: dict,
):
    print("\n------ VERBOSE · 原始 streams 前后 10 个点 ------")
    for stream_type in STREAM_TYPES:
        values = streams.get(stream_type, [])
        print(
            json.dumps(
                {
                    "type": stream_type,
                    "head": values[:10],
                    "tail": values[-10:],
                    "length": len(values),
                },
                ensure_ascii=False,
            )
        )

    print("\n------ VERBOSE · 高功率滑窗统计 ------")
    for window in high_power_debug.get("qualifying_windows", []):
        print(
            json.dumps(
                {
                    "start_km": round(window.start_distance_m / 1000, 1),
                    "end_km": round(window.end_distance_m / 1000, 1),
                    "avg_power": round(window.avg_power, 1),
                    "avg_hr": round(window.avg_heartrate, 1),
                    "valid_points": window.valid_points,
                },
                ensure_ascii=False,
            )
        )

    print("\n------ VERBOSE · HR/W 滑窗统计 ------")
    print(
        json.dumps(
            {
                "baseline_ratio": round(decoupling_debug.get("baseline_ratio"), 3)
                if decoupling_debug.get("baseline_ratio") is not None
                else None,
                "window_count": len(decoupling_debug.get("windows", [])),
                "candidate_count": len(decoupling_debug.get("candidates", [])),
            },
            ensure_ascii=False,
        )
    )
    for window, increase_pct in decoupling_debug.get("candidates", []):
        print(
            json.dumps(
                {
                    "distance_km": round(window.end_distance_m / 1000, 1),
                    "ratio": round(window.hr_power_ratio, 3),
                    "increase_pct": round(increase_pct, 1),
                    "power": round(window.midpoint_power, 1),
                    "heartrate": round(window.midpoint_heartrate, 1),
                },
                ensure_ascii=False,
            )
        )

    print("\n------ VERBOSE · 三段功率统计 ------")
    print(json.dumps(decay_debug.get("thirds", []), ensure_ascii=False, indent=2))


def _build_activity_info(detail: dict) -> dict:
    start_time = _pick(detail, "start_date_local", "start_date")
    try:
        start_time_text = _parse_datetime(str(start_time)).astimezone(BJ_TZ).strftime("%Y-%m-%d %H:%M")
    except Exception:
        start_time_text = str(start_time or "无数据")
    return {
        "name": detail.get("name") or "无数据",
        "start_time_text": start_time_text,
        "distance_km": round((coerce_float(detail.get("distance")) or 0) / 1000, 1),
        "duration_seconds": int(coerce_float(detail.get("moving_time")) or coerce_float(detail.get("elapsed_time")) or 0),
        "ftp": int(coerce_float(_pick(detail, "icu_ftp", "ftp")) or 0) or None,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--activity", help="指定 activity_id，不传则拉最近一次")
    parser.add_argument("--verbose", action="store_true", help="打印详细调试信息")
    args = parser.parse_args()

    activity_id = args.activity or fetch_latest_ride_id()
    detail = fetch_activity_detail(activity_id)
    streams = fetch_streams(activity_id)
    activity_info = _build_activity_info(detail)

    type_a, high_power_debug = detect_high_power_segments(
        streams,
        ftp=activity_info.get("ftp"),
        return_debug=True,
    )
    type_b, decoupling_debug = detect_decoupling_points(
        streams,
        return_debug=True,
    )
    type_c, decay_debug = detect_power_decay(
        streams,
        return_debug=True,
    )

    print(format_report(activity_info, type_a, type_b, type_c))
    if args.verbose:
        _print_verbose(
            streams,
            high_power_debug=high_power_debug,
            decoupling_debug=decoupling_debug,
            decay_debug=decay_debug,
        )


if __name__ == "__main__":
    main()
