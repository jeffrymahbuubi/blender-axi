"""Real-Blender checks for `new` (v1.1 U3), incl. A7: `new --user-prefs` gives the same result."""

import pytest

from blender_axi import flags, paths
from blender_axi.commands import new as cmd_new
from blender_axi.commands import objects as cmd_objects
from blender_axi.errors import AxiError
from blender_axi.runner import HeadlessTransport

pytestmark = pytest.mark.blender

NEW_DIR = "new"


@pytest.fixture(scope="module")
def transport(blender_info):
    return HeadlessTransport(blender_info)


@pytest.fixture(scope="module")
def prefs_transport(blender_info):
    return HeadlessTransport(blender_info, user_prefs=True)


@pytest.fixture
def target():
    d = paths.tmp_root() / "tests" / NEW_DIR
    d.mkdir(parents=True, exist_ok=True)
    for p in d.glob("*.blend*"):
        p.unlink()
    return d


def new(transport, path, *extra, user_prefs=False):
    argv = [str(path), *extra] + (["--user-prefs"] if user_prefs else [])
    inv, _ = flags.parse(cmd_new.SPEC, argv)
    inv.transport = transport
    return cmd_new.run(inv)


def test_new_empty_then_objects_lists_nothing(transport, target, monkeypatch):
    monkeypatch.chdir(target)
    res = new(transport, "a.blend")
    assert res.data["template"] == "empty" and res.data["objects"] == 0
    assert 60 <= res.data["size_kb"] <= 120
    assert (target / "a.blend").is_file() and res.data["blender"].startswith("5.")
    inv, _ = flags.parse(cmd_objects.OBJECTS_SPEC, ["a.blend"])
    inv.transport = transport
    listing = cmd_objects.OBJECTS_SPEC.run(inv).data
    assert listing["objects"] == "0 objects in a.blend" and listing["count"] == "0 of 0 total"


def test_new_default_template_has_three_objects(transport, target):
    res = new(transport, target / "d.blend", "--template", "default")
    assert res.data["template"] == "default" and res.data["objects"] == 3


def test_rerun_is_refused_and_force_overwrites(transport, target):
    out = target / "r.blend"
    new(transport, out)
    mtime = out.stat().st_mtime_ns
    with pytest.raises(AxiError) as exc:
        new(transport, out)
    assert exc.value.code == "WRITE_REFUSED" and exc.value.exit_code == 1
    assert out.stat().st_mtime_ns == mtime
    res = new(transport, out, "--force", "--template", "default")
    assert res.data["objects"] == 3 and out.stat().st_mtime_ns != mtime


def test_new_creates_missing_parent_directory(transport, target):
    out = target / "deeper" / "x.blend"
    new(transport, out)
    assert out.is_file()
    out.unlink()
    out.parent.rmdir()


@pytest.mark.parametrize("template,objects", [("empty", 0), ("default", 3)])
def test_a7_new_with_user_prefs_gives_the_same_result(transport, prefs_transport, target, template, objects):
    plain = new(transport, target / f"plain-{template}.blend", "--template", template).data
    prefs = new(prefs_transport, target / f"prefs-{template}.blend", "--template", template, user_prefs=True).data
    assert plain["objects"] == prefs["objects"] == objects
    assert 60 <= prefs["size_kb"] <= 120 and abs(plain["size_kb"] - prefs["size_kb"]) < 10
