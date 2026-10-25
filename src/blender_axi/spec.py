"""Command spec, invocation and result types shared by every command (plan section 3.2).

Pure data: no I/O, no imports from other blender_axi modules (so every layer can import it).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

DEFAULT_LIVE_HOST = "localhost"
DEFAULT_LIVE_PORT = 9876
TRUNCATE_CHARS = 1500
DEFAULT_LIMIT = 100

FlagKind = Literal["bool", "str", "int", "float", "path", "list"]


@dataclass(frozen=True)
class Flag:
    name: str  # "--type"
    kind: FlagKind  # list = comma-separated and/or repeatable
    default: Any = None
    required: bool = False
    help: str = ""
    choices: tuple[str, ...] | None = None
    renamed_from: tuple[str, ...] = ()  # old names; gives a targeted "use --x instead" hint


@dataclass(frozen=True)
class Positional:
    name: str
    required: bool = True
    help: str = ""


@dataclass
class GlobalOpts:
    json: bool = False
    full: bool = False
    debug: bool = False
    keep_tmp: bool = False
    blender: str | None = None
    timeout: float | None = None
    live: bool = False
    host: str = DEFAULT_LIVE_HOST
    port: int = DEFAULT_LIVE_PORT


@dataclass
class Invocation:
    command: str  # "objects"
    positionals: dict[str, str] = field(default_factory=dict)
    flags: dict[str, Any] = field(default_factory=dict)  # defaults applied; keys without dashes, "-" -> "_"
    globals: GlobalOpts = field(default_factory=GlobalOpts)
    transport: Any = None  # set by cli.main after parsing (plan K2)


@dataclass
class Result:
    data: dict[str, Any]  # ordered; emitted as the TOON root object
    help: list[str] = field(default_factory=list)  # appended as data["help"] when non-empty
    exit_code: int = 0


@dataclass(frozen=True)
class CommandSpec:
    name: str
    summary: str
    positionals: tuple[Positional, ...]
    flags: tuple[Flag, ...]
    examples: tuple[str, ...]
    run: Callable[[Invocation], Result]
    subcommands: dict[str, "CommandSpec"] | None = None  # e.g. skill -> gen


GLOBAL_FLAGS: tuple[Flag, ...] = (
    Flag("--help", "bool", help="Show help for the command"),
    Flag("--json", "bool", help="Emit JSON instead of TOON"),
    Flag("--full", "bool", help="Do not truncate long text fields"),
    Flag("--debug", "bool", help="Print tracebacks and Blender stderr to stderr"),
    Flag("--keep-tmp", "bool", help="Keep per-run temp directories under .tmp/"),
    Flag("--blender", "path", help="Path to the Blender executable (or set BLENDER_AXI_BLENDER)"),
    Flag("--timeout", "float", help="Timeout in seconds (default 120, render 600, live 300)"),
    Flag("--live", "bool", help="Talk to a running Blender instead of starting one"),
    Flag("--host", "str", default=DEFAULT_LIVE_HOST, help="Live-mode host"),
    Flag("--port", "int", default=DEFAULT_LIVE_PORT, help="Live-mode port"),
)
