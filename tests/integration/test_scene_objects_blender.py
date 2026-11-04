"""Real-Blender checks for U4: scene / objects / object against the generated fixtures."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest

from blender_axi import flags, registry, toon
from blender_axi.commands import objects as cmd_objects
from blender_axi.commands import scene as cmd_scene
from blender_axi.errors import AxiError
from blender_axi.runner import HeadlessTransport

pytestmark = pytest.mark.blender


@pytest.fixture(scope="module")
def transport(blender_info):
    return HeadlessTransport(blender_info)


@pytest.fixture
def basic(fixtures_dir):
    return str(fixtures_dir / "basic.blend")


@pytest.fixture
def empty(fixtures_dir):
    return str(fixtures_dir / "empty.blend")


def invoke(spec, argv, transport):
    inv, _ = flags.parse(spec, argv)
    inv.transport = transport
    return spec.run(inv)


def test_scene_basic(transport, basic):
    res = invoke(cmd_scene.SPEC, [basic], transport)
    d = res.data
    assert d["file"] == basic and d["blender"].startswith("5.")
    assert d["scene"] == "Scene" and d["resolution"] == "64x64@100%" and d["frames"] == "1-24 (current 1)"
    assert d["fps"] == 24 and d["camera"] == "Camera"
    assert d["objects"] == "5 (MESH 2, CAMERA 1, EMPTY 1, LIGHT 1)"
    assert d["collections"] == "3 (Scene Collection, Collection, Props)" and d["materials"].startswith("1 (")
    assert d["engine"] in {"EEVEE", "CYCLES", "WORKBENCH"}
    assert "unsaved_changes" not in d and "warning" not in d
    assert len(res.help) == 2 and res.help[1].startswith("Run `blender-axi render ")
    toon.encode({**d, "help": res.help})


def test_scene_empty(transport, empty):
    res = invoke(cmd_scene.SPEC, [empty], transport)
    d = res.data
    assert d["objects"] == "0" and d["camera"] == "none" and d["collections"] == "1 (Scene Collection)"
    assert d["warning"].startswith("unlit scene: ") and d["warning"].endswith(f"{d['engine']} renders black")
    assert len(res.help) == 2 and res.help[1].startswith("Run `blender-axi add camera ")  # camera wins over unlit


def test_objects_default(transport, basic):
    res = invoke(cmd_objects.OBJECTS_SPEC, [basic], transport)
    assert res.data["count"] == "5 of 5 total"
    names = [r["name"] for r in res.data["objects"]]
    assert names == ["Anchor", "Ball", "Camera", "Cube", "Light"]
    by = {r["name"]: r for r in res.data["objects"]}
    assert by["Ball"]["collection"] == "Props" and by["Anchor"]["type"] == "EMPTY"
    assert by["Cube"]["visible"] is True
    assert not any("--limit" in h for h in res.help)


def test_objects_type_mesh_with_fields(transport, basic):
    res = invoke(cmd_objects.OBJECTS_SPEC, [basic, "--type", "MESH", "--fields", "verts,faces,location,materials"],
                 transport)
    assert res.data["count"] == "2 of 2 matching (5 total)"
    by = {r["name"]: r for r in res.data["objects"]}
    assert by["Cube"]["verts"] == 8 and by["Cube"]["faces"] == 6 and by["Cube"]["materials"] == 1
    assert by["Ball"]["verts"] == 482 and by["Ball"]["faces"] == 512 and by["Ball"]["location"] == "3,0,0"
    assert by["Cube"]["location"] == "0,0,0"


def test_objects_collection_and_name_filter(transport, basic):
    res = invoke(cmd_objects.OBJECTS_SPEC, [basic, "--collection", "Props", "--name-contains", "BALL"], transport)
    assert [r["name"] for r in res.data["objects"]] == ["Ball"]


def test_objects_zero_line(transport, basic):
    res = invoke(cmd_objects.OBJECTS_SPEC, [basic, "--type", "CAMERA", "--name-contains", "zzz"], transport)
    assert res.data["objects"] == "0 objects matching type=CAMERA, name_contains=zzz in " + basic


def test_objects_empty_file(transport, empty):
    res = invoke(cmd_objects.OBJECTS_SPEC, [empty], transport)
    assert res.data["objects"] == f"0 objects in {empty}"


def test_objects_limit_hint(transport, basic):
    res = invoke(cmd_objects.OBJECTS_SPEC, [basic, "--limit", "2"], transport)
    assert len(res.data["objects"]) == 2 and res.data["count"] == "2 of 5 total"
    assert any("--limit <n>" in h and "5" in h for h in res.help)


def test_objects_unknown_collection(transport, basic):
    with pytest.raises(AxiError) as e:
        invoke(cmd_objects.OBJECTS_SPEC, [basic, "--collection", "Nope"], transport)
    assert e.value.code == "NOT_FOUND" and "Props" in e.value.message
    assert "scene <file>" in e.value.help[0]


def test_object_ball(transport, basic):
    res = invoke(cmd_objects.OBJECT_SPEC, ["Ball", basic], transport)
    d = res.data
    assert d["name"] == "Ball" and d["type"] == "MESH" and d["collection"] == "Props"
    assert d["parent"] == "none" and d["children"] == "none" and d["location"] == "3,0,0"
    assert d["rotation_deg"] == "0,0,0" and d["scale"] == "1,1,1" and d["dimensions"] == "2,2,2"
    assert d["verts"] == 482 and d["faces"] == 512 and d["visible"] is True
    assert d["materials"] == "none" and d["modifiers"] == "none" and d["custom_props"] == "none"
    assert res.help == []


def test_object_empty_and_missing(transport, basic):
    d = invoke(cmd_objects.OBJECT_SPEC, ["Anchor", basic], transport).data
    assert d["type"] == "EMPTY" and "verts" not in d and d["location"] == "0,3,0"
    with pytest.raises(AxiError) as e:
        invoke(cmd_objects.OBJECT_SPEC, ["Bal", basic], transport)
    assert e.value.code == "NOT_FOUND" and "did you mean: Ball" in e.value.message
    assert e.value.help == ["Run `blender-axi objects <file>` to list object names"]


def test_cli_end_to_end_json(monkeypatch, capsys, basic):
    from blender_axi import cli

    monkeypatch.setitem(registry.COMMANDS, "scene", "blender_axi.commands.scene:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "objects", "blender_axi.commands.objects:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "object", "blender_axi.commands.objects:OBJECT_SPEC")
    assert cli.main(["objects", basic, "--type", "LIGHT", "--json"]) == 0
    out = capsys.readouterr()
    assert out.err == ""
    data = json.loads(out.out)
    assert [o["name"] for o in data["objects"]] == ["Light"]
    assert cli.main(["scene", basic]) == 0
    captured = capsys.readouterr()
    assert captured.err == "" and captured.out.startswith("file: ")
    assert cli.main(["object", "Nope", basic]) == 1
    assert "code: NOT_FOUND" in capsys.readouterr().out


def test_objects_tris_sums_to_the_export_dry_run_triangles(capsys, basic):
    """0.10.0 U5: `--fields tris` (shared `tris.py`) agrees with `export --dry-run` on the same file."""
    from blender_axi import cli

    assert cli.main(["--json", "objects", basic, "--fields", "tris"]) == 0
    by = {o["name"]: o["tris"] for o in json.loads(capsys.readouterr().out)["objects"]}
    assert by["Cube"] == 12 and by["Ball"] == 960 and by["Camera"] is None and by["Anchor"] is None
    assert cli.main(["--json", "export", basic, "--out", str(Path(basic).with_name("dry.glb")), "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["triangles"] == sum(v for v in by.values() if v)
    assert cli.main(["--json", "object", "Ball", basic]) == 0
    assert json.loads(capsys.readouterr().out)["tris"] == 960


# -- used_by (0.8.0 U4) ----------------------------------------------------------------------------

CITY = Path(__file__).resolve().parents[2] / ".tmp" / "bench" / "scenes" / "city.blend"
HELPER_STEPS = ["add cube --name Ring --size 2", "add cube --name Hole --size 1",
                "modifier Ring boolean --cutter Hole", "set Hole --hide"]


def axi_json(capsys, *argv):
    from blender_axi import cli

    code = cli.main(["--json", *map(str, argv)])
    out = capsys.readouterr()
    assert out.err == ""
    return code, json.loads(out.out)


@pytest.fixture(scope="module")
def helper(blender_info, fixtures_dir):
    from blender_axi import cli, paths

    work = paths.tmp_root() / "scene_objects"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    blend = work / "helper.blend"
    shutil.copy2(fixtures_dir / "basic.blend", blend)
    steps = work / "helper.json"
    steps.write_text(json.dumps(HELPER_STEPS), encoding="utf-8")
    assert cli.main(["--json", "apply", str(steps), str(blend), "--in-place"]) == 0
    return blend


def test_objects_used_by_names_the_cutter_users(capsys, helper):
    code, data = axi_json(capsys, "objects", helper, "--fields", "used_by", "--limit", "50")
    assert code == 0
    by = {o["name"]: o for o in data["objects"]}
    assert by["Hole"]["used_by"] == "Ring (Boolean)" and by["Hole"]["visible"] is False  # A4: Object identifier
    assert by["Ring"]["used_by"] == "none" and by["Cube"]["used_by"] == "none"


def test_objects_default_rows_have_no_used_by(capsys, helper):
    code, data = axi_json(capsys, "objects", helper, "--limit", "50")
    assert code == 0
    assert all(set(o) == {"name", "type", "collection", "visible"} for o in data["objects"])


def test_object_shows_used_by(capsys, helper):
    _, hole = axi_json(capsys, "object", "Hole", helper)
    _, ring = axi_json(capsys, "object", "Ring", helper)
    assert hole["used_by"] == "Ring (Boolean)" and ring["used_by"] == "none"
    keys = list(hole)
    assert keys.index("used_by") == keys.index("children") + 1


def test_used_by_city_timing(capsys):
    if not CITY.exists():
        pytest.skip("city.blend not generated")
    started = time.perf_counter()
    code, data = axi_json(capsys, "objects", CITY, "--fields", "used_by", "--limit", "5")
    assert code == 0 and "used_by" in data["objects"][0]
    assert time.perf_counter() - started < 2
