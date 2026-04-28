"""用户画像生成器

读取最近 90 天的 ICU 睡眠/骑行数据 + 用户问卷答案 + 个人信息，
生成一份结构化的 Markdown 画像，供 chat AI 注入到 system prompt。

输出文件：
- /root/garmin_assistant/data/congzhi/user_portrait.md     # AI 阅读
- /root/garmin_assistant/data/congzhi/user_portrait_stats.json  # 原始统计数据（调试/前端用）

调用方式：
    from portrait_builder import build_portrait
    build_portrait()                  # 默认 90 天
    build_portrait(days=60)
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

BJ_TZ = timezone(timedelta(hours=8))
DATA_DIR = Path("/root/garmin_assistant/data/congzhi")
ICU_SLEEP_DIR = DATA_DIR / "icu_sleep"
ICU_CYCLING_DIR = DATA_DIR / "icu_cycling"
ONBOARDING_PATH = DATA_DIR / "user_onboarding_profile.json"
PORTRAIT_PATH = DATA_DIR / "user_portrait.md"
STATS_PATH = DATA_DIR / "user_portrait_stats.json"

DAY_NAMES_CN = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五", 5: "周六", 6: "周日"}
DAY_NAMES_EN = {"Mon": "周一", "Tue": "周二", "Wed": "周三", "Thu": "周四",
                "Fri": "周五", "Sat": "周六", "Sun": "周日"}
SPORT_NAMES = {"cycling": "骑行", "running": "跑步", "swimming": "游泳", "triathlon": "铁三"}
GOAL_NAMES = {"ftp_improve": "提升能力", "race": "备赛", "fat_loss": "减脂", "maintain": "保持健康"}
STYLE_NAMES = {"strict": "严师直接", "data": "数据理性", "gentle": "鼓励温和", "friend": "朋友平等"}
GENDER_NAMES = {"male": "男", "female": "女"}


# ── 工具函数 ──────────────────────────────────────────────────


def _read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _f(v):
    """安全转 float，None/异常 → None。"""
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * p / 100
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _fmt_hours(secs: float | None) -> str:
    if not secs:
        return "—"
    h = int(secs // 3600)
    m = int((secs % 3600) // 60)
    return f"{h}h{m:02d}m"


# ── 数据读取 ──────────────────────────────────────────────────


def _load_sleep_records(days: int) -> list[dict]:
    """返回最近 N 天的 ICU 睡眠记录（含 _date / _weekday 元数据）。"""
    if not ICU_SLEEP_DIR.exists():
        return []
    today = datetime.now(BJ_TZ).date()
    cutoff = today - timedelta(days=days)
    out: list[dict] = []
    for f in sorted(ICU_SLEEP_DIR.glob("*.json")):
        raw = _read_json(f)
        if not raw:
            continue
        date_str = raw.get("date") or ""
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d < cutoff:
            continue
        payload = raw.get("payload") or raw
        payload["_date"] = date_str
        payload["_weekday"] = d.weekday()
        out.append(payload)
    return out


def _load_cycling_records(days: int) -> list[dict]:
    if not ICU_CYCLING_DIR.exists():
        return []
    today = datetime.now(BJ_TZ).date()
    cutoff = today - timedelta(days=days)
    out: list[dict] = []
    for f in sorted(ICU_CYCLING_DIR.glob("*.json")):
        raw = _read_json(f)
        if not raw:
            continue
        date_str = raw.get("date") or ""
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d < cutoff:
            continue
        raw["_date"] = date_str
        raw["_weekday"] = d.weekday()
        out.append(raw)
    return out


def _load_onboarding() -> dict:
    return _read_json(ONBOARDING_PATH) or {}


# ── 统计计算 ──────────────────────────────────────────────────


def compute_stats(days: int = 90) -> dict:
    sleep_records = _load_sleep_records(days)
    cycling_records = _load_cycling_records(days)

    stats: dict = {
        "period_days": days,
        "data_count": {"sleep": len(sleep_records), "cycling": len(cycling_records)},
        "first_date": (sleep_records[0]["_date"] if sleep_records else None),
        "last_date": (sleep_records[-1]["_date"] if sleep_records else None),
    }

    # ── 恢复指标 ─────────────────────────────────────────────
    hrv_vals = [_f(s.get("hrv", {}).get("rMSSD")) for s in sleep_records]
    hrv_vals = [v for v in hrv_vals if v]
    rhr_vals = [_f(s.get("cardiovascular_recovery", {}).get("restingHR"))
                for s in sleep_records]
    rhr_vals = [v for v in rhr_vals if v]
    sleep_secs = [_f(s.get("sleep", {}).get("sleepSecs")) for s in sleep_records]
    sleep_secs = [v for v in sleep_secs if v]
    sleep_scores = [_f(s.get("sleep", {}).get("sleepScore")) for s in sleep_records]
    sleep_scores = [v for v in sleep_scores if v]
    readiness_vals = [_f(s.get("cardiovascular_recovery", {}).get("readiness"))
                      for s in sleep_records]
    readiness_vals = [v for v in readiness_vals if v is not None]

    if hrv_vals:
        stats["hrv"] = {
            "avg": round(statistics.mean(hrv_vals), 1),
            "min": round(min(hrv_vals), 1),
            "max": round(max(hrv_vals), 1),
            "p10": round(_percentile(hrv_vals, 10), 1),
            "p90": round(_percentile(hrv_vals, 90), 1),
        }
    if rhr_vals:
        stats["rhr"] = {
            "avg": round(statistics.mean(rhr_vals), 1),
            "min": round(min(rhr_vals), 0),
            "max": round(max(rhr_vals), 0),
        }
    if sleep_secs:
        stats["sleep"] = {
            "avg_secs": round(statistics.mean(sleep_secs)),
            "avg_score": round(statistics.mean(sleep_scores), 1) if sleep_scores else None,
            "below_6h_count": sum(1 for s in sleep_secs if s < 6 * 3600),
            "above_8h_count": sum(1 for s in sleep_secs if s >= 8 * 3600),
            "total_records": len(sleep_secs),
        }
    if readiness_vals:
        stats["readiness"] = {
            "avg": round(statistics.mean(readiness_vals), 1),
            "low_days": sum(1 for r in readiness_vals if r < 50),
            "high_days": sum(1 for r in readiness_vals if r >= 75),
        }

    # ── 训练负荷指标（CTL/ATL） ──────────────────────────────
    ctl_vals = [_f(s.get("training_load_context", {}).get("ctl")) for s in sleep_records]
    ctl_vals = [v for v in ctl_vals if v is not None]
    atl_vals = [_f(s.get("training_load_context", {}).get("atl")) for s in sleep_records]
    atl_vals = [v for v in atl_vals if v is not None]

    if ctl_vals:
        stats["ctl"] = {
            "current": round(ctl_vals[-1], 1),
            "avg": round(statistics.mean(ctl_vals), 1),
            "peak": round(max(ctl_vals), 1),
            "low": round(min(ctl_vals), 1),
        }
    if atl_vals:
        stats["atl"] = {
            "current": round(atl_vals[-1], 1),
            "avg": round(statistics.mean(atl_vals), 1),
            "peak": round(max(atl_vals), 1),
        }
    if ctl_vals and atl_vals:
        tsb_now = round(ctl_vals[-1] - atl_vals[-1], 1)
        stats["tsb"] = {"current": tsb_now}

    # ── 训练规律 ─────────────────────────────────────────────
    if cycling_records:
        weeks = max(days / 7, 1.0)
        weekday_count = {i: 0 for i in range(7)}
        for c in cycling_records:
            weekday_count[c["_weekday"]] += 1

        tss_vals = [_f(c.get("tss")) for c in cycling_records]
        tss_vals = [v for v in tss_vals if v is not None]
        durations = [_f(c.get("duration_secs")) for c in cycling_records]
        durations = [v / 60 for v in durations if v]

        # 长距离骑行：>=90分钟
        long_rides = [c for c in cycling_records
                      if (c.get("duration_secs") or 0) >= 90 * 60]
        long_weekday_count = {i: 0 for i in range(7)}
        for c in long_rides:
            long_weekday_count[c["_weekday"]] += 1

        # 实际最常训练日 Top3（按次数排序）
        sorted_wd = sorted(weekday_count.items(), key=lambda x: -x[1])
        top_days = [(DAY_NAMES_CN[k], v) for k, v in sorted_wd if v > 0][:5]

        # 长距离日：次数 >= 3 才算"通常"
        long_top = [(DAY_NAMES_CN[k], v) for k, v in
                    sorted(long_weekday_count.items(), key=lambda x: -x[1]) if v >= 2]

        stats["training"] = {
            "total_rides": len(cycling_records),
            "rides_per_week": round(len(cycling_records) / weeks, 1),
            "weekly_tss_avg": round(sum(tss_vals) / weeks, 0) if tss_vals else None,
            "avg_duration_min": round(statistics.mean(durations), 0) if durations else None,
            "max_duration_min": round(max(durations), 0) if durations else None,
            "weekday_top": top_days,
            "long_rides_count": len(long_rides),
            "long_ride_typical_days": [n for n, _ in long_top[:2]],
        }

    return stats


# ── 行为洞察推导（基于规则） ──────────────────────────────────


def derive_insights(stats: dict, onboarding: dict) -> list[str]:
    insights: list[str] = []

    # 计划 vs 实际频次差异
    planned = (onboarding.get("schedule") or {}).get("days_per_week")
    actual = stats.get("training", {}).get("rides_per_week")
    if planned and actual is not None:
        diff = actual - planned
        if diff <= -1:
            insights.append(
                f"实际训练频次（每周 {actual} 次）比计划（每周 {planned} 次）少 "
                f"{abs(diff):.1f} 次，说明计划执行存在难度，建议把计划频次调到接近实际值"
            )
        elif diff >= 1.5:
            insights.append(
                f"实际训练频次（每周 {actual} 次）超出计划（每周 {planned} 次），"
                f"需关注疲劳积累"
            )

    # 实际长距离日 vs 计划长距离日
    onboard_long = (onboarding.get("schedule") or {}).get("long_session_days") or []
    actual_long = stats.get("training", {}).get("long_ride_typical_days") or []
    if onboard_long and actual_long:
        plan_cn = {DAY_NAMES_EN.get(d, d) for d in onboard_long}
        actual_set = set(actual_long)
        if plan_cn != actual_set and actual_set:
            insights.append(
                f"长距离骑行实际多在 {' '.join(actual_long)}，"
                f"与计划的 {' '.join(plan_cn)} 不完全一致"
            )

    # CTL 趋势（积累期 / 减量期）
    ctl = stats.get("ctl", {})
    if ctl:
        if ctl["current"] > ctl["avg"] * 1.08:
            insights.append(
                f"当前 CTL（{ctl['current']}）高于90天均值（{ctl['avg']}），处于体能积累期，"
                f"距离 90 天峰值 {ctl['peak']} 还差 {round(ctl['peak'] - ctl['current'], 1)}"
            )
        elif ctl["current"] < ctl["avg"] * 0.92:
            insights.append(
                f"当前 CTL（{ctl['current']}）低于90天均值（{ctl['avg']}），近期训练量减少，"
                f"如果不是主动减量需关注"
            )

    # TSB 状态
    tsb = stats.get("tsb", {}).get("current")
    if tsb is not None:
        if tsb < -15:
            insights.append(f"当前 TSB={tsb}，疲劳显著（≤-15），下一次大强度前需要恢复")
        elif tsb > 15:
            insights.append(f"当前 TSB=+{tsb}，状态过好可能意味着掉体能（>+15），可加大训练")

    # 睡眠不足风险
    sleep = stats.get("sleep", {})
    total_records = sleep.get("total_records", 0)
    if total_records >= 30:
        below_pct = sleep.get("below_6h_count", 0) / total_records * 100
        if below_pct >= 15:
            insights.append(
                f"90天内 {sleep['below_6h_count']} 天睡眠不足6小时（占 {below_pct:.0f}%），"
                f"长期睡眠不足是恢复和HRV下降的主因"
            )

    # Readiness 长期偏低
    readiness = stats.get("readiness", {})
    if readiness.get("low_days", 0) >= max(stats.get("data_count", {}).get("sleep", 0) // 4, 10):
        insights.append(
            f"90天内 Readiness < 50 的天数达 {readiness['low_days']} 天，"
            f"恢复存在长期挑战，需排查训练负荷或生活作息"
        )

    return insights


# ── Markdown 渲染 ────────────────────────────────────────────


def _render_personal_section(personal: dict, stats: dict) -> list[str]:
    lines = []
    nickname = personal.get("nickname")
    if nickname:
        lines.append(f"- **昵称**：{nickname}")

    bits = []
    gender = personal.get("gender")
    if gender:
        bits.append(f"性别 {GENDER_NAMES.get(gender, gender)}")
    age = personal.get("age")
    if age:
        bits.append(f"年龄 {age} 岁")
    if bits:
        lines.append("- " + "　·　".join(bits))

    body_bits = []
    h = personal.get("height_cm")
    w = personal.get("weight_kg")
    if h:
        body_bits.append(f"身高 {h} cm")
    if w:
        body_bits.append(f"体重 {w} kg")
    if h and w:
        bmi = round(w / ((h / 100) ** 2), 1)
        body_bits.append(f"BMI {bmi}")
    if body_bits:
        lines.append("- " + "　·　".join(body_bits))

    return lines


def render_markdown(stats: dict, onboarding: dict) -> str:
    today_str = datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M")
    sleep_n = stats.get("data_count", {}).get("sleep", 0)
    cycling_n = stats.get("data_count", {}).get("cycling", 0)

    lines = [
        "# 用户画像",
        "",
        f"> 生成时间：{today_str}　·　数据周期：最近 {stats.get('period_days', 90)} 天",
        f"> 已采集：睡眠 {sleep_n} 天　·　骑行 {cycling_n} 次",
    ]
    if stats.get("first_date") and stats.get("last_date"):
        lines.append(f"> 数据范围：{stats['first_date']} ~ {stats['last_date']}")
    lines.append("")

    # ── 一、基本信息 ──
    lines.append("## 一、基本信息")
    personal = onboarding.get("personal_info") or {}
    personal_lines = _render_personal_section(personal, stats)
    lines.extend(personal_lines)

    sport = onboarding.get("sport")
    if sport:
        lines.append(f"- **主项**：{SPORT_NAMES.get(sport, sport)}")
    goal = (onboarding.get("goal") or {}).get("type")
    if goal:
        lines.append(f"- **训练目标**：{GOAL_NAMES.get(goal, goal)}")
    sched = onboarding.get("schedule") or {}
    if sched.get("days_per_week"):
        lines.append(f"- **计划训练频次**：每周 {sched['days_per_week']} 天")
    if sched.get("training_days"):
        days_cn = "、".join(DAY_NAMES_EN.get(d, d) for d in sched["training_days"])
        lines.append(f"- **计划训练日**：{days_cn}")
    if sched.get("long_session_days"):
        long_cn = "、".join(DAY_NAMES_EN.get(d, d) for d in sched["long_session_days"])
        lines.append(f"- **计划长距离日**：{long_cn}")
    style = onboarding.get("coaching_style")
    if style:
        lines.append(f"- **沟通风格偏好**：{STYLE_NAMES.get(style, style)}")

    if not personal_lines and not sport:
        lines.append("- _暂未完成问卷或个人信息设置，建议引导用户填写_")

    # ── 二、能力基线（基于真实数据） ──
    has_baseline = any(k in stats for k in ["hrv", "rhr", "sleep", "ctl", "readiness"])
    if has_baseline:
        lines.append("")
        lines.append("## 二、能力基线（基于历史数据计算）")

        if "ctl" in stats:
            c = stats["ctl"]
            lines.append(
                f"- **CTL（体能/慢性负荷）**：当前 {c['current']}，"
                f"90天均值 {c['avg']}，峰值 {c['peak']}，最低 {c['low']}"
            )
        if "atl" in stats:
            a = stats["atl"]
            lines.append(
                f"- **ATL（疲劳/急性负荷）**：当前 {a['current']}，"
                f"90天均值 {a['avg']}，峰值 {a['peak']}"
            )
        if "tsb" in stats:
            tv = stats["tsb"]["current"]
            lines.append(f"- **TSB（训练压力平衡）**：当前 {'+' if tv > 0 else ''}{tv}")
        if "hrv" in stats:
            h = stats["hrv"]
            lines.append(
                f"- **HRV 基线**：均值 {h['avg']} ms，"
                f"正常波动区间 {h['p10']}–{h['p90']} ms（极值 {h['min']}–{h['max']}）"
            )
        if "rhr" in stats:
            r = stats["rhr"]
            lines.append(
                f"- **静息心率**：均值 {r['avg']} bpm（{int(r['min'])}–{int(r['max'])}）"
            )
        if "sleep" in stats:
            s = stats["sleep"]
            line = f"- **睡眠**：平均 {_fmt_hours(s['avg_secs'])}"
            if s.get("avg_score"):
                line += f"，评分 {s['avg_score']}"
            line += f"；少于 6 小时 {s['below_6h_count']} 天，超过 8 小时 {s['above_8h_count']} 天"
            lines.append(line)
        if "readiness" in stats:
            r = stats["readiness"]
            lines.append(
                f"- **Readiness 平均**：{r['avg']}（高分 ≥75 共 {r['high_days']} 天，"
                f"低分 <50 共 {r['low_days']} 天）"
            )

        # 功率体重比
        weight = (onboarding.get("personal_info") or {}).get("weight_kg")
        if weight:
            lines.append(
                f"- _功率体重比可在已知 FTP 时计算：FTP/{weight}kg；"
                f"AI 在解读骑行数据时可主动提示_"
            )

    # ── 三、训练规律（实际行为） ──
    if "training" in stats:
        t = stats["training"]
        lines.append("")
        lines.append("## 三、训练规律（来自实际骑行记录）")
        lines.append(
            f"- 90天累计骑行 **{t['total_rides']} 次**，"
            f"平均 **每周 {t['rides_per_week']} 次**"
        )
        if t.get("weekly_tss_avg"):
            lines.append(f"- 周均 TSS：**{int(t['weekly_tss_avg'])}**")
        if t.get("avg_duration_min"):
            line = f"- 平均单次时长：{int(t['avg_duration_min'])} 分钟"
            if t.get("max_duration_min"):
                line += f"（最长 {int(t['max_duration_min'])} 分钟）"
            lines.append(line)
        if t.get("weekday_top"):
            top_str = "、".join(f"{n}({v}次)" for n, v in t["weekday_top"])
            lines.append(f"- 实际最常训练日：{top_str}")
        if t.get("long_rides_count"):
            line = f"- ≥90 分钟的长距离骑行 {t['long_rides_count']} 次"
            if t.get("long_ride_typical_days"):
                line += f"，主要在 {' / '.join(t['long_ride_typical_days'])}"
            lines.append(line)

    # ── 四、行为洞察 ──
    insights = derive_insights(stats, onboarding)
    if insights:
        lines.append("")
        lines.append("## 四、行为洞察（自动提炼）")
        for ins in insights:
            lines.append(f"- {ins}")

    # ── 五、AI 教练注意事项 ──
    lines.append("")
    lines.append("## 五、AI 教练使用本画像的方式")
    style_label = STYLE_NAMES.get(onboarding.get("coaching_style") or "", "")
    if style_label:
        lines.append(f"- 严格按用户偏好的「{style_label}」风格回复，不可越界")
    lines.append("- 回答涉及今日数据时，**对照第二节基线**判断偏离程度（HRV/RHR/睡眠/CTL）")
    lines.append("- 给训练建议时，**优先尊重第三节实际规律**，而非照搬计划训练日")
    lines.append("- 用户问「最近怎么样」时，结合**第四节行为洞察**给出具体观察，不要泛泛而谈")
    if not has_baseline and not stats.get("training"):
        lines.append("- ⚠️ 当前数据量不足，画像质量有限，建议提示用户回填历史或使用一段时间")

    return "\n".join(lines) + "\n"


# ── 主入口 ────────────────────────────────────────────────────


def build_portrait(days: int = 90) -> dict:
    """生成画像 Markdown + 统计 JSON，返回路径信息。"""
    stats = compute_stats(days)
    onboarding = _load_onboarding()
    md = render_markdown(stats, onboarding)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PORTRAIT_PATH.write_text(md, encoding="utf-8")
    STATS_PATH.write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return {
        "portrait_path": str(PORTRAIT_PATH),
        "stats_path": str(STATS_PATH),
        "md_chars": len(md),
        "data_count": stats.get("data_count", {}),
    }


def load_portrait_md() -> str:
    """供 chat_api 等调用方读取画像文本。"""
    if PORTRAIT_PATH.exists():
        try:
            return PORTRAIT_PATH.read_text(encoding="utf-8")
        except Exception:
            return ""
    return ""


if __name__ == "__main__":
    result = build_portrait()
    print(f"画像已生成：{result['portrait_path']}")
    print(f"Markdown 大小：{result['md_chars']} 字符")
    print(f"数据量：{result['data_count']}")
