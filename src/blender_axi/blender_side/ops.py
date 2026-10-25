"""Op dispatch inside Blender (plan section 3.7): `run(op, params)` imports `op_<op>` and calls its `run(params)`.

Op modules import `bpy` inside `run()` so unit tests can import them with a fake `bpy`.
"""

import importlib
import re

_OP_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


class OpError(Exception):
    """A structured op failure: `code` is an error code from the plan's table, `message` is user-facing."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def run(op: str, params: dict) -> dict:
    if not isinstance(op, str) or not _OP_NAME.match(op):
        raise OpError("OP_FAILED", f"invalid op name {op!r}")
    module_name = f"blender_axi.blender_side.op_{op}"
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            raise OpError("OP_FAILED", f"unknown op {op!r}") from exc
        raise
    result = module.run(params or {})
    if not isinstance(result, dict):
        raise OpError("OP_FAILED", f"op {op!r} returned {type(result).__name__}, expected a dict")
    return result
