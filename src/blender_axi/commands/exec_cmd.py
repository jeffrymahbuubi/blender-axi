"""`exec`: run Python inside Blender, gated behind --allow-exec (plan U6, decisions D5/D6, Q6).

Safety: without --allow-exec this exits 2 before Blender is located or started. The original .blend is
never written unless --in-place; otherwise a modified file is saved as a copy (`--out` or under
`<tmp_root>/exec/`). A file that the code did not change is not saved at all.
"""

import os
import re
import secrets
import time
from pathlib import Path

from .. import paths, text
from ..errors import AxiError
from ..runner import SaveSpec
from ..spec import CommandSpec, Flag, Invocation, Positional, Result
from ._common import blend_candidates, display_path, invalid

NO_CHANGES = "none (no changes)"
_SAFE = re.compile(r"^[A-Za-z0-9_@%+=:,./\\~-]+$")
_MAX_INLINE_CODE = 200


def _quote(s: str) -> str:
    return s if _SAFE.match(s) else '"' + s.replace('"', '\\"') + '"'


def _globals_args(inv: Invocation) -> list[str]:
    g = inv.globals
    out: list[str] = []
    if g.json:
        out.append("--json")
    if g.full:
        out.append("--full")
    if g.debug:
        out.append("--debug")
    if g.keep_tmp:
        out.append("--keep-tmp")
    if g.blender:
        out += ["--blender", _quote(g.blender)]
    if g.timeout is not None:
        out += ["--timeout", f"{g.timeout:g}"]
    if g.live:
        out += ["--live", "--host", g.host, "--port", str(g.port)]
    return out


def _command_with_allow_exec(inv: Invocation) -> str:
    """The invocation as typed, plus --allow-exec (long inline code is shown as a placeholder)."""
    f = inv.flags
    parts = ["blender-axi", "exec"]
    if inv.positionals.get("file"):
        parts.append(_quote(inv.positionals["file"]))
    if f.get("code") is not None:
        code = f["code"]
        parts += ["--code", _quote(code) if len(code) <= _MAX_INLINE_CODE else "<python>"]
    if f.get("script") is not None:
        parts += ["--script", _quote(str(f["script"]))]
    if f.get("in_place"):
        parts.append("--in-place")
    if f.get("out") is not None:
        parts += ["--out", _quote(str(f["out"]))]
    if f.get("force"):
        parts.append("--force")
    parts += _globals_args(inv)
    parts.append("--allow-exec")
    return " ".join(parts)


def _usage_error(message: str, help: list[str]) -> AxiError:
    return invalid(message, help + ["blender-axi exec --help"])


def _infer_blend(given: str | None) -> tuple[str, Path]:
    """(display name, path). Positional `file`, else the single .blend in the cwd (plan D3).

    Unlike `_common.resolve_blend` it does not check the suffix of an explicit file and lists up to 10 names.
    """
    if given:
        return given, Path(given)
    cwd = Path.cwd()
    found = blend_candidates(cwd)
    if len(found) == 1:
        return found[0].name, found[0]
    if not found:
        raise _usage_error(f"no .blend file given and none in {cwd}",
                           ["Pass the file: `blender-axi exec <file.blend> --code <python> --allow-exec`"])
    names = [p.name for p in found]
    shown = names[:10] + ([f"... and {len(names) - 10} more"] if len(names) > 10 else [])
    raise _usage_error(f"{len(found)} .blend files in {cwd}; say which one to use",
                       ["Candidates: " + ", ".join(shown),
                        "Pass one: `blender-axi exec <file.blend> --code <python> --allow-exec`"])


def _read_script(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise AxiError("NOT_FOUND", f"script not found: {path}",
                       help=["Check the --script path, or pass the code inline with `--code <python>`"]) from None
    except (OSError, UnicodeDecodeError) as exc:
        raise _usage_error(f"cannot read script {path}: {exc}", []) from None


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.exists() and b.exists() and os.path.samefile(a, b)
    except OSError:
        return False


def _validate_args(inv: Invocation) -> tuple[bool, str | None]:
    """Pure argument checks (no filesystem). Returns (in_place, out)."""
    f = inv.flags
    code, script = f.get("code"), f.get("script")
    if (code is None) == (script is None):
        raise _usage_error(
            "give exactly one of --code <python> or --script <path>" + (" (got both)" if code is not None else " (got neither)"),
            ["Example: blender-axi exec <file.blend> --code \"result={'n': len(bpy.data.objects)}\" --allow-exec"])
    if code is not None and not code.strip():
        raise _usage_error("--code is empty", [])
    if f.get("in_place") and f.get("out") is not None:
        raise _usage_error("--in-place and --out are mutually exclusive",
                           ["--in-place overwrites the original file; --out saves a copy"])
    out = f.get("out")
    if out is not None and not str(out).lower().endswith(".blend"):
        raise _usage_error(f"--out must be a .blend path, got {out}", [])
    return bool(f.get("in_place")), (str(out) if out is not None else None)


def run(inv: Invocation) -> Result:
    f = inv.flags
    if inv.globals.live:  # before anything else: nothing may reach a live server
        raise _usage_error("exec does not support --live in v1",
                           ["Run it headless on a file: `blender-axi exec <file.blend> --code <python> --allow-exec`"])
    in_place, out_arg = _validate_args(inv)

    # The gate: nothing below touches Blender or the filesystem until --allow-exec is present.
    if not f.get("allow_exec"):
        raise AxiError(
            "EXEC_NOT_ALLOWED",
            "exec runs arbitrary Python inside Blender and needs --allow-exec",
            help=[f"Run the same command with --allow-exec: {_command_with_allow_exec(inv)}",
                  "The original .blend is only written with --in-place; otherwise changes go to a copy"],
        )

    display, blend = _infer_blend(inv.positionals.get("file"))
    if not blend.is_file():
        raise AxiError("NOT_FOUND", f"file not found: {display}",
                       help=["Check the path; `blender-axi` lists .blend files in the current directory"])
    blend = blend.resolve()
    code = f["code"] if f.get("code") is not None else _read_script(Path(f["script"]))

    backup_probe: tuple[Path, int | None] | None = None
    if in_place:
        save = SaveSpec("in_place", str(blend))
        bak = blend.with_name(blend.name + "1")
        backup_probe = (bak, bak.stat().st_mtime_ns if bak.exists() else None)
    else:
        if out_arg is not None:
            target = Path(out_arg).expanduser().resolve()
            if target == blend or _same_file(target, blend):
                raise _usage_error("--out points at the input file", ["Use --in-place to overwrite the original"])
            if target.exists() and not f.get("force"):
                raise AxiError("WRITE_REFUSED", f"{out_arg} already exists",
                               help=["Run the same command with --force to overwrite it, or pick another --out"])
        else:
            run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
            target = paths.exec_copy_path(blend, run_id)
        save = SaveSpec("copy", str(target))

    outcome = inv.transport.run("exec", {"code": code}, blend=blend, save=save, timeout=inv.globals.timeout)

    stdout = str(outcome.result.get("stdout") or outcome.stdout or "")
    shown, total, truncated = text.truncate(stdout, full=inv.globals.full)
    hints: list[str] = []
    if truncated:
        shown += f"\n... (truncated, {total} chars total)"
        hints.append("Run again with --full to see the whole stdout")

    data: dict = {
        "file": display,
        "status": "ok",
        "result": outcome.result.get("result", {}),
        "stdout": shown,
        "modified": outcome.modified,
        "saved": display_path(outcome.saved) if outcome.saved else NO_CHANGES,
    }
    if outcome.saved and backup_probe is not None:
        bak, before = backup_probe
        if bak.exists() and (before is None or bak.stat().st_mtime_ns != before):
            data["backup"] = display_path(str(bak))
    data["duration_ms"] = outcome.duration_ms
    if outcome.warnings:
        data["warning"] = "; ".join(outcome.warnings)
    if outcome.saved:
        hints.insert(0, f"Inspect the result: `blender-axi objects {_quote(display_path(outcome.saved))}`")
    return Result(data=data, help=hints)


SPEC = CommandSpec(
    name="exec",
    summary="Run Python inside Blender on a .blend (requires --allow-exec; original only changed with --in-place)",
    positionals=(Positional("file", required=False, help="The .blend file (default: the single .blend in the current directory)"),),
    flags=(
        Flag("--code", "str", help="Python to run; `bpy` is predefined; assign a dict to `result`"),
        Flag("--script", "path", help="Path to a Python file to run instead of --code"),
        Flag("--allow-exec", "bool", help="Required: confirm that arbitrary Python may run inside Blender"),
        Flag("--in-place", "bool", help="Save changes into the original file (a .blend1 backup may appear)"),
        Flag("--out", "path", help="Save changes as a copy at this .blend path (default: a copy under .tmp/exec/)"),
        Flag("--force", "bool", help="Allow --out to overwrite an existing file"),
    ),
    examples=(
        "blender-axi exec scene.blend --code \"result={'n': len(bpy.data.objects)}\" --allow-exec",
        "blender-axi exec scene.blend --script fix.py --out fixed.blend --allow-exec",
        "blender-axi exec scene.blend --script fix.py --in-place --allow-exec",
    ),
    run=run,
)
