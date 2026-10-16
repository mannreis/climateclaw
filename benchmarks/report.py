"""Markdown base-vs-head benchmark report (PR comment, job summary).

One section per component with a table and Mermaid charts; badges for all
components on top. Input per side is a list of per-level results as written
by a component's load generator (see main.py).
"""

from __future__ import annotations

import math
from urllib.parse import quote

# Hidden marker so the workflow can find and update its own PR comment.
MARKER = "<!-- climateclaw-backend-benchmark -->"


def _fmt(x: float) -> str:
    return "n/a" if math.isnan(x) else f"{x:.1f}"


def _change(base: float, head: float, higher_is_better: bool) -> str:
    if math.isnan(base) or math.isnan(head) or base == 0:
        return ""
    pct = (head - base) / base * 100
    better = pct > 0 if higher_is_better else pct < 0
    icon = "🟢" if better and abs(pct) >= 5 else "🔴" if abs(pct) >= 5 else "⚪"
    return f"{icon} {pct:+.0f}%"


# Mermaid's xychart has no legend: pin the line colours and print a legend
# with matching coloured squares next to the charts.
BASE_COLOR, BASE_SQUARE = "#1f6feb", "🟦"
HEAD_COLOR, HEAD_SQUARE = "#f0883e", "🟧"
_CHART_THEME = (
    '%%{init: {"themeVariables": {"xyChart": {"plotColorPalette": "'
    + f"{BASE_COLOR}, {HEAD_COLOR}"
    + '"}}}}%%'
)


def _chart(title: str, y_label: str, users: list[int], base: list, head: list):
    def series(xs: list[float]) -> str:
        return "[" + ", ".join("0" if math.isnan(x) else f"{x:.1f}" for x in xs) + "]"

    return "\n".join(
        [
            "```mermaid",
            _CHART_THEME,
            "xychart-beta",
            f'    title "{title}"',
            f'    x-axis "concurrent users" [{", ".join(map(str, users))}]',
            f'    y-axis "{y_label}"',
            f"    line {series(base)}",
            f"    line {series(head)}",
            "```",
        ]
    )


def _geomean_ratio(pairs: list[tuple[float, float]]) -> float:
    """Geometric mean of num/den over pairs where both are valid and > 0."""
    logs = [
        math.log(num / den)
        for num, den in pairs
        if not (math.isnan(num) or math.isnan(den)) and num > 0 and den > 0
    ]
    return math.exp(sum(logs) / len(logs)) if logs else float("nan")


def _badge(label: str, ratio: float) -> str:
    """shields.io badge for an improvement ratio (> 1 is better)."""
    if math.isnan(ratio):
        value, color = "n/a", "lightgrey"
    else:
        value = f"×{ratio:.2f}"
        color = (
            "brightgreen" if ratio >= 1.05 else "red" if ratio <= 0.95 else "lightgrey"
        )

    def esc(s: str) -> str:  # shields.io static badge escaping
        return quote(s.replace("-", "--").replace("_", "__").replace(" ", "_"))

    return f"![{label}: {value}](https://img.shields.io/badge/{esc(label)}-{esc(value)}-{color})"


def _valid(b: dict, h: dict) -> bool:
    """Both sides completed requests at this level, so they can be compared."""
    return b["ok"] > 0 and h["ok"] > 0


def badges(component: str, base: dict[int, dict], head: dict[int, dict]) -> str:
    users = [u for u in sorted(set(base) & set(head)) if _valid(base[u], head[u])]
    throughput = _geomean_ratio([(head[u]["rps"], base[u]["rps"]) for u in users])
    # Latency improvement = base / head, so > 1 is better here too.
    latency = _geomean_ratio([(base[u]["p95_ms"], head[u]["p95_ms"]) for u in users])
    return " ".join(
        [
            _badge(f"{component} throughput", throughput),
            _badge(f"{component} p95 latency", latency),
        ]
    )


def label(name: str, sha: str, repo_url: str) -> str:
    """``name @ sha`` (sha linked when the repo URL is known)."""
    if not sha:
        return f"`{name}`"
    short = sha[:7]
    commit = f"[`{short}`]({repo_url}/commit/{sha})" if repo_url else f"`{short}`"
    # A commit given directly needs no separate name.
    return commit if sha.startswith(name) else f"`{name}` @ {commit}"


def _row(users: int, b: dict, h: dict) -> str:
    b_err, h_err = sum(b["errors"].values()), sum(h["errors"].values())
    err_icon = "🔴 " if h_err > b_err else "🟢 " if h_err < b_err else ""
    cells = [
        str(users),
        f"{_fmt(b['rps'])} → **{_fmt(h['rps'])}** "
        f"{_change(b['rps'], h['rps'], True)}".strip(),
        f"{_fmt(b['p95_ms'])} → **{_fmt(h['p95_ms'])}** "
        f"{_change(b['p95_ms'], h['p95_ms'], False)}".strip(),
        f"{err_icon}{b_err} → **{h_err}**",
    ]
    if not _valid(b, h):
        cells = [f"~~{c}~~" for c in cells]
    return "| " + " | ".join(cells) + " |"


def render_component(
    component: str,
    base: dict[int, dict],
    head: dict[int, dict],
    base_label: str,
    head_label: str,
) -> str:
    users = sorted(set(base) & set(head))
    out = [
        f"### {component.capitalize()}",
        "",
        "| Concurrency | req/s | p95 latency (ms) | errors |",
        "|---:|:--|:--|:--|",
        *(_row(u, base[u], head[u]) for u in users),
    ]
    if not all(_valid(base[u], head[u]) for u in users):
        out += [
            "",
            "~~Struck-through~~ rows: one side completed no requests in time "
            "(overloaded), so they can't be compared and are left out of the badges.",
        ]
    out += [
        "",
        f"{BASE_SQUARE} **base** {base_label} &nbsp;&nbsp; "
        f"{HEAD_SQUARE} **head** {head_label}",
        "",
        # Blank lines around the fences make GitHub render Markdown in cells.
        "<table><tr><td>",
        "",
        _chart(
            "Throughput (higher is better)",
            "req/s",
            users,
            [base[u]["rps"] for u in users],
            [head[u]["rps"] for u in users],
        ),
        "",
        "</td><td>",
        "",
        _chart(
            "p95 latency (lower is better)",
            "ms",
            users,
            [base[u]["p95_ms"] for u in users],
            [head[u]["p95_ms"] for u in users],
        ),
        "",
        "</td></tr></table>",
    ]
    return "\n".join(out)


def render(
    components: list[tuple[str, dict[int, dict], dict[int, dict]]],
    base_label: str,
    head_label: str,
) -> str:
    """Report with a shared header and one section per component."""
    out = [
        MARKER,
        "## Benchmark",
        "",
        *(badges(name, base, head) for name, base, head in components),
        "",
        "Improvement ratios, head vs base, averaged (geometric mean) over all user "
        "counts; above ×1 is better for both.",
        "",
        "| | ref |",
        "|---|---|",
        f"| **base** | {base_label} |",
        f"| **head** | {head_label} |",
        "",
        "Both run back to back on the same runner. Dependencies are instant "
        "stand-ins, so each component's own cost is measured. Changes under 5% "
        "are within noise.",
    ]
    for name, base, head in components:
        out += ["", render_component(name, base, head, base_label, head_label)]
    return "\n".join(out) + "\n"
