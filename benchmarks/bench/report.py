"""Markdown report and README block for the token benchmark (pure functions, plan section 4.3).

Reads the `results.json` dict (schema 1, plan section 4.2); never touches the filesystem.
"""

from __future__ import annotations

NA = "—"  # em dash: nothing measured / not applicable
START_MARKER = "<!-- bench:start -->"
END_MARKER = "<!-- bench:end -->"
README_RESULTS_DIR = "benchmarks/results"
TOOL_LIST_CAVEAT = (
    "The tool-list figure is a lower bound: it is the compact JSON of the server's `tools/list` reply, "
    "and real clients serialise tool schemas more verbosely."
)
REPRODUCE_CMD = ".venv/Scripts/python.exe benchmarks/token_bench.py"
KEY_VARS = "`BLENDER_AXI_ANTHROPIC_KEY` (recommended, e.g. via the .env launcher) or `ANTHROPIC_API_KEY`"


# ---------------------------------------------------------------------------------------------------------------
# shared helpers (also used by svg.py)
# ---------------------------------------------------------------------------------------------------------------


def unit_of(results: dict) -> str:
    """The active unit, "tokens" or "chars"; anything else counts as chars."""
    return "tokens" if results.get("unit") == "tokens" else "chars"


def unit_label(results: dict) -> str:
    """Column-header text: `chars`, or `tokens (<model>)`."""
    if unit_of(results) == "tokens":
        model = results.get("token_model")
        return f"tokens ({model})" if model else "tokens"
    return "chars"


def value_of(measure: dict | None, unit: str) -> int | None:
    """The measurement in the active unit, or None when it is absent (never falls back to another unit)."""
    if not isinstance(measure, dict):
        return None
    v = measure.get("tokens" if unit == "tokens" else "chars")
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def fmt_int(n: int | float) -> str:
    return f"{int(n):,}"


def fmt_pct(p: float | None) -> str:
    """`84.9%`, negatives as `-0.4%`, nothing as an em dash."""
    return NA if p is None else f"{p:.1f}%"


def saving_of(task: dict, unit: str) -> float | None:
    """Stored saving_pct when present, else computed from the active-unit values; None for axi-only rows."""
    if task.get("mcp") is None:
        return None
    if task.get("saving_pct") is not None:
        return task["saving_pct"]
    return computed_saving(value_of(task.get("axi"), unit), value_of(task.get("mcp"), unit))


def computed_saving(axi: float | None, mcp: float | None) -> float | None:
    if axi is None or not mcp:
        return None
    return round(100 * (1 - axi / mcp), 1)


def _cell(text: str) -> str:
    """Make text safe inside one Markdown table cell."""
    return " ".join(str(text).split()).replace("|", "\\|")


def _plural(n: int | None, word: str) -> str:
    if n is None:
        return f"? {word}s"
    return f"{n} {word}" + ("" if n == 1 else "s")


def _measure_cell(measure: dict | None, unit: str, prefix: str, extra: str = "") -> str:
    """`4,214 ([raw](desk/objects__mcp.json))`, or an em dash when there is no value."""
    v = value_of(measure, unit)
    if v is None:
        return NA
    out = fmt_int(v)
    raw = measure.get("raw")
    parts = []
    if extra:
        parts.append(extra)
    if raw:
        parts.append(f"[{'raw, truncated' if measure.get('truncated') else 'raw'}]({prefix}{raw})")
    return f"{out} ({', '.join(parts)})" if parts else out


def _task_note(task: dict) -> str:
    notes = []
    if task.get("note"):
        notes.append(task["note"])
    reused = (task.get("mcp") or {}).get("reused_from")
    if reused:
        notes.append(f"MCP call shared with {reused}")
    return "; ".join(notes)


def _task_row(task: dict, unit: str, prefix: str) -> str:
    label = _cell(task.get("label") or task.get("id") or "?")
    if task.get("error"):
        return f"| {label} | {NA} | {NA} | {NA} | error: {_cell(task['error'])} |"
    mcp = task.get("mcp")
    return (
        f"| {label} | {_measure_cell(mcp, unit, prefix)} | {_measure_cell(task.get('axi'), unit, prefix)} "
        f"| {fmt_pct(saving_of(task, unit))} | {_cell(_task_note(task))} |"
    )


def _fixed_values(results: dict) -> tuple[dict | None, dict | None]:
    fixed = results.get("fixed") or {}
    return fixed.get("mcp_tools_list"), fixed.get("axi_skill")


def _fixed_row(results: dict, prefix: str, skill_prefix: str) -> str:
    unit = unit_of(results)
    mcp, skill = _fixed_values(results)
    n_tools = (mcp or {}).get("tools")
    mcp_cell = _measure_cell(mcp, unit, prefix, _plural(n_tools, "tool") if n_tools is not None else "")
    axi_cell = _measure_cell(skill, unit, skill_prefix)
    pct = computed_saving(value_of(skill, unit), value_of(mcp, unit))
    return f"| Tool definitions vs. skill text | {mcp_cell} | {axi_cell} | {fmt_pct(pct)} |"


def _fixed_table(results: dict, prefix: str, skill_prefix: str) -> list[str]:
    ul = unit_label(results)
    return [
        f"| Item | Official MCP, {ul} | blender-axi, {ul} | Saving |",
        "|---|---:|---:|---:|",
        _fixed_row(results, prefix, skill_prefix),
    ]


def _task_table(scene: dict, results: dict, prefix: str) -> list[str]:
    ul, unit = unit_label(results), unit_of(results)
    lines = [
        f"| Task | Official MCP, {ul} | blender-axi, {ul} | Saving | Note |",
        "|---|---:|---:|---:|---|",
    ]
    lines += [_task_row(t, unit, prefix) for t in scene.get("tasks", [])]
    return lines


def _header_line(results: dict) -> str:
    srv = results.get("mcp_server") or {}
    server = " ".join(str(srv[k]) for k in ("name", "version") if srv.get(k)) or "unknown"
    if srv.get("commit"):
        server += f" ({srv['commit']})"
    return (
        f"Measured {results.get('date', 'unknown date')} · Blender {results.get('blender', '?')} · "
        f"blender-axi {results.get('blender_axi', '?')} · official server {server}"
    )


def _unit_sentence(results: dict) -> str:
    if unit_of(results) == "tokens":
        return f"Unit: **tokens**, counted with `{results.get('token_model') or 'the Anthropic count_tokens API'}`."
    return (
        "Unit: **characters** (a stand-in for tokens; roughly 3 to 4 characters per token for this kind of text). "
        f"Set {KEY_VARS} and re-run for exact token counts."
    )


# ---------------------------------------------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------------------------------------------


def render_markdown(results: dict) -> str:
    """Full `results.md`: header, fixed cost, per-scene tables, notes, reproduce steps."""
    prefix = ""
    out = ["# Token-savings benchmark", "", _header_line(results), "", _unit_sentence(results), ""]
    out += [
        "What the agent reads for the same question, from the same live Blender: the full stdout of "
        "`blender-axi ... --live` against the text the official `blender-mcp` server returns.",
        "",
        "![Characters or tokens per call, log scale](chart.svg)",
        "",
        "## Fixed cost per session",
        "",
    ]
    out += _fixed_table(results, prefix, "../../")
    out += ["", f"> {TOOL_LIST_CAVEAT}", ""]
    for name, scene in (results.get("scenes") or {}).items():
        out += [
            f"## Scene: {name} ({_plural(scene.get('objects'), 'object')}, "
            f"{_plural(scene.get('collections'), 'collection')})",
            "",
        ]
        if scene.get("render"):
            out += [f"![{name} scene render]({scene['render']})", ""]
        out += _task_table(scene, results, prefix)
        out.append("")
    out += [
        "## Notes",
        "",
        "- Saving is `100 * (1 - blender-axi / official MCP)` in the unit shown; a negative saving means "
        "blender-axi printed more than the official server for that task.",
        f"- {TOOL_LIST_CAVEAT}",
        "- An em dash means there is no equivalent measurement (for example a blender-axi-only row); "
        "the official server has no collection or name filters, so those rows compare against its full list.",
        "- A call repeated within one scene is made once; the later rows reuse that measurement.",
        "- Raw outputs are stored next to this file and cut at 20,000 characters (the `raw, truncated` links).",
        "- Latency is recorded in `results.json` (`ms`) but is not part of the claim.",
        "- Raw outputs are normalised so they do not depend on the machine: the checkout path is shown as "
        "`<repo>`, and the random port of the temporary server in blender-axi's hint lines is shown as `50000`.",
        "",
        "## Reproduce",
        "",
        "```",
        REPRODUCE_CMD,
        "```",
        "",
        "Needs Blender with the add-on server and the official server installed under `.tmp/mcpvenv` "
        "(see `--mcp-server`). Artifacts land in `benchmarks/results/`.",
        "",
        f"To enable exact token counts, set {KEY_VARS} and re-run; "
        "without a key the run reports characters.",
        "",
    ]
    return "\n".join(out)


def readme_block(results: dict) -> str:
    """Compact README block (inner content only, no markers): city table, renders, chart, unit line."""
    prefix = README_RESULTS_DIR + "/"
    scenes = results.get("scenes") or {}
    main = "city" if "city" in scenes else (list(scenes)[-1] if scenes else None)
    out = ["**Measured against the official `blender-mcp` server, same live Blender:**", ""]
    if main:
        scene = scenes[main]
        out += [
            f"*{main}: {_plural(scene.get('objects'), 'object')}, {_plural(scene.get('collections'), 'collection')}*",
            "",
        ]
        out += _task_table(scene, results, prefix)
        out.append("")
    out += _fixed_table(results, prefix, "")
    out += ["", f"*{TOOL_LIST_CAVEAT}*", ""]
    renders = [
        f"![{n} scene]({prefix}{s['render']})" for n, s in scenes.items() if s.get("render")
    ]
    if renders:
        out += [" ".join(renders), ""]
    out += [f"![Characters or tokens per call, log scale]({prefix}chart.svg)", ""]
    unit_line = (
        f"Unit: {unit_label(results)}, measured {results.get('date', 'unknown date')}."
        if unit_of(results) == "tokens"
        else f"Unit: characters (a stand-in for tokens), measured {results.get('date', 'unknown date')}; "
        f"set {KEY_VARS} and re-run for exact tokens."
    )
    out.append(f"{unit_line} Details: [results.md]({prefix}results.md).")
    return "\n".join(out)


def splice_readme(text: str, block: str, start: str = START_MARKER, end: str = END_MARKER) -> str:
    """Replace only what lies between the markers; everything else stays byte-identical.

    Raises ValueError when a marker is missing (or the end marker does not follow the start marker).
    """
    s = text.find(start)
    if s < 0:
        raise ValueError(f"README marker not found: {start}")
    e = text.find(end, s + len(start))
    if e < 0:
        raise ValueError(f"README marker not found after the start marker: {end}")
    nl = "\r\n" if "\r\n" in text else "\n"
    inner = block.strip("\r\n").replace("\r\n", "\n").replace("\n", nl)
    return text[: s + len(start)] + nl + inner + nl + text[e:]
