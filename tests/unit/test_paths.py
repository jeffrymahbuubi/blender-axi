import tempfile
from pathlib import Path

from blender_axi import paths

REPO = Path(__file__).resolve().parents[2]


def test_tmp_root_honours_env(monkeypatch):
    monkeypatch.setenv("BLENDER_AXI_TMP", str(REPO / ".tmp" / "x"))
    assert paths.tmp_root() == REPO / ".tmp" / "x"


def test_tmp_root_checkout_default(monkeypatch):
    monkeypatch.delenv("BLENDER_AXI_TMP", raising=False)
    assert paths.tmp_root() == REPO / ".tmp"
    assert paths.tmp_root() != Path(tempfile.gettempdir())


def test_tmp_root_installed_fallback(monkeypatch):
    monkeypatch.delenv("BLENDER_AXI_TMP", raising=False)
    monkeypatch.setattr(paths, "_checkout_root", lambda: None)
    monkeypatch.setenv("LOCALAPPDATA", str(REPO / ".tmp" / "localapp"))
    assert paths.tmp_root() == REPO / ".tmp" / "localapp" / "blender-axi" / "tmp"
    monkeypatch.delenv("LOCALAPPDATA")
    assert paths.tmp_root() == Path.home() / ".cache" / "blender-axi" / "tmp"


def test_blender_env_sets_all_three(monkeypatch):
    monkeypatch.setenv("BLENDER_AXI_TMP", str(REPO / ".tmp" / "tests"))
    env = paths.blender_env({"PATH": "p", "TEMP": "old"})
    want = str(REPO / ".tmp" / "tests" / "blender")
    assert env["TMPDIR"] == env["TEMP"] == env["TMP"] == want
    assert env["PATH"] == "p"
    assert Path(want).is_dir()


def test_blender_env_defaults_to_os_environ(monkeypatch):
    monkeypatch.setenv("BLENDER_AXI_MARK", "1")
    assert paths.blender_env()["BLENDER_AXI_MARK"] == "1"


def test_run_dir_unique_and_created():
    a, b = paths.run_dir("scene"), paths.run_dir("scene")
    try:
        assert a != b and a.is_dir() and b.is_dir()
        assert a.parent == paths.tmp_root() / "runs"
        assert a.name.startswith("scene-")
    finally:
        paths.cleanup(a, False)
        paths.cleanup(b, False)


def test_exec_copy_path():
    p = paths.exec_copy_path(Path("some/dir/my.blend"), "abc123")
    assert p == paths.tmp_root() / "exec" / "my-abc123.blend"
    assert p.parent.is_dir()


def test_cleanup_keep_and_remove():
    d = paths.run_dir("cleanup")
    (d / "f.txt").write_text("x")
    paths.cleanup(d, True)
    assert d.exists()
    paths.cleanup(d, False)
    assert not d.exists()
    paths.cleanup(d, False)  # already gone: no error


def test_only_paths_module_uses_tempfile():
    offenders = [p for p in (REPO / "src").rglob("*.py")
                 if p.name != "paths.py" and "tempfile" in p.read_text(encoding="utf-8")]
    assert offenders == []
