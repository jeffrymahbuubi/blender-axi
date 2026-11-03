"""`blender_side.video.encode` (0.6.0 plan U5) with a fake bpy: the F15 recipe, its order, cleanup and errors."""

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from blender_axi import paths
from blender_axi.blender_side import video
from blender_axi.blender_side.ops import OpError


class Recorder:
    """Records attribute assignments in order, so the media_type-before-file_format rule is testable."""

    def __init__(self, log, label):
        object.__setattr__(self, "_log", log)
        object.__setattr__(self, "_label", label)

    def __setattr__(self, name, value):
        self._log.append((self._label, name, value))
        object.__setattr__(self, name, value)


@pytest.fixture
def work():
    d = paths.tmp_root() / "tests" / "unit_video"
    d.mkdir(parents=True, exist_ok=True)
    return d


def make_bpy(out: Path, ffmpeg=True, fail_render=False, write=True):
    log, removed, calls = [], [], {"strips": [], "elements": [], "overrides": [], "renders": []}
    strip = NS(elements=NS(append=lambda name: calls["elements"].append(name)), frame_final_duration=0)
    strips = NS(new_image=lambda **kw: calls["strips"].append(kw) or strip)
    enc = NS(name="AxiEncode", sequence_editor=NS(strips=strips), sequence_editor_create=lambda: None,
             view_settings=Recorder(log, "view"))
    enc.render = Recorder(log, "render")
    enc.render.image_settings = Recorder(log, "image")
    enc.render.ffmpeg = Recorder(log, "ffmpeg")

    @contextmanager
    def temp_override(**kw):
        calls["overrides"].append(kw)
        yield

    def render(animation):
        calls["renders"].append(animation)
        if fail_render:
            raise RuntimeError("encoder exploded")
        if write:
            out.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"x" * 40)

    bpy = NS(app=NS(build_options=NS(codec_ffmpeg=ffmpeg)),
             data=NS(scenes=NS(new=lambda name: enc, remove=lambda s: removed.append(s))),
             context=NS(temp_override=temp_override), ops=NS(render=NS(render=render)))
    return bpy, enc, log, removed, calls


def test_encode_builds_the_strip_and_renders_the_animation_under_the_temp_scene(work):
    out = work / "ok.mp4"
    out.unlink(missing_ok=True)
    bpy, enc, log, removed, calls = make_bpy(out)
    stills = [str(work / f"turn_{i:04d}.png") for i in (1, 2, 3)]
    res = video.encode(bpy, stills, str(out), 24, (49, 33))
    assert res == {"path": str(out), "size_bytes": out.stat().st_size, "frames": 3, "codec": "H264"}
    assert calls["strips"] == [{"name": "turntable", "filepath": stills[0], "channel": 1, "frame_start": 1}]
    assert calls["elements"] == ["turn_0002.png", "turn_0003.png"]
    assert (enc.frame_start, enc.frame_end) == (1, 3) and enc.render.fps == 24
    assert (enc.render.resolution_x, enc.render.resolution_y) == (48, 32)  # even sides for yuv420p
    assert enc.render.resolution_percentage == 100 and enc.render.filepath == str(out)
    assert calls["overrides"] == [{"scene": enc}] and calls["renders"] == [True]
    assert removed == [enc]


def test_media_type_is_set_before_the_file_format(work):
    out = work / "order.mp4"
    bpy, _, log, _, _ = make_bpy(out)
    video.encode(bpy, [str(work / "a.png")], str(out), 12, (64, 64))
    image = [(name, value) for label, name, value in log if label == "image"]
    assert image == [("media_type", "VIDEO"), ("file_format", "FFMPEG")]
    assert [(n, v) for label, n, v in log if label == "ffmpeg"] == [("format", "MPEG4"), ("codec", "H264")]


def test_the_temporary_scene_is_removed_when_the_render_fails(work):
    out = work / "fail.mp4"
    bpy, enc, _, removed, _ = make_bpy(out, fail_render=True)
    with pytest.raises(OpError, match="video encoding failed: encoder exploded") as err:
        video.encode(bpy, [str(work / "a.png")], str(out), 12, (64, 64))
    assert err.value.code == "OP_FAILED" and removed == [enc]


def test_no_ffmpeg_is_a_clear_error_and_creates_no_scene(work):
    out = work / "noff.mp4"
    bpy, _, _, removed, calls = make_bpy(out, ffmpeg=False)
    with pytest.raises(OpError, match="video encoding unavailable: this Blender has no FFmpeg"):
        video.encode(bpy, [str(work / "a.png")], str(out), 12, (64, 64))
    assert calls["renders"] == [] and removed == []


def test_a_render_that_writes_nothing_is_reported(work):
    out = work / "nothing.mp4"
    out.unlink(missing_ok=True)
    bpy, enc, _, removed, _ = make_bpy(out, write=False)
    with pytest.raises(OpError, match="wrote no file"):
        video.encode(bpy, [str(work / "a.png")], str(out), 12, (64, 64))
    assert removed == [enc]


def test_video_extensions():
    assert video.VIDEO_EXTS == {".mp4"}
