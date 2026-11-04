"""`diff`: compare two .blend files (or a file with the open live session) object by object (0.6.0 U4).

Two `snapshot` op runs (two Blender starts headless) and a pure-Python compare in `_diff`. No rename detection.
"""

from __future__ import annotations

from pathlib import Path

from ..errors import AxiError
from ..spec import CommandSpec, Flag, Invocation, Positional, Result
from . import _diff
from ._common import Hints, carry_suffix, display_path, invalid, posix_path, quote_name, with_warnings

MAX_ROWS = 20


def _headless(opts):
    """The headless transport used for the file side of `--live` (tests replace this)."""
    from ..cli import _LazyHeadless

    return _LazyHeadless(opts)


def _blend(raw: str) -> Path:
    path = Path(raw)
    if path.suffix.lower() != ".blend":
        raise invalid(f"expected a .blend file, got '{raw}'", ["blender-axi diff --help"])
    if not path.is_file():
        raise AxiError("NOT_FOUND", f"file not found: {raw}",
                       help=["Run `blender-axi` to list .blend files in the current directory"])
    return path.resolve()


def _table(rows: list[dict], full: bool, label: str, cuts: list[str]) -> list[dict]:
    if full or len(rows) <= MAX_ROWS:
        return rows
    cuts.append(f"{label} {MAX_ROWS} of {len(rows)}")
    return rows[:MAX_ROWS]


def run(inv: Invocation) -> Result:
    live = inv.globals.live
    raw_a, raw_b = inv.positionals.get("a"), inv.positionals.get("b")
    if live and raw_b:
        raise invalid("--live compares <a> with the open session; drop <b>", ["blender-axi diff <a> --live"])
    if not live and not raw_b:
        raise invalid("diff needs two files (or one file with --live)",
                      ["blender-axi diff <a.blend> <b.blend>", "blender-axi diff <a.blend> --live"])
    objects = inv.flags.get("object")
    if objects is not None and not (objects and all(str(n).strip() for n in objects)):  # `--object ""` parses to []
        raise invalid("--object needs an object name", ["blender-axi diff --help"])
    path_a = _blend(raw_a)
    path_b = None if live else _blend(raw_b)

    out_a = (_headless(inv.globals) if live else inv.transport).run(
        "snapshot", {}, blend=path_a, timeout=inv.globals.timeout)
    out_b = inv.transport.run("snapshot", {}, blend=path_b, timeout=inv.globals.timeout)
    snap_a, snap_b = out_a.result, out_b.result
    label_a = display_path(str(path_a))
    if live:
        where = posix_path(snap_b.get("filepath") or "")
        label_b = f"(live session: {where})" if where else "(live session)"
    else:
        label_b = display_path(str(path_b))

    snap_a, snap_b, missing = _diff.restrict(snap_a, snap_b, objects, inv.flags.get("collection"))
    if missing:
        raise AxiError("NOT_FOUND", f"object '{missing[0]}' not found in either file",
                       help=[f"Run `blender-axi objects {quote_name(label_a)}` to list object names"])
    res = _diff.compare(snap_a, snap_b)
    added, removed, changed, scene = res["added"], res["removed"], res["changed"], res["scene"]
    n_a, n_b = len(snap_a["objects"]), len(snap_b["objects"])
    identical = not (added or removed or changed or scene)

    data: dict = {"a": label_a, "b": label_b,
                  "objects": f"{n_a} vs {n_b}: {len(added)} added, {len(removed)} removed, {len(changed)} changed, "
                             f"{res['unchanged']} unchanged"}
    if identical:
        data["changes"] = f"none ({n_a} objects, scene settings equal)"
    cuts: list[str] = []
    full = inv.globals.full
    for key, rows in (("added", added), ("removed", removed), ("changed", changed), ("scene", scene)):
        if rows:
            data[key] = _table(rows, full, key, cuts)
    if cuts:
        data["truncated"] = ", ".join(cuts) + " (--full shows all)"
    data["status"] = "identical" if identical else "different"
    data["duration_ms"] = out_a.duration_ms + out_b.duration_ms
    with_warnings(data, list(dict.fromkeys([*out_a.warnings, *out_b.warnings])))

    hints = Hints(inv)
    help_: list[str] = []
    if changed:
        target = "<file>" if live else quote_name(label_b)
        first = quote_name(changed[0]["name"])
        help_.append(f"Run `{hints.cmd(f'object {first} {target}')}` for the details")
    if cuts:
        again = f"blender-axi diff {quote_name(label_a)}" + ("" if live else f" {quote_name(label_b)}")
        help_.append(f"Run `{again}{carry_suffix(inv)} --full` to see all rows")
    if identical:
        help_.append(f"Run `blender-axi objects {quote_name(label_a)}` to list the objects")
    return Result(data=data, help=help_, exit_code=1 if inv.flags.get("exit_code") and not identical else 0)


SPEC = CommandSpec(
    name="diff",
    summary="Compare two .blend files: objects added, removed or changed (transform, visibility, materials, "
            "modifiers) and scene settings",
    positionals=(
        Positional("a", True, "The first .blend"),
        Positional("b", False, "The second .blend (omit with --live to compare <a> with the open session)"),
    ),
    flags=(
        Flag("--object", "list", split=False, help="Only these objects, repeatable"),
        Flag("--collection", "str", help="Only objects in this collection (by either file)"),
        Flag("--exit-code", "bool", help="Exit 1 when the files differ, 0 when identical (the report is still printed)"),
    ),
    examples=("blender-axi diff v1.blend v2.blend", "blender-axi diff v1.blend --live"),
    run=run,
)
