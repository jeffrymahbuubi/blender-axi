"""Unit tests for `scene` (fake transport, fake bpy) and the shared `.blend` inference helper."""

from __future__ import annotations

import types

import pytest

from blender_axi import flags, toon
from blender_axi.blender_side import op_scene_summary
from blender_axi.commands import scene
from blender_axi.errors import AxiError
from blender_axi.runner import RunOutcome

SUMMARY = {
    "filepath": "C:/x/basic.blend", "blender_version": "5.2.0 LTS", "scene": "Scene", "engine": "BLENDER_EEVEE",
    "res_x": 64, "res_y": 64, "percent": 100, "frame_start": 1, "frame_end": 24, "frame_current": 1,
    "fps": 24, "fps_base": 1.0, "camera": "Camera", "object_count": 5,
    "types": {"CAMERA": 1, "MESH": 2, "LIGHT": 1, "EMPTY": 1},
    "collections": ["Props"], "materials": ["Material"], "is_dirty": True,
}


class FakeTransport:
    name = "fake"

    def __init__(self, result=None, warnings=None, error=None):
        self.result, self.warnings, self.error, self.calls = result or SUMMARY, warnings or [], error, []

    def run(self, op, params, *, blend=None, save=None, timeout=None):
        self.calls.append((op, params, blend, timeout))
        if self.error:
            raise self.error
        return RunOutcome(result=self.result, modified=False, saved=None, blender_version="5.2.0 LTS",
                          duration_ms=1, stdout="", warnings=self.warnings)


def invoke(spec, argv, transport):
    inv, wants_help = flags.parse(spec, argv)
    assert not wants_help
    inv.transport = transport
    return spec.run(inv)


@pytest.fixture
def blend_dir(tmp_path, monkeypatch):
    (tmp_path / "basic.blend").write_bytes(b"x")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_scene_shape_and_order(blend_dir):
    t = FakeTransport()
    res = invoke(scene.SPEC, [], t)
    assert list(res.data) == ["file", "blender", "scene", "engine", "resolution", "frames", "fps", "camera",
                              "objects", "collections", "materials"]
    d = res.data
    assert d["file"] == "basic.blend" and d["blender"] == "5.2.0 LTS" and d["engine"] == "EEVEE"
    assert d["resolution"] == "64x64@100%" and d["frames"] == "1-24 (current 1)" and d["fps"] == 24
    assert d["camera"] == "Camera"
    assert d["objects"] == "5 (MESH 2, CAMERA 1, EMPTY 1, LIGHT 1)"
    assert d["collections"] == "1 (Props)" and d["materials"] == "1 (Material)"
    assert "unsaved_changes" not in d
    op, params, blend, timeout = t.calls[0]
    assert op == "scene_summary" and params == {} and blend == (blend_dir / "basic.blend").resolve()
    assert [h.split("`")[1] for h in res.help] == [
        "blender-axi objects <file>", "blender-axi render <file> --out <path.png>",
        "blender-axi export <file> --out <path.glb>"]
    toon.encode({**res.data, "help": res.help})  # encodable


def test_scene_empty_states_and_fps(blend_dir):
    r = dict(SUMMARY, object_count=0, types={}, collections=[], materials=[], camera=None, fps=30, fps_base=1.001)
    d = invoke(scene.SPEC, [], FakeTransport(r)).data
    assert d["objects"] == "0" and d["collections"] == "0" and d["materials"] == "0"
    assert d["camera"] == "none" and d["fps"] == 29.97


def test_scene_names_capped(blend_dir):
    r = dict(SUMMARY, collections=[f"C{i}" for i in range(8)])
    assert invoke(scene.SPEC, [], FakeTransport(r)).data["collections"] == "8 (C0, C1, C2, C3, C4, +3 more)"


def test_scene_warning_key(blend_dir):
    d = invoke(scene.SPEC, [], FakeTransport(warnings=["Blender 4.2 untested; 5.2 LTS verified"])).data
    assert d["warning"] == "Blender 4.2 untested; 5.2 LTS verified"


def test_scene_live(blend_dir):
    t = FakeTransport()
    res = invoke(scene.SPEC, ["--live", "--port", "9877"], t)
    assert t.calls[0][2] is None
    assert res.data["file"] == "C:/x/basic.blend" and res.data["unsaved_changes"] is True
    assert res.help[0] == "Run `blender-axi objects --live --port 9877` to list objects"


def test_scene_live_file_uses_forward_slashes(blend_dir):
    r = dict(SUMMARY, filepath="D:\\proj\\sub dir\\basic.blend")
    res = invoke(scene.SPEC, ["--live"], FakeTransport(r))
    assert res.data["file"] == "D:/proj/sub dir/basic.blend"
    assert "\\" not in toon.encode({**res.data, "help": res.help})


def test_scene_live_unsaved_file_label(blend_dir):
    assert invoke(scene.SPEC, ["--live"], FakeTransport(dict(SUMMARY, filepath=""))).data["file"] == "(unsaved)"


def test_scene_live_rejects_file(blend_dir):
    with pytest.raises(AxiError) as e:
        invoke(scene.SPEC, ["basic.blend", "--live"], FakeTransport())
    assert e.value.exit_code == 2 and e.value.code == "VALIDATION_ERROR"


def test_explicit_file_wins_over_cwd(blend_dir):
    (blend_dir / "other.blend").write_bytes(b"x")
    t = FakeTransport()
    res = invoke(scene.SPEC, ["other.blend"], t)
    assert res.data["file"] == "other.blend" and t.calls[0][2].name == "other.blend"


def test_missing_file_is_not_found(blend_dir):
    with pytest.raises(AxiError) as e:
        invoke(scene.SPEC, ["nope.blend"], FakeTransport())
    assert e.value.code == "NOT_FOUND" and e.value.exit_code == 1
    assert "basic.blend" in e.value.help[0]


def test_not_a_blend_file(blend_dir):
    (blend_dir / "a.txt").write_text("x")
    with pytest.raises(AxiError) as e:
        invoke(scene.SPEC, ["a.txt"], FakeTransport())
    assert e.value.exit_code == 2


def test_no_blend_in_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(AxiError) as e:
        invoke(scene.SPEC, [], FakeTransport())
    assert e.value.exit_code == 2 and "<file.blend>" in e.value.help[0]


def test_many_blends_lists_candidates(blend_dir):
    for n in ("b.blend", "c d.blend"):
        (blend_dir / n).write_bytes(b"x")
    with pytest.raises(AxiError) as e:
        invoke(scene.SPEC, [], FakeTransport())
    assert e.value.exit_code == 2 and "3 .blend files" in e.value.message
    assert e.value.help == ["blender-axi scene b.blend", "blender-axi scene basic.blend",
                            'blender-axi scene "c d.blend"']


def test_unknown_flag_exit_2(blend_dir):
    with pytest.raises(AxiError) as e:
        flags.parse(scene.SPEC, ["--bogus"])
    assert e.value.exit_code == 2


# -- op with a fake bpy ----------------------------------------------------------------------------

def test_op_scene_summary(fake_bpy):
    obj = lambda n, t: types.SimpleNamespace(name=n, type=t)  # noqa: E731
    sc = types.SimpleNamespace(
        name="S", objects=[obj("a", "MESH"), obj("b", "MESH"), obj("c", "CAMERA")], camera=obj("c", "CAMERA"),
        frame_start=1, frame_end=10, frame_current=3,
        render=types.SimpleNamespace(engine="CYCLES", resolution_x=8, resolution_y=4, resolution_percentage=50,
                                     fps=25, fps_base=1.0),
        collection=types.SimpleNamespace(children_recursive=[types.SimpleNamespace(name="K")]))
    fake_bpy.context.scene = sc
    fake_bpy.data.materials = [types.SimpleNamespace(name="M")]
    fake_bpy.data.filepath = "f.blend"
    fake_bpy.data.is_dirty = True
    r = op_scene_summary.run({})
    assert r["types"] == {"MESH": 2, "CAMERA": 1} and r["object_count"] == 3 and r["camera"] == "c"
    assert r["collections"] == ["K"] and r["materials"] == ["M"] and r["is_dirty"] is True
    assert r["engine"] == "CYCLES" and (r["res_x"], r["res_y"], r["percent"]) == (8, 4, 50)
    sc.camera = None
    assert op_scene_summary.run({})["camera"] is None
