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
