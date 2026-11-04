"""One A/B run: spawn `claude -p`, parse its stream, snapshot the scene after every tool result (plan U1, K1-K5)."""

from __future__ import annotations

import datetime
import json
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from blender_axi import __version__ as AXI_VERSION
from blender_axi import live
from bench.server import AddonServer

from . import REPO, arms, record

HOST = "127.0.0.1"
RUN_TIMEOUT_S = 20 * 60
SNAPSHOT_TIMEOUT_S = 60


@dataclass
class Config:
    tmp: Path                      # <repo>/.tmp/showcase
    claude: list                   # argv prefix, e.g. ["C:/.../claude.exe"] or [sys.executable, "fake_claude.py"]
    axi_bin: Path                  # directory holding blender-axi(.exe); first on the agent's PATH
    blender: Path                  # Blender executable (the harness only passes it on)
    projects: Any                  # module with prepare_start / run_check (sc.projects, or a stub in tests)
    mcp_exe: Path | None = None
    skill: Path = REPO / "skills" / "blender-axi" / "SKILL.md"
    model: str = arms.MODEL
    effort: str = arms.EFFORT
    max_turns: int = arms.MAX_TURNS
    max_budget_usd: float = arms.MAX_BUDGET_USD
    bare: bool = True
    timeout_s: float = RUN_TIMEOUT_S
    server_factory: Any = AddonServer   # tests substitute a stub context manager exposing `.port`
    meta: dict = field(default_factory=dict)  # claude_version, blender, mcp_server, results (filled by run_ab)
    repo: Path = REPO

    @property
    def common(self) -> tuple:
        return arms.common_flags(self.model, self.effort, self.max_turns, self.max_budget_usd, self.bare)

    @property
    def blender_axi_exe(self) -> Path:
        for name in ("blender-axi.exe", "blender-axi"):
            if (Path(self.axi_bin) / name).is_file():
                return Path(self.axi_bin) / name
        return Path(self.axi_bin) / "blender-axi"


def project_prompt(project) -> str:
    """Text of `<project dir>/prompt.md` when present, else `project.prompt` (text); placeholders filled."""
    path = Path(getattr(project, "dir", "") or ".") / "prompt.md"
    text = path.read_text(encoding="utf-8") if path.is_file() else str(project.prompt)
    return arms.fill_prompt(text.replace("\r\n", "\n").strip())


def build_arm(arm_name: str, cfg: Config, port: int | None = None) -> arms.Arm:
    if arm_name == "axi":
        return arms.axi_arm(cfg.skill)
    if arm_name == "mcp":
        return arms.mcp_arm(cfg.mcp_exe, port if port is not None else arms.DEFAULT_PORT_PLACEHOLDER)
    raise ValueError(f"unknown arm {arm_name!r}")


def argv_for(project, arm_name: str, cfg: Config, port: int | None = None) -> list[str]:
    return arms.build_argv(list(cfg.claude), build_arm(arm_name, cfg, port), project_prompt(project), cfg.common)


# ---------------------------------------------------------------------------------------------------------------
# snapshots (K5)
# ---------------------------------------------------------------------------------------------------------------


def snapshot_axi(work: Path, dest: Path) -> None:
    shutil.copyfile(work, dest)


def snapshot_mcp(port: int, dest: Path) -> None:
    """Ask the add-on server to save a copy of the open scene (the open file path and dirty state stay as they are)."""
    code = live.SAVE_COPY_CODE.format(path=Path(dest).as_posix())
    response = live.send(HOST, port, code, strict_json=True, timeout=SNAPSHOT_TIMEOUT_S)
    if response.get("status") != "ok":
        raise RuntimeError(str(response.get("message") or "snapshot request failed")[-200:])
    if not Path(dest).is_file():
        raise RuntimeError("the server answered ok but wrote no file")


def take_snapshot(run_dir: Path, call: dict, started: float, arm_name: str, port: int | None, errors: list) -> bool:
    n = call["n"]
    dest = run_dir / "snapshots" / f"{n:03d}.blend"
    meta = {"n": n, "tool": call["tool"], "summary": call["summary"], "ms": call["ms"], "is_error": call["is_error"],
            "t": round(time.monotonic() - started, 2)}
    try:
        if arm_name == "axi":
            snapshot_axi(run_dir / "work.blend", dest)
        else:
            snapshot_mcp(port, dest)
        ok = True
    except Exception as exc:  # a failed snapshot only costs a replay frame, never a metric
        meta["snapshot_error"] = str(exc)[:200]
        errors.append({"n": n, "error": meta["snapshot_error"]})
        ok = False
    record.write_json(run_dir / "snapshots" / f"{n:03d}.json", meta)
    return ok


# ---------------------------------------------------------------------------------------------------------------
# process handling
# ---------------------------------------------------------------------------------------------------------------


def kill_tree(proc: subprocess.Popen) -> None:
    """Only the process this harness started (and its children)."""
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=30)
        else:
            proc.kill()
    except Exception:
        pass


def _spawn_and_read(argv, env, run_dir: Path, state: record.StreamState, cfg: Config, arm_name: str,
                    port: int | None, snap_errors: list, started: float) -> tuple[int | None, bool, int]:
    """Run the agent; returns (exit code, timed out, snapshots written)."""
    timed_out = threading.Event()
    snaps = 0
    with open(run_dir / "stderr.txt", "wb") as err, open(run_dir / "stream.jsonl", "wb") as stream:
        proc = subprocess.Popen(argv, cwd=str(run_dir), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=err)

        def _timeout() -> None:
            timed_out.set()
            kill_tree(proc)

        watchdog = threading.Timer(cfg.timeout_s, _timeout)
        watchdog.daemon = True
        watchdog.start()
        try:
            for raw in proc.stdout:
                stream.write(raw)
                for call in state.feed(raw.decode("utf-8", errors="replace")):
                    if take_snapshot(run_dir, call, started, arm_name, port, snap_errors):
                        snaps += 1
            try:
                proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                kill_tree(proc)
                proc.wait(timeout=30)
        finally:
            watchdog.cancel()
            kill_tree(proc)
    return proc.returncode, timed_out.is_set(), snaps


def _prepare_dir(cfg: Config, project, arm_name: str, repeat: int) -> tuple[Path, Path]:
    run_dir = Path(cfg.tmp) / "runs" / project.name / arm_name / f"r{repeat}"
    if run_dir.exists():
        shutil.rmtree(run_dir)
    (run_dir / "out").mkdir(parents=True)
    (run_dir / "snapshots").mkdir()
    start = Path(cfg.tmp) / "scenes" / project.name / "start.blend"
    if not start.is_file():
        raise arms.SetupError(f"{start} is missing: prepare_start for {project.name} has not run")
    shutil.copyfile(start, run_dir / "work.blend")
    return run_dir, start


def run_one(project, arm_name: str, repeat: int, cfg: Config) -> dict:
    """Run one cell repeat and write run.json, transcript.md, check.json. Returns the run.json dict."""
    run_dir, _start = _prepare_dir(cfg, project, arm_name, repeat)
    env = arms.child_env(cfg.tmp, cfg.axi_bin)
    state = record.StreamState()
    snap_errors: list = []
    started_wall = datetime.datetime.now().isoformat(timespec="seconds")
    started = time.monotonic()

    def launch(port: int | None):
        argv = argv_for(project, arm_name, cfg, port)
        return _spawn_and_read(argv, env, run_dir, state, cfg, arm_name, port, snap_errors, started)

    if arm_name == "mcp":
        # the server's cwd is the log's directory = the run dir, so the agent's relative `out/...` paths resolve there
        with cfg.server_factory(cfg.blender, run_dir / "work.blend", run_dir / "server.log") as server:
            exit_code, timed_out, snaps = launch(server.port)
    else:
        exit_code, timed_out, snaps = launch(None)
    duration = round(time.monotonic() - started, 1)

    result = state.result or {}
    run = {
        "schema": record.SCHEMA, "project": project.name, "arm": arm_name, "repeat": repeat, "model": cfg.model,
        "effort": cfg.effort, "claude_version": cfg.meta.get("claude_version", "unknown"),
        "blender_axi": AXI_VERSION, "blender": cfg.meta.get("blender", "unknown"),
        "mcp_server": cfg.meta.get("mcp_server", {"version": None, "commit": None}),
        "key": _key_note(env),
        "started": started_wall, "duration_s": duration, "num_turns": int(result.get("num_turns") or 0),
        "stop": record.stop_reason(state.result, cfg.max_turns, exit_code, timed_out),
        "usage": record.usage_of(result.get("usage")), "total_cost_usd": float(result.get("total_cost_usd") or 0.0),
        "first_turn_cache_creation": state.first_turn_cache_creation or 0,
        "tool_calls": state.calls, "errors": sum(1 for c in state.calls if c["is_error"]),
        "permission_denials": len(result.get("permission_denials") or []),
        "blender_starts": state.blender_starts_axi if arm_name == "axi" else 1,
        "check": {}, "snapshots": snaps, "snapshot_errors": snap_errors, "transcript": "transcript.md",
    }
    run["check"] = _check(project, cfg, run_dir)
    _copy_showcase(project, run_dir)
    record.write_json(run_dir / "check.json", run["check"])
    record.write_run(run_dir, run)
    normalise = record.get_normaliser()
    record.write_transcript(run_dir, state, run, normalise, cfg.repo, cfg.tmp, Path.home(), _user())
    return run


def _key_note(env: dict) -> str:
    key = env.get("ANTHROPIC_API_KEY")
    return f"set,len {len(key)}" if key else "unset"


def _user() -> str:
    import getpass
    try:
        return getpass.getuser()
    except Exception:
        return ""


def _copy_showcase(project, run_dir: Path) -> None:
    """Copy the project's deliverable image (meta.json `showcase`, e.g. `out/sheet.png`) to `<run_dir>/showcase.png`,
    text chunks stripped; the replay's final frame and the published final image prefer it over the scene render."""
    try:
        meta_path = Path(getattr(project, "dir", "")) / "meta.json"
        rel = json.loads(meta_path.read_text(encoding="utf-8")).get("showcase") if meta_path.is_file() else None
        src = run_dir / rel if rel else None
        if src is not None and src.is_file() and src.suffix.lower() == ".png":
            from bench.scenes import strip_png_text
            shutil.copyfile(src, run_dir / "showcase.png")
            strip_png_text(run_dir / "showcase.png")
    except Exception:  # a missing or odd deliverable only costs the nicer picture
        pass


def _check(project, cfg: Config, run_dir: Path) -> dict:
    try:
        result = cfg.projects.run_check(project, cfg.blender, run_dir / "out")
        return result if isinstance(result, dict) else {"passed": False, "checks": {}, "notes": ["checker returned no dict"]}
    except Exception as exc:
        return {"passed": False, "checks": {}, "notes": [f"checker crashed: {str(exc)[-300:]}"]}
