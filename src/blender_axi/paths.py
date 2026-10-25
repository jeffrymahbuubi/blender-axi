"""Every temp location blender-axi uses (plan section 3.5).

All temp output (run dirs, Blender's TEMP/TMP/TMPDIR, exec copies) lives under `tmp_root()`.
No other module may create temp directories with the stdlib temp module; this one never does either.
"""

import os
import secrets
import shutil
import time
from collections.abc import Mapping
from pathlib import Path


def _checkout_root() -> Path | None:
    """The source checkout (the dir holding pyproject.toml next to src/), or None when installed."""
    root = Path(__file__).resolve().parents[2]
    return root if (root / "pyproject.toml").is_file() else None


def is_checkout() -> bool:
    """True when running from a source checkout, False when installed (wheel or `uv tool`)."""
    return _checkout_root() is not None


def tmp_root() -> Path:
    """1) $BLENDER_AXI_TMP  2) <checkout>/.tmp  3) %LOCALAPPDATA%/blender-axi/tmp or ~/.cache/blender-axi/tmp."""
    env = os.environ.get("BLENDER_AXI_TMP")
    if env:
        return Path(env)
    checkout = _checkout_root()
    if checkout is not None:
        return checkout / ".tmp"
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "blender-axi" / "tmp"
    return Path.home() / ".cache" / "blender-axi" / "tmp"


def blender_tmp() -> Path:
    """Directory Blender gets as TEMP/TMP/TMPDIR (created)."""
    path = tmp_root() / "blender"
    path.mkdir(parents=True, exist_ok=True)
    return path


def blender_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Copy of `base` (default os.environ) with TMPDIR, TEMP and TMP pointing at `blender_tmp()`."""
    env = dict(os.environ if base is None else base)
    tmp = str(blender_tmp())
    env["TMPDIR"] = env["TEMP"] = env["TMP"] = tmp
    return env


def run_dir(prefix: str) -> Path:
    """Fresh `<tmp_root>/runs/<prefix>-<YYYYmmdd-HHMMSS>-<6 hex>` (created)."""
    name = f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
    path = tmp_root() / "runs" / name
    path.mkdir(parents=True, exist_ok=False)
    return path


def exec_copy_path(blend: Path, run_id: str) -> Path:
    """Where `exec` writes its modified copy: `<tmp_root>/exec/<stem>-<run_id>.blend` (parent created)."""
    path = tmp_root() / "exec" / f"{Path(blend).stem}-{run_id}.blend"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def cleanup(path: Path, keep: bool) -> None:
    """Delete a run dir or file unless `keep`; never raises."""
    if keep:
        return
    path = Path(path)
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink()
    except OSError:
        pass
