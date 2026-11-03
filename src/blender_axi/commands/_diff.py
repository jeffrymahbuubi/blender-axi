"""Pure-Python comparison of two `snapshot` results (no Blender, no I/O); used by `diff`."""

from __future__ import annotations

from ._common import fmt_num, fmt_vec

TOL = 1e-3
MAX_VALUE_CHARS = 40
# (label, snapshot key, kind) in the order the changes are reported
FIELDS = (
    ("type", "type", "text"), ("parent", "parent", "text"), ("collections", "collections", "list"),
    ("location", "location", "vec"), ("rotation", "rotation_deg", "vec"), ("scale", "scale", "vec"),
    ("dimensions", "dimensions", "vec"), ("visible", "visible", "text"), ("hide_render", "hide_render", "text"),
    ("data", "data", "text"), ("verts", "verts", "text"), ("faces", "faces", "text"), ("materials", "materials", "list"),
)


def _eq(x, y, tol: float) -> bool:
    if isinstance(x, bool) or isinstance(y, bool) or x is None or y is None:
        return x == y
    if isinstance(x, (int, float)) and isinstance(y, (int, float)):
        return abs(x - y) <= tol
    if isinstance(x, (list, tuple)) and isinstance(y, (list, tuple)):
        return len(x) == len(y) and all(_eq(p, q, tol) for p, q in zip(x, y))
    if isinstance(x, dict) and isinstance(y, dict):
        return x.keys() == y.keys() and all(_eq(x[k], y[k], tol) for k in x)
    return x == y


def _text(v) -> str:
    """One value as report text: `none`, `true`/`false`, 3 dp numbers, `A|B` for string lists, `1,2,3` for numbers."""
    if v is None or v == [] or v == "":
        return "none"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return fmt_num(v)
    if isinstance(v, (list, tuple)):
        if all(isinstance(i, (int, float)) and not isinstance(i, bool) for i in v):
            return fmt_vec(v)
        return "|".join(_text(i) for i in v)
    if isinstance(v, dict):
        return ", ".join(f"{k}={_text(x)}" for k, x in v.items())
    s = str(v)
    return s if len(s) <= MAX_VALUE_CHARS else s[:MAX_VALUE_CHARS - 3] + "..."


def _arrow(label: str, old, new, kind: str) -> str:
    if kind == "vec":
        return f"{label} {fmt_vec(old)} -> {fmt_vec(new)}"
    return f"{label} {_text(old)} -> {_text(new)}"


def _modifier_changes(a: list, b: list, tol: float) -> list[str]:
    ma, mb = {m["name"]: m for m in a}, {m["name"]: m for m in b}
    out = [f"-{m['name']}({m['type']})" for m in a if m["name"] not in mb]
    out += [f"+{m['name']}({m['type']})" for m in b if m["name"] not in ma]
    for name in (m["name"] for m in a if m["name"] in mb):
        old, new = ma[name], mb[name]
        if old["type"] != new["type"]:
            out.append(f"{name}.type {old['type']} -> {new['type']}")
        sa, sb = old.get("settings") or {}, new.get("settings") or {}
        for key in [*sa, *(k for k in sb if k not in sa)]:
            if not _eq(sa.get(key), sb.get(key), tol):
                out.append(f"{name}.{key} {_text(sa.get(key))} -> {_text(sb.get(key))}")
    common_a = [m["name"] for m in a if m["name"] in mb]
    common_b = [m["name"] for m in b if m["name"] in ma]
    if common_a != common_b:
        out.append("modifiers reordered")
    return out


def _prop_changes(a: dict, b: dict, tol: float) -> list[str]:
    out = []
    for key in [*a, *(k for k in b if k not in a)]:
        if not _eq(a.get(key), b.get(key), tol):
            out.append(f"custom_props.{key} {_text(a.get(key))} -> {_text(b.get(key))}")
    return out


def object_changes(a: dict, b: dict, tol: float = TOL) -> list[str]:
    out: list[str] = []
    for label, key, kind in FIELDS:
        if not _eq(a.get(key), b.get(key), tol):
            out.append(_arrow(label, a.get(key), b.get(key), kind))
    out += _modifier_changes(a.get("modifiers") or [], b.get("modifiers") or [], tol)
    out += _prop_changes(a.get("custom_props") or {}, b.get("custom_props") or {}, tol)
    return out


def _engine(raw) -> str:
    text = str(raw or "").replace("BLENDER_", "")
    return "EEVEE" if text.startswith("EEVEE") else text


def _scene_values(s: dict) -> dict:
    w, h, pct = s["resolution"]
    return {
        "engine": _engine(s.get("engine")),
        "resolution": f"{w}x{h}@{pct}%",
        "frames": f"{s['frames'][0]}-{s['frames'][1]}",
        "fps": s["fps"],
        "camera": s.get("camera") or "none",
        "world": s.get("world") or "none",
    }


def scene_changes(a: dict, b: dict, tol: float = TOL) -> list[dict]:
    va, vb = _scene_values(a), _scene_values(b)
    return [{"setting": k, "from": _text(va[k]), "to": _text(vb[k])} for k in va if not _eq(va[k], vb[k], tol)]


def compare(a: dict, b: dict, tol: float = TOL) -> dict:
    """{"added": [{name,type}], "removed": [{name,type}], "changed": [{name, changes}], "scene": [{setting,from,to}],
    "unchanged": int}; objects are matched by name only (no rename detection)."""
    oa, ob = {o["name"]: o for o in a["objects"]}, {o["name"]: o for o in b["objects"]}
    changed, unchanged = [], 0
    for name, old in oa.items():
        if name in ob:
            items = object_changes(old, ob[name], tol)
            if items:
                changed.append({"name": name, "changes": "; ".join(items)})
            else:
                unchanged += 1
    return {
        "added": [{"name": n, "type": o["type"]} for n, o in ob.items() if n not in oa],
        "removed": [{"name": n, "type": o["type"]} for n, o in oa.items() if n not in ob],
        "changed": changed,
        "scene": scene_changes(a["scene"], b["scene"], tol),
        "unchanged": unchanged,
    }


def restrict(a: dict, b: dict, objects: list[str] | None, collection: str | None) -> tuple[dict, dict, list[str]]:
    """Keep only the named objects and/or the objects in `collection` in either file (a name kept in one file is kept
    in both, so a move out of the collection shows as a change). Returns (a, b, names missing from both files)."""
    if not objects and not collection:
        return a, b, []
    names = {o["name"] for s in (a, b) for o in s["objects"]}
    keep = set(names)
    if collection:
        keep = {o["name"] for s in (a, b) for o in s["objects"] if collection in o["collections"]}
    missing: list[str] = []
    if objects:
        missing = [n for n in objects if n not in names]
        keep &= set(objects)
    def pick(snap: dict) -> dict:
        return {**snap, "objects": [o for o in snap["objects"] if o["name"] in keep]}

    return pick(a), pick(b), missing
