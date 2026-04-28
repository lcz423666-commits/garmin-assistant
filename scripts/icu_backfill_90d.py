#!/usr/bin/env python3
"""ICU 90天历史数据回填脚本

新用户首次接入时运行一次，将历史 ICU wellness（睡眠/HRV/CTL/ATL/TSB）
和骑行活动基础数据写入本地，供状态页和 chat 上下文使用。

功能：
- 一次 API 调用拉取 90 天 wellness 数据（睡眠/HRV/CTL/ATL/TSB/readiness）
- 一次 API 调用拉取 90 天活动列表（骑行类型），保存基础指标，不做 LLM 分析
- 跳过已有文件，可重复运行（断点续传）
- 文件格式与 icu_sleep.py / icu_cycling.py 完全兼容

用法：
    python3 scripts/icu_backfill_90d.py
    python3 scripts/icu_backfill_90d.py --days 60
    python3 scripts/icu_backfill_90d.py --force          # 覆盖已有文件
    python3 scripts/icu_backfill_90d.py --wellness-only  # 只回填睡眠数据
    python3 scripts/icu_backfill_90d.py --activities-only
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path("/root/garmin_assistant")
for _p in (str(ROOT), str(ROOT / "app")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import requests

BJ_TZ = timezone(timedelta(hours=8))
ICU_BASE = "https://intervals.icu/api/v1"
ENV_PATH = Path("/root/.env")
DATA_DIR = Path("/root/garmin_assistant/data/congzhi")
ICU_SLEEP_DIR = DATA_DIR / "icu_sleep"
ICU_CYCLING_DIR = DATA_DIR / "icu_cycling"

CYCLING_TYPES = {"Ride", "VirtualRide", "EBikeRide", "MountainBikeRide", "GravelRide"}


def _ts() -> str:
    return datetime.now(BJ_TZ).strftime("%H:%M:%S")


def _log(msg: str):
    print(f"[{_ts()}] {msg}", flush=True)


def _load_settings() -> dict | None:
    try:
        from dotenv import load_dotenv
        load_dotenv(ENV_PATH)
    except ImportError:
        # 手动读取 .env
        if ENV_PATH.exists():
            for line in ENV_PATH.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

    athlete_id = (os.getenv("ICU_ATHLETE_ID") or "").strip()
    api_key = (os.getenv("ICU_API_KEY") or "").strip()
    if not athlete_id or not api_key:
        _log("缺少 ICU_ATHLETE_ID 或 ICU_API_KEY，请检查 /root/.env")
        return None
    return {"athlete_id": athlete_id, "api_key": api_key}


def _get(url: str, *, api_key: str, params: dict | None = None) -> list | dict:
    auth = ("API_KEY", api_key)
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, auth=auth, timeout=30)
            if r.status_code == 429:
                wait = 5 * (attempt + 1)
                _log(f"  429 限流，{wait}s 后重试...")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as exc:
            if attempt == 2:
                raise
            _log(f"  请求失败（{exc}），2s 后重试...")
            time.sleep(2)
    return {}


def _safe_float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_tsb(ctl, atl) -> int | None:
    c, a = _safe_float(ctl), _safe_float(atl)
    if c is None or a is None:
        return None
    return int(round(c - a))


# ── wellness（睡眠 / HRV / CTL / ATL / TSB）─────────────────


def _entry_to_payload(entry: dict) -> dict:
    """把 ICU wellness 条目转成与 icu_sleep.py 兼容的 payload 结构。"""
    date_str = entry.get("idate") or entry.get("date") or entry.get("id", "")
    return {
        "date": date_str,
        "sleep": {
            "sleepSecs": entry.get("sleepSecs"),
            "sleepScore": entry.get("sleepScore"),
            "sleepQuality": entry.get("sleepQuality"),
            "avgSleepingHR": entry.get("avgSleepingHR"),
        },
        "hrv": {
            "rMSSD": entry.get("hrv"),
            "SDNN": entry.get("hrvSDNN"),
            "baevskySI": entry.get("baevskySI"),
        },
        "cardiovascular_recovery": {
            "restingHR": entry.get("restingHR"),
            "spO2": entry.get("spO2"),
            "readiness": entry.get("readiness"),
        },
        "training_load_context": {
            "ctl": entry.get("ctl"),
            "atl": entry.get("atl"),
            "rampRate": entry.get("rampRate"),
        },
        "body_metrics": {
            "weight": entry.get("weight"),
            "bodyFat": entry.get("bodyFat"),
        },
        "subjective_feedback": {
            "soreness": entry.get("soreness"),
            "fatigue": entry.get("fatigue"),
            "stress": entry.get("stress"),
            "mood": entry.get("mood"),
            "motivation": entry.get("motivation"),
        },
        "daily_activity": {
            "steps": entry.get("steps"),
            "vo2max": entry.get("vo2max"),
            "respiration": entry.get("respiration"),
        },
        "tsb": _safe_tsb(entry.get("ctl"), entry.get("atl")),
    }


def backfill_wellness(settings: dict, days: int = 90, force: bool = False) -> int:
    ICU_SLEEP_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(BJ_TZ).date()
    oldest = today - timedelta(days=days)

    _log(f"[wellness] 拉取 {oldest} ~ {today}（{days}天）...")

    raw = _get(
        f"{ICU_BASE}/athlete/{settings['athlete_id']}/wellness",
        api_key=settings["api_key"],
        params={"oldest": oldest.isoformat(), "newest": today.isoformat()},
    )

    if not isinstance(raw, list):
        _log(f"  wellness 返回格式异常: {type(raw)}")
        return 0

    has_sleep = [e for e in raw if (e.get("sleepSecs") or 0) > 0]
    _log(f"  共 {len(raw)} 条记录，有睡眠数据 {len(has_sleep)} 条")

    saved = skipped = 0
    for entry in has_sleep:
        payload = _entry_to_payload(entry)
        date_str = payload["date"]
        if not date_str:
            continue

        save_path = ICU_SLEEP_DIR / f"{date_str}.json"
        if save_path.exists() and not force:
            skipped += 1
            continue

        # 保留已有 LLM 分析内容，不覆盖
        existing_content = ""
        if save_path.exists():
            try:
                existing_content = json.loads(save_path.read_text(encoding="utf-8")).get("content", "")
            except Exception:
                pass

        save_path.write_text(
            json.dumps(
                {
                    "date": date_str,
                    "saved_at": datetime.now(BJ_TZ).isoformat(),
                    "payload": payload,
                    "content": existing_content,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        saved += 1

    _log(f"  wellness 完成：新增 {saved} 天，跳过 {skipped} 天（已存在）")
    return saved


# ── activities（骑行活动列表）────────────────────────────────


def backfill_activities(settings: dict, days: int = 90, force: bool = False) -> int:
    ICU_CYCLING_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(BJ_TZ).date()
    oldest = today - timedelta(days=days)

    _log(f"[activities] 拉取 {oldest} ~ {today}（{days}天）...")

    raw = _get(
        f"{ICU_BASE}/athlete/{settings['athlete_id']}/activities",
        api_key=settings["api_key"],
        params={"oldest": oldest.isoformat(), "newest": today.isoformat()},
    )

    if not isinstance(raw, list):
        _log(f"  activities 返回格式异常: {type(raw)}")
        return 0

    rides = [a for a in raw if a.get("type") in CYCLING_TYPES]
    _log(f"  共 {len(raw)} 条活动，骑行类 {len(rides)} 条")

    saved = skipped = 0
    for ride in rides:
        activity_id = str(ride.get("id", ""))
        start = ride.get("start_date_local") or ride.get("start_date") or ""
        date_str = start[:10]
        if not date_str or not activity_id:
            continue

        # 文件命名与 icu_cycling.py 一致：{date}_{activity_id}.json
        save_path = ICU_CYCLING_DIR / f"{date_str}_{activity_id}.json"
        if save_path.exists() and not force:
            skipped += 1
            continue

        # 保留已有 LLM 分析内容
        existing_content = ""
        if save_path.exists():
            try:
                existing_content = json.loads(save_path.read_text(encoding="utf-8")).get("content", "")
            except Exception:
                pass

        basic = {
            "date": date_str,
            "activity_id": activity_id,
            "saved_at": datetime.now(BJ_TZ).isoformat(),
            "title": ride.get("name") or "骑行",
            "type": ride.get("type"),
            "duration_secs": ride.get("moving_time") or ride.get("elapsed_time"),
            "distance_m": ride.get("distance"),
            "elevation_gain_m": ride.get("total_elevation_gain"),
            "avg_power": ride.get("average_watts"),
            "normalized_power": ride.get("weighted_average_watts"),
            "tss": ride.get("icu_training_load"),
            "ctl_after": ride.get("icu_ctl"),
            "atl_after": ride.get("icu_atl"),
            "avg_hr": ride.get("average_heartrate"),
            "max_hr": ride.get("max_heartrate"),
            "avg_speed_mps": ride.get("average_speed"),
            "content": existing_content,
        }

        save_path.write_text(
            json.dumps(basic, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        saved += 1

    _log(f"  activities 完成：新增 {saved} 条，跳过 {skipped} 条（已存在）")
    return saved


# ── 主入口 ────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ICU 历史数据回填（默认90天，wellness + 骑行活动）"
    )
    parser.add_argument("--days", type=int, default=90, metavar="N", help="回填天数（默认90）")
    parser.add_argument("--force", action="store_true", help="覆盖已有文件")
    parser.add_argument("--wellness-only", action="store_true", help="只回填 wellness（睡眠/HRV）")
    parser.add_argument("--activities-only", action="store_true", help="只回填骑行活动列表")
    args = parser.parse_args()

    settings = _load_settings()
    if not settings:
        return 1

    _log(
        f"ICU 回填开始 | athlete={settings['athlete_id']} "
        f"| 天数={args.days} | force={args.force}"
    )
    _log(f"写入目录：{DATA_DIR}")

    total = 0
    if not args.activities_only:
        total += backfill_wellness(settings, days=args.days, force=args.force)
        time.sleep(0.5)

    if not args.wellness_only:
        total += backfill_activities(settings, days=args.days, force=args.force)

    _log(f"\n全部完成，共写入 {total} 条记录。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
