"""Real-Blender checks for `modifier` (v1.3 U2): create / upsert / remove, change detection (the fingerprint walks
modifier settings, P4), every typed kind, the generic `--prop` path, errors, and --live on a private server (never 9876).

Headless tests run the command through `cli.main` (the registry entry is patched in, so they pass before the lead's
splice). Live tests start a REAL headless MCP server on a free port on a copy of basic.blend.
"""

from __future__ import annotations

import json
import math
import shutil

import pytest

from blender_axi import cli, paths, registry
from test_live_optional import real_server

pytestmark = pytest.mark.blender

MODIFIER = "blender_axi.commands.modifier:SPEC"


@pytest.fixture(autouse=True)
def _register_modifier(monkeypatch):
    monkeypatch.setitem(registry.COMMANDS, "modifier", MODIFIER)


@pytest.fixture
def work():
    d = paths.tmp_root() / "modifier"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    return d


@pytest.fixture
def basic(work, fixtures_dir):
    path = work / "basic.blend"
    shutil.copy2(fixtures_dir / "basic.blend", path)
    return path


def axi(capsys, *argv):
    code = cli.main(["--json", *map(str, argv)])
    out = capsys.readouterr()
    assert out.err == ""
    return code, json.loads(out.out)


def stack(capsys, blend, obj="Cube", *live):
    """The modifier stack of `obj` as it really is (via exec): name, type and the properties each test looks at."""
    code = (
        "o = bpy.data.objects[%r]\n"
        "def get(m, k):\n"
        "    v = getattr(m, k, None)\n"
        "    if hasattr(v, 'name'):\n"
        "        return v.name\n"
        "    if hasattr(v, '__len__') and not isinstance(v, str):\n"
        "        return [round(x, 4) if isinstance(x, float) else x for x in v]\n"
        "    return round(v, 4) if isinstance(v, float) else v\n"
        "KEYS = ('width', 'segments', 'limit_method', 'angle_limit', 'levels', 'render_levels', 'subdivision_type',\n"
        "        'count', 'relative_offset_displace', 'use_relative_offset', 'use_constant_offset',\n"
        "        'constant_offset_displace', 'use_axis', 'use_clip', 'mirror_object', 'object', 'operation', 'solver',\n"
        "        'thickness', 'offset', 'use_even_offset')\n"
        "result = {'mods': [dict(name=m.name, type=m.type, **{k: get(m, k) for k in KEYS if hasattr(m, k)})\n"
        "                   for m in o.modifiers]}\n" % obj)
    argv = ["exec", *live] if live else ["exec", blend]
    c, data = axi(capsys, *argv, "--allow-exec", "--code", code)
    assert c == 0, data
    return data["result"]["mods"]


# -- bevel: create, upsert, change detection, remove -------------------------------------------------------------------

def test_bevel_create_then_object_lists_it(capsys, basic):
    code, data = axi(capsys, "modifier", "Cube", basic, "bevel", "--width", "0.05", "--segments", "3", "--in-place")
    assert code == 0, data
    assert data["created"] is True and data["modifier"] == "Bevel" and data["type"] == "BEVEL"
    assert data["modified"] is True and data["saved"].endswith("basic.blend") and data["backup"].endswith("basic.blend1")
    assert "width: 0.1 -> 0.05" in data["changed"] and "segments: 1 -> 3" in data["changed"]
    assert data["stack"] == "1 (Bevel)" and "undo" not in data
    assert any("object Cube" in h and "basic.blend" in h for h in data["help"])
    code, obj = axi(capsys, "object", "Cube", basic)
    assert code == 0 and obj["modifiers"] == [{"name": "Bevel", "type": "BEVEL"}]
    mods = stack(capsys, basic)
    assert mods[0]["width"] == 0.05 and mods[0]["segments"] == 3


def test_file_may_come_before_the_kind(capsys, basic):
    code, data = axi(capsys, "modifier", "Cube", "bevel", basic, "--width", "0.07", "--in-place")
    assert code == 0 and data["created"] is True
    assert stack(capsys, basic)[0]["width"] == 0.07


def test_rerun_changes_nothing_and_a_single_setting_is_saved(capsys, basic):
    axi(capsys, "modifier", "Cube", basic, "bevel", "--width", "0.05", "--segments", "3", "--in-place")
    before = (basic.stat().st_mtime_ns, basic.read_bytes())
    code, data = axi(capsys, "modifier", "Cube", basic, "bevel", "--width", "0.05", "--segments", "3", "--in-place")
    assert code == 0 and data["created"] is False and data["modified"] is False
    assert data["changed"] == "none (values already as requested)" and data["saved"] == "none (no changes)"
    assert "backup" not in data and (basic.stat().st_mtime_ns, basic.read_bytes()) == before

    code, data = axi(capsys, "modifier", "Cube", basic, "bevel", "--width", "0.1", "--in-place")  # P4: width alone
    assert code == 0 and data["modified"] is True and data["saved"].endswith("basic.blend")
    assert data["changed"] == ["width: 0.05 -> 0.1"]
    assert stack(capsys, basic)[0]["width"] == 0.1


def test_bevel_angle_is_in_degrees(capsys, basic):
    code, data = axi(capsys, "modifier", "Cube", basic, "bevel", "--limit", "angle", "--angle", "45", "--in-place")
    assert code == 0, data
    assert "angle_limit: 30 -> 45" in data["changed"]
    assert stack(capsys, basic)[0]["angle_limit"] == pytest.approx(math.radians(45), abs=1e-4)


def test_remove_then_remove_again(capsys, basic):
    axi(capsys, "modifier", "Cube", basic, "bevel", "--width", "0.05", "--in-place")
    code, data = axi(capsys, "modifier", "Cube", basic, "bevel", "--remove", "--in-place")
    assert code == 0 and data["removed"] is True and data["stack"] == "0" and data["modified"] is True
    assert stack(capsys, basic) == []
    before = (basic.stat().st_mtime_ns, basic.read_bytes())
    code, data = axi(capsys, "modifier", "Cube", basic, "bevel", "--remove", "--in-place")
    assert code == 0 and data["removed"] is False and data["modified"] is False
    assert (basic.stat().st_mtime_ns, basic.read_bytes()) == before


# -- the other typed kinds ---------------------------------------------------------------------------------------------

def test_subdivision_array_mirror_solidify(capsys, basic):
    code, data = axi(capsys, "modifier", "Cube", basic, "subdivision", "--levels", "2", "--render-levels", "3", "--simple",
                     "--in-place")
    assert code == 0 and data["modifier"] == "Subdivision" and data["type"] == "SUBSURF", data
    code, data = axi(capsys, "modifier", "Cube", basic, "array", "--count", "4", "--constant", "0,3,0", "--in-place")
    assert code == 0 and data["modifier"] == "Array", data
    code, data = axi(capsys, "modifier", "Cube", basic, "mirror", "--axis", "yz", "--clip", "--object", "Ball", "--in-place")
    assert code == 0 and data["modifier"] == "Mirror", data
    code, data = axi(capsys, "modifier", "Cube", basic, "solidify", "--thickness", "0.2", "--offset", "1", "--even",
                     "--in-place")
    assert code == 0 and data["modifier"] == "Solidify" and data["stack"].startswith("4 (Subdivision, Array, Mirror"), data
    by_type = {m["type"]: m for m in stack(capsys, basic)}
    assert (by_type["SUBSURF"]["levels"], by_type["SUBSURF"]["render_levels"]) == (2, 3)
    assert by_type["SUBSURF"]["subdivision_type"] == "SIMPLE"
    assert by_type["ARRAY"]["count"] == 4 and by_type["ARRAY"]["constant_offset_displace"] == [0, 3, 0]
    assert by_type["ARRAY"]["use_constant_offset"] is True and by_type["ARRAY"]["use_relative_offset"] is False
    assert by_type["MIRROR"]["use_axis"] == [False, True, True] and by_type["MIRROR"]["use_clip"] is True
    assert by_type["MIRROR"]["mirror_object"] == "Ball"
    assert by_type["SOLIDIFY"]["thickness"] == 0.2 and by_type["SOLIDIFY"]["offset"] == 1
    assert by_type["SOLIDIFY"]["use_even_offset"] is True


def test_array_relative_offset(capsys, basic):
    code, data = axi(capsys, "modifier", "Cube", basic, "array", "--count", "3", "--offset", "1.5,0,0", "--in-place")
    assert code == 0, data
    m = stack(capsys, basic)[0]
    assert m["count"] == 3 and m["relative_offset_displace"] == [1.5, 0, 0] and m["use_relative_offset"] is True


def test_boolean_with_a_cutter(capsys, basic):
    code, data = axi(capsys, "modifier", "Cube", basic, "boolean", "--object", "Ball", "--operation", "UNION", "--in-place")
    assert code == 0, data
    assert data["created"] is True and "object: none -> Ball" in data["changed"] and "operation: DIFFERENCE -> UNION" in data["changed"]
    code, obj = axi(capsys, "object", "Cube", basic)
    assert obj["modifiers"] == [{"name": "Boolean", "type": "BOOLEAN"}]
    m = stack(capsys, basic)[0]
    assert m["object"] == "Ball" and m["operation"] == "UNION" and m["solver"] == "EXACT"


def test_boolean_without_a_cutter_fails_and_writes_nothing(capsys, basic):
    before = basic.read_bytes()
    code, data = axi(capsys, "modifier", "Cube", basic, "boolean", "--in-place")
    assert code == 1 and data["code"] == "OP_FAILED" and data["error"] == "boolean needs --object <cutter>"
    assert basic.read_bytes() == before and not basic.with_name("basic.blend1").exists()


def test_unknown_cutter_is_not_found(capsys, basic):
    before = basic.read_bytes()
    code, data = axi(capsys, "modifier", "Cube", basic, "boolean", "--object", "Bal", "--in-place")
    assert code == 1 and data["code"] == "NOT_FOUND" and "Ball" in data["error"]
    assert basic.read_bytes() == before


# -- the generic path ---------------------------------------------------------------------------------------------------

def test_wireframe_via_prop(capsys, basic):
    code, data = axi(capsys, "modifier", "Cube", basic, "WIREFRAME", "--prop", "thickness=0.05", "--in-place")
    assert code == 0, data
    assert data["created"] is True and data["modifier"] == "Wireframe" and data["type"] == "WIREFRAME"
    assert any(c.startswith("thickness: ") and c.endswith("-> 0.05") for c in data["changed"]), data["changed"]
    code, obj = axi(capsys, "object", "Cube", basic)
    assert obj["modifiers"] == [{"name": "Wireframe", "type": "WIREFRAME"}]


def test_prop_works_on_a_typed_kind_and_reports_errors(capsys, basic):
    code, data = axi(capsys, "modifier", "Cube", basic, "bevel", "--prop", "use_clamp_overlap=false,harden_normals=true",
                     "--in-place")
    assert code == 0 and data["created"] is True, data
    before = basic.read_bytes()
    for prop, message in (("nope=1", "unknown property 'nope' for BEVEL"),
                          ("limit_method=SIDEWAYS", "invalid value 'SIDEWAYS' for 'limit_method'; one of NONE, ANGLE, WEIGHT, VGROUP"),
                          ("segments=many", "invalid value 'many' for 'segments'; expected an integer")):
        code, data = axi(capsys, "modifier", "Cube", basic, "bevel", "--prop", prop, "--in-place")
        assert code == 1 and data["code"] == "OP_FAILED" and data["error"] == message, data
    assert basic.read_bytes() == before


def test_unknown_identifier_is_op_failed(capsys, basic):
    before = basic.read_bytes()
    code, data = axi(capsys, "modifier", "Cube", basic, "WIREFRAMEX", "--in-place")  # A4: Blender raises TypeError
    assert code == 1 and data["code"] == "OP_FAILED" and data["error"] == "unknown modifier type 'WIREFRAMEX'"
    assert basic.read_bytes() == before


# -- errors and write rules -------------------------------------------------------------------------------------------

def test_camera_and_light_cannot_take_modifiers(capsys, basic):
    before = basic.read_bytes()
    for name in ("Camera", "Light", "Anchor"):
        code, data = axi(capsys, "modifier", name, basic, "bevel", "--in-place")
        assert code == 1 and data["code"] == "OP_FAILED" and data["error"].endswith("cannot take modifiers"), data
    assert basic.read_bytes() == before and not basic.with_name("basic.blend1").exists()


def test_name_clash_with_another_kind_is_op_failed(capsys, basic):
    axi(capsys, "modifier", "Cube", basic, "subdivision", "--name", "Rim", "--in-place")
    code, data = axi(capsys, "modifier", "Cube", basic, "bevel", "--name", "Rim", "--in-place")
    assert code == 1 and data["error"] == "'Rim' is a SUBSURF, not BEVEL"


def test_unknown_object_is_not_found(capsys, basic):
    code, data = axi(capsys, "modifier", "Cub", basic, "bevel", "--in-place")
    assert code == 1 and data["code"] == "NOT_FOUND" and "Cube" in data["error"]
    assert any("objects" in h and "basic.blend" in h for h in data["help"])


def test_out_leaves_the_original_untouched(capsys, basic, work):
    before = (basic.stat().st_mtime_ns, basic.read_bytes())
    copy = work / "copy.blend"
    code, data = axi(capsys, "modifier", "Cube", basic, "bevel", "--width", "0.2", "--out", copy)
    assert code == 0 and data["modified"] is True and data["saved"].endswith("copy.blend") and "backup" not in data
    assert (basic.stat().st_mtime_ns, basic.read_bytes()) == before
    assert stack(capsys, basic) == [] and stack(capsys, copy)[0]["width"] == 0.2
    assert any("object Cube" in h and "copy.blend" in h for h in data["help"])


def test_neither_flag_and_bad_flags_exit_2_before_blender(capsys, basic):
    before = (basic.stat().st_mtime_ns, basic.read_bytes())
    code, data = axi(capsys, "modifier", "Cube", basic, "bevel", "--width", "0.1")
    assert code == 2 and "say where to write it" in data["error"]
    assert any("modifier Cube bevel" in h and "--in-place" in h for h in data["help"])
    for argv in (["Cube", basic, "subdivision", "--width", "0.1"], ["Cube", basic, "bevel", "--remove", "--width", "0.1"],
                 ["Cube", basic, "bevel", "--prop", "oops"], ["Cube", basic, "wobble"], ["Cube", basic]):
        code, data = axi(capsys, "modifier", *argv, "--in-place")
        assert code == 2 and data["code"] == "VALIDATION_ERROR", (argv, data)
    assert (basic.stat().st_mtime_ns, basic.read_bytes()) == before


def test_render_after_bevel_and_subdivision_exits_0(capsys, basic, work):
    axi(capsys, "modifier", "Cube", basic, "bevel", "--width", "0.1", "--segments", "2", "--in-place")
    axi(capsys, "modifier", "Cube", basic, "subdivision", "--levels", "1", "--in-place")
    png = work / "bevel.png"
    code, data = axi(capsys, "render", basic, "--engine", "WORKBENCH", "--resolution", "64x64", "--out", png)
    assert code == 0, data
    assert png.exists() and png.stat().st_size > 0


# -- live: a private headless MCP server on a copy of basic.blend ----------------------------------------------------

@pytest.fixture(scope="module")
def session(blender_info, fixtures_dir):
    work = paths.tmp_root() / "modifier_live"
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
    assert axi(capsys, "live", "--host", "127.0.0.1", "--port", port)[1]["unsaved_changes"] is False

    code, data = axi(capsys, "modifier", "Cube", "bevel", *live, "--width", "0.2")
    assert code == 0, data
    assert data["created"] is True and data["modified"] is True and data["stack"] == "1 (Bevel)"
    assert data["undo"] == "unavailable (headless server)" and data["saved"] == "none (live session, unsaved)"
    assert data["file"] == blend.as_posix()
    assert any(h.startswith("Run `blender-axi object Cube") and "--live" in h for h in data["help"])
    assert any("Changes are only in the open session" in h for h in data["help"])
    assert axi(capsys, "live", "--host", "127.0.0.1", "--port", port)[1]["unsaved_changes"] is True
    code, obj = axi(capsys, "object", "Cube", *live)
    assert code == 0 and obj["modifiers"] == [{"name": "Bevel", "type": "BEVEL"}]
    assert stack(capsys, blend, "Cube", *live)[0]["width"] == 0.2
    assert stack(capsys, blend) == []  # the file on disk is untouched

    code, again = axi(capsys, "modifier", "Cube", "bevel", *live, "--width", "0.2")  # an upsert that changes nothing
    assert code == 0 and again["modified"] is False and again["created"] is False

    code, err = axi(capsys, "modifier", "Cube", blend, "bevel", *live)  # a file positional and --out are meaningless live
    assert code == 2 and err["error"] == "--live uses the file open in Blender; drop <file>"
    code, err = axi(capsys, "modifier", "Cube", "bevel", *live, "--out", "x.blend")
    assert code == 2 and "no copy" in err["error"]

    before = blend.stat().st_mtime_ns  # --in-place saves the open file
    code, data = axi(capsys, "modifier", "Cube", "bevel", *live, "--remove", "--in-place")
    assert code == 0 and data["removed"] is True and data["saved"].endswith("session.blend")
    assert blend.stat().st_mtime_ns > before and stack(capsys, blend) == []
