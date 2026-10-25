"""`modifier`: add, configure or remove one modifier on one object with the data API (no operators, so no context).

params: object, kind (Blender identifier such as BEVEL), name|None (default = Blender's name for the kind), remove,
settings {rna_property: value} (typed values from the CLI; `object`/`mirror_object` are object names), props
{rna_property: str} (the `--prop k=v` escape hatch, coerced from `bl_rna.properties`).
Upsert by name: a modifier of that name is reused (its kind must match), else created. Result: {object, modifier,
type, created, removed, changed: [{field, from, to}], stack: [names]}; `changed` lists only fields whose value really
differed (angles in degrees), so a repeat call changes nothing. Every object referenced is looked up before anything
is created or changed, so a bad cutter name leaves the object untouched.
"""

import math

from .common import find_object
from .ops import OpError

KINDS = {"bevel": "BEVEL", "subdivision": "SUBSURF", "array": "ARRAY", "mirror": "MIRROR", "boolean": "BOOLEAN",
         "solidify": "SOLIDIFY"}
DEFAULT_NAMES = {"BEVEL": "Bevel", "SUBSURF": "Subdivision", "ARRAY": "Array", "MIRROR": "Mirror",
                 "BOOLEAN": "Boolean", "SOLIDIFY": "Solidify"}
POINTER_LABELS = {"object": "cutter", "mirror_object": "mirror object"}  # settings that hold an object name
DEGREE_FIELDS = ("angle_limit",)  # stored in radians, reported in degrees
TRUE = ("true", "1", "yes", "on")
FALSE = ("false", "0", "no", "off")


def default_name(kind: str) -> str:
    return DEFAULT_NAMES.get(kind) or "".join(word.capitalize() for word in kind.split("_"))


def _norm(field: str, value):
    """A comparable, JSON-safe value: floats rounded, vectors as lists, ID pointers by name, degrees for angles."""
    if isinstance(value, (bool, str)) or value is None:
        return value
    if isinstance(value, (int, float)):
        number = math.degrees(value) if field in DEGREE_FIELDS else value
        return round(float(number), 4) + 0.0 if isinstance(value, float) else value
    if hasattr(value, "name") and not hasattr(value, "__iter__"):
        return value.name
    if isinstance(value, (set, frozenset)):
        return sorted(str(v) for v in value)
    try:
        return [_norm(field, v) for v in value]
    except TypeError:
        return getattr(value, "name", str(value))


def _lookup(collection, name, label):
    for item in collection:
        if item.name == name:
            return item
    raise OpError("NOT_FOUND", f"{label} '{name}' not found")


def _boolean(token: str, key: str) -> bool:
    low = token.strip().lower()
    if low in TRUE:
        return True
    if low in FALSE:
        return False
    raise OpError("OP_FAILED", f"invalid value '{token}' for '{key}'; expected true or false")


def _scalar(bpy, prop, key: str, token: str):
    kind = prop.type
    token = token.strip()
    try:
        if kind == "BOOLEAN":
            return _boolean(token, key)
        if kind == "INT":
            return int(token)
        if kind == "FLOAT":
            return float(token)
    except ValueError:
        raise OpError("OP_FAILED", f"invalid value '{token}' for '{key}'; expected "
                      + ("an integer" if kind == "INT" else "a number")) from None
    if kind == "ENUM":
        identifiers = [item.identifier for item in prop.enum_items]
        for identifier in identifiers:
            if token == identifier or token.upper() == identifier:
                return identifier
        raise OpError("OP_FAILED", f"invalid value '{token}' for '{key}'; one of {', '.join(identifiers)}")
    if kind == "STRING":
        return token
    if kind == "POINTER":
        wanted = getattr(getattr(prop, "fixed_type", None), "identifier", "")
        if token.lower() in ("", "none"):
            return None
        if wanted == "Object":
            return find_object(bpy, token, POINTER_LABELS.get(key, key.replace("_", " ")))
        if wanted == "Collection":
            return _lookup(bpy.data.collections, token, "collection")
        raise OpError("OP_FAILED", f"'{key}' ({wanted or 'pointer'}) cannot be set from a name")
    raise OpError("OP_FAILED", f"'{key}' ({kind}) cannot be set with --prop")


def _coerce(bpy, mod, key: str, raw: str):
    prop = mod.bl_rna.properties.get(key)
    if prop is None:
        raise OpError("OP_FAILED", f"unknown property '{key}' for {mod.type}")
    if getattr(prop, "is_readonly", False):
        raise OpError("OP_FAILED", f"property '{key}' of {mod.type} is read-only")
    length = getattr(prop, "array_length", 0) or 0
    if length:
        parts = [p for p in str(raw).split(",") if p.strip()]
        if len(parts) != length:
            raise OpError("OP_FAILED", f"'{key}' expects {length} comma-separated values, got '{raw}'")
        return tuple(_scalar(bpy, prop, key, p) for p in parts)
    return _scalar(bpy, prop, key, str(raw))


def _resolve_settings(bpy, settings: dict) -> dict:
    """Typed settings from the CLI with object names replaced by objects (looked up before anything changes)."""
    out = {}
    for key, value in settings.items():
        if key in POINTER_LABELS and value is not None:
            value = find_object(bpy, value, POINTER_LABELS[key])
        out[key] = tuple(value) if isinstance(value, list) else value
    return out


def _assign(mod, key: str, value) -> None:
    try:
        setattr(mod, key, value)
    except (TypeError, ValueError, AttributeError, RuntimeError) as exc:
        raise OpError("OP_FAILED", f"cannot set '{key}' on {mod.type}: {exc}") from None


def run(params: dict) -> dict:
    import bpy

    kind = str(params.get("kind") or "").strip()
    if not kind:
        raise OpError("OP_FAILED", "modifier kind is empty")
    obj = find_object(bpy, params.get("object"))
    if getattr(obj, "modifiers", None) is None:
        raise OpError("OP_FAILED", f"'{obj.name}' ({obj.type}) cannot take modifiers")
    name = params.get("name") or default_name(kind)
    mod = obj.modifiers.get(name)
    if mod is not None and mod.type != kind:
        raise OpError("OP_FAILED", f"'{name}' is a {mod.type}, not {kind}")

    if params.get("remove"):
        if mod is not None:
            obj.modifiers.remove(mod)
        return {"object": obj.name, "modifier": name, "type": kind, "created": False, "removed": mod is not None,
                "changed": [], "stack": [m.name for m in obj.modifiers]}

    settings = _resolve_settings(bpy, params.get("settings") or {})
    created = mod is None
    if created and kind == "BOOLEAN" and settings.get("object") is None and "object" not in (params.get("props") or {}):
        raise OpError("OP_FAILED", "boolean needs --object <cutter>")
    if created:
        try:
            mod = obj.modifiers.new(name, kind)
        except (TypeError, RuntimeError, ValueError):
            raise OpError("OP_FAILED", f"unknown modifier type '{kind}'") from None
        if mod is None:  # Blender returns None when the object cannot take this modifier
            raise OpError("OP_FAILED", f"'{obj.name}' ({obj.type}) cannot take modifiers")
    try:
        props = {key: _coerce(bpy, mod, key, raw) for key, raw in (params.get("props") or {}).items()}
    except OpError:
        if created:
            obj.modifiers.remove(mod)
        raise
    wanted = {**settings, **props}

    before = {key: _norm(key, getattr(mod, key, None)) for key in wanted}
    try:
        for key, value in wanted.items():
            _assign(mod, key, value)
    except OpError:
        if created:
            obj.modifiers.remove(mod)
        raise
    changed = []
    for key in wanted:
        after = _norm(key, getattr(mod, key, None))
        if after != before[key]:
            changed.append({"field": key, "from": before[key], "to": after})
    return {"object": obj.name, "modifier": mod.name, "type": mod.type, "created": created, "removed": False,
            "changed": changed, "stack": [m.name for m in obj.modifiers]}
