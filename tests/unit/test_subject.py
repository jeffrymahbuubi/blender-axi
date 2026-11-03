"""`blender_side.subject` (0.7.0 plan U1): the pure ground heuristic and the named subject."""

import pytest

from blender_axi.blender_side import subject


def box(lo, hi):
    """The eight corners of an axis-aligned box as flat x, y, z, ..."""
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    return [v for x in (x0, x1) for y in (y0, y1) for z in (z0, z1) for v in (x, y, z)]


def centred(size, z=0.0, thick=None):
    sx, sy, sz = size
    return box((-sx / 2, -sy / 2, z), (sx / 2, sy / 2, z + (sz if thick is None else thick)))


DESK = [
    ("Floor", centred((12, 12, 0))),
    ("TableTop", centred((4, 2.4, 0.16), z=0.74)),
    ("Leg1", box((1.7, 0.9, 0), (1.9, 1.1, 0.74))),
    ("Leg2", box((-1.9, -1.1, 0), (-1.7, -0.9, 0.74))),
    ("Mug", box((0.2, 0.1, 0.9), (0.5, 0.4, 1.2))),
]


def test_bbox_dims_and_coords_of():
    c = box((0, 0, 0), (4, 2, 1))
    assert subject.bbox(c) == ((0, 0, 0), (4, 2, 1)) and subject.dims(c) == (4, 2, 1)
    assert subject.coords_of([("a", [1, 2, 3]), ("b", [4, 5, 6])]) == [1, 2, 3, 4, 5, 6]
    assert subject.coords_of([]) == []


def test_is_flat_thresholds():
    assert subject.is_flat(centred((12, 12, 0)))
    assert subject.is_flat(centred((10, 10, 0.2)))  # exactly 2 %
    assert not subject.is_flat(centred((10, 10, 0.21)))
    assert not subject.is_flat(centred((4, 2.4, 0.16), z=0.74))  # 4 % : a table top is not ground
    assert not subject.is_flat(box((0, 0, 0), (0, 0, 0)))  # zero dims: max 0 is never flat


def test_desk_excludes_the_floor():
    pick = subject.choose(DESK, None)
    assert pick["mode"] == "auto" and pick["excluded"] == ["Floor"]
    assert [n for n, _ in pick["entries"]] == ["TableTop", "Leg1", "Leg2", "Mug"]


def test_city_without_a_flat_object_keeps_everything():
    city = [(f"B{i}", box((i * 3, 0, 0), (i * 3 + 2, 2, 5 + i))) for i in range(6)]
    pick = subject.choose(city, None)
    assert pick == {"entries": city, "excluded": [], "mode": "all"}


def test_a_lone_plane_is_never_excluded():
    only = [("Plane", centred((20, 20, 0)))]
    assert subject.choose(only, None) == {"entries": only, "excluded": [], "mode": "all"}


def test_two_equal_floors_are_not_excluded():
    pair = [("A", centred((12, 12, 0))), ("B", centred((12, 12, 0), z=3))]
    pick = subject.choose(pair, None)
    assert pick["mode"] == "all" and pick["excluded"] == [] and pick["entries"] == pair


def test_a_flat_object_that_is_not_wide_enough_stays():
    # a 4x4 sheet next to a 3x3 cube: 4 < 1.5 x 3, so it is part of the subject
    scene = [("Sheet", centred((4, 4, 0))), ("Cube", box((5, 0, 0), (8, 3, 3)))]
    assert subject.choose(scene, None)["mode"] == "all"


def test_the_factor_is_measured_against_the_bbox_of_everything_else():
    # the table top (4 wide, 4 % thick) is the widest thing here, but is not flat: it stays
    scene = [("Top", centred((4, 2.4, 0.16))), ("Mug", box((0, 0, 1), (0.3, 0.3, 1.3)))]
    assert subject.choose(scene, None)["excluded"] == []


def test_only_the_widest_of_stacked_flat_objects_is_ground():
    # the rug is flat too, but it is measured against the floor under it (12 < 1.5 x 30): it stays
    scene = [("Floor", centred((30, 30, 0))), ("Rug", centred((12, 12, 0.01), z=0.01)), ("Cube", box((0, 0, 0), (2, 2, 2)))]
    pick = subject.choose(scene, None)
    assert pick["excluded"] == ["Floor"] and [n for n, _ in pick["entries"]] == ["Rug", "Cube"]


def test_flat_big_next_to_flat_small_drops_only_the_big_one():
    scene = [("Big", centred((30, 30, 0))), ("Small", centred((3, 3, 0)))]
    pick = subject.choose(scene, None)
    assert pick["excluded"] == ["Big"] and [n for n, _ in pick["entries"]] == ["Small"]


def test_named_keeps_the_given_order_and_never_excludes():
    pick = subject.choose(DESK, ["Mug", "Floor", "Leg2"])
    assert pick["mode"] == "named" and pick["excluded"] == []
    assert [n for n, _ in pick["entries"]] == ["Mug", "Floor", "Leg2"]


def test_named_skips_unknown_names_and_can_be_empty():
    pick = subject.choose(DESK, ["Mug", "Ghost"])
    assert [n for n, _ in pick["entries"]] == ["Mug"]
    assert subject.choose(DESK, ["Ghost"])["entries"] == []


@pytest.mark.parametrize("names", [None, []])
def test_no_names_means_auto(names):
    assert subject.choose(DESK, names)["mode"] == "auto"


def test_empty_scene():
    assert subject.choose([], None) == {"entries": [], "excluded": [], "mode": "all"}
