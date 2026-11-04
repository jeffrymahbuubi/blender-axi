"""Runs INSIDE Blender: encode a directory of PNG stills into one H.264 mp4 (no ffmpeg binary, requirements F7).

    blender --background --factory-startup --python encode_mp4.py -- <frames_dir> <out.mp4> <fps>

Same approach as `blender_axi/blender_side/video.py`: a temporary scene with one video-sequencer image strip of the
stills (sorted by file name), rendered as an animation. Blender 5.x: the strip collection is `strips`, and
`image_settings.media_type` must be `VIDEO` before `file_format = 'FFMPEG'`. Prints one `ENCODE {json}` line; exits 3 on
failure. Only `bpy` and the standard library are used (Blender's Python has no Pillow).
"""

import glob
import json
import os
import sys


def _even(value: int) -> int:
    """H.264 (yuv420p) needs even sides."""
    return max(2, int(value) - int(value) % 2)


def encode(frames_dir: str, out: str, fps: int) -> dict:
    import bpy

    if not getattr(bpy.app.build_options, "codec_ffmpeg", False):
        raise RuntimeError("this Blender has no FFmpeg support")
    stills = sorted(glob.glob(os.path.join(frames_dir, "*.png")))
    if not stills:
        raise RuntimeError(f"no .png files in {frames_dir}")
    probe = bpy.data.images.load(stills[0])
    width, height = int(probe.size[0]), int(probe.size[1])
    bpy.data.images.remove(probe)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)

    scene = bpy.data.scenes.new("ShowcaseEncode")
    scene.sequence_editor_create()
    strips = scene.sequence_editor.strips  # an empty collection is falsy: never `or` it
    strip = strips.new_image(name="frames", filepath=stills[0], channel=1, frame_start=1)
    for still in stills[1:]:
        strip.elements.append(os.path.basename(still))
    strip.frame_final_duration = len(stills)
    scene.frame_start, scene.frame_end = 1, len(stills)
    render = scene.render
    render.resolution_x, render.resolution_y = _even(width), _even(height)
    render.resolution_percentage = 100
    render.fps = int(fps)
    try:
        scene.view_settings.view_transform = "Standard"  # the stills are already display-referred sRGB
    except Exception:
        pass
    render.image_settings.media_type = "VIDEO"  # before file_format: 5.x rejects FFMPEG on an image media type
    render.image_settings.file_format = "FFMPEG"
    render.ffmpeg.format = "MPEG4"
    render.ffmpeg.codec = "H264"
    try:
        render.ffmpeg.constant_rate_factor = "MEDIUM"
    except Exception:
        pass
    render.use_file_extension = False  # write exactly the path we were given
    render.filepath = out
    with bpy.context.temp_override(scene=scene):
        bpy.ops.render.render(animation=True)
    if not os.path.isfile(out) or os.path.getsize(out) == 0:
        raise RuntimeError(f"Blender wrote no file at {out}")
    return {"path": out, "size_bytes": os.path.getsize(out), "frames": len(stills), "width": _even(width),
            "height": _even(height), "fps": int(fps)}


def main(argv: list[str]) -> int:
    args = argv[argv.index("--") + 1:] if "--" in argv else []
    if len(args) != 3:
        print("usage: blender --background --factory-startup --python encode_mp4.py -- <frames_dir> <out.mp4> <fps>")
        return 3
    try:
        info = encode(args[0], args[1], int(args[2]))
    except Exception as exc:  # report and fail the process; the caller reads the tail
        print(f"ENCODE_ERROR {type(exc).__name__}: {exc}")
        return 3
    print("ENCODE " + json.dumps(info))
    return 0


if __name__ == "__main__":
    code = main(sys.argv)
    if code:
        sys.exit(code)
