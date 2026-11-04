"""stream-json parsing, run.json and transcript.md (plan section 3.1).

`StreamState` is fed one stdout line at a time and is pure (no I/O), so it is unit-testable without a process.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

SCHEMA = 1
SUMMARY_MAX = 160
TRANSCRIPT_MAX = 58_000  # bytes; scrub.py caps a transcript at 60 KB
RESULT_LINES = 20
INPUT_LINES = 60
LINE_MAX = 300
USAGE_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")


# ---------------------------------------------------------------------------------------------------------------
# tool call helpers
# ---------------------------------------------------------------------------------------------------------------


def _first_line(text: str, limit: int = SUMMARY_MAX) -> str:
    for line in str(text).splitlines():
        if line.strip():
            return line.strip()[:limit]
    return ""


def tool_input_text(name: str, inp: dict) -> str:
    """The text a transcript shows for a tool call: the Bash command, the MCP code, else the input as JSON."""
    if not isinstance(inp, dict):
        return str(inp)
    if name == "Bash" and "command" in inp:
        return str(inp["command"])
    for key in ("code", "command"):
        if key in inp and name.startswith("mcp__"):
            return str(inp[key])
    return json.dumps(inp, indent=1, ensure_ascii=False)


def tool_summary(name: str, inp: dict) -> str:
    """Bash command / MCP code first line (<= 160 chars) / Read-Write path."""
    if not isinstance(inp, dict):
        return _first_line(inp)
    if name == "Bash":
        return _first_line(inp.get("command", ""))
    if name in ("Read", "Write", "Edit"):
        return _first_line(inp.get("file_path") or inp.get("path") or "")
    for key in ("code", "command", "path", "file_path", "query", "name"):
        if inp.get(key):
            return _first_line(inp[key])
    return _first_line(json.dumps(inp, ensure_ascii=False))


def result_text(content) -> str:
    """A tool_result `content` (string or list of blocks) as text; image blocks become a marker."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(str(block["text"]) if block.get("type") == "text" else f"[{block.get('type', 'block')}]")
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return "" if content is None else str(content)


def blender_starts_in(name: str, command: str) -> int:
    """blender-axi invocations in one Bash call (each starts one Blender for commands that need it). Agents chain
    commands (`cd <dir> && blender-axi ... ; blender-axi ...`), so every `&&`/`||`/`;`/`|` segment is checked."""
    if name != "Bash":
        return 0
    count = 0
    for seg in re.split(r"&&|\|\||;|\|", str(command)):
        words = seg.strip().split()
        if len(words) < 2 or words[0] not in ("blender-axi", "blender-axi.exe"):
            continue
        if not any(w in ("--help", "-h", "--version") for w in words[1:]):
            count += 1
    return count


def is_blender_start(name: str, command: str) -> bool:
    """A Bash call that starts blender-axi at least once."""
    return blender_starts_in(name, command) > 0


def usage_of(raw: dict | None) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    out = {k: int(raw.get(k) or 0) for k in USAGE_KEYS}
    details = raw.get("output_tokens_details")
    out["thinking_tokens"] = int((details or {}).get("thinking_tokens") or 0) if isinstance(details, dict) else 0
    return out


class StreamState:
    """Accumulates one run's stream-json events."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.init: dict = {}
        self.calls: list[dict] = []  # public records, §3.1 keys
        self.events: list[tuple[str, object]] = []  # ("text", str) | ("call", call-index)
        self.detail: dict[int, dict] = {}  # call index -> {"input": str, "head": [lines], "total": int, "command": str}
        self.first_turn_cache_creation: int | None = None
        self.result: dict | None = None
        self.bad_lines = 0
        self._pending: dict[str, int] = {}
        self._t0: dict[int, float] = {}
        self._assistants = 0

    def feed(self, line: str) -> list[dict]:
        """Process one stdout line; returns the call records whose tool_result arrived with this line."""
        line = line.strip()
        if not line:
            return []
        try:
            event = json.loads(line)
        except ValueError:
            self.bad_lines += 1
            return []
        if not isinstance(event, dict):
            self.bad_lines += 1
            return []
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            self.init = event
        elif kind == "assistant":
            self._assistant(event)
        elif kind == "user":
            return self._user(event)
        elif kind == "result":
            self.result = event
        return []

    def _assistant(self, event: dict) -> None:
        message = event.get("message") or {}
        self._assistants += 1
        if self._assistants == 1 and isinstance(message.get("usage"), dict):
            self.first_turn_cache_creation = int(message["usage"].get("cache_creation_input_tokens") or 0)
        content = message.get("content")
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and str(block.get("text", "")).strip():
                self.events.append(("text", str(block["text"])))
            elif block.get("type") == "tool_use":
                name, inp = str(block.get("name", "")), block.get("input") or {}
                n = len(self.calls) + 1
                self.calls.append({"n": n, "tool": name, "summary": tool_summary(name, inp), "result_chars": 0,
                                   "is_error": False, "ms": 0})
                self.detail[n - 1] = {"input": tool_input_text(name, inp), "head": [], "total": 0,
                                      "command": str(inp.get("command", "")) if isinstance(inp, dict) else ""}
                self._pending[str(block.get("id", n))] = n - 1
                self._t0[n - 1] = self.clock()
                self.events.append(("call", n - 1))

    def _user(self, event: dict) -> list[dict]:
        content = (event.get("message") or {}).get("content")
        done = []
        if not isinstance(content, list):
            return done
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            idx = self._pending.pop(str(block.get("tool_use_id", "")), None)
            if idx is None:
                continue
            text = result_text(block.get("content"))
            lines = text.splitlines()
            call = self.calls[idx]
            call["result_chars"] = len(text)
            call["is_error"] = bool(block.get("is_error"))
            call["ms"] = round((self.clock() - self._t0.get(idx, self.clock())) * 1000)
            self.detail[idx]["head"] = lines[:RESULT_LINES]
            self.detail[idx]["total"] = len(lines)
            done.append(call)
        return done

    @property
    def blender_starts_axi(self) -> int:
        return sum(blender_starts_in(c["tool"], self.detail[i]["command"]) for i, c in enumerate(self.calls))


def stop_reason(result: dict | None, max_turns: int, exit_code: int | None, timed_out: bool) -> str:
    """end_turn | max_turns | budget | error | timeout (plan U1)."""
    if timed_out:
        return "timeout"
    if not result:
        return "error"
    subtype = str(result.get("subtype") or "")
    if "budget" in subtype:
        return "budget"
    if "max_turns" in subtype or int(result.get("num_turns") or 0) >= max_turns:
        return "max_turns"
    if subtype != "success" or result.get("is_error"):
        return "error"
    return "end_turn"


# ---------------------------------------------------------------------------------------------------------------
# normalising (U4's sc.scrub.normalise when present, else the local fallback with the same signature)
# ---------------------------------------------------------------------------------------------------------------


def _forms(path) -> list[str]:
    fwd = str(path).replace("\\", "/").rstrip("/")
    return sorted({fwd, fwd.replace("/", "\\"), fwd.replace("/", "\\\\")}, key=len, reverse=True)


def _normalise(text: str, repo, tmp, home, user) -> str:
    """Minimal local fallback: `--port N` -> 50000, then tmp/repo/home -> placeholders (longest first), user -> <user>."""
    text = re.sub(r"(--port[ =])\d+", r"\g<1>50000", text)
    text = re.sub(r'("BLENDER_MCP_PORT"\s*:\s*")\d+', r"\g<1>50000", text)
    text = re.sub(r"(127\.0\.0\.1:)\d+", r"\g<1>50000", text)
    pairs = [(p, ph) for p, ph in ((tmp, "<tmp>"), (repo, "<repo>"), (home, "<home>")) if p]
    for path, placeholder in sorted(pairs, key=lambda pp: len(str(pp[0])), reverse=True):
        for form in _forms(path):
            text = re.sub(re.escape(form), lambda _m, ph=placeholder: ph, text, flags=re.IGNORECASE)
    if user and len(user) >= 3:
        text = re.sub(re.escape(user), "<user>", text, flags=re.IGNORECASE)
    return text


def get_normaliser():
    """`sc.scrub.normalise` once U4's module is merged, else the local fallback."""
    try:
        from . import scrub  # type: ignore[attr-defined]
        return scrub.normalise
    except Exception:
        return _normalise


# ---------------------------------------------------------------------------------------------------------------
# metadata probes
# ---------------------------------------------------------------------------------------------------------------


def claude_version(claude: list[str], timeout: float = 30) -> str:
    try:
        out = subprocess.run([*claude, "--version"], capture_output=True, text=True, timeout=timeout,
                             stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace")
        match = re.search(r"\d+\.\d+\.\d+", out.stdout)
        return match.group(0) if match else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def mcp_server_info(exe) -> dict:
    """Best-effort version and source commit of the official server from its venv metadata (as token_bench does)."""
    info = {"version": None, "commit": None}
    root = Path(exe).resolve().parent.parent
    dists = sorted(root.glob("Lib/site-packages/blender_mcp-*.dist-info"))
    dists += sorted(root.glob("lib/python*/site-packages/blender_mcp-*.dist-info"))
    if not dists:
        return info
    info["version"] = dists[0].name[len("blender_mcp-"):-len(".dist-info")]
    try:
        url = json.loads((dists[0] / "direct_url.json").read_text(encoding="utf-8"))["url"]
        src = Path(url2pathname(urlparse(url).path))
        out = subprocess.run(["git", "-C", str(src), "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                             timeout=30)
        if out.returncode == 0 and out.stdout.strip():
            info["commit"] = out.stdout.strip()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        pass
    return info


# ---------------------------------------------------------------------------------------------------------------
# writers
# ---------------------------------------------------------------------------------------------------------------


def write_json(path: Path, data) -> None:
    Path(path).write_bytes((json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))


def write_run(run_dir: Path, run: dict) -> None:
    write_json(Path(run_dir) / "run.json", run)


def _fence(text: str) -> str:
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}\n{text}\n{ticks}"


def _clip(line: str) -> str:
    return line if len(line) <= LINE_MAX else line[:LINE_MAX] + " ..."


def _head(lines: list[str], limit: int, total: int) -> str:
    shown = [_clip(ln) for ln in lines[:limit]]
    if total > limit:
        shown.append(f"... ({total - limit} more lines)")
    return "\n".join(shown)


def render_transcript(state: StreamState, run: dict) -> str:
    """transcript.md text, not yet normalised (the caller applies the normaliser)."""
    cost = run.get("total_cost_usd") or 0
    check = run.get("check") or {}
    verdict = "passed" if check.get("passed") else "failed" if check else "not checked"
    head = [f"# {run['project']} · {run['arm']} · repeat {run['repeat']}", "",
            f"model {run['model']} · effort {run['effort']} · cost ${cost:.4f} (estimate) · turns {run['num_turns']} · "
            f"stop {run['stop']} · check {verdict}", ""]
    body: list[str] = []
    for kind, value in state.events:
        if kind == "text":
            body.append(f"**Assistant:**\n\n{value.strip()}\n")
            continue
        call, detail = state.calls[value], state.detail[value]
        input_lines = detail["input"].splitlines() or [""]
        parts = [f"## Step {call['n']} · {call['tool']} · {call['ms']} ms", "",
                 _fence(_head(input_lines, INPUT_LINES, len(input_lines)))]
        if detail["head"] or detail["total"]:
            parts += ["", _fence(_head(detail["head"], RESULT_LINES, detail["total"]))]
        if call["is_error"]:
            parts += ["", "(tool result flagged as an error)"]
        body.append("\n".join(parts) + "\n")
    text = "\n".join(head) + "\n" + "\n".join(body)
    if len(text.encode("utf-8")) > TRANSCRIPT_MAX:
        cut = text.encode("utf-8")[:TRANSCRIPT_MAX].decode("utf-8", errors="ignore")
        text = cut[: cut.rfind("\n## Step")] if "\n## Step" in cut else cut
        text += "\n\n... transcript truncated at 58 KB; the full stream is in the run's stream.jsonl\n"
    return text


def write_transcript(run_dir: Path, state: StreamState, run: dict, normalise, repo, tmp, home, user) -> Path:
    text = normalise(render_transcript(state, run), repo, tmp, home, user)
    path = Path(run_dir) / "transcript.md"
    path.write_bytes(text.replace("\r\n", "\n").encode("utf-8"))
    return path
