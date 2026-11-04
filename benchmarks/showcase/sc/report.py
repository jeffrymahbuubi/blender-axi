#!/usr/bin/env python3
"""Aggregate showcase runs into runs.json, results.md, side_by_side PNGs and the README block.

    python report.py --runs .tmp/showcase/runs --results benchmarks/showcase/results [--readme README.md]

Pillow is imported inside the functions that draw (dev extra `showcase`).
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import date
from pathlib import Path

_S = Path(__file__).resolve().parents[1]
if str(_S) not in sys.path:
    sys.path.insert(0, str(_S))

from sc import scrub  # noqa: E402

PROJECTS_DIR = _S / "projects"
ARMS = ("axi", "mcp")
ARM_LABEL = {"axi": "blender-axi", "mcp": "official MCP"}
START, END = "<!-- showcase:start -->", "<!-- showcase:end -->"
RESULTS_START, RESULTS_END = "<!-- showcase-results:start -->", "<!-- showcase-results:end -->"
BENCH_END = "<!-- bench:end -->"
RESULTS_REL = "benchmarks/showcase/results"
PNG_CAP = 400_000

# Requirements section 2, verbatim (each lettered item is one bullet).
MCP_BETTER_TITLE = "Where the official MCP is equal or better"
MCP_BETTER = [
    "(a) one-shot builds — a competent agent writes one `execute_blender_code` script and finishes #3 in 2–4 tool calls, "
    "so tokens may tie or MCP may win on wall time (blender-axi starts Blender once per command);",
    "(b) scene overview (`scene` saved only 2.9–11.3% in the 0.6.0 bench);",
    "(c) fixes that have no typed blender-axi command (purging orphan data, deleting collections, applying transforms) "
    "force blender-axi's agent to `exec` too, i.e. parity;",
    "(d) `search_api_docs`/`get_python_api_docs` give the MCP agent API help blender-axi lacks;",
    "(e) anything GUI (screenshots, viewport jumps) is MCP-only and out of this headless showcase;",
    "(f) the MCP arm keeps one Blender process alive, blender-axi pays a Blender start per command unless it batches "
    "with `apply`.",
]

METRICS = ("total_cost_usd", "input_total", "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens",
           "output_tokens", "thinking_tokens", "num_turns", "tool_calls", "errors", "duration_s", "blender_starts",
           "first_turn_cache_creation")


# --------------------------------------------------------------------------- aggregate

def _metrics(run: dict) -> dict:
    u = run.get("usage") or {}
    i, cc, cr = (u.get("input_tokens", 0) or 0, u.get("cache_creation_input_tokens", 0) or 0,
                 u.get("cache_read_input_tokens", 0) or 0)
    return {
        "total_cost_usd": run.get("total_cost_usd", 0.0) or 0.0,
        "input_total": i + cc + cr, "input_tokens": i,
        "cache_creation_input_tokens": cc, "cache_read_input_tokens": cr,
        "output_tokens": u.get("output_tokens", 0) or 0, "thinking_tokens": u.get("thinking_tokens", 0) or 0,
        "num_turns": run.get("num_turns", 0) or 0, "tool_calls": len(run.get("tool_calls") or []),
        "errors": run.get("errors", 0) or 0, "duration_s": run.get("duration_s", 0.0) or 0.0,
        "blender_starts": run.get("blender_starts", 0) or 0,
        "first_turn_cache_creation": run.get("first_turn_cache_creation", 0) or 0,
    }


def _num(x: float):
    x = round(float(x), 4)
    return int(x) if x == int(x) else x


def _stat(rows: list[dict], fn) -> dict:
    return {k: _num(fn([r[k] for r in rows])) for k in METRICS}


def aggregate(runs_root) -> dict:
    root = Path(runs_root)
    runs = []
    for f in sorted(root.rglob("run.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        chk = data.get("check") or {}
        runs.append({
            "project": data.get("project"), "arm": data.get("arm"), "repeat": data.get("repeat"),
            "model": data.get("model"), "effort": data.get("effort"), "stop": data.get("stop"),
            "started": data.get("started"), "passed": bool(chk.get("passed")),
            "failed_checks": sorted(k for k, v in (chk.get("checks") or {}).items() if not v),
            "metrics": _metrics(data), "versions": {
                "claude": data.get("claude_version"), "blender_axi": data.get("blender_axi"),
                "blender": data.get("blender"), "mcp_server": data.get("mcp_server")},
            "transcript": data.get("transcript"),
        })
    runs.sort(key=lambda r: (str(r["project"]), str(r["arm"]), r["repeat"] or 0))
    cells: dict = {}
    for project in dict.fromkeys(r["project"] for r in runs):
        for arm in ARMS:
            rs = [r for r in runs if r["project"] == project and r["arm"] == arm]
            if not rs:
                continue
            ms = [r["metrics"] for r in rs]
            ok = sum(r["passed"] for r in rs)
            cells.setdefault(project, {})[arm] = {
                "n": len(rs), "passed": ok, "success": f"{ok}/{len(rs)}",
                "median": _stat(ms, statistics.median), "min": _stat(ms, min), "max": _stat(ms, max)}
    first = runs[0] if runs else {}
    started = sorted(r["started"] for r in runs if r.get("started"))
    return {"schema": 1, "date": (started[-1][:10] if started else date.today().isoformat()),
            "model": ", ".join(sorted({r["model"] for r in runs if r["model"]})) or None,
            "effort": ", ".join(sorted({r["effort"] for r in runs if r["effort"]})) or None,
            "versions": first.get("versions", {}), "runs": runs, "cells": cells}


# --------------------------------------------------------------------------- tables

def _fmt(key: str, v) -> str:
    if key == "total_cost_usd":
        return f"${v:.2f}"
    if key == "duration_s":
        return f"{v:.0f}s"
    return f"{int(round(v)):,}"


def _cell_value(cell: dict, key: str) -> str:
    med = _fmt(key, cell["median"][key])
    lo, hi = _fmt(key, cell["min"][key]), _fmt(key, cell["max"][key])
    return med if lo == hi else f"{med}<br><sub>{lo}–{hi}</sub>"


def summary_table(agg: dict) -> str:
    head = ("| Project | Arm | Passed | Input tokens | Output tokens | Cost (est.) | Turns | Tool calls | Wall |\n"
            "|---|---|---:|---:|---:|---:|---:|---:|---:|")
    rows = []
    for project, arms in agg["cells"].items():
        for arm in ARMS:
            c = arms.get(arm)
            if not c:
                continue
            rows.append(f"| {project} | {ARM_LABEL[arm]} | {c['success']} | " + " | ".join(
                _cell_value(c, k) for k in ("input_total", "output_tokens", "total_cost_usd", "num_turns",
                                            "tool_calls", "duration_s")) + " |")
    return "\n".join([head, *rows])


def _repeat_table(agg: dict, project: str) -> str:
    out = ["| Arm | Repeat | Passed | Failed checks | Stop | Input | Output | Cost | Turns | Tool calls | Errors | Wall |",
           "|---|---:|---:|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in agg["runs"]:
        if r["project"] != project:
            continue
        m = r["metrics"]
        out.append(f"| {ARM_LABEL.get(r['arm'], r['arm'])} | {r['repeat']} | {'yes' if r['passed'] else 'NO'} | "
                   f"{', '.join(r['failed_checks']) or '-'} | {r['stop']} | {_fmt('input_total', m['input_total'])} | "
                   f"{_fmt('output_tokens', m['output_tokens'])} | {_fmt('total_cost_usd', m['total_cost_usd'])} | "
                   f"{m['num_turns']} | {m['tool_calls']} | {m['errors']} | {_fmt('duration_s', m['duration_s'])} |")
    return "\n".join(out)


def _caption(c: dict | None) -> str:
    if not c:
        return "no runs"
    m = c["median"]
    return (f"{int(m['input_total'] + m['output_tokens']):,} tokens · ${m['total_cost_usd']:.2f} · "
            f"{int(round(m['num_turns']))} turns · passed {c['success']}")


# --------------------------------------------------------------------------- images

def _font(size: int):
    from PIL import ImageFont
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # old Pillow without sized default font
        return ImageFont.load_default()


def _pane(path, size):
    from PIL import Image, ImageDraw
    try:
        im = Image.open(path).convert("RGB")
        im.thumbnail(size)
        tile = Image.new("RGB", size, (30, 30, 30))
        tile.paste(im, ((size[0] - im.width) // 2, (size[1] - im.height) // 2))
        return tile
    except (OSError, ValueError, TypeError):
        tile = Image.new("RGB", size, (90, 90, 90))
        ImageDraw.Draw(tile).text((12, size[1] // 2 - 8), "no image", fill=(230, 230, 230), font=_font(16))
        return tile


def side_by_side(project, axi_png, mcp_png, cell, out) -> Path:
    """Two panes (axi left, mcp right) with a title and a caption per pane; PNG without text chunks."""
    from PIL import Image, ImageDraw
    pw, ph, pad, top, bottom = 640, 360, 12, 44, 40
    cell = cell or {}
    canvas = Image.new("RGB", (pw * 2 + pad * 3, top + ph + bottom + pad), (24, 24, 28))
    d = ImageDraw.Draw(canvas)
    d.text((pad, 10), f"{project}: blender-axi (left) vs official MCP (right)", fill=(240, 240, 240), font=_font(22))
    for i, (arm, png) in enumerate((("axi", axi_png), ("mcp", mcp_png))):
        x = pad + i * (pw + pad)
        canvas.paste(_pane(png, (pw, ph)), (x, top))
        d.text((x, top + ph + 6), f"{ARM_LABEL[arm]}  |  {_caption(cell.get(arm))}", fill=(220, 220, 220), font=_font(15))
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, format="PNG", optimize=True)
    if out.stat().st_size > PNG_CAP:  # noisy renders: 256-colour palette keeps it under the cap
        canvas.convert("P", palette=Image.ADAPTIVE, colors=256).save(out, format="PNG", optimize=True)
    scrub.scrub_png(out)
    return out


# --------------------------------------------------------------------------- markdown

def _prompt(project: str) -> str:
    p = PROJECTS_DIR / project / "prompt.md"
    return p.read_text(encoding="utf-8").strip() if p.is_file() else "(prompt.md not found)"


def _mcp_section(level: str = "##") -> str:
    return f"{level} {MCP_BETTER_TITLE}\n\n" + "\n".join(f"- {x}" for x in MCP_BETTER)


def _fixed_cost(agg: dict) -> str:
    out = ["| Project | " + " | ".join(f"{ARM_LABEL[a]} first-turn cache creation (tokens)" for a in ARMS) + " |",
           "|---|---:|---:|"]
    for project, arms in agg["cells"].items():
        out.append(f"| {project} | " + " | ".join(
            _fmt("x", arms[a]["median"]["first_turn_cache_creation"]) if a in arms else "-" for a in ARMS) + " |")
    return "\n".join(out)


def write_results_md(agg: dict, results_dir) -> Path:
    results_dir = Path(results_dir)
    v = agg.get("versions") or {}
    mcp = v.get("mcp_server") or {}
    ns = sorted({c["n"] for arms in agg["cells"].values() for c in arms.values()})
    L = ["# blender-axi showcase results", "",
         f"Date {agg['date']} · model {agg['model']} · effort {agg['effort']} · repeats per cell {', '.join(map(str, ns))} · "
         f"blender-axi {v.get('blender_axi')} · Blender {v.get('blender')} · claude {v.get('claude')} · "
         f"official MCP server {mcp.get('version')} ({mcp.get('commit')}).", "",
         "All costs are client-side list-price estimates from `claude -p`, not invoices. Cells show the median of the repeats, "
         "with min–max below it.", "",
         "## Summary", "", summary_table(agg), ""]
    for project in agg["cells"]:
        L += [f"## {project}", "", "Prompt (identical for both arms):", "", "```text", _prompt(project), "```", "",
              f"![{project}: side by side]({project}/side_by_side.png)", "",
              "Replays: " + " · ".join(f"[{ARM_LABEL[a]}]({project}/replay_{a}.gif)" for a in ARMS)
              + ". Transcripts: " + " · ".join(f"[{ARM_LABEL[a]}]({project}/transcript_{a}.md)" for a in ARMS) + ".", "",
              "Every repeat (failures are reported, never re-rolled):", "", _repeat_table(agg, project), ""]
    L += ["## Fixed cost per session", "",
          "First-turn cache creation: the Claude Code system prompt plus the instrument's own text (26 MCP tool schemas for "
          "the MCP arm, the appended SKILL.md for blender-axi). Measured per run, median shown.", "", _fixed_cost(agg), "",
          _mcp_section(), "",
          "## Method and fairness", "",
          "- Same prompt, model, effort, starting .blend, runner (`claude -p` stream-json), working directory shape and "
          "tool budget (`Read`, `Write` plus exactly one instrument) for both arms.",
          "- blender-axi arm: `Bash` restricted to `blender-axi *`, SKILL.md appended to the system prompt. "
          "MCP arm: the official server against a headless add-on server on the same start file, no skill text.",
          "- Success is decided by a plain `bpy` checker, not by either tool and not by the agent's own claim.",
          "- N repeats per cell, sequential; Blender processes started are counted from the transcript (blender-axi) or fixed "
          "at 1 (MCP).", "",
          "### Honesty rules", "",
          "- Every repeat is listed; failed or cut runs stay in the table and count against the success column.",
          "- Wall time is structurally biased: the MCP arm keeps one Blender alive, blender-axi pays a start per command "
          "unless it batches with `apply`. It is reported, not claimed.",
          "- The list above states where the official MCP is equal or better.", "",
          "## Reproduce", "",
          "```bash", ".venv/Scripts/python.exe benchmarks/showcase/run_ab.py --project all --arm both --repeats 3 "
          "--model claude-opus-5-5", "```", "",
          "See [benchmarks/showcase/README.md](../README.md) for prerequisites.", ""]
    path = results_dir / "results.md"
    results_dir.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L), encoding="utf-8", newline="\n")
    return path


def readme_block(agg: dict) -> str:
    shots = "\n".join(f"![{p}: blender-axi (left) vs official MCP (right)]({RESULTS_REL}/{p}/side_by_side.png)" for p in agg["cells"])
    return "\n".join([
        "## Showcase: three real tasks, MCP vs blender-axi", "",
        f"Same prompt, model ({agg['model']}), effort ({agg['effort']}) and start file for both arms; success judged by a "
        f"plain bpy checker; measured {agg['date']}. Cells are medians (min–max below).", "",
        summary_table(agg), "", shots, "",
        _mcp_section("###"), "",
        f"Replay GIFs, transcripts and every repeat: [results.md]({RESULTS_REL}/results.md). "
        "Method: [benchmarks/showcase/README.md](benchmarks/showcase/README.md).", ""])


# --------------------------------------------------------------------------- splicing

def _splice(text: str, start: str, end: str, block: str, after: str | None = None) -> str:
    nl = "\r\n" if "\r\n" in text else "\n"
    inner = start + nl + block.strip("\r\n").replace("\r\n", "\n").replace("\n", nl) + nl + end
    s = text.find(start)
    if s >= 0:
        e = text.find(end, s + len(start))
        if e < 0:
            raise ValueError(f"marker not found after the start marker: {end}")
        return text[:s] + inner + text[e + len(end):]
    a = text.find(after) if after else -1
    if a >= 0:
        a += len(after)
        return text[:a] + nl + nl + inner + text[a:]
    return text.rstrip("\r\n") + nl + nl + inner + nl


def splice_readme(path, block: str) -> None:
    """Replace the showcase block in README (markers created right after the bench:end marker if absent)."""
    p = Path(path)
    text = p.read_bytes().decode("utf-8")
    new = _splice(text, START, END, block, after=BENCH_END)
    if new != text:
        p.write_bytes(new.encode("utf-8"))


def splice_showcase_readme(path, agg: dict) -> None:
    """Put the summary table between the showcase-results markers of benchmarks/showcase/README.md."""
    p = Path(path)
    text = p.read_bytes().decode("utf-8")
    new = _splice(text, RESULTS_START, RESULTS_END, summary_table(agg) + f"\n\n(measured {agg['date']}, "
                                                    f"{agg['model']}, effort {agg['effort']})")
    if new != text:
        p.write_bytes(new.encode("utf-8"))


# --------------------------------------------------------------------------- driver

def run(runs_root, results_dir, readme=None) -> dict:
    """aggregate -> runs.json, side_by_side per project (from results/<p>/final_*.png), results.md, optional README."""
    agg = aggregate(runs_root)
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    for project, cell in agg["cells"].items():
        d = results_dir / project
        side_by_side(project, d / "final_axi.png", d / "final_mcp.png", cell, d / "side_by_side.png")
    (results_dir / "runs.json").write_text(json.dumps(agg, indent=2) + "\n", encoding="utf-8", newline="\n")
    write_results_md(agg, results_dir)
    if readme:
        splice_readme(readme, readme_block(agg))
    return agg


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", required=True, help="runs root, e.g. .tmp/showcase/runs")
    ap.add_argument("--results", required=True, help="results dir, e.g. benchmarks/showcase/results")
    ap.add_argument("--readme", help="README to splice the showcase block into (root README, lead only)")
    args = ap.parse_args(argv)
    try:
        agg = run(args.runs, args.results, args.readme)
    except ImportError as exc:
        print(f"error: {exc}. Fix: .venv/Scripts/python.exe -m pip install pillow", file=sys.stderr)
        return 2
    offences = scrub.check(args.results)
    for o in offences:
        print(o)
    print(f"report: {len(agg['runs'])} runs, {len(offences)} scrub offence(s)")
    return 1 if offences else 0


if __name__ == "__main__":
    sys.exit(main())
