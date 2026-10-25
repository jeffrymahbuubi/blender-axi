"""cli output bytes: LF-only and UTF-8 on every platform (lead-owned, regression for Windows CRLF)."""

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _run(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(REPO / "src"))
    return subprocess.run([sys.executable, "-m", "blender_axi", *args], capture_output=True, env=env,
                          cwd=str(REPO), timeout=60)


def test_help_is_lf_only():
    proc = _run("--help")
    assert proc.returncode == 0
    assert b"\r" not in proc.stdout
    assert proc.stdout.count(b"\n") > 3


def test_error_is_lf_only_and_exit_2():
    proc = _run("objects", "--bogus")
    assert proc.returncode == 2
    assert b"\r" not in proc.stdout
    assert proc.stdout.startswith(b"error: ")
