"""Hand-built SVG chart for the token benchmark (stdlib only, plan decision K9).

Horizontal grouped bars on a log10 axis, one row per (scene, task) that has an official-MCP value, plus the
fixed-cost row. Every measured value is exactly one <rect class="bar ...">; nothing else in the file is a <rect>.
"""

from __future__ import annotations

import math
from xml.sax.saxutils import escape, quoteattr

from .report import fmt_int, unit_of, value_of

WIDTH = 900
ROW_H = 40
BAR_H = 14
LABEL_RIGHT = 250  # row labels end here
PLOT_X0 = 262
PLOT_X1 = 860
PLOT_TOP = 70
MIN_EXP = 6  # the axis covers at least 1 .. 10^6
MAX_LABEL_CHARS = 34
SUPERSCRIPT = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")

STYLE = """
:root{--bg:#ffffff;--fg:#1f2328;--muted:#59636e;--grid:#d1d9e0;--mcp:#b34700;--axi:#0072b2;--on-bar:#ffffff}
@media (prefers-color-scheme: dark){:root{--bg:#0d1117;--fg:#e6edf3;--muted:#9198a1;--grid:#30363d;--mcp:#f0884a;--axi:#56b4e9;--on-bar:#0d1117}}
.bg{fill:var(--bg)}
text{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;fill:var(--fg);font-size:12px}
.title{font-size:15px;font-weight:600}
.muted{fill:var(--muted)}
.tick{fill:var(--muted);font-size:11px}
.grid{stroke:var(--grid);stroke-width:1}
.sep{stroke:var(--grid);stroke-width:1;stroke-dasharray:3 3}
.bar.mcp,.dot.mcp{fill:var(--mcp)}
.bar.axi,.dot.axi{fill:var(--axi)}
.val{font-size:11px}
.val.in{fill:var(--on-bar);font-weight:600}
""".strip()


def _clip(label: str) -> str:
    return label if len(label) <= MAX_LABEL_CHARS else label[: MAX_LABEL_CHARS - 1] + "…"


def _rows(results: dict, unit: str) -> list[dict]:
    """Chart rows: {label, mcp, axi, tool} for tasks with an MCP value, then the fixed-cost row."""
    rows = []
    for sname, scene in (results.get("scenes") or {}).items():
        for t in scene.get("tasks", []):
            mcp = value_of(t.get("mcp"), unit)
            if t.get("error") or mcp is None:
                continue
            rows.append(
                {
                    "label": f"{sname} · {t.get('label') or t.get('id')}",
                    "mcp": mcp,
                    "axi": value_of(t.get("axi"), unit),
                    "mcp_what": (t.get("mcp") or {}).get("tool", "official MCP"),
                    "axi_what": (t.get("axi") or {}).get("cmd", "blender-axi"),
                }
            )
    fixed = results.get("fixed") or {}
    mcp, skill = value_of(fixed.get("mcp_tools_list"), unit), value_of(fixed.get("axi_skill"), unit)
    if mcp is not None or skill is not None:
        rows.append(
            {
                "label": "fixed · tool list vs SKILL.md",
                "mcp": mcp,
                "axi": skill,
                "mcp_what": "tools/list (lower bound)",
                "axi_what": "SKILL.md",
                "fixed": True,
            }
        )
    return rows


def _max_exp(rows: list[dict]) -> int:
    top = max((v for r in rows for v in (r["mcp"], r["axi"]) if v is not None), default=1)
    return max(MIN_EXP, math.ceil(math.log10(max(top, 1))))


def _bar_width(value: float, max_exp: int) -> float:
    """Pixel width on the log axis; values <= 1 clamp to 1 px."""
    frac = math.log10(max(value, 1)) / max_exp
    return max(1.0, frac * (PLOT_X1 - PLOT_X0))


def _bar(x: float, y: float, value: float, who: str, what: str, unit_name: str, w: float) -> list[str]:
    cls = "mcp" if who == "mcp" else "axi"
    who_name = "Official MCP" if who == "mcp" else "blender-axi"
    tip = escape(f"{who_name} ({what}): {int(value)} {unit_name}")
    out = [
        f'<rect class="bar {cls}" x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{BAR_H}" rx="2">'
        f"<title>{tip}</title></rect>"
    ]
    text = fmt_int(value)
    inside = x + w + 8 + 6.5 * len(text) > WIDTH - 10  # no room to the right: label inside the bar end
    if inside:
        out.append(f'<text class="val in" x="{x + w - 6:.1f}" y="{y + BAR_H - 3:.1f}" text-anchor="end">{text}</text>')
    else:
        out.append(f'<text class="val" x="{x + w + 6:.1f}" y="{y + BAR_H - 3:.1f}">{text}</text>')
    return out


def chart_svg(results: dict) -> str:
    unit = unit_of(results)
    unit_name = "tokens" if unit == "tokens" else "chars"
    rows = _rows(results, unit)
    max_exp = _max_exp(rows)
    height = ROW_H * len(rows) + 120
    plot_bottom = PLOT_TOP + ROW_H * len(rows)
    title = f"{'Tokens' if unit == 'tokens' else 'Characters'} the agent reads per call (log scale, lower is better)"
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height}" width="{WIDTH}" height="{height}" '
        f"role=\"img\" aria-label={quoteattr(title)}>",
        f"<style>{STYLE}</style>",
        f'<path class="bg" d="M0 0H{WIDTH}V{height}H0Z"/>',
        f'<text class="title" x="16" y="26">{escape(title)}</text>',
        '<circle class="dot mcp" cx="20" cy="46" r="5"/><text x="30" y="50">Official MCP</text>',
        '<circle class="dot axi" cx="140" cy="46" r="5"/><text x="150" y="50">blender-axi</text>',
    ]
    for k in range(max_exp + 1):
        x = PLOT_X0 + (PLOT_X1 - PLOT_X0) * k / max_exp
        parts.append(f'<line class="grid" x1="{x:.1f}" y1="{PLOT_TOP}" x2="{x:.1f}" y2="{plot_bottom}"/>')
        label = "1" if k == 0 else "10" + str(k).translate(SUPERSCRIPT)
        parts.append(f'<text class="tick" x="{x:.1f}" y="{plot_bottom + 16}" text-anchor="middle">{label}</text>')
    parts.append(
        f'<text class="tick" x="{(PLOT_X0 + PLOT_X1) / 2:.1f}" y="{plot_bottom + 36}" text-anchor="middle">'
        f"{unit_name} per call</text>"
    )
    for i, r in enumerate(rows):
        top = PLOT_TOP + ROW_H * i
        if r.get("fixed") and i > 0:
            parts.append(f'<line class="sep" x1="16" y1="{top}" x2="{WIDTH - 16}" y2="{top}"/>')
        parts.append(
            f'<text x="{LABEL_RIGHT}" y="{top + ROW_H / 2 + 4:.1f}" text-anchor="end">{escape(_clip(r["label"]))}</text>'
        )
        y = top + (ROW_H - 2 * BAR_H - 2) / 2
        for j, who in enumerate(("mcp", "axi")):
            v = r[who]
            if v is None:
                continue
            what = r["mcp_what"] if who == "mcp" else r["axi_what"]
            parts += _bar(PLOT_X0, y + j * (BAR_H + 2), v, who, what, unit_name, _bar_width(v, max_exp))
    parts.append("</svg>")
    return "\n".join(parts) + "\n"
