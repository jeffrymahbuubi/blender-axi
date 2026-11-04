#!/usr/bin/env python3
"""Leak gate for the showcase artifacts (plan K10): `python scrub.py --check <dir> [--fix]`.

Mirrors the checks of scripts/audit_release.py (which is not public, so the regexes are copied, not imported):
PNG text chunks, absolute user paths, denied terms, secrets, emails; plus GIF comment blocks and the showcase
size caps. Exit 1 when offences remain.

The deny terms (user name, machine folder, ids) are NOT in this file because benchmarks/ ships publicly. They come from
  1. env SHOWCASE_DENY (comma-separated),
  2. <tmp_root>/showcase/deny.txt (one term per line, `#` comments; .tmp/ is gitignored),
  3. runtime values: the current user name and home directory.
"""
from __future__ import annotations

import argparse
import getpass
import os
import re
import struct
import sys
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

ABS_PATH = re.compile(r"(?:[A-Za-z]:[\\/]+(?:Users|home|Documents and Settings)[\\/]+|/home/|/Users/)"  # scan:allow (the detection pattern itself)
                      r"([A-Za-z0-9._-]+)")
PLACEHOLDERS = {"someone", "user", "username", "you", "me", "example", "name", "runner", "public",
                "shared", "default", "x", "foo"}
SECRETS = [
    ("anthropic key", re.compile(r"sk-ant-[A-Za-z0-9_-]{16,}")),
    ("openai key", re.compile(r"sk-(?:proj-)?[A-Za-z0-9]{32,}")),
    ("github token", re.compile(r"(?:ghp|gho|ghs|ghu|ghr)_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}")),
    ("aws key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("slack token", re.compile(r"xox[abprs]-[A-Za-z0-9-]{10,}")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
SAFE_EMAIL = re.compile(r"(noreply|no-reply|example\.(com|org|net)|users\.noreply\.github\.com|localhost)$", re.I)
PNG_SIG = b"\x89PNG\r\n\x1a\n"
TEXT_CHUNKS = (b"tEXt", b"zTXt", b"iTXt")

GIF_CAP = 2_500_000
PNG_CAP = 400_000
TRANSCRIPT_CAP = 60_000
TOTAL_CAP = 20_000_000


# --------------------------------------------------------------------------- deny terms

def _tmp_root() -> Path:
    env = os.environ.get("BLENDER_AXI_TMP")
    return Path(env) if env else REPO / ".tmp"


def deny_file() -> Path:
    return _tmp_root() / "showcase" / "deny.txt"


def _runtime_user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 - no user database in some containers
        return ""


def deny_terms(extra=(), file: Path | None = None) -> list[str]:
    """Env + file + runtime terms + `extra`, de-duplicated case-insensitively."""
    raw: list[str] = [t for t in os.environ.get("SHOWCASE_DENY", "").split(",")]
    f = file or deny_file()
    if f.is_file():
        raw += [ln for ln in f.read_text(encoding="utf-8", errors="replace").splitlines()
                if ln.strip() and not ln.lstrip().startswith("#")]
    raw += list(extra)
    user = _runtime_user()
    if len(user) >= 3:
        raw.append(user)
    home = Path.home()
    if len(home.name) >= 3:
        raw.append(home.name)
    raw += [str(home), str(home).replace("\\", "/")]
    seen: set[str] = set()
    out: list[str] = []
    for t in raw:
        t = t.strip()
        if len(t) >= 2 and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def _deny_regexes(terms: list[str]) -> list[re.Pattern]:
    return [re.compile(re.escape(t), re.I) for t in terms]


# --------------------------------------------------------------------------- normalise

def _variants(p) -> list[str]:
    s = str(p).rstrip("\\/")
    if not s:
        return []
    back = s.replace("/", "\\")
    fwd = s.replace("\\", "/")
    out = [back, back.replace("\\", "\\\\"), fwd]
    if re.match(r"^[A-Za-z]:/", fwd):  # Git Bash / MSYS form of a drive path (`pwd`, `which` output): D:/x -> /d/x
        out.append("/" + fwd[0].lower() + fwd[2:])
    return list(dict.fromkeys(out))


def _local_paths(text: str, repo) -> str:
    """Any leftover drive path under the repo's top folder -> <local-path>, whatever its escaping (1-4 backslashes or
    `/`) and even when a line clip cut it mid-name, e.g. `D:\\\\top\\\\Proj ...` inside JSON-in-JSON tool output."""
    parts = [x for x in re.split(r"[\\/]+", str(repo or "")) if x]
    if len(parts) < 2 or not re.fullmatch(r"[A-Za-z]:", parts[0]) or len(parts[1]) < 3:
        return text
    sep = r"(?:\\{1,4}|/)"
    pat = re.escape(parts[0]) + sep + re.escape(parts[1]) + r"(?:" + sep + r"[^\s\"'\\/<>|`]*)*"
    return re.sub(pat, "<local-path>", text, flags=re.I)


def _slug(p) -> str:
    """Claude Code's per-project folder name for a path (oversized tool results are saved there): D:/a/b.c -> D--a-b-c."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(p).rstrip("\\/").replace("\\", "/"))


def normalise(text: str, repo, tmp, home, user, run=None) -> str:
    """Make output machine independent: <repo>, <run>, <tmp>, <home>, <user>, `--port N` -> 50000.

    Longest path first (the run dir lies in tmp, tmp in the repo). Slash style and JSON-escaped backslashes are covered.
    """
    pairs: list[tuple[str, str]] = []
    for p, token in ((run, "<run>"), (tmp, "<tmp>"), (repo, "<repo>"), (home, "<home>")):
        if p:
            pairs += [(v, token) for v in _variants(p)]
    pairs.sort(key=lambda x: len(x[0]), reverse=True)
    for variant, token in pairs:
        text = re.sub(re.escape(variant) + r"(?![A-Za-z0-9_-])", lambda _m, t=token: t, text, flags=re.I)
    slugs = [(_slug(p), token) for p, token in ((run, "<run>"), (tmp, "<tmp>"), (repo, "<repo>"), (home, "<home>")) if p]
    for slug, token in sorted(slugs, key=lambda x: len(x[0]), reverse=True):  # no guard: a slug is followed by "-"
        if len(slug) >= 8:
            text = re.sub(re.escape(slug), lambda _m, t=token: t, text, flags=re.I)
    text = _local_paths(text, repo)
    if user and len(user) >= 3:
        text = re.sub(r"(?<![A-Za-z0-9_-])" + re.escape(user) + r"(?![A-Za-z0-9_-])", "<user>", text, flags=re.I)
    text = re.sub(r"(--port[ =])\d+", r"\g<1>50000", text)

    def _leftover(m: re.Match) -> str:  # any other user's home prefix
        name = m.group(1)
        return m.group(0) if name.lower() in PLACEHOLDERS or name.startswith("<") else "<home>"
    return ABS_PATH.sub(_leftover, text)


# --------------------------------------------------------------------------- PNG

def _png_chunks(raw: bytes):
    """Yield (kind, start, end) for each chunk; stops on a truncated one."""
    pos = 8
    while pos + 12 <= len(raw):
        length, kind = struct.unpack(">I4s", raw[pos:pos + 8])
        end = pos + 12 + length
        if end > len(raw):
            return
        yield kind, pos, end
        if kind == b"IEND":
            return
        pos = end


def png_texts(blob: bytes) -> list[str]:
    out = []
    for kind, s, e in _png_chunks(blob):
        if kind not in TEXT_CHUNKS:
            continue
        body = blob[s + 8:e - 4]
        if kind == b"tEXt":
            out.append(body.replace(b"\0", b": ").decode("latin-1"))
        elif kind == b"zTXt":
            key, _, rest = body.partition(b"\0")
            try:
                out.append(key.decode("latin-1") + ": " + zlib.decompress(rest[1:]).decode("latin-1"))
            except zlib.error:
                out.append(key.decode("latin-1") + ": <undecodable>")
        else:
            out.append(body.replace(b"\0", b" ").decode("utf-8", errors="replace"))
    return out


def scrub_png(path) -> int:
    """Strip tEXt/zTXt/iTXt chunks in place; returns how many were removed."""
    path = Path(path)
    raw = path.read_bytes()
    if raw[:8] != PNG_SIG:
        return 0
    drop = [(s, e) for kind, s, e in _png_chunks(raw) if kind in TEXT_CHUNKS]
    if not drop:
        return 0
    out, last = [], 0
    for s, e in drop:
        out.append(raw[last:s])
        last = e
    out.append(raw[last:])
    path.write_bytes(b"".join(out))
    return len(drop)


# --------------------------------------------------------------------------- GIF

def _skip_sub_blocks(blob: bytes, pos: int) -> int:
    while pos < len(blob):
        n = blob[pos]
        pos += 1 + n
        if n == 0:
            break
    return pos


def _gif_blocks(blob: bytes):
    """Yield (kind, start, end, label) for extension and image blocks of a GIF."""
    if blob[:6] not in (b"GIF87a", b"GIF89a") or len(blob) < 13:
        return
    flags, pos = blob[10], 13
    if flags & 0x80:
        pos += 3 * (2 << (flags & 7))
    while pos < len(blob):
        b = blob[pos]
        if b == 0x21 and pos + 1 < len(blob):
            start, label = pos, blob[pos + 1]
            pos = _skip_sub_blocks(blob, pos + 2)
            yield "ext", start, pos, label
        elif b == 0x2C and pos + 10 <= len(blob):
            start, f = pos, blob[pos + 9]
            pos += 10
            if f & 0x80:
                pos += 3 * (2 << (f & 7))
            pos = _skip_sub_blocks(blob, pos + 1)
            yield "image", start, pos, None
        else:
            return


def gif_comments(blob: bytes) -> list[str]:
    out = []
    for kind, s, e, label in _gif_blocks(blob):
        if kind == "ext" and label == 0xFE:
            out.append(blob[s + 2:e].decode("latin-1", errors="replace"))
    return out


def scrub_gif(path) -> int:
    """Remove comment extension blocks (0x21 0xFE) in place; returns how many were removed."""
    path = Path(path)
    raw = path.read_bytes()
    cuts = [(s, e) for kind, s, e, label in _gif_blocks(raw) if kind == "ext" and label == 0xFE]
    if not cuts:
        return 0
    out, last = [], 0
    for s, e in cuts:
        out.append(raw[last:s])
        last = e
    out.append(raw[last:])
    path.write_bytes(b"".join(out))
    return len(cuts)


# --------------------------------------------------------------------------- check / fix

def _scan_text(rel: str, text: str, deny: list[re.Pattern], out: list[str]) -> None:
    for n, line in enumerate(text.splitlines(), 1):
        if "scan:allow" in line:
            continue
        where = f"{rel}:{n}"
        for m in ABS_PATH.finditer(line):
            if m.group(1).lower() not in PLACEHOLDERS and not m.group(1).startswith("<"):
                out.append(f"{where}: local-path: {m.group(0)}")
        for label, rx in SECRETS:
            if rx.search(line):
                out.append(f"{where}: secret: {label}")
        for i, rx in enumerate(deny, 1):
            if rx.search(line):
                out.append(f"{where}: denied-term: deny term #{i}")
        for m in EMAIL.finditer(line):
            addr = m.group(0)
            if not SAFE_EMAIL.search(addr) and not addr.lower().endswith(".png"):
                out.append(f"{where}: email: {addr}")


def _files(root: Path):
    return sorted(p for p in root.rglob("*") if p.is_file())


def check(results_dir, deny=()) -> list[str]:
    """Offences under results_dir (empty list = clean). `deny` adds terms to env/file/runtime ones."""
    root = Path(results_dir)
    rx = _deny_regexes(deny_terms(deny))
    out: list[str] = []
    total = 0
    for f in _files(root):
        rel = f.relative_to(root).as_posix()
        blob = f.read_bytes()
        total += len(blob)
        low = f.name.lower()
        if low.endswith(".gif") and len(blob) > GIF_CAP:
            out.append(f"{rel}: size: {len(blob)} bytes > {GIF_CAP} (gif cap)")
        if low.endswith(".png") and len(blob) > PNG_CAP:
            out.append(f"{rel}: size: {len(blob)} bytes > {PNG_CAP} (png cap)")
        if low.startswith("transcript") and low.endswith(".md") and len(blob) > TRANSCRIPT_CAP:
            out.append(f"{rel}: size: {len(blob)} bytes > {TRANSCRIPT_CAP} (transcript cap)")
        if blob.startswith(PNG_SIG):
            for t in png_texts(blob):
                out.append(f"{rel}: png-text: {t[:60]!r}")
            continue
        if blob[:6] in (b"GIF87a", b"GIF89a"):
            for t in gif_comments(blob):
                out.append(f"{rel}: gif-comment: {t[:60]!r}")
            continue
        if b"\0" in blob[:8192]:
            continue  # other binary (mp4, blend): not scanned, like audit_release
        _scan_text(rel, blob.decode("utf-8", errors="replace"), rx, out)
    if total > TOTAL_CAP:
        out.append(f".: size: total {total} bytes > {TOTAL_CAP}")
    return out


def fix(results_dir, deny=()) -> dict:
    """Strip PNG text chunks and GIF comments; normalise text files and redact denied terms."""
    root = Path(results_dir)
    terms = deny_terms(deny)
    stats = {"png_chunks": 0, "gif_comments": 0, "files_rewritten": 0}
    for f in _files(root):
        blob = f.read_bytes()
        if blob.startswith(PNG_SIG):
            stats["png_chunks"] += scrub_png(f)
        elif blob[:6] in (b"GIF87a", b"GIF89a"):
            stats["gif_comments"] += scrub_gif(f)
        elif b"\0" not in blob[:8192]:
            try:
                text = blob.decode("utf-8")
            except UnicodeDecodeError:
                continue
            new = normalise(text, REPO, _tmp_root(), Path.home(), _runtime_user())
            for t in sorted(terms, key=len, reverse=True):
                new = re.sub(re.escape(t), "<redacted>", new, flags=re.I)
            if new != text:
                f.write_bytes(new.encode("utf-8"))
                stats["files_rewritten"] += 1
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", metavar="DIR", required=True, help="directory to scan")
    ap.add_argument("--fix", action="store_true", help="strip PNG chunks / GIF comments, normalise and redact text")
    ap.add_argument("--deny", action="append", default=[], help="extra denied term (repeatable)")
    args = ap.parse_args(argv)
    root = Path(args.check)
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 2
    if args.fix:
        print("fixed:", fix(root, args.deny))
    offences = check(root, args.deny)
    for o in offences:
        print(o)
    print(f"scrub: {len(offences)} offence(s) in {root}")
    return 1 if offences else 0


if __name__ == "__main__":
    sys.exit(main())
