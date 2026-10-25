"""bench.artifacts: truncation, raw-file records and the size budget."""

import hashlib

import pytest
from bench.artifacts import RAW_LIMIT, check_budget, sha256_text, truncate, write_raw

from blender_axi import paths


@pytest.fixture
def results(request):
    """A fresh results dir under the project .tmp (never the system temp)."""
    path = paths.tmp_root() / "bench_artifacts" / request.node.name
    if path.exists():
        for f in sorted(path.rglob("*"), reverse=True):
            f.unlink() if f.is_file() else f.rmdir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_sha256_text():
    assert sha256_text("abc") == hashlib.sha256(b"abc").hexdigest()


def test_truncate_short_text_unchanged():
    assert truncate("hello") == ("hello", False)
    boundary = "x" * RAW_LIMIT
    assert truncate(boundary) == (boundary, False)


def test_truncate_long_text_head_and_trailer():
    text = "ab" * 15_000  # 30,000 chars
    stored, cut = truncate(text)
    assert cut is True
    head, trailer = stored[:RAW_LIMIT], stored[RAW_LIMIT:]
    assert head == text[:RAW_LIMIT]
    assert trailer == f"\n…[truncated: 30000 chars total, sha256 {sha256_text(text)[:12]}]"
    assert len(sha256_text(text)[:12]) == 12


def test_truncate_custom_limit():
    stored, cut = truncate("0123456789", limit=4)
    assert cut and stored.startswith("0123\n…[truncated: 10 chars total")


def test_write_raw_record_and_file(results):
    record = write_raw(results, "desk/objects__axi.txt", "hello\nworld\n")
    assert record == {
        "raw": "desk/objects__axi.txt",
        "chars": 12,
        "truncated": False,
        "sha256": sha256_text("hello\nworld\n"),
    }
    assert (results / "desk" / "objects__axi.txt").read_bytes() == b"hello\nworld\n"  # LF, no CRLF translation


def test_write_raw_utf8(results):
    write_raw(results, "a.txt", "né…")
    assert (results / "a.txt").read_bytes() == "né…".encode("utf-8")


def test_write_raw_truncates_but_records_full_size(results):
    text = "y" * 25_000
    record = write_raw(results, "city/objects__mcp.json", text)
    assert record["chars"] == 25_000
    assert record["truncated"] is True
    assert record["sha256"] == sha256_text(text)
    stored = (results / "city" / "objects__mcp.json").read_text(encoding="utf-8")
    assert stored.startswith("y" * RAW_LIMIT + "\n…[truncated: 25000 chars total")


def test_write_raw_rejects_escaping_path(results):
    with pytest.raises(ValueError):
        write_raw(results, "../outside.txt", "x")
    assert not (results.parent / "outside.txt").exists()


def test_check_budget_within_budget_is_empty(results):
    (results / "a.txt").write_bytes(b"x" * 1000)
    assert check_budget(results) == []


def test_check_budget_missing_dir_is_empty(results):
    assert check_budget(results / "nope") == []


def test_check_budget_flags_big_file(results):
    (results / "sub").mkdir()
    (results / "sub" / "big.txt").write_bytes(b"x" * 70_000)
    out = check_budget(results)
    assert len(out) == 1
    assert "sub/big.txt" in out[0] and "70000" in out[0]


def test_check_budget_flags_total(results):
    for i in range(10):
        (results / f"f{i}.txt").write_bytes(b"x" * 60_000)  # each under 64 KiB, 600,000... plus one more pushes it over
    (results / "extra.txt").write_bytes(b"x" * 1000)
    out = check_budget(results)
    assert len(out) == 1 and out[0].startswith("total: 601000")


def test_check_budget_flags_both(results):
    for i in range(10):
        (results / f"f{i}.txt").write_bytes(b"x" * 70_000)
    out = check_budget(results)
    assert len(out) == 11  # ten files over the per-file cap, plus the 700 KB total
    assert any(line.startswith("total: 700000") for line in out)
