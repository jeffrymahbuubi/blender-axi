"""`exec`: run user Python inside Blender (mirrors the lab contract: `result` dict + captured stdout).

The namespace starts as `{"bpy": bpy, "result": {}}`; the code assigns a dict to `result`.
Saving (copy / in place) is the bootstrap's job, not the op's.
"""

import contextlib
import io
import json
import traceback


def _line_of(exc: BaseException, filename: str) -> int | None:
    """Line number inside the user's code, from a SyntaxError or the innermost user traceback frame."""
    if isinstance(exc, SyntaxError) and exc.filename == filename:
        return exc.lineno
    line = None
    for frame in traceback.extract_tb(exc.__traceback__):
        if frame.filename == filename:
            line = frame.lineno
    return line


def run(params: dict) -> dict:
    import bpy

    from blender_axi.blender_side.ops import OpError

    code = params.get("code")
    if not isinstance(code, str) or not code.strip():
        raise OpError("OP_FAILED", "exec needs a non-empty `code` string")
    filename = "<exec>"
    namespace = {"__name__": "__blender_axi_exec__", "bpy": bpy, "result": {}}
    out = io.StringIO()
    try:
        compiled = compile(code, filename, "exec")
        with contextlib.redirect_stdout(out):
            exec(compiled, namespace)  # gated by --allow-exec at the CLI
    except BaseException as exc:  # user code may raise anything, including SystemExit
        if isinstance(exc, KeyboardInterrupt):
            raise
        line = _line_of(exc, filename)
        where = f" (line {line} of the exec code)" if line else ""
        raise OpError("EXEC_FAILED", f"{type(exc).__name__}: {exc}{where}") from exc

    result = namespace.get("result")
    if not isinstance(result, dict):
        raise OpError(
            "EXEC_FAILED",
            f"`result` must be a dict, got {type(result).__name__}; assign e.g. result = {{'n': 1}}",
        )
    try:
        safe = json.loads(json.dumps(result, default=repr))
    except (TypeError, ValueError) as exc:
        raise OpError("EXEC_FAILED", f"`result` is not JSON serialisable: {exc}") from exc
    return {"result": safe, "stdout": out.getvalue()}
