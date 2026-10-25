"""Token-savings benchmark: blender-axi vs the official blender-mcp server, same live Blender (plan section 4.4).

    .venv/Scripts/python.exe benchmarks/token_bench.py [--scenes desk,city] [--model claude-opus-5-5]
        [--mcp-server <exe>] [--blender <exe>] [--skip-build] [--skip-render] [--readme README.md]
        [--results-dir benchmarks/results]

Exit 0 on success, 2 on setup problems (one-line fix printed), 1 if any task failed (results.json is still written).
Characters are always recorded; exact tokens need BLENDER_AXI_ANTHROPIC_KEY (or ANTHROPIC_API_KEY) in the environment.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

REPO = Path(__file__).resolve().parents[1]
for _p in (str(REPO / "src"), str(REPO / "benchmarks")):  # the harness and the CLI are not installed
    if _p not in sys.path:
        sys.path.insert(0, _p)

from blender_axi import __version__ as AXI_VERSION  # noqa: E402
from blender_axi import paths  # noqa: E402
from blender_axi.errors import AxiError  # noqa: E402
from blender_axi.locate import find_blender  # noqa: E402

from bench import artifacts, report, svg, tokens  # noqa: E402
from bench.mcp_client import McpError, McpStdioClient, compact_json  # noqa: E402
from bench.scenes import SceneError, build_scene, ensure_empty_blend, render_scene  # noqa: E402
from bench.server import AddonServer, ServerError  # noqa: E402
from bench.tasks import SCENES, Task  # noqa: E402

HOST = "127.0.0.1"
SKILL_PATH = REPO / "skills" / "blender-axi" / "SKILL.md"
AXI_TIMEOUT = 300
MCP_INSTALL = ("uv venv .tmp/mcpvenv --python 3.13", "uv pip install --python .tmp/mcpvenv -e .tmp/blender_mcp_src/mcp")


class SetupError(RuntimeError):
    """A setup problem: the message is the one-line fix; the run exits 2."""


def default_mcp_exe() -> Path:
    sub = "Scripts/blender-mcp.exe" if sys.platform == "win32" else "bin/blender-mcp"
    return REPO / ".tmp" / "mcpvenv" / sub


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="token_bench.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--scenes", default=",".join(SCENES), help="comma-separated scene names (default: all)")
    ap.add_argument("--model", default=tokens.DEFAULT_MODEL, help="model id for exact token counts")
    ap.add_argument("--mcp-server", default=None, help="official blender-mcp executable")
    ap.add_argument("--blender", default=None, help="Blender executable")
    ap.add_argument("--skip-build", action="store_true", help="reuse the scene .blend files from a previous run")
    ap.add_argument("--skip-render", action="store_true", help="do not render the scene previews")
    ap.add_argument("--readme", default=None, help="README file whose bench:start/bench:end block is rewritten")
    ap.add_argument("--results-dir", default=str(REPO / "benchmarks" / "results"), help="where artifacts are written")
    return ap.parse_args(argv)


# ---------------------------------------------------------------------------------------------------------------
# environment and small helpers
# ---------------------------------------------------------------------------------------------------------------


def axi_env() -> dict[str, str]:
    """The CLI's environment: Blender temp under the project .tmp/, the repo's src/ first on PYTHONPATH."""
    env = paths.blender_env()
    env["BLENDER_AXI_TMP"] = str(paths.tmp_root())
    src = str(REPO / "src")
    env["PYTHONPATH"] = src + os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else src
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def mcp_server_info(exe: Path) -> dict:
    """Best-effort name, version and source commit of the official server from its venv metadata."""
    info = {"name": "blender-mcp", "version": None, "commit": None}
    root = Path(exe).resolve().parent.parent
    dists = sorted(root.glob("Lib/site-packages/blender_mcp-*.dist-info"))
    dists += sorted(root.glob("lib/python*/site-packages/blender_mcp-*.dist-info"))
    if not dists:
        return info
    dist = dists[0]
    info["version"] = dist.name[len("blender_mcp-"):-len(".dist-info")]
    try:
        url = json.loads((dist / "direct_url.json").read_text(encoding="utf-8"))["url"]
        src = Path(url2pathname(urlparse(url).path))
        out = subprocess.run(["git", "-C", str(src), "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                             timeout=30)
        if out.returncode == 0 and out.stdout.strip():
            info["commit"] = out.stdout.strip()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        pass
    return info


def read_skill() -> str:
    return SKILL_PATH.read_bytes().decode("utf-8").replace("\r\n", "\n")


def run_axi(task: Task, port: int) -> tuple[str, int, int, str]:
    """Run one blender-axi command against the live server: (stdout, exit code, ms, stderr tail)."""
    argv = [sys.executable, "-m", "blender_axi", *task.axi, "--live", "--host", HOST, "--port", str(port)]
    started = time.monotonic()
    proc = subprocess.run(argv, cwd=str(REPO), env=axi_env(), capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=AXI_TIMEOUT)
    return proc.stdout, proc.returncode, round((time.monotonic() - started) * 1000), proc.stderr.strip()[-300:]


def _path_forms(path) -> list[str]:
    """A directory in forward-slash, backslash and JSON-escaped (double backslash) spelling, longest first."""
    fwd = str(path).replace("\\", "/").rstrip("/")
    return sorted({fwd, fwd.replace("/", "\\"), fwd.replace("/", "\\\\")}, key=len, reverse=True)


def _replace_dir(text: str, path, placeholder: str) -> str:
    for form in _path_forms(path):
        text = re.sub(re.escape(form), lambda _m: placeholder, text, flags=re.IGNORECASE)
    return text


def normalise(text: str, repo: Path, scenes_dir: Path, home: Path | None = None) -> str:
    """Make raw outputs machine-independent: no random port, no absolute local paths (the repo root becomes
    `<repo>`, the scenes temp dir `<repo>/.tmp/bench/scenes`, the home directory `<home>`)."""
    text = re.sub(r"--port (\d+)", lambda m: "--port 5" + "0" * (len(m.group(1)) - 1), text)
    text = _replace_dir(text, scenes_dir, "<repo>/.tmp/bench/scenes")
    text = _replace_dir(text, repo, "<repo>")
    return text if home is None else _replace_dir(text, home, "<home>")


_NORMALISER = {"fn": lambda t: t}  # set by setup(); measure() applies it to every stored output


def measure(counter: tokens.TokenCounter, results_dir: Path, rel: str, text: str) -> dict:
    """Store the (normalised) raw output and return the chars/tokens/hash record shared by every measurement."""
    text = _NORMALISER["fn"](text)
    rec = artifacts.write_raw(results_dir, rel, text)
    return {"chars": rec["chars"], "tokens": counter.count(text), "raw": rec["raw"],
            "truncated": rec["truncated"], "sha256": rec["sha256"]}


def one_line(text: str, limit: int = 160) -> str:
    return " ".join(str(text).split())[:limit]


# ---------------------------------------------------------------------------------------------------------------
# per-task measurement
# ---------------------------------------------------------------------------------------------------------------


def measure_task(task: Task, scene: str, port: int, client: McpStdioClient, counter: tokens.TokenCounter,
                 results_dir: Path, seen: dict) -> dict:
    """One results.json task record. Failures become `error` records instead of exceptions."""
    out = {"id": task.id, "label": task.label, "note": task.note}
    try:
        text, code, ms, err = run_axi(task, port)
        if code != 0:
            raise RuntimeError(f"blender-axi {' '.join(task.axi)} exited {code}: {one_line(err or text)}")
        axi = measure(counter, results_dir, f"{scene}/{task.id}__axi.txt", text)
        axi = {"cmd": "blender-axi " + " ".join((*task.axi, "--live")), **axi, "ms": ms, "exit_code": code}
        out["axi"] = {k: axi[k] for k in ("cmd", "chars", "tokens", "ms", "exit_code", "raw", "truncated", "sha256")}
        out["mcp"] = None
        if task.mcp is not None:
            tool, args = task.mcp
            key = (tool, json.dumps(args, sort_keys=True))
            if key in seen:
                out["mcp"] = {**seen[key][1], "reused_from": seen[key][0]}
            else:
                text, ms = client.call_text(tool, args)
                rec = measure(counter, results_dir, f"{scene}/{task.id}__mcp.json", text)
                mcp = {"tool": tool, "args": args, "chars": rec["chars"], "tokens": rec["tokens"], "ms": ms,
                       "raw": rec["raw"], "truncated": rec["truncated"], "sha256": rec["sha256"]}
                seen[key] = (task.id, mcp)
                out["mcp"] = {**mcp, "reused_from": None}
    except (McpError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        return {"id": task.id, "label": task.label, "note": task.note, "error": one_line(exc)}
    return out


def saving(axi: int | None, mcp: int | None) -> float | None:
    return report.computed_saving(axi, mcp)


def finalize_task(task: dict, unit: str) -> None:
    """Fill `saving_pct` in the final unit (tokens may have switched back to chars mid-run)."""
    if task.get("error"):
        return
    task["saving_pct"] = None if task.get("mcp") is None else saving(
        report.value_of(task["axi"], unit), report.value_of(task["mcp"], unit))


def task_line(scene: str, task: dict, unit: str) -> str:
    name = f"{scene}/{task['id']}"
    if task.get("error"):
        return f"{name}  ERROR {task['error']}"
    axi, mcp = report.value_of(task["axi"], unit), report.value_of(task.get("mcp"), unit)
    pct = report.fmt_pct(saving(axi, mcp)).replace(report.NA, "-")
    return f"{name}  mcp {'-' if mcp is None else mcp}  axi {axi}  saving {pct}  ({unit})"


# ---------------------------------------------------------------------------------------------------------------
# scenes
# ---------------------------------------------------------------------------------------------------------------


def prepare_scene(name: str, args, blender: Path, base: Path, scenes_dir: Path, results_dir: Path) -> tuple[Path, dict]:
    """Build (or reuse) the scene .blend and render its preview. Returns (blend, {objects, collections, render})."""
    blend = scenes_dir / f"{name}.blend"
    sidecar = scenes_dir / f"{name}.json"
    if args.skip_build:
        if not blend.is_file():
            raise SetupError(f"{blend} is missing: run once without --skip-build")
        try:
            built = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            built = {}
    else:
        print(f"building scene {name} ...", flush=True)
        built = build_scene(sys.executable, REPO, name, base, blend)
        sidecar.write_text(json.dumps(built), encoding="utf-8")
    png = results_dir / name / "render.png"
    if not args.skip_render:
        print(f"rendering scene {name} ...", flush=True)
        render_scene(sys.executable, REPO, blend, png)
    info = {"objects": built.get("objects"), "collections": built.get("collections"),
            "render": f"{name}/render.png" if png.is_file() else None}
    return blend, info


def run_scene(name: str, args, ctx: dict, first: bool) -> tuple[dict, dict | None]:
    """Measure one scene. Returns (scene record, fixed mcp tools_list record or None)."""
    counter, results_dir, logs = ctx["counter"], ctx["results_dir"], ctx["logs"]
    blend, info = prepare_scene(name, args, ctx["blender"], ctx["base"], ctx["scenes_dir"], results_dir)
    records, fixed = [], None
    print(f"starting the add-on server for {name} ...", flush=True)
    with ExitStack() as stack:
        server = stack.enter_context(AddonServer(ctx["blender"].path, blend, logs / f"{name}-server.log"))
        env = paths.blender_env()
        env.update({"BLENDER_MCP_HOST": HOST, "BLENDER_MCP_PORT": str(server.port)})
        try:
            client = stack.enter_context(McpStdioClient(str(ctx["mcp_exe"]), env, logs / f"{name}-mcp.log"))
        except McpError as exc:
            raise SetupError(f"the official server did not start ({one_line(exc, 300)}); check {logs / (name + '-mcp.log')}")
        if first:
            fixed = fixed_mcp(client, counter, results_dir)
        seen: dict = {}
        for task in SCENES[name]:
            rec = measure_task(task, name, server.port, client, counter, results_dir, seen)
            records.append(rec)
            print(task_line(name, rec, counter.unit), flush=True)
    return {**info, "tasks": records}, fixed


def fixed_mcp(client: McpStdioClient, counter: tokens.TokenCounter, results_dir: Path) -> dict | None:
    try:
        tools = client.tools_list()
    except McpError as exc:
        print(f"fixed/tools_list  ERROR {one_line(exc)}", flush=True)
        return None
    rec = measure(counter, results_dir, "fixed/tools_list__mcp.json", compact_json(tools))
    return {"chars": rec["chars"], "tokens": rec["tokens"], "tools": len(tools), "raw": rec["raw"]}


# ---------------------------------------------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------------------------------------------


def setup(args) -> dict:
    names = [s.strip() for s in args.scenes.split(",") if s.strip()]
    unknown = [s for s in names if s not in SCENES]
    if not names or unknown:
        raise SetupError(f"unknown scene {', '.join(unknown) or '(none)'}: choose from {', '.join(SCENES)}")
    try:
        blender = find_blender(args.blender)
    except AxiError as exc:
        raise SetupError(f"{exc.message}: pass --blender <path-to-blender> or set BLENDER_AXI_BLENDER")
    mcp_exe = Path(args.mcp_server) if args.mcp_server else default_mcp_exe()
    if not mcp_exe.is_file():
        raise SetupError(f"official server not found at {mcp_exe}: pass --mcp-server <exe> or install it with "
                         f"`{MCP_INSTALL[0]}` then `{MCP_INSTALL[1]}`")
    bench_tmp = paths.tmp_root() / "bench"
    scenes_dir, logs = bench_tmp / "scenes", bench_tmp / "logs"
    scenes_dir.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    counter = tokens.from_env(args.model)
    if counter.unit == "chars":
        print("note: no BLENDER_AXI_ANTHROPIC_KEY / ANTHROPIC_API_KEY set, reporting chars", file=sys.stderr)
    base = scenes_dir / "empty.blend"
    _NORMALISER["fn"] = lambda t: normalise(t, REPO, scenes_dir.resolve(), Path.home())
    if not args.skip_build:
        try:
            ensure_empty_blend(blender.path, base)
        except SceneError as exc:
            raise SetupError(one_line(exc, 400))
    return {"names": names, "blender": blender, "mcp_exe": mcp_exe, "scenes_dir": scenes_dir, "logs": logs,
            "counter": counter, "base": base, "results_dir": Path(args.results_dir).resolve()}


def write_outputs(results: dict, results_dir: Path) -> None:
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "results.json").write_bytes((json.dumps(results, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    (results_dir / "results.md").write_bytes(report.render_markdown(results).encode("utf-8"))
    (results_dir / "chart.svg").write_bytes(svg.chart_svg(results).encode("utf-8"))


def splice_readme(path: Path, results: dict) -> None:
    text = path.read_bytes().decode("utf-8")
    path.write_bytes(report.splice_readme(text, report.readme_block(results)).encode("utf-8"))


def run(args) -> int:
    ctx = setup(args)
    counter, results_dir = ctx["counter"], ctx["results_dir"]
    results_dir.mkdir(parents=True, exist_ok=True)
    scenes: dict[str, dict] = {}
    fixed_mcp_rec = None
    for i, name in enumerate(ctx["names"]):
        scene, fixed = run_scene(name, args, ctx, first=i == 0)
        scenes[name] = scene
        fixed_mcp_rec = fixed or fixed_mcp_rec
    skill_text = read_skill()
    skill = {"chars": len(skill_text), "tokens": counter.count(skill_text), "raw": "skills/blender-axi/SKILL.md"}
    unit = counter.unit  # final: a mid-run API failure switches the whole report to chars
    for scene in scenes.values():
        for task in scene["tasks"]:
            finalize_task(task, unit)
    results = {
        "schema": 1, "date": datetime.date.today().isoformat(), "unit": unit, "token_model": counter.model,
        "blender": ctx["blender"].version_string, "blender_axi": AXI_VERSION, "mcp_server": mcp_server_info(ctx["mcp_exe"]),
        "fixed": {"mcp_tools_list": fixed_mcp_rec, "axi_skill": skill}, "scenes": scenes,
    }
    write_outputs(results, results_dir)
    for line in artifacts.check_budget(results_dir):
        print(f"warning: results over budget: {line}", file=sys.stderr)
    if args.readme:
        try:
            splice_readme(Path(args.readme), results)
        except (OSError, ValueError) as exc:
            raise SetupError(f"could not update the README block: {exc}")
    tasks = [t for s in scenes.values() for t in s["tasks"]]
    failed = sum(1 for t in tasks if t.get("error"))
    print(f"done: {len(scenes)} scenes, {len(tasks)} tasks, {failed} failed, unit {unit} -> {results_dir}")
    return 1 if failed or fixed_mcp_rec is None else 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = parse_args(argv)
    try:
        return run(args)
    except (SetupError, SceneError, ServerError) as exc:
        print(f"error: {one_line(exc, 600)}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
