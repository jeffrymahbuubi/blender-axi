"""Real-Blender checks for `diff` (0.6.0 U4): two headless starts, and `diff <file> --live` against a REAL headless MCP
server on a free port (never 9876). Files live under .tmp/tests/diff/; `diff` is registered with monkeypatch so the tests
pass before the lead's registry splice.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil

import pytest

from blender_axi import cli, paths, registry
from test_live_optional import real_server

pytestmark = pytest.mark.blender

V1 = ["add cube --name A", "add cube --name B --location 3,0,0", "material A --name Red --color 1,0,0",
      "modifier A bevel --width 0.05", "add camera --name Cam --location 0,-8,4"]
V2 = ["delete B", "add cube --name C --location -3,0,0", "set A --location 1,0,0", "material A --name Blue --color 0,0,1",
      "modifier A bevel --width 0.1", "configure --resolution 320x240"]


def axi(*argv):
    """(exit code, parsed --json output); stdout captured without capsys so module fixtures can use it."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(["--json", *map(str, argv)])
    return code, json.loads(buf.getvalue())


BATCH = {"add": "blender_axi.commands.add:BATCH", "set": "blender_axi.commands.set_cmd:BATCH",
         "delete": "blender_axi.commands.delete:BATCH", "material": "blender_axi.commands.material:BATCH",
         "modifier": "blender_axi.commands.modifier:BATCH", "configure": "blender_axi.commands.configure:BATCH"}


def register(mp):
    mp.setitem(registry.COMMANDS, "diff", "blender_axi.commands.diff:SPEC")
    mp.setitem(registry.COMMANDS, "apply", "blender_axi.commands.apply:SPEC")
    for name, target in BATCH.items():
        mp.setitem(registry.BATCH, name, target)


@pytest.fixture(autouse=True)
def _register(monkeypatch):
    register(monkeypatch)


def build(blend, steps):
    steps_file = blend.with_suffix(".json")
    steps_file.write_text(json.dumps(steps), encoding="utf-8")
    code, data = axi("apply", steps_file, blend, "--in-place")
    assert code == 0, data


@pytest.fixture(scope="module")
def pair(blender_info):
    """v1.blend and v2.blend (v2 = v1 plus V2 steps)."""
    work = paths.tmp_root() / "diff"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    v1, v2 = work / "v1.blend", work / "v2.blend"
    with pytest.MonkeyPatch.context() as mp:
        register(mp)
        assert axi("new", v1)[0] == 0
        build(v1, V1)
        shutil.copy2(v1, v2)
        build(v2, V2)
    return v1, v2


def test_headless_diff_reports_every_kind_of_change(pair):
    v1, v2 = pair
    stamps = [p.stat().st_mtime_ns for p in pair]
    code, d = axi("diff", v1, v2)
    assert code == 0, d
    assert d["objects"] == "3 vs 3: 1 added, 1 removed, 1 changed, 1 unchanged"
    assert d["added"] == [{"name": "C", "type": "MESH"}] and d["removed"] == [{"name": "B", "type": "MESH"}]
    (row,) = d["changed"]
    assert row["name"] == "A"
    for part in ("location 0,0,0 -> 1,0,0", "materials Red -> Blue", "Bevel.width 0.05 -> 0.1"):
        assert part in row["changes"], row
    assert "width_pct" not in row["changes"], row  # 0.7.0: a bevel's width is reported once
    assert {"setting": "resolution", "from": "1920x1080@100%", "to": "320x240@100%"} in d["scene"]
    assert d["status"] == "different" and d["duration_ms"] >= 0
    assert any("object A" in h and "v2.blend" in h for h in d["help"])
    assert [p.stat().st_mtime_ns for p in pair] == stamps  # read-only


def test_percent_bevel_reports_width_pct_once(blender_info):
    """0.7.0 U3: with offset_type PERCENT the one reported width is `width_pct` (ASSUMED A2: `--prop` applies to `bevel`)."""
    work = paths.tmp_root() / "diff"
    work.mkdir(parents=True, exist_ok=True)
    p1, p2 = work / "p1.blend", work / "p2.blend"
    assert axi("new", p1)[0] == 0
    build(p1, ["add cube --name A", "modifier A bevel --prop offset_type=PERCENT --prop width_pct=5"])
    shutil.copy2(p1, p2)
    build(p2, ["modifier A bevel --prop width_pct=10"])
    code, d = axi("diff", p1, p2)
    assert code == 0, d
    (row,) = d["changed"]
    assert "Bevel.width_pct 5 -> 10" in row["changes"], row
    assert "Bevel.width " not in row["changes"] + " ", row


def test_identical_and_same_file_twice(pair):
    v1, _ = pair
    code, d = axi("diff", v1, v1)
    assert code == 0 and d["status"] == "identical" and d["changes"] == "none (3 objects, scene settings equal)"
    assert "changed" not in d and "added" not in d


def test_exit_code_flag_gates_on_the_difference(pair):
    v1, v2 = pair
    code, d = axi("diff", v1, v2, "--exit-code")
    assert code == 1 and d["status"] == "different" and d["changed"], d  # the report is still printed
    code, d = axi("diff", v1, v1, "--exit-code")
    assert code == 0 and d["status"] == "identical", d
    assert axi("diff", v1, v2)[0] == 0  # without the flag a difference stays exit 0


def test_object_filter(pair):
    v1, v2 = pair
    code, d = axi("diff", v1, v2, "--object", "A")
    assert code == 0 and d["objects"] == "1 vs 1: 0 added, 0 removed, 1 changed, 0 unchanged"
    code, d = axi("diff", v1, v2, "--object", "Ghost")
    assert code == 1 and d["code"] == "NOT_FOUND"


def test_one_file_without_live_is_a_usage_error(pair):
    code, d = axi("diff", pair[0])
    assert code == 2 and d["error"] == "diff needs two files (or one file with --live)"


@pytest.fixture(scope="module")
def session(blender_info, pair):
    """A live server on a copy of v2 (never 9876)."""
    live_copy = paths.tmp_root() / "diff" / "session.blend"
    shutil.copy2(pair[1], live_copy)
    with real_server(blender_info, live_copy) as (port, _log):
        assert port != 9876
        yield port, live_copy


def test_live_diff_file_against_the_open_session(pair, session):
    v1, v2 = pair
    port, live_copy = session
    addr = ["--live", "--host", "127.0.0.1", "--port", port]
    code, d = axi("diff", v1, *addr)
    assert code == 0, d
    assert d["a"].endswith("v1.blend") and d["b"] == f"(live session: {live_copy.as_posix()})"
    assert d["objects"] == "3 vs 3: 1 added, 1 removed, 1 changed, 1 unchanged"
    assert d["added"] == [{"name": "C", "type": "MESH"}] and [r["name"] for r in d["changed"]] == ["A"]
    assert "Bevel.width 0.05 -> 0.1" in d["changed"][0]["changes"]
    assert any(h.startswith("Run `blender-axi object A --live") for h in d["help"])
    code, d = axi("diff", v2, *addr)  # the session is a copy of v2
    assert code == 0 and d["status"] == "identical", d
    code, d = axi("diff", v1, v2, *addr)
    assert code == 2 and "drop <b>" in d["error"]
