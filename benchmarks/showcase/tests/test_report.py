"""sc/report.py on synthetic run records (no Blender, no API). Splicing is tested on copies only."""
import json
from pathlib import Path

import pytest

from sc import report, scrub

DENY = ("jdoe-box",)


def make_run(root: Path, project: str, arm: str, k: int, passed: bool, out_tok: int, cost: float, calls: int = 3):
    d = root / project / arm / f"r{k}"
    d.mkdir(parents=True)
    run = {
        "schema": 1, "project": project, "arm": arm, "repeat": k, "model": "claude-opus-5-5", "effort": "medium",
        "claude_version": "2.1.317", "blender_axi": "0.9.0", "blender": "5.2.0",
        "mcp_server": {"version": "1.0.2", "commit": "dbbf836"}, "started": f"2026-11-05T10:0{k}:00",
        "duration_s": 100.0 + k * 10, "num_turns": 5 + k, "stop": "end_turn",
        "usage": {"input_tokens": 10, "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 5000,
                  "output_tokens": out_tok, "thinking_tokens": 7},
        "total_cost_usd": cost, "first_turn_cache_creation": 9450 if arm == "axi" else 12320,
        "tool_calls": [{"n": i + 1, "tool": "Bash", "summary": "x", "result_chars": 1, "is_error": False, "ms": 5}
                       for i in range(calls)],
        "errors": 0 if passed else 2, "permission_denials": 0, "blender_starts": 1,
        "check": {"passed": passed, "checks": {"sheet": True, "hero": passed}}, "snapshots": 3,
        "transcript": "transcript.md",
    }
    (d / "run.json").write_text(json.dumps(run), encoding="utf-8")


@pytest.fixture
def runs(work):
    root = work / "runs"
    for arm, cost in (("axi", 0.30), ("mcp", 0.70)):
        make_run(root, "turntable", arm, 1, True, 1000, cost)
        make_run(root, "turntable", arm, 2, arm == "axi", 3000, cost + 0.1)  # mcp repeat 2 fails
    return root


@pytest.fixture
def agg(runs):
    return report.aggregate(runs)


def test_aggregate_cells(agg):
    assert agg["schema"] == 1 and len(agg["runs"]) == 4
    assert agg["model"] == "claude-opus-5-5" and agg["date"] == "2026-11-05"
    axi, mcp = agg["cells"]["turntable"]["axi"], agg["cells"]["turntable"]["mcp"]
    assert axi["success"] == "2/2" and mcp["success"] == "1/2" and mcp["passed"] == 1
    assert axi["median"]["output_tokens"] == 2000 and axi["min"]["output_tokens"] == 1000
    assert axi["max"]["output_tokens"] == 3000
    assert axi["median"]["input_total"] == 6010 and axi["median"]["cache_read_input_tokens"] == 5000
    assert axi["median"]["total_cost_usd"] == 0.35 and axi["median"]["tool_calls"] == 3
    assert mcp["median"]["first_turn_cache_creation"] == 12320 and mcp["median"]["errors"] == 1
    failed = [r for r in agg["runs"] if not r["passed"]]
    assert len(failed) == 1 and failed[0]["failed_checks"] == ["hero"]


def test_aggregate_empty(work):
    a = report.aggregate(work / "nothing")
    assert a["runs"] == [] and a["cells"] == {}


def test_results_md(agg, work):
    p = report.write_results_md(agg, work / "res")
    text = p.read_text(encoding="utf-8")
    assert "## Method and fairness" in text and "### Honesty rules" in text
    assert "## " + report.MCP_BETTER_TITLE in text
    for item in report.MCP_BETTER:
        assert item in text
    assert text.count("| blender-axi | ") >= 3 and "| NO |" in text  # summary row + 2 repeats, failed repeat shown
    assert "turntable/side_by_side.png" in text and "turntable/replay_mcp.gif" in text
    assert "Fixed cost per session" in text and "12,320" in text
    assert scrub.check(work / "res", DENY) == []


def test_mcp_list_is_verbatim_from_requirements():
    req = report._S.parents[1] / "docs" / "brainstorms" / "2026-11-04-showcase-requirements.md"
    if not req.is_file():
        pytest.skip("requirements doc not in this checkout")
    flat = " ".join(req.read_text(encoding="utf-8").split())
    for item in report.MCP_BETTER:
        assert " ".join(item.split()) in flat


def test_readme_block(agg):
    block = report.readme_block(agg)
    assert "benchmarks/showcase/results/turntable/side_by_side.png" in block
    assert "benchmarks/showcase/results/results.md" in block and report.MCP_BETTER[0] in block
    assert "| turntable | official MCP | 1/2 |" in block


README = "# T\n\ntext\n\n<!-- bench:start -->\nbench\n<!-- bench:end -->\n\ntail\n"


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_splice_readme_inserts_after_bench_and_is_idempotent(agg, work, newline):
    p = work / "README.md"
    p.write_bytes(README.replace("\n", newline).encode())
    block = report.readme_block(agg)
    report.splice_readme(p, block)
    once = p.read_bytes()
    text = once.decode()
    assert text.count(report.START) == 1 and text.count(report.END) == 1
    assert text.index("<!-- bench:end -->") < text.index(report.START) < text.index("tail")
    assert text.startswith(README.replace("\n", newline).split("<!-- bench:end -->")[0])
    assert text.endswith("tail" + newline)
    if newline == "\r\n":
        assert "\n" not in text.replace("\r\n", "")
    report.splice_readme(p, block)
    assert p.read_bytes() == once
    report.splice_readme(p, "NEW" + newline)  # replaces only what lies between the markers
    text2 = p.read_bytes().decode()
    assert "NEW" in text2 and "turntable" not in text2 and text2.endswith("tail" + newline)


def test_splice_readme_with_existing_markers_and_without_bench(work, agg):
    p = work / "R.md"
    p.write_text(f"a\n{report.START}\nold\n{report.END}\nz\n", encoding="utf-8", newline="")
    report.splice_readme(p, "fresh")
    assert p.read_text(encoding="utf-8") == f"a\n{report.START}\nfresh\n{report.END}\nz\n"
    q = work / "Q.md"
    q.write_text("only text\n", encoding="utf-8", newline="")
    report.splice_readme(q, "fresh")
    assert q.read_text(encoding="utf-8").endswith(f"{report.START}\nfresh\n{report.END}\n")


def test_splice_showcase_readme(work, agg):
    p = work / "S.md"
    p.write_text(f"intro\n{report.RESULTS_START}\n{report.RESULTS_END}\nend\n", encoding="utf-8", newline="")
    report.splice_showcase_readme(p, agg)
    text = p.read_text(encoding="utf-8")
    assert "| turntable | blender-axi | 2/2 |" in text and text.startswith("intro\n") and text.endswith("end\n")


def test_side_by_side(agg, work):
    Image = pytest.importorskip("PIL.Image")
    for name, col in (("a.png", (200, 30, 30)), ("m.png", (30, 30, 200))):
        Image.new("RGB", (640, 360), col).save(work / name)
    out = report.side_by_side("turntable", work / "a.png", work / "m.png", agg["cells"]["turntable"], work / "o" / "s.png")
    im = Image.open(out)
    assert im.width > 1280 and im.height > 400 and not getattr(im, "text", {})
    assert out.stat().st_size <= report.PNG_CAP
    assert scrub.png_texts(out.read_bytes()) == []
    # a missing pane degrades to a grey tile instead of raising
    out2 = report.side_by_side("turntable", work / "a.png", work / "missing.png", {}, work / "o" / "s2.png")
    assert out2.is_file()


def test_run_end_to_end(runs, work):
    pytest.importorskip("PIL.Image")
    res = work / "results"
    readme = work / "README.md"
    readme.write_text(README, encoding="utf-8", newline="")
    agg = report.run(runs, res, readme=readme)
    assert len(agg["runs"]) == 4
    for name in ("runs.json", "results.md", "turntable/side_by_side.png"):
        assert (res / name).is_file()
    assert json.loads((res / "runs.json").read_text(encoding="utf-8"))["cells"]["turntable"]["mcp"]["success"] == "1/2"
    assert report.START in readme.read_text(encoding="utf-8")
    assert scrub.check(res, DENY) == []
