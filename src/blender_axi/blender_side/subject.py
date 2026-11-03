"""The subject of a render: which visible geometry a turntable orbits and `--fit` frames (0.7.0 plan K1).

Auto mode drops a *ground-like* object: a flat one (world bounding box thinner than `GROUND_FLAT` of its widest
side) that is at least `GROUND_FACTOR` times wider than the bounding box of everything else. If that would drop
everything, nothing is dropped. `--subject a,b` names the objects instead.

Pure Python (no `bpy`): the op feeds it per-object world-space bound-box corners (`camera_fit.collect_entries`).
"""

GROUND_FLAT = 0.02  # min/max world-bbox dimension ratio at or below which an object counts as flat
GROUND_FACTOR = 1.5  # a flat object at least this many times wider than everything else's bbox is ground

Entry = tuple[str, list[float]]  # (object name, flat world-space bound-box corners x, y, z, ...)


def bbox(coords: list[float]) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """(mins, maxs) of flat x, y, z, ... coordinates."""
    axes = (coords[0::3], coords[1::3], coords[2::3])
    return tuple(min(a) for a in axes), tuple(max(a) for a in axes)


def dims(coords: list[float]) -> tuple[float, float, float]:
    lo, hi = bbox(coords)
    return tuple(h - l for l, h in zip(lo, hi))


def is_flat(coords: list[float]) -> bool:
    """True when the thinnest side is at most `GROUND_FLAT` of the widest (a zero-size object is not flat)."""
    d = dims(coords)
    return max(d) > 0 and min(d) <= GROUND_FLAT * max(d)


def coords_of(entries: list[Entry]) -> list[float]:
    out: list[float] = []
    for _, coords in entries:
        out.extend(coords)
    return out


def choose(entries: list[Entry], names: list[str] | None) -> dict:
    """`{"entries": kept, "excluded": [names], "mode": "named" | "auto" | "all"}`."""
    if names:
        by_name = dict(entries)
        return {"entries": [(n, by_name[n]) for n in names if n in by_name], "excluded": [], "mode": "named"}
    excluded: list[str] = []
    for name, coords in entries:
        if not is_flat(coords):
            continue
        rest = coords_of([e for e in entries if e[0] != name])
        if rest and max(dims(coords)) >= GROUND_FACTOR * max(dims(rest)):
            excluded.append(name)
    if not excluded or len(excluded) == len(entries):
        return {"entries": entries, "excluded": [], "mode": "all"}
    return {"entries": [e for e in entries if e[0] not in excluded], "excluded": excluded, "mode": "auto"}
