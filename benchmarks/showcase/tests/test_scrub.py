"""sc/scrub.py: leak gate. Deny terms here are made up; the real ones come from env/file/runtime at run time."""
import getpass
import struct
import zlib
from pathlib import Path

import pytest

from sc import scrub

DENY = ("jdoe-box", "Secret-Projects")
FAKE_PATH = "C:" + "\\" + "Users" + "\\" + "jdoe" + "\\" + "proj"  # scan:allow (built to test detection)


def _chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def make_png(path: Path, text: str | None = None, kind: bytes = b"tEXt") -> Path:
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00")
    parts = [scrub.PNG_SIG, _chunk(b"IHDR", ihdr)]
    if text is not None:
        body = b"File\0" + text.encode() if kind == b"tEXt" else b"File\0\0" + zlib.compress(text.encode())
        parts.append(_chunk(kind, body))
    parts += [_chunk(b"IDAT", idat), _chunk(b"IEND", b"")]
    path.write_bytes(b"".join(parts))
    return path


def make_gif(path: Path, comment: bytes | None = None) -> Path:
    head = b"GIF89a" + struct.pack("<HHBBB", 1, 1, 0x80, 0, 0) + b"\x00\x00\x00\xff\xff\xff"
    cmt = b"\x21\xfe" + bytes([len(comment)]) + comment + b"\x00" if comment is not None else b""
    img = b"\x2c" + struct.pack("<HHHHB", 0, 0, 1, 1, 0) + b"\x02\x02\x44\x01\x00"
    path.write_bytes(head + cmt + img + b"\x3b")
    return path


def kinds(offences):
    return {o.split(": ")[1] for o in offences}


def test_clean_dir_passes(work):
    make_png(work / "a.png")
    make_gif(work / "a.gif")
    (work / "transcript_axi.md").write_text("## Step 1\n<repo>/x.blend, port 50000, <home>/y\n", encoding="utf-8")
    assert scrub.check(work, DENY) == []


def test_png_text_chunk_with_path_is_flagged(work):
    make_png(work / "bad.png", FAKE_PATH + "\\x.blend")
    make_png(work / "bad2.png", FAKE_PATH, kind=b"zTXt")
    out = scrub.check(work, DENY)
    assert len([o for o in out if "png-text" in o]) == 2


def test_transcript_with_denied_term_flagged(work):
    (work / "transcript_mcp.md").write_text("fine\nopened Secret-Projects/thing\n", encoding="utf-8")
    out = scrub.check(work, DENY)
    assert any(o.startswith("transcript_mcp.md:2: denied-term") for o in out)


def test_local_path_secret_email_flagged(work):
    (work / "t.md").write_text(f"{FAKE_PATH}\\x\nsk-ant-" + "a" * 20 + "\nwrite to someone" + "@corp-mail.org\n", encoding="utf-8")
    assert kinds(scrub.check(work, DENY)) >= {"local-path", "secret", "email"}


def test_scan_allow_and_placeholder_skipped(work):
    (work / "t.md").write_text(FAKE_PATH + " scan:allow\n" + "C:" + "\\Users\\you\\x\n", encoding="utf-8")
    assert scrub.check(work, DENY) == []


def test_runtime_user_is_always_denied(work):
    user = getpass.getuser()
    if len(user) < 3:
        pytest.skip("user name too short to match safely")
    (work / "t.md").write_text(f"hello {user}\n", encoding="utf-8")
    assert "denied-term" in kinds(scrub.check(work, ()))


def test_deny_terms_from_env_and_file(work, monkeypatch):
    f = work / "deny.txt"
    f.write_text("# comment\nfile-term\n\n", encoding="utf-8")
    monkeypatch.setenv("SHOWCASE_DENY", "env-one, env-two")
    terms = scrub.deny_terms(extra=("arg-term",), file=f)
    low = {t.lower() for t in terms}
    assert {"env-one", "env-two", "file-term", "arg-term"} <= low
    assert str(Path.home()).lower() in low


def test_gif_with_comment_flagged(work):
    make_gif(work / "c.gif", b"made in " + FAKE_PATH.encode())
    out = scrub.check(work, DENY)
    assert kinds(out) == {"gif-comment"}


def test_pillow_gif_has_no_comment(work):
    Image = pytest.importorskip("PIL.Image")
    frames = [Image.new("RGB", (32, 32), (i * 20, 0, 0)) for i in range(4)]
    frames[0].save(work / "p.gif", save_all=True, append_images=frames[1:], duration=500, loop=0)
    assert scrub.check(work, DENY) == []


def test_oversize_files_flagged(work):
    (work / "big.gif").write_bytes(b"GIF89a" + b"\0" * scrub.GIF_CAP)
    (work / "transcript_axi.md").write_text("x" * (scrub.TRANSCRIPT_CAP + 1), encoding="utf-8")
    big = work / "big.png"
    make_png(big)
    big.write_bytes(big.read_bytes() + b"\0" * scrub.PNG_CAP)
    out = scrub.check(work, DENY)
    assert len([o for o in out if ": size:" in o]) == 3


def test_total_cap(work, monkeypatch):
    monkeypatch.setattr(scrub, "TOTAL_CAP", 100)
    (work / "a.bin").write_bytes(b"\0" * 200)
    assert any("total" in o for o in scrub.check(work, DENY))


def test_normalise_replaces_paths_user_and_port():
    repo, tmp, home = Path("D:/code/proj"), Path("D:/code/proj/.tmp"), Path("C:/Users/jdoe")  # scan:allow (fake)
    run = tmp / "showcase" / "runs" / "p" / "axi" / "r1"
    text = (f"cd {run}\nread {str(repo / 'src' / 'a.py').replace('/', chr(92))}\n"
            f"json {str(tmp / 'x').replace('/', chr(92) * 2)}\nopen {home}/Desktop/f.blend\n"
            "user is jdoe here; blender-axi --port 61234 exec\nother " + "C:" + "\\Users\\bob\\y")  # scan:allow
    out = scrub.normalise(text, repo, tmp, home, "jdoe", run=run)
    assert "<run>" in out and "<repo>" in out and "<tmp>" in out and "<home>" in out and "<user>" in out
    assert "--port 50000" in out and "61234" not in out
    for leaked in ("D:/code", "D:\\code", "jdoe", "bob", "Users"):
        assert leaked not in out, leaked


def test_normalise_longest_path_wins():
    out = scrub.normalise("D:/r/.tmp/x", "D:/r", "D:/r/.tmp", "C:/Users/jdoe", "jdoe")  # scan:allow (fake)
    assert out == "<tmp>/x"


def test_normalise_git_bash_drive_paths():  # `pwd` / `which` in Git Bash print /d/... (seen in the Haiku run)
    out = scrub.normalise("/d/code/proj/.tmp/showcase/x and /d/code/proj/.venv/Scripts/blender-axi",
                          "D:/code/proj", "D:/code/proj/.tmp", "C:/Users/jdoe", "jdoe")  # scan:allow (fake)
    assert out == "<tmp>/showcase/x and <repo>/.venv/Scripts/blender-axi"


def test_normalise_claude_project_slug():  # oversized tool results are saved under a dashed project-path folder
    out = scrub.normalise("<x>\\projects\\D--code-proj--tmp-showcase-runs-p\\t.json and D--code-proj",
                          "D:/code/proj", "D:/code/proj/.tmp", "C:/Users/jdoe", "jdoe")  # scan:allow (fake)
    assert "code" not in out and "proj" not in out.replace("projects", "")


def test_normalise_escaped_and_clipped_paths():  # JSON-in-JSON escaping, cut by the transcript line clip
    text = 'Read blend: \\"D:\\\\\\\\code\\\\\\\\Pro ... and jq \'D:\\\\code\\\\proj\\\\b ...'  # scan:allow (fake)
    out = scrub.normalise(text, "D:/code/proj", "D:/code/proj/.tmp", "C:/Users/jdoe", "jdoe")  # scan:allow (fake)
    assert "code" not in out and "<local-path>" in out


def test_scrub_png_strips_chunks(work):
    p = make_png(work / "a.png", FAKE_PATH)
    assert scrub.scrub_png(p) == 1
    assert scrub.png_texts(p.read_bytes()) == []
    assert scrub.scrub_png(p) == 0
    assert p.read_bytes().startswith(scrub.PNG_SIG) and p.read_bytes().endswith(_chunk(b"IEND", b""))


def test_scrub_gif_removes_comment(work):
    p = make_gif(work / "a.gif", b"hello")
    assert scrub.scrub_gif(p) == 1
    assert scrub.gif_comments(p.read_bytes()) == []
    assert p.read_bytes().endswith(b"\x3b")


def test_fix_makes_flagged_dir_pass(work):
    make_png(work / "bad.png", FAKE_PATH)
    make_gif(work / "bad.gif", b"x" * 5)
    (work / "transcript_axi.md").write_text(f"see Secret-Projects and {scrub.REPO}/src/a\n", encoding="utf-8")
    assert scrub.check(work, DENY)
    stats = scrub.fix(work, DENY)
    assert stats["png_chunks"] == 1 and stats["gif_comments"] == 1 and stats["files_rewritten"] == 1
    assert scrub.check(work, DENY) == []
    assert "<redacted>" in (work / "transcript_axi.md").read_text(encoding="utf-8")


def test_cli_exit_codes(work, capsys):
    (work / "ok.md").write_text("fine\n", encoding="utf-8")
    assert scrub.main(["--check", str(work), "--deny", "jdoe-box"]) == 0
    (work / "bad.md").write_text("jdoe-box was here\n", encoding="utf-8")
    assert scrub.main(["--check", str(work), "--deny", "jdoe-box"]) == 1
    assert scrub.main(["--check", str(work), "--deny", "jdoe-box", "--fix"]) == 0
    assert scrub.main(["--check", str(work / "missing")]) == 2
