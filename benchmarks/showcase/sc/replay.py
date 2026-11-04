"""Workflow replay: scene snapshots -> WORKBENCH renders -> composed frames -> GIF and stacked mp4 (plan 3.4, K7).

    snapshots/NNN.blend --render_snapshots--> frames/NNN_render.png --compose_frames--> frames/NNN.png + final.png
    frames --write_gif--> replay.gif          two runs --pair_mp4--> one 1280x720 mp4 (axi on top, mcp below)

No ffmpeg, no browser, no font file: text uses Pillow's built-in default font. Pillow is imported inside functions; a
missing Pillow prints the install hint and exits 2. Frames and GIFs carry no text chunks and no comment block.
"""

from __future__ import annotations

import io
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENCODE_SCRIPT = HERE / "encode_mp4.py"
_SRC = HERE.parents[2] / "src"
try:
    import blender_axi  # noqa: F401
except ImportError:  # the CLI package is not installed in this interpreter: use the checkout
    sys.path.insert(0, str(_SRC))

from blender_axi import locate, paths  # noqa: E402

INSTALL_HINT = "Pillow is missing: run `uv pip install --python .venv -e .[showcase]` (or `.venv/Scripts/python.exe -m pip install pillow`)"
ARM_TITLES = {"axi": "blender-axi", "mcp": "official MCP"}
ARM_COLORS = {"axi": (96, 205, 130), "mcp": (240, 165, 70)}
BG, PANE, FG, DIM = (20, 22, 26), (30, 33, 39), (222, 226, 232), (138, 145, 156)
OK, BAD = (96, 205, 130), (238, 96, 96)
MAX_TEXT_LINES = 14
FINAL_HOLD_S = 2.0
RENDER_TIMEOUT_S = 240
ENCODE_TIMEOUT_S = 600


class ReplayError(RuntimeError):
    """An external step failed; the message ends with the tail of its output."""


def _pil():
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print(INSTALL_HINT, file=sys.stderr)
        raise SystemExit(2) from None
    return Image, ImageDraw, ImageFont


@lru_cache(maxsize=8)
def _font(size: int):
    """Pillow's built-in default font (an embedded vector font with FreeType, a bitmap font without it)."""
    _, _, image_font = _pil()
    try:
        return image_font.load_default(size=size)
    except TypeError:
        return image_font.load_default()


def _parse_size(size: str) -> tuple[int, int]:
    m = re.fullmatch(r"(\d+)[xX](\d+)", size.strip())
    if not m:
        raise ValueError(f"size must be <width>x<height>, got '{size}'")
    return int(m.group(1)), int(m.group(2))


# ---------------------------------------------------------------------------------------------------------------- renders

def _grey_tile(path: Path, width: int, height: int, text: str = "no render") -> None:
    image_mod, draw_mod, _ = _pil()
    img = image_mod.new("RGB", (width, height), (70, 72, 78))
    draw = draw_mod.Draw(img)
    font = _font(18)
    tw = draw.textlength(text, font=font)
    draw.text(((width - tw) / 2, height / 2 - 10), text, fill=(170, 174, 182), font=font)
    img.save(path, "PNG")


def _render_one(exe: str, snap: Path, out: Path, size: str, env: dict) -> bool:
    out.unlink(missing_ok=True)
    argv = [exe, "render", str(snap), "--fit", "--auto-light", "--engine", "WORKBENCH", "--resolution", size,
            "--out", str(out), "--force"]
    try:
        proc = subprocess.run(argv, cwd=str(out.parent), env=env, stdin=subprocess.DEVNULL, capture_output=True,
                              timeout=RENDER_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and out.is_file() and out.stat().st_size > 0


def render_snapshots(run_dir, blender_axi_exe, size: str = "480x270") -> list[Path]:
    """snapshots/NNN.blend -> frames/NNN_render.png. A failed render becomes a grey "no render" tile; never aborts."""
    run_dir = Path(run_dir)
    width, height = _parse_size(size)
    frames = run_dir / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["BLENDER_AXI_TMP"] = str(paths.tmp_root())
    env["PYTHONIOENCODING"] = "utf-8"
    renders = []
    for snap in sorted((run_dir / "snapshots").glob("*.blend")):
        out = frames / f"{snap.stem}_render.png"
        if not _render_one(str(blender_axi_exe), snap, out, size, env):
            _grey_tile(out, width, height)
        renders.append(out)
    return renders


# ----------------------------------------------------------------------------------------------------- run data and frames

def _load_run(run_dir: Path) -> dict:
    return json.loads((run_dir / "run.json").read_text(encoding="utf-8"))


def _transcript_steps(run_dir: Path) -> dict[int, tuple[str, list[str]]]:
    """{n: (input text, first result lines)} parsed from transcript.md (`## Step n · …` blocks with two fences)."""
    path = run_dir / "transcript.md"
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8", errors="replace")
    heads = list(re.finditer(r"^## Step (\d+)\b.*$", text, re.M))
    steps = {}
    for i, head in enumerate(heads):
        block = text[head.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        fences = re.findall(r"```[^\n]*\n(.*?)```", block, re.S)
        steps[int(head.group(1))] = (fences[0].strip("\n") if fences else "",
                                     fences[1].strip("\n").splitlines() if len(fences) > 1 else [])
    return steps


def _kfmt(n: float) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else f"{int(n)}"


def _totals(run: dict) -> dict:
    u = run.get("usage") or {}
    tin = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    return {"in": tin, "out": int(u.get("output_tokens") or 0), "cost": float(run.get("total_cost_usd") or 0.0),
            "wall": float(run.get("duration_s") or 0.0)}


def _cumulative(run: dict, n: int) -> dict:
    """Approximate running totals after step n: the run's totals scaled by that step's share of (1000 + result_chars)."""
    calls = run.get("tool_calls") or []
    weights = [1000 + int(c.get("result_chars") or 0) for c in calls]
    frac = sum(weights[:n]) / (sum(weights) or 1)
    tot = _totals(run)
    return {"frac": frac, "in": tot["in"] * frac, "out": tot["out"] * frac, "cost": tot["cost"] * frac,
            "wall": tot["wall"] * frac}


def _wrap(draw, text: str, font, max_w: int) -> list[str]:
    lines = []
    for raw in (text[:400].expandtabs(2).splitlines() or [""]):
        line = ""
        for ch in raw:
            if line and draw.textlength(line + ch, font=font) > max_w:
                lines.append(line)
                line = ch
            else:
                line += ch
        lines.append(line)
    return lines


def _fit_render(image_mod, path: Path | None, box_w: int, box_h: int):
    if path is None or not path.is_file():
        return None
    img = image_mod.open(path).convert("RGB")
    scale = min(box_w / img.width, box_h / img.height, 1.25)
    return img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), image_mod.LANCZOS)


def _step_lines(run: dict, steps: dict, n: int) -> list[tuple[str, tuple]]:
    call = (run.get("tool_calls") or [])[n - 1]
    cmd = steps.get(n, ("", []))[0].strip() or str(call.get("summary") or "")
    result = steps.get(n, ("", []))[1]
    lines = [("> " + cmd, FG), ("-" * 28, DIM)]
    if result:
        lines += [(r, BAD if call.get("is_error") else DIM) for r in result]
    else:
        lines.append((f"({call.get('result_chars', 0)} chars returned)", DIM))
    return lines


def _final_lines(run: dict) -> list[tuple[str, tuple]]:
    check = run.get("check") or {}
    tot = _totals(run)
    lines = [("PASSED" if check.get("passed") else "FAILED", OK if check.get("passed") else BAD)]
    failing = [k for k, v in (check.get("checks") or {}).items() if not v]
    if failing:
        lines.append(("failed: " + ", ".join(failing), BAD))
    lines += [("", FG),
              (f"turns {run.get('num_turns', '?')} · tool calls {len(run.get('tool_calls') or [])} · "
               f"errors {run.get('errors', 0)}", FG),
              (f"tokens in {_kfmt(tot['in'])} / out {_kfmt(tot['out'])}", FG),
              (f"cost ${tot['cost']:.2f} (list-price estimate)", FG),
              (f"wall {tot['wall']:.0f} s · Blender starts {run.get('blender_starts', '?')}", FG)]
    return lines


def _compose(run: dict, steps: dict, n: int | None, render: Path | None, width: int, height: int):
    """One frame image. `n` = step number, or None for the final summary frame."""
    image_mod, draw_mod, _ = _pil()
    img = image_mod.new("RGB", (width, height), BG)
    draw = draw_mod.Draw(img)
    left, head, foot = round(width * 440 / 960), 40, 34
    arm = str(run.get("arm", "?"))
    accent = ARM_COLORS.get(arm, FG)
    count = len(run.get("tool_calls") or [])
    draw.rectangle([0, 0, width, head], fill=PANE)
    draw.rectangle([0, 0, 6, head], fill=accent)
    if n is None:
        title = "final result"
    else:
        tool = str((run["tool_calls"][n - 1]).get("tool", "")).replace("mcp__blender__", "mcp:")
        title = f"step {n}/{count} · {tool}"
    draw.text((16, 9), title, fill=FG, font=_font(20))
    tag = f"{ARM_TITLES.get(arm, arm)} · {run.get('project', '')}"
    draw.text((width - 14 - draw.textlength(tag, font=_font(15)), 12), tag, fill=accent, font=_font(15))

    text_font, line_h = _font(15), 19
    lines = _final_lines(run) if n is None else _step_lines(run, steps, n)
    budget = min(MAX_TEXT_LINES, (height - head - foot - 16) // line_h)
    wrapped = []
    for text, color in lines:
        for part in _wrap(draw, text, text_font, left - 28):
            wrapped.append((part, color))
    if len(wrapped) > budget:
        wrapped = wrapped[:budget - 1] + [("...", DIM)]
    y = head + 12
    for part, color in wrapped:
        big = n is None and part in ("PASSED", "FAILED")
        draw.text((16, y), part, fill=color, font=_font(30) if big else text_font)
        y += 38 if big else line_h

    rend = _fit_render(image_mod, render, width - left - 24, height - head - foot - 24)
    if rend is not None:
        img.paste(rend, (left + (width - left - rend.width) // 2, head + (height - head - foot - rend.height) // 2))
    else:
        draw.text((left + 24, head + 24), "no render for this step", fill=DIM, font=text_font)

    draw.rectangle([0, height - foot, width, height], fill=PANE)
    if n is None:
        tot = _totals(run)
        foot_text = (f"total: {_kfmt(tot['in'])} in / {_kfmt(tot['out'])} out · ${tot['cost']:.2f} · {tot['wall']:.0f} s")
    else:
        c = _cumulative(run, n)
        foot_text = f"approx. so far: {_kfmt(c['in'])} in / {_kfmt(c['out'])} out · ${c['cost']:.2f} · {c['wall']:.0f} s"
    draw.text((16, height - foot + 8), foot_text, fill=DIM, font=_font(16))
    return img


def _renders_by_step(renders) -> dict[int, Path]:
    out = {}
    for p in renders:
        m = re.match(r"(\d+)_render$", Path(p).stem)
        if m:
            out[int(m.group(1))] = Path(p)
    return out


def _compose_run(run_dir: Path, renders, width: int, height: int, out_dir: Path) -> list[Path]:
    run, steps = _load_run(run_dir), _transcript_steps(run_dir)
    by_step = _renders_by_step(renders)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for n in range(1, len(run.get("tool_calls") or []) + 1):
        have = [k for k in by_step if k <= n]
        img = _compose(run, steps, n, by_step[max(have)] if have else None, width, height)
        path = out_dir / f"{n:03d}.png"
        img.save(path, "PNG")
        written.append(path)
    final = by_step[max(by_step)] if by_step else None
    if (run_dir / "showcase.png").is_file():  # the task's own deliverable (e.g. the contact sheet) beats the scene
        final = run_dir / "showcase.png"
    path = out_dir / "final.png"
    _compose(run, steps, None, final, width, height).save(path, "PNG")
    return written + [path]


def compose_frames(run_dir, renders, width: int = 960) -> list[Path]:
    """frames/NNN.png (one per tool call, `step n/N`) + frames/final.png (verdict and totals). Returns them in order."""
    run_dir = Path(run_dir)
    return _compose_run(run_dir, renders, width, round(width * 9 / 16), run_dir / "frames")


# -------------------------------------------------------------------------------------------------------------- GIF / mp4

def _pick(frames: list, count: int) -> list:
    """`count` frames spread evenly, always keeping the first and the last."""
    if len(frames) <= count:
        return list(frames)
    count = max(2, count)
    idx = sorted({round(i * (len(frames) - 1) / (count - 1)) for i in range(count)})
    return [frames[i] for i in idx]


def _gif_bytes(images: list, fps: int, colors: int) -> bytes:
    image_mod, _, _ = _pil()
    pal = [im.quantize(colors=colors, method=image_mod.Quantize.MEDIANCUT, dither=image_mod.Dither.NONE)
           for im in images]
    durations = [round(1000 / fps)] * len(pal)
    durations[-1] = round(FINAL_HOLD_S * 1000)
    buf = io.BytesIO()
    pal[0].save(buf, "GIF", save_all=True, append_images=pal[1:], duration=durations, loop=0, optimize=True)
    return buf.getvalue()


def write_gif(frames, out, fps: int = 2, max_frames: int = 40, max_bytes: int = 2_500_000) -> dict:
    """Pillow GIF: even subsample (first and last kept); the last frame is held 2 s. Over `max_bytes` it shrinks
    (960 -> 720 -> 480 px wide, then fewer colours, then fewer frames). Returns {"frames", "bytes", "width"}."""
    image_mod, _, _ = _pil()
    paths_ = [Path(f) for f in frames]
    if not paths_:
        raise ValueError("write_gif needs at least one frame")
    picked = [image_mod.open(p).convert("RGB") for p in _pick(paths_, max_frames)]
    full = picked[0].width
    data, width = b"", full
    for target in dict.fromkeys([full] + [w for w in (720, 480) if w < full]):
        imgs = picked if target == full else [im.resize((target, round(im.height * target / im.width)), image_mod.LANCZOS)
                                              for im in picked]
        width = target
        for colors in (128, 64):
            data = _gif_bytes(imgs, fps, colors)
            if len(data) <= max_bytes:
                break
        if len(data) <= max_bytes:
            break
    while len(data) > max_bytes and len(imgs) > 6:  # last resort: drop frames at the smallest size
        imgs = _pick(imgs, len(imgs) // 2)
        data = _gif_bytes(imgs, fps, 64)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return {"frames": len(imgs), "bytes": len(data), "width": width}


def _blender_exe(blender) -> str:
    if blender is None:
        return str(locate.find_blender().path)
    return str(getattr(blender, "path", blender))


def write_mp4(frames_dir, out, fps: int, blender=None) -> dict:
    """Encode every PNG in `frames_dir` (sorted by name) to H.264 mp4 inside Blender via sc/encode_mp4.py."""
    frames_dir, out = Path(frames_dir), Path(out)
    count = len(list(frames_dir.glob("*.png")))
    out.parent.mkdir(parents=True, exist_ok=True)
    argv = [_blender_exe(blender), "--background", "--factory-startup", "--python-exit-code", "3",
            "--python", str(ENCODE_SCRIPT), "--", str(frames_dir), str(out), str(int(fps))]
    try:
        proc = subprocess.run(argv, env=paths.blender_env(), cwd=str(out.parent), stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=ENCODE_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReplayError(f"could not run Blender for the mp4: {exc}") from exc
    if proc.returncode != 0 or not out.is_file() or out.stat().st_size == 0:
        tail = (proc.stdout[-500:] + proc.stderr[-300:]).strip()
        raise ReplayError(f"mp4 encoding failed (exit {proc.returncode}): {tail}")
    return {"bytes": out.stat().st_size, "frames": count}


def pair_mp4(run_a, run_b, out, blender=None, fps: int = 2) -> dict:
    """One 1280x720 mp4: run_a (axi) on top, run_b (mcp) below, each in a 1280x360 band; the shorter holds its last
    frame; the very last frame is held 2 s. Needs `replay.run` (or `render_snapshots`) to have written both frames/."""
    image_mod, _, _ = _pil()
    work = paths.tmp_root() / "showcase" / "pair" / f"{os.getpid()}-{secrets.token_hex(3)}"
    try:
        bands = []
        for run_dir in (Path(run_a), Path(run_b)):
            renders = sorted((run_dir / "frames").glob("*_render.png"))
            bands.append(_compose_run(run_dir, renders, 1280, 360, work / f"band{len(bands)}"))
        seq = work / "seq"
        seq.mkdir(parents=True, exist_ok=True)
        total = max(len(b) for b in bands) + round(FINAL_HOLD_S * fps) - 1
        for i in range(total):
            img = image_mod.new("RGB", (1280, 720), BG)
            for row, band in enumerate(bands):
                img.paste(image_mod.open(band[min(i, len(band) - 1)]).convert("RGB"), (0, 360 * row))
            img.save(seq / f"{i:05d}.png", "PNG")
        return write_mp4(seq, out, fps, blender) | {"width": 1280, "height": 720}
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ------------------------------------------------------------------------------------------------------------------ run()

def _cfg(cfg, name: str, default=None):
    if cfg is None:
        return default
    value = cfg.get(name) if isinstance(cfg, dict) else getattr(cfg, name, None)
    return default if value is None else value


def _axi_exe(cfg) -> str:
    explicit = _cfg(cfg, "blender_axi_exe") or _cfg(cfg, "axi_exe")
    if explicit:
        return str(explicit)
    exe_name = "blender-axi.exe" if sys.platform == "win32" else "blender-axi"
    for folder in (_cfg(cfg, "axi_bin"), Path(sys.executable).parent):
        if folder and (Path(folder) / exe_name).is_file():
            return str(Path(folder) / exe_name)
    return shutil.which("blender-axi") or "blender-axi"


def run(run_dir, cfg=None) -> dict:
    """snapshots -> renders -> frames -> `<run_dir>/replay.gif`. `cfg` may be a dict or an object; read when present:
    blender_axi_exe | axi_exe | axi_bin, replay_size ("480x270"), replay_fps (2), replay_max_frames (40)."""
    run_dir = Path(run_dir)
    renders = render_snapshots(run_dir, _axi_exe(cfg), str(_cfg(cfg, "replay_size", "480x270")))
    frames = compose_frames(run_dir, renders)
    gif = write_gif(frames, run_dir / "replay.gif", fps=int(_cfg(cfg, "replay_fps", 2)),
                    max_frames=int(_cfg(cfg, "replay_max_frames", 40)))
    return gif | {"path": str(run_dir / "replay.gif"), "renders": len(renders)}
