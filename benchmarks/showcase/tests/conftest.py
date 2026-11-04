"""Shared fixtures for the showcase tests (unit U4 owns this file).

Puts benchmarks/showcase (package `sc`), benchmarks (package `bench`) and src (package `blender_axi`) on sys.path.
All temp output lives under paths.tmp_root()/showcase/tests; nothing lands in the host's system temp.
"""
import shutil
import sys
import uuid
from pathlib import Path

import pytest

S = Path(__file__).resolve().parents[1]
REPO = S.parents[1]
for _p in (REPO / "src", REPO / "benchmarks", S):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def pytest_configure(config):
    config.addinivalue_line("markers", "blender: needs a real Blender installation")


@pytest.fixture
def work():
    """A fresh empty directory under tmp_root()/showcase/tests, removed afterwards."""
    from blender_axi import paths

    root = paths.tmp_root() / "showcase" / "tests" / uuid.uuid4().hex[:10]
    root.mkdir(parents=True)
    yield root
    shutil.rmtree(root, ignore_errors=True)
