#!/usr/bin/env python3
"""本地开发/演示服务器

不依赖远端 ICU API、不调 LLM，所有数据用 mock。
让你能在本地浏览器里点遍所有功能（问卷、设置面板、画像查看、状态卡、对话）。

用法：
    cd /path/to/repo
    pip install fastapi uvicorn
    python3 scripts/dev_server.py

然后浏览器打开：
    http://localhost:8765/chat/

数据持久化在 dev_data/ 目录（profile / portrait 都写在这里），
关闭后再次启动会保留之前的填写内容。删除 dev_data/ 即可重置。
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import StreamingResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel
    import uvicorn
except ImportError:
    print("缺少依赖。请先运行：pip install fastapi uvicorn")
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parent.parent
PUBLIC_DIR = REPO_ROOT / "public"
APP_DIR = REPO_ROOT / "app"
DEV_DATA_DIR = REPO_ROOT / "dev_data"
DEV_DATA_DIR.mkdir(exist_ok=True)

PROFILE_PATH = DEV_DATA_DIR / "user_onboarding_profile.json"
PORTRAIT_PATH = DEV_DATA_DIR / "user_portrait.md"
STATS_PATH = DEV_DATA_DIR / "user_portrait_stats.json"

# ──────── Mock 数据 ────────────────────────────────────────────

MOCK_STATUS = {
    "metrics": {
        "hrv_rmssd": 38.2,
        "hrv_status": "BALANCED",
        "sleep_score": 78,
        "sleep_seconds": 26400,
        "sleep_quality": "GOOD",
        "resting_hr": 52,
        "readiness": 72,
        "ctl": 51,
        "atl": 47,
        "tsb": 4,
        "cycling_ftp": 250,
        "vo2max": 52,
    },
    "date": date.today().isoformat(),
}

MOCK_STATS = {
    "period_days": 90,
    "data_count": {"sleep": 87, "cycling": 38},
    "first_date": (date.today() - timedelta(days=87)).isoformat(),
    "last_date": date.today().isoformat(),
    "hrv": {"avg": 38.2, "min": 28.5, "max": 47.8, "p10": 33.0, "p90": 44.5},
    "rhr": {"avg": 52.3, "min": 48, "max": 58},
    "sleep": {
        "avg_secs": 25800,
        "avg_score": 76.5,
        "below_6h_count": 8,
        "above_8h_count": 12,
        "total_records": 87,
    },
    "ctl": {"current": 51.0, "avg": 48.2, "peak": 62.5, "low": 35.1},
    "atl": {"current": 47.0, "avg": 44.5, "peak": 68.3},
    "tsb": {"current": 4.0},
    "readiness": {"avg": 67.8, "low_days": 5, "high_days": 32},
    "training": {
        "total_rides": 38,
        "rides_per_week": 3.2,
        "weekly_tss_avg": 320,
        "avg_duration_min": 85,
        "max_duration_min": 180,
        "weekday_top": [["周二", 9], ["周六", 9], ["周日", 8], ["周四", 7], ["周三", 5]],
        "long_rides_count": 12,
        "long_ride_typical_days": ["周六"],
    },
}

EMPTY_PROFILE = {
    "personal_info": {"nickname": None, "gender": None, "age": None, "height_cm": None, "weight_kg": None},
    "sport": None,
    "goal": {"type": None, "race_info": None},
    "schedule": {"days_per_week": None, "training_days": [], "long_session_days": []},
    "coaching_style": None,
    "completed": False,
}


# ──────── 工具函数 ─────────────────────────────────────────────


def _load_profile() -> dict:
    if PROFILE_PATH.exists():
        try:
            return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return dict(EMPTY_PROFILE)


def _save_profile(data: dict):
    PROFILE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _generate_portrait(profile: dict):
    """用 portrait_builder 生成画像（数据源用 MOCK_STATS，问卷用真实 profile）。"""
    sys.path.insert(0, str(APP_DIR))
    try:
        import portrait_builder as pb
        # 把所有路径改到 dev_data/ 下
        pb.DATA_DIR = DEV_DATA_DIR
        pb.ICU_SLEEP_DIR = DEV_DATA_DIR / "icu_sleep"
        pb.ICU_CYCLING_DIR = DEV_DATA_DIR / "icu_cycling"
        pb.ONBOARDING_PATH = PROFILE_PATH
        pb.PORTRAIT_PATH = PORTRAIT_PATH
        pb.STATS_PATH = STATS_PATH
        # 直接用 MOCK_STATS 渲染（不读 ICU 文件）
        md = pb.render_markdown(MOCK_STATS, profile)
        PORTRAIT_PATH.write_text(md, encoding="utf-8")
        STATS_PATH.write_text(json.dumps(MOCK_STATS, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[portrait] 已更新 {PORTRAIT_PATH.name}")
    except Exception as exc:
        print(f"[portrait] 生成失败: {exc}")


# 启动时确保 stats 文件存在（让首次问卷就能读到 mock 数据）
if not STATS_PATH.exists():
    STATS_PATH.write_text(json.dumps(MOCK_STATS, ensure_ascii=False, indent=2), encoding="utf-8")


# ──────── FastAPI 应用 ─────────────────────────────────────────

app = FastAPI(title="Garmin Assistant Dev Server")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/api/health")
async def health():
    return {"status": "ok", "mode": "dev"}


@app.get("/api/status")
async def status():
    return MOCK_STATUS


@app.get("/api/profile")
async def get_profile():
    return _load_profile()


class ProfilePayload(BaseModel):
    personal_info: dict | None = None
    sport: str | None = None
    goal: dict | None = None
    schedule: dict | None = None
    coaching_style: str | None = None
    completed: bool | None = None


@app.post("/api/profile")
async def save_profile(payload: ProfilePayload):
    data = _load_profile()
    if "personal_info" not in data:
        data["personal_info"] = dict(EMPTY_PROFILE["personal_info"])
    if payload.personal_info is not None:
        data["personal_info"].update(payload.personal_info)
    for f in ("sport", "goal", "schedule", "coaching_style", "completed"):
        v = getattr(payload, f, None)
        if v is not None:
            data[f] = v
    _save_profile(data)
    _generate_portrait(data)
    return {"ok": True}


@app.get("/api/portrait")
async def get_portrait():
    if PORTRAIT_PATH.exists():
        return {"markdown": PORTRAIT_PATH.read_text(encoding="utf-8")}
    return {"markdown": ""}


@app.get("/api/portrait/stats")
async def get_portrait_stats():
    return MOCK_STATS


@app.post("/api/portrait/refresh")
async def refresh_portrait():
    _generate_portrait(_load_profile())
    return {"ok": True}


class ChatRequest(BaseModel):
    messages: list


@app.post("/api/chat")
async def chat(req: ChatRequest):
    """模拟流式回复，用于演示对话 UI。不接 LLM。"""
    last = ""
    if req.messages:
        m = req.messages[-1]
        last = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")

    profile = _load_profile()
    nickname = (profile.get("personal_info") or {}).get("nickname") or "你"
    style = profile.get("coaching_style") or ""
    style_label = {"strict": "严师直接", "data": "数据理性", "gentle": "鼓励温和", "friend": "朋友平等"}.get(style, "中立")

    parts = [
        f"嗨{nickname}，这里是本地演示服务器（未接入真实 LLM）。\n\n",
        f"**你刚才问**：{last}\n\n",
        f"**当前 mock 数据**：HRV 38.2ms（基线 38），TSB +4 状态良好，CTL 51 处于体能积累期。\n\n",
    ]
    if style_label != "中立":
        parts.append(f"**回复风格**：将按你设置的「{style_label}」风格回答。\n\n")
    if "状态" in last or "今天" in last:
        parts.append("结合你 90 天基线（HRV 33–46）来看，今天属于正常区间，可以正常训练。")
    elif "训练" in last:
        parts.append("基于你实际每周 3.2 次的频次，今天如果没排训练就别勉强。要练的话 Z2 60–90 分钟即可。")
    elif "睡眠" in last or "HRV" in last:
        parts.append("近 90 天有 8 天睡眠不足 6 小时，这点要注意。HRV 38.2 在你基线均值附近。")
    else:
        parts.append("完整 AI 对话需要部署到带 LLM API 的服务器。本地 dev server 仅用于 UI 交互演示。")

    text = "".join(parts)

    def gen():
        for ch in text:
            yield f'data: {json.dumps({"content": ch}, ensure_ascii=False)}\n\n'
            time.sleep(0.012)
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/reports")
async def reports(days: int = 30):
    return {"reports": []}


@app.get("/api/reports/{report_date}")
async def report_detail(report_date: str, type: str = ""):
    raise HTTPException(404, "演示模式无报告数据")


@app.post("/api/gpx-parse")
async def gpx_parse():
    raise HTTPException(501, "GPX 解析在 dev server 未启用")


# 静态资源最后挂载（API 路径优先匹配）
app.mount("/", StaticFiles(directory=str(PUBLIC_DIR), html=True), name="public")


# ──────── 入口 ─────────────────────────────────────────────────


def _print_banner(host: str, port: int):
    line = "═" * 58
    print(line)
    print("  Garmin Assistant · 本地开发服务器（Dev Mode）")
    print(line)
    print(f"  数据目录    : {DEV_DATA_DIR}")
    print(f"  浏览器打开  : http://{host}:{port}/chat/")
    print(f"  停止服务    : Ctrl+C")
    print(line)
    print()


if __name__ == "__main__":
    HOST, PORT = "127.0.0.1", 8765
    _print_banner(HOST, PORT)
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
