"""Real-Blender checks for U3: ping, temp discipline, fixtures, save semantics."""

import json
import subprocess
from pathlib import Path

import pytest

from blender_axi import paths
from blender_axi.errors import AxiError
from blender_axi.runner import HeadlessTransport, SaveSpec

pytestmark = pytest.mark.blender

INSPECT = """
import bpy, json, sys
out = sys.argv[sys.argv.index('--') + 1]
sc = bpy.context.scene
objs = {o.name: {"type": o.type, "colls": [c.name for c in o.users_collection]} for o in bpy.data.objects}
json.dump({"objs": objs, "start": sc.frame_start, "end": sc.frame_end,
           "res": [sc.render.resolution_x, sc.render.resolution_y],
           "colls": [c.name for c in bpy.data.collections],
           "camera": sc.camera.name if sc.camera else None}, open(out, "w"))
"""


@pytest.fixture(scope="module")
def transport(blender_info):
    return HeadlessTransport(blender_info)


def inspect(blender_info, blend: Path) -> dict:
    work = paths.run_dir("inspect")
    script, out = work / "inspect.py", work / "out.json"
    script.write_text(INSPECT)
    subprocess.run([str(blender_info.path), "--background", "--factory-startup", str(blend),
                    "--python-exit-code", "3", "--python", str(script), "--", str(out)],
                   env=paths.blender_env(), cwd=str(work), capture_output=True, timeout=120, check=True)
    data = json.loads(out.read_text())
    paths.cleanup(work, False)
    return data


def test_ping(transport):
    out = transport.run("ping", {}, blend=None)
    assert out.result["blender_version"].startswith("5.")
    assert out.result["python"].startswith("3.")
    assert out.result["file"] == "" and out.modified is False and out.saved is None
    assert out.blender_version == out.result["blender_version"]


def test_ping_factory_prefs_and_addons(transport):
    res = transport.run("ping", {}, blend=None).result
    assert res["prefs"] == "factory"
    assert "io_scene_gltf2" in res["addons"]
    assert not any(a.endswith(".mcp") for a in res["addons"])  # user extension is not loaded


def test_ping_user_prefs(blender_info):
    out = HeadlessTransport(blender_info, user_prefs=True).run("ping", {}, blend=None)
    assert out.result["prefs"] == "user"
    if not any(a.endswith(".mcp") for a in out.result["addons"]):
        pytest.skip("this machine's user prefs do not enable the mcp extension (prefs verified only)")
    assert "bl_ext.user_default.mcp" in out.result["addons"]


def test_user_prefs_with_a_blend_still_loads_that_file(blender_info, fixtures_dir):
    out = HeadlessTransport(blender_info, user_prefs=True).run("ping", {}, blend=fixtures_dir / "basic.blend")
    assert out.result["file"].endswith("basic.blend") and out.result["prefs"] == "user"


def test_blender_temp_is_project_local(transport):
    out = transport.run("ping", {}, blend=None)
    want = Path(paths.blender_tmp()).resolve()
    assert Path(out.result["env_temp"]).resolve() == want
    assert Path(out.result["tempdir"]).resolve() == want


def test_unknown_op_is_op_failed(transport):
    with pytest.raises(AxiError) as e:
        transport.run("nope", {}, blend=None)
    assert e.value.code == "OP_FAILED"


def test_basic_fixture(blender_info, fixtures_dir):
    d = inspect(blender_info, fixtures_dir / "basic.blend")
    assert {o: v["type"] for o, v in d["objs"].items()} == {
        "Cube": "MESH", "Camera": "CAMERA", "Light": "LIGHT", "Ball": "MESH", "Anchor": "EMPTY"}
    assert d["objs"]["Ball"]["colls"] == ["Props"] and d["objs"]["Anchor"]["colls"] == ["Props"]
    assert "Props" in d["colls"] and d["camera"] == "Camera"
    assert (d["start"], d["end"], d["res"]) == (1, 24, [64, 64])


def test_empty_fixture(blender_info, fixtures_dir):
    d = inspect(blender_info, fixtures_dir / "empty.blend")
    assert d["objs"] == {}


def test_ping_with_blend_reports_file(transport, fixtures_dir):
    out = transport.run("ping", {}, blend=fixtures_dir / "basic.blend")
    assert out.result["file"].endswith("basic.blend")
    assert out.modified is False


def test_save_copy_only_when_dirty(transport, fixtures_dir):
    # a clean run never writes the copy
    target = paths.tmp_root() / "tests" / "save" / "clean.blend"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    out = transport.run("ping", {}, blend=fixtures_dir / "basic.blend", save=SaveSpec("copy", str(target)))
    assert out.saved is None and not target.exists()


DIRTY_OP = '''
def run(params):
    import bpy
    kind = params.get("kind", "remove")
    if kind == "remove":
        bpy.data.objects.remove(bpy.data.objects["Cube"])
    elif kind == "move":
        bpy.data.objects["Cube"].location.x += 1.5
    elif kind == "resolution":
        bpy.context.scene.render.resolution_x = 128
    elif kind == "vertex":
        bpy.data.objects["Cube"].data.vertices[0].co.x += 0.25
    return {"kind": kind}
'''


@pytest.fixture
def dirty_op():
    """Temporarily drop a test-only op module into the package so Blender can import it; always removed."""
    from blender_axi.blender_side import ops as ops_mod

    module = Path(ops_mod.__file__).with_name("op_zz_testdirty.py")
    module.write_text(DIRTY_OP)
    try:
        yield "zz_testdirty"
    finally:
        module.unlink(missing_ok=True)


def test_save_copy_when_dirty_leaves_original_alone(blender_info, transport, fixtures_dir, dirty_op):
    src = fixtures_dir / "basic.blend"
    before = (src.stat().st_mtime_ns, src.stat().st_size)
    target = paths.tmp_root() / "tests" / "save" / "dirty.blend"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    out = transport.run(dirty_op, {}, blend=src, save=SaveSpec("copy", str(target)))
    assert out.modified is True and out.saved == str(target) and target.is_file()
    assert (src.stat().st_mtime_ns, src.stat().st_size) == before
    assert "Cube" not in inspect(blender_info, target)["objs"]
    assert "Cube" in inspect(blender_info, src)["objs"]


@pytest.mark.parametrize("kind", ["move", "resolution", "vertex"])
def test_direct_data_edits_count_as_modified(transport, fixtures_dir, dirty_op, kind):
    """bpy.data.is_dirty stays False for direct data edits; the bootstrap fingerprint must catch them."""
    target = paths.tmp_root() / "tests" / "save" / f"edit-{kind}.blend"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    out = transport.run(dirty_op, {"kind": kind}, blend=fixtures_dir / "basic.blend",
                        save=SaveSpec("copy", str(target)))
    assert out.modified is True and target.is_file()


def test_save_in_place_when_dirty(blender_info, transport, fixtures_dir, dirty_op):
    work = paths.tmp_root() / "tests" / "save" / "inplace.blend"
    work.parent.mkdir(parents=True, exist_ok=True)
    work.write_bytes((fixtures_dir / "basic.blend").read_bytes())
    out = transport.run(dirty_op, {}, blend=work, save=SaveSpec("in_place"))
    assert out.modified is True and Path(out.saved).resolve() == work.resolve()
    assert "Cube" not in inspect(blender_info, work)["objs"]
