"""佳明健康助手 Chat API — FastAPI 入口。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 让 chat_api 能 import 上层 app/ 目录
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from openai import OpenAI

from app_config import load_system_config
from data_loader import build_context_for_chat, load_reports_list, load_report_detail

# ── LLM 初始化 ──────────────────────────────────────────────
SYSTEM_CONFIG = load_system_config()
LLM_CONFIG = SYSTEM_CONFIG.get("llm") or {}
llm_client = OpenAI(
    api_key=LLM_CONFIG.get("api_key"),
    base_url=LLM_CONFIG.get("api_base_url"),
)
LLM_MODEL = LLM_CONFIG.get("model", "Pro/moonshotai/Kimi-K2.5")

SYSTEM_PROMPT = """你是"佳明健康助手"，是丛至的专属运动健康 AI 教练。

你的能力：
- 分析 Garmin 手表采集的 HRV、睡眠、血氧、训练负荷、FTP、心率等数据
- 根据身体状态给出今天是否适合训练、适合什么强度的具体建议
- 解读骑行指标（功率、踏频、TSS、IF、左右平衡等）
- 回答关于训练科学、恢复、营养等运动健康问题

回答风格：
- 简洁直接，有数据依据，不说空话
- 给建议时要具体（如"今天 FTP 60% 以内的 Z2 强度，心率不超过 145"）
- 如果当前数据显示状态不佳，要诚实说明原因
- 用中文回答，语气像一个懂数据的朋友，不要太正式

注意：以下是用户最新的健康数据，请基于这些数据回答问题。"""

# ── FastAPI 应用 ──────────────────────────────────────────────
app = FastAPI(title="佳明健康助手 API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]


@app.post("/api/chat")
async def chat(req: ChatRequest):
    """流式对话接口，返回 SSE 格式的 text/event-stream。"""
    health_context = build_context_for_chat()
    system_content = SYSTEM_PROMPT + "\n\n" + health_context

    messages = [{"role": "system", "content": system_content}]
    for m in req.messages:
        messages.append({"role": m.role, "content": m.content})

    def generate():
        try:
            stream = llm_client.chat.completions.create(
                model=LLM_MODEL,
                messages=messages,
                stream=True,
                max_tokens=1500,
                temperature=0.7,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    data = json.dumps({"content": delta}, ensure_ascii=False)
                    yield f"data: {data}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/api/status")
async def status():
    """返回今日健康快照（ICU 优先），供前端首页卡片展示。"""
    from datetime import date, timedelta
    from data_loader import load_daily, _extract_key_metrics, load_icu_sleep_latest, _extract_icu_sleep_metrics
    today = date.today()
    today_data = load_daily(today) or load_daily(today - timedelta(days=1))
    metrics = _extract_key_metrics(today_data or {})
    icu_date = str(today)
    icu_raw = load_icu_sleep_latest(days=2)
    if icu_raw:
        icu_date = icu_raw.get("date", str(today))
    return {"metrics": metrics, "date": icu_date or str(today)}


@app.get("/api/reports")
async def reports(days: int = 30):
    """返回最近 N 天报告列表。"""
    return {"reports": load_reports_list(days)}


@app.get("/api/reports/{report_date}")
async def report_detail(report_date: str, type: str = ""):
    """返回指定日期报告详情，type 可为 icu_sleep / icu_cycling / garmin。"""
    detail = load_report_detail(report_date, report_type=type)
    if not detail:
        raise HTTPException(status_code=404, detail="报告不存在")
    return detail


@app.get("/api/health")
async def health_check():
    return {"status": "ok"}
