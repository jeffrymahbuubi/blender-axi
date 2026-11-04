"""Showcase harness: A/B runs of `claude -p` with the official Blender MCP vs blender-axi (plan 2026-11-04-showcase).

Importing this package puts the repo's `src/` and `benchmarks/` first on `sys.path` (neither is installed), exactly as
`benchmarks/token_bench.py` does, so `blender_axi.*` and `bench.*` resolve to the checkout being measured.
"""

import sys
from pathlib import Path

SHOWCASE = Path(__file__).resolve().parents[1]  # benchmarks/showcase
REPO = SHOWCASE.parents[1]

for _p in (str(REPO / "benchmarks"), str(REPO / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
