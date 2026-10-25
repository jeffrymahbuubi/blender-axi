"""Raw-output artifacts for the benchmark (plan section 4.3, decision K7).

Every measured output is stored next to the report so the numbers can be audited. Files are capped at `RAW_LIMIT`
characters; the JSON record keeps the full `chars` and `sha256` of the original text, so truncation never hides size.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

RAW_LIMIT = 20_000
PER_FILE_BUDGET = 65_536
TOTAL_BUDGET = 600_000


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def truncate(text: str, limit: int = RAW_LIMIT) -> tuple[str, bool]:
    """Return (stored text, truncated?). A cut text is the first `limit` chars plus a one-line trailer."""
    if len(text) <= limit:
        return text, False
    trailer = f"\n…[truncated: {len(text)} chars total, sha256 {sha256_text(text)[:12]}]"
    return text[:limit] + trailer, True


def write_raw(results_dir: Path, rel: str, text: str) -> dict:
    """Write `text` (truncated) under `results_dir/rel` as UTF-8 with no newline translation; return its record."""
    root = Path(results_dir).resolve()
    target = (root / rel).resolve()
    if root != target and root not in target.parents:
        raise ValueError(f"raw path escapes the results directory: {rel}")
    stored, truncated = truncate(text)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(stored.encode("utf-8"))
    return {"raw": Path(rel).as_posix(), "chars": len(text), "truncated": truncated, "sha256": sha256_text(text)}


def check_budget(results_dir: Path, per_file: int = PER_FILE_BUDGET, total: int = TOTAL_BUDGET) -> list[str]:
    """Human-readable list of size offenders under `results_dir` (empty when within budget)."""
    root = Path(results_dir)
    if not root.is_dir():
        return []
    offenders: list[str] = []
    running = 0
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        size = path.stat().st_size
        running += size
        if size > per_file:
            offenders.append(f"{path.relative_to(root).as_posix()}: {size} bytes exceeds {per_file} per file")
    if running > total:
        offenders.append(f"total: {running} bytes exceeds {total}")
    return offenders
