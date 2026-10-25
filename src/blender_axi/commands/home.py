"""Home view: what `blender-axi` prints with no arguments (plan U7, AXI sections 8 and 10).

Cheap and total: it never starts a Blender session (only `blender --version`), never raises
when Blender is missing, and shows only state relevant to the current directory.
"""

from __future__ import annotations

import shutil
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import __version__, paths
from ..errors import AxiError
from ..spec import DEFAULT_LIVE_HOST, DEFAULT_LIVE_PORT, CommandSpec, Invocation, Result

DESCRIPTION = "Agent-ergonomic CLI for Blender: inspect, render, export and script .blend files headlessly"
MAX_BLENDS = 20
LIVE_PROBE_SECONDS = 0.3


def collapse_home(path: str | Path) -> str:
    """Absolute path with the user's home directory collapsed to `~` (forward slashes)."""
    text = Path(path).as_posix()
    home = Path.home().as_posix().rstrip("/")
    if home and (text == home or text.startswith(home + "/")):
        return "~" + text[len(home):]
    return text


def _bin_path() -> str:
    """The launcher path. On Windows the console script sets argv[0] without `.exe`, so try that too, then PATH."""
    arg0 = sys.argv[0] if sys.argv and sys.argv[0] else ""
    if arg0 and Path(arg0).name not in ("__main__.py", "-m", "-c"):
        for candidate in (Path(arg0), Path(arg0 + ".exe")):
            if candidate.is_file():
                return collapse_home(candidate.resolve())
        found = shutil.which(arg0)
        if found:
            return collapse_home(Path(found).resolve())
    return f"{collapse_home(sys.executable)} -m blender_axi"


def _blender_line(explicit: str | None) -> tuple[str, bool]:
    from ..locate import find_blender

    try:
        info = find_blender(explicit)
    except AxiError:
        return "not found", False
    return f"{info.version_string} ({collapse_home(info.path)})", True


def _connects(address: str, port: int) -> bool:
    try:
        with socket.create_connection((address, port), timeout=LIVE_PROBE_SECONDS):
            return True
    except OSError:
        return False


def _live_reachable(host: str, port: int) -> bool:
    """TCP connect only. Every resolved address (`localhost` is ::1 and 127.0.0.1) is tried in parallel:
    Windows takes the full timeout to refuse a loopback connect, so serial tries cost 0.3 s each."""
    try:
        addresses = sorted({info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
    except OSError:
        return False
    if not addresses:
        return False
    with ThreadPoolExecutor(max_workers=len(addresses)) as pool:
        return any(pool.map(lambda a: _connects(a, port), addresses))


def _blends(cwd: Path) -> tuple[list[dict], int]:
    rows: list[tuple[float, dict]] = []
    try:
        entries = [p for p in cwd.iterdir() if p.suffix.lower() == ".blend" and p.is_file()]
    except OSError:
        entries = []
    for p in entries:
        try:
            st = p.stat()
        except OSError:
            continue
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))
        rows.append((st.st_mtime, {"file": p.name, "size_kb": max(1, round(st.st_size / 1024)), "modified": stamp}))
    rows.sort(key=lambda r: (-r[0], r[1]["file"]))
    return [r[1] for r in rows[:MAX_BLENDS]], len(rows)


def _tmp_line() -> str:
    """The temp root, plus the count of `exec` copies kept there. Read-only: never creates the directory."""
    root = paths.tmp_root()
    try:
        copies = len(list((root / "exec").glob("*.blend")))
    except OSError:
        copies = 0
    line = collapse_home(root)
    return f"{line} ({copies} exec {'copy' if copies == 1 else 'copies'})" if copies > 0 else line


def _carry(inv: Invocation) -> str:
    """Global flags worth repeating in suggested commands."""
    g = inv.globals
    parts = []
    if g.live:
        parts.append("--live")
        if g.host != DEFAULT_LIVE_HOST:
            parts.append(f"--host {g.host}")
        if g.port != DEFAULT_LIVE_PORT:
            parts.append(f"--port {g.port}")
    if g.user_prefs:
        parts.append("--user-prefs")
    if g.blender:
        parts.append(f'--blender "{g.blender}"')
    return (" " + " ".join(parts)) if parts else ""


def run(inv: Invocation) -> Result:
    g = inv.globals
    with ThreadPoolExecutor(max_workers=1) as pool:  # probe overlaps with `blender --version`
        probe = pool.submit(_live_reachable, g.host, g.port)
        blender, found = _blender_line(g.blender)
        live_up = probe.result()
    cwd = Path.cwd()
    rows, total = _blends(cwd)

    data: dict = {
        "bin": _bin_path(),
        "version": __version__,
        "description": DESCRIPTION,
        "blender": blender,
        "live": f"{'reachable' if live_up else 'unreachable'} {g.host}:{g.port}",
        "tmp": _tmp_line(),
    }
    data["blends"] = rows if rows else f"0 .blend files in {collapse_home(cwd)}"

    carry = _carry(inv)
    help_lines = [
        f"Run `blender-axi{carry} scene <file>` for a scene summary",
        f"Run `blender-axi{carry} objects <file>` to list objects",
        f"Run `blender-axi{carry} render <file> --out <path.png>` to render a still",
    ]
    if total > len(rows):
        help_lines.append(f"Showing the newest {len(rows)} of {total} .blend files; pass a path to use another one")
    if live_up and not g.live:
        help_lines.append("A live Blender is reachable: add `--live` to inspect the open session")
    if not found:
        help_lines.append("Pass `--blender <path>` or set BLENDER_AXI_BLENDER to point at blender.exe")
    if total == 0:
        help_lines.append("Run `blender-axi new <file.blend>` to create one")
    if not paths.is_checkout():
        help_lines.append("Installed from git: upgrade with `uv tool upgrade blender-axi`")
    return Result(data=data, help=help_lines)


SPEC = CommandSpec(
    name="home",
    summary=DESCRIPTION,
    positionals=(),
    flags=(),
    examples=("blender-axi",),
    run=run,
)
