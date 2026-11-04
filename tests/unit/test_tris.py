"""blender_side.tris: triangle counts from fake objects (0.10.0 pre-step P2)."""

from types import SimpleNamespace as NS

from blender_axi.blender_side.tris import triangles


def poly(n):
    return NS(vertices=list(range(n)))


def test_raw_cube_has_12_triangles():
    cube = NS(type="MESH", data=NS(polygons=[poly(4)] * 6))
    assert triangles(cube) == 12


def test_non_mesh_without_depsgraph_is_none():
    assert triangles(NS(type="EMPTY", data=None)) is None
    assert triangles(NS(type="CAMERA", data=NS()), depsgraph=object()) is None


def test_evaluated_mesh_is_counted_and_cleared():
    cleared = []
    ev = NS(to_mesh=lambda: NS(polygons=[poly(3)] * 4 + [poly(5)]), to_mesh_clear=lambda: cleared.append(True))
    obj = NS(type="MESH", evaluated_get=lambda dg: ev)
    assert triangles(obj, depsgraph=object()) == 4 + 3
    assert cleared == [True]
