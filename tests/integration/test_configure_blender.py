"""Real-Blender checks for `configure` (v1.3 U4): headless --in-place / --out, fingerprint of world nodes (P4), and --live.

Headless tests run the commands through `cli.main` (the registry entries are patched in, so they pass before the lead's
splice). Live tests start a REAL headless MCP server on a free port (never 9876) on a copy of basic.blend.
basic.blend: Cube, Camera (the scene camera), Light, collection Props with Ball and the empty Anchor; a factory world.
"""

from __future__ import annotations

import json
import shutil

import pytest

from blender_axi import cli, paths, registry
from test_live_optional import real_server

pytestmark = pytest.mark.blender


@pytest.fixture(autouse=True)
def _register(monkeypatch):
    monkeypatch.setitem(registry.COMMANDS, "configure", "blender_axi.commands.configure:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "add", "blender_axi.commands.add:SPEC")


@pytest.fixture
def work():
    d = paths.tmp_root() / "configure"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    return d


@pytest.fixture
def basic(work, fixtures_dir):
    path = work / "basic.blend"
    shutil.copy2(fixtures_dir / "basic.blend", path)
    return path


@pytest.fixture
def empty(work, fixtures_dir):
    path = work / "empty.blend"
    shutil.copy2(fixtures_dir / "empty.blend", path)
    return path


def axi(capsys, *argv):
    code = cli.main(["--json", *map(str, argv)])
    out = capsys.readouterr()
    assert out.err == ""
    return code, json.loads(out.out)


def stamps(blend):
    bak = blend.with_name(blend.name + "1")
    return blend.stat().st_mtime_ns, bak.stat().st_mtime_ns if bak.exists() else None


def world_color(capsys, blend) -> list[float]:
    code, ex = axi(capsys, "exec", blend, "--allow-exec", "--code",
                   "w = bpy.context.scene.world\n"
                   "n = [n for n in w.node_tree.nodes if n.type == 'BACKGROUND'][0]\n"
                   "result = {'c': [round(v, 3) for v in n.inputs['Color'].default_value][:3]}")
    assert code == 0, ex
    return ex["result"]["c"]


# -- headless ---------------------------------------------------------------------------------------------------------

def test_acceptance_story_and_rerun_writes_nothing(capsys, basic):
    assert axi(capsys, "add", "camera", basic, "--name", "Cam", "--in-place")[0] == 0
    args = ["configure", basic, "--engine", "CYCLES", "--resolution", "1920x1080", "--percent", "100",
            "--frames", "1..48", "--fps", "30", "--camera", "Cam", "--in-place"]
    code, data = axi(capsys, *args)
    assert code == 0, data
    assert data["scene"] == "Scene" and data["modified"] is True and data["saved"].endswith("basic.blend")
    assert data["changed"] == ["engine: EEVEE -> CYCLES", "resolution: 64x64 -> 1920x1080", "frames: 1-24 -> 1-48",
                               "fps: 24 -> 30", "camera: Camera -> Cam"]
    assert data["backup"].endswith("basic.blend1")
    _, scene = axi(capsys, "scene", basic)
    assert scene["engine"] == "CYCLES" and scene["resolution"] == "1920x1080@100%"
    assert scene["frames"] == "1-48 (current 1)" and scene["fps"] == 30 and scene["camera"] == "Cam"
    assert any("configure" in h for h in scene["help"])

    before = stamps(basic)
    code, data = axi(capsys, *args)
    assert code == 0 and data["modified"] is False and data["saved"] == "none (no changes)" and "backup" not in data
    assert data["changed"] == "none (values already as requested)"
    assert stamps(basic) == before


def test_world_colour_alone_is_a_modification(capsys, basic):
    code, data = axi(capsys, "configure", basic, "--world", "0.1,0.1,0.2", "--in-place")
    assert code == 0, data
    assert data["modified"] is True and data["saved"].endswith("basic.blend")
    assert data["changed"] == ["world_color: 0.051,0.051,0.051 -> 0.1,0.1,0.2"]
    assert world_color(capsys, basic) == [0.1, 0.1, 0.2]
    before = stamps(basic)
    code, data = axi(capsys, "configure", basic, "--world", "0.1,0.1,0.2", "--in-place")
    assert data["modified"] is False and stamps(basic) == before
    code, data = axi(capsys, "configure", basic, "--world-strength", "2", "--in-place")
    assert data["modified"] is True and data["changed"] == ["world_strength: 1 -> 2"]


def test_world_is_created_in_a_file_without_one(capsys, empty):
    code, data = axi(capsys, "configure", empty, "--world", "0.2,0.3,0.4", "--in-place")
    assert code == 0, data
    assert data["changed"][0] == "world: none -> World" and data["modified"] is True
    assert world_color(capsys, empty) == [0.2, 0.3, 0.4]


def test_fractional_fps(capsys, basic):
    code, data = axi(capsys, "configure", basic, "--fps", "29.97", "--in-place")
    assert code == 0 and data["changed"] == ["fps: 24 -> 29.97"]
    assert axi(capsys, "scene", basic)[1]["fps"] == 29.97
    assert axi(capsys, "configure", basic, "--fps", "29.97", "--in-place")[1]["modified"] is False


def test_samples_on_workbench_warns(capsys, basic):
    code, data = axi(capsys, "configure", basic, "--samples", "8", "--engine", "WORKBENCH", "--in-place")
    assert code == 0, data
    assert data["changed"] == ["engine: EEVEE -> WORKBENCH"]
    assert data["warning"] == "--samples ignored: WORKBENCH has no sample setting"
    code, data = axi(capsys, "configure", basic, "--samples", "8", "--engine", "CYCLES", "--in-place")
    assert code == 0 and data["changed"] == ["engine: WORKBENCH -> CYCLES", "samples: 4096 -> 8"]
    assert "warning" not in data


def test_camera_must_be_a_camera(capsys, basic):
    before = stamps(basic)
    code, data = axi(capsys, "configure", basic, "--camera", "Cube", "--in-place")
    assert code == 1 and "'Cube' is a MESH, not a CAMERA" in data["error"]
    code, data = axi(capsys, "configure", basic, "--camera", "Nope", "--in-place")
    assert code == 1 and data["code"] == "NOT_FOUND" and "--type CAMERA" in data["help"][0]
    assert stamps(basic) == before


def test_out_leaves_the_original_untouched(capsys, basic):
    before = (basic.stat().st_mtime_ns, basic.read_bytes())
    copy = basic.with_name("copy.blend")
    code, data = axi(capsys, "configure", basic, "--resolution", "320x180", "--out", copy)
    assert code == 0 and data["saved"].endswith("copy.blend") and "backup" not in data
    assert (basic.stat().st_mtime_ns, basic.read_bytes()) == before and not basic.with_name("basic.blend1").exists()
    assert axi(capsys, "scene", copy)[1]["resolution"] == "320x180@100%"
    assert axi(capsys, "scene", basic)[1]["resolution"] == "64x64@100%"


def test_validation_and_missing_write_mode_exit_2_and_write_nothing(capsys, basic):
    before = (basic.stat().st_mtime_ns, basic.read_bytes())
    for argv in (["configure", basic, "--in-place"], ["configure", basic, "--engine", "CYCLES"],
                 ["configure", basic, "--frames", "5..1", "--in-place"], ["configure", basic, "--world", "2,0,0", "--in-place"]):
        code, data = axi(capsys, *argv)
        assert code == 2 and "duration_ms" not in data, argv
    assert (basic.stat().st_mtime_ns, basic.read_bytes()) == before


def test_render_uses_the_configured_engine(capsys, basic, work):
    code, data = axi(capsys, "configure", basic, "--engine", "WORKBENCH", "--resolution", "32x32", "--in-place")
    assert code == 0, data
    out = work / "shot.png"
    code, data = axi(capsys, "render", basic, "--out", out)
    assert code == 0, data
    assert data["engine"] == "WORKBENCH" and data["size"] == "32x32" and out.exists()


# -- live: a private headless MCP server on a copy of basic.blend -----------------------------------------------------

@pytest.fixture(scope="module")
def session(blender_info, fixtures_dir):
    work = paths.tmp_root() / "configure_live"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    blend = work / "session.blend"
    shutil.copy2(fixtures_dir / "basic.blend", blend)
    with real_server(blender_info, blend) as (port, _log):
        assert port != 9876
        yield port, blend


def test_live_story(capsys, session):
    port, blend = session
    live = ["--live", "--host", "127.0.0.1", "--port", port]
    code, data = axi(capsys, "configure", *live, "--resolution", "32x32")
    assert code == 0, data
    assert data["modified"] is True and data["changed"] == ["resolution: 64x64 -> 32x32"]
    assert data["undo"] == "unavailable (headless server)" and data["saved"] == "none (live session, unsaved)"
    assert axi(capsys, "scene", *live)[1]["resolution"] == "32x32@100%"
    code, data = axi(capsys, "configure", *live, "--resolution", "32x32")
    assert code == 0 and data["modified"] is False
    code, data = axi(capsys, "configure", *live, "--world", "0.3,0.2,0.1")
    assert code == 0 and data["modified"] is True and data["changed"] == ["world_color: 0.051,0.051,0.051 -> 0.3,0.2,0.1"]
    # --out / a file positional have no meaning live, and fail before any change
    code, err = axi(capsys, "configure", *live, "--fps", "30", "--out", "x.blend")
    assert code == 2
    code, err = axi(capsys, "configure", blend, *live, "--fps", "30")
    assert code == 2 and err["error"] == "--live uses the file open in Blender; drop <file>"
    assert axi(capsys, "scene", *live)[1]["fps"] == 24
