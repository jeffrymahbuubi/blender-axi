"""U6 against a real headless Blender. Always works on COPIES of the fixtures under .tmp/tests/."""

import shutil
from pathlib import Path

import pytest

from blender_axi import flags, paths
from blender_axi.commands import exec_cmd
from blender_axi.commands._common import display_path
from blender_axi.errors import AxiError
from blender_axi.runner import HeadlessTransport

pytestmark = pytest.mark.blender

ADD_CUBE = "bpy.ops.mesh.primitive_cube_add()\nresult = {'n': len(bpy.data.objects)}"


@pytest.fixture(scope="module")
def transport(blender_info):
    return HeadlessTransport(blender_info)


@pytest.fixture
def work(fixtures_dir):
    """A fresh working copy of basic.blend under .tmp/tests/ (never the fixture itself)."""
    d = paths.run_dir("exec-it")
    dst = d / "basic.blend"
    shutil.copy2(fixtures_dir / "basic.blend", dst)
    yield dst
    paths.cleanup(d, False)


def run(transport, argv):
    inv, _ = flags.parse(exec_cmd.SPEC, argv)
    inv.transport = transport
    return exec_cmd.run(inv)


def exec_files() -> set[str]:
    d = paths.tmp_root() / "exec"
    return {p.name for p in d.iterdir()} if d.is_dir() else set()


def count_objects(transport, blend: Path) -> int:
    out = transport.run("exec", {"code": "result = {'n': len(bpy.data.objects)}"}, blend=blend)
    return out.result["result"]["n"]


def test_read_only_code_changes_nothing(transport, work):
    mtime, before = work.stat().st_mtime_ns, exec_files()
    res = run(transport, [str(work), "--code", "result={'n': len(bpy.data.objects)}", "--allow-exec"])
    assert res.data["result"] == {"n": 5} and res.data["modified"] is False
    assert res.data["saved"] == "none (no changes)" and res.help == []
    assert work.stat().st_mtime_ns == mtime and exec_files() == before


def test_adding_a_cube_saves_a_copy_and_spares_the_original(transport, work):
    mtime = work.stat().st_mtime_ns
    res = run(transport, [str(work), "--code", ADD_CUBE, "--allow-exec"])
    assert res.data["modified"] is True and res.data["result"] == {"n": 6}
    assert "\\" not in res.data["saved"] and res.help[0].count("\\") == 0  # U10: forward slashes
    assert res.data["stdout"] == ""  # U10: Blender's `Info: Saved copy as ...` is not the user's output
    saved = Path(res.data["saved"]).resolve()
    assert saved.is_file() and saved.parent == (paths.tmp_root() / "exec").resolve() and saved != work
    assert work.stat().st_mtime_ns == mtime and count_objects(transport, work) == 5
    assert count_objects(transport, saved) == 6
    assert "backup" not in res.data
    assert res.help and "objects" in res.help[0]
    saved.unlink()


def test_direct_data_edit_is_detected(transport, work):
    code = "bpy.data.objects['Cube'].name = 'Renamed'\nresult = {'ok': True}"
    res = run(transport, [str(work), "--code", code, "--allow-exec"])
    assert res.data["modified"] is True
    Path(res.data["saved"]).unlink()


def test_out_copy_and_refuse_overwrite(transport, work):
    out = work.parent / "copy.blend"
    res = run(transport, [str(work), "--code", ADD_CUBE, "--out", str(out), "--allow-exec"])
    assert res.data["saved"] == display_path(str(out)) and count_objects(transport, out) == 6
    assert res.data["stdout"] == "" and "\\" not in res.data["saved"]
    with pytest.raises(AxiError) as e:
        run(transport, [str(work), "--code", ADD_CUBE, "--out", str(out), "--allow-exec"])
    assert e.value.code == "WRITE_REFUSED"
    res = run(transport, [str(work), "--code", ADD_CUBE, "--out", str(out), "--force", "--allow-exec"])
    assert res.data["modified"] is True
    assert count_objects(transport, work) == 5


def test_in_place_changes_the_working_copy_and_reports_backup(transport, work):
    before = work.stat().st_mtime_ns
    res = run(transport, [str(work), "--code", ADD_CUBE, "--in-place", "--allow-exec"])
    assert res.data["modified"] is True and Path(res.data["saved"]).resolve() == work.resolve()
    assert res.data["stdout"] == "" and "\\" not in res.data["saved"]
    assert work.stat().st_mtime_ns != before and count_objects(transport, work) == 6
    # Q6: Blender keeps the previous file as <name>.blend1; the command must report it.
    backup = work.with_name(work.name + "1")
    assert backup.is_file() and res.data["backup"] == display_path(str(backup)) and "\\" not in res.data["backup"]
    assert count_objects(transport, backup) == 5


def test_in_place_without_changes_writes_nothing(transport, work):
    mtime = work.stat().st_mtime_ns
    res = run(transport, [str(work), "--code", "result={}", "--in-place", "--allow-exec"])
    assert res.data["modified"] is False and "backup" not in res.data
    assert work.stat().st_mtime_ns == mtime and not work.with_name(work.name + "1").exists()


def test_script_file_and_stdout(transport, work):
    script = work.parent / "s.py"
    script.write_text("print('hello from blender')\nresult={'objs': sorted(o.name for o in bpy.data.objects)}\n")
    res = run(transport, [str(work), "--script", str(script), "--allow-exec"])
    assert res.data["stdout"] == "hello from blender\n"
    assert res.data["result"]["objs"] == ["Anchor", "Ball", "Camera", "Cube", "Light"]
    res = run(transport, [str(work), "--code", "print('mine')\n" + ADD_CUBE, "--allow-exec"])  # modifies -> saves
    assert res.data["stdout"] == "mine\n" and res.data["modified"] is True  # only the user's print, no save chatter
    Path(res.data["saved"]).unlink()


def test_raising_code_is_exec_failed_and_original_untouched(transport, work):
    mtime = work.stat().st_mtime_ns
    with pytest.raises(AxiError) as e:
        run(transport, [str(work), "--code", "bpy.data.objects.remove(bpy.data.objects['Cube'])\n1/0", "--allow-exec"])
    assert e.value.code == "EXEC_FAILED" and "ZeroDivisionError" in e.value.message
    assert e.value.detail and "ZeroDivisionError" in e.value.detail
    assert work.stat().st_mtime_ns == mtime


def test_non_dict_result_is_exec_failed(transport, work):
    with pytest.raises(AxiError) as e:
        run(transport, [str(work), "--code", "result = [1, 2]", "--allow-exec"])
    assert e.value.code == "EXEC_FAILED" and "must be a dict" in e.value.message


def test_gate_does_not_start_blender(work):
    class Boom:
        def run(self, *a, **k):
            raise AssertionError("transport used without --allow-exec")

    with pytest.raises(AxiError) as e:
        run(Boom(), [str(work), "--code", "result={}"])
    assert e.value.exit_code == 2


FRAME_CODE = "bpy.context.scene.frame_set(10)\nresult = {'f': bpy.context.scene.frame_current}"


def test_frame_set_alone_is_not_a_modification(transport, work):
    """v1.1 K4: moving the current frame reports `frame_changed`, never `modified`, and writes no copy."""
    mtime, before = work.stat().st_mtime_ns, exec_files()
    res = run(transport, [str(work), "--code", FRAME_CODE, "--allow-exec"])
    assert res.data["result"] == {"f": 10} and res.data["modified"] is False
    assert res.data["frame_changed"] == "1 -> 10" and res.data["saved"] == "none (no changes)"
    assert work.stat().st_mtime_ns == mtime and exec_files() == before and res.help == []
    assert list(res.data)[-2:] == ["frame_changed", "duration_ms"]


def test_frame_set_plus_a_real_change_is_both(transport, work):
    code = FRAME_CODE + "\nbpy.data.objects['Cube'].name = 'Renamed'"
    res = run(transport, [str(work), "--code", code, "--allow-exec"])
    assert res.data["modified"] is True and res.data["frame_changed"] == "1 -> 10"
    saved = Path(res.data["saved"]).resolve()
    assert saved.is_file()
    saved.unlink()


def test_no_frame_move_has_no_frame_changed_key(transport, work):
    res = run(transport, [str(work), "--code", "result={'n': 1}", "--allow-exec"])
    assert "frame_changed" not in res.data


@pytest.mark.parametrize("code", ["pass", "result = {}"])
def test_empty_result_is_the_none_string(transport, work, code):
    res = run(transport, [str(work), "--code", code, "--allow-exec"])
    assert res.data["result"] == "none (the code did not assign result)"
