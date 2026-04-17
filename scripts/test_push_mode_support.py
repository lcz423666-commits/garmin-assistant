#!/usr/bin/env python3
import sys
sys.path.insert(0, '/root/garmin_assistant/app')
sys.path.insert(0, '/root/garmin_assistant/scripts')

import add_user_and_onboard as add_user
import garmin_monitor as gm


def main() -> int:
    friend = add_user.parse_user_fields("添加佳明用户：测试A，邮箱：a@example.com，密码：abc123，推送方式：B，好友令牌：friend_123")
    assert friend["push_mode"] == "friend", friend
    assert friend["friend_token"] == "friend_123", friend
    assert friend["pushplus_token"] is None, friend

    self_user = add_user.parse_user_fields("添加佳明用户：测试B，邮箱：b@example.com，密码：abc123，token：self_456")
    assert self_user["push_mode"] == "self", self_user
    assert self_user["pushplus_token"] == "self_456", self_user

    payload, meta = gm.build_pushplus_payload({
        "name": "测试A",
        "push_mode": "friend",
        "friend_token": "friend_123",
        "pushplus_token": None,
    }, "标题", "内容")
    assert payload["to"] == "friend_123", payload
    assert meta["push_mode"] == "friend", meta

    payload2, meta2 = gm.build_pushplus_payload({
        "name": "测试B",
        "push_mode": "self",
        "pushplus_token": "self_456",
        "friend_token": None,
    }, "标题", "内容")
    assert payload2["token"] == "self_456", payload2
    assert "to" not in payload2, payload2
    assert meta2["push_mode"] == "self", meta2

    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
