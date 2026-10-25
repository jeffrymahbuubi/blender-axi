"""`batch`: run several modeling ops in order inside one request (the Blender side of `apply`).

params: steps [{op, params}, ...]. Every step goes through `ops.run`, so it behaves exactly like the single command.
A step that fails raises the same error with `step N (op): ` in front (N is 1-based); nothing else is caught, so in a
headless run the bootstrap writes nothing (all-or-nothing) and in a live session the earlier steps stay applied.
Result: {applied: n, results: [{op, result}, ...]}.
"""

from . import ops
from .ops import OpError

ALLOWED = ("add", "set", "delete", "material", "modifier", "import", "configure")


def run(params: dict) -> dict:
    steps = params.get("steps")
    if not isinstance(steps, list) or not steps:
        raise OpError("OP_FAILED", "batch needs a non-empty list of steps")
    for i, step in enumerate(steps, 1):  # refuse before step 1 runs
        op = step.get("op") if isinstance(step, dict) else None
        if op not in ALLOWED:
            raise OpError("OP_FAILED", f"step {i}: op {op!r} is not allowed in a batch (allowed: {', '.join(ALLOWED)})")
    results = []
    for i, step in enumerate(steps, 1):
        op = step["op"]
        try:
            result = ops.run(op, step.get("params") or {})
        except OpError as exc:
            raise OpError(exc.code, f"step {i} ({op}): {exc.message}") from exc
        results.append({"op": op, "result": result})
    return {"applied": len(results), "results": results}
