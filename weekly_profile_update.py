#!/usr/bin/env python3
"""
Weekly long-term profile update for user 丛至.

Reads recent observations from observations_log.json and updates
user_long_term_profile.json via LLM synthesis.

Usage:
  python weekly_profile_update.py           # use last 14 days
  python weekly_profile_update.py --days 30 # use last 30 days
  python weekly_profile_update.py --dry-run # print result, don't save
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "app"))

BJ_TZ = timezone(timedelta(hours=8))


def _load_cycling_progression() -> dict:
    try:
        from ride_history import build_cycling_progression_summary
        return build_cycling_progression_summary(days=60)
    except Exception:
        return {}


def _bj_now() -> datetime:
    return datetime.now(BJ_TZ)


def _load_recent_observations(days: int) -> list[dict]:
    from goal_tracker import OBS_LOG_PATH, _load_json

    all_obs = _load_json(OBS_LOG_PATH)
    cutoff = (_bj_now().date() - timedelta(days=days)).isoformat()
    return [o for o in all_obs if o.get("date", "") >= cutoff]


def run(days: int = 14, dry_run: bool = False):
    observations = _load_recent_observations(days)
    if not observations:
        print(f"近 {days} 天没有观察数据，跳过画像更新")
        return

    from user_profile import load_profile, save_profile, PROFILE_PATH
    from llm_helper import LLM_MODEL, client

    current_profile = load_profile()
    today = _bj_now().date().isoformat()

    obs_text = "\n".join(
        f"[{o['date']}][{o['source']}] {o['obs']}" for o in observations
    )

    cycling_progression = _load_cycling_progression()
    cycling_section = ""
    if cycling_progression:
        cycling_section = (
            f"\n骑行历史统计（来自rides.db）：\n"
            f"{json.dumps(cycling_progression, ensure_ascii=False, indent=2)}\n"
        )

    prompt = (
        f"今天是 {today}。以下是用户丛至近 {days} 天的健康观察记录：\n\n"
        f"{obs_text}\n"
        f"{cycling_section}\n"
        f"当前长期画像：\n{json.dumps(current_profile, ensure_ascii=False, indent=2)}\n\n"
        "请综合以上信息，更新用户长期画像。要求：\n"
        "1. 只保留有多次观察支撑的规律，不要推测单次事件\n"
        "2. 每个列表最多 6 条，每条 ≤ 25 字\n"
        "3. 如果当前画像中某条规律与新数据矛盾，更新它；如果新数据不足以推翻，保留原条目\n"
        "4. cycling_progression 字段用骑行历史统计来填充，体现功率范围、FTP变化、IF分布等\n"
        "5. 只输出 JSON 对象，不要其他文字\n\n"
        "输出格式：\n"
        "{\n"
        '  "training_patterns": ["训练规律，如惯用强度分布、周训练量范围"],\n'
        '  "sleep_patterns": ["睡眠特征，如通常入睡时间、对深睡影响因素"],\n'
        '  "recovery_characteristics": ["恢复模式，如大强度后需几天、HRV恢复规律"],\n'
        '  "personal_observations": ["个人独特规律，如BB波动特点、REM补偿现象"],\n'
        '  "cycling_progression": ["骑行进步轨迹，如FTP趋势、功率范围、惯用IF区间"]\n'
        "}"
    )

    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )
    raw = (response.choices[0].message.content or "").strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        raw = "\n".join(inner)

    updated = json.loads(raw)

    print(f"\n=== 更新后的长期画像 ===\n{json.dumps(updated, ensure_ascii=False, indent=2)}")

    if dry_run:
        print("\n[dry-run] 未写入文件")
        return

    save_profile(updated)
    print(f"\n画像已保存：{PROFILE_PATH}")


def main():
    parser = argparse.ArgumentParser(description="更新用户丛至的长期健康画像")
    parser.add_argument("--days", type=int, default=14, help="回顾天数（默认14）")
    parser.add_argument("--dry-run", action="store_true", help="只打印结果，不写入文件")
    args = parser.parse_args()
    run(days=args.days, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
