from __future__ import annotations

from pathlib import Path


def _configure_matplotlib():
    import matplotlib

    matplotlib.use("Agg")

    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = [
        "Noto Sans CJK SC",
        "Noto Sans CJK JP",
        "Source Han Sans SC",
        "WenQuanYi Zen Hei",
        "Microsoft YaHei",
        "SimHei",
        "DejaVu Sans",
    ]
    plt.rcParams["axes.unicode_minus"] = False
    return plt


def _format_minutes_text(total_minutes: float | int | None) -> str:
    if total_minutes in (None, ""):
        return "0分钟"
    minutes = int(round(float(total_minutes)))
    hours, remain = divmod(minutes, 60)
    if hours <= 0:
        return f"{remain}分钟"
    if remain == 0:
        return f"{hours}小时"
    return f"{hours}小时{remain}分钟"


def render_line_chart(
    output_path: Path,
    title: str,
    x_labels: list[str],
    y_values: list[float],
    highlight_index: int | None = None,
    mean_value: float | None = None,
    line_color: str = "#245A78",
) -> None:
    plt = _configure_matplotlib()

    output_path.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(6, 3.2), facecolor="white")
    try:
        ax.set_facecolor("white")

        x_positions = list(range(len(y_values)))
        ax.plot(
            x_positions,
            y_values,
            color=line_color,
            marker="o",
            markersize=4,
            linewidth=2,
        )

        if mean_value is not None:
            ax.axhline(mean_value, color="#C9D3DB", linestyle="--", linewidth=1)

        if highlight_index is not None and 0 <= highlight_index < len(y_values):
            ax.scatter(
                [highlight_index],
                [y_values[highlight_index]],
                color="#D9822B",
                s=36,
                zorder=3,
            )

        ax.set_title(title, loc="left")
        ax.set_xticks(x_positions)
        ax.set_xticklabels(x_labels)
        ax.grid(axis="y", color="#E6ECF0", linewidth=0.8)
        ax.grid(axis="x", visible=False)

        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_visible(False)
        ax.spines["bottom"].set_color("#D8E0E6")

        fig.savefig(output_path, format="png", bbox_inches="tight")
    finally:
        plt.close(fig)


def render_sleep_structure_chart(
    output_path: Path,
    *,
    total_sleep_minutes: float | int,
    deep_sleep_minutes: float | int,
    light_sleep_minutes: float | int,
    rem_sleep_minutes: float | int,
    awake_minutes: float | int,
) -> None:
    plt = _configure_matplotlib()

    output_path.parent.mkdir(parents=True, exist_ok=True)

    stage_values = [
        max(float(deep_sleep_minutes or 0), 0.0),
        max(float(light_sleep_minutes or 0), 0.0),
        max(float(rem_sleep_minutes or 0), 0.0),
        max(float(awake_minutes or 0), 0.0),
    ]
    stage_labels = ["深睡", "浅睡", "REM", "清醒"]
    stage_colors = ["#2F80ED", "#56A8FF", "#C854D3", "#F08AA6"]

    fig = plt.figure(figsize=(6.0, 3.6), facecolor="white")
    try:
        donut_ax = fig.add_axes([0.03, 0.08, 0.44, 0.84])
        legend_ax = fig.add_axes([0.50, 0.14, 0.46, 0.72])
        legend_ax.axis("off")

        donut_ax.pie(
            stage_values,
            colors=stage_colors,
            startangle=90,
            counterclock=False,
            wedgeprops={"width": 0.22, "edgecolor": "white", "linewidth": 2},
        )
        donut_ax.text(0, 0.10, _format_minutes_text(total_sleep_minutes), ha="center", va="center", fontsize=22, color="#1F2933")
        donut_ax.text(0, -0.15, "总睡眠时间", ha="center", va="center", fontsize=10, color="#6B7785")
        donut_ax.set_aspect("equal")

        legend_rows = [
            ("深睡", deep_sleep_minutes, stage_colors[0]),
            ("浅睡", light_sleep_minutes, stage_colors[1]),
            ("REM", rem_sleep_minutes, stage_colors[2]),
            ("清醒", awake_minutes, stage_colors[3]),
        ]
        y_positions = [0.82, 0.58, 0.34, 0.10]
        for (label, minutes, color), y in zip(legend_rows, y_positions):
            legend_ax.add_patch(plt.Rectangle((0.00, y - 0.035), 0.045, 0.07, color=color, transform=legend_ax.transAxes, clip_on=False))
            legend_ax.text(0.08, y + 0.02, _format_minutes_text(minutes), transform=legend_ax.transAxes, ha="left", va="center", fontsize=18, color="#1F2933")
            legend_ax.text(0.08, y - 0.08, label, transform=legend_ax.transAxes, ha="left", va="center", fontsize=10, color="#6B7785")

        fig.savefig(output_path, format="png", bbox_inches="tight")
    finally:
        plt.close(fig)


def _smooth(arr, window: int = 15):
    import numpy as np
    if len(arr) < window:
        return arr
    kernel = np.ones(window) / window
    return np.convolve(arr, kernel, mode="same")


def _add_story_markers(ax, story_points: dict | None):
    if not isinstance(story_points, dict):
        return
    for seg in story_points.get("高功率段") or []:
        if not isinstance(seg, dict):
            continue
        km_range = seg.get("起止里程_km")
        if isinstance(km_range, list) and len(km_range) == 2:
            try:
                mid = (float(km_range[0] or 0) + float(km_range[1] or 0)) / 2
                ax.axvline(x=mid, color="#1565C0", linestyle="--", linewidth=1.2, alpha=0.7)
            except (TypeError, ValueError):
                pass
    for pt in story_points.get("脱钩点") or []:
        if not isinstance(pt, dict):
            continue
        km = pt.get("里程_km")
        if km and km != "无数据":
            try:
                ax.axvline(x=float(km), color="#F57F17", linestyle="--", linewidth=1.2, alpha=0.7)
            except (TypeError, ValueError):
                pass
    decay = story_points.get("衰减段")
    if isinstance(decay, dict):
        km_range = decay.get("里程范围_km")
        if isinstance(km_range, list) and km_range:
            km = km_range[0]
            if km and km != "无数据":
                try:
                    ax.axvline(x=float(km), color="#6A1B9A", linestyle="--", linewidth=1.2, alpha=0.7)
                except (TypeError, ValueError):
                    pass


def render_cycling_trace_chart(
    output_path: Path,
    *,
    streams: dict,
    ftp: float = 240.0,
    story_points: dict | None = None,
    has_power: bool = True,
) -> None:
    import numpy as np
    import matplotlib.patches as mpatches

    plt = _configure_matplotlib()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    raw_dist = streams.get("distance") or []
    n = len(raw_dist)
    if n < 20:
        return

    dist_km = np.array([float(v) if v is not None else 0.0 for v in raw_dist[:n]]) / 1000.0

    fig, ax1 = plt.subplots(figsize=(10, 4.5), facecolor="white")
    ax1.set_facecolor("white")
    ax2 = ax1.twinx()

    if has_power and streams.get("watts"):
        raw_watts = streams.get("watts") or []
        raw_hr = streams.get("heartrate") or []
        m = min(n, len(raw_watts))
        watts = _smooth(np.array([float(v) if v is not None else 0.0 for v in raw_watts[:m]]), 15)
        hr_arr = _smooth(np.array([float(v) if v is not None else 0.0 for v in raw_hr[:m]]), 20)

        zone_colors = ["#C8E6C9", "#A5D6A7", "#FFE082", "#FFB300", "#EF9A9A", "#E53935", "#9C27B0"]
        zone_pct = [0, 0.55, 0.75, 0.90, 1.05, 1.20, 1.50, 3.0]
        zone_names = ["Z1", "Z2", "Z3", "Z4", "Z5", "Z6", "Z7"]
        for i in range(len(zone_pct) - 1):
            lo, hi = zone_pct[i] * ftp, zone_pct[i + 1] * ftp
            fill = np.where((watts >= lo) & (watts < hi), watts, np.nan)
            ax1.fill_between(dist_km[:m], 0, fill, color=zone_colors[i], alpha=0.85, linewidth=0)
        ax1.plot(dist_km[:m], watts, color="#424242", linewidth=0.8, alpha=0.5)
        ax1.set_ylabel("功率 (W)", fontsize=9)
        ax1.set_ylim(0, max(float(watts.max()) * 1.15, ftp * 1.5))

        ax2.plot(dist_km[:m], hr_arr, color="#E53935", linewidth=1.6, alpha=0.85)
        ax2.set_ylabel("心率 (bpm)", fontsize=9, color="#E53935")
        ax2.tick_params(axis="y", labelcolor="#E53935")
        ax2.set_ylim(80, 210)

        patches = [mpatches.Patch(color=zone_colors[i], label=zone_names[i]) for i in range(7)]
        hr_line = plt.Line2D([0], [0], color="#E53935", linewidth=2, label="心率")
        ax1.legend(handles=patches + [hr_line], loc="upper left", fontsize=7.5, ncol=4, framealpha=0.85)
        _add_story_markers(ax1, story_points)
        ax1.set_xlim(dist_km[0], dist_km[m - 1])
    else:
        raw_speed = streams.get("velocity_smooth") or []
        raw_hr = streams.get("heartrate") or []
        raw_alt = streams.get("altitude") or []
        m = min(n, len(raw_speed) if raw_speed else n, len(raw_hr) if raw_hr else n)
        speed_kmh = _smooth(np.array([float(v) * 3.6 if v is not None else 0.0 for v in raw_speed[:m]]), 15)
        hr_arr = _smooth(np.array([float(v) if v is not None else 0.0 for v in raw_hr[:m]]), 20)

        if raw_alt and len(raw_alt) >= m:
            alt = _smooth(np.array([float(v) if v is not None else 0.0 for v in raw_alt[:m]]), 20)
            alt_range = float(alt.max()) - float(alt.min())
            if alt_range > 5:
                alt_norm = (alt - alt.min()) / alt_range * float(speed_kmh.max()) * 0.75
                ax1.fill_between(dist_km[:m], 0, alt_norm, color="#ECEFF1", linewidth=0, alpha=0.9)
                ax1.plot(dist_km[:m], alt_norm, color="#B0BEC5", linewidth=0.8, alpha=0.7)

        MAX_HR = 190.0
        hr_zone_colors = ["#C8E6C9", "#A5D6A7", "#FFE082", "#FFB300", "#EF9A9A"]
        hr_zone_bounds = [0, 0.60, 0.70, 0.80, 0.90, 99.0]
        hr_zone_names = ["Z1", "Z2", "Z3", "Z4", "Z5"]
        for i in range(len(hr_zone_bounds) - 1):
            lo, hi = hr_zone_bounds[i] * MAX_HR, hr_zone_bounds[i + 1] * MAX_HR
            fill = np.where((hr_arr >= lo) & (hr_arr < hi), speed_kmh, np.nan)
            ax1.fill_between(dist_km[:m], 0, fill, color=hr_zone_colors[i], alpha=0.80, linewidth=0)
        ax1.plot(dist_km[:m], speed_kmh, color="#1565C0", linewidth=1.2, alpha=0.7)
        ax1.set_ylabel("速度 (km/h)", fontsize=9)
        ax1.set_ylim(0, float(speed_kmh.max()) * 1.3 if speed_kmh.max() > 0 else 60)

        ax2.plot(dist_km[:m], hr_arr, color="#E53935", linewidth=1.6, alpha=0.85)
        ax2.set_ylabel("心率 (bpm)", fontsize=9, color="#E53935")
        ax2.tick_params(axis="y", labelcolor="#E53935")
        ax2.set_ylim(80, 210)

        patches = [mpatches.Patch(color=hr_zone_colors[i], label=hr_zone_names[i]) for i in range(5)]
        hr_line = plt.Line2D([0], [0], color="#E53935", linewidth=2, label="心率")
        ax1.legend(handles=patches + [hr_line], loc="upper left", fontsize=7.5, ncol=3, framealpha=0.85)
        _add_story_markers(ax1, story_points)
        ax1.set_xlim(dist_km[0], dist_km[m - 1])

    ax1.set_xlabel("距离 (km)", fontsize=9)
    ax1.grid(axis="y", color="#E6ECF0", linewidth=0.6, alpha=0.5)
    ax1.spines["top"].set_visible(False)
    ax2.spines["top"].set_visible(False)
    fig.savefig(output_path, format="png", dpi=130, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def render_cycling_zones_chart(
    output_path: Path,
    *,
    power_zone_times: list,
    hr_zone_times: list,
) -> None:
    import matplotlib.patches as mpatches

    plt = _configure_matplotlib()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Normalise power zone data: accept [{id,secs},...] or plain list of secs
    pwr_secs = []
    for item in (power_zone_times or []):
        if isinstance(item, dict):
            zone_id = str(item.get("id") or "").upper()
            if not zone_id.startswith("Z"):
                continue
            v = item.get("secs") or item.get("seconds") or 0
        else:
            v = item or 0
        try:
            pwr_secs.append(float(v))
        except (TypeError, ValueError):
            pwr_secs.append(0.0)
    pwr_secs = pwr_secs[:7]

    # HR: plain list, take first 5
    hr_secs = []
    for item in (hr_zone_times or [])[:5]:
        try:
            hr_secs.append(float(item or 0))
        except (TypeError, ValueError):
            hr_secs.append(0.0)

    pwr_colors = ["#C8E6C9", "#A5D6A7", "#FFE082", "#FFB300", "#EF9A9A", "#E53935", "#9C27B0"]
    hr_colors  = ["#C8E6C9", "#A5D6A7", "#FFE082", "#FFB300", "#EF9A9A"]

    fig, ax = plt.subplots(figsize=(6, 2.4), facecolor="white")
    ax.set_facecolor("white")
    ax.set_xlim(0, 1)
    ax.set_ylim(-0.6, 1.6)
    ax.axis("off")

    def _draw_bar(y, secs_list, colors, label):
        total = sum(secs_list) or 1
        x = 0.0
        for i, secs in enumerate(secs_list):
            w = secs / total
            if w <= 0:
                continue
            color = colors[i] if i < len(colors) else "#BDBDBD"
            ax.barh(y, w, left=x, height=0.55, color=color, edgecolor="white", linewidth=1.5)
            if w > 0.05:
                mins = int(round(secs / 60))
                text_color = "white" if i >= 3 else "#333333"
                ax.text(x + w / 2, y, f"{mins}m",
                        ha="center", va="center", fontsize=8,
                        color=text_color, fontweight="bold")
            x += w
        ax.text(-0.02, y, label, ha="right", va="center", fontsize=9, color="#555555")

    _draw_bar(1.0, pwr_secs, pwr_colors, "功率")
    _draw_bar(0.2, hr_secs,  hr_colors,  "心率")

    n_zones = max(len(pwr_secs), 7)
    patches = [mpatches.Patch(color=pwr_colors[i], label=f"Z{i+1}")
               for i in range(min(n_zones, len(pwr_colors)))]
    ax.legend(handles=patches, loc="upper right", fontsize=7.5, ncol=7,
              frameon=False, bbox_to_anchor=(1, -0.05))
    ax.set_title("训练区间分布", fontsize=10, pad=8, loc="left")

    plt.tight_layout(pad=0.5)
    fig.savefig(output_path, format="png", dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
