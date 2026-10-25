"""Find the Blender executable and read its version (plan section 3.6)."""

import glob
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .errors import AxiError

WINDOWS_GLOB = r"C:\Program Files\Blender Foundation\Blender *\blender.exe"
MACOS_PATH = "/Applications/Blender.app/Contents/MacOS/Blender"  # ASSUMED
LINUX_PATHS = ("/usr/bin/blender", "/snap/bin/blender")  # ASSUMED
_VERSION_RE = re.compile(r"^Blender (\d+)\.(\d+)\.(\d+)")
_DIR_VERSION_RE = re.compile(r"Blender (\d+(?:\.\d+)*)")


@dataclass(frozen=True)
class BlenderInfo:
    path: Path
    version: tuple[int, int, int]
    version_string: str  # e.g. "5.2.0 LTS"


def _not_found(message: str) -> AxiError:
    return AxiError(
        "BLENDER_NOT_FOUND",
        message,
        help=["Pass `--blender <path-to-blender>` or set BLENDER_AXI_BLENDER"],
    )


def _as_executable(value: str) -> Path | None:
    p = Path(value).expanduser()
    if p.is_dir():
        for name in ("blender.exe", "blender", "Blender"):
            if (p / name).is_file():
                return p / name
        return None
    return p if p.is_file() else None


def _windows_candidates() -> list[Path]:
    def key(p: str) -> tuple[int, ...]:
        m = _DIR_VERSION_RE.search(p)
        return tuple(int(x) for x in m.group(1).split(".")) if m else (0,)

    return [Path(p) for p in sorted(glob.glob(WINDOWS_GLOB), key=key, reverse=True)]


def _platform() -> str:
    return sys.platform


def _fallback_candidates() -> list[Path]:
    found = shutil.which("blender")
    out: list[Path] = [Path(found)] if found else []
    plat = _platform()
    if plat == "win32":
        out += _windows_candidates()
    elif plat == "darwin":
        out.append(Path(MACOS_PATH))
    else:
        out += [Path(p) for p in LINUX_PATHS]
    return out


def read_version(path: Path) -> BlenderInfo:
    """Run `blender --version` and parse its first line."""
    from . import paths

    try:
        proc = subprocess.run(
            [str(path), "--version"], capture_output=True, text=True, timeout=60,
            env=paths.blender_env(), cwd=str(paths.blender_tmp()),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise _not_found(f"cannot run `{path} --version`: {exc}") from exc
    lines = proc.stdout.splitlines()
    match = _VERSION_RE.match(lines[0]) if lines else None
    if proc.returncode != 0 or not match:
        raise _not_found(f"`{path}` is not a Blender executable (no 'Blender X.Y.Z' version line)")
    version = (int(match[1]), int(match[2]), int(match[3]))
    return BlenderInfo(Path(path), version, lines[0][len("Blender "):].strip())


def find_blender(explicit: str | None = None) -> BlenderInfo:
    """Order: --blender, BLENDER_AXI_BLENDER, BLENDER_PATH, PATH, platform install dirs.

    An explicit value or BLENDER_AXI_BLENDER that does not resolve is an error (no silent fallthrough);
    BLENDER_PATH (lab compat) and the platform defaults fall through when unusable.
    """
    if explicit:
        exe = _as_executable(explicit)
        if exe is None:
            raise _not_found(f"--blender path not found: {explicit}")
        return read_version(exe)
    axi = os.environ.get("BLENDER_AXI_BLENDER")
    if axi:
        exe = _as_executable(axi)
        if exe is None:
            raise _not_found(f"BLENDER_AXI_BLENDER path not found: {axi}")
        return read_version(exe)
    lab = os.environ.get("BLENDER_PATH")
    if lab:
        exe = _as_executable(lab)
        if exe is not None:
            return read_version(exe)
    for cand in _fallback_candidates():
        if cand.is_file():
            return read_version(cand)
    raise _not_found("Blender executable not found")
