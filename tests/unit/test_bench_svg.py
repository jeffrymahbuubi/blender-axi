"""Unit tests for bench.svg (pure functions; no Blender, no network)."""

import copy
import math
import re
import xml.etree.ElementTree as ET

import pytest

from bench import svg

NS = "{http://www.w3.org/2000/svg}"


def _m(chars, tool=None):
    d = {"chars": chars, "tokens": None, "raw": "x", "ms": 1}
    if tool:
        d["tool"] = tool
    return d


@pytest.fixture
def results():
    def task(tid, axi, mcp):
        return {"id": tid, "label": tid, "note": "", "axi": _m(axi),
                "mcp": _m(mcp, "get_objects_summary") if mcp else None, "saving_pct": None}

    return {
        "schema": 1, "date": "2026-10-25", "unit": "chars", "token_model": None,
        "fixed": {"mcp_tools_list": {"chars": 18911, "tokens": None, "tools": 26},
                  "axi_skill": {"chars": 3225, "tokens": None}},
        "scenes": {
            "desk": {"objects": 13, "collections": 1,
                     "tasks": [task("objects", 636, 4214), task("objects_json", 1500, None)]},
            "city": {"objects": 502, "collections": 21, "tasks": [
                task("scene", 674, 671), task("objects", 3800, 146677),
                {"id": "bad", "label": "bad", "note": "", "error": "boom"}]},
        },
    }


def _parse(text):
    return ET.fromstring(text)


def _rects(root):
    return root.findall(f".//{NS}rect")


def test_parses_and_viewbox_height(results):
    root = _parse(svg.chart_svg(results))
    assert root.tag == f"{NS}svg"
    rows = 3 + 1  # three tasks with MCP values + fixed row
    assert root.get("viewBox") == f"0 0 900 {40 * rows + 120}"
    assert root.get("role") == "img" and "log scale" in root.get("aria-label")


def test_one_rect_per_measured_value(results):
    root = _parse(svg.chart_svg(results))
    # 3 tasks x (mcp + axi) + fixed row x (mcp + axi); the axi-only and error tasks add nothing
    assert len(_rects(root)) == 8
    assert all("bar" in r.get("class") for r in _rects(root))


def test_widths_monotone_in_log10(results):
    root = _parse(svg.chart_svg(results))
    by_value = {}
    for r in _rects(root):
        value = int(re.search(r": (\d+) chars", r.find(f"{NS}title").text).group(1))
        by_value[value] = float(r.get("width"))
    values = sorted(by_value)
    widths = [by_value[v] for v in values]
    assert widths == sorted(widths) and len(set(widths)) == len(widths)
    span = svg.PLOT_X1 - svg.PLOT_X0
    assert by_value[146677] == pytest.approx(span * math.log10(146677) / 6, abs=0.1)


def test_every_title_contains_its_number(results):
    root = _parse(svg.chart_svg(results))
    titles = [t.text for t in root.iter(f"{NS}title")]
    assert len(titles) == 8
    for n in ("4214", "636", "146677", "3800", "674", "671", "18911", "3225"):
        assert any(n in t for t in titles), n
    assert all("chars" in t for t in titles)


def test_ticks_at_powers_of_ten(results):
    root = _parse(svg.chart_svg(results))
    ticks = [t.text for t in root.iter(f"{NS}text") if t.get("class") == "tick" and t.text and t.text[0] == "1"]
    assert ticks == ["1", "10¹", "10²", "10³", "10⁴", "10⁵", "10⁶"]
    assert len(root.findall(f".//{NS}line[@class='grid']")) == 7


def test_axis_extends_past_a_million(results):
    big = copy.deepcopy(results)
    big["scenes"]["city"]["tasks"][1]["mcp"]["chars"] = 2_500_000
    root = _parse(svg.chart_svg(big))
    assert len(root.findall(f".//{NS}line[@class='grid']")) == 8  # 1 .. 10^7


def test_small_values_clamp_to_one_pixel(results):
    tiny = copy.deepcopy(results)
    tiny["scenes"]["desk"]["tasks"][0]["mcp"]["chars"] = 0
    tiny["scenes"]["desk"]["tasks"][0]["axi"]["chars"] = 1
    widths = [float(r.get("width")) for r in _rects(_parse(svg.chart_svg(tiny)))]
    assert widths.count(1.0) == 2


def test_label_inside_when_no_room(results):
    big = copy.deepcopy(results)
    big["scenes"]["city"]["tasks"][1]["mcp"]["chars"] = 900_000
    root = _parse(svg.chart_svg(big))
    assert any(t.get("class") == "val in" and t.text == "900,000" for t in root.iter(f"{NS}text"))


def test_tokens_mode_uses_tokens(results):
    r = copy.deepcopy(results)
    r["unit"] = "tokens"
    r["token_model"] = "claude-opus-5-5"
    r["fixed"]["mcp_tools_list"]["tokens"] = 6000
    r["fixed"]["axi_skill"]["tokens"] = 1000
    for scene in r["scenes"].values():
        for t in scene["tasks"]:
            for side in ("axi", "mcp"):
                if t.get(side):
                    t[side]["tokens"] = t[side]["chars"] // 4
    root = _parse(svg.chart_svg(r))
    titles = [t.text for t in root.iter(f"{NS}title")]
    assert any("1053 tokens" in t for t in titles) and not any("chars" in t for t in titles)
    assert "Tokens" in root.get("aria-label")


def test_missing_values_are_not_drawn(results):
    r = copy.deepcopy(results)
    r["unit"] = "tokens"  # no tokens recorded anywhere
    assert _rects(_parse(svg.chart_svg(r))) == []


def test_escapes_labels(results):
    r = copy.deepcopy(results)
    r["scenes"]["desk"]["tasks"][0]["label"] = 'A <b> & "c"'
    root = _parse(svg.chart_svg(r))
    assert any(t.text and "<b>" in t.text for t in root.iter(f"{NS}text"))
