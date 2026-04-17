#!/usr/bin/env python3
"""
一次性交互式登录脚本，用于为需要验证码的 Garmin 账号生成缓存 Token。

用法：
    python3 init_garmin_token.py --email xxx@example.com [--password xxx] [--cn]

登录成功后，Token 会保存到 TOKEN_BASE_DIR 下，监控脚本后续将直接使用缓存，
不再需要验证码。
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = PROJECT_ROOT / "app"
if APP_DIR.exists() and str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from app_config import load_system_config

SYSTEM_CONFIG = load_system_config()
MONITOR_CONFIG = SYSTEM_CONFIG.get("monitor") or {}
TOKEN_BASE_DIR = MONITOR_CONFIG.get("token_base_dir", "/root/garmin_tokens")


def get_token_dir(email: str) -> str:
    safe_email = email.replace("@", "_at_").replace(".", "_")
    return os.path.join(TOKEN_BASE_DIR, safe_email)


def prompt_mfa() -> str:
    code = input("请输入 Garmin 验证码（MFA/邮件验证码）：").strip()
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description="为需要验证码的 Garmin 账号初始化 Token")
    parser.add_argument("--email", required=True, help="Garmin 账号邮箱")
    parser.add_argument("--password", default=None, help="Garmin 密码（可选，不传则交互输入）")
    parser.add_argument("--cn", action="store_true", help="是否为中国区账号（garmin.cn）")
    args = parser.parse_args()

    email = args.email.strip()
    is_cn = args.cn

    if args.password:
        password = args.password
    else:
        password = getpass.getpass(f"请输入 {email} 的 Garmin 密码：")

    token_dir = get_token_dir(email)
    os.makedirs(token_dir, exist_ok=True)

    print(f"\n正在登录 {'中国区' if is_cn else '国际区'} 账号：{email}")
    print(f"Token 将保存至：{token_dir}\n")

    try:
        from garminconnect import Garmin

        client = Garmin(
            email,
            password,
            is_cn=is_cn,
            prompt_mfa=prompt_mfa,
        )
        client.login()
    except Exception as exc:
        print(f"\n登录失败：{exc}", file=sys.stderr)
        return 1

    try:
        client.garth.dump(token_dir)
        print(f"\nToken 已成功保存到：{token_dir}")
        print("后续监控脚本将自动使用此 Token，无需再次输入验证码。")
    except Exception as exc:
        print(f"\nToken 保存失败：{exc}", file=sys.stderr)
        return 1

    try:
        name = client.get_full_name() or client.display_name or email
        print(f"登录用户：{name}")
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
