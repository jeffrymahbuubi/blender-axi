"""The three showcase projects: loading, deterministic start files and the plain-bpy checkers (plan section 3.3).

    from sc import projects
    p = projects.load("qa_cleanup")
    start = projects.prepare_start(p, tmp, blender)      # <tmp>/scenes/<project>/start.blend (+ start.json)
    verdict = projects.run_check(p, blender, run_dir / "out")

`projects/<name>/` holds `meta.json`, `prompt.md` (byte-identical for both arms; `{WORK}` and `{OUT}` placeholders),
`setup.py` (only `qa_cleanup`: the deterministic mess, run through `blender-axi exec --in-place`) and `check.py`
(the judge: plain bpy run by `blender --background --factory-startup`, never imports blender_axi, prints `CHECK {json}`).
Temp files stay under `paths.tmp_root()` (Blender's TEMP/TMP/TMPDIR included).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

SC_DIR = Path(__file__).resolve().parent
SHOWCASE = SC_DIR.parent
REPO = SHOWCASE.parents[1]
PROJECTS_DIR = SHOWCASE / "projects"
ORDER = ("turntable", "qa_cleanup", "build_export")  # the order of the README; other directories follow alphabetically
STARTS = ("desk", "city_messy", "empty")
PROMPT_END = ("Write every output under `{OUT}` (it already exists) and do not create anything outside it or `{WORK}`; "
              "write text files such as reports with the Write tool.")
CHECK_PREFIX = "CHECK "
AXI_TIMEOUT = 600.0
CHECK_TIMEOUT = 300.0

for _p in (str(REPO / "src"), str(REPO / "benchmarks")):  # the CLI and the bench helpers are not installed
    if _p not in sys.path:
        sys.path.insert(0, _p)

from blender_axi import paths  # noqa: E402
from bench import scenes  # noqa: E402


class ProjectError(RuntimeError):
    """A project is missing or malformed, or a start file could not be built; the message is the one-line fix."""


@dataclass(frozen=True)
class Project:
    name: str
    title: str
    start: str      # "desk" | "city_messy" | "empty"
    prompt: str     # text of prompt.md with the {WORK} and {OUT} placeholders still in
    dir: Path


def names() -> list[str]:
    """Project names that have a meta.json, README order first."""
    found = sorted(p.name for p in PROJECTS_DIR.iterdir() if (p / "meta.json").is_file()) if PROJECTS_DIR.is_dir() else []
    return [n for n in ORDER if n in found] + [n for n in found if n not in ORDER]


def load(name: str) -> Project:
    pdir = PROJECTS_DIR / name
    meta_path = pdir / "meta.json"
    if not meta_path.is_file():
        raise ProjectError(f"unknown project '{name}'; choose one of: {', '.join(names())}")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        prompt = (pdir / "prompt.md").read_text(encoding="utf-8").strip("\n")
    except (OSError, ValueError) as exc:
        raise ProjectError(f"project '{name}' is unreadable: {exc}") from None
    if meta.get("name") != name or meta.get("start") not in STARTS or not meta.get("title"):
        raise ProjectError(f"{meta_path}: needs name == '{name}', a title and start in {STARTS}")
    if "{WORK}" not in prompt or "{OUT}" not in prompt or not prompt.endswith(PROMPT_END):
        raise ProjectError(f"{pdir / 'prompt.md'}: must use {{WORK}} and {{OUT}} and end with the output-rule sentence")
    return Project(name=name, title=meta["title"], start=meta["start"], prompt=prompt, dir=pdir)


def meta(project: Project) -> dict:
    """The raw meta.json (`expect`, `commands_hint`; the README uses it, the agents never see it)."""
    return json.loads((project.dir / "meta.json").read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# -- start files ----------------------------------------------------------------------------------

def _axi(blender: Path, args: list[str], timeout: float = AXI_TIMEOUT) -> dict:
    """`python -m blender_axi <args> --json` from this checkout's src; returns the parsed JSON or raises."""
    env = paths.blender_env()
    env["BLENDER_AXI_TMP"] = str(paths.tmp_root())
    src = str(REPO / "src")
    env["PYTHONPATH"] = src + os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else src
    env["PYTHONIOENCODING"] = "utf-8"
    argv = [sys.executable, "-m", "blender_axi", *args, "--blender", str(blender), "--json"]
    proc = subprocess.run(argv, cwd=str(REPO), env=env, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        data = None
    if proc.returncode != 0 or not isinstance(data, dict):
        raise ProjectError(f"`blender-axi {args[0]}` failed (exit {proc.returncode}): "
                           f"{(proc.stdout[-500:] + proc.stderr[-300:]).strip()}")
    return data


def _exec_script(blender: Path, blend: Path, script: Path, out: Path | None) -> dict:
    args = ["exec", str(blend), "--script", str(script), "--allow-exec"]
    args += ["--out", str(out), "--force"] if out is not None else ["--in-place"]
    data = _axi(blender, args)
    return data.get("result", data)


def prepare_start(project: Project, tmp: Path, blender: Path) -> Path:
    """Build `<tmp>/scenes/<project>/start.blend` once (a matching sha256 in start.json means: reuse) and return it."""
    tmp, blender = Path(tmp), Path(blender)
    out = tmp / "scenes" / project.name / "start.blend"
    info = out.with_name("start.json")
    if out.is_file() and info.is_file():
        try:
            if json.loads(info.read_text(encoding="utf-8")).get("sha256") == sha256(out):
                return out
        except (OSError, ValueError):
            pass
    out.parent.mkdir(parents=True, exist_ok=True)
    for stale in (out, info, out.with_suffix(".blend1")):
        stale.unlink(missing_ok=True)
    if project.start == "empty":
        scenes.ensure_empty_blend(blender, out)
        result: dict = {"objects": 0}
    else:
        base = scenes.ensure_empty_blend(blender, tmp / "scenes" / "_base" / "empty.blend")
        scene = "desk" if project.start == "desk" else "city"
        result = _exec_script(blender, base, REPO / "benchmarks" / "scenes" / f"{scene}.py", out)
        if project.start == "city_messy":
            result = _exec_script(blender, out, project.dir / "setup.py", None)
            out.with_suffix(".blend1").unlink(missing_ok=True)
    if not out.is_file():
        raise ProjectError(f"the start file for '{project.name}' was not written: {out}")
    record = {"project": project.name, "start": project.start, "sha256": sha256(out), "bytes": out.stat().st_size,
              "result": result}
    info.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return out


# -- the judge ------------------------------------------------------------------------------------

def _infer_start(project: Project, out_dir: Path) -> Path | None:
    """`<tmp>/runs/<project>/<arm>/r<k>/out` -> `<tmp>/scenes/<project>/start.blend` (the frozen layout, plan 3.1)."""
    parts = out_dir.parents
    if len(parts) > 4:
        candidate = parts[4] / "scenes" / project.name / "start.blend"
        if candidate.is_file():
            return candidate
    return None


def _parse_check(stdout: str) -> dict | None:
    for line in reversed(stdout.splitlines()):
        if line.startswith(CHECK_PREFIX):
            try:
                data = json.loads(line[len(CHECK_PREFIX):])
            except ValueError:
                return None
            return data if isinstance(data, dict) else None
    return None


def run_check(project: Project, blender: Path, out_dir: Path, start: Path | None = None) -> dict:
    """Run check.py in a plain Blender; returns {"passed": bool, "checks": {...}, "notes": [...]} (a crash is a failure)."""
    out_dir = Path(out_dir).resolve()
    start = Path(start) if start is not None else _infer_start(project, out_dir)
    argv = [str(blender), "--background", "--factory-startup", "--python-exit-code", "3", "--python",
            str(project.dir / "check.py"), "--", str(out_dir), str(start) if start else "-"]
    cwd = out_dir.parent if out_dir.parent.is_dir() else REPO
    try:
        proc = subprocess.run(argv, cwd=str(cwd), env=paths.blender_env(), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=CHECK_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"passed": False, "checks": {}, "notes": [f"checker did not run: {exc}"]}
    data = _parse_check(proc.stdout)
    if data is None:
        tail = (proc.stderr[-600:] or proc.stdout[-600:]).strip()
        return {"passed": False, "checks": {}, "notes": [f"checker crashed (exit {proc.returncode}): {tail}"]}
    checks = {str(k): bool(v) for k, v in dict(data.get("checks", {})).items()}
    notes = [str(n) for n in data.get("notes", [])]
    return {"passed": bool(checks) and all(checks.values()) and bool(data.get("passed", False)), "checks": checks,
            "notes": notes}
