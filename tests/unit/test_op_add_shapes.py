"""0.10.0 U4: the pure-Python UV sphere and torus, and `cube` with `dimensions` (fake bm recording what is built)."""

import math
import sys
import types

import pytest

from blender_axi.blender_side import op_add


class FakeBm:
    """`verts.new` / `faces.new` record tuples; a vertex is its coordinate tuple, a face the tuple of its vertices."""

    def __init__(self):
        self.vert_list, self.face_list = [], []
        self.verts = types.SimpleNamespace(new=self._vert)
        self.faces = types.SimpleNamespace(new=self._face)

    def _vert(self, co):
        self.vert_list.append(tuple(co))
        return len(self.vert_list) - 1  # the index stands in for the BMVert

    def _face(self, vs):
        self.face_list.append(tuple(vs))


def build(fn, *args):
    bm = FakeBm()
    fn(bm, *args)
    return bm


def test_uvsphere_counts_poles_and_determinism():
    a = build(op_add._uvsphere, 20, 10, 1.0)
    assert (len(a.vert_list), len(a.face_list)) == (182, 200)
    assert a.vert_list[0] == (0.0, 0.0, 1.0) and a.vert_list[-1] == (0.0, 0.0, -1.0)
    assert a.vert_list[1] == pytest.approx((math.sin(math.pi / 10), 0.0, math.cos(math.pi / 10)))  # a ring starts at +X
    b = build(op_add._uvsphere, 20, 10, 1.0)
    assert a.vert_list == b.vert_list and a.face_list == b.face_list


def test_uvsphere_faces_are_fans_then_quads_then_fans():
    bm = build(op_add._uvsphere, 20, 10, 2.0)
    assert bm.face_list[0] == (0, 1, 2) and bm.face_list[19] == (0, 20, 1)  # the top fan wraps
    assert bm.face_list[20] == (1, 21, 22, 2)  # first quad of the first band
    assert bm.face_list[-1] == (180, 181, 161)  # the bottom fan ends at the south pole and wraps
    assert [len(f) for f in bm.face_list] == [3] * 20 + [4] * 160 + [3] * 20
    assert max(max(f) for f in bm.face_list) == 181
    assert max(abs(c) for co in bm.vert_list for c in co) == pytest.approx(2.0)


def test_uvsphere_smallest_still_closes():
    bm = build(op_add._uvsphere, 3, 2, 1.0)
    assert (len(bm.vert_list), len(bm.face_list)) == (5, 6)


def test_torus_counts_first_vertex_and_determinism():
    a = build(op_add._torus, 48, 12, 1.0, 0.25)
    assert (len(a.vert_list), len(a.face_list)) == (576, 576)
    assert a.vert_list[0] == (1.25, 0.0, 0.0)
    assert a.vert_list[6] == pytest.approx((0.75, 0.0, 0.0), abs=1e-12)  # the inside of the tube
    assert all(len(f) == 4 for f in a.face_list) and a.face_list[0] == (0, 12, 13, 1)
    assert a.face_list[-1] == (575, 11, 0, 564)  # wraps around the ring and around the tube
    assert max(abs(c) for co in a.vert_list for c in co[:2]) == pytest.approx(1.25)
    b = build(op_add._torus, 48, 12, 1.0, 0.25)
    assert a.vert_list == b.vert_list and a.face_list == b.face_list


class StubBmesh(types.ModuleType):
    def __init__(self):
        super().__init__("bmesh")
        self.calls = []
        self.ops = types.SimpleNamespace(
            create_cube=lambda bm, **kw: self.calls.append(("create_cube", kw)),
            scale=lambda bm, **kw: self.calls.append(("scale", kw)))


def test_cube_with_dimensions_scales_a_unit_cube(monkeypatch):
    stub = StubBmesh()
    monkeypatch.setitem(sys.modules, "bmesh", stub)
    op_add._fill(types.SimpleNamespace(verts=["v"]), "cube", {"dimensions": [14, 4.4, 0.5], "size": None})
    assert stub.calls == [("create_cube", {"size": 1.0}), ("scale", {"vec": (14.0, 4.4, 0.5), "verts": ["v"]})]


def test_cube_without_dimensions_is_unchanged(monkeypatch):
    stub = StubBmesh()
    monkeypatch.setitem(sys.modules, "bmesh", stub)
    op_add._fill(types.SimpleNamespace(verts=[]), "cube", {"size": 3})
    assert stub.calls == [("create_cube", {"size": 3.0})]


def test_fill_torus_and_sphere_defaults(monkeypatch):
    monkeypatch.setitem(sys.modules, "bmesh", StubBmesh())
    t = FakeBm()
    op_add._fill(t, "torus", {})
    assert (len(t.vert_list), len(t.face_list)) == (576, 576)
    s = FakeBm()
    op_add._fill(s, "sphere", {})
    assert (len(s.vert_list), len(s.face_list)) == (482, 512)  # 32 segments, 16 rings: Blender's own defaults
    small = FakeBm()
    op_add._fill(small, "sphere", {"segments": 3})
    assert (len(small.vert_list), len(small.face_list)) == (5, 6)  # two rings
    custom = FakeBm()
    op_add._fill(custom, "torus", {"segments": 8, "minor_segments": 4, "radius": 2, "minor_radius": 0.5})
    assert (len(custom.vert_list), len(custom.face_list)) == (32, 32) and custom.vert_list[0] == (2.5, 0.0, 0.0)
