"""`audit`: read-only asset checks over a .blend (0.6.0 plan §3.4, K3). The op does the work; this formats.

Exit 0 whatever is found (findings are data, not failures); `--fail-on error|warn` (`--strict` = `--fail-on warn`)
exits 1 when a finding of that level or worse exists and still prints the report (`Result.exit_code`).
"""

from __future__ import annotations

from ..blender_side.audit_checks import CHECK_IDS, LEVELS, SCENE_IDS
from ..blender_side.common import count
from ..errors import AxiError
from ..spec import CommandSpec, Flag, Invocation, Positional, Result
from ._common import Hints, invalid, posix_path, quote_name, resolve_blend, translate_not_found, with_warnings

MAX_ITEMS = 5
CAMERA_HINT = "add camera <file> --location 7,-7,5 --rotation 63.6,0,46.7"
CLEANUP_HINT = "Apply scale (Ctrl+A) and clean up meshes in Blender, or via exec --allow-exec"


def _validated(inv: Invocation) -> tuple[list[str] | None, list[str] | None, str | None, list[str] | None, str | None]:
    checks, objects, collection = inv.flags.get("checks"), inv.flags.get("object"), inv.flags.get("collection")
    ignore, fail_on = inv.flags.get("ignore"), inv.flags.get("fail_on")
    if ignore is not None and not ignore:
        raise invalid("--ignore needs at least one check id", ["Valid checks: " + ", ".join(CHECK_IDS)])
    if ignore and checks:
        raise invalid("--checks already selects the checks; drop --ignore", ["blender-axi audit --help"])
    if ignore and set(ignore) >= set(CHECK_IDS):
        raise invalid("--ignore would skip every check; leave at least one", ["Valid checks: " + ", ".join(CHECK_IDS)])
    if inv.flags.get("strict") and fail_on == "error":
        raise invalid("--strict means --fail-on warn; pass one of them", ["blender-axi audit --help"])
    if checks is not None and not checks:
        raise invalid("--checks needs at least one check id", ["Valid checks: " + ", ".join(CHECK_IDS)])
    if objects is not None and not objects:
        raise invalid("--object needs an object name", ["blender-axi audit --help"])
    if collection is not None and not collection.strip():
        raise invalid("--collection needs a collection name", ["blender-axi audit --help"])
    if (objects or collection) and checks and all(c in SCENE_IDS for c in checks):
        raise invalid("--object/--collection skip scene-level checks, and every check in --checks is scene-level",
                      ["Object-level checks: " + ", ".join(c for c in CHECK_IDS if c not in SCENE_IDS),
                       "blender-axi audit --help"])
    return checks, objects, collection, ignore, fail_on


def _items_text(items: list[str], full: bool) -> str:
    if full or len(items) <= MAX_ITEMS:
        return ", ".join(items)
    return ", ".join(items[:MAX_ITEMS]) + f", +{len(items) - MAX_ITEMS} more"


def _gate_fails(findings: list[dict], fail_on: str | None) -> bool:
    """True when a finding is at `fail_on` level or worse (LEVELS is ordered worst first)."""
    if fail_on is None:
        return False
    worse = LEVELS[: LEVELS.index(fail_on) + 1]
    return any(f["level"] in worse for f in findings)


def _status(findings: list[dict], gate: str | None) -> str:
    """`gate` is the flag text that made the exit code 1 (None when it stayed 0)."""
    if not findings:
        return "clean"
    n = {level: sum(1 for f in findings if f["level"] == level) for level in LEVELS}
    parts = ([f"{n['error']} error"] if n["error"] else []) + [f"{n['warn']} warn", f"{n['info']} info"]
    text = f"{count(len(findings), 'finding')} ({', '.join(parts)})"
    return f"{text}; {gate}: exit 1" if gate else text


def _replay(checks, objects, collection, ignore=None) -> str:
    parts = ["audit <file>"]
    if checks:
        parts.append(f"--checks {','.join(checks)}")
    if ignore:
        parts.append(f"--ignore {','.join(ignore)}")
    parts.extend(f"--object {quote_name(o)}" for o in objects or [])
    if collection:
        parts.append(f"--collection {quote_name(collection)}")
    return " ".join(parts)


def _hints(inv: Invocation, findings: list[dict], replay: str) -> list[str]:
    hints = Hints(inv)
    ip = "" if inv.globals.live else " --in-place"
    by_check = {f["check"]: f for f in findings}
    out: list[str] = []
    if "lighting" in by_check:
        out.append(f"Run `{hints.cmd('add light <file> --type SUN')}{ip}` to light the scene")
    if "camera" in by_check:
        out.append(f"Run `{hints.cmd(CAMERA_HINT)}{ip}` to add a camera")
    if "materials" in by_check:
        material = "material " + quote_name(by_check["materials"]["items"][0]) + " <file> --name Mat --color 0.8,0.8,0.8"
        out.append(f"Run `{hints.cmd(material)}{ip}` to give it a material")
    if not inv.globals.full and any(len(f["items"]) > MAX_ITEMS for f in findings):
        out.append(f"Run `{hints.cmd(replay + ' --full')}` to list every item")
    if "scale" in by_check or "nonmanifold" in by_check:
        out.append(CLEANUP_HINT)
    if not findings:
        out.append(f"Run `{hints.cmd('export <file> --out <path.glb>')}` to export the scene")
    return out[:3]


def run(inv: Invocation) -> Result:
    checks, objects, collection, ignore, fail_on = _validated(inv)
    blend, label = resolve_blend(inv)
    hints = Hints(inv)
    params = {"checks": checks, "objects": objects, "collection": collection}
    if ignore:
        params["ignore"] = ignore
    try:
        out = inv.transport.run("audit", params, blend=blend, timeout=inv.globals.timeout)
    except AxiError as err:
        raise translate_not_found(err, [f"Run `{hints.cmd('objects <file>')}` to list object and collection names"]) from None
    r = out.result
    findings = r["findings"]
    strict = bool(inv.flags.get("strict"))
    failed = _gate_fails(findings, "warn" if strict else fail_on)
    gate = ("--strict" if strict else f"--fail-on {fail_on}") if failed else None
    checked = f"{count(len(r['checks_run']), 'check')}, {count(r['objects_checked'], 'object')}"
    if ignore:
        checked += f" (ignored: {', '.join(ignore)})"
    if r["scene_checks_skipped"]:
        checked += " (scene-level checks skipped)"

    data: dict = {"file": label if label is not None else (posix_path(r.get("filepath") or "") or "(unsaved)"),
                  "checked": checked}
    if findings:
        data["findings"] = [{"check": f["check"], "level": f["level"], "count": f["count"],
                             "items": _items_text(f["items"], inv.globals.full)} for f in findings]
    data["status"] = _status(findings, gate)
    data["duration_ms"] = out.duration_ms
    with_warnings(data, [*r.get("warnings", []), *out.warnings])
    return Result(data=data, help=_hints(inv, findings, _replay(checks, objects, collection, ignore)),
                  exit_code=1 if failed else 0)


SPEC = CommandSpec(
    name="audit",
    summary="Check a .blend for asset problems: unapplied scale, missing materials, n-gons, non-manifold edges, "
            "unused data, hidden or render-disabled objects, missing files, empty collections, no camera or light",
    positionals=(Positional("file", False, "The .blend file (optional when the current directory has exactly one)"),),
    flags=(
        Flag("--checks", "list", choices=CHECK_IDS, help="Only these checks, comma-separated (default: all)"),
        Flag("--object", "list", split=False, help="Only this object, repeatable (scene-level checks are skipped)"),
        Flag("--collection", "str", help="Only objects in this collection (scene-level checks are skipped)"),
        Flag("--ignore", "list", choices=CHECK_IDS, help="Skip these checks, comma-separated (not with --checks)"),
        Flag("--fail-on", "str", choices=("error", "warn"),
             help="Exit 1 when a finding of this level or worse exists (the report is still printed)"),
        Flag("--strict", "bool", help="Same as --fail-on warn"),
    ),
    examples=("blender-axi audit scene.blend", "blender-axi audit scene.blend --checks scale,materials --strict",
              "blender-axi audit --live"),
    run=run,
)
