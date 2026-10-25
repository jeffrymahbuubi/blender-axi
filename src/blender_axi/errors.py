"""Structured errors (plan section 3.4). Raised anywhere, caught once in cli.main."""

from __future__ import annotations

from .spec import Result

# code -> exit code. 2 = usage/validation problems, 1 = everything else.
CODE_EXIT: dict[str, int] = {
    "VALIDATION_ERROR": 2,
    "EXEC_NOT_ALLOWED": 2,
    "NOT_FOUND": 1,
    "BLENDER_NOT_FOUND": 1,
    "BLENDER_FAILED": 1,
    "OP_FAILED": 1,
    "EXEC_FAILED": 1,
    "WRITE_REFUSED": 1,
    "TIMEOUT": 1,
    "LIVE_UNREACHABLE": 1,
    "LIVE_ERROR": 1,
    "INTERNAL": 1,
}


class AxiError(Exception):
    """A failure the CLI reports as data. `detail` (tracebacks, stderr tails) goes to stderr with --debug only.

    `exit_code=None` looks the code up in CODE_EXIT (unknown codes -> 1).
    """

    def __init__(
        self,
        code: str,
        message: str,
        help: list[str] | tuple[str, ...] = (),
        exit_code: int | None = None,
        detail: str | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.help = list(help)
        self.exit_code = CODE_EXIT.get(code, 1) if exit_code is None else exit_code
        self.detail = detail


def to_result(err: AxiError) -> Result:
    """data = {"error", "code", "help"}; `detail` is deliberately NOT in data (stdout stays clean)."""
    data: dict = {"error": err.message, "code": err.code}
    if err.help:
        data["help"] = list(err.help)
    return Result(data=data, help=list(err.help), exit_code=err.exit_code)
