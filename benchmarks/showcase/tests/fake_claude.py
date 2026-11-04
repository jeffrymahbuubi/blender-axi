"""Stand-in for `claude -p` used by the harness tests: prints a canned stream-json sequence, never calls any API.

Knobs (environment): FAKE_CLAUDE_MODE = ok | max_turns | error | budget | hang;
FAKE_CLAUDE_COMMANDS = JSON list of Bash commands to "run" (default one `blender-axi objects work.blend`).
It records what it was started with in `fake_started.json` (cwd) and writes `out/x.txt`. The key value is never echoed,
only its length.
"""

import json
import os
import sys
import time
from pathlib import Path

args = sys.argv[1:]
if "--version" in args:
    print("2.1.317 (Claude Code)")
    sys.exit(0)

mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
mcp = "--mcp-config" in args
commands = json.loads(os.environ.get("FAKE_CLAUDE_COMMANDS", '["blender-axi objects work.blend"]'))
key = os.environ.get("ANTHROPIC_API_KEY", "")

Path("fake_started.json").write_text(json.dumps({
    "argv": args, "key_len": len(key), "config_dir": os.environ.get("CLAUDE_CONFIG_DIR"),
    "has_auth_token": "ANTHROPIC_AUTH_TOKEN" in os.environ, "has_axi_key": "BLENDER_AXI_ANTHROPIC_KEY" in os.environ,
    "path_head": os.environ.get("PATH", "").split(os.pathsep)[0], "temp": os.environ.get("TEMP"),
}), encoding="utf-8")


def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


emit({"type": "system", "subtype": "init", "tools": ["Read", "Write"], "mcp_servers": []})
if mode == "hang":
    time.sleep(120)
    sys.exit(0)

usage1 = {"input_tokens": 3, "cache_creation_input_tokens": 9450, "cache_read_input_tokens": 0, "output_tokens": 40}
for i, command in enumerate(commands, 1):
    name = "mcp__blender__get_objects_summary" if mcp else "Bash"
    tool_input = {} if mcp else {"command": command}
    emit({"type": "assistant", "message": {"content": [
        {"type": "text", "text": f"Step {i}: looking at the scene."},
        {"type": "tool_use", "id": f"toolu_{i}", "name": name, "input": tool_input}],
        "usage": usage1 if i == 1 else {"cache_creation_input_tokens": 12}}})
    time.sleep(0.05)
    emit({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": f"toolu_{i}", "content": "count: 13 of 13\nname: Mug\n",
         "is_error": mode == "error" and i == len(commands)}]}})

Path("out").mkdir(exist_ok=True)
Path("out/x.txt").write_text("done\n", encoding="utf-8")
emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "Done. 13 objects."}], "usage": {}}})

subtype, is_error, turns = "success", False, len(commands) + 1
if mode == "max_turns":
    subtype, is_error, turns = "error_max_turns", True, 40
elif mode == "error":
    subtype, is_error = "error_during_execution", True
elif mode == "budget":
    subtype, is_error = "error_max_budget_usd", True
emit({"type": "result", "subtype": subtype, "is_error": is_error, "duration_ms": 1234, "num_turns": turns,
      "result": "Done. 13 objects.", "total_cost_usd": 0.0123,
      "usage": {"input_tokens": 7, "cache_creation_input_tokens": 9450, "cache_read_input_tokens": 1000,
                "output_tokens": 120, "output_tokens_details": {"thinking_tokens": 30}},
      "permission_denials": [{"tool_name": "Bash"}] if mode == "error" else []})
