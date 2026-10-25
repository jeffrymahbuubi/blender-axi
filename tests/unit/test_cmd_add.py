"""v1.2 U1: `add` (command level with a fake transport) and `op_add` (fake `bpy` plus a stub `bmesh`)."""

import math
import shutil
import sys
import types
from pathlib import Path

import pytest

from blender_axi import flags as flagparser
from blender_axi.blender_side import common, op_add
from blender_axi.blender_side.ops import OpError
from blender_axi.commands import add as cmd
from blender_axi.errors import AxiError
from blender_axi.runner import RunOutcome, SaveSpec

WORK = Path(__file__).resolve().parents[2] / ".tmp" / "tests" / "unit_cmd_add"
LIVE = ["--live", "--host", "127.0.0.1", "--port", "9999"]
MESH_RESULT = {"name": "Box", "type": "MESH", "kind": "cube", "collection": "Collection", "location": [0, 0, 1],
               "dimensions": [2, 2, 2], "verts": 8, "faces": 6, "scene_camera_set": False, "scene_camera": None}
CAMERA_RESULT = {**MESH_RESULT, "name": "Cam", "type": "CAMERA", "kind": "camera", "verts": None, "faces": None,
                 "scene_camera_set": True, "scene_camera": "Cam"}


class Fake:
    name = "fake"

    def __init__(self, result=None, file="C:\\work\\open.blend", **outcome_kw):
        self.result = dict(MESH_RESULT if result is None else result)
        self.file, self.calls, self.kw = file, [], outcome_kw

    def run(self, op, params, *, blend=None, save=SaveSpec("none"), timeout=None):
        self.calls.append({"op": op, "params": params, "blend": blend, "save": save, "timeout": timeout})
        if op == "ping":
            return RunOutcome({"file": self.file}, False, None, "5.2", 1, "")
        kw = {"modified": True, "saved": save.path if save.mode != "none" else None, **self.kw}
        return RunOutcome(self.result, kw.pop("modified"), kw.pop("saved"), "5.2.0 LTS", 33, "", **kw)

    @property
    def ops(self):
        return [c["op"] for c in self.calls]


@pytest.fixture
def work(monkeypatch):
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    (WORK / "scene.blend").write_bytes(b"BLEND")
    monkeypatch.chdir(WORK)
    return WORK


def invoke(argv, transport=None):
    inv, wants_help = flagparser.parse(cmd.SPEC, argv)
    assert not wants_help
    inv.transport = transport or Fake()
    return cmd.run(inv), inv.transport


def fails(argv, transport=None):
    with pytest.raises(AxiError) as exc:
        invoke(argv, transport)
    return exc.value, (transport or None)


# -- spec -------------------------------------------------------------------------------------------------------------

def test_spec_shape():
    assert cmd.SPEC.name == "add"
    assert cmd.SPEC.summary == "Add a primitive, camera, light or empty to a .blend (or the open session with --live)"
    assert [p.name for p in cmd.SPEC.positionals] == ["kind", "file"] and cmd.SPEC.positionals[1].required is False
    names = [f.name for f in cmd.SPEC.flags]
    assert names == ["--name", "--location", "--rotation", "--scale", "--size", "--radius", "--depth", "--segments",
                     "--collection", "--type", "--energy", "--in-place", "--out", "--force"]
    assert next(f for f in cmd.SPEC.flags if f.name == "--type").choices == ("POINT", "SUN", "SPOT", "AREA")
    assert op_add.KINDS == ("cube", "plane", "sphere", "icosphere", "cylinder", "cone", "monkey", "camera", "light", "empty")


# -- validation -------------------------------------------------------------------------------------------------------

def test_unknown_kind_lists_the_kinds(work):
    t = Fake()
    err, _ = fails(["torus", "scene.blend", "--in-place"], t)
    assert err.code == "VALIDATION_ERROR" and err.exit_code == 2 and t.calls == []
    assert "unknown kind 'torus'" in err.message and "cube, plane, sphere" in err.message and "monkey" in err.message


def test_kind_is_case_insensitive(work):
    _, t = invoke(["CUBE", "scene.blend", "--in-place"])
    assert t.calls[0]["params"]["kind"] == "cube"


@pytest.mark.parametrize("kind,flag,value,message", [
    ("cylinder", "--size", "1", "--size applies to cube and plane"),
    ("sphere", "--depth", "1", "--depth applies to cylinder and cone"),
    ("cube", "--depth", "1", "--depth applies to cylinder and cone"),
    ("cube", "--radius", "1", "--radius applies to sphere, icosphere, cylinder and cone"),
    ("plane", "--segments", "8", "--segments applies to sphere, icosphere, cylinder and cone"),
    ("cube", "--type", "SUN", "--type applies to light"),
    ("camera", "--energy", "3", "--energy applies to light"),
])
def test_per_kind_flag_restrictions(work, kind, flag, value, message):
    t = Fake()
    err, _ = fails([kind, "scene.blend", flag, value, "--in-place"], t)
    assert err.exit_code == 2 and err.message == message and t.calls == []
    assert err.help[0].startswith(f"Flags for {kind}: ") and "--name" in err.help[0]


@pytest.mark.parametrize("kind,flag,value", [
    ("cube", "--size", "3"), ("plane", "--size", "3"), ("sphere", "--radius", "2"), ("icosphere", "--segments", "3"),
    ("cylinder", "--depth", "2"), ("cone", "--depth", "2"), ("cone", "--segments", "8"), ("light", "--type", "SPOT"),
    ("light", "--energy", "500"), ("empty", "--scale", "2,2,2")])
def test_allowed_combinations(work, kind, flag, value):
    _, t = invoke([kind, "scene.blend", flag, value, "--in-place"])
    assert t.ops == ["add"]


@pytest.mark.parametrize("argv", [
    ["cube", "--size", "0"], ["cube", "--size", "-1"], ["sphere", "--radius", "0"], ["cone", "--depth", "-2"],
    ["sphere", "--segments", "2"], ["sphere", "--segments", "513"], ["icosphere", "--segments", "7"],
    ["icosphere", "--segments", "0"], ["light", "--energy", "-1"], ["cube", "--name", " "],
    ["cube", "--location", "1,2"], ["cube", "--rotation", "a,b,c"], ["cube", "--scale", "1,1,1,1"],
])
def test_value_errors_exit_2_without_requests(work, argv):
    t = Fake()
    err, _ = fails([*argv, "scene.blend", "--in-place"], t)
    assert err.code == "VALIDATION_ERROR" and err.exit_code == 2 and t.calls == []


def test_bad_light_type_is_rejected_by_the_parser():
    with pytest.raises(AxiError) as exc:
        flagparser.parse(cmd.SPEC, ["light", "x.blend", "--type", "LASER"])
    assert exc.value.exit_code == 2 and "POINT" in exc.value.message


def test_light_type_is_case_insensitive(work):
    _, t = invoke(["light", "scene.blend", "--type", "sun", "--in-place"])
    assert t.calls[0]["params"]["light_type"] == "SUN"


# -- params and result ------------------------------------------------------------------------------------------------

def test_params_shape_defaults_to_none(work):
    _, t = invoke(["cube", "scene.blend", "--in-place"])
    call = t.calls[0]
    assert call["op"] == "add" and call["params"] == {
        "kind": "cube", "name": None, "location": None, "rotation_deg": None, "scale": None, "size": None,
        "radius": None, "depth": None, "segments": None, "collection": None, "light_type": None, "energy": None}
    assert call["blend"] == (WORK / "scene.blend").resolve() and call["save"] == SaveSpec("in_place", str(call["blend"]))


def test_params_passthrough_with_degrees(work):
    _, t = invoke(["cylinder", "scene.blend", "--name", "Pipe", "--location", "1,2,3", "--rotation", "0,0,90",
                   "--scale", "1,1,2", "--radius", "0.5", "--depth", "3", "--segments", "16", "--collection", "Props",
                   "--out", "o.blend"])
    assert t.calls[0]["params"] == {
        "kind": "cylinder", "name": "Pipe", "location": [1.0, 2.0, 3.0], "rotation_deg": [0.0, 0.0, 90.0],
        "scale": [1.0, 1.0, 2.0], "size": None, "radius": 0.5, "depth": 3.0, "segments": 16, "collection": "Props",
        "light_type": None, "energy": None}
    assert t.calls[0]["save"] == SaveSpec("copy", str((WORK / "o.blend").resolve()))


def test_light_params(work):
    _, t = invoke(["light", "scene.blend", "--type", "SUN", "--energy", "3", "--in-place"])
    assert t.calls[0]["params"]["light_type"] == "SUN" and t.calls[0]["params"]["energy"] == 3.0


def test_data_order_mesh_and_hints_headless(work):
    res, _ = invoke(["cube", "scene.blend", "--name", "Box", "--in-place"])
    assert list(res.data) == ["file", "added", "type", "collection", "location", "dimensions", "verts", "faces",
                              "modified", "saved", "duration_ms"]
    assert res.data["file"] == "scene.blend" and res.data["added"] == "Box" and res.data["type"] == "MESH"
    assert res.data["location"] == "0,0,1" and res.data["dimensions"] == "2,2,2"
    assert res.data["verts"] == 8 and res.data["faces"] == 6 and res.data["modified"] is True
    assert res.help == [
        "Run `blender-axi objects scene.blend` to list the objects",
        "Run `blender-axi set Box scene.blend --location x,y,z --in-place` to move it",
        "Run `blender-axi material Box scene.blend --name <mat> --color r,g,b --in-place` to give it a material"]


def test_non_mesh_has_no_verts_and_no_material_hint(work):
    res, _ = invoke(["light", "scene.blend", "--in-place"], Fake(
        {"name": "Point", "type": "LIGHT", "kind": "light", "collection": "Collection", "location": [0, 0, 0],
         "dimensions": [0, 0, 0], "verts": None, "faces": None}))
    assert "verts" not in res.data and "faces" not in res.data
    assert not any("material" in h for h in res.help) and any(" set Point " in h for h in res.help)


def test_camera_that_became_the_scene_camera(work):
    res, _ = invoke(["camera", "scene.blend", "--in-place"], Fake(CAMERA_RESULT))
    assert list(res.data) == ["file", "added", "type", "collection", "location", "dimensions", "scene_camera",
                              "modified", "saved", "duration_ms"]
    assert res.data["scene_camera"] == "set" and not any("material" in h for h in res.help)
    assert res.help[-1] == "Run `blender-axi render scene.blend --engine WORKBENCH --out <path.png>` to render through it"


def test_camera_hint_follows_out_and_live(work):
    res, _ = invoke(["camera", "scene.blend", "--out", "copy.blend"], Fake(CAMERA_RESULT))
    assert res.help[-1].startswith("Run `blender-axi render copy.blend --engine WORKBENCH")
    res, _ = invoke(["camera", *LIVE], Fake(CAMERA_RESULT))
    assert any(h.startswith("Run `blender-axi render --engine WORKBENCH --out <path.png> --live --host 127.0.0.1 "
                            "--port 9999` to render through it") for h in res.help)


def test_camera_that_left_the_scene_camera_alone(work):
    res, _ = invoke(["camera", "scene.blend", "--in-place"],
                    Fake({**CAMERA_RESULT, "scene_camera_set": False, "scene_camera": "Camera"}))
    assert res.data["scene_camera"] == "unchanged (Camera)" and not any("render" in h for h in res.help)


def test_non_camera_results_have_no_scene_camera_field(work):
    res, _ = invoke(["cube", "scene.blend", "--in-place"])
    assert "scene_camera" not in res.data and not any("render" in h for h in res.help)


def test_hint_names_with_spaces_are_quoted(work):
    res, _ = invoke(["cube", "scene.blend", "--in-place"], Fake({**MESH_RESULT, "name": "My Box"}))
    assert 'blender-axi set "My Box" scene.blend' in res.help[1]


def test_out_hints_use_the_written_copy(work):
    res, _ = invoke(["cube", "scene.blend", "--out", "copy.blend"])
    assert res.data["saved"] == "copy.blend" and res.help[0] == "Run `blender-axi objects copy.blend` to list the objects"
    assert res.help[1] == "Run `blender-axi set Box copy.blend --location x,y,z --in-place` to move it"


def test_hints_carry_user_prefs(work):
    res, _ = invoke(["cube", "scene.blend", "--in-place", "--user-prefs"])
    assert all(h.split("`")[1].endswith("--user-prefs") for h in res.help)


def test_neither_flag_exit_2_with_both_commands(work):
    t = Fake()
    err, _ = fails(["cube", "scene.blend", "--name", "Box"], t)
    assert err.exit_code == 2 and t.calls == []
    assert err.help[0] == "Run `blender-axi add cube scene.blend --name Box --in-place` to write the file (a .blend1 backup is kept)"
    assert err.help[1] == "Run `blender-axi add cube scene.blend --name Box --out <copy.blend>` to keep the original and write a copy"


def test_collection_not_found_gets_an_actionable_hint(work):
    class Missing(Fake):
        def run(self, op, params, **kw):
            raise AxiError("NOT_FOUND", "collection 'X' not found (available: Props)", help=["generic"])
    err, _ = fails(["cube", "scene.blend", "--collection", "X", "--in-place"], Missing())
    assert err.code == "NOT_FOUND" and err.help == ["Run `blender-axi scene scene.blend` to see the collections"]


# -- live -------------------------------------------------------------------------------------------------------------

def test_live_unsaved_change():
    res, t = invoke(["cube", *LIVE, "--name", "Box"], Fake(undo="unavailable", saved=None))
    assert t.ops == ["ping", "add"] and t.calls[1]["blend"] is None and t.calls[1]["save"] == SaveSpec("none")
    d = res.data
    assert d["file"] == "C:/work/open.blend" and d["modified"] is True
    assert d["saved"] == "none (live session, unsaved)" and d["undo"] == "unavailable (headless server)"
    assert res.help[0] == "Run `blender-axi objects --live --host 127.0.0.1 --port 9999` to list the objects"
    assert res.help[1] == "Run `blender-axi set Box --location x,y,z --live --host 127.0.0.1 --port 9999` to move it"
    assert res.help[-1].startswith("Changes are only in the open session")


def test_live_in_place_and_refusals():
    res, t = invoke(["cube", *LIVE, "--in-place"], Fake(undo="pushed"))
    assert t.calls[1]["save"] == SaveSpec("in_place", "C:\\work\\open.blend") and res.data["saved"].endswith("open.blend")
    assert res.data["undo"] == "pushed (Ctrl+Z in Blender reverts)"
    assert not any(h.startswith("Changes are only") for h in res.help)
    for argv in (["cube", "x.blend", *LIVE], ["cube", *LIVE, "--out", "o.blend"]):
        t = Fake()
        err, _ = fails(argv, t)
        assert err.exit_code == 2 and t.calls == []


def test_live_unmodified_has_no_unsaved_hint():
    res, _ = invoke(["cube", *LIVE], Fake(modified=False))
    assert res.data["saved"] == "none (no changes)" and not any(h.startswith("Changes are only") for h in res.help)


# -- op_add (fake bpy + stub bmesh) -----------------------------------------------------------------------------------

class FakeColl:
    def __init__(self, name):
        self.name = name
        self.objects = types.SimpleNamespace(linked=[], link=lambda o: self.objects.linked.append(o))


class Registry(list):
    """bpy.data.<collection> with `.new(...)` recording what was created."""

    def __init__(self, make):
        super().__init__()
        self.make = make

    def new(self, *args, **kwargs):
        item = self.make(*args, **kwargs)
        self.append(item)
        return item


class StubBmesh(types.ModuleType):
    def __init__(self, calls):
        super().__init__("bmesh")
        self.calls = calls
        self.freed = []
        outer = self

        class Bm:
            def to_mesh(self, mesh):
                mesh.vertices, mesh.polygons = list(range(8)), list(range(6))

            def free(self):
                outer.freed.append(True)

        self.new = Bm
        self.ops = types.SimpleNamespace(**{
            n: (lambda name: lambda bm, **kw: calls.append((name, kw)))(n)
            for n in ("create_cube", "create_grid", "create_uvsphere", "create_icosphere", "create_cone", "create_monkey")})


@pytest.fixture
def world(fake_bpy, monkeypatch):
    scene_coll, props = FakeColl("Scene Collection"), FakeColl("Props")
    active = FakeColl("Active")
    calls = []
    monkeypatch.setitem(sys.modules, "bmesh", StubBmesh(calls))

    def new_object(name, data):
        kind = ("MESH" if hasattr(data, "vertices") else "CAMERA" if getattr(data, "is_camera", False)
                else "LIGHT" if hasattr(data, "energy") else "EMPTY")
        return types.SimpleNamespace(name=name, type=kind, data=data, location=(0.0, 0.0, 0.0),
                                     rotation_euler=(0.0, 0.0, 0.0), scale=(1.0, 1.0, 1.0), dimensions=(2.0, 2.0, 2.0))

    fake_bpy.data = types.SimpleNamespace(
        meshes=Registry(lambda name: types.SimpleNamespace(name=name, vertices=[], polygons=[])),
        cameras=Registry(lambda name: types.SimpleNamespace(name=name, is_camera=True)),
        lights=Registry(lambda name, type: types.SimpleNamespace(name=name, type=type, energy=1000.0)),
        objects=Registry(new_object), collections=[props])
    fake_bpy.context = types.SimpleNamespace(
        scene=types.SimpleNamespace(collection=scene_coll, camera=None), collection=active,
        view_layer=types.SimpleNamespace(update=lambda: calls.append(("view_layer.update", {}))))
    return types.SimpleNamespace(bpy=fake_bpy, calls=calls, scene=scene_coll, props=props, active=active,
                                 bmesh=sys.modules["bmesh"])


def add_op(**params):
    base = {k: None for k in ("name", "location", "rotation_deg", "scale", "size", "radius", "depth", "segments",
                              "collection", "light_type", "energy")}
    return op_add.run({**base, **params})


@pytest.mark.parametrize("kind,expected_op,kwargs,name", [
    ("cube", "create_cube", {"size": 2.0}, "Cube"),
    ("plane", "create_grid", {"x_segments": 1, "y_segments": 1, "size": 1.0}, "Plane"),
    ("sphere", "create_uvsphere", {"u_segments": 32, "v_segments": 16, "radius": 1.0}, "Sphere"),
    ("icosphere", "create_icosphere", {"subdivisions": 2, "radius": 1.0}, "Icosphere"),
    ("cylinder", "create_cone", {"cap_ends": True, "cap_tris": False, "segments": 32, "radius1": 1.0, "radius2": 1.0,
                                 "depth": 2.0}, "Cylinder"),
    ("cone", "create_cone", {"cap_ends": True, "cap_tris": False, "segments": 32, "radius1": 1.0, "radius2": 0.0,
                             "depth": 2.0}, "Cone"),
    ("monkey", "create_monkey", {}, "Suzanne"),
])
def test_op_kind_to_bmesh_op_and_defaults(world, kind, expected_op, kwargs, name):
    r = add_op(kind=kind)
    assert world.calls[0] == (expected_op, kwargs)
    assert r["name"] == name and r["type"] == "MESH" and r["kind"] == kind and r["verts"] == 8 and r["faces"] == 6
    assert world.bmesh.freed == [True] and world.bpy.data.meshes[0].name == name
    assert world.active.objects.linked[0].name == name


def test_op_params_reach_bmesh(world):
    add_op(kind="plane", size=6)
    add_op(kind="cube", size=3)
    add_op(kind="sphere", radius=0.5, segments=8)
    add_op(kind="icosphere", radius=2, segments=3)
    add_op(kind="cylinder", radius=0.5, depth=3, segments=12)
    add_op(kind="cone", radius=0.25, depth=1)
    c = {n: kw for n, kw in world.calls if n != "view_layer.update"}
    assert c["create_grid"]["size"] == 3.0 and c["create_cube"]["size"] == 3.0
    assert c["create_uvsphere"] == {"u_segments": 8, "v_segments": 4, "radius": 0.5}
    assert c["create_icosphere"] == {"subdivisions": 3, "radius": 2.0}
    cones = [kw for n, kw in world.calls if n == "create_cone"]
    assert cones[0]["segments"] == 12 and cones[0]["radius2"] == 0.5 and cones[0]["depth"] == 3.0
    assert cones[1]["radius1"] == 0.25 and cones[1]["radius2"] == 0.0 and cones[1]["depth"] == 1.0


def test_op_small_uv_sphere_keeps_two_rings(world):
    add_op(kind="sphere", segments=3)
    assert world.calls[0][1]["v_segments"] == 2


def test_op_transform_name_and_view_layer_update(world):
    r = add_op(kind="cube", name="Box", location=[1, 2, 3], rotation_deg=[0, 0, 90], scale=[2, 2, 2])
    obj = world.active.objects.linked[0]
    assert obj.location == (1.0, 2.0, 3.0) and obj.scale == (2.0, 2.0, 2.0)
    assert obj.rotation_euler == pytest.approx((0.0, 0.0, math.pi / 2))
    assert r["name"] == "Box" and r["location"] == [1.0, 2.0, 3.0] and r["dimensions"] == [2.0, 2.0, 2.0]
    assert world.calls[-1][0] == "view_layer.update"  # dimensions are read after the depsgraph ran


def test_op_camera_light_empty_use_the_data_api(world):
    cam = add_op(kind="camera")
    assert cam["type"] == "CAMERA" and cam["name"] == "Camera" and cam["verts"] is None and cam["faces"] is None
    assert world.bpy.data.cameras[0].name == "Camera"
    sun = add_op(kind="light", light_type="SUN", energy=3)
    assert sun["type"] == "LIGHT" and sun["name"] == "Sun"
    assert (world.bpy.data.lights[0].type, world.bpy.data.lights[0].energy) == ("SUN", 3.0)
    default = add_op(kind="light")
    assert default["name"] == "Point" and world.bpy.data.lights[1].type == "POINT" and world.bpy.data.lights[1].energy == 1000.0
    empty = add_op(kind="empty", name="Anchor")
    assert empty["type"] == "EMPTY" and empty["name"] == "Anchor"
    assert world.calls == [("view_layer.update", {})] * 4  # no bmesh op for any of them


def test_op_first_camera_becomes_the_scene_camera_a_second_does_not(world):
    scene = world.bpy.context.scene
    first = add_op(kind="camera")
    assert first["scene_camera_set"] is True and first["scene_camera"] == "Camera"
    assert scene.camera is world.active.objects.linked[0]
    second = add_op(kind="camera", name="Cam2")
    assert second["scene_camera_set"] is False and second["scene_camera"] == "Camera"
    assert scene.camera is world.active.objects.linked[0]


def test_op_non_cameras_never_become_the_scene_camera(world):
    for kind in ("cube", "light", "empty"):
        r = add_op(kind=kind)
        assert r["scene_camera_set"] is False and r["scene_camera"] is None
    assert world.bpy.context.scene.camera is None


def test_op_collection_helper_moved_to_common(world):
    assert not hasattr(op_add, "_collection") and not hasattr(op_add, "SCENE_COLLECTION")
    assert op_add.find_collection is common.find_collection


def test_op_collection_lookup(world):
    assert add_op(kind="cube")["collection"] == "Active"  # bpy.context.collection
    assert add_op(kind="cube", collection="Props")["collection"] == "Props"
    assert add_op(kind="cube", collection="Scene Collection")["collection"] == "Scene Collection"
    assert [o.name for o in world.props.objects.linked] == ["Cube"]
    assert len(world.scene.objects.linked) == 1


def test_op_falls_back_to_the_scene_collection(world):
    world.bpy.context.collection = None
    assert add_op(kind="cube")["collection"] == "Scene Collection"
    del world.bpy.context.collection  # attribute missing altogether
    assert add_op(kind="cube")["collection"] == "Scene Collection"


def test_op_missing_collection_is_not_found_and_creates_nothing(world):
    with pytest.raises(OpError) as exc:
        add_op(kind="cube", collection="Nope")
    assert exc.value.code == "NOT_FOUND" and "'Nope' not found" in exc.value.message and "Props" in exc.value.message
    assert world.bpy.data.meshes == [] and world.bpy.data.objects == []
    world.bpy.data.collections = []
    with pytest.raises(OpError) as exc:
        add_op(kind="cube", collection="Nope")
    assert "the file has no collections" in exc.value.message


def test_op_unknown_kind_or_light_type(world):
    with pytest.raises(OpError) as exc:
        add_op(kind="torus")
    assert exc.value.code == "OP_FAILED"
    with pytest.raises(OpError):
        add_op(kind="light", light_type="LASER")
