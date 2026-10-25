"""Packaging check (v1.1 U4): build the wheel + sdist from the LOCAL tree, inspect them, install the wheel into a
throw-away venv and run it from there. No network beyond what `uv build` needs for its isolated build env
(setuptools comes from the uv cache; when that needs the network and the machine is offline the module skips).

All output lives under <repo>/.tmp/build/ (never dist/ or build/ in the repo root). blender-axi is NOT published to
PyPI; this test only proves the artefacts are correct and usable.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from blender_axi import __version__

REPO = Path(__file__).resolve().parents[2]
BUILD = REPO / ".tmp" / "build"
DIST = BUILD / "dist"
VENV = BUILD / "venv"

pytestmark = pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not on PATH")

NETWORK_HINT = re.compile(r"dns error|failed to fetch|error sending request|connection (refused|reset|timed out)|"
                          r"network|offline|tcp connect|timed out", re.I)
FORBIDDEN_PREFIXES = ("tests/", "docs/", ".tmp", "benchmarks/", "skills/", ".claude", "scripts/")


def _run(argv, *, env=None, cwd=None, timeout=600):
    return subprocess.run([str(a) for a in argv], capture_output=True, text=True, timeout=timeout,
                          env=env, cwd=str(cwd) if cwd else None)


def _venv_bin(name: str) -> Path:
    sub = "Scripts" if sys.platform == "win32" else "bin"
    exe = ".exe" if sys.platform == "win32" else ""
    return VENV / sub / f"{name}{exe}"


def _clean_env() -> dict[str, str]:
    """The environment for running the INSTALLED tool: no PYTHONPATH leakage from the checkout."""
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")}
    env["BLENDER_AXI_TMP"] = str(REPO / ".tmp" / "tests")
    return env


@pytest.fixture(scope="module")
def built():
    """Build once into .tmp/build/dist; returns (wheel_path, sdist_path)."""
    if BUILD.exists():
        shutil.rmtree(BUILD, ignore_errors=True)
    DIST.mkdir(parents=True)
    proc = _run(["uv", "build", "--out-dir", DIST, REPO])
    if proc.returncode != 0:
        text = proc.stdout + proc.stderr
        if NETWORK_HINT.search(text):
            pytest.skip(f"uv build needs the network for its isolated build env (offline?): {text.strip()[-300:]}")
        pytest.fail(f"uv build failed ({proc.returncode}):\n{text[-2000:]}")
    wheels, sdists = sorted(DIST.glob("*.whl")), sorted(DIST.glob("*.tar.gz"))
    assert len(wheels) == 1 and len(sdists) == 1, f"unexpected build output: {sorted(p.name for p in DIST.iterdir())}"
    return wheels[0], sdists[0]


@pytest.fixture(scope="module")
def wheel_names(built):
    with zipfile.ZipFile(built[0]) as z:
        return sorted(z.namelist())


def _wheel_text(wheel: Path, suffix: str) -> str:
    with zipfile.ZipFile(wheel) as z:
        match = [n for n in z.namelist() if n.endswith(suffix)]
        assert len(match) == 1, f"expected one {suffix}, got {match}"
        return z.read(match[0]).decode("utf-8")


def test_build_produces_one_wheel_and_one_sdist_with_the_package_version(built):
    wheel, sdist = built
    assert wheel.name == f"blender_axi-{__version__}-py3-none-any.whl"
    assert sdist.name == f"blender_axi-{__version__}.tar.gz"
    assert not (REPO / "dist").exists() and not (REPO / "build").exists()  # output stays under .tmp/build


def test_wheel_contains_every_package_module(wheel_names):
    expected = {p.relative_to(REPO / "src").as_posix() for p in (REPO / "src" / "blender_axi").rglob("*.py")}
    assert "blender_axi/blender_side/bootstrap.py" in expected
    assert any(e.startswith("blender_axi/blender_side/op_") for e in expected)
    missing = expected - set(wheel_names)
    assert not missing, f"missing from the wheel: {sorted(missing)}"


def test_wheel_has_license_entry_point_and_metadata(built, wheel_names):
    wheel = built[0]
    assert any(re.fullmatch(r"[^/]+\.dist-info/(licenses/)?LICENSE", n) for n in wheel_names), wheel_names
    entry = _wheel_text(wheel, ".dist-info/entry_points.txt")
    assert "blender-axi = blender_axi.cli:main" in entry
    meta = _wheel_text(wheel, ".dist-info/METADATA")
    assert "Name: blender-axi" in meta and f"Version: {__version__}" in meta
    assert "License-Expression: GPL-3.0-or-later" in meta
    assert "Requires-Python: >=3.13" in meta
    assert re.search(r"^Project-URL: ", meta, re.M)
    assert "pypi.org" not in meta.lower()


def test_wheel_ships_only_the_package(wheel_names):
    assert not [n for n in wheel_names if n.startswith(FORBIDDEN_PREFIXES)], wheel_names
    assert all(n.startswith("blender_axi/") or ".dist-info/" in n for n in wheel_names), wheel_names


def test_sdist_contents(built):
    sdist = built[1]
    root = sdist.name.removesuffix(".tar.gz")
    with tarfile.open(sdist) as t:
        names = [n.removeprefix(root + "/") for n in t.getnames()]
    for must in ("LICENSE", "README.md", "pyproject.toml", "src/blender_axi/cli.py"):
        assert must in names, f"{must} missing from sdist"
    assert not [n for n in names if n.startswith((".claude", "docs", ".tmp"))], names


@pytest.fixture(scope="module")
def installed(built):
    """The wheel installed into .tmp/build/venv (offline: the package has no dependencies)."""
    proc = _run(["uv", "venv", VENV, "--python", "3.13"])
    if proc.returncode != 0:
        pytest.skip(f"cannot create a CPython 3.13 venv: {(proc.stdout + proc.stderr).strip()[-300:]}")
    proc = _run(["uv", "pip", "install", "--python", _venv_bin("python"), built[0]])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return _venv_bin("blender-axi")


def test_installed_cli_reports_the_version(installed):
    proc = _run([installed, "--version"], env=_clean_env())
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().endswith(__version__), proc.stdout


def test_installed_package_is_not_in_checkout_mode(installed):
    proc = _run([_venv_bin("python"), "-I", "-c",
                 "from blender_axi import paths, __file__ as f; print(paths._checkout_root()); print(f)"],
                env=_clean_env(), cwd=BUILD)
    assert proc.returncode == 0, proc.stderr
    first, second = proc.stdout.splitlines()[:2]
    assert first == "None"
    assert "site-packages" in Path(second).as_posix()


def test_installed_home_reports_version_and_upgrade_hint(installed, tmp_path):
    proc = _run([installed, "--json"], env=_clean_env(), cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    assert data["version"] == __version__
    assert any("uv tool upgrade blender-axi" in h for h in data["help"]), data["help"]


def test_installed_help_works_offline(installed):
    proc = _run([installed, "scene", "--help"], env=_clean_env())
    assert proc.returncode == 0 and "scene" in proc.stdout


@pytest.mark.blender
def test_installed_scene_runs_blender_bootstrap_from_site_packages(installed, fixtures_dir):
    proc = _run([installed, "--json", "scene", str(fixtures_dir / "basic.blend")], env=_clean_env(), timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    assert data["scene"] == "Scene" and data["objects"].startswith("5 (")


@pytest.mark.blender
def test_installed_objects_live_against_a_real_headless_server(installed, blender_info, fixtures_dir):
    from test_live_optional import real_server  # binds a free port, never 9876; kills only its own process tree

    with real_server(blender_info, fixtures_dir / "basic.blend") as (port, _log):
        assert port != 9876
        proc = _run([installed, "--json", "--live", "--host", "127.0.0.1", "--port", str(port), "objects"],
                    env=_clean_env(), timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    assert data["count"] == "5 of 5 total"
