#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, time as dt_time, timedelta, timezone
from pathlib import Path

import requests


PROJECT_ROOT = Path("/root/garmin_assistant")
APP_DIR = PROJECT_ROOT / "app"
REPORT_DIR = PROJECT_ROOT / "reports" / "daily"
BJ_TZ = timezone(timedelta(hours=8))

if APP_DIR.exists() and str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import garmin_monitor as gm
from app_config import sanitize_text


TEST_MARKERS = (
    "字段自动填充验证",
    "字段顺序校验",
    "测试：飞书运行日志写入验证",
)

TITLE_PATTERNS = (
    re.compile(r"标题[:：]\s*(?P<title>[^|]+)"),
    re.compile(r"推送[「\"](?P<title>.+?)[」\"]"),
)
ACTIVITY_PATTERN = re.compile(r"活动[:：]\s*(?P<activity>[^|]+)")
FAILURE_REASON_PATTERNS = (
    re.compile(r"推送失败[:：]\s*(?P<reason>.+)"),
    re.compile(r"推送异常[:：]\s*(?P<reason>.+)"),
    re.compile(r"失败[:：]\s*(?P<reason>.+)"),
    re.compile(r"异常[:：]\s*(?P<reason>.+)"),
)


def bj_now() -> datetime:
    return datetime.now(BJ_TZ)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成佳明健康助手当日运行日报")
    parser.add_argument("--date", help="目标日期，格式 YYYY-MM-DD；默认今天")
    parser.add_argument("--output-dir", default=str(REPORT_DIR), help="日报输出目录")
    parser.add_argument("--json", action="store_true", help="额外输出 JSON 文件")
    return parser.parse_args()


def ensure_date(value: str | None) -> str:
    if value:
        datetime.strptime(value, "%Y-%m-%d")
        return value
    return bj_now().strftime("%Y-%m-%d")


def parse_record_datetime(date_text: str, time_text: str) -> datetime | None:
    if not date_text:
        return None
    raw_time = time_text or "00:00:00"
    try:
        return datetime.strptime(f"{date_text} {raw_time}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=BJ_TZ)
    except ValueError:
        try:
            return datetime.strptime(date_text, "%Y-%m-%d").replace(tzinfo=BJ_TZ)
        except ValueError:
            return None


def fetch_bitable_records(table_id: str) -> list[dict]:
    token = gm.get_feishu_token()
    if not token:
        raise RuntimeError("飞书 token 获取失败")

    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{gm.FEISHU_APP_TOKEN}/tables/{table_id}/records"
    headers = {"Authorization": f"Bearer {token}"}

    records: list[dict] = []
    page_token = ""
    while True:
        params = {"page_size": 500}
        if page_token:
            params["page_token"] = page_token
        response = requests.get(url, headers=headers, params=params, timeout=30)
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise RuntimeError(f"飞书读取失败: {payload.get('msg')}")
        data = payload.get("data") or {}
        records.extend((item.get("fields") or {}) for item in data.get("items") or [])
        if not data.get("has_more"):
            break
        page_token = data.get("page_token") or ""
        if not page_token:
            break
    return records


def contains_test_marker(*values: str) -> bool:
    haystack = " ".join(value for value in values if value)
    return any(marker in haystack for marker in TEST_MARKERS)


def parse_title_from_detail(detail: str) -> str:
    for pattern in TITLE_PATTERNS:
        match = pattern.search(detail)
        if match:
            return sanitize_text(match.group("title").strip())
    return ""


def parse_activity_from_detail(detail: str) -> str:
    match = ACTIVITY_PATTERN.search(detail)
    if not match:
        return ""
    return sanitize_text(match.group("activity").strip())


def parse_failure_reason(text: str) -> str:
    clean_text = sanitize_text(text or "")
    for pattern in FAILURE_REASON_PATTERNS:
        match = pattern.search(clean_text)
        if match:
            return sanitize_text(match.group("reason").strip())
    return clean_text


def normalize_push_record(fields: dict) -> dict:
    content = sanitize_text(fields.get("推送内容全文") or "")
    title = sanitize_text(fields.get("消息标题") or "")
    activity_type = sanitize_text(fields.get("活动类型") or "")
    failure_reason = sanitize_text(fields.get("失败原因") or "")
    record = {
        "date": sanitize_text(fields.get("日期") or ""),
        "time": sanitize_text(fields.get("时间") or ""),
        "user": sanitize_text(fields.get("用户") or ""),
        "message_type": sanitize_text(fields.get("消息类型") or ""),
        "title": title,
        "activity_type": activity_type,
        "distance_duration": sanitize_text(fields.get("距离/时长") or ""),
        "status": sanitize_text(fields.get("推送状态") or ""),
        "content": content,
        "user_stage": sanitize_text(fields.get("用户阶段") or ""),
        "is_first_onboarding": sanitize_text(fields.get("是否首次接入阶段") or ""),
        "failure_reason": failure_reason,
    }
    if not record["message_type"]:
        record["message_type"] = sanitize_text(gm.normalize_message_type(title=title, activity_type=activity_type))
    if not record["failure_reason"] and record["status"] != "成功":
        record["failure_reason"] = parse_failure_reason(content)
    record["dt"] = parse_record_datetime(record["date"], record["time"])
    return record


def normalize_log_record(fields: dict) -> dict:
    event_type = sanitize_text(fields.get("事件类型") or "")
    normalized_event_type = sanitize_text(gm.normalize_run_log_event_type(event_type) or event_type)
    detail = sanitize_text(fields.get("详情") or "")
    title = parse_title_from_detail(detail)
    activity_type = parse_activity_from_detail(detail)
    message_type = sanitize_text(fields.get("关联消息类型") or "")
    if not message_type:
        message_type = sanitize_text(gm.normalize_message_type(title=title, activity_type=activity_type))
    record = {
        "date": sanitize_text(fields.get("日期") or ""),
        "time": sanitize_text(fields.get("时间") or ""),
        "event_type": normalized_event_type,
        "raw_event_type": event_type,
        "user": sanitize_text(fields.get("用户") or ""),
        "detail": detail,
        "message_type": message_type,
        "user_stage": sanitize_text(fields.get("关联用户阶段") or ""),
        "error_code": sanitize_text(fields.get("错误原因标准化") or ""),
        "title": title,
        "activity_type": activity_type,
    }
    if not record["error_code"] and normalized_event_type == "推送失败":
        record["error_code"] = sanitize_text(gm.normalize_error_code(detail))
    record["dt"] = parse_record_datetime(record["date"], record["time"])
    return record


def filter_push_records(records: list[dict], target_date: str) -> list[dict]:
    result = []
    for fields in records:
        record = normalize_push_record(fields)
        if record["date"] != target_date:
            continue
        if contains_test_marker(record["title"], record["content"], record["failure_reason"]):
            continue
        result.append(record)
    return sorted(result, key=lambda item: item["dt"] or datetime.min.replace(tzinfo=BJ_TZ))


def filter_log_records(records: list[dict], target_date: str) -> list[dict]:
    result = []
    for fields in records:
        record = normalize_log_record(fields)
        if record["date"] != target_date:
            continue
        if contains_test_marker(record["detail"], record["title"]):
            continue
        result.append(record)
    return sorted(result, key=lambda item: item["dt"] or datetime.min.replace(tzinfo=BJ_TZ))


def enrich_push_records(push_records: list[dict], log_records: list[dict]):
    logs_by_key: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for log in log_records:
        if log["event_type"] not in {"推送成功", "推送失败"}:
            continue
        logs_by_key[(log["user"], log["event_type"])].append(log)

    used_logs: set[tuple[str, str, datetime | None]] = set()
    for key in logs_by_key:
        logs_by_key[key].sort(key=lambda item: item["dt"] or datetime.min.replace(tzinfo=BJ_TZ))

    for push in push_records:
        event_type = "推送成功" if push["status"] == "成功" else "推送失败"
        candidates = []
        for log in logs_by_key.get((push["user"], event_type), []):
            log_key = (log["user"], log["event_type"], log["dt"])
            if log_key in used_logs:
                continue
            if push["dt"] is None or log["dt"] is None:
                continue
            diff = abs((log["dt"] - push["dt"]).total_seconds())
            if diff > 120:
                continue
            candidates.append((diff, log))
        if not candidates:
            continue
        _, matched_log = min(candidates, key=lambda item: item[0])
        used_logs.add((matched_log["user"], matched_log["event_type"], matched_log["dt"]))

        if not push["title"]:
            push["title"] = matched_log["title"]
        if not push["activity_type"]:
            push["activity_type"] = matched_log["activity_type"]
        if not push["message_type"]:
            push["message_type"] = matched_log["message_type"]
        if not push["user_stage"]:
            push["user_stage"] = matched_log["user_stage"]
        if push["status"] != "成功" and not push["failure_reason"]:
            push["failure_reason"] = parse_failure_reason(matched_log["detail"])

        if not push["message_type"]:
            push["message_type"] = sanitize_text(
                gm.normalize_message_type(title=push["title"], activity_type=push["activity_type"])
            )


def describe_counter(counter: Counter) -> list[str]:
    if not counter:
        return ["无"]
    return [f"{name or '未标注'}：{count}" for name, count in counter.most_common()]


def record_matches_message(failure: dict, candidate: dict) -> bool:
    if failure["user"] != candidate["user"]:
        return False
    failure_type = failure["message_type"] or sanitize_text(
        gm.normalize_message_type(title=failure["title"], activity_type=failure["activity_type"])
    )
    candidate_type = candidate["message_type"] or sanitize_text(
        gm.normalize_message_type(title=candidate["title"], activity_type=candidate["activity_type"])
    )
    if failure_type and candidate_type and failure_type != candidate_type:
        return False
    if failure["title"] and candidate["title"] and failure["title"] == candidate["title"]:
        return True
    return bool(failure_type == candidate_type or not failure_type or not candidate_type)


def resolve_push_failure(failure: dict, push_records: list[dict], log_records: list[dict]) -> tuple[bool, str]:
    for record in push_records:
        if record["status"] != "成功":
            continue
        if failure["dt"] and record["dt"] and record["dt"] <= failure["dt"]:
            continue
        if record_matches_message(failure, record):
            return True, f"{record['time']} 已补发成功"

    if (failure["message_type"] or failure["activity_type"]) == "睡眠晨报":
        for log in log_records:
            if log["event_type"] != "跟踪巡检":
                continue
            if failure["dt"] and log["dt"] and log["dt"] <= failure["dt"]:
                continue
            if "已恢复" in log["detail"]:
                return True, f"{log['time']} 跟踪巡检已确认恢复"

    return False, "未见恢复记录"


def inspection_is_success(log: dict) -> bool:
    detail = log["detail"]
    if log["event_type"] == "晨报巡检":
        return "全部成功推送" in detail
    if log["event_type"] == "跟踪巡检":
        return "已恢复" in detail
    if log["event_type"] == "日终巡检":
        return "未发现影响明早运行的问题" in detail
    return False


def should_include_inspection_anomaly(log: dict) -> bool:
    if log["event_type"] == "日终巡检" and log["time"] < "20:00:00":
        return False
    return True


def build_inspection_summary(log_records: list[dict], target_date: str) -> dict:
    now = bj_now()
    target_is_today = target_date == now.strftime("%Y-%m-%d")
    now_time = now.timetz().replace(tzinfo=None)

    grouped: dict[str, list[dict]] = {"晨报巡检": [], "跟踪巡检": [], "日终巡检": []}
    for log in log_records:
        if log["event_type"] not in grouped:
            continue
        grouped[log["event_type"]].append(log)

    morning_logs = grouped["晨报巡检"]
    followup_logs = grouped["跟踪巡检"]
    night_logs = grouped["日终巡检"]

    morning_result = morning_logs[0] if morning_logs else None

    night_candidates = [log for log in night_logs if log["time"] >= "20:00:00"]
    if target_is_today and now_time < dt_time(21, 0):
        night_candidates = [log for log in night_candidates if log["time"] >= "21:00:00"]
    night_result = night_candidates[-1] if night_candidates else None

    return {
        "morning": morning_result,
        "followup": followup_logs,
        "night": night_result,
    }


def build_failure_sections(push_records: list[dict], log_records: list[dict]) -> dict:
    failed_pushes = []
    for push in push_records:
        if push["status"] == "成功":
            continue
        recovered, recovery_note = resolve_push_failure(push, push_records, log_records)
        failed_pushes.append(
            {
                "user": push["user"],
                "message_type": push["message_type"] or push["activity_type"] or "未标注",
                "title": push["title"],
                "time": push["time"],
                "failure_reason": push["failure_reason"] or "未记录失败原因",
                "recovered": recovered,
                "recovery_note": recovery_note,
            }
        )

    other_anomalies = []
    for log in log_records:
        if log["event_type"] in {"晨报巡检", "跟踪巡检", "日终巡检"}:
            if not should_include_inspection_anomaly(log):
                continue
            if inspection_is_success(log):
                continue
            other_anomalies.append(
                {
                    "event_type": log["event_type"],
                    "time": log["time"],
                    "detail": log["detail"],
                }
            )
        elif log["event_type"] == "推送失败":
            continue
        elif log["error_code"] or any(keyword in log["detail"] for keyword in ("失败", "异常", "错误", "失效")):
            other_anomalies.append(
                {
                    "event_type": log["event_type"],
                    "time": log["time"],
                    "detail": log["detail"],
                }
            )

    return {
        "failed_pushes": failed_pushes,
        "other_anomalies": other_anomalies,
    }


def extract_tomorrow_focus(failure_sections: dict, inspection_summary: dict) -> dict:
    persistent_failure_users = sorted(
        {
            item["user"]
            for item in failure_sections["failed_pushes"]
            if not item["recovered"]
        }
    )
    unresolved_issues = [
        f"{item['user']}｜{item['message_type']}｜{item['failure_reason']}"
        for item in failure_sections["failed_pushes"]
        if not item["recovered"]
    ]
    risk_points = []

    for item in failure_sections["other_anomalies"]:
        risk_points.append(f"{item['event_type']}｜{item['detail']}")

    night_result = inspection_summary.get("night")
    if night_result and not inspection_is_success(night_result):
        risk_points.append(f"日终巡检｜{night_result['detail']}")

    followup_logs = inspection_summary.get("followup") or []
    if followup_logs:
        latest_followup = followup_logs[-1]
        if not inspection_is_success(latest_followup):
            risk_points.append(f"跟踪巡检｜{latest_followup['detail']}")

    return {
        "persistent_failure_users": persistent_failure_users,
        "unresolved_issues": unresolved_issues,
        "risk_points": list(dict.fromkeys(risk_points)),
    }


def build_conclusion(push_records: list[dict], failure_sections: dict, inspection_summary: dict, tomorrow_focus: dict) -> list[str]:
    total_pushes = len(push_records)
    success_count = sum(1 for item in push_records if item["status"] == "成功")
    failure_count = total_pushes - success_count
    users = len({item["user"] for item in push_records if item["user"]})
    unresolved_count = len(tomorrow_focus["unresolved_issues"])
    morning_ok = inspection_summary.get("morning") and inspection_is_success(inspection_summary["morning"])
    night_ok = inspection_summary.get("night") and inspection_is_success(inspection_summary["night"])

    sentence_one = f"今日共记录 {total_pushes} 条推送，涉及 {users} 位用户；成功 {success_count} 条，失败 {failure_count} 条。"

    if unresolved_count == 0 and not failure_sections["other_anomalies"] and (morning_ok or not inspection_summary.get("morning")) and (night_ok or not inspection_summary.get("night")):
        sentence_two = "截至日报生成时，未发现明确的未恢复问题；目前看不出会直接影响明天运行的异常。"
    else:
        sentence_two = (
            f"截至日报生成时，仍有 {unresolved_count} 项未恢复问题；"
            + ("可能会影响明天运行。" if unresolved_count or tomorrow_focus["risk_points"] else "建议继续观察。")
        )
    return [sentence_one, sentence_two]


def build_report_payload(target_date: str) -> dict:
    raw_push_records = fetch_bitable_records(gm.FEISHU_PUSH_TABLE_ID)
    raw_log_records = fetch_bitable_records(gm.FEISHU_LOG_TABLE_ID)

    push_records = filter_push_records(raw_push_records, target_date)
    log_records = filter_log_records(raw_log_records, target_date)
    enrich_push_records(push_records, log_records)

    for push in push_records:
        if not push["message_type"]:
            push["message_type"] = sanitize_text(
                gm.normalize_message_type(title=push["title"], activity_type=push["activity_type"])
            )

    message_distribution = Counter(item["message_type"] or item["activity_type"] or "未标注" for item in push_records)
    push_overview = {
        "total_messages": len(push_records),
        "user_count": len({item["user"] for item in push_records if item["user"]}),
        "message_distribution": dict(message_distribution),
        "success_count": sum(1 for item in push_records if item["status"] == "成功"),
        "failure_count": sum(1 for item in push_records if item["status"] != "成功"),
    }

    failure_sections = build_failure_sections(push_records, log_records)
    inspection_summary = build_inspection_summary(log_records, target_date)
    tomorrow_focus = extract_tomorrow_focus(failure_sections, inspection_summary)
    conclusion = build_conclusion(push_records, failure_sections, inspection_summary, tomorrow_focus)

    def serialize_inspection(record: dict | None) -> dict | None:
        if not record:
            return None
        return {
            "date": record["date"],
            "time": record["time"],
            "event_type": record["event_type"],
            "detail": record["detail"],
        }

    return {
        "date": target_date,
        "generated_at": bj_now().strftime("%Y-%m-%d %H:%M:%S"),
        "today_conclusion": conclusion,
        "push_overview": push_overview,
        "failures_and_anomalies": failure_sections,
        "inspection_results": inspection_summary,
        "inspection_results_json": {
            "morning": serialize_inspection(inspection_summary["morning"]),
            "followup": [serialize_inspection(item) for item in inspection_summary["followup"]],
            "night": serialize_inspection(inspection_summary["night"]),
        },
        "tomorrow_focus": tomorrow_focus,
        "all_push_messages": [
            {
                "user": item["user"],
                "message_type": item["message_type"] or item["activity_type"],
                "message_title": item["title"],
                "push_time": item["time"],
                "push_status": item["status"],
                "content": item["content"],
                "failure_reason": item["failure_reason"],
                "user_stage": item["user_stage"],
                "is_first_onboarding": item["is_first_onboarding"],
            }
            for item in push_records
        ],
        "source_counts": {
            "push_records": len(push_records),
            "run_logs": len(log_records),
        },
    }


def render_inspection_line(label: str, record: dict | None) -> str:
    if not record:
        return f"- {label}：今日无记录。"
    return f"- {label}（{record['time']}）：{record['detail']}"


def render_followup_lines(records: list[dict]) -> list[str]:
    if not records:
        return ["- followup 跟踪：今日无触发记录。"]
    return [f"- followup 跟踪（{record['time']}）：{record['detail']}" for record in records]


def build_markdown(payload: dict) -> str:
    overview = payload["push_overview"]
    failures = payload["failures_and_anomalies"]
    inspection = payload["inspection_results"]
    tomorrow = payload["tomorrow_focus"]

    lines = [
        f"# 佳明健康助手当日运行日报",
        "",
        f"- 日期：{payload['date']}",
        f"- 生成时间：{payload['generated_at']}",
        "",
        "## 1. 今日结论",
    ]
    lines.extend([f"- {sentence}" for sentence in payload["today_conclusion"]])

    lines.extend(
        [
            "",
            "## 2. 推送概况",
            f"- 今日总推送：{overview['total_messages']} 条",
            f"- 涉及用户：{overview['user_count']} 位",
            f"- 成功：{overview['success_count']} 条",
            f"- 失败：{overview['failure_count']} 条",
            "- 消息类型分布：",
        ]
    )
    lines.extend([f"  - {item}" for item in describe_counter(Counter(overview["message_distribution"]))])

    lines.extend(["", "## 3. 失败与异常"])
    if failures["failed_pushes"]:
        lines.append("### 3.1 推送失败")
        for item in failures["failed_pushes"]:
            title_text = item["title"] or "未记录标题"
            recovery_state = item["recovery_note"] if item["recovered"] else f"未恢复，{item['recovery_note']}"
            lines.append(
                f"- {item['time']}｜{item['user']}｜{item['message_type']}｜{title_text}｜原因：{item['failure_reason']}｜恢复情况：{recovery_state}"
            )
    else:
        lines.append("- 今日未记录推送失败。")

    anomaly_lines = [item for item in failures["other_anomalies"] if item["event_type"] not in {"晨报巡检", "跟踪巡检", "日终巡检"}]
    inspection_anomaly_lines = [item for item in failures["other_anomalies"] if item["event_type"] in {"晨报巡检", "跟踪巡检", "日终巡检"}]
    if anomaly_lines:
        lines.append("### 3.2 其他异常日志")
        for item in anomaly_lines:
            lines.append(f"- {item['time']}｜{item['event_type']}｜{item['detail']}")
    if inspection_anomaly_lines:
        lines.append("### 3.3 巡检异常")
        for item in inspection_anomaly_lines:
            lines.append(f"- {item['time']}｜{item['event_type']}｜{item['detail']}")

    lines.extend(["", "## 4. 巡检结果"])
    lines.append(render_inspection_line("晨报巡检", inspection.get("morning")))
    lines.extend(render_followup_lines(inspection.get("followup") or []))
    lines.append(render_inspection_line("日终巡检", inspection.get("night")))

    lines.extend(["", "## 5. 明日关注点"])
    lines.append(
        "- 持续失败用户："
        + ("、".join(tomorrow["persistent_failure_users"]) if tomorrow["persistent_failure_users"] else "无")
    )
    if tomorrow["unresolved_issues"]:
        lines.append("- 未恢复问题：")
        lines.extend([f"  - {item}" for item in tomorrow["unresolved_issues"]])
    else:
        lines.append("- 未恢复问题：无")
    if tomorrow["risk_points"]:
        lines.append("- 可能影响明天运行的风险点：")
        lines.extend([f"  - {item}" for item in tomorrow["risk_points"]])
    else:
        lines.append("- 可能影响明天运行的风险点：无")

    lines.extend(["", "## 6. 当天全部推送文案明细"])
    if not payload["all_push_messages"]:
        lines.append("- 当天没有可写入日报的推送记录。")
    else:
        for index, item in enumerate(payload["all_push_messages"], start=1):
            lines.extend(
                [
                    f"### {index}. {item['user']}｜{item['message_type'] or '未标注'}｜{item['push_time']}｜{item['push_status']}",
                    f"- 消息标题：{item['message_title'] or '未记录标题'}",
                    f"- 用户阶段：{item['user_stage'] or '未记录'}",
                    f"- 是否首次接入阶段：{item['is_first_onboarding'] or '未记录'}",
                    "- 正文全文：",
                    "```text",
                    item["content"] or "",
                    "```",
                ]
            )
            if item["push_status"] != "成功":
                lines.append(f"- 失败原因：{item['failure_reason'] or '未记录失败原因'}")
            lines.append("")

    return "\n".join(lines).strip() + "\n"


def main() -> int:
    args = parse_args()
    target_date = ensure_date(args.date)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    payload = build_report_payload(target_date)
    markdown = build_markdown(payload)

    md_path = output_dir / f"{target_date}.md"
    md_path.write_text(markdown, encoding="utf-8")

    json_path = output_dir / f"{target_date}.json"
    if args.json:
        json_payload = dict(payload)
        json_payload["inspection_results"] = json_payload.pop("inspection_results_json")
        json_path.write_text(json.dumps(json_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "success": True,
                "date": target_date,
                "markdown_path": str(md_path),
                "json_path": str(json_path) if args.json else "",
                "push_records": payload["source_counts"]["push_records"],
                "run_logs": payload["source_counts"]["run_logs"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
