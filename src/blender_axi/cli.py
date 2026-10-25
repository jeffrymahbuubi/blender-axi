"""blender-axi entry point: global flags, dispatch, TOON/JSON emit (plan section 3.8).

`--version` is answered before anything but `blender_axi/__init__.py` is imported.
Every other path ends in exactly one stdout document; tracebacks go to stderr only with --debug.
"""

import sys


def _plain_stdout() -> None:
    """LF line endings and UTF-8 on every platform (Windows text mode would emit CRLF / cp1252)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", newline="\n")
        except (AttributeError, ValueError, OSError):  # replaced streams (tests, pipes without reconfigure)
            pass


def main(argv: list[str] | None = None) -> int:
    _plain_stdout()
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv in (["-v"], ["-V"], ["--version"]):
        from . import __version__

        sys.stdout.write(__version__ + "\n")
        return 0
    return _run(argv)


class _LazyHeadless:
    """Resolves Blender on first run, so commands that never touch Blender never pay for it."""

    name = "headless"

    def __init__(self, opts):
        self._opts = opts
        self._inner = None

    def run(self, op, params, **kwargs):
        if self._inner is None:
            from .locate import find_blender
            from .runner import HeadlessTransport

            self._inner = HeadlessTransport(find_blender(self._opts.blender), keep_tmp=self._opts.keep_tmp,
                                            user_prefs=self._opts.user_prefs)
        return self._inner.run(op, params, **kwargs)


def _split_command(argv: list[str]) -> tuple[str | None, list[str]]:
    """Return (command, args) where args keeps leading global flags plus everything after the command."""
    from .spec import GLOBAL_FLAGS

    value_flags = {f.name for f in GLOBAL_FLAGS if f.kind != "bool"}
    leading: list[str] = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if not tok.startswith("-") or tok == "-":
            return tok, leading + argv[i + 1:]
        leading.append(tok)
        if tok in value_flags and "=" not in tok and i + 1 < len(argv):
            leading.append(argv[i + 1])
            i += 1
        i += 1
    return None, leading


def _load(target: str):
    import importlib

    module, _, attr = target.partition(":")
    return getattr(importlib.import_module(module), attr)


def _top_help() -> dict:
    from .registry import COMMANDS
    from .spec import GLOBAL_FLAGS

    rows = [{"command": name, "summary": _load(target).summary} for name, target in COMMANDS.items()]
    return {
        "usage": "blender-axi [<command>] [<file.blend>] [flags]",
        "summary": "Agent-ergonomic CLI for Blender. No command shows the home view.",
        "commands": rows,
        "global_flags": [{"flag": f.name, "help": f.help} for f in GLOBAL_FLAGS],
        "help": ["Run `blender-axi <command> --help` for flags and examples"],
    }


def _emit(data: dict, as_json: bool) -> None:
    from . import toon

    text = toon.encode_json(data) if as_json else toon.encode(data)
    sys.stdout.write(text + "\n")


def _run(argv: list[str]) -> int:
    as_json = "--json" in argv
    debug = "--debug" in argv
    try:
        from . import errors

        try:
            return _dispatch(argv)
        except errors.AxiError as err:
            result = errors.to_result(err)
            data = dict(result.data)
            _emit(data, as_json)
            if debug and err.detail:
                sys.stderr.write(err.detail + "\n")
            return err.exit_code
    except Exception as exc:  # last resort: never leak a traceback to stdout
        if debug:
            import traceback

            traceback.print_exc(file=sys.stderr)
        _emit({"error": f"internal error: {type(exc).__name__}: {exc}", "code": "INTERNAL",
               "help": ["Rerun with --debug for the traceback"]}, as_json)
        return 1


def _dispatch(argv: list[str]) -> int:
    from . import flags, helptext
    from .registry import COMMANDS, HOME

    command, args = _split_command(argv)
    if command is None and "--help" in args:
        _emit(_top_help(), "--json" in args)
        return 0
    if command == "help":
        _emit(_top_help(), "--json" in args)
        return 0

    if command is None:
        spec = _load(HOME)
    elif command in COMMANDS:
        spec = _load(COMMANDS[command])
    else:
        from .errors import AxiError

        raise AxiError("VALIDATION_ERROR", f"unknown command `{command}`",
                       help=["Commands: " + ", ".join(COMMANDS), "Run `blender-axi --help`"], exit_code=2)

    if spec.subcommands and args and args[0] in spec.subcommands:
        spec = spec.subcommands[args[0]]
        args = args[1:]

    inv, wants_help = flags.parse(spec, args)
    if wants_help:
        _emit(helptext.render(spec), inv.globals.json)
        return 0

    if inv.globals.live and inv.globals.user_prefs:
        from .errors import AxiError

        raise AxiError("VALIDATION_ERROR",
                       "--user-prefs applies to headless runs; live mode uses the running Blender's preferences",
                       help=[f"blender-axi {inv.command} --live", f"blender-axi {inv.command} <file> --user-prefs"],
                       exit_code=2)
    if inv.globals.live:
        from .live import LiveTransport

        inv.transport = LiveTransport(inv.globals.host, inv.globals.port, timeout=inv.globals.timeout or 300)
    else:
        inv.transport = _LazyHeadless(inv.globals)
    result = spec.run(inv)
    data = dict(result.data)
    if result.help:
        data["help"] = list(result.help)
    _emit(data, inv.globals.json)
    return result.exit_code
