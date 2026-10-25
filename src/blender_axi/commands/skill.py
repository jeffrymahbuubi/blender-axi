"""`skill gen [--check]`: render skills/blender-axi/SKILL.md from the live command registry (plan U7).

The skill is the static half of the home view (description, command table, global flags, safety rules);
live state (Blender path, live reachability, .blend files) is never included. `--check` fails when the
committed file is stale, replacing `npm run skill:check` (AXI V11/V12).
"""

from __future__ import annotations

import importlib
from pathlib import Path

from ..errors import AxiError
from ..spec import GLOBAL_FLAGS, CommandSpec, Flag, Invocation, Result
from .home import DESCRIPTION

SKILL_NAME = "blender-axi"
SKILL_DESCRIPTION = (
    "Inspect, render, export and script Blender (.blend) files from the terminal with the blender-axi CLI. "
    "Use when asked what is in a .blend file, to list its objects, render a still or frames, "
    "export glb/fbx/obj/stl, or run a Blender Python script headlessly."
)
DEFAULT_REL = Path("skills") / SKILL_NAME / "SKILL.md"


def _load(target: str) -> CommandSpec:
    module, _, attr = target.partition(":")
    return getattr(importlib.import_module(module), attr)


def _default_path() -> Path:
    root = Path(__file__).resolve().parents[3]
    return (root if (root / "pyproject.toml").is_file() else Path.cwd()) / DEFAULT_REL


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def _flag_label(flag: Flag) -> str:
    if flag.kind == "bool":
        return flag.name
    word = {"str": "text", "int": "n", "float": "n", "path": "path", "list": "a,b"}.get(flag.kind, flag.kind)
    return f"{flag.name} <{word}>"


def _commands_table(specs: dict[str, CommandSpec]) -> list[str]:
    lines = ["| Command | What it does | Example |", "|---|---|---|"]
    for name, spec in specs.items():
        example = spec.examples[0] if spec.examples else f"blender-axi {name}"
        lines.append(f"| `{_cell(name)}` | {_cell(spec.summary)} | `{_cell(example)}` |")
    return lines


def _safety(names: set[str]) -> list[str]:
    lines = ["- Read-only commands never modify the `.blend`; Blender runs headless with `--factory-startup`."]
    if names & {"render", "export"}:
        lines.append("- Outputs are never overwritten without `--force` (`WRITE_REFUSED`, exit 1).")
    if "exec" in names:
        lines.append("- `exec` refuses to run without `--allow-exec` (`EXEC_NOT_ALLOWED`, exit 2); "
                     "it works on a copy unless `--in-place` is given.")
    lines.append("- Temp files stay under the project-local `.tmp/` (or `BLENDER_AXI_TMP`), never the system temp.")
    lines.append("- Errors are data on stdout with a `code` and `help`; exit 2 means a usage problem, 1 a runtime failure.")
    return lines


def render_skill(specs: dict[str, CommandSpec]) -> str:
    """The full SKILL.md text for `specs` (name -> CommandSpec). Deterministic; ends with one newline."""
    out = [
        "---",
        f"name: {SKILL_NAME}",
        f"description: {SKILL_DESCRIPTION}",
        "---",
        "",
        f"# {SKILL_NAME}",
        "",
        DESCRIPTION + ".",
        "",
        "Run `blender-axi` with no arguments for the live home view: Blender location, live-session "
        "reachability and the `.blend` files in the current directory. "
        "Output is TOON on stdout (use `--json` for JSON); every response ends with `help` next steps.",
        "",
        "Not on PATH yet? Run `python -m blender_axi ...` from the project's virtualenv. "
        "Once published, `uvx blender-axi ...` runs it without installing.",
        "",
        "## Commands",
        "",
        *_commands_table(specs),
        "",
        "Run `blender-axi <command> --help` for flags and examples.",
        "",
        "## Global flags",
        "",
    ]
    out += [f"- `{_flag_label(f)}`: {f.help}" for f in GLOBAL_FLAGS]
    out += ["", "## Safety", "", *_safety(set(specs)), ""]
    return "\n".join(out)


def _specs() -> dict[str, CommandSpec]:
    from .. import registry

    return {name: _load(target) for name, target in registry.COMMANDS.items()}


def _normalize(text: str) -> str:
    return text.replace("\r\n", "\n")


def run_gen(inv: Invocation) -> Result:
    target = Path(inv.flags["out"]) if inv.flags.get("out") else _default_path()
    specs = _specs()
    text = render_skill(specs)
    try:
        shown = target.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        shown = target.as_posix()

    if inv.flags.get("check"):
        current = _normalize(target.read_text(encoding="utf-8")) if target.is_file() else None
        if current != text:
            why = "is missing" if current is None else "is stale"
            raise AxiError(
                "VALIDATION_ERROR", f"{shown} {why}",
                help=["Run `blender-axi skill gen` to regenerate it"], exit_code=1,
            )
        return Result(data={"skill": shown, "status": "fresh", "commands": len(specs)})

    previous = _normalize(target.read_text(encoding="utf-8")) if target.is_file() else None
    if previous == text:
        status = "unchanged"
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        status = "written"
    return Result(
        data={"skill": shown, "status": status, "commands": len(specs)},
        help=["Run `blender-axi skill gen --check` to verify the committed skill is fresh"],
    )


GEN_SPEC = CommandSpec(
    name="skill gen",
    summary="Generate skills/blender-axi/SKILL.md from the command registry (--check verifies it is fresh)",
    positionals=(),
    flags=(
        Flag("--check", "bool", help="Do not write; exit 1 if the skill file is missing or stale"),
        Flag("--out", "path", help="Skill file path (default skills/blender-axi/SKILL.md in the checkout)"),
    ),
    examples=("blender-axi skill gen", "blender-axi skill gen --check"),
    run=run_gen,
)


def run_skill(inv: Invocation) -> Result:
    raise AxiError(
        "VALIDATION_ERROR", "`skill` needs a subcommand",
        help=["Run `blender-axi skill gen` to write the skill", "Run `blender-axi skill --help` for subcommands"],
        exit_code=2,
    )


SPEC = CommandSpec(
    name="skill",
    summary="Agent skill file: `skill gen` writes it, `skill gen --check` verifies it",
    positionals=(),
    flags=(),
    examples=("blender-axi skill gen", "blender-axi skill gen --check"),
    run=run_skill,
    subcommands={"gen": GEN_SPEC},
)
