"""U3 replay tests: composed frames, GIF writer, failing renders, and (marker `blender`) real mp4 encoding.

Temp files live under paths.tmp_root()/"showcase"/"tests"; the stdlib temp module is never used.
"""

import json
import random
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SHOWCASE = HERE.parent
REPO = SHOWCASE.parents[1]
for _p in (str(REPO / "src"), str(REPO / "benchmarks"), str(SHOWCASE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

PIL = pytest.importorskip("PIL")
from PIL import Image, ImageDraw  # noqa: E402

from blender_axi import paths  # noqa: E402
from blender_axi.locate import find_blender  # noqa: E402
from sc import replay  # noqa: E402


@pytest.fixture
def work():
    path = paths.tmp_root() / "showcase" / "tests" / f"replay-{secrets.token_hex(3)}"
    path.mkdir(parents=True)
    yield path
    shutil.rmtree(path, ignore_errors=True)


def fake_render(path: Path, color, size=(480, 270)) -> None:
    img = Image.new("RGB", size, color)
    ImageDraw.Draw(img).rectangle([40, 40, 200, 160], fill=(240, 240, 240))
    img.save(path)


def make_run(root: Path, calls: int = 4, arm: str = "axi", passed: bool = True) -> Path:
    run = root / f"run_{arm}"
    (run / "frames").mkdir(parents=True)
    tool_calls = [{"n": i, "tool": "Bash" if i % 2 else "mcp__blender__execute_blender_code",
                   "summary": f"blender-axi objects work.blend --step {i}", "result_chars": 500 * i,
                   "is_error": i == 3, "ms": 1000} for i in range(1, calls + 1)]
    (run / "run.json").write_text(json.dumps({
        "schema": 1, "project": "turntable", "arm": arm, "repeat": 1, "model": "claude-opus-5-5", "duration_s": 100.0,
        "num_turns": calls + 1, "stop": "end_turn", "total_cost_usd": 0.5, "errors": 1, "blender_starts": calls,
        "usage": {"input_tokens": 1000, "cache_creation_input_tokens": 9000, "cache_read_input_tokens": 90000,
                  "output_tokens": 4000},
        "tool_calls": tool_calls,
        "check": {"passed": passed, "checks": {"sheet": True, "mp4_frames": passed}}}), encoding="utf-8")
    lines = []
    for i in range(1, calls + 1):
        lines += [f"## Step {i} · Bash · 1000 ms", "```", f"blender-axi objects work.blend --step {i}", "```", "```",
                  "\n".join(f"result line {j} of step {i}" for j in range(30)), "```", "assistant text", ""]
    (run / "transcript.md").write_text("\n".join(lines), encoding="utf-8")
    return run


def gif_extension_labels(data: bytes) -> list[int]:
    """Walk the GIF block structure and return the label byte of every extension block (0xFE = comment)."""
    assert data[:6] in (b"GIF87a", b"GIF89a")
    pos = 13
    if data[10] & 0x80:
        pos += 3 * (2  << (data[10] & 7))
    labels = []

    def skip_sub_blocks(p: int) -> int:
        while data[p]:
            p += data[p] + 1
        return p + 1

    while data[pos] != 0x3B:
        if data[pos] == 0x21:
            labels.append(data[pos + 1])
            pos = skip_sub_blocks(pos + 2)
        elif data[pos] == 0x2C:
            flags = data[pos + 9]
            pos += 10
            if flags & 0x80:
                pos += 3 * (2 << (flags & 7))
            pos = skip_sub_blocks(pos + 1)
        else:
            raise AssertionError(f"bad GIF block 0x{data[pos]:02x} at {pos}")
    return labels


def test_compose_frames_960x540_without_text_chunks(work):
    run = make_run(work)
    fake_render(run / "frames" / "001_render.png", (60, 90, 140))
    fake_render(run / "frames" / "003_render.png", (140, 90, 60))
    renders = sorted((run / "frames").glob("*_render.png"))
    frames = replay.compose_frames(run, renders)
    assert [p.name for p in frames] == ["001.png", "002.png", "003.png", "004.png", "final.png"]
    for p in frames:
        raw = p.read_bytes()
        with Image.open(p) as img:
            assert img.size == (960, 540)
            assert not getattr(img, "text", {})
        assert b"tEXt" not in raw and b"zTXt" not in raw and b"iTXt" not in raw
    # steps 2 and 4 hold the previous render, so frames differ from each other only through text/footer
    a, b = (Image.open(frames[i]).convert("RGB") for i in (0, 2))
    assert a.tobytes() != b.tobytes()


def test_compose_without_renders_or_transcript_still_works(work):
    run = make_run(work, calls=2, passed=False)
    (run / "transcript.md").unlink()
    frames = replay.compose_frames(run, [])
    assert len(frames) == 3 and all(p.is_file() for p in frames)


def test_transcript_steps_and_cumulative(work):
    run = make_run(work, calls=3)
    steps = replay._transcript_steps(run)
    assert steps[2][0] == "blender-axi objects work.blend --step 2"
    assert steps[2][1][0] == "result line 0 of step 2" and len(steps[2][1]) == 30
    data = json.loads((run / "run.json").read_text(encoding="utf-8"))
    cum = [replay._cumulative(data, n) for n in (1, 2, 3)]
    assert [c["cost"] for c in cum] == sorted(c["cost"] for c in cum)
    assert cum[-1]["cost"] == pytest.approx(0.5) and cum[-1]["in"] == 100000 and cum[-1]["out"] == 4000


def test_write_gif_subsamples_and_has_no_comment(work):
    frames = []
    for i in range(60):
        path = work / f"f{i:03d}.png"
        Image.new("RGB", (960, 540), (i * 4 % 256, 30, 200 - i * 3)).save(path)
        frames.append(path)
    out = work / "replay.gif"
    info = replay.write_gif(frames, out)
    assert info["frames"] <= 40 and info["bytes"] == out.stat().st_size <= 2_500_000 and info["width"] == 960
    with Image.open(out) as gif:
        assert gif.n_frames == info["frames"]
        assert "comment" not in gif.info
        gif.seek(0)
        first = gif.convert("RGB").getpixel((5, 5))
        gif.seek(gif.n_frames - 1)
        last = gif.convert("RGB").getpixel((5, 5))
    assert all(abs(a - b) < 12 for a, b in zip(first, (0, 30, 200)))
    assert all(abs(a - b) < 12 for a, b in zip(last, (59 * 4 % 256, 30, 200 - 59 * 3)))
    assert 0xFE not in gif_extension_labels(out.read_bytes())


def test_write_gif_shrinks_to_480_when_over_max_bytes(work):
    rng = random.Random(1)
    frames = []
    for i in range(8):
        img = Image.new("RGB", (960, 540))
        img.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256)) for _ in range(960 * 540 // 16)] * 16)
        path = work / f"n{i}.png"
        img.save(path)
        frames.append(path)
    info = replay.write_gif(frames, work / "noisy.gif", max_bytes=3_000_000)
    assert info["width"] < 960 and info["bytes"] <= 3_000_000


def test_write_gif_rejects_no_frames(work):
    with pytest.raises(ValueError):
        replay.write_gif([], work / "x.gif")


def test_render_snapshots_with_failing_exe_gives_grey_tiles(work):
    run = work / "run"
    (run / "snapshots").mkdir(parents=True)
    for n in (1, 2):
        (run / "snapshots" / f"{n:03d}.blend").write_bytes(b"not a blend")
    renders = replay.render_snapshots(run, str(work / "no-such-blender-axi.exe"), size="320x180")
    assert [p.name for p in renders] == ["001_render.png", "002_render.png"]
    for p in renders:
        with Image.open(p) as img:
            assert img.size == (320, 180)
            assert img.convert("RGB").getpixel((2, 2)) == (70, 72, 78)


def test_run_with_failing_exe_still_writes_gif(work):
    run = make_run(work, calls=2)
    (run / "snapshots").mkdir()
    (run / "snapshots" / "001.blend").write_bytes(b"x")
    info = replay.run(run, {"axi_exe": str(work / "missing.exe")})
    assert (run / "replay.gif").is_file() and info["renders"] == 1 and info["frames"] == 3


def blender_exe() -> str:
    return str(find_blender().path)


def movieclip_probe(mp4: Path) -> dict:
    expr = ("import bpy, json; c = bpy.data.movieclips.load(r'%s'); "
            "print('CLIP ' + json.dumps({'frames': c.frame_duration, 'size': list(c.size)}))" % mp4)
    proc = subprocess.run([blender_exe(), "--background", "--factory-startup", "--python-expr", expr],
                          env=paths.blender_env(), capture_output=True, text=True, timeout=300)
    line = next(ln for ln in proc.stdout.splitlines() if ln.startswith("CLIP "))
    return json.loads(line[5:])


@pytest.mark.blender
def test_write_mp4_six_frames(work):
    frames = work / "seq"
    frames.mkdir()
    for i in range(6):
        Image.new("RGB", (640, 360), (40 * i, 80, 160)).save(frames / f"{i:03d}.png")
    out = work / "out" / "clip.mp4"
    info = replay.write_mp4(frames, out, 2, blender_exe())
    head = out.read_bytes()[:12]
    assert out.stat().st_size > 0 and b"ftyp" in head and info == {"bytes": out.stat().st_size, "frames": 6}
    clip = movieclip_probe(out)  # ASSUMED A3: frame_duration is the frame count of Blender's own H.264 output
    assert clip["frames"] == 6 and clip["size"] == [640, 360]


@pytest.mark.blender
def test_pair_mp4_stacks_two_runs_1280x720(work):
    runs = []
    for arm, calls in (("axi", 3), ("mcp", 5)):
        run = make_run(work, calls=calls, arm=arm)
        fake_render(run / "frames" / "001_render.png", (60, 90, 140))
        runs.append(run)
    out = work / "pair.mp4"
    info = replay.pair_mp4(runs[0], runs[1], out, blender_exe())
    assert info["frames"] == 6 + 4 - 1 and (info["width"], info["height"]) == (1280, 720)
    assert b"ftyp" in out.read_bytes()[:12]
    clip = movieclip_probe(out)
    assert clip["frames"] == info["frames"] and clip["size"] == [1280, 720]
