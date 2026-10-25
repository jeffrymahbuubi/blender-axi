"""Real-Blender checks for `set` and `delete` (v1.2 U2): headless --in-place / --out, A3 and A5, and --live on a private server.

Headless tests run the commands through `cli.main` (the registry entries are patched in, so they pass before the lead's
splice). Live tests start a REAL headless MCP server on a free port (never 9876) on a copy of basic.blend.
basic.blend: Cube, Camera, Light, collection Props with Ball (3,0,0) and the empty Anchor (0,3,0).
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
    monkeypatch.setitem(registry.COMMANDS, "set", "blender_axi.commands.set_cmd:SPEC")
    monkeypatch.setitem(registry.COMMANDS, "delete", "blender_axi.commands.delete:SPEC")


@pytest.fixture
def basic(fixtures_dir):
    d = paths.tmp_root() / "set_delete"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    path = d / "basic.blend"
    shutil.copy2(fixtures_dir / "basic.blend", path)
    return path


def axi(capsys, *argv):
    code = cli.main(["--json", *map(str, argv)])
    out = capsys.readouterr()
    assert out.err == ""
    return code, json.loads(out.out)


def world_location(capsys, blend, name) -> list[float]:
    code, ex = axi(capsys, "exec", blend, "--allow-exec", "--code",
                   "bpy.context.view_layer.update()\n"
                   f"result = {{'w': [round(v, 3) for v in bpy.data.objects[{name!r}].matrix_world.translation]}}")
    assert code == 0, ex
    return ex["result"]["w"]


def stamps(blend):
    bak = blend.with_name(blend.name + "1")
    return blend.stat().st_mtime_ns, bak.stat().st_mtime_ns if bak.exists() else None


# -- set --------------------------------------------------------------------------------------------------------------

def test_set_transform_matches_object_and_rerun_writes_nothing(capsys, basic):
    code, data = axi(capsys, "set", "Cube", basic, "--location", "1,2,3", "--rotation", "0,0,90", "--scale", "2,2,2",
                     "--in-place")
    assert code == 0, data
    assert data["object"] == "Cube" and data["modified"] is True and data["saved"].endswith("basic.blend")
    assert data["changed"] == ["location: 0,0,0 -> 1,2,3", "rotation: 0,0,0 -> 0,0,90", "scale: 1,1,1 -> 2,2,2"]
    assert data["backup"].endswith("basic.blend1")
    _, obj = axi(capsys, "object", "Cube", basic)
    assert obj["location"] == "1,2,3" and obj["rotation_deg"] == "0,0,90" and obj["scale"] == "2,2,2"

    before = stamps(basic)
    code, data = axi(capsys, "set", "Cube", basic, "--location", "1,2,3", "--rotation", "0,0,90", "--scale", "2,2,2",
                     "--in-place")
    assert code == 0 and data["modified"] is False and data["saved"] == "none (no changes)" and "backup" not in data
    assert data["changed"] == "none (values already as requested)"
    assert stamps(basic) == before


def test_set_out_leaves_the_original_untouched(capsys, basic):
    before = (basic.stat().st_mtime_ns, basic.read_bytes())
    copy = basic.with_name("copy.blend")
    code, data = axi(capsys, "set", "Cube", basic, "--location", "4,5,6", "--out", copy)
    assert code == 0 and data["saved"].endswith("copy.blend") and "backup" not in data
    assert (basic.stat().st_mtime_ns, basic.read_bytes()) == before and not basic.with_name("basic.blend1").exists()
    assert axi(capsys, "object", "Cube", copy)[1]["location"] == "4,5,6"
    assert axi(capsys, "object", "Cube", basic)[1]["location"] == "0,0,0"
    code, data = axi(capsys, "set", "Cube", basic, "--location", "4,5,6", "--out", copy)
    assert code == 1 and data["code"] == "WRITE_REFUSED"


def test_set_neither_flag_and_bad_arguments_exit_2_and_write_nothing(capsys, basic):
    before = (basic.stat().st_mtime_ns, basic.read_bytes())
    code, data = axi(capsys, "set", "Cube", basic, "--location", "1,2,3")
    assert code == 2 and "say where to write it" in data["error"]
    assert any("--in-place" in h for h in data["help"]) and any("--out <copy.blend>" in h for h in data["help"])
    for argv in (["Cube", basic, "--in-place"], ["Cube", basic, "--hide", "--show", "--in-place"],
                 ["Cube", basic, "--location", "1,2", "--in-place"]):
        code, data = axi(capsys, "set", *argv)
        assert code == 2 and data["code"] == "VALIDATION_ERROR", (argv, data)
    assert (basic.stat().st_mtime_ns, basic.read_bytes()) == before and not basic.with_name("basic.blend1").exists()


def test_set_parent_keeps_the_world_position(capsys, basic):
    assert axi(capsys, "set", "Cube", basic, "--location", "1,2,3", "--in-place")[0] == 0
    code, data = axi(capsys, "set", "Cube", basic, "--parent", "Anchor", "--in-place")
    assert code == 0 and data["changed"] == ["parent: none -> Anchor"], data
    assert axi(capsys, "object", "Cube", basic)[1]["parent"] == "Anchor"
    assert world_location(capsys, basic, "Cube") == [1, 2, 3]
    assert axi(capsys, "object", "Cube", basic)[1]["location"] == "1,2,3"

    # reparent to something else that is not at the origin: still in place
    code, data = axi(capsys, "set", "Cube", basic, "--parent", "Ball", "--in-place")
    assert code == 0 and data["changed"] == ["parent: Anchor -> Ball"]
    assert world_location(capsys, basic, "Cube") == [1, 2, 3]

    code, data = axi(capsys, "set", "Cube", basic, "--parent", "none", "--in-place")
    assert code == 0 and data["changed"] == ["parent: Ball -> none"]
    assert axi(capsys, "object", "Cube", basic)[1]["parent"] == "none"
    assert world_location(capsys, basic, "Cube") == [1, 2, 3]


def test_set_parent_problems_change_nothing(capsys, basic):
    before = basic.read_bytes()
    code, data = axi(capsys, "set", "Cube", basic, "--parent", "Nope", "--location", "9,9,9", "--in-place")
    assert code == 1 and data["code"] == "NOT_FOUND" and "parent object 'Nope'" in data["error"]
    code, data = axi(capsys, "set", "Cube", basic, "--parent", "Cube", "--in-place")
    assert code == 1 and data["code"] == "OP_FAILED" and "same object" in data["error"]
    code, data = axi(capsys, "set", "Ghost", basic, "--location", "1,1,1", "--in-place")
    assert code == 1 and data["code"] == "NOT_FOUND"
    assert any(h.startswith("Run `blender-axi objects ") for h in data["help"])
    assert basic.read_bytes() == before


def test_set_collection_moves_the_object(capsys, basic):
    code, data = axi(capsys, "set", "Cube", basic, "--collection", "Props", "--in-place")
    assert code == 0 and data["changed"] == ["collection: Collection -> Props"], data
    _, listing = axi(capsys, "objects", basic, "--collection", "Props")
    assert "Cube" in {o["name"] for o in listing["objects"]}
    _, obj = axi(capsys, "object", "Cube", basic)
    assert obj["collection"] == "Props"
    code, data = axi(capsys, "set", "Cube", basic, "--collection", "Scene Collection", "--in-place")
    assert code == 0 and data["changed"] == ["collection: Props -> Scene Collection"]
    code, data = axi(capsys, "set", "Cube", basic, "--collection", "Nope", "--in-place")
    assert code == 1 and data["code"] == "NOT_FOUND" and "Props" in data["error"]


def test_set_hide_and_show(capsys, basic):
    code, data = axi(capsys, "set", "Cube", basic, "--hide", "--in-place")
    assert code == 0 and data["changed"] == ["hidden: false -> true"], data
    assert axi(capsys, "object", "Cube", basic)[1]["visible"] is False
    code, data = axi(capsys, "set", "Cube", basic, "--hide", "--in-place")
    assert code == 0 and data["modified"] is False
    code, data = axi(capsys, "set", "Cube", basic, "--show", "--in-place")
    assert code == 0 and data["changed"] == ["hidden: true -> false"], data
    assert axi(capsys, "object", "Cube", basic)[1]["visible"] is True


def test_set_rename(capsys, basic):
    code, data = axi(capsys, "set", "Cube", basic, "--name", "Crate", "--in-place")
    assert code == 0 and data["object"] == "Crate" and data["changed"] == ["name: Cube -> Crate"]
    assert any("object Crate " in h for h in data["help"])
    assert axi(capsys, "object", "Crate", basic)[0] == 0
    assert axi(capsys, "object", "Cube", basic)[0] == 1
    code, data = axi(capsys, "set", "Crate", basic, "--name", "Ball", "--in-place")
    assert code == 0 and data["object"] == "Ball.001"  # Blender's own clash handling


# -- delete -----------------------------------------------------------------------------------------------------------

def test_delete_missing_name_is_all_or_nothing(capsys, basic):
    before = basic.read_bytes()
    code, data = axi(capsys, "delete", "Cube,Ghost", basic, "--in-place")
    assert code == 1 and data["code"] == "NOT_FOUND" and "'Ghost'" in data["error"] and "nothing deleted" in data["error"]
    assert any(h.startswith("Run `blender-axi objects ") for h in data["help"])
    assert basic.read_bytes() == before
    assert axi(capsys, "objects", basic)[1]["count"] == "5 of 5 total"


def test_delete_in_place_and_out(capsys, basic):
    copy = basic.with_name("copy.blend")
    code, data = axi(capsys, "delete", "Ball", basic, "--out", copy)
    assert code == 0 and data["deleted"] == ["Ball"] and data["remaining"] == "4 objects" and "children_kept" not in data
    assert axi(capsys, "objects", basic)[1]["count"] == "5 of 5 total"
    code, data = axi(capsys, "delete", "Ball", basic, "--in-place")
    assert code == 0 and data["deleted"] == ["Ball"] and data["remaining"] == "4 objects" and data["modified"] is True
    assert data["backup"].endswith("basic.blend1")
    assert axi(capsys, "objects", basic)[1]["count"] == "4 of 4 total"
    code, data = axi(capsys, "delete", "Cube, Light", basic, "--in-place")
    assert code == 0 and data["deleted"] == ["Cube", "Light"] and data["remaining"] == "2 objects"


def test_delete_neither_flag_is_exit_2(capsys, basic):
    before = basic.read_bytes()
    code, data = axi(capsys, "delete", "Ball", basic)
    assert code == 2 and any("--out <copy.blend>" in h for h in data["help"]) and basic.read_bytes() == before


def test_a5_deleting_a_parent_keeps_the_children_in_place(capsys, basic):  # ASSUMED A5
    assert axi(capsys, "set", "Ball", basic, "--parent", "Anchor", "--in-place")[0] == 0
    assert axi(capsys, "set", "Anchor", basic, "--location", "0,3,2", "--in-place")[0] == 0
    ball_world = world_location(capsys, basic, "Ball")
    assert ball_world == [3, 0, 2]  # moved with its parent: the inverse matrix was taken at the old position
    code, data = axi(capsys, "delete", "Anchor", basic, "--in-place")
    assert code == 0 and data["children_kept"] == ["Ball"] and data["remaining"] == "4 objects", data
    _, obj = axi(capsys, "object", "Ball", basic)
    assert obj["parent"] == "none"
    assert world_location(capsys, basic, "Ball") == ball_world == [3, 0, 2]
    assert obj["location"] == "3,0,2"


# -- live: a private headless MCP server on a copy of basic.blend -----------------------------------------------------

@pytest.fixture(scope="module")
def session(blender_info, fixtures_dir):
    work = paths.tmp_root() / "set_delete_live"
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
    where = ["--host", "127.0.0.1", "--port", port]
    assert axi(capsys, "live", *where)[1]["unsaved_changes"] is False

    # A3: --show calls hide_set(False), which may raise in the headless server; the command must still succeed
    code, data = axi(capsys, "set", "Cube", *live, "--show")
    assert code == 0, data
    assert data["modified"] is False and data["saved"] == "none (no changes)"
    assert axi(capsys, "live", *where)[1]["unsaved_changes"] is False

    code, data = axi(capsys, "set", "Cube", *live, "--location", "5,5,5")
    assert code == 0, data
    assert data["modified"] is True and data["changed"] == ["location: 0,0,0 -> 5,5,5"]
    assert data["undo"] == "unavailable (headless server)" and data["saved"] == "none (live session, unsaved)"
    assert data["file"] == blend.as_posix()
    assert axi(capsys, "scene", *live)[1]["unsaved_changes"] is True
    assert axi(capsys, "object", "Cube", *live)[1]["location"] == "5,5,5"

    code, data = axi(capsys, "set", "Cube", *live, "--hide")
    assert code == 0 and data["modified"] is True
    assert axi(capsys, "object", "Cube", *live)[1]["visible"] is False

    code, data = axi(capsys, "delete", "Ghost", *live)
    assert code == 1 and data["code"] == "NOT_FOUND"
    code, data = axi(capsys, "delete", "Ball", *live)
    assert code == 0 and data["deleted"] == ["Ball"] and data["remaining"] == "4 objects" and data["modified"] is True
    assert data["undo"] == "unavailable (headless server)"
    _, listing = axi(capsys, "objects", *live)
    assert "Ball" not in {o["name"] for o in listing["objects"]} and listing["count"] == "4 of 4 total"

    # --out / a file positional have no meaning live, and fail before any change
    code, err = axi(capsys, "set", "Cube", *live, "--location", "0,0,0", "--out", "x.blend")
    assert code == 2 and "no copy" in err["error"]
    code, err = axi(capsys, "delete", "Cube", blend, *live)
    assert code == 2 and err["error"] == "--live uses the file open in Blender; drop <file>"
    assert axi(capsys, "object", "Cube", *live)[1]["location"] == "5,5,5"

    # --in-place saves the open file
    before = blend.stat().st_mtime_ns
    code, data = axi(capsys, "set", "Cube", *live, "--location", "6,6,6", "--in-place")
    assert code == 0 and data["modified"] is True and data["saved"].endswith("session.blend")
    assert blend.stat().st_mtime_ns > before
    assert axi(capsys, "live", *where)[1]["unsaved_changes"] is False
