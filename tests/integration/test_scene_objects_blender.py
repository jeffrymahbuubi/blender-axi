"""Real-Blender checks for U4: scene / objects / object against the generated fixtures."""

from __future__ import annotations

import json

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
    assert d["collections"] == "2 (Collection, Props)" and d["materials"].startswith("1 (")
    assert d["engine"] in {"EEVEE", "CYCLES", "WORKBENCH"}
    assert "unsaved_changes" not in d
    toon.encode({**d, "help": res.help})


def test_scene_empty(transport, empty):
    d = invoke(cmd_scene.SPEC, [empty], transport).data
    assert d["objects"] == "0" and d["camera"] == "none" and d["collections"] == "0"


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
