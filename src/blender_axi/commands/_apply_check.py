"""`apply --check` and `apply --strict` (0.5.0): the validation summary and the target simulation.

`--check` never needs the file and never starts Blender: it prints a summary (steps, commands, targets, plan, status) and
one replay hint. `--strict` adds a read-only `names` request (one Blender start, or one live call) and walks the recipe in
order against the listed names, so a step that targets an object nothing created is reported before anything runs.
"""

from __future__ import annotations

import dataclasses
import re
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

from ..blender_side import op_add
from ..blender_side.common import SCENE_COLLECTION
from ..errors import AxiError
from ..spec import CommandSpec, Invocation, Result
from . import _mutate
from ._common import Hints, blend_candidates, display_path, invalid, quote_name, resolve_blend

if TYPE_CHECKING:
    from .apply import _Step

CANONICAL = ("add", "set", "delete", "material", "modifier", "configure", "import")
PLAN_LINES = 20
UNCHECKED = "unchecked (add --strict to verify object names against the file)"
_SUFFIX = re.compile(r"^(.*)\.\d{3}$")


# -- the label of the file ------------------------------------------------------------------------

def resolve(inv: Invocation, spec: CommandSpec, strict: bool) -> tuple[str, Path | None, bool]:
    """(`file:` label, path for `names` or None, known): `known` is False for a missing or unidentified file.

    Plain `--check` never fails on the file; `--strict` and `--in-place/--out` keep the regular resolution."""
    if inv.globals.live:
        if inv.positionals.get("file"):
            raise invalid("--live uses the file open in Blender; drop <file>", ["blender-axi apply <steps> --live"])
        return "(live session)", None, True
    if inv.flags.get("in_place") or inv.flags.get("out"):
        target = _mutate.resolve_target(inv, spec)
        return target.label, target.blend, True
    if strict:
        blend, label = resolve_blend(inv)
        assert label is not None
        return label, blend, True
    given = inv.positionals.get("file")
    if given:
        exists = Path(given).is_file()
        return display_path(given) + ("" if exists else " (not found)"), None, exists
    found = blend_candidates()
    if len(found) == 1:
        return found[0].name, None, True
    return "(none)", None, False


# -- --check --------------------------------------------------------------------------------------

def _commands(steps: list[_Step]) -> str:
    counts = Counter(s.cmd for s in steps)
    order = [c for c in CANONICAL if c in counts] + sorted(c for c in counts if c not in CANONICAL)
    return ", ".join(f"{c} {counts[c]}" for c in order)


def _plan(inv: Invocation, steps: list[_Step]) -> list[str]:
    lines = [f"{i} " + " ".join(quote_name(t) for t in (s.cmd, *s.argv)) for i, s in enumerate(steps, 1)]
    if not inv.globals.full and len(lines) > PLAN_LINES:
        lines = [*lines[:PLAN_LINES], f"... ({len(lines) - PLAN_LINES} more; --full shows all)"]
    return lines


def _replay_hint(inv: Invocation, spec: CommandSpec, known: bool) -> str:
    drop = ("--check", "--strict", "--force", "--in-place", "--out")
    if inv.globals.live:
        return f"Run `{_mutate.replay(inv, spec, '', drop).rstrip()}` to apply them to the open session"
    shown = inv if known else dataclasses.replace(inv, positionals={**inv.positionals, "file": "<file.blend>"})
    return f"Run `{_mutate.replay(shown, spec, '--in-place', drop)}` to apply them"


def check(inv: Invocation, spec: CommandSpec, steps: list[_Step], steps_label: str, warnings: list[str],
          strict: bool) -> Result:
    label, blend, known = resolve(inv, spec, strict)
    warnings = list(warnings)
    targets = UNCHECKED
    if strict:
        if inv.globals.live:
            inv.transport.run("ping", {}, blend=None, timeout=inv.globals.timeout)
        targets, more = verify(inv, steps, label, blend, steps_label)
        warnings += more
    data: dict = {"file": label, "steps": len(steps), "commands": _commands(steps), "targets": targets}
    if warnings:
        data["warning"] = "; ".join(warnings)
    data["plan"] = _plan(inv, steps)
    data["status"] = "valid"
    return Result(data=data, help=[_replay_hint(inv, spec, known)])


# -- --strict -------------------------------------------------------------------------------------

def _unique(name: str, known: set[str]) -> str:
    """The name Blender gives a new object: `Cube` -> `Cube.001` when `Cube` is taken."""
    if name not in known:
        return name
    match = _SUFFIX.match(name)
    base = match.group(1) if match else name
    n = 1
    while f"{base}.{n:03d}" in known:
        n += 1
    return f"{base}.{n:03d}"


class _Simulation:
    def __init__(self, inv: Invocation, label: str, steps_label: str, listing: dict):
        self.label, self.steps_label = label, steps_label
        self.objects = {str(o["name"]) for o in listing.get("objects") or []}
        self.collections = {str(c) for c in listing.get("collections") or []} | {SCENE_COLLECTION}
        self.import_step: int | None = None  # after an import the names are no longer fully known
        self.warnings: list[str] = []
        given = inv.positionals.get("file")
        listing_cmd = f"objects {quote_name(given)}" if given and not inv.globals.live else "objects"
        self.list_hint = f"Run `{Hints(inv).cmd(listing_cmd)}` to list object names"

    def need(self, i: int, cmd: str, kind: str, name) -> None:
        pool = self.collections if kind == "collection" else self.objects
        if not name or str(name) in pool:
            return
        if self.import_step is not None:
            note = (f"step {i} ({cmd}): {kind} '{name}' is not in {self.label}; "
                    f"it may come from the import in step {self.import_step}")
            if note not in self.warnings:
                self.warnings.append(note)
            return
        raise AxiError("NOT_FOUND", f"step {i} ({cmd}): {kind} '{name}' not found in {self.label} "
                                    "(nor created by an earlier step)",
                       help=[self.list_hint, f"Fix step {i} in {self.steps_label} (nothing ran)"])

    def step(self, i: int, cmd: str, p: dict) -> None:
        if cmd == "add":
            self.need(i, cmd, "collection", p.get("collection"))
            kind = p.get("kind")
            default = (p.get("light_type") or "POINT").title() if kind == "light" else op_add.DEFAULT_NAMES.get(kind, "Empty")
            self.objects.add(_unique(p.get("name") or default, self.objects))
        elif cmd == "set":
            self.need(i, cmd, "object", p.get("name"))
            if p.get("parent") not in (None, "none"):
                self.need(i, cmd, "object", p["parent"])
            self.need(i, cmd, "collection", p.get("collection"))
            if p.get("new_name") and p["new_name"] != p.get("name"):
                self.objects.discard(p.get("name"))
                self.objects.add(_unique(p["new_name"], self.objects))
        elif cmd == "delete":
            for name in p.get("names") or []:
                self.need(i, cmd, "object", name)
            self.objects -= set(p.get("names") or [])
        elif cmd == "material":
            self.need(i, cmd, "object", p.get("object"))
        elif cmd == "modifier":
            self.need(i, cmd, "object", p.get("object"))
            settings = p.get("settings") or {}
            for key in ("object", "mirror_object"):
                self.need(i, cmd, "object", settings.get(key))
        elif cmd == "configure":
            self.need(i, cmd, "object", p.get("camera"))
        elif cmd == "import":
            if p.get("collection"):
                self.collections.add(p["collection"])
            if self.import_step is None:
                self.import_step = i


def verify(inv: Invocation, steps: list[_Step], label: str, blend: Path | None,
           steps_label: str = "the steps") -> tuple[str, list[str]]:
    """--strict: one `names` request, then an in-order simulation. Returns (targets_text, warnings); raises AxiError."""
    listing = inv.transport.run("names", {}, blend=blend, timeout=inv.globals.timeout).result
    sim = _Simulation(inv, label, steps_label, listing)
    total = f"{len(sim.objects)} objects, {len(listing.get('collections') or [])} collections"
    for i, s in enumerate(steps, 1):
        sim.step(i, s.cmd, s.params)
    return f"verified against {label} ({total})", sim.warnings
