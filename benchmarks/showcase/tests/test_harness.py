"""U1 harness tests: argv recipes, isolation, a run against tests/fake_claude.py, snapshots, stop mapping, dry run.

No real `claude`, no API, no Blender. Temp files live under blender_axi paths.tmp_root()/showcase/tests.
"""

import json
import re
import shutil
import socket
import sys
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

TESTS = Path(__file__).resolve().parent
SHOWCASE = TESTS.parent
REPO = SHOWCASE.parents[1]
for _p in (str(REPO / "src"), str(REPO / "benchmarks"), str(SHOWCASE)):  # last inserted ends up first
    if _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)

import pytest  # noqa: E402

import run_ab  # noqa: E402
from blender_axi import paths  # noqa: E402
from bench.server import RESERVED_PORT  # noqa: E402
from sc import arms, harness, record  # noqa: E402

FAKE = TESTS / "fake_claude.py"
DUMMY_KEY = "sk-ant-test-0123456789abcdef"  # scan:allow (fake test key)


@dataclass(frozen=True)
class Proj:
    name: str
    title: str
    start: str
    prompt: str
    dir: Path


@pytest.fixture
def work():
    root = paths.tmp_root() / "showcase" / "tests" / f"harness-{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True)
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("BLENDER_AXI_ANTHROPIC_KEY", DUMMY_KEY)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token-should-not-leak")
    monkeypatch.delenv("FAKE_CLAUDE_MODE", raising=False)
    monkeypatch.delenv("FAKE_CLAUDE_COMMANDS", raising=False)


def make_project(work: Path, name="proj") -> Proj:
    pdir = work / "projects" / name
    pdir.mkdir(parents=True)
    (pdir / "prompt.md").write_text("Edit `{WORK}` and write under `{OUT}`.\n", encoding="utf-8")
    start = work / "scenes" / name / "start.blend"
    start.parent.mkdir(parents=True)
    start.write_bytes(b"BLENDER-dummy-start")
    return Proj(name, "Proj", "empty", "unused", pdir)


def stub_projects(checks=None):
    calls = []

    def run_check(project, blender, out_dir):
        calls.append((project.name, Path(out_dir)))
        return checks or {"passed": True, "checks": {"x": True}, "notes": []}

    return SimpleNamespace(names=lambda: ["proj"], load=lambda n: None, prepare_start=lambda p, t, b: None,
                           run_check=run_check, calls=calls)


def make_cfg(work: Path, **kw) -> harness.Config:
    meta = {"claude_version": "2.1.317", "blender": "5.2.0", "mcp_server": {"version": "1.0.2", "commit": "dbbf836"}}
    base = dict(tmp=work, claude=[sys.executable, str(FAKE)], axi_bin=work / "bin", blender=Path("blender.exe"),
                projects=stub_projects(), mcp_exe=Path("blender-mcp.exe"), skill=Path("SKILL.md"), meta=meta)
    base.update(kw)
    return harness.Config(**base)

# --- argv recipes (plan 3.2) ---


def test_axi_argv_is_exact(work):
    proj = make_project(work)
    cfg = make_cfg(work, claude=["claude"])
    assert harness.argv_for(proj, "axi", cfg) == [
        "claude", "--tools", "Bash,Read,Write", "--allowedTools", "Bash(blender-axi *),Read,Write",
        "--append-system-prompt-file", "SKILL.md",
        "-p", "--bare", "--output-format", "stream-json", "--verbose", "--strict-mcp-config",
        "--no-session-persistence", "--disable-slash-commands", "--max-turns", "40", "--max-budget-usd", "3",
        "--model", "claude-opus-5-5", "--effort", "medium",
        "Edit `work.blend` and write under `out`."]


def test_mcp_argv_is_exact(work):
    proj = make_project(work)
    cfg = make_cfg(work, claude=["claude"], mcp_exe=Path("blender-mcp.exe"))
    argv = harness.argv_for(proj, "mcp", cfg, port=51234)
    config = json.dumps({"mcpServers": {"blender": {"command": "blender-mcp.exe", "env": {
        "BLENDER_MCP_HOST": "127.0.0.1", "BLENDER_MCP_PORT": "51234"}}}})
    assert argv == [
        "claude", "--tools", "Read,Write", "--allowedTools", "mcp__blender__*,Read,Write", "--mcp-config", config,
        "-p", "--bare", "--output-format", "stream-json", "--verbose", "--strict-mcp-config",
        "--no-session-persistence", "--disable-slash-commands", "--max-turns", "40", "--max-budget-usd", "3",
        "--model", "claude-opus-5-5", "--effort", "medium",
        "Edit `work.blend` and write under `out`."]
    assert str(RESERVED_PORT) not in config


def test_arms_differ_only_in_instrument_flags(work):
    proj = make_project(work)
    cfg = make_cfg(work, claude=["claude"])
    axi, mcp = harness.argv_for(proj, "axi", cfg), harness.argv_for(proj, "mcp", cfg, port=50000)
    only_axi, only_mcp = set(axi) - set(mcp), set(mcp) - set(axi)
    assert only_axi == {"Bash,Read,Write", "Bash(blender-axi *),Read,Write", "--append-system-prompt-file", "SKILL.md"}
    assert only_mcp == {"Read,Write", "mcp__blender__*,Read,Write", "--mcp-config", mcp[mcp.index("--mcp-config") + 1]}
    assert axi[-1] == mcp[-1]  # byte-identical prompt


def test_prompt_is_last_and_not_swallowed_by_a_variadic_option(work):
    proj = make_project(work)
    cfg = make_cfg(work, claude=["claude"])
    variadic = {"--tools", "--allowedTools", "--mcp-config"}
    for arm in ("axi", "mcp"):
        argv = harness.argv_for(proj, arm, cfg, port=50000)
        assert argv[-3] == "--effort"  # a plain option (with its value) right before the prompt
        for i, item in enumerate(argv[:-1]):
            if item in variadic:  # exactly one value, then the next token is another option
                assert argv[i + 1] and not argv[i + 1].startswith("-")
                assert argv[i + 2].startswith("-")


def test_no_bare_fallback():
    flags = arms.common_flags(bare=False)
    assert "--bare" not in flags and flags[flags.index("--setting-sources") + 1] == ""

# --- environment isolation (K2) ---


def test_child_env_needs_the_key(work, monkeypatch):
    monkeypatch.delenv("BLENDER_AXI_ANTHROPIC_KEY", raising=False)
    with pytest.raises(arms.SetupError, match="BLENDER_AXI_ANTHROPIC_KEY"):
        arms.child_env(work, work / "bin")


def test_child_env_isolation(work, key):
    env = arms.child_env(work, work / "bin")
    assert "ANTHROPIC_AUTH_TOKEN" not in env and "BLENDER_AXI_ANTHROPIC_KEY" not in env
    assert env["ANTHROPIC_API_KEY"] == DUMMY_KEY
    assert Path(env["CLAUDE_CONFIG_DIR"]).parent == work
    assert env["PATH"].startswith(str(work / "bin"))
    assert Path(env["TEMP"]).is_relative_to(work)
    assert arms.mask_key() == f"<set,len {len(DUMMY_KEY)}>"

# --- a full run against the stand-in ---


def test_run_one_axi_writes_the_record(work, key):
    proj = make_project(work)
    cfg = make_cfg(work)
    run = harness.run_one(proj, "axi", 1, cfg)
    run_dir = work / "runs" / "proj" / "axi" / "r1"
    on_disk = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert on_disk == run
    assert run["schema"] == 1 and run["project"] == "proj" and run["arm"] == "axi" and run["repeat"] == 1
    assert run["model"] == "claude-opus-5-5" and run["effort"] == "medium" and run["claude_version"] == "2.1.317"
    assert run["usage"] == {"input_tokens": 7, "cache_creation_input_tokens": 9450, "cache_read_input_tokens": 1000,
                            "output_tokens": 120, "thinking_tokens": 30}
    assert run["total_cost_usd"] == 0.0123 and run["num_turns"] == 2 and run["stop"] == "end_turn"
    assert run["first_turn_cache_creation"] == 9450
    assert len(run["tool_calls"]) == 1
    call = run["tool_calls"][0]
    assert call["n"] == 1 and call["tool"] == "Bash" and call["summary"] == "blender-axi objects work.blend"
    assert call["result_chars"] == len("count: 13 of 13\nname: Mug\n") and call["is_error"] is False and call["ms"] >= 0
    assert run["blender_starts"] == 1 and run["errors"] == 0 and run["permission_denials"] == 0
    assert run["key"] == f"set,len {len(DUMMY_KEY)}"
    assert run["check"] == {"passed": True, "checks": {"x": True}, "notes": []}
    assert run["snapshots"] == 1 and run["snapshot_errors"] == []
    assert (run_dir / "snapshots" / "001.blend").read_bytes() == b"BLENDER-dummy-start"
    assert json.loads((run_dir / "snapshots" / "001.json").read_text(encoding="utf-8"))["tool"] == "Bash"
    assert (run_dir / "out" / "x.txt").is_file() and (run_dir / "check.json").is_file()
    assert cfg.projects.calls == [("proj", run_dir / "out")]
    transcript = (run_dir / "transcript.md").read_text(encoding="utf-8")
    assert "## Step 1 · Bash ·" in transcript and "blender-axi objects work.blend" in transcript
    assert "Done. 13 objects." in transcript and "count: 13 of 13" in transcript
    started = json.loads((run_dir / "fake_started.json").read_text(encoding="utf-8"))
    assert started["key_len"] == len(DUMMY_KEY) and not started["has_auth_token"] and not started["has_axi_key"]
    assert started["config_dir"] == str(work / "claude-config") and started["path_head"] == str(work / "bin")
    assert started["argv"][-1] == "Edit `work.blend` and write under `out`."


def test_the_key_value_is_never_written(work, key):
    harness.run_one(make_project(work), "axi", 1, make_cfg(work))
    hits = [p for p in work.rglob("*") if p.is_file() and DUMMY_KEY.encode() in p.read_bytes()]
    assert hits == []


def test_blender_starts_counts_commands_not_help(work, key, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_COMMANDS", json.dumps(
        ["blender-axi --help", "blender-axi objects work.blend", "blender-axi render --help", "ls"]))
    run = harness.run_one(make_project(work), "axi", 1, make_cfg(work))
    assert len(run["tool_calls"]) == 4 and run["blender_starts"] == 1 and run["snapshots"] == 4


@pytest.mark.parametrize("mode,stop,errors,denials", [("ok", "end_turn", 0, 0), ("max_turns", "max_turns", 0, 0),
                                                       ("error", "error", 1, 1), ("budget", "budget", 0, 0)])
def test_stop_mapping_through_a_run(work, key, monkeypatch, mode, stop, errors, denials):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)
    run = harness.run_one(make_project(work), "axi", 1, make_cfg(work))
    assert (run["stop"], run["errors"], run["permission_denials"]) == (stop, errors, denials)


def test_stop_reason_unit():
    ok = {"subtype": "success", "is_error": False, "num_turns": 9}
    assert record.stop_reason(ok, 40, 0, False) == "end_turn"
    assert record.stop_reason({**ok, "num_turns": 40}, 40, 0, False) == "max_turns"
    assert record.stop_reason({**ok, "subtype": "error_max_turns"}, 40, 0, False) == "max_turns"
    assert record.stop_reason({**ok, "subtype": "error_max_budget_usd"}, 40, 0, False) == "budget"
    assert record.stop_reason({**ok, "is_error": True}, 40, 0, False) == "error"
    assert record.stop_reason(None, 40, 1, False) == "error"
    assert record.stop_reason(ok, 40, None, True) == "timeout"


def test_timeout_kills_the_agent(work, key, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "hang")
    run = harness.run_one(make_project(work), "axi", 1, make_cfg(work, timeout_s=3))
    assert run["stop"] == "timeout" and run["duration_s"] < 60 and run["tool_calls"] == []
    assert (work / "runs" / "proj" / "axi" / "r1" / "run.json").is_file()


def test_a_crashing_checker_is_a_failed_check(work, key):
    def boom(*_a):
        raise RuntimeError("no blender here")

    cfg = make_cfg(work, projects=SimpleNamespace(run_check=boom))
    run = harness.run_one(make_project(work), "axi", 1, cfg)
    assert run["check"]["passed"] is False and "no blender here" in run["check"]["notes"][0]


def test_run_dir_is_recreated_and_needs_a_prepared_start(work, key):
    proj = make_project(work)
    harness.run_one(proj, "axi", 1, make_cfg(work))
    (work / "runs" / "proj" / "axi" / "r1" / "stale.txt").write_text("x", encoding="utf-8")
    harness.run_one(proj, "axi", 1, make_cfg(work))
    assert not (work / "runs" / "proj" / "axi" / "r1" / "stale.txt").exists()
    (work / "scenes" / "proj" / "start.blend").unlink()
    with pytest.raises(arms.SetupError, match="start.blend"):
        harness.run_one(proj, "axi", 2, make_cfg(work))

# --- the MCP arm: snapshots over the add-on socket (stub server, no Blender) ---


class FakeAddon:
    """Speaks the add-on protocol (JSON, NUL terminated), records requests and 'saves' the requested copy."""

    def __init__(self, status="ok"):
        self.requests, self.status = [], status
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                buf = b""
                while b"\0" not in buf:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                if b"\0" not in buf:
                    continue
                request = json.loads(buf.split(b"\0")[0])
                self.requests.append(request)
                match = re.search(r"_AXI_SAVE = '(.*)'", request["code"])
                if self.status == "ok" and match:
                    Path(match.group(1)).parent.mkdir(parents=True, exist_ok=True)
                    Path(match.group(1)).write_bytes(b"BLENDER-copy")
                reply = {"status": self.status, "result": {"saved": match and match.group(1)}, "message": "boom"}
                conn.sendall(json.dumps(reply).encode("utf-8") + b"\0")

    def close(self):
        self.sock.close()


class StubServer:
    def __init__(self, addon, _record=None):
        self.port = addon.port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


def test_snapshot_mcp_sends_a_save_copy_request(work):
    addon = FakeAddon()
    try:
        dest = work / "snapshots" / "001.blend"
        harness.snapshot_mcp(addon.port, dest)
    finally:
        addon.close()
    assert dest.read_bytes() == b"BLENDER-copy"
    request = addon.requests[0]
    assert request["type"] == "execute" and "save_as_mainfile" in request["code"] and "copy=True" in request["code"]
    assert dest.as_posix() in request["code"]


def test_snapshot_mcp_reports_a_server_error(work):
    addon = FakeAddon(status="error")
    try:
        with pytest.raises(RuntimeError, match="boom"):
            harness.snapshot_mcp(addon.port, work / "001.blend")
    finally:
        addon.close()


def test_run_one_mcp_snapshots_through_the_socket(work, key):
    addon, servers = FakeAddon(), []
    cfg = make_cfg(work, server_factory=lambda blender, blend, log: StubServer(addon, servers))
    try:
        run = harness.run_one(make_project(work), "mcp", 1, cfg)
    finally:
        addon.close()
    run_dir = work / "runs" / "proj" / "mcp" / "r1"
    assert run["arm"] == "mcp" and run["blender_starts"] == 1 and run["snapshots"] == 1
    assert run["tool_calls"][0]["tool"] == "mcp__blender__get_objects_summary"
    assert (run_dir / "snapshots" / "001.blend").read_bytes() == b"BLENDER-copy"
    started = json.loads((run_dir / "fake_started.json").read_text(encoding="utf-8"))
    config = json.loads(started["argv"][started["argv"].index("--mcp-config") + 1])
    assert config["mcpServers"]["blender"]["env"]["BLENDER_MCP_PORT"] == str(addon.port)


def test_a_failed_snapshot_is_recorded_and_the_run_continues(work, key):
    addon = FakeAddon(status="error")
    cfg = make_cfg(work, server_factory=lambda blender, blend, log: StubServer(addon, []))
    try:
        run = harness.run_one(make_project(work), "mcp", 1, cfg)
    finally:
        addon.close()
    assert run["snapshots"] == 0 and run["stop"] == "end_turn" and "boom" in run["snapshot_errors"][0]["error"]


def test_the_mcp_server_log_sits_in_the_run_dir_so_relative_paths_resolve(work, key):
    addon, seen = FakeAddon(), []

    def factory(blender, blend, log):
        seen.append((Path(blend), Path(log)))
        return StubServer(addon, [])

    try:
        harness.run_one(make_project(work), "mcp", 1, make_cfg(work, server_factory=factory))
    finally:
        addon.close()
    run_dir = work / "runs" / "proj" / "mcp" / "r1"
    assert seen == [(run_dir / "work.blend", run_dir / "server.log")]

# --- stream parsing and transcript ---


def line(**event):
    return json.dumps(event)


def test_stream_state_handles_noise_lists_and_images():
    s = record.StreamState(clock=iter(range(100)).__next__)
    assert s.feed("not json") == [] and s.feed("") == [] and s.feed("[1]") == [] and s.bad_lines == 2
    s.feed(line(type="assistant", message={"content": [
        {"type": "thinking", "thinking": "hm"},
        {"type": "tool_use", "id": "a", "name": "Read", "input": {"file_path": "work.blend"}},
        {"type": "tool_use", "id": "b", "name": "mcp__blender__execute_blender_code",
         "input": {"code": "\nimport bpy\nprint(1)"}}], "usage": {"cache_creation_input_tokens": 77}}))
    done = s.feed(line(type="user", message={"content": [
        {"type": "tool_result", "tool_use_id": "a", "content": [{"type": "text", "text": "x"}, {"type": "image"}]},
        {"type": "tool_result", "tool_use_id": "b", "content": "y" * 10, "is_error": True}]}))
    assert [c["n"] for c in done] == [1, 2]
    assert s.calls[0]["summary"] == "work.blend" and s.calls[0]["result_chars"] == len("x\n[image]")
    assert s.calls[1]["summary"] == "import bpy" and s.calls[1]["is_error"] is True
    assert s.first_turn_cache_creation == 77
    assert len(record.tool_summary("Bash", {"command": "x" * 500 + "\nsecond"})) == 160
    assert s.feed(line(type="user", message={"content": [{"type": "tool_result", "tool_use_id": "zzz"}]})) == []


def test_summary_is_truncated_to_160_chars():
    assert len(record.tool_summary("Bash", {"command": "x" * 500 + "\nsecond"})) == 160


def test_transcript_caps_result_lines_and_total_size(work, key, monkeypatch):
    s = record.StreamState()
    s.feed(line(type="assistant", message={"content": [
        {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "blender-axi objects work.blend"}}]}))
    big = "\n".join(f"line {i} " + "z" * 500 for i in range(500))
    s.feed(line(type="user", message={"content": [{"type": "tool_result", "tool_use_id": "t1", "content": big}]}))
    run = {"project": "p", "arm": "axi", "repeat": 1, "model": "m", "effort": "e", "total_cost_usd": 1.5,
           "num_turns": 2, "stop": "end_turn", "check": {"passed": True}}
    text = record.render_transcript(s, run)
    assert "... (480 more lines)" in text and "line 19 " in text and "line 20 " not in text
    assert all(len(ln) <= record.LINE_MAX + 4 for ln in text.splitlines())
    assert len(text.encode("utf-8")) < 60_000


def test_local_normalise_replaces_paths_user_and_ports(work):
    repo, home = Path("E:/janedoe/Projects-X/blender-axi"), Path("C:/Users/jdoe-box")  # scan:allow (fake)
    tmp = repo / ".tmp" / "showcase"
    text = (f"{tmp}\\runs\\x and {repo.as_posix()}/src and {str(home).replace(chr(92), chr(92) * 2)} "
            "--port 51234 jdoe-box 127.0.0.1:60000")
    out = record._normalise(text, repo, tmp, home, "jdoe-box")
    assert "<tmp>" in out and "<repo>/src" in out and "<home>" in out and "<user>" in out
    assert "51234" not in out and "60000" not in out and "--port 50000" in out
    assert "janedoe" not in out and "jdoe-box" not in out

# --- run_ab: dry run, setup errors, ordering ---


def test_dry_run_masks_the_key_and_runs_nothing(work, key, capsys):
    proj = make_project(work)
    projects = stub_projects()
    projects.names = lambda: ["proj"]
    projects.load = lambda n: proj
    rc = run_ab.main(["--project", "all", "--arm", "both", "--dry-run", "--claude", str(FAKE), "--tmp", str(work),
                      "--mcp-server", str(work / "blender-mcp.exe")], projects=projects)
    out = capsys.readouterr().out
    assert rc == 0 and "<set,len" in out and DUMMY_KEY not in out and "token-should-not-leak" not in out
    assert "== proj / axi" in out and "== proj / mcp" in out and "--mcp-config" in out
    assert str(RESERVED_PORT) not in out
    assert not (work / "runs").exists()


def test_dry_run_without_a_key_says_unset(work, monkeypatch, capsys):
    monkeypatch.delenv("BLENDER_AXI_ANTHROPIC_KEY", raising=False)
    proj = make_project(work)
    projects = stub_projects()
    projects.load = lambda n: proj
    assert run_ab.main(["--dry-run", "--claude", str(FAKE), "--tmp", str(work)], projects=projects) == 0
    assert "<unset>" in capsys.readouterr().out


def test_setup_errors_exit_2(work, monkeypatch, capsys):
    monkeypatch.delenv("BLENDER_AXI_ANTHROPIC_KEY", raising=False)
    proj = make_project(work)
    projects = stub_projects()
    projects.load = lambda n: proj
    assert run_ab.main(["--claude", str(FAKE), "--tmp", str(work)], projects=projects) == 2
    assert "BLENDER_AXI_ANTHROPIC_KEY" in capsys.readouterr().err
    assert run_ab.main(["--project", "nope", "--dry-run", "--claude", str(FAKE)], projects=projects) == 2
    assert run_ab.main(["--dry-run", "--claude", "no-such-claude.py"], projects=projects) == 2


def test_run_matrix_reports_crashes_and_cuts(work, key, monkeypatch):
    proj = make_project(work)
    cfg = make_cfg(work)
    runs, rc = run_ab.run_matrix(cfg, [proj], ["axi"], 2)
    assert rc == 0 and [r["repeat"] for r in runs] == [1, 2]
    assert run_ab.order_for(2, ["axi", "mcp"]) == ["mcp", "axi"]
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "max_turns")
    runs, rc = run_ab.run_matrix(cfg, [proj], ["axi"], 1)
    assert rc == 1 and runs[0]["stop"] == "max_turns"


def test_representative_prefers_the_first_passed_repeat():
    def r(k, passed):
        return {"project": "p", "arm": "axi", "repeat": k, "check": {"passed": passed}}

    runs = [r(1, False), r(2, True), r(3, True)]
    assert run_ab.representative(runs, "p", "axi")["repeat"] == 2
    assert run_ab.representative([r(1, False), r(2, False)], "p", "axi")["repeat"] == 1
    assert run_ab.representative(runs, "p", "mcp") is None


def test_no_forbidden_literals_in_showcase_sources():
    forbidden_port = "98" + "76"  # spelled in pieces so this file is not itself a hit
    mine = [SHOWCASE / "run_ab.py"] + [SHOWCASE / "sc" / n for n in ("__init__.py", "harness.py", "arms.py", "record.py")]
    for path in mine:
        text = path.read_text(encoding="utf-8")
        assert forbidden_port not in text or path.name == "arms.py", path.name
        assert not re.search(r"playwright|chrom", text, re.IGNORECASE), path.name
    for path in mine + [TESTS / "test_harness.py", TESTS / "fake_claude.py"]:
        assert len(path.read_text(encoding="utf-8").splitlines()) < 500, path.name
