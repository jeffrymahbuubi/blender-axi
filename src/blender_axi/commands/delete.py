"""`delete`: remove objects from a .blend (or the open session with --live); their children stay where they are.

Built with the data API (see `op_delete`). All-or-nothing: one missing name deletes nothing. Children of a deleted
object are unparented with their world transform kept and listed as `children_kept`.
Headless writes need `--in-place` or `--out <copy.blend>` (`_mutate`).
"""

from __future__ import annotations

from ..errors import AxiError
from ..spec import CommandSpec, Invocation, Positional, Result
from . import _mutate
from ._common import Hints, invalid, quote_name, translate_not_found


def _names(inv: Invocation) -> list[str]:
    """`"A, B"` -> ["A", "B"] (trimmed, empties and repeats dropped); nothing left -> exit 2."""
    names: list[str] = []
    for part in str(inv.positionals["names"]).split(","):
        part = part.strip()
        if part and part not in names:
            names.append(part)
    if not names:
        raise invalid("no object names given", ["Example: " + SPEC.examples[0]])
    return names


def run(inv: Invocation) -> Result:
    names = _names(inv)
    try:
        target, outcome = _mutate.run_mutation(inv, SPEC, "delete", {"names": names})
    except AxiError as err:
        if err.message.startswith("file not found"):
            raise
        given = inv.positionals.get("file")
        objects = f"objects {quote_name(given)}" if given and not inv.globals.live else "objects"
        raise translate_not_found(err, [f"Run `{Hints(inv).cmd(objects)}` to list object names"]) from err
    r = outcome.result
    data: dict = {"file": target.label, "deleted": r["deleted"]}
    if r["children_kept"]:
        data["children_kept"] = r["children_kept"]
    n = r["remaining"]
    data["remaining"] = f"{n} object{'s' if n != 1 else ''}"
    return _mutate.finish(inv, SPEC, target, outcome, data, [])


SPEC = CommandSpec(
    name="delete",
    summary="Delete objects from a .blend (or the open session with --live); children stay in place",
    positionals=(
        Positional("names", help="Comma-separated object names, e.g. Cube,Ball (all or nothing)"),
        Positional("file", required=False, help="The .blend file (default: the single .blend in the current directory)"),
    ),
    flags=(*_mutate.MUTATE_FLAGS,),
    examples=(
        "blender-axi delete Cube scene.blend --in-place",
        "blender-axi delete Cube,Ball scene.blend --out scene2.blend",
        "blender-axi delete Cube --live",
    ),
    run=run,
)
