"""U11: `render --live` / `export --live` leave the open Blender session exactly as they found it.

Starts a REAL headless MCP server on a free port (never 9876, which may be the user's GUI Blender) on a copy of
basic.blend, puts the session into a distinctive "user" state, runs the ops through `cli.main`, and reads the state
back over the same socket. The server process tree is killed at teardown; no other Blender is touched.
"""

from __future__ import annotations

import json
import shutil

import pytest

from blender_axi import cli, live, paths
from test_live_optional import real_server

pytestmark = pytest.mark.blender

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

SET_USER_STATE = """
import bpy
s = bpy.context.scene
vl = bpy.context.view_layer
s.render.engine = 'BLENDER_EEVEE'
s.render.resolution_x, s.render.resolution_y, s.render.resolution_percentage = 77, 55, 80
s.render.filepath = '//user_own_'
s.render.use_file_extension = True
s.render.image_settings.file_format = 'JPEG'
s.render.image_settings.quality = 71
s.eevee.taa_render_samples = 33
s.frame_set(3)
for o in s.objects:
    o.select_set(False)
bpy.data.objects['Cube'].select_set(True)
bpy.data.objects['Camera'].select_set(True)
vl.objects.active = bpy.data.objects['Camera']
result = {"ok": True}
"""

READ_STATE = """
import bpy
s = bpy.context.scene
vl = bpy.context.view_layer
r = s.render
result = {
    "engine": r.engine, "res": [r.resolution_x, r.resolution_y, r.resolution_percentage],
    "path": r.filepath, "ext": r.use_file_extension, "fmt": r.image_settings.file_format,
    "quality": r.image_settings.quality, "color_mode": r.image_settings.color_mode,
    "eevee": s.eevee.taa_render_samples, "camera": s.camera.name if s.camera else None,
    "frame": s.frame_current, "selected": sorted(o.name for o in s.objects if o.select_get()),
    "active": vl.objects.active.name if vl.objects.active else None,
}
"""


SELECTIVE_EXPORT = """
import sys
import bpy
sys.path.insert(0, {src!r})
from blender_axi.blender_side import ops, op_export
seen = []
real_call = op_export._call
op_export._call = lambda b, f, p, s: (seen.append(sorted(o.name for o in bpy.context.scene.objects if o.select_get())),
                                      real_call(b, f, p, s))[1]
objs = [bpy.data.objects[n] for n in {names!r}]
try:
    with bpy.context.temp_override(selected_objects=objs, active_object=objs[0]):
        result = ops.run('export', {params!r})
finally:
    op_export._call = real_call
result["selected_during"] = seen[0]
"""


@pytest.fixture(scope="module")
def session(blender_info, fixtures_dir):
    work = paths.tmp_root() / "live_side_effects"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    blend = work / "session.blend"
    shutil.copy(fixtures_dir / "basic.blend", blend)
    with real_server(blender_info, blend) as (port, _log):
        assert port != 9876
        yield port, work


def _exec(port: int, code: str) -> dict:
    response = live.send("127.0.0.1", port, code, strict_json=True, timeout=120)
    assert response.get("status") == "ok", response
    return response["result"]


def _axi(capsys, *argv):
    code = cli.main(["--json", *map(str, argv)])
    out = capsys.readouterr()
    return code, json.loads(out.out)


def test_render_and_export_live_leave_the_session_untouched(session, capsys):
    port, work = session
    live_args = ["--live", "--host", "127.0.0.1", "--port", port]
    _exec(port, SET_USER_STATE)
    before = _exec(port, READ_STATE)
    assert before["frame"] == 3 and before["selected"] == ["Camera", "Cube"] and before["active"] == "Camera"
    assert before["res"] == [77, 55, 80] and before["fmt"] == "JPEG" and before["engine"] == "BLENDER_EEVEE"

    for round_ in (1, 2):  # twice in one session: nothing may accumulate
        png = work / f"shot{round_}.png"
        code, data = _axi(capsys, "render", *live_args, "--resolution", "32x32", "--engine", "WORKBENCH",
                          "--frame", "2", "--out", png, "--force")
        assert code == 0, data
        # the render used its own settings (32x32 at the session's 80%) ...
        assert data["size"] == "25x25" and data["engine"] == "WORKBENCH"
        assert png.read_bytes()[:8] == PNG_MAGIC
        assert _exec(port, READ_STATE) == before

        # A windowless headless server has no context.selected_objects / window, so the exporters cannot honour
        # `use_selection` through the CLI there (pre-existing, unrelated to side effects; the GUI has them). The
        # export op is therefore also run inside the server under a temp_override that supplies them, which
        # exercises the real select / export / restore path with the FBX exporter.
        cases = (({"collection": "Props"}, ["Ball"], ["Anchor", "Ball"]),  # Props = Ball + the Anchor empty
                 ({"objects": ["Cube", "Ball"]}, ["Cube", "Ball"], ["Ball", "Cube"]))
        for i, (params, exported, selected_during) in enumerate(cases):
            out = work / f"sel{round_}_{i}.fbx"
            res = _exec(port, SELECTIVE_EXPORT.format(src=live.SRC_DIR, names=exported,
                                                      params={"out": str(out), "format": "fbx", "force": True, **params}))
            assert res["objects"] == len(exported) and out.stat().st_size > 0
            assert res["selected_during"] == selected_during  # the op really replaced the selection ...
            assert _exec(port, READ_STATE) == before  # ... and put it back

        code, data = _axi(capsys, "export", *live_args, "--out", work / f"all{round_}.fbx", "--force")
        assert code == 0, data  # whole scene through the real CLI
        assert _exec(port, READ_STATE) == before


def test_failed_render_and_export_live_also_restore(session, capsys):
    port, work = session
    live_args = ["--live", "--host", "127.0.0.1", "--port", port]
    _exec(port, SET_USER_STATE)
    before = _exec(port, READ_STATE)

    code, data = _axi(capsys, "render", *live_args, "--resolution", "32x32", "--engine", "WORKBENCH",
                      "--camera", "Ball", "--out", work / "never.png", "--force")
    assert code == 1 and data["code"] == "NOT_FOUND"  # fails after engine/resolution were assigned
    assert _exec(port, READ_STATE) == before

    code, data = _axi(capsys, "export", *live_args, "--objects", "Cube,Ghost", "--out", work / "never.fbx")
    assert code == 1 and data["code"] == "NOT_FOUND"
    assert _exec(port, READ_STATE) == before

    # an exporter that blows up after the selection was replaced (glTF needs a context a headless server lacks)
    code, data = _axi(capsys, "export", *live_args, "--collection", "Props", "--out", work / "never.glb")
    assert code == 1 and data["code"] == "LIVE_ERROR"  # (if a future Blender allows it, drop this case)
    assert _exec(port, READ_STATE) == before
    assert not (work / "never.png").exists() and not (work / "never.glb").exists()
