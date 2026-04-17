#!/usr/bin/env python3
"""Add a Garmin assistant user from natural language and trigger first onboarding."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = PROJECT_ROOT / "app"
if APP_DIR.exists() and str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import app_config
from app_config import USERS_CONFIG_PATH, mask_email, mask_identifier, sanitize_text


FIELD_PATTERNS = {
    "name": (
        "添加新用户",
        "新增用户",
        "新增佳明用户",
        "添加佳明用户",
        "帮我添加佳明用户",
        "帮我新增佳明用户",
        "添加 Garmin 用户",
        "新增 Garmin 用户",
        "姓名",
        "name",
    ),
    "garmin_email": ("garmin_email", "garmin email", "email", "邮箱"),
    "garmin_password": ("garmin_password", "garmin password", "password", "密码"),
    "pushplus_token": ("pushplus_token", "pushplus token", "token", "用户自己的pushplus token", "用户token", "自有token"),
    "friend_token": ("friend_token", "friend token", "好友令牌", "好友token", "好友 token"),
    "push_mode": ("push_mode", "push mode", "推送方式", "推送模式"),
    "garmin_is_cn": ("garmin_is_cn", "is_cn", "中国区", "国区"),
    "enabled": ("enabled", "启用"),
}
REQUIRED_FIELDS = ("name", "garmin_email")
PASSWORD_FIELD = "garmin_password"
TRUE_VALUES = {"1", "true", "yes", "y", "是", "开", "开启", "启用"}
FALSE_VALUES = {"0", "false", "no", "n", "否", "关", "关闭", "禁用"}


def parse_push_mode(value: str | None, *, friend_token: str | None = None, pushplus_token: str | None = None) -> str:
    if value is None:
        return "friend" if friend_token else "self"
    normalized = strip_wrapping_quotes(value).strip().lower()
    mapping = {
        "a": "self",
        "方式a": "self",
        "self": "self",
        "自有token": "self",
        "用户自己的token": "self",
        "用户自己的pushplus token": "self",
        "b": "friend",
        "方式b": "friend",
        "friend": "friend",
        "好友消息": "friend",
        "通过管理员账号推送": "friend",
        "管理员账号推送": "friend",
    }
    if normalized in mapping:
        return mapping[normalized]
    if pushplus_token and not friend_token:
        return "self"
    if friend_token:
        return "friend"
    raise ValueError(f"push_mode 无法识别: {sanitize_text(value)}")


def get_conditional_missing_fields(user: dict) -> list[str]:
    push_mode = user.get("push_mode") or "self"
    if push_mode == "friend":
        return [field for field in ("friend_token",) if not user.get(field)]
    return [field for field in ("pushplus_token",) if not user.get(field)]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="新增佳明健康助手用户并自动触发首次接入")
    parser.add_argument("text", nargs="?", help="自然语言输入文本")
    return parser.parse_args()


def load_input_text(args: argparse.Namespace) -> str:
    if args.text:
        return args.text.strip()
    return sys.stdin.read().strip()


def strip_wrapping_quotes(value: str) -> str:
    value = value.strip().strip("，,；;。")
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1].strip()
    return value.strip()


def extract_field(text: str, aliases: tuple[str, ...]) -> str | None:
    alias_pattern = "|".join(re.escape(alias) for alias in aliases)
    pattern = re.compile(
        rf"(?:{alias_pattern})\s*[:：]\s*(?P<value>\"[^\"]*\"|'[^']*'|[^,，;\n]+)",
        re.IGNORECASE,
    )
    match = pattern.search(text)
    if not match:
        return None
    value = strip_wrapping_quotes(match.group("value"))
    return value or None


def parse_bool(value: str | None, field_name: str, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise ValueError(f"{field_name} 无法识别: {sanitize_text(value)}")


def parse_user_fields(text: str) -> dict:
    parsed: dict[str, object] = {}
    for field_name, aliases in FIELD_PATTERNS.items():
        value = extract_field(text, aliases)
        if value is not None:
            parsed[field_name] = value

    parsed["garmin_is_cn"] = parse_bool(parsed.get("garmin_is_cn"), "garmin_is_cn", False)
    parsed["enabled"] = parse_bool(parsed.get("enabled"), "enabled", True)

    pushplus_token = strip_wrapping_quotes(str(parsed.get("pushplus_token", "") or "")) or None
    friend_token = strip_wrapping_quotes(str(parsed.get("friend_token", "") or "")) or None
    push_mode = parse_push_mode(parsed.get("push_mode"), friend_token=friend_token, pushplus_token=pushplus_token)

    return {
        "name": strip_wrapping_quotes(str(parsed.get("name", "") or "")) or None,
        "garmin_email": strip_wrapping_quotes(str(parsed.get("garmin_email", "") or "")) or None,
        "garmin_password": strip_wrapping_quotes(str(parsed.get("garmin_password", "") or "")) or None,
        "garmin_is_cn": bool(parsed["garmin_is_cn"]),
        "push_mode": push_mode,
        "pushplus_token": pushplus_token,
        "friend_token": friend_token,
        "enabled": bool(parsed["enabled"]),
    }


def get_missing_fields(user: dict, required_fields: tuple[str, ...]) -> list[str]:
    return [field for field in required_fields if not user.get(field)]


def load_users_config() -> dict:
    if not USERS_CONFIG_PATH.exists():
        raise FileNotFoundError(f"users.json 不存在: {USERS_CONFIG_PATH}")
    data = json.loads(USERS_CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("users"), list):
        raise ValueError("users.json 顶层必须是包含 users 数组的对象")
    return data


def normalized_name(value: str) -> str:
    return value.strip().casefold()


def normalized_email(value: str) -> str:
    return value.strip().casefold()


def check_duplicates(existing_users: list[dict], new_user: dict) -> None:
    duplicate_fields = []
    for existing in existing_users:
        if not isinstance(existing, dict):
            continue
        if normalized_name(existing.get("name", "")) == normalized_name(new_user["name"]):
            duplicate_fields.append("name")
        if normalized_email(existing.get("garmin_email", "")) == normalized_email(new_user["garmin_email"]):
            duplicate_fields.append("garmin_email")
    if duplicate_fields:
        unique_fields = []
        for field in duplicate_fields:
            if field not in unique_fields:
                unique_fields.append(field)
        raise ValueError(f"用户已存在，重复字段: {', '.join(unique_fields)}")


def validate_users_payload(payload: dict) -> None:
    if not isinstance(payload, dict):
        raise ValueError("users.json 顶层必须是对象")
    users = payload.get("users")
    if not isinstance(users, list):
        raise ValueError("users.json 的 users 字段必须是数组")
    for index, user in enumerate(users, start=1):
        if not isinstance(user, dict):
            raise ValueError(f"users.json 第 {index} 个用户配置不是对象")
        if not user.get("enabled", True):
            continue
        push_mode = (user.get("push_mode") or "self").strip().lower()
        if push_mode not in {"self", "friend"}:
            raise ValueError(f"users.json 第 {index} 个启用用户 push_mode 无效: {push_mode}")
        missing = [field for field in ("name", "garmin_email", "garmin_password") if not user.get(field)]
        if push_mode == "self" and not user.get("pushplus_token"):
            missing.append("pushplus_token")
        if push_mode == "friend" and not user.get("friend_token"):
            missing.append("friend_token")
        if missing:
            raise ValueError(f"users.json 第 {index} 个启用用户缺少字段: {', '.join(missing)}")


def write_users_config(payload: dict) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    json.loads(serialized)
    validate_users_payload(payload)

    original_text = USERS_CONFIG_PATH.read_text(encoding="utf-8")
    temp_path = USERS_CONFIG_PATH.with_suffix(".json.tmp")
    temp_path.write_text(serialized, encoding="utf-8")
    temp_path.replace(USERS_CONFIG_PATH)

    try:
        app_config.load_users.cache_clear()
        app_config.load_users()
    except Exception:
        USERS_CONFIG_PATH.write_text(original_text, encoding="utf-8")
        app_config.load_users.cache_clear()
        raise


def mask_user_summary(user: dict) -> dict:
    summary = {
        "name": user["name"],
        "garmin_email": mask_email(user["garmin_email"]),
        "push_mode": user.get("push_mode", "self"),
        "garmin_is_cn": user.get("garmin_is_cn", False),
        "enabled": user.get("enabled", True),
    }
    if user.get("pushplus_token"):
        summary["pushplus_token"] = mask_identifier(user["pushplus_token"])
    if user.get("friend_token"):
        summary["friend_token"] = mask_identifier(user["friend_token"])
    return summary


def load_written_user(target_name: str, target_email: str) -> dict | None:
    app_config.load_users.cache_clear()
    for user in app_config.load_users():
        if normalized_name(user["name"]) == normalized_name(target_name):
            return user
        if normalized_email(user["garmin_email"]) == normalized_email(target_email):
            return user
    return None


def load_analysis_summary(user_name: str, state: dict, gm_module, load_package_func) -> tuple[str | None, str | None]:
    record_key = None
    title = None
    if state.get("initial_30d_summary_record_key"):
        record_key = state["initial_30d_summary_record_key"]
        title = gm_module.INITIAL_SUMMARY_TITLE
    elif state.get("initial_7d_summary_record_key"):
        record_key = state["initial_7d_summary_record_key"]
        title = gm_module.INITIAL_7D_SUMMARY_TITLE
    if not record_key:
        return None, None

    payload = load_package_func(user_name, gm_module.INITIAL_SUMMARY_CATEGORY, record_key) or {}
    preview = sanitize_text((payload.get("message_preview") or "").replace("\n", " ").strip())
    if len(preview) > 120:
        preview = preview[:120].rstrip() + "..."
    return title, preview or None


def evaluate_onboarding_success(state: dict, gm_module) -> tuple[bool, str | None]:
    effective_sleep_days = state.get("effective_sleep_days", 0) or 0
    if state.get("initial_30d_summary_sent_at"):
        return True, None
    if state.get("initial_7d_summary_sent_at"):
        return True, None
    if effective_sleep_days >= gm_module.INITIAL_BACKFILL_DAYS:
        return False, "已达到30天分析条件，但第一次用户分析未生成成功"
    if effective_sleep_days >= gm_module.MIN_INITIAL_BACKFILL_DAYS:
        return False, "已达到7天分析条件，但7天初步分析未生成成功"
    if state.get("initial_backfill_completed_at"):
        return True, None
    status = state.get("initial_onboarding_status") or "unknown"
    return False, f"首次接入未完成，当前状态: {status}"


def check_rate_limit_before_onboarding(user: dict, now_ts: float | None = None) -> str | None:
    import garmin_monitor as gm

    backoff_until = gm.get_rate_limit_backoff_until(user.get("name", ""), now_ts=now_ts)
    if not backoff_until:
        return None
    resume = gm.datetime.fromtimestamp(backoff_until, gm.BJ_TZ).strftime("%H:%M")
    return f"Garmin 登录仍在 429 限流退避中，预计 {resume} 后再重试首次接入。用户配置已保存。"


def run_first_onboarding(user: dict) -> dict:
    import garmin_monitor as gm
    from garmin_storage import load_package

    rate_limit_message = check_rate_limit_before_onboarding(user)
    if rate_limit_message:
        return {
            "first_onboarding_success": False,
            "effective_sleep_days": 0,
            "effective_activity_days": 0,
            "current_stage": "Garmin 限流退避中",
            "generated_7d_initial_analysis": False,
            "generated_first_user_analysis": False,
            "observation_only": True,
            "user_stage": "observation",
            "analysis_title": None,
            "analysis_summary": None,
            "failure_reason": rate_limit_message,
        }

    state = gm.load_state(user)
    try:
        client = gm.login_garmin(user)
    except Exception as exc:
        if gm.is_rate_limit_error(exc):
            backoff_until = gm.record_rate_limit_backoff(user.get("name", ""))
            resume = gm.datetime.fromtimestamp(backoff_until, gm.BJ_TZ).strftime("%H:%M")
            return {
                "first_onboarding_success": False,
                "effective_sleep_days": 0,
                "effective_activity_days": 0,
                "current_stage": "Garmin 限流退避中",
                "generated_7d_initial_analysis": False,
                "generated_first_user_analysis": False,
                "observation_only": True,
                "user_stage": "observation",
                "analysis_title": None,
                "analysis_summary": None,
                "failure_reason": f"Garmin 登录触发 429 限流，预计 {resume} 后再重试首次接入。用户配置已保存。",
            }
        raise
    display_name = gm.get_display_name(client, user["name"])
    gm.run_user_cycle(client, user, display_name, state)

    final_state = gm.load_state(user)
    stage_context = gm.build_cold_start_context(final_state)
    current_stage = stage_context["stage_summary"]
    analysis_title, analysis_summary = load_analysis_summary(user["name"], final_state, gm, load_package)
    first_onboarding_success, failure_reason = evaluate_onboarding_success(final_state, gm)

    return {
        "first_onboarding_success": first_onboarding_success,
        "effective_sleep_days": final_state.get("effective_sleep_days", 0) or 0,
        "effective_activity_days": final_state.get("effective_activity_days", 0) or 0,
        "current_stage": current_stage,
        "generated_7d_initial_analysis": bool(final_state.get("initial_7d_summary_sent_at")),
        "generated_first_user_analysis": bool(final_state.get("initial_30d_summary_sent_at")),
        "observation_only": not (
            final_state.get("initial_7d_summary_sent_at") or final_state.get("initial_30d_summary_sent_at")
        ),
        "user_stage": stage_context["user_stage"],
        "analysis_title": analysis_title,
        "analysis_summary": analysis_summary,
        "failure_reason": failure_reason,
    }


def print_json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def build_missing_field_response(parsed_user: dict, missing_field: str) -> dict:
    prompts = {
        PASSWORD_FIELD: "还差 Garmin 密码，请补一下。",
        "pushplus_token": "如果使用自有 Token 推送，还差 PushPlus Token，请补一下。",
        "friend_token": "如果使用好友消息推送，还差好友令牌，请补一下。",
    }
    return {
        "success": False,
        "needs_more_input": True,
        "missing_fields": [missing_field],
        "prompt": prompts.get(missing_field, f"还差 {missing_field}，请补一下。"),
        "user": mask_user_summary(parsed_user),
    }


def write_feishu_run_log(
    user_name: str,
    event_type: str,
    detail: str,
    *,
    message_type: str = "",
    user_stage: str = "",
    error_code: str = "",
) -> None:
    try:
        import garmin_monitor as gm

        resolved_error_code = sanitize_text(error_code or "")
        if not resolved_error_code and any(keyword in detail for keyword in ("失败", "异常", "错误")):
            resolved_error_code = gm.normalize_error_code(detail)
        gm.feishu_write_run_log(
            user_name,
            event_type,
            detail,
            message_type=gm.normalize_message_type(title=message_type),
            user_stage=user_stage,
            error_code=resolved_error_code,
        )
    except Exception:
        pass


def main() -> int:
    args = parse_args()
    text = load_input_text(args)
    if not text:
        print_json({"success": False, "error": "缺少输入文本"})
        return 1

    try:
        parsed_user = parse_user_fields(text)
        missing = get_missing_fields(parsed_user, REQUIRED_FIELDS)
        if missing:
            raise ValueError(f"缺少字段: {', '.join(missing)}")
        if not parsed_user.get(PASSWORD_FIELD):
            print_json(build_missing_field_response(parsed_user, PASSWORD_FIELD))
            return 0
        conditional_missing = get_conditional_missing_fields(parsed_user)
        if conditional_missing:
            print_json(build_missing_field_response(parsed_user, conditional_missing[0]))
            return 0
        users_config = load_users_config()
        existing_users = users_config.get("users", [])
        check_duplicates(existing_users, parsed_user)
    except Exception as exc:
        print_json({"success": False, "error": sanitize_text(str(exc))})
        return 1

    updated_config = dict(users_config)
    updated_config["users"] = [
        *existing_users,
        {
            "name": parsed_user["name"],
            "garmin_email": parsed_user["garmin_email"],
            "garmin_password": parsed_user["garmin_password"],
            "garmin_is_cn": parsed_user["garmin_is_cn"],
            "push_mode": parsed_user["push_mode"],
            "pushplus_token": parsed_user["pushplus_token"],
            "friend_token": parsed_user["friend_token"],
            "enabled": parsed_user["enabled"],
        },
    ]

    try:
        write_users_config(updated_config)
    except Exception as exc:
        print_json({"success": False, "error": sanitize_text(f"users.json 写入或校验失败: {exc}")})
        return 1

    result = {
        "success": True,
        "user_add_success": True,
        "json_validated": True,
        "user": mask_user_summary(parsed_user),
    }
    write_feishu_run_log(
        parsed_user["name"],
        "用户新增",
        (
            f"新增用户完成，Garmin 邮箱 {mask_email(parsed_user['garmin_email'])}，推送模式 {parsed_user['push_mode']}，"
            + ("已启用。" if parsed_user["enabled"] else "未启用。")
        ),
        user_stage="observation" if parsed_user["enabled"] else "",
    )

    if not parsed_user["enabled"]:
        result.update(
            {
                "first_onboarding_success": False,
                "current_stage": "用户未启用，未启动首次接入",
                "generated_7d_initial_analysis": False,
                "generated_first_user_analysis": False,
                "observation_only": False,
            }
        )
        print_json(result)
        return 0

    try:
        written_user = load_written_user(parsed_user["name"], parsed_user["garmin_email"])
        if not written_user:
            raise RuntimeError("新增用户已写入，但未能从启用用户列表中重新加载")
        onboarding_result = run_first_onboarding(written_user)
    except Exception as exc:
        result.update(
            {
                "success": False,
                "first_onboarding_success": False,
                "error": sanitize_text(str(exc)),
            }
        )
        write_feishu_run_log(
            parsed_user["name"],
            "首次接入",
            f"首次接入失败：{sanitize_text(str(exc))}",
            user_stage="observation",
            error_code="unknown",
        )
        print_json(result)
        return 1

    result.update(onboarding_result)
    write_feishu_run_log(
        parsed_user["name"],
        "首次接入",
        (
            "首次接入已完成。"
            if not onboarding_result.get("failure_reason")
            else f"首次接入未完成：{sanitize_text(onboarding_result['failure_reason'])}"
        ),
        message_type=onboarding_result.get("analysis_title") or "",
        user_stage=onboarding_result.get("user_stage") or "",
        error_code="" if not onboarding_result.get("failure_reason") else "unknown",
    )
    if result.get("failure_reason") is None:
        result.pop("failure_reason", None)
    if result.get("analysis_title") is None:
        result.pop("analysis_title", None)
    if result.get("analysis_summary") is None:
        result.pop("analysis_summary", None)
    if onboarding_result.get("failure_reason"):
        result["success"] = False
        result["error"] = onboarding_result["failure_reason"]
        print_json(result)
        return 1

    print_json(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
