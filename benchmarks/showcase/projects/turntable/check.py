"""turntable judge (plain bpy, never imports blender_axi).

    blender --background --factory-startup --python check.py -- <out_dir> <start.blend | ->

`<out_dir>/../work.blend` must still be byte-identical to `start.blend`. Prints one `CHECK {json}` line.
Checks: sheet, sheet_width (>= 1280), mp4_frames (>= 12), hero (a PNG >= 640 wide), work_unchanged.
"""

import hashlib
import json
import sys
from pathlib import Path

MIN_SHEET_WIDTH, MIN_HERO_WIDTH, MIN_FRAMES = 1280, 640, 12


def png_size(path):
    """(width, height) from the PNG IHDR chunk, or None when the file is missing or not a PNG."""
    try:
        head = Path(path).read_bytes()[:24]
    except OSError:
        return None
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def mp4_frames(path, notes):
    """Frame count of the video through Blender's own movie-clip reader (0 when unreadable)."""
    import bpy

    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        notes.append("turntable.mp4 is missing or empty")
        return 0
    try:
        clip = bpy.data.movieclips.load(str(path))
    except Exception as exc:  # unreadable container
        notes.append(f"turntable.mp4 could not be opened: {str(exc)[:120]}")
        return 0
    frames = int(clip.frame_duration)
    notes.append(f"turntable.mp4: {frames} frames, {clip.size[0]}x{clip.size[1]}, {round(float(clip.fps), 2)} fps")
    return frames


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    out_dir = Path(argv[0])
    start = None if len(argv) < 2 or argv[1] == "-" else Path(argv[1])
    work = out_dir.parent / "work.blend"
    notes, checks = [], {}

    sheet = png_size(out_dir / "sheet.png")
    checks["sheet"] = sheet is not None
    checks["sheet_width"] = sheet is not None and sheet[0] >= MIN_SHEET_WIDTH
    if sheet is not None:
        notes.append(f"sheet.png {sheet[0]}x{sheet[1]}")
    checks["mp4_frames"] = mp4_frames(out_dir / "turntable.mp4", notes) >= MIN_FRAMES
    hero = png_size(out_dir / "hero.png")
    checks["hero"] = hero is not None and hero[0] >= MIN_HERO_WIDTH
    if hero is not None:
        notes.append(f"hero.png {hero[0]}x{hero[1]}")
    if start is None or not start.is_file() or not work.is_file():
        checks["work_unchanged"] = False
        notes.append("work.blend or start.blend not found, cannot compare")
    else:
        checks["work_unchanged"] = sha256(work) == sha256(start)
    print("CHECK " + json.dumps({"passed": all(checks.values()), "checks": checks, "notes": notes}))


main()
