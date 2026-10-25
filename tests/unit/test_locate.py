from pathlib import Path
from types import SimpleNamespace

import pytest

from blender_axi import locate
from blender_axi.errors import AxiError

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def fake_exes(monkeypatch):
    """Create fake executables and a subprocess.run that answers `--version` from the file's name."""
    root = REPO / ".tmp" / "tests" / "locate"
    root.mkdir(parents=True, exist_ok=True)
    for key in ("BLENDER_AXI_BLENDER", "BLENDER_PATH"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(locate.shutil, "which", lambda name: None)
    monkeypatch.setattr(locate, "_windows_candidates", lambda: [])

    def make(name: str) -> Path:
        p = root / name
        p.write_text("")
        return p

    def fake_run(argv, **kw):
        stem = Path(argv[0]).stem
        out = {"b52": "Blender 5.2.0 LTS\n\tbuild date: x\n", "b44": "Blender 4.4.1\n",
               "bad": "not blender\n"}[stem]
        return SimpleNamespace(returncode=0, stdout=out, stderr="")

    monkeypatch.setattr(locate.subprocess, "run", fake_run)
    return make


def test_version_regex_and_string(fake_exes):
    info = locate.find_blender(str(fake_exes("b52")))
    assert info.version == (5, 2, 0)
    assert info.version_string == "5.2.0 LTS"
    assert info.path.name == "b52"
    assert locate.find_blender(str(fake_exes("b44"))).version == (4, 4, 1)


def test_explicit_wins_over_env(fake_exes, monkeypatch):
    monkeypatch.setenv("BLENDER_AXI_BLENDER", str(fake_exes("b44")))
    assert locate.find_blender(str(fake_exes("b52"))).version == (5, 2, 0)


def test_axi_env_before_lab_env(fake_exes, monkeypatch):
    monkeypatch.setenv("BLENDER_AXI_BLENDER", str(fake_exes("b44")))
    monkeypatch.setenv("BLENDER_PATH", str(fake_exes("b52")))
    assert locate.find_blender().version == (4, 4, 1)


def test_lab_env_before_path(fake_exes, monkeypatch):
    monkeypatch.setenv("BLENDER_PATH", str(fake_exes("b44")))
    monkeypatch.setattr(locate.shutil, "which", lambda name: str(fake_exes("b52")))
    assert locate.find_blender().version == (4, 4, 1)


def test_stale_lab_env_falls_through_to_path(fake_exes, monkeypatch):
    monkeypatch.setenv("BLENDER_PATH", "does/not/exist")
    monkeypatch.setattr(locate.shutil, "which", lambda name: str(fake_exes("b52")))
    assert locate.find_blender().version == (5, 2, 0)


def test_path_before_platform_dirs(fake_exes, monkeypatch):
    monkeypatch.setattr(locate.shutil, "which", lambda name: str(fake_exes("b44")))
    monkeypatch.setattr(locate, "_windows_candidates", lambda: [fake_exes("b52")])
    monkeypatch.setattr(locate, "_platform", lambda: "win32")
    assert locate.find_blender().version == (4, 4, 1)


def test_windows_glob_highest_version(monkeypatch):
    monkeypatch.setattr(locate.glob, "glob", lambda pat: [
        r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 5.10\blender.exe",
    ])
    cands = locate._windows_candidates()
    assert "5.10" in str(cands[0]) and "4.2" in str(cands[-1])


def test_windows_platform_uses_glob_result(fake_exes, monkeypatch):
    monkeypatch.setattr(locate, "_platform", lambda: "win32")
    monkeypatch.setattr(locate, "_windows_candidates", lambda: [fake_exes("b52")])
    assert locate.find_blender().version == (5, 2, 0)


def test_directory_value_resolves_exe(fake_exes):
    d = REPO / ".tmp" / "tests" / "locate" / "dir"
    d.mkdir(exist_ok=True)
    (d / "blender.exe").write_text("")
    # fake_run keys on stem "blender" -> not in table; only verify resolution helper
    assert locate._as_executable(str(d)) == d / "blender.exe"


def test_not_found_variants(fake_exes, monkeypatch):
    for kwargs in ({"explicit": "nope.exe"},):
        with pytest.raises(AxiError) as e:
            locate.find_blender(**kwargs)
        assert e.value.code == "BLENDER_NOT_FOUND"
        assert any("BLENDER_AXI_BLENDER" in h and "--blender" in h for h in e.value.help)
    monkeypatch.setenv("BLENDER_AXI_BLENDER", "nope2.exe")
    with pytest.raises(AxiError) as e:
        locate.find_blender()
    assert e.value.code == "BLENDER_NOT_FOUND"
    monkeypatch.delenv("BLENDER_AXI_BLENDER")
    monkeypatch.setattr(locate, "_platform", lambda: "linux")
    monkeypatch.setattr(locate, "LINUX_PATHS", ("/nonexistent/blender",))
    with pytest.raises(AxiError) as e:
        locate.find_blender()
    assert e.value.code == "BLENDER_NOT_FOUND"


def test_non_blender_executable_rejected(fake_exes):
    with pytest.raises(AxiError) as e:
        locate.find_blender(str(fake_exes("bad")))
    assert e.value.code == "BLENDER_NOT_FOUND"
