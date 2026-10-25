"""Build the benchmark scenes headlessly and render them (all through the real `blender-axi` CLI).

`ensure_empty_blend` makes the shared empty base; `build_scene` runs `benchmarks/scenes/<name>.py` on it with
`blender-axi exec`; `render_scene` writes a small WORKBENCH PNG. Temp files live under the project `.tmp/` only.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from blender_axi import paths

EMPTY_EXPR = ("import bpy; bpy.ops.wm.read_factory_settings(use_empty=True); "
              "bpy.ops.wm.save_as_mainfile(filepath={path!r})")


class SceneError(RuntimeError):
    """A Blender or blender-axi step failed; the message carries the tail of its output."""


def _cli_env(repo: Path) -> dict[str, str]:
    env = paths.blender_env()
    env["BLENDER_AXI_TMP"] = str(paths.tmp_root())
    src = str(Path(repo) / "src")
    env["PYTHONPATH"] = src + os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else src
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def ensure_empty_blend(blender_exe: Path, path: Path) -> Path:
    """Create `path` (a .blend with no objects) once; an existing file is reused."""
    path = Path(path)
    if path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    argv = [str(blender_exe), "--background", "--factory-startup", "--python-exit-code", "3",
            "--python-expr", EMPTY_EXPR.format(path=path.as_posix())]
    proc = subprocess.run(argv, env=paths.blender_env(), cwd=str(path.parent), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300)
    if proc.returncode != 0 or not path.is_file():
        raise SceneError(f"could not create the empty base {path} (exit {proc.returncode}): {proc.stderr[-600:]}")
    return path


def _run_cli(python_exe: str, repo: Path, args: list[str], timeout: float) -> dict:
    argv = [python_exe, "-m", "blender_axi", *args, "--json"]
    proc = subprocess.run(argv, cwd=str(repo), env=_cli_env(repo), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        data = None
    if proc.returncode != 0 or not isinstance(data, dict):
        tail = (proc.stdout[-500:] + proc.stderr[-300:]).strip()
        raise SceneError(f"`blender-axi {args[0]}` failed (exit {proc.returncode}): {tail}")
    return data


def build_scene(python_exe: str, repo: Path, name: str, base: Path, out: Path, timeout: float = 600) -> dict:
    """Run benchmarks/scenes/<name>.py on `base`, save the result as `out`; returns the script's `result` dict."""
    repo = Path(repo)
    script = repo / "benchmarks" / "scenes" / f"{name}.py"
    if not script.is_file():
        raise SceneError(f"scene script not found: {script}")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    data = _run_cli(python_exe, repo, ["exec", str(base), "--script", str(script), "--out", str(out), "--force",
                                       "--allow-exec"], timeout)
    if not Path(out).is_file():
        raise SceneError(f"exec did not write {out}: {json.dumps(data)[:300]}")
    return data.get("result", data)


def render_scene(python_exe: str, repo: Path, blend: Path, out_png: Path, engine: str = "WORKBENCH",
                 size: str = "320x180") -> dict:
    """Headless render of the scene camera to `out_png`; returns the CLI's JSON data."""
    Path(out_png).parent.mkdir(parents=True, exist_ok=True)
    data = _run_cli(python_exe, Path(repo), ["render", str(blend), "--engine", engine, "--resolution", size,
                                             "--out", str(out_png), "--force"], 600)
    if not Path(out_png).is_file():
        raise SceneError(f"render did not write {out_png}: {json.dumps(data)[:300]}")
    strip_png_text(Path(out_png))
    return data


def strip_png_text(path: Path) -> None:
    """Drop tEXt/zTXt/iTXt chunks: Blender stamps the source .blend's absolute path into them (`File`)."""
    raw = path.read_bytes()
    sig, pos, kept = raw[:8], 8, []
    if sig != b"\x89PNG\r\n\x1a\n":
        raise SceneError(f"not a PNG: {path}")
    while pos < len(raw):
        length = int.from_bytes(raw[pos:pos + 4], "big")
        chunk = raw[pos:pos + 12 + length]
        if chunk[4:8] not in (b"tEXt", b"zTXt", b"iTXt"):
            kept.append(chunk)
        pos += 12 + length
    path.write_bytes(sig + b"".join(kept))
