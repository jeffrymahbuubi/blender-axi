"""Helpers shared by the ops (data API only; `bpy` is passed in so unit tests can use a fake)."""

from .ops import OpError

SCENE_COLLECTION = "Scene Collection"


def find_collection(bpy, scene, wanted):
    """The collection to link to / move into: the active one for no name, else by name (NOT_FOUND when missing)."""
    if not wanted:
        coll = getattr(bpy.context, "collection", None)
        return coll if coll is not None else scene.collection
    if wanted == SCENE_COLLECTION or scene.collection.name == wanted:
        return scene.collection
    for coll in bpy.data.collections:
        if coll.name == wanted:
            return coll
    names = [c.name for c in bpy.data.collections]
    raise OpError("NOT_FOUND", f"collection '{wanted}' not found"
                  + (f" (available: {', '.join(names)})" if names else " (the file has no collections)"))


def count(n: int, noun: str) -> str:
    """`1 object`, `0 objects`, `4 objects` (adds 's' unless n == 1)."""
    return f"{n} {noun}{'' if n == 1 else 's'}"
