"""`render --turntable --out name.mp4`: encode the rendered stills into one H.264 mp4 (0.6.0 plan K5, requirements F15).

A temporary scene with a video-sequencer strip of the stills is rendered as an animation under `temp_override`, so the
user's scene (frame, settings, selection) is never touched; the temporary scene is removed in `finally`. Blender 5.x
names the strip collection `strips` (`sequences` is gone) and `image_settings.media_type` must be set to `VIDEO`
before `file_format = 'FFMPEG'`.

Data API only; `bpy` is passed in so the unit tests can drive it with plain stubs.
"""

import os

from .ops import OpError

VIDEO_EXTS = {".mp4"}
ENCODE_SCENE = "AxiEncode"
CODEC = "H264"


def _even(value: int) -> int:
    """H.264 (yuv420p) needs even sides."""
    return max(2, int(value) - int(value) % 2)


def encode(bpy, stills: list[str], out: str, fps: int, size: tuple[int, int]) -> dict:
    """Write `stills` (in order) to `out` as H.264 in MPEG-4 at `fps`. Returns path, size_bytes, frames, codec."""
    if not getattr(bpy.app.build_options, "codec_ffmpeg", False):
        raise OpError("OP_FAILED", "video encoding unavailable: this Blender has no FFmpeg")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    enc = bpy.data.scenes.new(ENCODE_SCENE)
    try:
        enc.sequence_editor_create()
        strips = enc.sequence_editor.strips  # an empty collection is falsy: never `or` it with anything
        strip = strips.new_image(name="turntable", filepath=stills[0], channel=1, frame_start=1)
        for still in stills[1:]:
            strip.elements.append(os.path.basename(still))
        strip.frame_final_duration = len(stills)
        enc.frame_start, enc.frame_end = 1, len(stills)
        render = enc.render
        render.resolution_x, render.resolution_y = _even(size[0]), _even(size[1])
        render.resolution_percentage = 100
        render.fps = int(fps)
        try:
            enc.view_settings.view_transform = "Standard"  # the stills already carry the original view transform
        except Exception:
            pass
        render.image_settings.media_type = "VIDEO"  # before file_format: 5.x rejects FFMPEG on an image media type
        render.image_settings.file_format = "FFMPEG"
        render.ffmpeg.format = "MPEG4"
        render.ffmpeg.codec = CODEC
        render.use_file_extension = False  # write exactly the path we computed
        render.filepath = out
        with bpy.context.temp_override(scene=enc):
            bpy.ops.render.render(animation=True)
    except OpError:
        raise
    except Exception as exc:
        raise OpError("OP_FAILED", f"video encoding failed: {exc}") from exc
    finally:
        try:
            bpy.data.scenes.remove(enc)
        except Exception:
            pass
    if not os.path.isfile(out) or os.path.getsize(out) == 0:
        raise OpError("OP_FAILED", f"Blender encoded the video but wrote no file at {out}")
    return {"path": out, "size_bytes": os.path.getsize(out), "frames": len(stills), "codec": CODEC}
