"""A/B showcase runner: official Blender MCP vs blender-axi, driven by `claude -p` (plan 2026-11-04-showcase, section 3.5).

    .venv/Scripts/python.exe benchmarks/showcase/run_ab.py --project turntable|qa_cleanup|build_export|all --arm axi|mcp|both
        [--repeats 3] [--model claude-opus-5-5] [--effort medium] [--max-turns 40] [--max-budget-usd 3]
        [--tmp .tmp/showcase] [--results benchmarks/showcase/results] [--axi-bin .venv/Scripts]
        [--mcp-server .tmp/mcpvenv/Scripts/blender-mcp.exe] [--blender <exe>] [--bare] [--dry-run]
        [--skip-replay] [--skip-report] [--claude claude]

Exit 0: every run completed (failed checks still exit 0). 1: a run crashed or was cut (error, timeout, turn or budget
limit) or the scrub gate found offences. 2: setup problem (one-line fix printed). The API key is read from
BLENDER_AXI_ANTHROPIC_KEY in the environment only (never from .env) and is shown as `<set,len N>`.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from sc import REPO, arms, harness, record  # noqa: E402

from blender_axi import paths  # noqa: E402
from blender_axi.errors import AxiError  # noqa: E402
from blender_axi.locate import find_blender  # noqa: E402

ARMS = ("axi", "mcp")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="run_ab.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", default="all", help="project name or `all`")
    ap.add_argument("--arm", default="both", choices=("axi", "mcp", "both"))
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--model", default=arms.MODEL)
    ap.add_argument("--effort", default=arms.EFFORT)
    ap.add_argument("--max-turns", type=int, default=arms.MAX_TURNS)
    ap.add_argument("--max-budget-usd", type=float, default=arms.MAX_BUDGET_USD)
    ap.add_argument("--tmp", default=None, help="work dir (default <blender-axi tmp root>/showcase)")
    ap.add_argument("--results", default=str(HERE / "results"))
    ap.add_argument("--axi-bin", default=None, help="dir holding blender-axi (default .venv/Scripts)")
    ap.add_argument("--mcp-server", default=None, help="official blender-mcp executable")
    ap.add_argument("--blender", default=None)
    ap.add_argument("--bare", action="store_true", help="isolate with `--bare` (drops the Write tool in claude 2.1.x; default: `--setting-sources \"\"` + fresh config dir)")
    ap.add_argument("--dry-run", action="store_true", help="print the argv per arm (key masked) and run nothing")
    ap.add_argument("--skip-replay", action="store_true")
    ap.add_argument("--skip-report", action="store_true")
    ap.add_argument("--publish-only", action="store_true",
                    help="no new runs: rebuild results from the run.json files already under --tmp (replays kept)")
    ap.add_argument("--claude", default="claude", help="claude executable (a .py file is run with this Python)")
    return ap.parse_args(argv)


def _abs(value: str | None, default: Path) -> Path:
    p = Path(value) if value else default
    return p if p.is_absolute() else (REPO / p).resolve()


def default_axi_bin() -> Path:
    for sub in ("Scripts", "bin"):
        if (REPO / ".venv" / sub).is_dir():
            return REPO / ".venv" / sub
    return Path(sys.executable).resolve().parent


def resolve_claude(value: str) -> list[str]:
    if value.lower().endswith(".py"):
        if not Path(value).is_file():
            raise arms.SetupError(f"claude stand-in {value} not found")
        return [sys.executable, str(Path(value).resolve())]
    found = shutil.which(value)
    if not found:
        raise arms.SetupError("`claude` not found on PATH: install Claude Code or pass --claude <exe>")
    return [found]


def load_projects():
    try:
        from sc import projects
    except ImportError as exc:
        raise arms.SetupError(f"sc/projects.py (unit U2) is not available: {exc}")
    return projects


def select_projects(projects, spec: str) -> list:
    names = projects.names()
    chosen = names if spec == "all" else [s.strip() for s in spec.split(",") if s.strip()]
    unknown = [n for n in chosen if n not in names]
    if not chosen or unknown:
        raise arms.SetupError(f"unknown project {', '.join(unknown) or '(none)'}: choose from {', '.join(names)} or all")
    return [projects.load(n) for n in chosen]


def setup(args: argparse.Namespace, projects=None) -> tuple[harness.Config, list, list[str]]:
    """Validate the environment; returns (config, projects, arm names). Raises SetupError."""
    projects = projects or load_projects()
    chosen = select_projects(projects, args.project)
    arm_names = list(ARMS) if args.arm == "both" else [args.arm]
    tmp = _abs(args.tmp, paths.tmp_root() / "showcase")
    axi_bin = _abs(args.axi_bin, default_axi_bin())
    mcp_exe = _abs(args.mcp_server, REPO / ".tmp" / "mcpvenv" / ("Scripts/blender-mcp.exe" if sys.platform == "win32"
                                                                  else "bin/blender-mcp"))
    cfg = harness.Config(tmp=tmp, claude=resolve_claude(args.claude), axi_bin=axi_bin, blender=Path("blender"),
                         projects=projects, mcp_exe=mcp_exe, model=args.model, effort=args.effort,
                         max_turns=args.max_turns, max_budget_usd=args.max_budget_usd, bare=args.bare)
    if args.dry_run:
        return cfg, chosen, arm_names
    arms.read_key()  # SetupError when unset
    try:
        info = find_blender(args.blender)
    except AxiError as exc:
        raise arms.SetupError(f"{exc.message}: pass --blender <path-to-blender> or set BLENDER_AXI_BLENDER")
    cfg.blender = info.path
    if "axi" in arm_names and not cfg.blender_axi_exe.is_file():
        raise arms.SetupError(f"blender-axi not found in {axi_bin}: pass --axi-bin <dir> (the venv Scripts dir)")
    if "mcp" in arm_names and not mcp_exe.is_file():
        raise arms.SetupError(f"official server not found at {mcp_exe}: pass --mcp-server <exe> (see token_bench.py setup)")
    if not cfg.skill.is_file():
        raise arms.SetupError(f"{cfg.skill} is missing")
    cfg.meta = {"claude_version": record.claude_version(cfg.claude), "blender": ".".join(map(str, info.version)),
                "mcp_server": record.mcp_server_info(mcp_exe) if "mcp" in arm_names else {"version": None, "commit": None}}
    return cfg, chosen, arm_names


def dry_run(cfg: harness.Config, chosen: list, arm_names: list[str], repeats: int) -> None:
    print("dry run: nothing is executed")
    print(f"env: ANTHROPIC_API_KEY={arms.mask_key()} (from {arms.KEY_VAR}), CLAUDE_CONFIG_DIR={cfg.tmp / 'claude-config'}, "
          f"PATH={cfg.axi_bin} first, CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1")
    for project in chosen:
        for arm_name in arm_names:
            argv = harness.argv_for(project, arm_name, cfg)
            note = " (port shown is a placeholder: each run draws a free port)" if arm_name == "mcp" else ""
            print(f"== {project.name} / {arm_name}: cwd {cfg.tmp / 'runs' / project.name / arm_name / 'r<k>'}, "
                  f"{repeats} repeat(s){note}")
            for item in argv:
                print("  " + json.dumps(item, ensure_ascii=False))


def order_for(repeat: int, arm_names: list[str]) -> list[str]:
    """Alternate the start order per repeat (axi first on odd repeats) so neither arm always runs first."""
    return arm_names if repeat % 2 == 1 else list(reversed(arm_names))


def run_matrix(cfg: harness.Config, chosen: list, arm_names: list[str], repeats: int) -> tuple[list[dict], int]:
    rc, runs = 0, []
    for project in chosen:
        for repeat in range(1, repeats + 1):
            for arm_name in order_for(repeat, arm_names):
                label = f"{project.name}/{arm_name}/r{repeat}"
                print(f"running {label} ...", flush=True)
                try:
                    run = harness.run_one(project, arm_name, repeat, cfg)
                except arms.SetupError:
                    raise
                except Exception as exc:  # a crashed run is reported, never re-rolled
                    print(f"{label}  CRASHED {type(exc).__name__}: {str(exc)[:300]}", flush=True)
                    rc = 1
                    continue
                runs.append(run)
                check = run["check"]
                print(f"{label}  stop {run['stop']}  turns {run['num_turns']}  calls {len(run['tool_calls'])}  "
                      f"${run['total_cost_usd']:.4f}  {run['duration_s']}s  check "
                      f"{'passed' if check.get('passed') else 'FAILED'}", flush=True)
                if run["stop"] != "end_turn":
                    rc = 1
    return runs, rc


# ---------------------------------------------------------------------------------------------------------------
# replay, report, scrub (units U3 and U4; imported lazily, missing modules are reported, not fatal)
# ---------------------------------------------------------------------------------------------------------------


def _stage(name: str, fn) -> bool:
    try:
        fn()
        return True
    except ImportError as exc:
        print(f"{name} skipped: module not available ({exc})", flush=True)
    except Exception as exc:
        print(f"{name} FAILED: {type(exc).__name__}: {str(exc)[:300]}", flush=True)
    return False


def run_replay(cfg: harness.Config, runs: list[dict]) -> bool:
    from sc import replay

    ok = True
    for run in runs:
        run_dir = cfg.tmp / "runs" / run["project"] / run["arm"] / f"r{run['repeat']}"
        print(f"replay {run['project']}/{run['arm']}/r{run['repeat']} ...", flush=True)
        ok &= _stage("replay", lambda d=run_dir: replay.run(d, cfg))
    return ok


def representative(runs: list[dict], project: str, arm: str) -> dict | None:
    """First passed repeat, else the first repeat (deterministic, no cherry-picking by cost)."""
    cell = sorted((r for r in runs if r["project"] == project and r["arm"] == arm), key=lambda r: r["repeat"])
    return next((r for r in cell if r["check"].get("passed")), cell[0] if cell else None)


def _copy_artifacts(cfg: harness.Config, run: dict, dest: Path) -> Path | None:
    """transcript, GIF and final render of one representative run into results/<project>/; returns the final png."""
    from bench.scenes import strip_png_text

    src = cfg.tmp / "runs" / run["project"] / run["arm"] / f"r{run['repeat']}"
    arm = run["arm"]
    dest.mkdir(parents=True, exist_ok=True)
    if (src / "transcript.md").is_file():  # normalised again (idempotent) so older runs pick up normaliser fixes
        text = src.joinpath("transcript.md").read_text(encoding="utf-8")
        text = record.get_normaliser()(text, cfg.repo, cfg.tmp, Path.home(), harness._user())
        dest.joinpath(f"transcript_{arm}.md").write_bytes(text.encode("utf-8"))
    if (src / "replay.gif").is_file():
        shutil.copyfile(src / "replay.gif", dest / f"replay_{arm}.gif")
    renders = sorted((src / "frames").glob("*_render.png")) if (src / "frames").is_dir() else []
    if (src / "showcase.png").is_file():  # the task's deliverable, copied by the harness
        renders.append(src / "showcase.png")
    if not renders:
        return None
    final = dest / f"final_{arm}.png"
    _fit_png(renders[-1], final)
    strip_png_text(final)
    return final


def _fit_png(src: Path, out: Path, cap: int = 380_000) -> None:
    """Copy `src` to `out` as a PNG under the scrub cap (400 KB): shrink the width (1280, 960, 720 px) until it fits."""
    from PIL import Image

    with Image.open(src) as img:
        img = img.convert("RGB")
        for width in (img.width, 1280, 960, 720, 480):
            if width > img.width:
                continue
            frame = img if width == img.width else img.resize((width, round(img.height * width / img.width)))
            frame.save(out, "PNG", optimize=True)
            if out.stat().st_size <= cap:
                return


def publish(cfg: harness.Config, runs: list[dict], results: Path, replayed: bool) -> bool:
    from sc import report

    results.mkdir(parents=True, exist_ok=True)
    agg = report.aggregate(cfg.tmp / "runs")
    record.write_json(results / "runs.json", agg)
    projects = sorted({r["project"] for r in runs})
    for name in projects:
        finals = {}
        for arm in ARMS:
            run = representative(runs, name, arm)
            if run is not None:
                finals[arm] = _copy_artifacts(cfg, run, results / name)
        if finals.get("axi") and finals.get("mcp"):
            report.side_by_side(name, finals["axi"], finals["mcp"], agg["cells"].get(name, {}),
                                results / name / "side_by_side.png")
        elif len(finals) == 2:
            print(f"side_by_side for {name} skipped: a final render is missing (run without --skip-replay)", flush=True)
    report.write_results_md(agg, results)
    if replayed:
        _stage("mp4", lambda: _pair_mp4s(cfg, runs))
    print(f"wrote {results / 'runs.json'} and results.md", flush=True)
    return True


def _pair_mp4s(cfg: harness.Config, runs: list[dict]) -> None:
    from sc import replay

    out_dir = cfg.tmp / "release"
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in sorted({r["project"] for r in runs}):
        a, m = representative(runs, name, "axi"), representative(runs, name, "mcp")
        if a and m:
            dirs = [cfg.tmp / "runs" / r["project"] / r["arm"] / f"r{r['repeat']}" for r in (a, m)]
            replay.pair_mp4(dirs[0], dirs[1], out_dir / f"{name}.mp4")


def scrub_gate(results: Path) -> int:
    from sc import scrub

    offences = scrub.check(results)
    for item in offences:
        print(f"scrub: {item}", flush=True)
    print(f"scrub: {len(offences)} offence(s) in {results}", flush=True)
    return 1 if offences else 0


def main(argv: list[str] | None = None, projects=None) -> int:
    args = parse_args(argv)
    try:
        cfg, chosen, arm_names = setup(args, projects)
        if args.dry_run:
            dry_run(cfg, chosen, arm_names, args.repeats)
            return 0
        if args.publish_only:
            names = {p.name for p in chosen}
            runs = [json.loads(f.read_text(encoding="utf-8")) for f in sorted((cfg.tmp / "runs").glob("*/*/r*/run.json"))]
            runs = [r for r in runs if r.get("project") in names and r.get("arm") in arm_names]
            ok = bool(runs) and _stage("report", lambda: publish(cfg, runs, _abs(args.results, HERE / "results"), True))
            print(f"published {len(runs)} existing run(s)", flush=True)
            return 0 if ok and not scrub_gate_safe(_abs(args.results, HERE / "results")) else 1
        cfg.tmp.mkdir(parents=True, exist_ok=True)
        for project in chosen:
            print(f"preparing start scene for {project.name} ...", flush=True)
            cfg.projects.prepare_start(project, cfg.tmp, cfg.blender)
        runs, rc = run_matrix(cfg, chosen, arm_names, args.repeats)
    except arms.SetupError as exc:
        print(f"setup error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 1
    replayed = False
    if runs and not args.skip_replay:
        replayed = run_replay(cfg, runs)
    if runs and not args.skip_report:
        results = _abs(args.results, HERE / "results")
        if not _stage("report", lambda: publish(cfg, runs, results, replayed)):
            rc = 1
        elif scrub_gate_safe(results):
            rc = 1
    total = sum(r["total_cost_usd"] for r in runs)
    print(f"done: {len(runs)} run(s), estimated cost ${total:.2f}", flush=True)
    return rc


def scrub_gate_safe(results: Path) -> bool:
    """True when the gate ran and found offences (or crashed)."""
    try:
        return scrub_gate(results) != 0
    except ImportError as exc:
        print(f"scrub skipped: module not available ({exc})", flush=True)
        return False
    except Exception as exc:
        print(f"scrub FAILED: {type(exc).__name__}: {str(exc)[:300]}", flush=True)
        return True


if __name__ == "__main__":
    sys.exit(main())
