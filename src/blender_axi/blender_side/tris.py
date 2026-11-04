"""Triangle count of one object: the evaluated mesh (modifiers applied) when a depsgraph is given, else the raw mesh."""
MESH_LIKE = {"MESH", "CURVE", "SURFACE", "META", "FONT"}


def triangles(obj, depsgraph=None) -> int | None:
    """None for objects without faces; `sum(len(polygon) - 2)` == `len(loop_triangles)` (plan 0.10.0 F8)."""
    if depsgraph is None:
        return sum(max(len(p.vertices) - 2, 0) for p in obj.data.polygons) if obj.type == "MESH" else None
    if obj.type not in MESH_LIKE:
        return None
    ev = obj.evaluated_get(depsgraph)
    try:
        mesh = ev.to_mesh()
    except RuntimeError:
        return None
    try:
        return sum(max(len(p.vertices) - 2, 0) for p in mesh.polygons)
    finally:
        ev.to_mesh_clear()
