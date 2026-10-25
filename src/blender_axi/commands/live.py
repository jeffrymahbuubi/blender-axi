"""`live`: is a Blender with the MCP add-on server reachable, and what is open in it (plan U8)."""

from __future__ import annotations

from ..spec import DEFAULT_LIVE_HOST, DEFAULT_LIVE_PORT, CommandSpec, Invocation, Result
from ._common import posix_path

DEFAULT_TIMEOUT = 300.0


def _suffix(inv: Invocation) -> str:
    g = inv.globals
    out = " --live"
    if g.host != DEFAULT_LIVE_HOST:
        out += f" --host {g.host}"
    if g.port != DEFAULT_LIVE_PORT:
        out += f" --port {g.port}"
    return out


def _transport(inv: Invocation):
    """The invocation's live transport when cli.main built one (`--live`), else one from --host/--port."""
    if getattr(inv.transport, "name", None) == "live":
        return inv.transport
    from ..live import LiveTransport

    g = inv.globals
    return LiveTransport(g.host, g.port, timeout=g.timeout or DEFAULT_TIMEOUT)


def run(inv: Invocation) -> Result:
    g = inv.globals
    out = _transport(inv).run("scene_summary", {}, blend=None, timeout=g.timeout)
    r = out.result
    data = {
        "live": f"reachable {g.host}:{g.port}",
        "blender": r.get("blender_version") or out.blender_version,
        "file": posix_path(r.get("filepath") or "") or "(unsaved)",
        "unsaved_changes": bool(r.get("is_dirty")),
        "objects": r.get("object_count", 0),
    }
    sfx = _suffix(inv)
    help_ = [f"Run `blender-axi scene{sfx}` for the scene overview",
             f"Run `blender-axi objects{sfx}` to list objects"]
    return Result(data=data, help=help_)


SPEC = CommandSpec(
    name="live",
    summary="Check a running Blender (MCP add-on server) and show the open file; use --live on other commands",
    positionals=(),
    flags=(),
    examples=("blender-axi live", "blender-axi live --port 9877", "blender-axi scene --live"),
    run=run,
)
