"""Real-Blender checks for `apply` (v1.3 U1): one Blender start for many steps, all-or-nothing headless, best-effort live.

Headless tests run through `cli.main` with the registry entries patched in (apply and its four BATCH hooks), so they pass
before the lead's splice; only add/set/delete/material steps are used. Live tests start a REAL headless MCP server on a
free port (never 9876) on a copy of basic.blend.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from blender_axi import cli, paths, registry
from test_live_optional import real_server

pytestmark = pytest.mark.blender

BATCH = {"add": "blender_axi.commands.add:BATCH", "set": "blender_axi.commands.set_cmd:BATCH",
         "delete": "blender_axi.commands.delete:BATCH", "material": "blender_axi.commands.material:BATCH"}
TEN = ["add cube --name A", "add sphere --name B --location 3,0,0", "add cylinder --name C --location -3,0,0",
       "add cone --name D --location 0,3,0", "add plane --name E --location 0,-3,0", "add cube --name F",
       "set A --location 0,0,2", "material B --name Red --color 1,0,0", "delete F",
       {"cmd": "add", "kind": "camera", "name": "Cam", "location": [0, -8, 4]}]


@pytest.fixture(autouse=True)
def _register(monkeypatch):
    monkeypatch.setitem(registry.COMMANDS, "apply", "blender_axi.commands.apply:SPEC")
    for name, target in BATCH.items():
        monkeypatch.setitem(registry.BATCH, name, target)


@pytest.fixture
def work():
    d = paths.tmp_root() / "apply"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    return d


@pytest.fixture
def fresh(capsys, work):
    path = work / "fresh.blend"
    assert axi(capsys, "new", path)[0] == 0
    return path


def axi(capsys, *argv):
    code = cli.main(["--json", *map(str, argv)])
    out = capsys.readouterr()
    assert out.err == ""
    return code, json.loads(out.out)


def steps_file(work, steps, name="steps.json"):
    path = work / name
    path.write_text(json.dumps(steps), encoding="utf-8")
    return path


def names(capsys, blend, *extra):
    argv = ["objects", *([blend] if blend is not None else []), *extra]
    listing = axi(capsys, *argv)[1].get("objects")
    return {o["name"] for o in listing} if isinstance(listing, list) else set()


def test_ten_steps_one_blender_start(capsys, work, fresh):
    steps = steps_file(work, TEN)
    single = work / "single.blend"
    shutil.copy2(fresh, single)
    started = time.perf_counter()
    assert axi(capsys, "add", "cube", single, "--in-place")[0] == 0
    add_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    code, data = axi(capsys, "apply", steps, fresh, "--in-place")
    apply_ms = (time.perf_counter() - started) * 1000
    assert code == 0, data
    assert data["steps"] == 10 and data["applied"] == 10 and len(data["results"]) == 10
    assert data["results"][0] == "1 add: A (MESH) in Scene Collection" and data["results"][6] == "7 set: A (location)"
    assert data["results"][7] == "8 material: B <- Red (created)" and data["results"][8].startswith("9 delete: F (remaining")
    assert data["modified"] is True and data["saved"].endswith("fresh.blend") and data["backup"].endswith("fresh.blend1")
    assert "undo" not in data
    assert names(capsys, fresh) == {"A", "B", "C", "D", "E", "Cam"}
    assert apply_ms < 2 * add_ms + 1000, (apply_ms, add_ms)  # ten steps cost about one Blender start
    _, obj = axi(capsys, "object", "B", fresh)
    assert "Red" in json.dumps(obj) and axi(capsys, "object", "A", fresh)[1]["location"] == "0,0,2"
    assert axi(capsys, "scene", fresh)[1]["camera"] == "Cam"


def test_failing_step_writes_nothing(capsys, work, fresh):
    steps = steps_file(work, [*TEN[:6], "delete Ghost", *TEN[6:]])
    before = (fresh.stat().st_mtime_ns, fresh.read_bytes())
    code, data = axi(capsys, "apply", steps, fresh, "--in-place")
    assert code == 1 and data["code"] == "NOT_FOUND", data
    assert data["error"].startswith("step 7 (delete): object 'Ghost' not found")
    assert any("all-or-nothing" in h for h in data["help"]) and "Run `blender-axi objects" in data["help"][0]
    assert (fresh.stat().st_mtime_ns, fresh.read_bytes()) == before
    assert not fresh.with_name("fresh.blend1").exists() and names(capsys, fresh) == set()


def test_out_writes_a_copy_and_leaves_the_original(capsys, work, fresh):
    before = (fresh.stat().st_mtime_ns, fresh.read_bytes())
    copy = work / "copy.blend"
    code, data = axi(capsys, "apply", steps_file(work, TEN[:3]), fresh, "--out", copy)
    assert code == 0 and data["applied"] == 3 and data["saved"].endswith("copy.blend"), data
    assert names(capsys, copy) == {"A", "B", "C"}
    assert (fresh.stat().st_mtime_ns, fresh.read_bytes()) == before


def test_check_and_invalid_steps_never_start_blender(capsys, work, fresh):
    code, data = axi(capsys, "apply", steps_file(work, TEN), fresh, "--check")
    assert code == 0 and data["status"] == "valid" and data["steps"] == 10 and len(data["plan"]) == 10
    assert "duration_ms" not in data and "modified" not in data and names(capsys, fresh) == set()
    code, data = axi(capsys, "apply", steps_file(work, ["add cube", "set Cube"], "bad.json"), fresh, "--in-place")
    assert code == 2 and data["error"] == "step 2 (set): set needs at least one change flag"
    assert data["help"][-1].startswith("Fix step 2 in ") and data["help"][-1].endswith("bad.json (nothing ran)") and "duration_ms" not in data
    code, data = axi(capsys, "apply", steps_file(work, TEN[:2]), fresh)  # no write flag
    assert code == 2 and "--in-place" in " ".join(data["help"]) and "duration_ms" not in data


def test_stdin_form_in_a_real_process(work, fresh):
    # the registry entries are patched inside the child too (the lead's splice is not in this worktree)
    code = ("import sys; from blender_axi import cli, registry; "
            f"registry.COMMANDS['apply'] = 'blender_axi.commands.apply:SPEC'; registry.BATCH.update({BATCH!r}); "
            "sys.exit(cli.main(sys.argv[1:]))")
    env = paths.blender_env({**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")})
    proc = subprocess.run([sys.executable, "-c", code, "--json", "apply", "-", str(fresh), "--in-place"],
                          input=json.dumps(TEN[:3]), capture_output=True, text=True, env=env, cwd=str(work), timeout=180)
    data = json.loads(proc.stdout)
    assert proc.returncode == 0 and data["applied"] == 3, (proc.stdout, proc.stderr)


# -- live: a private headless MCP server on a copy of basic.blend ------------------------------------------------------

@pytest.fixture(scope="module")
def session(blender_info, fixtures_dir):
    work = paths.tmp_root() / "apply_live"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    blend = work / "session.blend"
    shutil.copy2(fixtures_dir / "basic.blend", blend)
    with real_server(blender_info, blend) as (port, _log):
        assert port != 9876
        yield port, blend


def test_live_story(capsys, work, session):
    port, blend = session
    live = ["--live", "--host", "127.0.0.1", "--port", port]
    steps = ["add cube --name LiveA", "add sphere --name LiveB --location 3,0,0", "material LiveB --name LiveRed --color 1,0,0"]
    code, data = axi(capsys, "apply", steps_file(work, steps), *live)
    assert code == 0, data
    assert data["applied"] == 3 and data["modified"] is True and data["file"] == blend.as_posix()
    assert data["undo"] == "unavailable (headless server)" and data["saved"] == "none (live session, unsaved)"
    assert axi(capsys, "live", "--host", "127.0.0.1", "--port", port)[1]["unsaved_changes"] is True
    assert {"LiveA", "LiveB"} <= names(capsys, None, *live)

    code, data = axi(capsys, "apply", steps_file(work, ["add cube --name LiveC", "delete Ghost", "add cube --name LiveD"], "f.json"), *live)
    assert code == 1 and data["error"].startswith("step 2 (delete): object 'Ghost' not found"), data
    assert any(h.startswith("Steps before the failing one were applied to the open session") for h in data["help"])
    seen = names(capsys, None, *live)
    assert "LiveC" in seen and "LiveD" not in seen  # step 1 stayed, the batch stopped at the failure

    code, data = axi(capsys, "apply", steps_file(work, steps), blend, *live)  # a file positional is refused before anything runs
    assert code == 2 and data["error"] == "--live uses the file open in Blender; drop <file>"
