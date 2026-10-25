"""Shared test harness (lead-owned, plan section 4 step 6).

All temp output goes under <repo>/.tmp/tests; nothing may land in the host's system temp.
"""

import os
import subprocess
import sys
import tempfile
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TMP = REPO / ".tmp" / "tests"
TMP.mkdir(parents=True, exist_ok=True)
os.environ["BLENDER_AXI_TMP"] = str(TMP)

# Fallback when the package is not installed (e.g. a fresh worktree): import from src/.
_src = str(REPO / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

HOST_TEMP = Path(tempfile.gettempdir())
FIXTURES = REPO / ".tmp" / "fixtures"


@pytest.fixture(scope="session")
def blender_info():
    """BlenderInfo for the installed Blender, or skip the test."""
    from blender_axi.errors import AxiError
    from blender_axi.locate import find_blender

    try:
        return find_blender()
    except AxiError as err:
        pytest.skip(f"Blender not found: {err}")


@pytest.fixture(scope="session")
def fixtures_dir(blender_info):
    """Generate basic.blend and empty.blend into .tmp/fixtures once per session.

    Contract: `blender --background --factory-startup --python tests/fixtures/make_fixtures.py -- <out_dir>`.
    """
    from blender_axi import paths

    if not (FIXTURES / "basic.blend").exists() or not (FIXTURES / "empty.blend").exists():
        FIXTURES.mkdir(parents=True, exist_ok=True)
        script = REPO / "tests" / "fixtures" / "make_fixtures.py"
        proc = subprocess.run(
            [str(blender_info.path), "--background", "--factory-startup", "--python-exit-code", "3",
             "--python", str(script), "--", str(FIXTURES)],
            env=paths.blender_env(), cwd=str(TMP), capture_output=True, text=True, timeout=300,
        )
        if proc.returncode != 0:
            pytest.fail(f"make_fixtures failed ({proc.returncode}):\n{proc.stderr[-2000:]}")
    return FIXTURES


@pytest.fixture
def fake_bpy(monkeypatch):
    """A stub `bpy` module in sys.modules; tests attach whatever attributes they need."""
    mod = types.ModuleType("bpy")
    mod.app = types.SimpleNamespace(version=(5, 2, 0), version_string="5.2.0 LTS", tempdir=str(TMP))
    mod.data = types.SimpleNamespace(objects=[], collections=[], materials=[], scenes=[], filepath="", is_dirty=False)
    mod.context = types.SimpleNamespace(scene=None)
    mod.ops = types.SimpleNamespace()
    monkeypatch.setitem(sys.modules, "bpy", mod)
    return mod


def _host_blender_entries() -> set[str]:
    try:
        return {p.name for p in HOST_TEMP.iterdir() if p.name.lower().startswith("blender")}
    except OSError:
        return set()


@pytest.fixture(autouse=True)
def _no_host_temp_garbage(request):
    """For Blender-backed tests, assert nothing new named blender* appears in the system temp."""
    if request.node.get_closest_marker("blender") is None:
        yield
        return
    before = _host_blender_entries()
    yield
    new = _host_blender_entries() - before
    assert not new, f"Blender wrote to the system temp {HOST_TEMP}: {sorted(new)}"
