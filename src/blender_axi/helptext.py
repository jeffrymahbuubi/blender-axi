"""Help rendering: a TOON-ready dict per command (plan section 3.3)."""

from __future__ import annotations

from .spec import GLOBAL_FLAGS, CommandSpec, Flag


def _placeholder(flag: Flag) -> str:
    if flag.kind == "bool":
        return flag.name
    if flag.choices:
        return f"{flag.name} <{'|'.join(flag.choices)}>"
    word = {"str": "text", "int": "n", "float": "n", "path": "path", "list": "a,b"}.get(flag.kind, flag.kind)
    return f"{flag.name} <{word}>"


def _default(flag: Flag) -> str:
    d = flag.default
    if d is None or d is False:
        return ""
    if d is True:
        return "true"
    if isinstance(d, (list, tuple)):
        return ",".join(str(x) for x in d)
    return str(d)


def _flag_row(flag: Flag) -> dict:
    text = flag.help + (" (required)" if flag.required else "")
    return {"flag": _placeholder(flag), "default": _default(flag), "help": text.strip()}


def render(spec: CommandSpec, prog: str = "blender-axi") -> dict:
    """usage, summary, [subcommands], positionals, flags, global_flags, examples."""
    own = [f for f in spec.flags if f.name not in {g.name for g in GLOBAL_FLAGS}]
    parts = [prog, spec.name]
    if spec.subcommands:
        parts.append("<subcommand>")
    for p in spec.positionals:
        parts.append(f"<{p.name}>" if p.required else f"[<{p.name}>]")
    parts.extend(_placeholder(f) for f in own if f.required)
    parts.append("[flags]")
    out: dict = {"usage": " ".join(parts), "summary": spec.summary}
    if spec.subcommands:
        out["subcommands"] = [{"name": n, "summary": s.summary} for n, s in spec.subcommands.items()]
    out["positionals"] = [{"name": p.name, "required": p.required, "help": p.help} for p in spec.positionals]
    out["flags"] = [_flag_row(f) for f in own]
    out["global_flags"] = [_flag_row(f) for f in GLOBAL_FLAGS]
    out["examples"] = list(spec.examples)
    return out
