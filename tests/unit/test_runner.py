import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from blender_axi import paths, runner
from blender_axi.blender_side import bootstrap, ops
from blender_axi.errors import AxiError
from blender_axi.locate import BlenderInfo
from blender_axi.runner import HeadlessTransport, SaveSpec

REPO = Path(__file__).resolve().parents[2]
INFO = BlenderInfo(Path("C:/fake/blender.exe"), (5, 2, 0), "5.2.0 LTS")


class FakeBlender:
    """Replaces subprocess.run: records argv/env/cwd and optionally writes a response file."""

    def __init__(self, response=None, returncode=0, stderr="", raises=None):
        self.response, self.returncode, self.stderr, self.raises = response, returncode, stderr, raises
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        if self.raises:
            raise self.raises
        request = json.loads(Path(argv[argv.index("--request") + 1]).read_text())
        self.request = request
        if self.response is not None:
            Path(request["response_path"]).write_text(json.dumps(self.response))
        return SimpleNamespace(returncode=self.returncode, stdout="", stderr=self.stderr)


OK = {"protocol": 1, "ok": True, "result": {"a": 1}, "modified": True, "saved": "x.blend",
      "blender_version": "5.2.0 LTS", "duration_ms": 12, "stdout": "hi", "warnings": ["w"]}


@pytest.fixture
def blend():
    p = REPO / ".tmp" / "tests" / "unit_runner.blend"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")
    return p


def use(monkeypatch, fake):
    monkeypatch.setattr(runner.subprocess, "run", fake)
    return fake


def test_argv_exact(monkeypatch, blend):
    fake = use(monkeypatch, FakeBlender(OK))
    HeadlessTransport(INFO, keep_tmp=True).run("scene_summary", {"x": 1}, blend=blend)
    argv, kw = fake.calls[0]
    req = argv[-1]
    assert argv == [str(INFO.path), "--background", "--factory-startup", str(blend), "--python-exit-code", "3",
                    "--python", str(runner.BOOTSTRAP), "--", "--request", req]
    assert Path(req).name == "request.json" and Path(req).parent.parent.name == "runs"
    assert kw["env"]["TEMP"] == kw["env"]["TMP"] == kw["env"]["TMPDIR"] == str(paths.blender_tmp())
    assert kw["cwd"] == str(Path(req).parent) and kw["capture_output"] is True
    paths.cleanup(Path(req).parent, False)


def test_argv_without_blend(monkeypatch):
    fake = use(monkeypatch, FakeBlender(OK))
    HeadlessTransport(INFO).run("ping", {}, blend=None)
    argv = fake.calls[0][0]
    assert argv[1:4] == ["--background", "--factory-startup", "--python-exit-code"]


def test_request_file_and_outcome(monkeypatch, blend):
    fake = use(monkeypatch, FakeBlender(OK))
    out = HeadlessTransport(INFO).run("ping", {"k": "v"}, blend=blend, save=SaveSpec("copy", "o.blend"))
    r = fake.request
    assert r["protocol"] == 1 and r["op"] == "ping" and r["params"] == {"k": "v"}
    assert r["save"] == {"mode": "copy", "path": "o.blend"}
    assert Path(r["response_path"]).name == "response.json"
    assert out.result == {"a": 1} and out.modified is True and out.saved == "x.blend"
    assert out.blender_version == "5.2.0 LTS" and out.duration_ms == 12
    assert out.stdout == "hi" and out.warnings == ["w"]


def test_default_savespec_is_none(monkeypatch):
    fake = use(monkeypatch, FakeBlender(OK))
    HeadlessTransport(INFO).run("ping", {}, blend=None)
    assert fake.request["save"] == {"mode": "none", "path": None}


def test_timeouts(monkeypatch):
    fake = use(monkeypatch, FakeBlender(OK))
    t = HeadlessTransport(INFO)
    t.run("ping", {}, blend=None)
    t.run("render", {}, blend=None)
    t.run("ping", {}, blend=None, timeout=7)
    assert [c[1]["timeout"] for c in fake.calls] == [120.0, 600.0, 7]


def test_timeout_maps_to_axierror(monkeypatch):
    use(monkeypatch, FakeBlender(raises=subprocess.TimeoutExpired("blender", 1)))
    with pytest.raises(AxiError) as e:
        HeadlessTransport(INFO).run("ping", {}, blend=None, timeout=1)
    assert e.value.code == "TIMEOUT" and any("--timeout" in h for h in e.value.help)


def test_nonzero_exit_is_blender_failed(monkeypatch):
    use(monkeypatch, FakeBlender(OK, returncode=3, stderr="Traceback boom"))
    with pytest.raises(AxiError) as e:
        HeadlessTransport(INFO).run("ping", {}, blend=None)
    assert e.value.code == "BLENDER_FAILED" and "boom" in e.value.detail
    assert any("--debug" in h for h in e.value.help)


def test_missing_response_is_blender_failed(monkeypatch):
    use(monkeypatch, FakeBlender(None, returncode=0, stderr="tail"))
    with pytest.raises(AxiError) as e:
        HeadlessTransport(INFO).run("ping", {}, blend=None)
    assert e.value.code == "BLENDER_FAILED" and e.value.detail == "tail"


def test_stderr_tail_is_bounded(monkeypatch):
    use(monkeypatch, FakeBlender(None, returncode=1, stderr="x" * 10000))
    with pytest.raises(AxiError) as e:
        HeadlessTransport(INFO).run("ping", {}, blend=None)
    assert len(e.value.detail) == runner.STDERR_TAIL


def test_ok_false_maps_code_message(monkeypatch):
    bad = {"protocol": 1, "ok": False, "error": {"code": "EXEC_FAILED", "message": "nope", "traceback": "tb"}}
    use(monkeypatch, FakeBlender(bad))
    with pytest.raises(AxiError) as e:
        HeadlessTransport(INFO).run("exec", {}, blend=None)
    assert (e.value.code, e.value.message, e.value.detail) == ("EXEC_FAILED", "nope", "tb")


def test_missing_blend_is_not_found(monkeypatch):
    fake = use(monkeypatch, FakeBlender(OK))
    with pytest.raises(AxiError) as e:
        HeadlessTransport(INFO).run("ping", {}, blend=Path("nope.blend"))
    assert e.value.code == "NOT_FOUND" and fake.calls == []


def test_non_5x_adds_warning(monkeypatch):
    use(monkeypatch, FakeBlender(OK))
    info = BlenderInfo(INFO.path, (4, 4, 1), "4.4.1")
    out = HeadlessTransport(info).run("ping", {}, blend=None)
    assert "Blender 4.4 untested; 5.2 LTS verified" in out.warnings


def test_run_dir_cleanup_and_keep(monkeypatch):
    use(monkeypatch, FakeBlender(OK))
    runs = paths.tmp_root() / "runs"
    before = set(runs.iterdir()) if runs.exists() else set()
    HeadlessTransport(INFO).run("keepcheck", {}, blend=None)
    assert set(runs.iterdir()) == before
    HeadlessTransport(INFO, keep_tmp=True).run("keepcheck", {}, blend=None)
    kept = set(runs.iterdir()) - before
    assert len(kept) == 1
    for d in kept:
        paths.cleanup(d, False)


def test_run_dir_cleaned_on_failure(monkeypatch):
    use(monkeypatch, FakeBlender(raises=subprocess.TimeoutExpired("b", 1)))
    runs = paths.tmp_root() / "runs"
    before = set(runs.iterdir()) if runs.exists() else set()
    with pytest.raises(AxiError):
        HeadlessTransport(INFO).run("failclean", {}, blend=None)
    assert set(runs.iterdir()) == before


# ---- ops + bootstrap (fake bpy) ----

def test_ops_unknown_op():
    with pytest.raises(ops.OpError) as e:
        ops.run("nope", {})
    assert e.value.code == "OP_FAILED" and "nope" in e.value.message


@pytest.mark.parametrize("name", ["../x", "Ping", "", "a.b"])
def test_ops_rejects_bad_names(name):
    with pytest.raises(ops.OpError):
        ops.run(name, {})


def test_ops_nested_import_error_not_masked(monkeypatch):
    # op_ping imports bpy inside run(); with no bpy the ImportError must surface, not "unknown op"
    monkeypatch.setitem(sys.modules, "bpy", None)
    with pytest.raises(ImportError):
        ops.run("ping", {})


def test_op_ping_with_fake_bpy(fake_bpy):
    fake_bpy.app.tempdir = str(REPO / ".tmp" / "tests" / "blender" / "blender_123") + "/"
    fake_bpy.data.filepath = "a.blend"
    res = ops.run("ping", {})
    assert res["blender_version"] == "5.2.0 LTS" and res["file"] == "a.blend"
    assert res["python"].startswith("3.") and res["tempdir"].endswith("blender")


def _request(tmp: Path, op: str, save: dict | None = None) -> tuple[Path, Path]:
    tmp.mkdir(parents=True, exist_ok=True)
    resp = tmp / "response.json"
    req = tmp / "request.json"
    req.write_text(json.dumps({"protocol": 1, "op": op, "params": {}, "response_path": str(resp),
                               "save": save or {"mode": "none", "path": None}}))
    return req, resp


def test_bootstrap_ok_not_dirty_never_saves(fake_bpy):
    calls = []
    fake_bpy.ops = SimpleNamespace(wm=SimpleNamespace(save_as_mainfile=lambda **k: calls.append(k),
                                                      save_mainfile=lambda **k: calls.append(k)))
    out = REPO / ".tmp" / "tests" / "bootstrap_ok"
    req, resp = _request(out, "ping", {"mode": "copy", "path": str(out / "copy.blend")})
    assert bootstrap.main(["--request", str(req)]) == 0
    data = json.loads(resp.read_text())
    assert data["ok"] is True and data["modified"] is False and data["saved"] is None
    assert data["result"]["blender_version"] == "5.2.0 LTS" and data["blender_version"] == "5.2.0 LTS"
    assert calls == []


def test_bootstrap_copy_only_when_dirty(fake_bpy):
    calls = []
    fake_bpy.ops = SimpleNamespace(wm=SimpleNamespace(save_as_mainfile=lambda **k: calls.append(k)))
    fake_bpy.data.is_dirty = True
    out = REPO / ".tmp" / "tests" / "bootstrap_dirty"
    target = str(out / "sub" / "copy.blend")
    req, resp = _request(out, "ping", {"mode": "copy", "path": target})
    bootstrap.main(["--request", str(req)])
    data = json.loads(resp.read_text())
    assert data["modified"] is True and data["saved"] == target
    assert calls == [{"filepath": target, "copy": True}]


def test_bootstrap_in_place_when_dirty(fake_bpy):
    calls = []
    fake_bpy.ops = SimpleNamespace(wm=SimpleNamespace(save_mainfile=lambda **k: calls.append(k)))
    fake_bpy.data.is_dirty = True
    fake_bpy.data.filepath = "orig.blend"
    req, resp = _request(REPO / ".tmp" / "tests" / "bootstrap_inplace", "ping", {"mode": "in_place", "path": None})
    bootstrap.main(["--request", str(req)])
    data = json.loads(resp.read_text())
    assert calls == [{}] and data["saved"] == "orig.blend"


def test_bootstrap_unknown_op_is_data(fake_bpy):
    req, resp = _request(REPO / ".tmp" / "tests" / "bootstrap_unknown", "nope")
    assert bootstrap.main(["--request", str(req)]) == 0
    data = json.loads(resp.read_text())
    assert data["ok"] is False and data["error"]["code"] == "OP_FAILED"
    assert "unknown op" in data["error"]["message"] and "traceback" in data["error"]


def test_bootstrap_unexpected_exception_is_op_failed(fake_bpy, monkeypatch):
    monkeypatch.setattr(ops, "run", lambda op, params: 1 / 0)
    req, resp = _request(REPO / ".tmp" / "tests" / "bootstrap_boom", "ping")
    assert bootstrap.main(["--request", str(req)]) == 0
    data = json.loads(resp.read_text())
    assert data["error"]["code"] == "OP_FAILED" and "division by zero" in data["error"]["message"]
    assert "ZeroDivisionError" in data["error"]["traceback"]


def test_bootstrap_opError_code_preserved(fake_bpy, monkeypatch):
    def boom(op, params):
        raise ops.OpError("NOT_FOUND", "no such object")

    monkeypatch.setattr(ops, "run", boom)
    req, resp = _request(REPO / ".tmp" / "tests" / "bootstrap_ope", "ping")
    bootstrap.main(["--request", str(req)])
    err = json.loads(resp.read_text())["error"]
    assert (err["code"], err["message"]) == ("NOT_FOUND", "no such object")


def test_bootstrap_fingerprint_change_counts_as_modified(fake_bpy, monkeypatch):
    calls = []
    fake_bpy.ops = SimpleNamespace(wm=SimpleNamespace(save_as_mainfile=lambda **k: calls.append(k)))
    prints = iter(["a", "b"])
    monkeypatch.setattr(bootstrap, "fingerprint", lambda bpy: next(prints))
    out = REPO / ".tmp" / "tests" / "bootstrap_fp"
    target = str(out / "copy.blend")
    req, resp = _request(out, "ping", {"mode": "copy", "path": target})
    bootstrap.main(["--request", str(req)])
    data = json.loads(resp.read_text())
    assert data["modified"] is True and data["saved"] == target and calls == [{"filepath": target, "copy": True}]


def test_bootstrap_fingerprint_skipped_when_not_saving(fake_bpy, monkeypatch):
    monkeypatch.setattr(bootstrap, "fingerprint", lambda bpy: pytest.fail("fingerprint computed for mode none"))
    req, resp = _request(REPO / ".tmp" / "tests" / "bootstrap_nofp", "ping")
    bootstrap.main(["--request", str(req)])
    assert json.loads(resp.read_text())["modified"] is False


def test_bootstrap_captures_op_stdout(fake_bpy, monkeypatch, capsys):
    def noisy(op, params):
        print("hello from op")
        return {}

    monkeypatch.setattr(ops, "run", noisy)
    req, resp = _request(REPO / ".tmp" / "tests" / "bootstrap_stdout", "ping")
    bootstrap.main(["--request", str(req)])
    assert json.loads(resp.read_text())["stdout"] == "hello from op\n"
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("mode", ["copy", "in_place"])
def test_bootstrap_save_chatter_is_not_op_stdout(fake_bpy, monkeypatch, capsys, mode):
    """U10: Blender's own `Info: Saved copy as ...` must not land in `stdout`; user output still does."""
    def noisy(op, params):
        print("hello from op")
        return {}

    chatty = lambda **k: print(f"Info: Saved copy as {k.get('filepath', 'x.blend')}")  # noqa: E731
    fake_bpy.ops = SimpleNamespace(wm=SimpleNamespace(save_as_mainfile=chatty, save_mainfile=chatty))
    fake_bpy.data.is_dirty = True
    fake_bpy.data.filepath = "orig.blend"
    monkeypatch.setattr(ops, "run", noisy)
    out = REPO / ".tmp" / "tests" / f"bootstrap_chatter_{mode}"
    req, resp = _request(out, "ping", {"mode": mode, "path": str(out / "copy.blend") if mode == "copy" else None})
    bootstrap.main(["--request", str(req)])
    data = json.loads(resp.read_text())
    assert data["stdout"] == "hello from op\n" and data["saved"] is not None
    assert capsys.readouterr().out == ""


def test_relative_blend_is_made_absolute(monkeypatch, blend):
    fake = use(monkeypatch, FakeBlender(OK))
    monkeypatch.chdir(blend.parent)
    HeadlessTransport(INFO).run("ping", {}, blend=Path(blend.name))
    argv = fake.calls[0][0]
    assert argv[argv.index("--factory-startup") + 1] == str(blend)
    assert Path(argv[3]).is_absolute()
