"""The two contestants as `claude -p` argv recipes (plan section 3.2).

Both arms share every flag except the instrument: `--tools`, `--allowedTools` and the extras. The blender port for the
MCP arm is always a fresh OS-drawn port from `bench.server.AddonServer`; the GUI Blender port is never used (it is
the reserved port 9876 in `bench.server`, which re-draws it).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from . import REPO  # noqa: F401  (ensures sys.path is prepared)

MODEL = "claude-opus-5-5"
EFFORT = "medium"
MAX_TURNS = 40
MAX_BUDGET_USD = 3
KEY_VAR = "BLENDER_AXI_ANTHROPIC_KEY"
DEFAULT_PORT_PLACEHOLDER = 50000  # --dry-run only; real runs use the port of the AddonServer


class SetupError(RuntimeError):
    """A setup problem: the message is the one-line fix; run_ab exits 2."""


@dataclass(frozen=True)
class Arm:
    name: str
    tools: str
    allowed: tuple[str, ...]
    extra: tuple[str, ...]  # argv pieces for the instrument (the skill file or the MCP server config)


def common_flags(model: str = MODEL, effort: str = EFFORT, max_turns: int = MAX_TURNS,
                 max_budget_usd: float = MAX_BUDGET_USD, bare: bool = True) -> tuple[str, ...]:
    """Flags shared by both arms. `bare=False` is the K2 fallback: `--setting-sources ""` instead of `--bare`."""
    isolation = ("--bare",) if bare else ("--setting-sources", "")
    return ("-p", *isolation, "--output-format", "stream-json", "--verbose", "--strict-mcp-config",
            "--no-session-persistence", "--disable-slash-commands", "--max-turns", str(max_turns),
            "--max-budget-usd", _num(max_budget_usd), "--model", model, "--effort", effort)


def _num(value: float) -> str:
    return str(int(value)) if float(value) == int(value) else str(value)


COMMON = common_flags()


def axi_arm(skill_path) -> Arm:
    return Arm("axi", "Bash,Read,Write", ("Bash(blender-axi *)", "Read", "Write"),
               ("--append-system-prompt-file", str(skill_path)))


def mcp_arm(mcp_exe, port: int) -> Arm:
    config = {"mcpServers": {"blender": {"command": str(mcp_exe),
                                         "env": {"BLENDER_MCP_HOST": "127.0.0.1", "BLENDER_MCP_PORT": str(port)}}}}
    return Arm("mcp", "Read,Write", ("mcp__blender__*", "Read", "Write"), ("--mcp-config", json.dumps(config)))


def build_argv(claude: list[str], arm: Arm, prompt: str, common: tuple[str, ...] = COMMON) -> list[str]:
    """Full argv. The variadic options (`--tools`, `--allowedTools`, `--mcp-config`) come first, each value comma
    joined, and are closed by the next option (`-p` opens `common`): a variadic option placed right before the
    positional prompt would swallow the prompt as one more value. The prompt is the last item."""
    return [*claude, "--tools", arm.tools, "--allowedTools", ",".join(arm.allowed), *arm.extra, *common, prompt]


def fill_prompt(text: str) -> str:
    """Both arms get the byte-identical prompt; paths are relative to the run dir (the agent's cwd)."""
    return text.replace("{WORK}", "work.blend").replace("{OUT}", "out")


def read_key(environ=None) -> str:
    env = os.environ if environ is None else environ
    key = env.get(KEY_VAR)
    if not key:
        raise SetupError(f"{KEY_VAR} is not set: start the shell through the launcher that loads it (never from .env here)")
    return key


def mask_key(environ=None) -> str:
    """`<set,len N>` or `<unset>`; the value itself never leaves this function."""
    key = (os.environ if environ is None else environ).get(KEY_VAR)
    return f"<set,len {len(key)}>" if key else "<unset>"


def child_env(tmp, axi_bin_dir) -> dict:
    """The agent's environment: the key only here, a fresh Claude config dir, `blender-axi` first on PATH."""
    key = read_key()
    tmp = Path(tmp)
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_AUTH_TOKEN", KEY_VAR)}
    env["ANTHROPIC_API_KEY"] = key
    env["CLAUDE_CONFIG_DIR"] = str(tmp / "claude-config")
    env["PATH"] = str(axi_bin_dir) + os.pathsep + env.get("PATH", "")
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    scratch = tmp / "claude-tmp"
    scratch.mkdir(parents=True, exist_ok=True)
    env["TEMP"] = env["TMP"] = env["TMPDIR"] = str(scratch)
    (tmp / "claude-config").mkdir(parents=True, exist_ok=True)
    return env
