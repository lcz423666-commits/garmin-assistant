from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

BJ_TZ = timezone(timedelta(hours=8))
DATA_DIR = Path("/root/garmin_assistant/data/丛至")
GOALS_PATH = DATA_DIR / "daily_goals.json"
OBS_LOG_PATH = DATA_DIR / "observations_log.json"


def _bj_today() -> str:
    return datetime.now(BJ_TZ).date().isoformat()


def _load_json(path: Path) -> list:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_json(path: Path, data: list):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def extract_goals_from_text(text: str, source: str) -> list[dict]:
    """Use LLM to extract forward-looking recommendations with a target date."""
    from llm_helper import LLM_MODEL, client

    today = _bj_today()
    prompt = (
        f"今天是 {today}。\n\n"
        "以下是一段健康分析文本：\n\n"
        f"{text}\n\n"
        "请提取文本中所有指向「今天之后」的具体行动建议（如「明天」「明晚」「本周」「后天」等）。\n"
        "每条建议输出一个 JSON 对象：{\"target_date\": \"YYYY-MM-DD\", \"goal\": \"简洁的建议内容（≤30字）\"}\n"
        "只输出 JSON 数组，无其他文字。没有则输出 []。\n"
        "「今晚」「今天」的建议不提取，只提取明天及之后的。"
    )
    try:
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        raw = (response.choices[0].message.content or "").strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
            raw = "\n".join(inner)
        items = json.loads(raw)
        if not isinstance(items, list):
            return []
        return [
            {
                "target_date": str(item.get("target_date", "")),
                "goal": str(item.get("goal", "")),
                "source": source,
                "created_at": today,
            }
            for item in items
            if item.get("target_date") and item.get("goal")
        ]
    except Exception:
        return []


def save_goals(goals: list[dict]):
    if not goals:
        return
    existing = _load_json(GOALS_PATH)
    existing_keys = {
        (g.get("target_date"), g.get("source"), g.get("goal")) for g in existing
    }
    new_goals = [
        g for g in goals
        if (g.get("target_date"), g.get("source"), g.get("goal")) not in existing_keys
    ]
    if new_goals:
        _save_json(GOALS_PATH, existing + new_goals)


def get_pending_goals(target_date: str | None = None) -> list[dict]:
    """Return goals for a given date (default: today)."""
    date = target_date or _bj_today()
    return [g for g in _load_json(GOALS_PATH) if g.get("target_date") == date]


def append_observation(obs_text: str, source: str):
    """Append a brief factual observation (one per source per day)."""
    today = _bj_today()
    obs_log = _load_json(OBS_LOG_PATH)
    obs_log = [
        o for o in obs_log
        if not (o.get("date") == today and o.get("source") == source)
    ]
    obs_log.append({"date": today, "source": source, "obs": obs_text})
    obs_log = sorted(obs_log, key=lambda x: x.get("date", ""))[-90:]
    _save_json(OBS_LOG_PATH, obs_log)
