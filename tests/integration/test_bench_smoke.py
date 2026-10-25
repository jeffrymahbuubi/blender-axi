"""Smoke test for the token benchmark: one real run on the desk scene (needs Blender and the official blender-mcp).

Skips when the official server executable is missing. Set BLENDER_AXI_MCP_SERVER to point at one outside the repo
(for example from a git worktree, where the gitignored .tmp/mcpvenv does not exist).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.blender


def _mcp_exe() -> Path | None:
    override = os.environ.get("BLENDER_AXI_MCP_SERVER")
    sub = "Scripts/blender-mcp.exe" if sys.platform == "win32" else "bin/blender-mcp"
    for cand in ((Path(override),) if override else ()) + (REPO / ".tmp" / "mcpvenv" / sub,):
        if cand.is_file():
            return cand
    return None


def test_desk_run_writes_results_and_artifacts(blender_info):
    exe = _mcp_exe()
    if exe is None:
        pytest.skip("official blender-mcp executable not found (.tmp/mcpvenv or BLENDER_AXI_MCP_SERVER)")
    out = REPO / ".tmp" / "bench" / "smoke"
    shutil.rmtree(out, ignore_errors=True)
    env = {k: v for k, v in os.environ.items() if k not in ("BLENDER_AXI_ANTHROPIC_KEY", "ANTHROPIC_API_KEY")}
    argv = [sys.executable, str(REPO / "benchmarks" / "token_bench.py"), "--scenes", "desk", "--skip-render",
            "--results-dir", str(out), "--mcp-server", str(exe), "--blender", str(blender_info.path)]
    proc = subprocess.run(argv, cwd=str(REPO), env=env, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=900)
    assert proc.returncode == 0, proc.stdout[-1500:] + proc.stderr[-1500:]
    results = json.loads((out / "results.json").read_text(encoding="utf-8"))
    assert results["schema"] == 1 and results["unit"] == "chars" and results["token_model"] is None
    tasks = results["scenes"]["desk"]["tasks"]
    assert [t["id"] for t in tasks] == ["scene", "objects", "object_mug", "objects_json"]
    assert not any("error" in t for t in tasks)
    assert tasks[3]["mcp"] is None and tasks[3]["saving_pct"] is None
    assert tasks[1]["axi"]["chars"] < tasks[1]["mcp"]["chars"]
    assert (out / "desk" / "objects__axi.txt").is_file() and (out / "desk" / "objects__mcp.json").is_file()
    assert results["fixed"]["mcp_tools_list"]["tools"] > 0 and (out / "fixed" / "tools_list__mcp.json").is_file()
    ET.fromstring((out / "chart.svg").read_text(encoding="utf-8"))
    assert (out / "results.md").is_file()
