"""Real-Blender checks for `render` and `export` (tiny 64x64 WORKBENCH renders only). Outputs go under .tmp/tests/."""

import json
import shutil
from pathlib import Path

import pytest

from blender_axi import cli, paths

pytestmark = pytest.mark.blender

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.fixture(scope="module")
def work():
    d = paths.tmp_root() / "render_export"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    return d


@pytest.fixture
def basic(fixtures_dir):
    return fixtures_dir / "basic.blend"


def axi(capsys, *argv):
    code = cli.main([*map(str, argv), "--json"])
    captured = capsys.readouterr()
    assert captured.err == ""
    return code, json.loads(captured.out)


def render_args(blend, out, *extra):
    return ["render", blend, "--engine", "WORKBENCH", "--resolution", "64x64", "--out", out, *extra]


def test_render_still_refuse_and_force(capsys, basic, work):
    out = work / "render.png"
    before = (basic.stat().st_mtime_ns, basic.stat().st_size)
    code, data = axi(capsys, *render_args(basic, out))
    assert code == 0, data
    assert data["rendered"] == "1 frame" and data["size"] == "64x64" and data["engine"] == "WORKBENCH"
    assert data["samples"] == "n/a" and data["duration_ms"] > 0
    assert out.stat().st_size > 0 and out.read_bytes()[:8] == PNG_MAGIC
    assert (basic.stat().st_mtime_ns, basic.stat().st_size) == before

    code, data = axi(capsys, *render_args(basic, out))
    assert code == 1 and data["code"] == "WRITE_REFUSED" and any("--force" in h for h in data["help"])

    out.unlink()
    out.write_bytes(b"stale")
    code, data = axi(capsys, *render_args(basic, out, "--force"))
    assert code == 0 and out.read_bytes()[:8] == PNG_MAGIC


def test_render_frame_range_writes_one_file_per_frame(capsys, basic, work):
    out = work / "seq" / "shot_####.png"
    code, data = axi(capsys, *render_args(basic, out, "--frames", "1..2"))
    assert code == 0, data
    assert data["rendered"] == "2 frames" and data["output"].endswith("shot_####.png")
    files = sorted(p.name for p in out.parent.iterdir())
    assert files == ["shot_0001.png", "shot_0002.png"]
    assert all((out.parent / f).stat().st_size > 0 for f in files)


def test_render_range_without_hashes_and_single_frame_number(capsys, basic, work):
    code, data = axi(capsys, *render_args(basic, work / "plain" / "p.png", "--frames", "3..4"))
    assert code == 0, data
    assert sorted(p.name for p in (work / "plain").iterdir()) == ["p_0003.png", "p_0004.png"]
    code, data = axi(capsys, *render_args(basic, work / "plain" / "one_####.png", "--frame", "7"))
    assert code == 0 and (work / "plain" / "one_0007.png").is_file()


def test_render_default_out_and_inferred_file_in_cwd(capsys, basic, work, monkeypatch):
    cwd = work / "cwd"
    cwd.mkdir()
    shutil.copy(basic, cwd / "basic.blend")
    monkeypatch.chdir(cwd)
    code, data = axi(capsys, "render", "--engine", "WORKBENCH", "--resolution", "64x64")
    assert code == 0, data
    assert data["file"] == "basic.blend" and data["output"] == "basic_f0001.png"
    assert (cwd / "basic_f0001.png").stat().st_size > 0
    # rerun: the default frame is the scene's current frame, so the op refuses it
    code, data = axi(capsys, "render", "--engine", "WORKBENCH", "--resolution", "64x64")
    assert code == 1 and data["code"] == "WRITE_REFUSED"


def test_render_jpeg_and_percent(capsys, basic, work):
    out = work / "fmt" / "r.jpg"
    code, data = axi(capsys, *render_args(basic, out, "--percent", "50"))
    assert code == 0, data
    assert data["size"] == "32x32" and out.read_bytes()[:2] == b"\xff\xd8"


def test_render_camera_errors(capsys, basic, fixtures_dir, work):
    code, data = axi(capsys, *render_args(basic, work / "cam.png", "--camera", "Ball"))
    assert code == 1 and data["code"] == "NOT_FOUND" and "camera not found" in data["error"]
    assert any("--type CAMERA" in h for h in data["help"])
    code, data = axi(capsys, *render_args(fixtures_dir / "empty.blend", work / "nocam.png"))
    assert code == 1 and data["code"] == "NOT_FOUND" and "no active camera" in data["error"]
    assert not (work / "cam.png").exists() and not (work / "nocam.png").exists()


def test_render_validation_does_not_start_blender(capsys, basic, work):
    code, data = axi(capsys, *render_args(basic, work / "v.png", "--frames", "9..2"))
    assert code == 2 and data["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("name,magic", [
    ("model.glb", b"glTF"), ("model.obj", None), ("model.fbx", b"Kaydara FBX Binary"), ("model.stl", None),
    ("model.ply", b"ply"), ("model.usda", b"#usda"), ("model.abc", b"Ogaw"),
])
def test_export_formats(capsys, basic, work, name, magic):
    out = work / "export" / name
    code, data = axi(capsys, "export", basic, "--out", out)
    assert code == 0, data
    assert data["format"] == name.split(".")[1] and data["objects"] == 2 and data["size_kb"] > 0
    assert out.is_file() and out.stat().st_size > 0
    if magic:
        assert out.read_bytes()[: len(magic)] == magic


def test_export_gltf_writes_bin_sidecar(capsys, basic, work):
    out = work / "export" / "sep.gltf"
    code, data = axi(capsys, "export", basic, "--out", out)
    assert code == 0, data
    assert out.is_file() and out.with_suffix(".bin").is_file()
    assert data["size_kb"] >= round(out.with_suffix(".bin").stat().st_size / 1024, 1)


def test_export_collection_and_objects_reduce_count(capsys, basic, work):
    code, whole = axi(capsys, "export", basic, "--out", work / "sel" / "all.glb")
    code2, props = axi(capsys, "export", basic, "--out", work / "sel" / "props.glb", "--collection", "Props")
    code3, one = axi(capsys, "export", basic, "--out", work / "sel" / "cube.obj", "--objects", "Cube")
    assert (code, code2, code3) == (0, 0, 0), (whole, props, one)
    assert whole["objects"] == 2 and props["objects"] == 1 and one["objects"] == 1
    assert (work / "sel" / "props.glb").stat().st_size < (work / "sel" / "all.glb").stat().st_size


def test_export_refuse_force_and_errors(capsys, basic, fixtures_dir, work):
    out = work / "ref" / "a.glb"
    assert axi(capsys, "export", basic, "--out", out)[0] == 0
    code, data = axi(capsys, "export", basic, "--out", out)
    assert code == 1 and data["code"] == "WRITE_REFUSED"
    assert axi(capsys, "export", basic, "--out", out, "--force")[0] == 0

    code, data = axi(capsys, "export", basic, "--out", work / "ref" / "b.glb", "--collection", "Nope")
    assert code == 1 and data["code"] == "NOT_FOUND" and any("objects <file>" in h for h in data["help"])
    code, data = axi(capsys, "export", basic, "--out", work / "ref" / "c.glb", "--objects", "Cube,Ghost")
    assert code == 1 and data["code"] == "NOT_FOUND" and "Ghost" in data["error"]
    code, data = axi(capsys, "export", fixtures_dir / "empty.blend", "--out", work / "ref" / "d.glb")
    assert code == 1 and data["code"] == "NOT_FOUND" and "no exportable" in data["error"]
    assert not (work / "ref" / "b.glb").exists() and not (work / "ref" / "d.glb").exists()


def test_export_format_inference_error(capsys, basic, work):
    code, data = axi(capsys, "export", basic, "--out", work / "x.dae")
    assert code == 2 and data["code"] == "VALIDATION_ERROR"
