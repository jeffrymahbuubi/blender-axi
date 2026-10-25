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


# (selector flags, expected exported object count): Props = Ball + the Anchor empty -> 1 geometry object
SELECTIVE = (
    (["--collection", "Props"], 1, ("glb", "fbx", "obj", "stl")),
    (["--objects", "Cube,Ball"], 2, ("fbx", "obj")),
)


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

        # v1.1 K5: op_export supplies the window and the selection context a windowless headless server lacks, so
        # selective exports and glTF work through the real CLI there; the select / export / restore path is exercised.
        for flags, count, formats in SELECTIVE:
            for fmt in formats:
                out = work / f"sel{round_}_{flags[0].lstrip('-')}.{fmt}"
                code, data = _axi(capsys, "export", *live_args, *flags, "--out", out, "--force")
                assert code == 0, (flags, fmt, data)
                assert data["objects"] == count and data["format"] == fmt and out.stat().st_size > 0, data
                assert _exec(port, READ_STATE) == before  # the user's selection / active object are put back

        for fmt in ("glb", "fbx", "obj", "stl"):  # whole scene through the real CLI (A2: glTF needs no selection)
            out = work / f"all{round_}.{fmt}"
            code, data = _axi(capsys, "export", *live_args, "--out", out, "--force")
            assert code == 0, (fmt, data)
            assert data["objects"] == 2 and out.stat().st_size > 0, data  # Cube + Ball; camera/light/empty are not geometry
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

    # a failure after the selection was replaced: the output's parent directory is a regular file
    blocker = work / "blocker.txt"
    blocker.write_text("not a directory")
    code, data = _axi(capsys, "export", *live_args, "--collection", "Props", "--out", blocker / "never.glb")
    assert code == 1, data
    assert _exec(port, READ_STATE) == before
    assert not (work / "never.png").exists() and not (blocker / "never.glb").exists()
