"""Real-Blender checks for `audit` (0.6.0 plan U3).

Headless tests run through `cli.main` with `audit` registered by monkeypatch (the lead splices registry.py); the live
test starts a REAL headless MCP server on a free port (never the default port) on a COPY of the stand-in scene. Everything lands
under .tmp/tests/audit/. The 502-object city scene (.tmp/bench/scenes/city.blend) is used when present.

Stand-in scene (16 objects, no camera, no light, EEVEE, no world): Big0-9 scaled 2x, Bare (no material), Cutter
(hidden), NoRender (render off), Cyl (2 n-gon caps), Plane (4 open edges), Dust (3 loose verts); an empty collection;
an image `Wood.png` that points at the missing `//tex_missing.png` (ASSUMED A6: `//` resolves headless).
"""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

import pytest

from blender_axi import cli, paths, registry
from test_live_optional import real_server

pytestmark = pytest.mark.blender

REPO = Path(__file__).resolve().parents[2]
CITY = REPO / ".tmp" / "bench" / "scenes" / "city.blend"
DESK = REPO / ".tmp" / "bench" / "scenes" / "desk.blend"

BUILD = """
import bpy
mat = bpy.data.materials.new("Mat")
def cube(name, x, scale=(1, 1, 1), material=True):
    bpy.ops.mesh.primitive_cube_add(location=(x, 0, 0))
    o = bpy.context.active_object
    o.name, o.scale = name, scale
    if material:
        o.data.materials.append(mat)
    return o
for i in range(10):
    cube(f"Big{i}", i * 3, (2, 2, 2))
cube("Bare", 0, material=False)
cube("Cutter", 3).hide_set(True)
cube("NoRender", 6).hide_render = True
bpy.ops.mesh.primitive_cylinder_add(vertices=8, location=(0, 4, 0))
bpy.context.active_object.name = "Cyl"
bpy.context.active_object.data.materials.append(mat)
bpy.ops.mesh.primitive_plane_add(location=(0, 8, 0))
bpy.context.active_object.name = "Plane"
bpy.context.active_object.data.materials.append(mat)
mesh = bpy.data.meshes.new("DustMesh")
mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [])
dust = bpy.data.objects.new("Dust", mesh)
bpy.context.scene.collection.objects.link(dust)
tex = bpy.data.materials.new("Tex")
tex.use_nodes = True
node = tex.node_tree.nodes.new("ShaderNodeTexImage")
img = bpy.data.images.new("Wood.png", 4, 4)
img.source, img.filepath = "FILE", "//tex_missing.png"
node.image = img
mesh.materials.append(tex)
bpy.context.scene.collection.children.link(bpy.data.collections.new("Hollow"))
result = {"objects": len(bpy.context.scene.objects)}
"""


def axi(capsys, *argv):
    code = cli.main(["--json", *map(str, argv)])
    out = capsys.readouterr()
    assert out.err == ""
    return code, json.loads(out.out)


@pytest.fixture(autouse=True)
def audit_registered(monkeypatch):
    monkeypatch.setitem(registry.COMMANDS, "audit", "blender_axi.commands.audit:SPEC")


@pytest.fixture(scope="module")
def work():
    d = paths.tmp_root() / "audit"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    return d


@pytest.fixture(scope="module")
def stand_in(work):
    path = work / "stand_in.blend"
    script = work / "build.py"
    script.write_text(BUILD, encoding="utf-8")
    assert cli.main(["--json", "new", str(path)]) == 0
    assert cli.main(["--json", "exec", str(path), "--script", str(script), "--allow-exec", "--in-place"]) == 0
    return path


def rows(data) -> dict:
    return {f["check"]: f for f in data.get("findings", [])}


def test_stand_in_findings(capsys, stand_in):
    before = (stand_in.stat().st_mtime_ns, stand_in.stat().st_size)
    code, data = axi(capsys, "audit", stand_in)
    assert code == 0, data
    assert data["checked"] == "11 checks, 16 objects"
    found = rows(data)
    assert found["camera"]["items"] == "no scene camera" and found["camera"]["level"] == "warn"
    assert found["lighting"]["items"].startswith("unlit scene:")
    assert found["scale"]["count"] == 10 and found["scale"]["items"].startswith("Big0 (2,2,2), Big1 (2,2,2)")
    assert found["materials"]["items"] == "Bare"
    assert found["ngons"] == {"check": "ngons", "level": "info", "count": 1, "items": "Cyl (2 faces)"}
    assert found["nonmanifold"]["items"] == "Plane (4 edges)"
    assert found["loose"]["items"] == "Dust (3 verts)"
    assert found["visibility"]["items"] == "Cutter (hidden, renders), NoRender (render off)"
    assert found["collections"]["items"] == "Hollow"
    assert found["files"]["items"].startswith("image Wood.png (missing: ")  # A6
    assert found["files"]["items"].endswith("/tex_missing.png)") and "\\" not in found["files"]["items"]
    assert str(stand_in.parent.as_posix()) in found["files"]["items"]  # `//` resolved against the .blend directory
    assert data["status"].startswith(f"{len(found)} findings (") and data["status"].count("warn") == 1
    assert (stand_in.stat().st_mtime_ns, stand_in.stat().st_size) == before  # read-only


def test_strict_exits_1_and_prints_the_report(capsys, stand_in):
    code, data = axi(capsys, "audit", stand_in, "--strict")
    assert code == 1 and data["status"].endswith("; --strict: exit 1") and "scale" in rows(data)
    code, data = axi(capsys, "audit", stand_in, "--strict", "--checks", "ngons,visibility")
    assert code == 0 and "--strict" not in data["status"]  # info findings only


def test_checks_object_filter(capsys, stand_in):
    code, data = axi(capsys, "audit", stand_in, "--checks", "scale", "--object", "Big3")
    assert code == 0 and list(rows(data)) == ["scale"] and rows(data)["scale"]["items"] == "Big3 (2,2,2)"
    assert data["checked"] == "1 check, 1 object (scene-level checks skipped)"
    code, data = axi(capsys, "audit", stand_in, "--object", "Bare", "--checks", "scale,materials")
    assert data["checked"] == "2 checks, 1 object (scene-level checks skipped)"
    assert list(rows(data)) == ["materials"]
    code, data = axi(capsys, "audit", stand_in, "--object", "Nope")
    assert code == 1 and data["code"] == "NOT_FOUND" and any("objects" in h for h in data["help"])
    code, data = axi(capsys, "audit", stand_in, "--collection", "Nope")
    assert code == 1 and data["code"] == "NOT_FOUND"


def test_collection_filter(capsys, stand_in):
    code, data = axi(capsys, "audit", stand_in, "--collection", "Hollow")
    assert code == 0 and data["status"] == "clean" and data["checked"].startswith("6 checks, 0 objects")


def test_full_shows_every_item_and_hint_is_gone(capsys, stand_in):
    code, data = axi(capsys, "audit", stand_in, "--checks", "scale")
    assert rows(data)["scale"]["items"].endswith("+5 more") and any("--full" in h for h in data["help"])
    code, data = axi(capsys, "--full", "audit", stand_in, "--checks", "scale")
    assert "more" not in rows(data)["scale"]["items"] and rows(data)["scale"]["items"].count("(2,2,2)") == 10
    assert not any("--full" in h for h in data["help"])


def test_basic_and_empty_fixtures(capsys, fixtures_dir):
    code, data = axi(capsys, "audit", fixtures_dir / "basic.blend")
    assert code == 0 and "Ball" in rows(data)["materials"]["items"]
    code, data = axi(capsys, "audit", fixtures_dir / "empty.blend")
    found = rows(data)
    assert code == 0 and "camera" in found and "lighting" in found and data["checked"] == "11 checks, 0 objects"
    assert [h for h in data["help"] if "add light" in h] and [h for h in data["help"] if "add camera" in h]


def test_clean_scene_after_fixing_camera_and_light(capsys, work, stand_in):
    copy = work / "fixed.blend"
    shutil.copy(stand_in, copy)
    code, data = axi(capsys, "audit", copy, "--checks", "camera,lighting,collections")
    assert set(rows(data)) == {"camera", "lighting", "collections"}
    assert cli.main(["--json", "add", "camera", str(copy), "--in-place"]) == 0
    assert cli.main(["--json", "add", "light", str(copy), "--type", "SUN", "--in-place"]) == 0
    capsys.readouterr()
    code, data = axi(capsys, "audit", copy, "--checks", "camera,lighting")
    assert code == 0 and data["status"] == "clean" and "findings" not in data
    assert any("export <file>" in h or "export " in h for h in data["help"])


def test_desk_nonmanifold_edges(capsys):
    if not DESK.exists():
        pytest.skip("desk.blend not generated")
    code, data = axi(capsys, "--full", "audit", DESK, "--checks", "nonmanifold")
    assert code == 0
    edges = sum(int(n) for n in re.findall(r"\((\d+) edges\)", rows(data)["nonmanifold"]["items"]))
    assert edges == 4


def test_city_counts_and_timing(capsys):
    if not CITY.exists():
        pytest.skip("city.blend not generated")
    started = time.perf_counter()
    code, data = axi(capsys, "audit", CITY)
    wall = time.perf_counter() - started
    assert code == 0, data
    found = rows(data)
    assert data["checked"] == "11 checks, 502 objects"
    assert found["scale"]["count"] == 400 and found["materials"]["count"] == 100
    assert found["ngons"]["count"] == 100  # 100 objects (50 trunks with 2 cap n-gons, 50 crowns with 1) = 150 faces
    _, full = axi(capsys, "--full", "audit", CITY, "--checks", "ngons")
    assert sum(int(n) for n in re.findall(r"\((\d+) faces?\)", rows(full)["ngons"]["items"])) == 150
    assert set(found) == {"scale", "materials", "ngons"}
    assert data["duration_ms"] < 4000 and wall < 20


def test_live_audit_equals_headless(capsys, blender_info, work, stand_in):
    copy = work / "live_stand_in.blend"
    shutil.copy(stand_in, copy)
    _, headless = axi(capsys, "audit", copy)
    with real_server(blender_info, copy) as (port, _log):
        assert port != 9876
        live = ["--live", "--host", "127.0.0.1", "--port", port]
        code, via_live = axi(capsys, "audit", *live)
        assert code == 0, via_live
        for key in ("file", "duration_ms", "help"):
            headless.pop(key, None)
            via_live.pop(key, None)
        assert via_live == headless  # A3: bmesh works in the headless MCP session, nothing is dropped
        assert "warning" not in via_live and "nonmanifold" in rows(via_live)
        before = (copy.stat().st_mtime_ns, copy.stat().st_size)
        orphan = "bpy.data.materials.new('Orphan'); result = {}"
        assert cli.main(["--json", "exec", "--code", orphan, "--allow-exec", *map(str, live)]) == 0
        capsys.readouterr()
        code, data = axi(capsys, "audit", "--checks", "unused", *live)
        assert code == 0 and rows(data)["unused"]["items"] == "material Orphan"
        assert (copy.stat().st_mtime_ns, copy.stat().st_size) == before
