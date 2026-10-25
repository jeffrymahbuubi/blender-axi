"""Transport protocol and the headless transport (plan section 3.7).

`HeadlessTransport` starts `blender --background --factory-startup`, hands it a request file and
reads a response file; see `blender_side/bootstrap.py` for the other end.
"""

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from . import paths
from .errors import AxiError
from .locate import BlenderInfo

PROTOCOL = 1
DEFAULT_TIMEOUT = 120.0
RENDER_TIMEOUT = 600.0
BOOTSTRAP = Path(__file__).resolve().parent / "blender_side" / "bootstrap.py"
STDERR_TAIL = 2000


@dataclass(frozen=True)
class SaveSpec:
    """What to do with the loaded file after the op: nothing, a copy at `path`, or in place."""

    mode: Literal["none", "copy", "in_place"] = "none"
    path: str | None = None


@dataclass
class RunOutcome:
    result: dict
    modified: bool
    saved: str | None
    blender_version: str
    duration_ms: int
    stdout: str
    warnings: list[str] = field(default_factory=list)


class Transport(Protocol):
    name: str

    def run(
        self, op: str, params: dict, *, blend: Path | None, save: SaveSpec = SaveSpec("none"),
        timeout: float | None = None,
    ) -> RunOutcome: ...


class HeadlessTransport:
    name = "headless"

    def __init__(self, blender: BlenderInfo, keep_tmp: bool = False):
        self.blender = blender
        self.keep_tmp = keep_tmp

    def build_argv(self, blend: Path | None, request_path: Path) -> list[str]:
        argv = [str(self.blender.path), "--background", "--factory-startup"]
        if blend is not None:
            argv.append(str(blend))
        argv += ["--python-exit-code", "3", "--python", str(BOOTSTRAP), "--", "--request", str(request_path)]
        return argv

    def run(
        self, op: str, params: dict, *, blend: Path | None = None, save: SaveSpec = SaveSpec("none"),
        timeout: float | None = None,
    ) -> RunOutcome:
        if blend is not None and not Path(blend).is_file():
            raise AxiError("NOT_FOUND", f"file not found: {blend}",
                           help=["Check the path; `blender-axi` lists .blend files in the current directory"])
        if blend is not None:
            blend = Path(os.path.abspath(blend))  # Blender runs in the run dir, so a relative path would miss
        if timeout is None:
            timeout = RENDER_TIMEOUT if op == "render" else DEFAULT_TIMEOUT
        rd = paths.run_dir(op)
        try:
            return self._run_in(rd, op, params, blend, save, timeout)
        finally:
            paths.cleanup(rd, self.keep_tmp)

    def _run_in(self, rd: Path, op: str, params: dict, blend: Path | None, save: SaveSpec,
                timeout: float) -> RunOutcome:
        request_path = rd / "request.json"
        response_path = rd / "response.json"
        request = {
            "protocol": PROTOCOL, "op": op, "params": params, "response_path": str(response_path),
            "save": {"mode": save.mode, "path": save.path},
        }
        request_path.write_text(json.dumps(request), encoding="utf-8")
        try:
            proc = subprocess.run(
                self.build_argv(blend, request_path), env=paths.blender_env(), cwd=str(rd),
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise AxiError("TIMEOUT", f"`{op}` timed out after {timeout:g}s",
                           help=["Raise the limit with `--timeout <seconds>`"]) from exc
        except OSError as exc:
            raise AxiError("BLENDER_FAILED", f"could not start Blender: {exc}",
                           help=["Check `--blender <path>`"], detail=str(exc)) from exc

        stderr_tail = (proc.stderr or "")[-STDERR_TAIL:]
        if proc.returncode != 0 or not response_path.is_file():
            raise AxiError(
                "BLENDER_FAILED",
                f"Blender exited with code {proc.returncode} without a response"
                if proc.returncode != 0 else "Blender finished without writing a response",
                help=["Rerun with `--debug` to see the end of Blender's stderr"], detail=stderr_tail or None,
            )
        try:
            response = json.loads(response_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise AxiError("BLENDER_FAILED", "Blender wrote an unreadable response",
                           help=["Rerun with `--debug` to see the end of Blender's stderr"],
                           detail=stderr_tail or str(exc)) from exc

        if not response.get("ok"):
            err = response.get("error") or {}
            raise AxiError(
                err.get("code", "OP_FAILED"), err.get("message", f"`{op}` failed inside Blender"),
                help=["Rerun with `--debug` for the traceback"], detail=err.get("traceback"),
            )
        warnings = list(response.get("warnings") or [])
        if self.blender.version[0] != 5:
            v = self.blender.version
            warnings.append(f"Blender {v[0]}.{v[1]} untested; 5.2 LTS verified")
        return RunOutcome(
            result=response.get("result") or {}, modified=bool(response.get("modified")),
            saved=response.get("saved"), blender_version=str(response.get("blender_version", "")),
            duration_ms=int(response.get("duration_ms", 0)), stdout=str(response.get("stdout", "")),
            warnings=warnings,
        )
