"""Unit tests for bench.report (pure functions; no Blender, no network)."""

import copy

import pytest

from bench import report

TOOLS_LIST = {"chars": 18911, "tokens": None, "tools": 26, "raw": "fixed/tools_list__mcp.json"}
SKILL = {"chars": 3225, "tokens": None, "raw": "skills/blender-axi/SKILL.md"}


def _m(name, chars, tool=None, reused=None, scene="desk", truncated=False):
    d = {"chars": chars, "tokens": None, "ms": 10, "raw": f"{scene}/{name}", "truncated": truncated, "sha256": "ab" * 32}
    if tool:
        d.update({"tool": tool, "args": {}, "reused_from": reused})
    else:
        d["cmd"] = "blender-axi x --live"
        d["exit_code"] = 0
    return d


def _task(tid, label, axi, mcp, pct, note=""):
    return {"id": tid, "label": label, "note": note, "axi": axi, "mcp": mcp, "saving_pct": pct}


@pytest.fixture
def chars_results():
    return {
        "schema": 1, "date": "2026-10-25", "unit": "chars", "token_model": None, "blender": "5.2.0 LTS",
        "blender_axi": "0.2.0", "mcp_server": {"name": "blender-mcp", "version": "1.0.2", "commit": "dbbf836"},
        "fixed": {"mcp_tools_list": dict(TOOLS_LIST), "axi_skill": dict(SKILL)},
        "scenes": {
            "desk": {"objects": 13, "collections": 1, "render": "desk/render.png", "tasks": [
                _task("objects", "List objects (13)", _m("objects__axi.txt", 636),
                      _m("objects__mcp.json", 4214, "get_objects_summary"), 84.9),
                _task("objects_json", "List objects, --json", _m("objects_json__axi.txt", 1500), None, None,
                      "TOON vs JSON, blender-axi only"),
            ]},
            "city": {"objects": 502, "collections": 21, "render": "city/render.png", "tasks": [
                _task("scene", "Scene overview", _m("scene__axi.txt", 674, scene="city"),
                      _m("scene__mcp.json", 671, "get_blendfile_summary_datablocks", scene="city"), -0.4),
                _task("objects", "List objects", _m("objects__axi.txt", 3800, scene="city"),
                      _m("objects__mcp.json", 146677, "get_objects_summary", scene="city", truncated=True), 97.4),
                _task("objects_tree", "Objects named *Tree*", _m("objects_tree__axi.txt", 900, scene="city"),
                      _m("objects_tree__mcp.json", 146677, "get_objects_summary", reused="objects", scene="city"), 99.4,
                      "MCP has no name filter: full list"),
                {"id": "object_bldg", "label": "One object: D05_Bldg03", "note": "", "error": "timeout | after 120 s"},
            ]},
        },
    }


@pytest.fixture
def tokens_results(chars_results):
    r = copy.deepcopy(chars_results)
    r["unit"], r["token_model"] = "tokens", "claude-opus-5-5"
    r["fixed"]["mcp_tools_list"]["tokens"], r["fixed"]["axi_skill"]["tokens"] = 6000, 1000
    for scene in r["scenes"].values():
        for t in scene["tasks"]:
            for side in ("axi", "mcp"):
                if t.get(side):
                    t[side]["tokens"] = t[side]["chars"] // 3
    return r


def test_markdown_chars_header_and_links(chars_results):
    md = report.render_markdown(chars_results)
    assert "| Task | Official MCP, chars | blender-axi, chars | Saving | Note |" in md
    assert "[raw](desk/objects__axi.txt)" in md and "[raw](desk/objects__mcp.json)" in md
    assert "![desk scene render](desk/render.png)" in md and "![city scene render](city/render.png)" in md
    assert "(chart.svg)" in md
    assert "4,214" in md and "84.9%" in md
    assert "Measured 2026-10-25" in md and "blender-mcp 1.0.2 (dbbf836)" in md


def test_markdown_tokens_header(tokens_results):
    md = report.render_markdown(tokens_results)
    assert "Official MCP, tokens (claude-opus-5-5)" in md
    assert "(chars)" not in md
    assert "6,000" in md and "1,404" in md  # fixed-cost tokens; desk objects mcp 4214 // 3
    assert "Unit: **tokens**" in md


def test_markdown_honest_rows(chars_results):
    md = report.render_markdown(chars_results)
    assert "-0.4%" in md  # negative saving printed as is
    axi_only = next(line for line in md.splitlines() if "List objects, --json" in line)
    assert axi_only.count("—") == 2 and "1,500" in axi_only and "blender-axi only" in axi_only
    assert "MCP call shared with objects" in md
    err = next(line for line in md.splitlines() if "D05_Bldg03" in line)
    assert "error: timeout \\| after 120 s" in err and err.count("—") == 3
    assert "raw, truncated" in md


def test_markdown_fixed_cost_caveat_and_reproduce(chars_results):
    md = report.render_markdown(chars_results)
    assert "lower bound" in md
    assert "| Tool definitions vs. skill text | 18,911 (26 tools, [raw](fixed/tools_list__mcp.json)) |" in md
    assert "../../skills/blender-axi/SKILL.md" in md and "82.9%" in md
    reproduce = md.split("## Reproduce")[1]
    assert "benchmarks/token_bench.py" in reproduce
    assert "BLENDER_AXI_ANTHROPIC_KEY" in reproduce and "ANTHROPIC_API_KEY" in reproduce


def test_markdown_tolerates_sparse_input():
    md = report.render_markdown({"schema": 1, "unit": "chars", "scenes": {}})
    assert "# Token-savings benchmark" in md and "lower bound" in md


def test_readme_block_shape(chars_results):
    block = report.readme_block(chars_results)
    assert report.START_MARKER not in block and report.END_MARKER not in block
    assert len(block.splitlines()) <= 40
    assert "*city: 502 objects, 21 collections*" in block
    assert "benchmarks/results/desk/objects" not in block  # city table only
    assert "benchmarks/results/city/objects__mcp.json" in block
    assert "![desk scene](benchmarks/results/desk/render.png)" in block
    assert "![city scene](benchmarks/results/city/render.png)" in block
    assert "(benchmarks/results/chart.svg)" in block
    assert "(skills/blender-axi/SKILL.md)" in block and "../" not in block
    last = block.splitlines()[-1]
    assert "characters" in last and "2026-10-25" in last and "BLENDER_AXI_ANTHROPIC_KEY" in last
    assert "-0.4%" in block and "lower bound" in block


def test_readme_block_tokens_unit_line(tokens_results):
    last = report.readme_block(tokens_results).splitlines()[-1]
    assert "tokens (claude-opus-5-5)" in last and "BLENDER_AXI_ANTHROPIC_KEY" not in last


def test_splice_replaces_only_inner_block():
    start, end = report.START_MARKER, report.END_MARKER
    text = f"# Title\n\nBefore.\n{start}\nold stuff\nmore old\n{end}\n\nAfter é.\n"
    out = report.splice_readme(text, "NEW\nBLOCK\n")
    assert out == f"# Title\n\nBefore.\n{start}\nNEW\nBLOCK\n{end}\n\nAfter é.\n"
    assert report.splice_readme(out, "NEW\nBLOCK") == out  # idempotent
    adjacent = f"a\n{start}\n{end}\nb\n"
    assert report.splice_readme(adjacent, "X") == f"a\n{start}\nX\n{end}\nb\n"


def test_splice_keeps_crlf_style():
    start, end = report.START_MARKER, report.END_MARKER
    out = report.splice_readme(f"a\r\n{start}\r\n{end}\r\nb\r\n", "X\nY")
    assert out == f"a\r\n{start}\r\nX\r\nY\r\n{end}\r\nb\r\n"


@pytest.mark.parametrize(
    "text", ["no markers", "<!-- bench:start -->\nonly start", "<!-- bench:end -->\n<!-- bench:start -->\n"]
)
def test_splice_missing_or_misordered_marker(text):
    with pytest.raises(ValueError):
        report.splice_readme(text, "x")


def test_splice_custom_markers():
    assert report.splice_readme("<a>\nold\n<b>", "n", "<a>", "<b>") == "<a>\nn\n<b>"
