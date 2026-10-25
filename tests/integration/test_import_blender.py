"""Real-Blender checks for `import` (v1.3 U3): every format round trip, --collection, --new, --out, and --live on a
private headless MCP server (ASSUMED A1) plus `import --new` from a `wm.read_factory_settings` inside the op (A2).

Sources are made with `export` from basic.blend, so the round trip also proves the two commands agree on formats.
Headless tests run the command through `cli.main` (the registry entry is patched in, so they pass before the lead's
splice). Live tests start a REAL headless MCP server on a free port (never 9876) on a copy of basic.blend.
"""

from __future__ import annotations

import json
import shutil

import pytest

from blender_axi import cli, paths, registry
from test_live_optional import real_server

pytestmark = pytest.mark.blender

IMPORT = "blender_axi.commands.import_cmd:SPEC"
FORMATS = ["glb", "gltf", "fbx", "obj", "stl", "ply", "usd", "abc"]


@pytest.fixture(autouse=True)
def _register_import(monkeypatch):
    monkeypatch.setitem(registry.COMMANDS, "import", IMPORT)


def axi(capsys, *argv):
    code = cli.main(["--json", *map(str, argv)])
    out = capsys.readouterr()
    assert out.err == ""
    return code, json.loads(out.out)


@pytest.fixture(scope="module")
def work():
    d = paths.tmp_root() / "import"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    return d


@pytest.fixture(scope="module")
def sources(work, fixtures_dir):
    """One file per format, each holding just the basic.blend Cube."""
    out = work / "src"
    out.mkdir()
    for fmt in FORMATS:
        code = cli.main(["--json", "export", str(fixtures_dir / "basic.blend"), "--objects", "Cube", "--out",
                         str(out / f"cube.{fmt}")])
        assert code == 0, fmt
    return out


@pytest.fixture
def empty(work, fixtures_dir):
    path = work / "empty.blend"
    shutil.copy2(fixtures_dir / "empty.blend", path)
    path.with_name("empty.blend1").unlink(missing_ok=True)
    return path


@pytest.fixture
def basic(work, fixtures_dir):
    path = work / "basic.blend"
    shutil.copy2(fixtures_dir / "basic.blend", path)
    return path


def object_list(capsys, blend, *extra):
    code, data = axi(capsys, "objects", blend, "--limit", "200", *extra)
    assert code == 0, data
    return data["objects"] if isinstance(data["objects"], list) else []  # an empty listing is "0 objects in <file>"


@pytest.mark.parametrize("fmt", FORMATS)
def test_each_format_imports_a_mesh(capsys, sources, empty, fmt):
    code, data = axi(capsys, "import", sources / f"cube.{fmt}", empty, "--in-place")
    assert code == 0, data
    assert data["modified"] is True and data["saved"].endswith("empty.blend") and data["backup"].endswith("empty.blend1")
    assert data["format"] == fmt and data["imported"].endswith(f"cube.{fmt}")
    assert not data["added"].startswith("0") and "MESH" in data["types"], data  # names may differ per format
    objs = object_list(capsys, empty)
    assert any(o["type"] == "MESH" for o in objs)
    assert str(len(objs)) == data["added"].split(" ")[0]
    assert any("render" in h and "--engine WORKBENCH" in h for h in data["help"])


def test_format_flag_names_the_family(capsys, sources, empty):
    code, data = axi(capsys, "import", sources / "cube.usd", empty, "--format", "usd", "--in-place")
    assert code == 0 and data["format"] == "usd"


def test_collection_is_created_on_an_empty_file_and_reused_on_basic(capsys, sources, empty, basic):
    code, data = axi(capsys, "import", sources / "cube.glb", empty, "--collection", "Props", "--in-place")
    assert code == 0, data
    assert data["collection"] == "Props (created)"
    listing = object_list(capsys, empty, "--collection", "Props")
    assert len(listing) == 1 and listing[0]["type"] == "MESH"

    before = len(object_list(capsys, basic, "--collection", "Props"))
    code, data = axi(capsys, "import", sources / "cube.glb", basic, "--collection", "Props", "--in-place")
    assert code == 0 and data["collection"] == "Props", data
    assert len(object_list(capsys, basic, "--collection", "Props")) == before + 1
    names = {o["name"] for o in object_list(capsys, basic)}
    assert {"Cube", "Cube.001"} <= names  # the imported cube did not replace the file's own


def test_import_moves_objects_out_of_the_active_collection(capsys, sources, basic):
    code, data = axi(capsys, "import", sources / "cube.glb", basic, "--collection", "Props", "--in-place")
    assert code == 0, data
    in_props = {o["name"] for o in object_list(capsys, basic, "--collection", "Props")}
    assert "Cube.001" in in_props and "Cube" not in in_props  # the original Cube stays in its own collection


def test_new_imports_into_an_empty_file_a2(capsys, sources, work):  # ASSUMED A2
    out = work / "m.blend"
    out.unlink(missing_ok=True)
    code, data = axi(capsys, "import", sources / "cube.glb", "--new", "--out", out)
    assert code == 0, data
    assert data["file"] == "(new)" and data["modified"] is True and data["saved"].endswith("m.blend")
    assert out.is_file() and "backup" not in data
    listing = object_list(capsys, out)
    assert len(listing) == int(data["added"].split(" ")[0]) and {o["type"] for o in listing} == {"MESH"}
    assert not {"Camera", "Light"} & {o["name"] for o in listing}  # no default scene objects

    code, again = axi(capsys, "import", sources / "cube.glb", "--new", "--out", out)
    assert code == 1 and again["code"] == "WRITE_REFUSED"
    assert any("import" in h and "--force" in h for h in again["help"])
    code, _ = axi(capsys, "import", sources / "cube.glb", "--new", "--out", out, "--force")
    assert code == 0


def test_new_rule_violations_exit_2(capsys, sources, empty, work):
    assert axi(capsys, "import", sources / "cube.glb", empty, "--new", "--out", work / "x.blend")[0] == 2
    assert axi(capsys, "import", sources / "cube.glb", "--new")[0] == 2
    assert axi(capsys, "import", sources / "cube.glb", "--new", "--in-place")[0] == 2
    assert not (work / "x.blend").exists()


def test_out_leaves_the_original_untouched(capsys, sources, empty, work):
    before = (empty.stat().st_mtime_ns, empty.read_bytes())
    copy = work / "copy.blend"
    copy.unlink(missing_ok=True)
    code, data = axi(capsys, "import", sources / "cube.glb", empty, "--out", copy)
    assert code == 0 and data["modified"] is True and data["saved"].endswith("copy.blend") and "backup" not in data
    assert (empty.stat().st_mtime_ns, empty.read_bytes()) == before and not empty.with_name("empty.blend1").exists()
    assert len(object_list(capsys, copy)) == 1 and object_list(capsys, empty) == []


def test_neither_write_flag_is_exit_2_and_writes_nothing(capsys, sources, empty):
    before = (empty.stat().st_mtime_ns, empty.read_bytes())
    code, data = axi(capsys, "import", sources / "cube.glb", empty)
    assert code == 2 and "say where to write it" in data["error"]
    assert (empty.stat().st_mtime_ns, empty.read_bytes()) == before


def test_bad_sources_exit_before_blender(capsys, work, empty):
    (work / "model.xyz").write_bytes(b"x")
    code, data = axi(capsys, "import", work / "nope.glb", empty, "--in-place")
    assert code == 1 and data["code"] == "NOT_FOUND" and "duration_ms" not in data
    code, data = axi(capsys, "import", work / "model.xyz", empty, "--in-place")
    assert code == 2 and data["code"] == "VALIDATION_ERROR" and "duration_ms" not in data


def test_an_obj_with_no_objects_adds_nothing_and_saves_nothing(capsys, work, empty):
    src = work / "nothing.obj"
    src.write_text("# an obj with no geometry\n", encoding="utf-8")
    before = (empty.stat().st_mtime_ns, empty.read_bytes())
    code, data = axi(capsys, "import", src, empty, "--in-place")
    assert code == 0, data
    assert data["added"] == "0" and data["types"] == "none" and data["modified"] is False
    assert data["saved"] == "none (no changes)" and "backup" not in data
    assert (empty.stat().st_mtime_ns, empty.read_bytes()) == before


def test_a_corrupt_source_is_op_failed_and_changes_nothing(capsys, work, empty):
    src = work / "broken.glb"
    src.write_bytes(b"this is not a glb")
    before = empty.read_bytes()
    code, data = axi(capsys, "import", src, empty, "--in-place")
    assert code == 1 and data["code"] == "OP_FAILED", data
    assert empty.read_bytes() == before


# -- live: a private headless MCP server on a copy of basic.blend ------------------------------------------------------

@pytest.fixture(scope="module")
def session(blender_info, fixtures_dir):
    work = paths.tmp_root() / "import_live"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    blend = work / "session.blend"
    shutil.copy2(fixtures_dir / "basic.blend", blend)
    with real_server(blender_info, blend) as (port, _log):
        assert port != 9876
        yield port, blend


def test_live_import_a1(capsys, session, sources):  # ASSUMED A1
    port, _blend = session
    live = ["--live", "--host", "127.0.0.1", "--port", port]
    count_before = len(object_list(capsys, *live))
    code, data = axi(capsys, "import", sources / "cube.glb", *live)
    assert code == 0, data
    assert data["modified"] is True and data["undo"] == "unavailable (headless server)"
    assert data["saved"] == "none (live session, unsaved)"
    assert len(object_list(capsys, *live)) == count_before + 1

    code, data = axi(capsys, "import", sources / "cube.glb", *live, "--collection", "LiveProps")
    assert code == 0 and data["collection"] == "LiveProps (created)", data
    assert len(object_list(capsys, *live, "--collection", "LiveProps")) == 1
    code, data = axi(capsys, "import", sources / "cube.glb", *live, "--collection", "LiveProps")
    assert code == 0 and data["collection"] == "LiveProps"
    assert len(object_list(capsys, *live, "--collection", "LiveProps")) == 2

    code, err = axi(capsys, "import", sources / "cube.glb", *live, "--new", "--out", "x.blend")
    assert code == 2
