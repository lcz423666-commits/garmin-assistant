#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path("/root/garmin_assistant")
APP_DIR = ROOT / "app"
CONFIG_FILE = ROOT / "config" / "users.json"
STATE_DIR = ROOT / "state"
HEALTH_RUNTIME_FILE = STATE_DIR / "health_runtime.json"
FOLLOWUP_STATE_FILE = STATE_DIR / "health_followup_state.json"
PID_FILE = STATE_DIR / "garmin_monitor.pid"
BJ_TZ = timezone(timedelta(hours=8))
RUNTIME_STALE_MINUTES = 30


def bj_now() -> datetime:
    return datetime.now(BJ_TZ)


def iso_now() -> str:
    return bj_now().isoformat()


if APP_DIR.exists() and str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def safe_state_name(name: str) -> str:
    return name.replace("/", "_").replace(" ", "_")


def enabled_users():
    payload = load_json(CONFIG_FILE, {"users": []})
    return [user for user in payload.get("users", []) if user.get("enabled", True)]


def load_user_state(name: str):
    return load_json(STATE_DIR / f".garmin_monitor_state_{safe_state_name(name)}.json", {})


def parse_iso(value: str | None):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BJ_TZ)
    return dt.astimezone(BJ_TZ)


def is_runtime_stale(runtime: dict):
    now = bj_now()
    cycle_finished = parse_iso(runtime.get("cycle_finished_at"))
    if not cycle_finished:
        return True
    return now - cycle_finished > timedelta(minutes=RUNTIME_STALE_MINUTES)


def classify_user_status(name: str, runtime: dict, state: dict, today: str):
    loaded_users = set(runtime.get("loaded_users") or [])
    if name not in loaded_users:
        return {"ok": False, "reason_code": "not_loaded", "reason": "未被监控进程加载"}

    if is_runtime_stale(runtime):
        return {"ok": False, "reason_code": "runtime_stale", "reason": "监控循环超过 30 分钟未更新"}

    cycle_error = (state.get("last_cycle_error") or "").strip()
    if cycle_error:
        if "401" in cycle_error or "403" in cycle_error or "token" in cycle_error.lower() or "login" in cycle_error.lower():
            return {"ok": False, "reason_code": "auth_error", "reason": "Garmin 登录/token 失效"}
        return {"ok": False, "reason_code": "cycle_error", "reason": "运行循环异常"}

    if state.get("last_sleep_push_date") == today and state.get("last_sleep_push_result") == "success":
        return {"ok": True, "reason_code": "success", "reason": "晨报已成功推送"}

    if state.get("last_sleep_push_date") == today and state.get("last_sleep_push_result") == "failed":
        return {"ok": False, "reason_code": "push_failed", "reason": "推送通道失败"}

    if state.get("last_sleep_check_date") == today:
        result = state.get("last_sleep_check_result")
        if result == "no_data":
            return {"ok": False, "reason_code": "no_data", "reason": "Garmin 未返回完整睡眠数据"}
        if result == "insufficient_sleep":
            return {"ok": False, "reason_code": "insufficient_sleep", "reason": "睡眠时长不足，未生成晨报"}
        if result == "error":
            check_reason = (state.get("last_sleep_check_reason") or "").lower()
            if "401" in check_reason or "403" in check_reason or "token" in check_reason or "login" in check_reason:
                return {"ok": False, "reason_code": "auth_error", "reason": "Garmin 登录/token 失效"}
            return {"ok": False, "reason_code": "analysis_error", "reason": "分析生成失败"}
        if result == "push_failed":
            return {"ok": False, "reason_code": "push_failed", "reason": "推送通道失败"}
        if result in {"already_processed", "already_success"} and state.get("last_sleep_push_date") != today:
            return {"ok": False, "reason_code": "not_pushed", "reason": "今天尚未完成晨报推送"}

    return {"ok": False, "reason_code": "not_checked", "reason": "今天尚未完成睡眠检查"}


def build_user_status_map(today: str):
    users = enabled_users()
    runtime = load_json(HEALTH_RUNTIME_FILE, {})
    status_map = {}
    for user in users:
        name = user["name"]
        state = load_user_state(name)
        status_map[name] = classify_user_status(name, runtime, state, today)
    return users, runtime, status_map


def build_user_failures(today: str):
    users, runtime, status_map = build_user_status_map(today)
    failures = []
    for user in users:
        name = user["name"]
        status = status_map[name]
        if not status["ok"]:
            failures.append({"name": name, "reason_code": status["reason_code"], "reason": status["reason"]})
    return users, runtime, failures


def format_failure_lines(failures: list[dict]):
    return "\n".join(f"- {item['name']}：{item['reason']}" for item in failures)


def format_failure_inline(failures: list[dict]) -> str:
    return "；".join(f"{item['name']}（{item['reason']}）" for item in failures)


def compact_message_text(message: str) -> str:
    parts = []
    for line in message.splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        if cleaned.startswith("- "):
            cleaned = cleaned[2:].strip()
        parts.append(cleaned)
    return "；".join(parts)


def build_snapshot_log_detail(payload: dict) -> str:
    title = (payload.get("title") or "").strip()
    failures = payload.get("failures") or []
    if failures:
        failure_text = format_failure_inline(failures)
        return f"{title}：{failure_text}" if title else failure_text

    message = compact_message_text(payload.get("message") or "")
    if title and message:
        return f"{title}：{message}"
    return title or message


def load_followup_state():
    payload = load_json(FOLLOWUP_STATE_FILE, {})
    tracked_users = payload.get("tracked_users") or []
    initial_failed_users = payload.get("initial_failed_users") or []
    if not isinstance(tracked_users, list):
        tracked_users = []
    if not isinstance(initial_failed_users, list):
        initial_failed_users = []
    return {
        "date": payload.get("date"),
        "active": bool(payload.get("active", False)),
        "resolved_sent": bool(payload.get("resolved_sent", False)),
        "tracked_users": [item for item in tracked_users if isinstance(item, str) and item],
        "initial_failed_users": [item for item in initial_failed_users if isinstance(item, str) and item],
    }


def save_followup_state(payload: dict):
    normalized = {
        "date": payload.get("date"),
        "active": bool(payload.get("active", False)),
        "resolved_sent": bool(payload.get("resolved_sent", False)),
        "tracked_users": list(dict.fromkeys(payload.get("tracked_users") or [])),
        "initial_failed_users": list(dict.fromkeys(payload.get("initial_failed_users") or [])),
    }
    save_json(FOLLOWUP_STATE_FILE, normalized)


def write_snapshot_run_log(payload: dict):
    mode = payload.get("mode") or ""
    if mode == "manual":
        return
    if not payload.get("should_notify", True):
        return
    try:
        import garmin_monitor as gm

        event_type = {
            "morning": "晨报巡检",
            "followup": "跟踪巡检",
            "night": "日终巡检",
        }.get(mode, "")
        if not event_type:
            return
        detail = build_snapshot_log_detail(payload)

        gm.feishu_write_run_log(
            "",
            event_type,
            detail,
        )
    except Exception:
        pass


def build_recovery_message(today: str, followup: dict, status_map: dict | None = None) -> str:
    initial_failed_users = followup.get("initial_failed_users") or followup.get("tracked_users") or []
    if status_map is None:
        _, _, status_map = build_user_status_map(today)
    remaining_failures = [name for name, status in (status_map or {}).items() if not status.get("ok")]

    if len(initial_failed_users) == 1:
        prefix = f"{initial_failed_users[0]}的睡眠晨报已成功补发"
    else:
        prefix = "09:00 时失败的用户已全部恢复"

    if remaining_failures:
        suffix = "当前仍有未完成晨报。"
    elif len(initial_failed_users) == 1:
        suffix = "当前所有用户今日晨报均已推送完成。"
    else:
        suffix = "当前无未完成晨报。"

    return f"{prefix}，{suffix}"


def morning_snapshot(today: str):
    users, _, failures = build_user_failures(today)
    if failures:
        failed_user_names = [item["name"] for item in failures]
        save_followup_state(
            {
                "date": today,
                "active": True,
                "resolved_sent": False,
                "tracked_users": failed_user_names,
                "initial_failed_users": failed_user_names,
            }
        )
        return {
            "mode": "morning",
            "check_time": iso_now(),
            "should_notify": True,
            "title": "今日晨报仍有失败用户",
            "message": format_failure_lines(failures),
            "failures": failures,
        }

    save_followup_state(
        {
            "date": today,
            "active": False,
            "resolved_sent": False,
            "tracked_users": [],
            "initial_failed_users": [],
        }
    )
    return {
        "mode": "morning",
        "check_time": iso_now(),
        "should_notify": False,
        "title": "",
        "message": "",
        "failures": [],
    }


def followup_snapshot(today: str):
    followup = load_followup_state()
    if followup.get("date") != today:
        followup = {
            "date": today,
            "active": False,
            "resolved_sent": False,
            "tracked_users": [],
            "initial_failed_users": [],
        }

    tracked_users = followup.get("tracked_users") or []
    if not followup.get("active") or not tracked_users:
        followup["active"] = False
        followup["tracked_users"] = []
        save_followup_state(followup)
        return {
            "mode": "followup",
            "check_time": iso_now(),
            "should_notify": False,
            "title": "",
            "message": "",
            "failures": [],
        }

    _, _, status_map = build_user_status_map(today)
    failures = []
    for name in tracked_users:
        status = status_map.get(name)
        if status and not status["ok"]:
            failures.append({"name": name, "reason_code": status["reason_code"], "reason": status["reason"]})

    if failures:
        followup["active"] = True
        followup["resolved_sent"] = False
        followup["tracked_users"] = [item["name"] for item in failures]
        save_followup_state(followup)
        return {
            "mode": "followup",
            "check_time": iso_now(),
            "should_notify": True,
            "title": "晨报失败仍未恢复",
            "message": format_failure_lines(failures),
            "failures": failures,
        }

    if followup.get("active") and not followup.get("resolved_sent"):
        recovery_message = build_recovery_message(today, followup, status_map)
        followup["active"] = False
        followup["resolved_sent"] = True
        followup["tracked_users"] = []
        save_followup_state(followup)
        return {
            "mode": "followup",
            "check_time": iso_now(),
            "should_notify": True,
            "title": "今日晨报异常已恢复",
            "message": recovery_message,
            "failures": [],
        }

    save_followup_state(followup)
    return {
        "mode": "followup",
        "check_time": iso_now(),
        "should_notify": False,
        "title": "",
        "message": "",
        "failures": [],
    }


def build_night_risks(today: str):
    users, runtime, status_map = build_user_status_map(today)
    risks = []

    enabled_names = [user["name"] for user in users]
    loaded_names = runtime.get("loaded_users") or []

    if not runtime:
        risks.append("监控运行快照缺失")
    if is_runtime_stale(runtime):
        risks.append("监控循环最近 30 分钟未更新")
    if sorted(enabled_names) != sorted(loaded_names):
        risks.append(f"监控加载用户与配置不一致（配置 {len(enabled_names)} 人，运行 {len(loaded_names)} 人）")
    if not PID_FILE.exists():
        risks.append("监控进程 PID 文件缺失")

    failures = []
    for user in users:
        name = user["name"]
        status = status_map[name]
        if not status["ok"] and status["reason_code"] not in {"runtime_stale", "not_loaded"}:
            failures.append({"name": name, "reason": status["reason"]})

    return risks, failures


def night_snapshot(today: str):
    risks, failures = build_night_risks(today)
    lines = [f"- {risk}" for risk in risks]
    lines.extend(f"- {item['name']}：{item['reason']}" for item in failures)
    if lines:
        return {
            "mode": "night",
            "check_time": iso_now(),
            "should_notify": True,
            "title": "日终巡检发现风险",
            "message": "\n".join(lines),
            "failures": failures,
        }
    return {
        "mode": "night",
        "check_time": iso_now(),
        "should_notify": True,
        "title": "日终巡检完成，未发现影响明早运行的问题。",
        "message": "",
        "failures": [],
    }


def manual_snapshot(today: str):
    users, runtime, failures = build_user_failures(today)
    if failures:
        return {
            "mode": "manual",
            "check_time": iso_now(),
            "should_notify": True,
            "title": "当前仍有失败用户",
            "message": format_failure_lines(failures),
            "failures": failures,
            "runtime": runtime,
        }
    return {
        "mode": "manual",
        "check_time": iso_now(),
        "should_notify": True,
        "title": "佳明助手当前运行正常。",
        "message": "",
        "failures": [],
        "runtime": runtime,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["morning", "followup", "night", "manual"], default="manual")
    args = parser.parse_args()

    today = bj_now().strftime("%Y-%m-%d")
    if args.mode == "morning":
        payload = morning_snapshot(today)
    elif args.mode == "followup":
        payload = followup_snapshot(today)
    elif args.mode == "night":
        payload = night_snapshot(today)
    else:
        payload = manual_snapshot(today)

    write_snapshot_run_log(payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
