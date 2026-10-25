"""v1.1 U1: frame-neutral change detection (bootstrap), the session marker (session) and the RunOutcome fields.

Everything runs against a stub `bpy`; the real thing is covered by tests/integration/test_exec_blender.py and
test_live_optional.py.
"""

import json
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.mock_live_server import MockLiveServer  # noqa: E402

from blender_axi import live, paths, runner  # noqa: E402
from blender_axi.errors import AxiError  # noqa: E402
from blender_axi.blender_side import bootstrap, session
from blender_axi.locate import BlenderInfo
from blender_axi.runner import HeadlessTransport, RunOutcome, SaveSpec

INFO = BlenderInfo(Path("C:/fake/blender.exe"), (5, 2, 0), "5.2.0 LTS")


class FakeID:
    def __init__(self, name):
        self.name = name
        self.bl_rna = SimpleNamespace(properties=[])


class FakeColl(list):
    rna_type = object()

    def keys(self):
        return [i.name for i in self]


class FakeScene:
    def __init__(self, frame=1):
        self.frame_current = frame
        self.set_calls = []

    def frame_set(self, frame):
        self.set_calls.append(frame)
        self.frame_current = frame


class WM(dict):
    """Window-manager stand-in: ID properties behave like a dict."""


@pytest.fixture
def world(fake_bpy):
    """fake bpy whose fingerprint reacts to ID names, with a scene and a window manager."""
    fake_bpy.types = SimpleNamespace(ID=FakeID)
    fake_bpy.data.objects = FakeColl([FakeID("Cube"), FakeID("Ball")])
    fake_bpy.context.scene = FakeScene()
    fake_bpy.context.window_manager = WM()
    fake_bpy.data.filepath = ""
    return fake_bpy


# -- bootstrap.baseline / modified_since ---------------------------------------------------------

def test_fingerprint_of_stub_changes_with_names(world):
    before = bootstrap.fingerprint(world)
    world.data.objects[0].name = "Renamed"
    assert bootstrap.fingerprint(world) != before


def test_fingerprint_sees_modifier_settings(world):
    """F2 (v1.3 P4): modifier structs are walked, so a width edit alone changes the fingerprint."""
    prop = SimpleNamespace
    bevel = SimpleNamespace(name="Bevel", width=0.1, bl_rna=SimpleNamespace(properties=[prop(identifier="width", type="FLOAT")]))
    cube = world.data.objects[0]
    cube.modifiers = [bevel]
    cube.bl_rna = SimpleNamespace(properties=[prop(identifier="modifiers", type="COLLECTION")])
    before = bootstrap.fingerprint(world)
    bevel.width = 0.05
    assert bootstrap.fingerprint(world) != before


def test_frame_moved_alone_is_not_modified(world):
    base = bootstrap.baseline(world)
    assert base.frame == 1
    world.context.scene.frame_set(10)
    world.context.scene.set_calls.clear()
    assert bootstrap.modified_since(world, base) == (False, "1 -> 10")
    assert world.context.scene.set_calls == [1, 10]  # compared at the original frame, then put back
    assert world.context.scene.frame_current == 10


def test_rename_is_modified_without_frame_note(world):
    base = bootstrap.baseline(world)
    world.data.objects[0].name = "Renamed"
    assert bootstrap.modified_since(world, base) == (True, None)
    assert world.context.scene.set_calls == []  # frame did not move: no frame_set at all


def test_rename_and_frame_move_report_both(world):
    base = bootstrap.baseline(world)
    world.context.scene.frame_set(10)
    world.data.objects[1].name = "Other"
    assert bootstrap.modified_since(world, base) == (True, "1 -> 10")
    assert world.context.scene.frame_current == 10


def test_no_scene_still_compares(world):
    world.context.scene = None
    base = bootstrap.baseline(world)
    assert base.frame is None
    assert bootstrap.modified_since(world, base) == (False, None)
    world.data.objects[0].name = "Renamed"
    assert bootstrap.modified_since(world, base) == (True, None)


def test_main_reports_frame_changed_and_not_modified(world):
    rd = paths.run_dir("unit-bootstrap")
    try:
        response = _run_main(rd, "bpy.context.scene.frame_set(10)\nresult = {'f': bpy.context.scene.frame_current}",
                             SaveSpec("copy", str(rd / "copy.blend")))
        assert response["ok"] is True and response["modified"] is False and response["saved"] is None
        assert response["frame_changed"] == "1 -> 10" and response["result"]["result"] == {"f": 10}
        assert not (rd / "copy.blend").exists()
    finally:
        paths.cleanup(rd, False)


def test_main_real_change_saves_and_has_no_frame_note(world):
    saved = []
    world.ops.wm = SimpleNamespace(save_as_mainfile=lambda **kw: saved.append(kw), save_mainfile=lambda: None)
    rd = paths.run_dir("unit-bootstrap")
    try:
        target = str(rd / "copy.blend")
        response = _run_main(rd, "bpy.data.objects[0].name = 'Renamed'\nresult = {'ok': True}", SaveSpec("copy", target))
        assert response["modified"] is True and response["saved"] == target and response["frame_changed"] is None
        assert saved == [{"filepath": target, "copy": True}]
    finally:
        paths.cleanup(rd, False)


def test_main_without_save_mode_does_not_track_frames(world):
    rd = paths.run_dir("unit-bootstrap")
    try:
        response = _run_main(rd, "bpy.context.scene.frame_set(10)\nresult = {}", SaveSpec("none"))
        assert response["ok"] is True and response["frame_changed"] is None and response["modified"] is False
    finally:
        paths.cleanup(rd, False)


def _run_main(rd, code, save):
    request = {"protocol": 1, "op": "exec", "params": {"code": code}, "response_path": str(rd / "response.json"),
               "save": {"mode": save.mode, "path": save.path}}
    (rd / "request.json").write_text(json.dumps(request), encoding="utf-8")
    assert bootstrap.main(["--request", str(rd / "request.json")]) == 0
    return json.loads((rd / "response.json").read_text(encoding="utf-8"))


# -- session --------------------------------------------------------------------------------------

@pytest.fixture
def saved_file():
    d = paths.run_dir("unit-session")
    f = d / "s.blend"
    f.write_bytes(b"BLEND")
    yield f
    paths.cleanup(d, False)


def test_marker_valid_for_same_file_and_mtime(world, saved_file):
    world.data.filepath = str(saved_file)
    assert session.unsaved_changes(world) is False
    session.mark_modified(world)
    assert session.MARKER in world.context.window_manager
    assert session.unsaved_changes(world) is True


def test_marker_expires_when_the_file_is_saved(world, saved_file):
    world.data.filepath = str(saved_file)
    session.mark_modified(world)
    stat = saved_file.stat()
    os.utime(saved_file, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))  # a save by someone else
    assert session.unsaved_changes(world) is False


def test_marker_invalid_for_another_file(world, saved_file):
    world.data.filepath = str(saved_file)
    session.mark_modified(world)
    world.data.filepath = str(saved_file.with_name("other.blend"))
    assert session.unsaved_changes(world) is False


def test_marker_valid_for_an_unsaved_session(world):
    world.data.filepath = ""
    session.mark_modified(world)
    assert session.unsaved_changes(world) is True


def test_clear_marker(world):
    session.mark_modified(world)
    session.clear_marker(world)
    assert session.MARKER not in world.context.window_manager and session.unsaved_changes(world) is False
    session.clear_marker(world)  # idempotent


def test_unsaved_changes_true_when_dirty(world):
    world.data.is_dirty = True
    assert session.unsaved_changes(world) is True


def test_session_tolerates_a_bpy_without_window_manager(fake_bpy):
    assert not hasattr(fake_bpy.context, "window_manager")
    session.mark_modified(fake_bpy)
    session.clear_marker(fake_bpy)
    assert session.unsaved_changes(fake_bpy) is False
    fake_bpy.data.is_dirty = True
    assert session.unsaved_changes(fake_bpy) is True


def test_undo_push(world):
    calls = []
    world.ops.ed = types.SimpleNamespace(undo_push=lambda message: calls.append(message))
    assert session.undo_push(world, "blender-axi exec") is True and calls == ["blender-axi exec"]

    def refuse(message):
        raise RuntimeError("Operator bpy.ops.ed.undo_push.poll() failed, context is incorrect")

    world.ops.ed = types.SimpleNamespace(undo_push=refuse)
    assert session.undo_push(world, "x") is False


def test_scene_summary_reports_the_marker_as_unsaved_changes(world, saved_file):
    from blender_axi.blender_side import op_scene_summary

    world.data.filepath = str(saved_file)
    world.data.materials = []
    scene = world.context.scene
    scene.name, scene.objects, scene.camera = "Scene", [], None
    scene.collection = SimpleNamespace(children_recursive=[])
    scene.render = SimpleNamespace(engine="X", resolution_x=1, resolution_y=1, resolution_percentage=100, fps=24,
                                   fps_base=1.0)
    scene.frame_start = scene.frame_end = 1
    assert op_scene_summary.run({})["is_dirty"] is False
    session.mark_modified(world)
    assert op_scene_summary.run({})["is_dirty"] is True


# -- RunOutcome / HeadlessTransport -----------------------------------------------------------------

def test_run_outcome_defaults():
    out = RunOutcome({}, False, None, "5.2", 1, "")
    assert out.frame_changed is None and out.undo is None


def test_headless_transport_passes_frame_changed(monkeypatch):
    def fake_run(argv, **kw):
        request = json.loads(Path(argv[argv.index("--request") + 1]).read_text())
        Path(request["response_path"]).write_text(json.dumps({
            "protocol": 1, "ok": True, "result": {}, "modified": False, "saved": None, "blender_version": "5.2.0 LTS",
            "duration_ms": 1, "stdout": "", "warnings": [], "frame_changed": "1 -> 10"}))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    out = HeadlessTransport(INFO).run("exec", {"code": "x"}, blend=None, save=SaveSpec("copy", "x.blend"))
    assert out.frame_changed == "1 -> 10" and out.modified is False and out.undo is None

# -- the generated live code against the mock server in exec mode (fake bpy) -----------------------------------

def _axi_modules() -> list[str]:
    return [n for n in sys.modules if n == "blender_axi" or n.startswith("blender_axi.")]


@pytest.fixture
def exec_mock(fake_bpy):
    # The generated code purges blender_axi from sys.modules (U12); restore so other tests keep one module identity.
    saved = {n: sys.modules[n] for n in _axi_modules()}
    fake_bpy.ops.wm = types.SimpleNamespace(save_as_mainfile=lambda **kw: None, save_mainfile=lambda: None)
    try:
        with MockLiveServer(mode="exec") as srv:
            yield srv
    finally:
        for n in _axi_modules():
            del sys.modules[n]
        sys.modules.update(saved)


def transport_for(srv, timeout=5.0):
    return live.LiveTransport("127.0.0.1", srv.port, timeout=timeout)


def test_in_place_save_drops_the_session_marker(exec_mock, fake_bpy):
    """SAVE_IN_PLACE_CODE ends with session.clear_marker, so the file counts as clean again."""
    assert live.SAVE_IN_PLACE_CODE.rstrip().splitlines()[-2] == "session.clear_marker(bpy)"
    wm = {"blender_axi_modified": {"file": "x", "mtime_ns": "1"}}
    fake_bpy.context.window_manager = wm
    out = transport_for(exec_mock).run("exec", {"code": "bpy.data.is_dirty = True\nresult = {}"},
                                       save=SaveSpec("in_place", "x.blend"))
    assert out.modified is True and out.saved == "x.blend"
    assert "blender_axi_modified" not in wm


def test_generated_code_sets_the_marker_when_modified_and_reports_envelope(exec_mock, fake_bpy):
    wm = {}
    fake_bpy.context.window_manager = wm
    fake_bpy.ops.ed = types.SimpleNamespace(undo_push=lambda message: None)
    out = transport_for(exec_mock).run("exec", {"code": "bpy.data.is_dirty = True\nresult = {'a': 1}"})
    assert out.modified is True and out.undo == "pushed" and out.frame_changed is None and out.saved is None
    assert "blender_axi_modified" in wm
    wm.clear()
    out = transport_for(exec_mock).run("exec", {"code": "result = {}"})
    assert out.modified is False and wm == {}


def test_generated_code_undo_unavailable_when_push_raises(exec_mock, fake_bpy):
    def refuse(message):
        raise RuntimeError("poll() failed, context is incorrect")

    fake_bpy.ops.ed = types.SimpleNamespace(undo_push=refuse)
    out = transport_for(exec_mock).run("exec", {"code": "result = {}"})
    assert out.undo == "unavailable" and out.modified is False


def test_generated_code_frame_move_is_not_a_modification(exec_mock, fake_bpy):
    class Scene:
        frame_current = 1

        def frame_set(self, frame):
            self.frame_current = frame

    fake_bpy.context.scene = Scene()
    fake_bpy.context.window_manager = {}
    fake_bpy.ops.ed = types.SimpleNamespace(undo_push=lambda message: None)
    out = transport_for(exec_mock).run("exec", {"code": "bpy.context.scene.frame_set(10)\nresult = {}"})
    assert (out.modified, out.frame_changed) == (False, "1 -> 10")
    assert fake_bpy.context.window_manager == {}


def test_generated_code_failing_exec_that_changed_things_still_sets_the_marker(exec_mock, fake_bpy):
    wm = {}
    fake_bpy.context.window_manager = wm
    fake_bpy.ops.ed = types.SimpleNamespace(undo_push=lambda message: None)
    fake_bpy.types = types.SimpleNamespace(ID=FakeID)
    fake_bpy.data.objects = FakeColl([FakeID("Cube")])
    code = "bpy.data.objects[0].name = 'Renamed'\nraise ValueError('after the edit')"
    with pytest.raises(AxiError) as ei:
        transport_for(exec_mock).run("exec", {"code": code})
    assert ei.value.code == "EXEC_FAILED"
    assert "blender_axi_modified" in wm
    wm.clear()
    with pytest.raises(AxiError):
        transport_for(exec_mock).run("exec", {"code": "raise ValueError('nothing changed')"})
    assert wm == {}
