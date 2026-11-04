"""U2 tests: project registry (no Blender) and, marked `blender`, the start files and the three judges.

The judges are proven both ways on real Blender: an empty `out/` fails with named checks, and a reference output
produced with blender-axi itself passes (so every task is solvable by the axi arm). Temp files live under
`<repo>/.tmp/showcase/tests` (BLENDER_AXI_TMP is pointed there for this module); no real `claude`, no API, no live-session port.
"""

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

SHOWCASE = Path(__file__).resolve().parents[1]
REPO = SHOWCASE.parents[1]
for _p in (str(SHOWCASE), str(REPO / "benchmarks"), str(REPO / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from sc import projects  # noqa: E402

TMP = REPO / ".tmp" / "showcase" / "tests"
NAMES = ["turntable", "qa_cleanup", "build_export"]
EXPECTED_CHECKS = {
    "turntable": {"sheet", "sheet_width", "mp4_frames", "hero", "work_unchanged"},
    "qa_cleanup": {"clean_exists", "no_debug", "d05_visible", "no_empty_collections", "no_unused_materials",
                   "no_missing_images", "object_count", "objects_preserved", "d05_dimensions", "report_exists",
                   "report_before_after"},
    "build_export": {"lamp_blend", "meshes", "camera", "light", "bevel", "materials", "emission", "settings", "png",
                     "glb_magic", "glb_meshes", "glb_clean"},
}


@pytest.fixture(scope="module", autouse=True)
def _tmp_env():
    """All temp output (run dirs, Blender's TEMP/TMP/TMPDIR) under .tmp/showcase/tests."""
    TMP.mkdir(parents=True, exist_ok=True)
    mp = pytest.MonkeyPatch()
    mp.setenv("BLENDER_AXI_TMP", str(TMP))
    yield
    mp.undo()


# -- registry (no Blender) -------------------------------------------------------------------------

def test_names_in_readme_order():
    assert projects.names() == NAMES


@pytest.mark.parametrize("name", NAMES)
def test_load_project(name):
    p = projects.load(name)
    assert p.name == name and p.title and p.dir == SHOWCASE / "projects" / name
    assert p.start == {"turntable": "desk", "qa_cleanup": "city_messy", "build_export": "empty"}[name]
    assert "{WORK}" in p.prompt and "{OUT}" in p.prompt
    assert p.prompt.endswith(projects.PROMPT_END)
    for fname in ("prompt.md", "setup.py", "check.py", "meta.json"):
        assert (p.dir / fname).is_file(), fname
    meta = projects.meta(p)
    assert meta["expect"] and all(e.startswith("out/") for e in meta["expect"])
    assert meta["commands_hint"]


@pytest.mark.parametrize("name", NAMES)
def test_prompt_is_instrument_neutral(name):
    """Both arms get the same bytes, so the prompt names neither instrument nor an absolute path."""
    text = projects.load(name).prompt.lower()
    for word in ("blender-axi", "blender_axi", "mcp", "execute_blender_code", "c:\\", "/users/", ".tmp"):
        assert word not in text, word


def test_load_unknown_project():
    with pytest.raises(projects.ProjectError, match="unknown project 'nope'"):
        projects.load("nope")


def test_load_rejects_prompt_without_output_rule(monkeypatch, tmp_path):
    pdir = tmp_path / "bad"
    pdir.mkdir()
    (pdir / "meta.json").write_text(json.dumps({"name": "bad", "title": "t", "start": "empty"}), encoding="utf-8")
    (pdir / "prompt.md").write_text("Do things in {WORK} and {OUT}.", encoding="utf-8")
    monkeypatch.setattr(projects, "PROJECTS_DIR", tmp_path)
    with pytest.raises(projects.ProjectError, match="output-rule"):
        projects.load("bad")


def test_parse_check_takes_the_last_check_line():
    out = 'noise\nCHECK {"passed": false}\nmore\nCHECK {"passed": true, "checks": {"a": true}}\n'
    assert projects._parse_check(out) == {"passed": True, "checks": {"a": True}}
    assert projects._parse_check("no marker here") is None
    assert projects._parse_check("CHECK {broken") is None


def test_infer_start_follows_the_run_layout(tmp_path):
    p = projects.load("turntable")
    start = tmp_path / "scenes" / "turntable" / "start.blend"
    start.parent.mkdir(parents=True)
    start.write_bytes(b"x")
    out = tmp_path / "runs" / "turntable" / "axi" / "r1" / "out"
    out.mkdir(parents=True)
    assert projects._infer_start(p, out) == start
    assert projects._infer_start(p, tmp_path / "elsewhere") is None


def test_run_check_with_a_missing_blender_is_a_failure(tmp_path):
    verdict = projects.run_check(projects.load("turntable"), tmp_path / "no-such-blender.exe", tmp_path / "out")
    assert verdict["passed"] is False and verdict["checks"] == {}
    assert verdict["notes"] and "checker" in verdict["notes"][0]


def test_checkers_never_import_the_tool():
    for name in NAMES:
        for fname in ("check.py", "setup.py"):
            text = (SHOWCASE / "projects" / name / fname).read_text(encoding="utf-8")
            assert "import blender_axi" not in text and "from blender_axi" not in text, f"{name}/{fname}"


# -- real Blender ----------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def blender():
    from blender_axi.errors import AxiError
    from blender_axi.locate import find_blender

    try:
        return find_blender().path
    except AxiError as exc:
        pytest.skip(f"Blender not available: {exc.message}")


@pytest.fixture(scope="module")
def starts(blender):
    return {n: projects.prepare_start(projects.load(n), TMP / "showcase", blender) for n in NAMES}


def axi(blender, *args):
    return projects._axi(blender, [str(a) for a in args])


def fresh_run(starts, name, arm="axi"):
    """`<tmp>/runs/<project>/<arm>/r1` with work.blend copied from the start and an empty out/ (frozen layout)."""
    run = TMP / "showcase" / "runs" / name / arm / "r1"
    shutil.rmtree(run, ignore_errors=True)
    (run / "out").mkdir(parents=True)
    shutil.copyfile(starts[name], run / "work.blend")
    return run


def check_of(blender, name, run):
    return projects.run_check(projects.load(name), blender, run / "out")


@pytest.mark.blender
def test_start_files_have_the_measured_object_counts(blender, starts):
    counts = {}
    for name, path in starts.items():
        info = json.loads(path.with_name("start.json").read_text(encoding="utf-8"))
        assert info["sha256"] == projects.sha256(path) and info["project"] == name
        counts[name] = int(str(axi(blender, "scene", path)["objects"]).split()[0])   # "13 (MESH 10, LIGHT 2, ...)"
    assert counts == {"turntable": 13, "qa_cleanup": 505, "build_export": 0}


@pytest.mark.blender
def test_prepare_start_is_idempotent(blender, starts):
    before = {n: (p.stat().st_mtime_ns, projects.sha256(p)) for n, p in starts.items()}
    again = {n: projects.prepare_start(projects.load(n), TMP / "showcase", blender) for n in NAMES}
    assert again == starts
    assert {n: (p.stat().st_mtime_ns, projects.sha256(p)) for n, p in again.items()} == before


@pytest.mark.blender
def test_qa_start_has_the_planted_problems(blender, starts):
    path = starts["qa_cleanup"]
    audit = {f["check"]: f for f in axi(blender, "audit", path, "--checks", "visibility,files,collections")["findings"]}
    assert audit["visibility"]["count"] == 6      # 3 Debug_* + 3 D05_Bldg*, render off
    assert audit["files"]["count"] == 1 and "missing_tex.png" in audit["files"]["items"]
    assert audit["collections"]["count"] == 2     # Tmp_A, Tmp_B
    info = json.loads(path.with_name("start.json").read_text(encoding="utf-8"))["result"]
    assert info["debug_objects"] == 3 and info["objects"] == 505


@pytest.mark.blender
@pytest.mark.parametrize("name", NAMES)
def test_empty_output_fails_with_named_checks(blender, starts, name):
    run = fresh_run(starts, name)
    verdict = check_of(blender, name, run)
    assert verdict["passed"] is False
    assert set(verdict["checks"]) == EXPECTED_CHECKS[name]
    failed = {k for k, v in verdict["checks"].items() if not v}
    assert failed, verdict
    if name == "turntable":
        assert failed == {"sheet", "sheet_width", "mp4_frames", "hero"}   # work.blend is still the start
    if name == "build_export":
        assert not verdict["checks"]["lamp_blend"] and not verdict["checks"]["png"]


@pytest.mark.blender
def test_turntable_reference_with_blender_axi_passes(blender, starts):
    run = fresh_run(starts, "turntable")
    work, out = run / "work.blend", run / "out"
    common = ["--fit", "--subject", "Mug", "--engine", "WORKBENCH", "--resolution", "640x360", "--force"]
    axi(blender, "render", work, "--turntable", 12, "--sheet", *common, "--out", out / "sheet.png")
    axi(blender, "render", work, "--turntable", 12, *common, "--out", out / "turntable.mp4", "--fps", 12)
    axi(blender, "render", work, *common, "--out", out / "hero.png")
    verdict = check_of(blender, "turntable", run)
    assert verdict["passed"] is True, verdict
    assert all(verdict["checks"].values()) and set(verdict["checks"]) == EXPECTED_CHECKS["turntable"]


@pytest.mark.blender
def test_turntable_fails_when_the_work_file_changed(blender, starts):
    run = fresh_run(starts, "turntable")
    axi(blender, "set", "Mug", run / "work.blend", "--location", "0,0,2", "--in-place")
    verdict = check_of(blender, "turntable", run)
    assert verdict["checks"]["work_unchanged"] is False and verdict["passed"] is False


PURGE = '''
import os
changed = True
while changed:
    changed = False
    for c in list(bpy.data.collections):
        if not c.objects and not c.children:
            bpy.data.collections.remove(c)
            changed = True
for m in list(bpy.data.materials):
    if m.users - (1 if m.use_fake_user else 0) == 0:
        bpy.data.materials.remove(m)
for i in list(bpy.data.images):
    if i.source == "FILE" and not os.path.exists(bpy.path.abspath(i.filepath)):
        bpy.data.images.remove(i)
result = {"objects": len(bpy.data.objects), "collections": len(bpy.data.collections),
          "materials": len(bpy.data.materials), "images": len(bpy.data.images)}
'''


@pytest.mark.blender
def test_qa_cleanup_reference_with_blender_axi_passes(blender, starts):
    run = fresh_run(starts, "qa_cleanup")
    work, out = run / "work.blend", run / "out"
    steps = ["delete Debug_Marker01,Debug_Marker02,Debug_Marker03"]
    steps += [f"set D05_Bldg{n:02d} --show" for n in (2, 9, 15)]
    (run / "steps.json").write_text(json.dumps(steps), encoding="utf-8")
    (run / "purge.py").write_text(PURGE, encoding="utf-8")
    axi(blender, "apply", run / "steps.json", work, "--out", out / "clean.blend")
    axi(blender, "exec", out / "clean.blend", "--script", run / "purge.py", "--in-place", "--allow-exec")
    (out / "report.md").write_text("# Cleanup\nBefore: 505 objects. After: 502 objects.\n", encoding="utf-8")
    verdict = check_of(blender, "qa_cleanup", run)
    assert verdict["passed"] is True, verdict
    assert set(verdict["checks"]) == EXPECTED_CHECKS["qa_cleanup"]


@pytest.mark.blender
def test_qa_cleanup_partial_fix_fails_named_checks(blender, starts):
    """Only the typed fixes (no purge): Debug gone and District05 visible, but orphans, collections and image remain."""
    run = fresh_run(starts, "qa_cleanup")
    steps = ["delete Debug_Marker01,Debug_Marker02,Debug_Marker03"] + [f"set D05_Bldg{n:02d} --show" for n in (2, 9, 15)]
    (run / "steps.json").write_text(json.dumps(steps), encoding="utf-8")
    axi(blender, "apply", run / "steps.json", run / "work.blend", "--out", run / "out" / "clean.blend")
    (run / "out" / "report.md").write_text("before and after", encoding="utf-8")
    verdict = check_of(blender, "qa_cleanup", run)
    failed = {k for k, v in verdict["checks"].items() if not v}
    assert failed == {"no_empty_collections", "no_unused_materials", "no_missing_images"}, verdict
    assert verdict["passed"] is False


LAMP_STEPS = [
    "add cylinder --name Base --radius 0.6 --depth 0.15 --location 0,0,0.075",
    "add cylinder --name Stem --radius 0.04 --depth 1.4 --location 0,0,0.85",
    "add cone --name Head --radius 0.4 --depth 0.6 --location 0,0,1.8 --rotation 180,0,0",
    "add sphere --name Bulb --radius 0.15 --location 0,0,1.65",
    "add camera --name Camera --location 3,-3,1.8 --rotation 80,0,45",
    "add light --name KeyLight --type POINT --energy 500 --location 2,-2,3",
    "material Base --name DarkMetal --color 0.05,0.05,0.06 --metallic 1 --roughness 0.35",
    "material Stem --name DarkMetal",
    "material Head --name RedMatte --color 0.8,0.05,0.05 --roughness 0.9",
    "material Bulb --name BulbGlow --color 1,0.9,0.6 --emission 1,0.9,0.6 --emission-strength 20",
    "modifier Base bevel --width 0.03 --segments 3",
    "configure --engine EEVEE --resolution 640x360 --samples 16 --camera Camera",
]


@pytest.mark.blender
def test_build_export_reference_with_blender_axi_passes(blender, starts):
    run = fresh_run(starts, "build_export")
    out = run / "out"
    (run / "steps.json").write_text(json.dumps(LAMP_STEPS), encoding="utf-8")
    axi(blender, "apply", run / "steps.json", run / "work.blend", "--out", out / "lamp.blend")
    axi(blender, "render", out / "lamp.blend", "--fit", "--out", out / "lamp.png", "--force")
    axi(blender, "export", out / "lamp.blend", "--object", "Base", "--object", "Stem", "--object", "Head",
        "--object", "Bulb", "--out", out / "lamp.glb")
    verdict = check_of(blender, "build_export", run)
    assert verdict["passed"] is True, verdict
    assert set(verdict["checks"]) == EXPECTED_CHECKS["build_export"]


@pytest.mark.blender
def test_build_export_glb_with_a_camera_fails(blender, starts):
    run = fresh_run(starts, "build_export")
    out = run / "out"
    (run / "steps.json").write_text(json.dumps(LAMP_STEPS), encoding="utf-8")
    axi(blender, "apply", run / "steps.json", run / "work.blend", "--out", out / "lamp.blend")
    axi(blender, "render", out / "lamp.blend", "--fit", "--out", out / "lamp.png", "--force")
    (run / "glb_all.py").write_text(      # the typed export leaves cameras and lights out; force them in
        f"bpy.ops.export_scene.gltf(filepath={(out / 'lamp.glb').as_posix()!r}, export_cameras=True, "
        "export_lights=True)\nresult = {}\n", encoding="utf-8")
    axi(blender, "exec", out / "lamp.blend", "--script", run / "glb_all.py", "--allow-exec")
    verdict = check_of(blender, "build_export", run)
    assert verdict["checks"]["glb_clean"] is False and verdict["passed"] is False
    assert os.path.getsize(out / "lamp.glb") > 0
