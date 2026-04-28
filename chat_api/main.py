"""佳明健康助手 Chat API — FastAPI 入口。"""

from __future__ import annotations

import json
import math
import sys
import urllib.request
from datetime import date as _date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

import gpxpy
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from openai import OpenAI

from app_config import load_system_config
from data_loader import build_context_for_chat, load_reports_list, load_report_detail
from tools import TOOLS, execute_tool

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

app = FastAPI(title="佳明健康助手 API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]


@app.post("/api/chat")
async def chat(req: ChatRequest):
    health_context = build_context_for_chat()
    profile = _read_profile()
    style_hint = COACHING_STYLE_PROMPTS.get(profile.get("coaching_style") or "", "")

    # 优先注入完整的用户画像 Markdown；不存在时退化为简短 profile 摘要
    portrait_path = Path("/root/garmin_assistant/data/congzhi/user_portrait.md")
    portrait_md = ""
    if portrait_path.exists():
        try:
            portrait_md = portrait_path.read_text(encoding="utf-8").strip()
        except Exception:
            portrait_md = ""

    system_content = SYSTEM_PROMPT
    if style_hint:
        system_content += f"\n\n{style_hint}"
    if portrait_md:
        system_content += f"\n\n以下是该用户的长期画像（基于真实历史数据生成），回答时务必参考：\n\n{portrait_md}"
    system_content += "\n\n" + health_context
    messages = [{"role": "system", "content": system_content}]
    for m in req.messages:
        messages.append({"role": m.role, "content": m.content})

    def generate():
        cur_messages = list(messages)
        max_rounds = 4
        for _round in range(max_rounds):
            try:
                stream = llm_client.chat.completions.create(
                    model=LLM_MODEL, messages=cur_messages, tools=TOOLS,
                    tool_choice="auto", stream=True, max_tokens=2000, temperature=0.7,
                )
                content_parts: list[str] = []
                tool_calls_acc: dict[int, dict] = {}
                finish_reason = None
                for chunk in stream:
                    if not chunk.choices: continue
                    choice = chunk.choices[0]
                    finish_reason = choice.finish_reason or finish_reason
                    delta = choice.delta
                    if delta.content:
                        content_parts.append(delta.content)
                        yield f"data: {json.dumps({'content': delta.content}, ensure_ascii=False)}\n\n"
                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in tool_calls_acc:
                                tool_calls_acc[idx] = {"id": "", "name": "", "args": ""}
                            if tc.id: tool_calls_acc[idx]["id"] = tc.id
                            if tc.function:
                                if tc.function.name: tool_calls_acc[idx]["name"] = tc.function.name
                                if tc.function.arguments: tool_calls_acc[idx]["args"] += tc.function.arguments
                if finish_reason == "tool_calls" and tool_calls_acc:
                    tool_names = [tool_calls_acc[i]["name"] for i in sorted(tool_calls_acc)]
                    yield f"data: {json.dumps({'meta': 'querying', 'tools': tool_names}, ensure_ascii=False)}\n\n"
                    tool_calls_list = [
                        {"id": tool_calls_acc[i]["id"], "type": "function",
                         "function": {"name": tool_calls_acc[i]["name"], "arguments": tool_calls_acc[i]["args"]}}
                        for i in sorted(tool_calls_acc)
                    ]
                    cur_messages.append({"role": "assistant", "content": "".join(content_parts) or None, "tool_calls": tool_calls_list})
                    for tc in tool_calls_list:
                        try:
                            args = json.loads(tc["function"]["arguments"] or "{}")
                            result = execute_tool(tc["function"]["name"], args)
                        except Exception as e:
                            result = {"error": str(e)}
                        cur_messages.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(result, ensure_ascii=False)})
                    continue
                break
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
                break
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


# ── GPX 工具 ─────────────────────────────────────────────────

def _haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlam = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _parse_gpx(content: bytes) -> dict:
    gpx = gpxpy.parse(content.decode("utf-8", errors="ignore"))
    raw: list[dict] = []
    for track in gpx.tracks:
        for seg in track.segments:
            for p in seg.points:
                raw.append({"lat": p.latitude, "lon": p.longitude, "ele": p.elevation or 0.0})
    if len(raw) < 2:
        return {"error": "GPX 文件中没有足够的轨迹点"}

    cum: list[float] = [0.0]
    for i in range(1, len(raw)):
        cum.append(cum[-1] + _haversine_m(raw[i-1]["lat"], raw[i-1]["lon"], raw[i]["lat"], raw[i]["lon"]))
    total_m = cum[-1]

    total_gain = total_loss = 0.0
    for i in range(1, len(raw)):
        diff = raw[i]["ele"] - raw[i-1]["ele"]
        if diff > 0: total_gain += diff
        else: total_loss += abs(diff)

    max_ele = max(p["ele"] for p in raw)
    min_ele = min(p["ele"] for p in raw)

    # 50m 窗口坡度采样
    MIN_WIN = 50.0
    gw: list[tuple[float, float]] = []  # (cum_dist_m, grade_pct)
    wi = 0
    for i in range(1, len(raw)):
        if cum[i] - cum[wi] >= MIN_WIN:
            d = cum[i] - cum[wi]
            gw.append((cum[i], (raw[i]["ele"] - raw[wi]["ele"]) / d * 100))
            wi = i

    # 全程坡度分布
    flat_m = easy_m = mod_m = steep_m = 0.0
    for j, (gd, gg) in enumerate(gw):
        sd = gd - (gw[j-1][0] if j > 0 else 0)
        ag = abs(gg)
        if ag <= 2: flat_m += sd
        elif ag <= 5: easy_m += sd
        elif ag <= 9: mod_m += sd
        else: steep_m += sd

    # 分段（每5km，最多25段）
    seg_len_m = max(5000.0, total_m / 25)
    segments = []
    si = sd2 = 0
    for i in range(1, len(raw)):
        if cum[i] - sd2 >= seg_len_m or i == len(raw) - 1:
            pts = raw[si: i + 1]
            seg_d = cum[i] - sd2
            if seg_d < 200:
                si = i; sd2 = cum[i]; continue
            ele_diff = pts[-1]["ele"] - pts[0]["ele"]
            avg_grade = (ele_diff / seg_d) * 100
            ele_gain = sum(max(0.0, pts[j]["ele"] - pts[j-1]["ele"]) for j in range(1, len(pts)))
            # 段内最大坡度（上坡）
            s_d, e_d = cum[si], cum[i]
            seg_grades = [gg for gd, gg in gw if s_d <= gd <= e_d and gg > 0]
            max_grade = max(seg_grades) if seg_grades else 0.0
            segments.append({
                "start_km": round(cum[si] / 1000, 1),
                "end_km": round(cum[i] / 1000, 1),
                "dist_km": round(seg_d / 1000, 1),
                "avg_grade_pct": round(avg_grade, 1),
                "max_grade_pct": round(max_grade, 1),
                "ele_gain_m": round(ele_gain),
                "start_ele_m": round(pts[0]["ele"]),
                "end_ele_m": round(pts[-1]["ele"]),
            })
            si = i; sd2 = cum[i]

    step = max(1, len(raw) // 120)
    profile = [{"d": round(cum[i] / 1000, 2), "e": round(raw[i]["ele"])} for i in range(0, len(raw), step)]

    return {
        "start_lat": raw[0]["lat"],
        "start_lon": raw[0]["lon"],
        "total_dist_km": round(total_m / 1000, 1),
        "total_gain_m": round(total_gain),
        "total_loss_m": round(total_loss),
        "max_ele_m": round(max_ele),
        "min_ele_m": round(min_ele),
        "grade_dist": {
            "flat_km": round(flat_m / 1000, 1),
            "easy_km": round(easy_m / 1000, 1),
            "moderate_km": round(mod_m / 1000, 1),
            "steep_km": round(steep_m / 1000, 1),
        },
        "segments": segments,
        "profile": profile,
    }


def _fetch_weather(lat: float, lon: float, planned_dt_str: str) -> str:
    try:
        dt = datetime.fromisoformat(planned_dt_str)
        date_str = dt.strftime("%Y-%m-%d")
        hour = dt.hour
        ride_date = dt.date()

        params = (
            f"latitude={lat:.4f}&longitude={lon:.4f}"
            f"&hourly=temperature_2m,precipitation_probability,windspeed_10m,winddirection_10m"
            f"&timezone=Asia/Shanghai&start_date={date_str}&end_date={date_str}"
        )
        if ride_date >= _date.today():
            url = f"https://api.open-meteo.com/v1/forecast?{params}"
        else:
            url = (f"https://archive-api.open-meteo.com/v1/archive?{params}"
                   f"&hourly=temperature_2m,windspeed_10m,winddirection_10m,precipitation_sum")

        req = urllib.request.Request(url, headers={"User-Agent": "GarminAssistant/1.0"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read())

        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        target = f"{date_str}T{hour:02d}:00"
        idx = times.index(target) if target in times else -1
        if idx < 0:
            return "（天气数据无对应时间）\n"

        temp = hourly.get("temperature_2m", [None])[idx]
        precip = hourly.get("precipitation_probability", [None])[idx]
        wind = hourly.get("windspeed_10m", [None])[idx]
        wind_dir = hourly.get("winddirection_10m", [None])[idx]
        dirs = ["北","东北","东","东南","南","西南","西","西北"]
        dir_name = dirs[round((wind_dir or 0) / 45) % 8] if wind_dir is not None else "未知"

        lines = [f"- 骑行日期：{dt.strftime('%Y年%m月%d日')} {hour:02d}:00 出发"]
        if temp is not None: lines.append(f"- 气温：{temp}°C")
        if wind is not None: lines.append(f"- 风速：{wind} km/h（{dir_name}风）")
        if precip is not None: lines.append(f"- 降水概率：{precip}%")
        return "\n".join(lines) + "\n"
    except Exception as e:
        return f"（天气获取失败：{e}）\n"


RIDE_TYPE_NAMES = {
    "recovery": "恢复骑",
    "z2": "Z2 有氧训练",
    "strength": "爬坡/力量训练",
    "touring": "骑游",
    "race": "比赛/全力测试",
}

RIDE_TYPE_INSTRUCTIONS = {
    "recovery": "本次为恢复骑，功率严控 Z1（FTP 50–60%），心率不超过 130，补给以水和电解质为主，不需要能量胶，重点是放松骑行。",
    "z2": "本次为 Z2 有氧训练，功率 FTP 60–75%，心率 Z2，长于 90 分钟时适量补给保持血糖稳定。",
    "strength": "本次为爬坡/力量训练，核心目标是爬升段的功率刺激，爬坡可短时冲到 FTP 90–110%，下坡平路充分恢复。",
    "touring": "本次为骑游，舒适配速（FTP 55–70%），补给宽松，可在景点/补给点停留，不追求速度。",
    "race": "本次为比赛/全力测试，给出最优配速策略、精确补给方案，目标最短完赛时间。",
}


class RouteAnalyzeRequest(BaseModel):
    route: dict
    ride_type: str = "z2"
    planned_dt: str = ""
    weight_kg: float = 70.0


@app.post("/api/gpx-parse")
async def gpx_parse(file: UploadFile = File(...)):
    content = await file.read()
    route = _parse_gpx(content)
    if "error" in route:
        raise HTTPException(status_code=400, detail=route["error"])
    return route


@app.post("/api/gpx-analyze")
async def gpx_analyze(req: RouteAnalyzeRequest):
    route = req.route
    ride_name = RIDE_TYPE_NAMES.get(req.ride_type, req.ride_type)
    ride_hint = RIDE_TYPE_INSTRUCTIONS.get(req.ride_type, "")
    health_ctx = build_context_for_chat()

    weather_ctx = ""
    if req.planned_dt and route.get("start_lat") and route.get("start_lon"):
        weather_ctx = _fetch_weather(route["start_lat"], route["start_lon"], req.planned_dt)

    gd = route.get("grade_dist", {})
    grade_summary = (
        f"平路(≤2%): {gd.get('flat_km',0)}km  "
        f"缓坡(2-5%): {gd.get('easy_km',0)}km  "
        f"中坡(5-9%): {gd.get('moderate_km',0)}km  "
        f"陡坡(>9%): {gd.get('steep_km',0)}km"
    )

    seg_lines = "\n".join(
        f"  段{i+1} | {s['start_km']}–{s['end_km']}km | 均坡{s['avg_grade_pct']:+.1f}% "
        f"| 最大坡{s.get('max_grade_pct', 0):+.1f}% | 爬升{s['ele_gain_m']}m "
        f"| 海拔{s['start_ele_m']}→{s['end_ele_m']}m"
        for i, s in enumerate(route.get("segments", []))
    )

    user_prompt = f"""请根据以下信息，给出针对「{ride_name}」性质的骑行策略。

**骑行性质：{ride_name}**
{ride_hint}

**路线概况**
- 总里程：{route['total_dist_km']} km
- 总爬升：{route['total_gain_m']} m / 总下降：{route['total_loss_m']} m
- 海拔区间：{route['min_ele_m']}–{route['max_ele_m']} m
- 坡度分布（实测）：{grade_summary}

**分段数据（均坡+最大坡均已列出，请据此分析，不要说「本路线不存在此坡度」）**
{seg_lines}

**骑手信息**
- FTP：250W，体重：{req.weight_kg} kg
{health_ctx}

**出行信息**
{weather_ctx if weather_ctx else '- 未提供骑行日期'}

请按以下三个部分回答：

## 一、分段功率目标
根据坡度分布数据（注意陡坡 {gd.get('steep_km',0)}km、中坡 {gd.get('moderate_km',0)}km 实际存在），
结合「{ride_name}」性质，给出每种坡度类型的功率目标（W + %FTP），并针对 TSB 说明是否调整配速。

## 二、补给节点
根据骑行性质（{ride_name}）和总里程给出合理补给方案。恢复骑只需水/电解质，无需能量胶；比赛才需要密集补给，请按实际性质给出，不要过度建议。

## 三、预估完赛时间
给出时间范围，说明天气、坡度、TSB 的影响。

回答要与骑行性质完全匹配，补给建议要克制合理。"""

    def generate():
        try:
            stream = llm_client.chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": "你是专业自行车教练。不同骑行性质（恢复/训练/比赛）的建议力度完全不同，请严格按照用户告知的性质给出针对性策略，不要无脑推荐高强度或大量补给。"},
                    {"role": "user", "content": user_prompt},
                ],
                stream=True, max_tokens=2500, temperature=0.6,
            )
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield f"data: {json.dumps({'content': chunk.choices[0].delta.content}, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/api/status")
async def status():
    from datetime import date, timedelta
    from data_loader import load_daily, _extract_key_metrics, load_icu_sleep_latest
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
    return {"reports": load_reports_list(days)}


@app.get("/api/reports/{report_date}")
async def report_detail(report_date: str, type: str = ""):
    detail = load_report_detail(report_date, report_type=type)
    if not detail:
        raise HTTPException(status_code=404, detail="报告不存在")
    return detail


@app.get("/api/health")
async def health_check():
    return {"status": "ok"}


# ── 用户画像 ──────────────────────────────────────────────────

PROFILE_PATH = Path("/root/garmin_assistant/data/congzhi/user_onboarding_profile.json")

_EMPTY_PROFILE = {
    "personal_info": {
        "nickname": None,
        "gender": None,
        "age": None,
        "height_cm": None,
        "weight_kg": None,
    },
    "sport": None,
    "goal": {"type": None, "race_info": None},
    "schedule": {"days_per_week": None, "training_days": [], "long_session_days": []},
    "coaching_style": None,
    "completed": False,
}

COACHING_STYLE_PROMPTS = {
    "strict": "回答风格：严师直接，直接指出问题不包装，数据说话，不用鼓励语，言简意赅。",
    "data": "回答风格：数据理性，引用具体数字，逻辑清晰，少废话，不用情绪化表达。",
    "gentle": "回答风格：鼓励温和，多用正面肯定语言，指出问题时先肯定再建议，语气温暖。",
    "friend": "回答风格：朋友平等，口语化，像朋友聊天，不要过于正式，可以稍微随意一点。",
}


def _read_profile() -> dict:
    try:
        if PROFILE_PATH.exists():
            return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return dict(_EMPTY_PROFILE)


def _write_profile(data: dict):
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class ProfilePayload(BaseModel):
    personal_info: dict | None = None
    sport: str | None = None
    goal: dict | None = None
    schedule: dict | None = None
    coaching_style: str | None = None
    completed: bool | None = None


@app.get("/api/profile")
async def get_profile():
    return _read_profile()


@app.post("/api/profile")
async def save_profile(payload: ProfilePayload):
    data = _read_profile()
    # 兼容旧 profile：补全 personal_info 字段
    if "personal_info" not in data:
        data["personal_info"] = dict(_EMPTY_PROFILE["personal_info"])

    if payload.personal_info is not None:
        data["personal_info"].update(payload.personal_info)
    if payload.sport is not None:
        data["sport"] = payload.sport
    if payload.goal is not None:
        data["goal"] = payload.goal
    if payload.schedule is not None:
        data["schedule"] = payload.schedule
    if payload.coaching_style is not None:
        data["coaching_style"] = payload.coaching_style
    if payload.completed is not None:
        data["completed"] = payload.completed
    _write_profile(data)

    # 触发用户画像 Markdown 重新生成
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
        from portrait_builder import build_portrait
        build_portrait()
    except Exception as exc:
        print(f"[portrait] 生成失败: {exc}", flush=True)

    return {"ok": True}


@app.post("/api/portrait/refresh")
async def refresh_portrait():
    """手动重新生成用户画像。"""
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
        from portrait_builder import build_portrait
        result = build_portrait()
        return {"ok": True, **result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/portrait")
async def get_portrait():
    """返回画像 Markdown 文本，供前端展示用。"""
    portrait_path = Path("/root/garmin_assistant/data/congzhi/user_portrait.md")
    if portrait_path.exists():
        return {"markdown": portrait_path.read_text(encoding="utf-8")}
    return {"markdown": ""}
