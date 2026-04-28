#!/usr/bin/env python3
"""手动生成用户画像 Markdown 文件。

通常情况下，画像会在用户提交问卷或修改个人信息时由 chat_api 自动生成。
本脚本用于：
- 首次部署后批量生成
- 数据回填后手动刷新
- 调试画像内容

用法：
    python3 scripts/generate_user_portrait.py
    python3 scripts/generate_user_portrait.py --days 60
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path("/root/garmin_assistant")
for _p in (str(ROOT), str(ROOT / "app")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成用户画像 Markdown")
    parser.add_argument("--days", type=int, default=90, help="数据周期天数（默认 90）")
    args = parser.parse_args()

    from portrait_builder import build_portrait

    print(f"读取最近 {args.days} 天数据 + 用户问卷答案 → 生成画像...")
    result = build_portrait(args.days)
    print(f"画像 Markdown：{result['portrait_path']}")
    print(f"统计 JSON：    {result['stats_path']}")
    print(f"Markdown 大小：{result['md_chars']} 字符")
    print(f"数据量：       睡眠 {result['data_count'].get('sleep', 0)} 天，"
          f"骑行 {result['data_count'].get('cycling', 0)} 次")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
