"""0.10.0 U3: `export` dry run `also:`, `triangles`, `--json` full set, `--cameras`. Fake transport + fake bpy."""

import contextlib
import types
from pathlib import Path

import pytest

from blender_axi import flags as flagparser
from blender_axi.blender_side import op_export
from blender_axi.commands import export as cmd
from blender_axi.errors import AxiError
from blender_axi.runner import RunOutcome

WORK = Path(__file__).resolve().parents[2] / ".tmp" / "tests" / "unit_export_more"
SET = [{"name": "Ring", "type": "MESH"}, {"name": "Floor", "type": "MESH"}]


class FakeTransport:
    name = "fake"

    def __init__(self, **result):
        self.calls = []
        self.result = result

    def run(self, op, params, *, blend=None, save=None, timeout=None):
        self.calls.append((op, params, blend, timeout))
        if params["dry_run"]:
            res = {"format": params["format"], "dry_run": True, "objects": 2, "export_set": SET,
                   "skipped_hidden": [], "excluded": [], "non_geometry": [], "modifiers": "applied",
                   "modifiers_note": None}
        else:
            res = {"path": params["out"], "format": params["format"], "objects": 2, "size_bytes": 2048,
                   "skipped_hidden": [], "excluded": [], "modifiers": "applied", "modifiers_note": None}
        res.update(self.result)
        return RunOutcome(result=res, modified=False, saved=None, blender_version="5.2.0 LTS",
                          duration_ms=99, stdout="", warnings=[])


@pytest.fixture
def work(monkeypatch):
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "scene.blend").write_bytes(b"")
    monkeypatch.chdir(WORK)
    return WORK


def invoke(argv, *, json=False, full=False, **result):
    inv, _ = flagparser.parse(cmd.SPEC, argv)
    inv.globals.json, inv.globals.full = json, full
    inv.transport = FakeTransport(**result)
    return cmd.run(inv), inv.transport


# --- CLI ---------------------------------------------------------------------------------------------

def test_dry_run_prints_also_only_when_the_op_returns_it(work):
    res, _ = invoke(["--out", "a.glb", "--dry-run"], also=[{"name": "Grp", "type": "EMPTY"}], triangles=12)
    assert list(res.data) == ["file", "format", "dry_run", "would_export", "export_set", "also", "skipped",
                              "triangles", "modifiers", "duration_ms"]
    assert res.data["also"] == "1 empty (Grp)" and res.data["triangles"] == 12
    res, _ = invoke(["--out", "a.glb", "--dry-run"], also=[], triangles=12)
    assert "also" not in res.data and list(res.data)[-3:] == ["triangles", "modifiers", "duration_ms"]
    res, _ = invoke(["--out", "a.glb", "--dry-run"])
    assert "also" not in res.data and "triangles" not in res.data  # an op that sends neither prints neither


def test_also_text_counts_and_names_per_type(work):
    rows = [{"name": "Grp", "type": "EMPTY"}, {"name": "Rig", "type": "EMPTY"}, {"name": "Cam", "type": "CAMERA"}]
    res, _ = invoke(["--out", "a.glb", "--dry-run", "--cameras"], also=rows)
    assert res.data["also"] == "2 empties (Grp, Rig), 1 camera (Cam)"
    rows = [{"name": f"E{i}", "type": "EMPTY"} for i in range(7)]
    res, _ = invoke(["--out", "a.glb", "--dry-run"], also=rows)
    assert res.data["also"] == "7 empties (E0, E1, E2, E3, E4, +2 more)"


def test_json_dry_run_emits_all_rows_and_no_full_hint(work):
    many = [{"name": f"O{i}", "type": "MESH"} for i in range(25)]
    res, _ = invoke(["--out", "a.glb", "--dry-run"], json=True, export_set=many, objects=25)
    assert len(res.data["export_set"]) == 25 and not any("--full" in h for h in res.help)
    res, _ = invoke(["--out", "a.glb", "--dry-run"], export_set=many, objects=25)  # TOON keeps 20 + the hint
    assert len(res.data["export_set"]) == 20 and any("20 of 25" in h and "--full" in h for h in res.help)


def test_triangles_after_objects_in_the_real_export(work):
    res, _ = invoke(["--out", "a.glb"], triangles=372)
    assert list(res.data) == ["file", "exported", "format", "objects", "triangles", "modifiers", "skipped",
                              "size_kb", "duration_ms"]
    assert res.data["triangles"] == 372
    res, _ = invoke(["--out", "b.glb", "--cameras"], triangles=12, cameras=["Cam"])
    assert list(res.data)[3:6] == ["objects", "triangles", "cameras"] and res.data["cameras"] == "1 (Cam)"
    res, _ = invoke(["--out", "c.glb"], triangles=12)
    assert "cameras" not in res.data


@pytest.mark.parametrize("out", ["x.fbx", "x.obj", "x.usd", "x.abc", "x.stl", "x.ply"])
def test_cameras_applies_to_glb_and_gltf_only(work, out):
    with pytest.raises(AxiError) as e:
        invoke(["--out", out, "--cameras"])
    assert e.value.exit_code == 2 and "--cameras applies to glb and gltf" in e.value.message


def test_cameras_param_and_replay_hint(work):
    _, t = invoke(["--out", "a.glb"])
    assert t.calls[0][1]["cameras"] is False
    _, t = invoke(["--out", "a.gltf", "--cameras"])
    assert t.calls[0][1]["cameras"] is True
    res, t = invoke(["--out", "a.glb", "--cameras", "--dry-run"])
    assert t.calls[0][1]["cameras"] is True
    assert res.help[0] == "Run `blender-axi export --out a.glb --cameras` to write it"


def test_cameras_flag_in_help():
    assert any(f.name == "--cameras" for f in cmd.SPEC.flags)


# --- op with fake bpy -----------------------------------------------------------------------------------

class Obj:
    def __init__(self, name, type="MESH", polygons=((0, 1, 2),)):
        self.name, self.type = name, type
        self.hide_viewport = False
        self.selected = False
        self.data = types.SimpleNamespace(polygons=[types.SimpleNamespace(vertices=p) for p in polygons])

    def visible_get(self):
        return True

    def hide_get(self):
        return False

    def hide_set(self, state):
        pass

    def select_set(self, state):
        self.selected = state

    def select_get(self):
        return self.selected

    def evaluated_get(self, depsgraph):
        return self

    def to_mesh(self):
        return self.data

    def to_mesh_clear(self):
        pass


@pytest.fixture
def scene(fake_bpy):
    quad, tri = ((0, 1, 2, 3),), ((0, 1, 2),)
    objs = [Obj("Ring", polygons=quad * 3), Obj("Floor", polygons=tri), Obj("Grp", "EMPTY"), Obj("Cam", "CAMERA"),
            Obj("Lamp", "LIGHT")]
    ctx = fake_bpy.context
    ctx.scene = types.SimpleNamespace(objects=objs)
    ctx.view_layer = types.SimpleNamespace(objects=types.SimpleNamespace(active=None))
    ctx.window, ctx.temp_override = "win", lambda **kw: contextlib.nullcontext()
    depsgraph_calls = []
    ctx.evaluated_depsgraph_get = lambda: depsgraph_calls.append(1) or "depsgraph"
    fake_bpy.data.objects = types.SimpleNamespace(get={o.name: o for o in objs}.get)
    fake_bpy.data.collections = types.SimpleNamespace(get={}.get)
    calls = []

    def gltf(**kw):
        calls.append(kw)
        Path(kw["filepath"]).write_bytes(b"x" * 100)

    fake_bpy.ops.export_scene = types.SimpleNamespace(gltf=gltf, fbx=gltf)
    fake_bpy.ops.wm = types.SimpleNamespace(obj_export=gltf)
    fake_bpy.calls, fake_bpy.objs, fake_bpy.depsgraph_calls = calls, objs, depsgraph_calls
    return fake_bpy


def out_path(name):
    path = WORK / "op" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    return str(path)


def test_gltf_call_gets_export_cameras_only_when_asked(scene):
    op_export.run({"out": out_path("a.glb"), "format": "glb"})
    assert scene.calls[-1]["export_cameras"] is False
    res = op_export.run({"out": out_path("b.glb"), "format": "glb", "cameras": True})
    assert scene.calls[-1]["export_cameras"] is True and res["cameras"] == ["Cam"]
    res = op_export.run({"out": out_path("c.glb"), "format": "glb"})
    assert "cameras" not in res


def test_also_lists_empties_always_and_cameras_only_with_the_flag(scene):
    res = op_export.run({"out": out_path("d.glb"), "format": "glb", "dry_run": True})
    assert res["also"] == [{"name": "Grp", "type": "EMPTY"}]
    res = op_export.run({"out": out_path("d.glb"), "format": "glb", "dry_run": True, "cameras": True})
    assert res["also"] == [{"name": "Grp", "type": "EMPTY"}, {"name": "Cam", "type": "CAMERA"}]
    res = op_export.run({"out": out_path("d.obj"), "format": "obj", "dry_run": True})
    assert res["also"] == []  # obj writes no Empty


def test_selected_cameras_are_reported_from_the_selection(scene):
    res = op_export.run({"out": out_path("e.glb"), "format": "glb", "objects": ["Ring", "Cam"], "cameras": True})
    assert res["cameras"] == ["Cam"] and scene.calls[-1]["use_selection"] is True
    res = op_export.run({"out": out_path("f.glb"), "format": "glb", "objects": ["Ring"], "cameras": True})
    assert res["cameras"] == []


def test_triangles_sum_the_geometry_polygons(scene):
    res = op_export.run({"out": out_path("g.glb"), "format": "glb", "dry_run": True})
    assert res["triangles"] == 3 * 2 + 1  # three quads (2 each) + one triangle
    res = op_export.run({"out": out_path("g.glb"), "format": "glb", "force": True})
    assert res["triangles"] == 7
    assert len(scene.depsgraph_calls) == 2


def test_raw_mesh_count_skips_the_depsgraph(scene):
    res = op_export.run({"out": out_path("h.glb"), "format": "glb", "dry_run": True, "apply_modifiers": False})
    assert res["triangles"] == 7 and scene.depsgraph_calls == []
    res = op_export.run({"out": out_path("h.usda"), "format": "usda", "dry_run": True, "apply_modifiers": False})
    assert res["triangles"] == 7 and scene.depsgraph_calls == [1]  # usd always exports the evaluated scene
