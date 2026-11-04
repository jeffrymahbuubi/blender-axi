"""`material`: give an object a Principled BSDF material (data API, no operators, so no context is needed).

params: object, name, color[4]|None, metallic|None, roughness|None, emission[3]|None, emission_strength|None,
slot ("replace" = slot 0, "add" = append). The material named `name` is reused when it exists, else created; only
the values that are given are written. Colour, metallic and roughness are also written to the material's viewport
fields (`diffuse_color`, `metallic`, `roughness`), which is what WORKBENCH previews and the solid viewport show.
`object` may be a glob (`*`, `?`, `[`; `fnmatch.fnmatchcase` over sorted object names): the material then goes to every
match. `alpha` (float | None) is written to the Principled `Alpha` input (glTF `alphaMode: BLEND` below 1).
Result: {object (first), objects[names], material, created, color[4], metallic, roughness, slots[names of the first
object]}, plus `alpha` when given and `pattern` when `object` was a glob.
"""

import difflib
import fnmatch

from .common import count
from .ops import OpError

PRINCIPLED = "BSDF_PRINCIPLED"
PATTERN_CHARS = "*?["


def is_pattern(name) -> bool:
    """True when an object positional is a glob (`Region*`) rather than one object name."""
    return any(c in str(name) for c in PATTERN_CHARS)


def _vec(values) -> list:
    return [round(float(x), 3) + 0.0 for x in values]


def _num(value) -> float:
    return round(float(value), 3) + 0.0


def _find(collection, name):
    for item in collection:
        if item.name == name:
            return item
    return None


def _object(bpy, name):
    obj = _find(bpy.data.objects, name)
    if obj is None:
        names = [o.name for o in bpy.data.objects]
        close = difflib.get_close_matches(str(name), names, n=3, cutoff=0.5)
        hint = f" (did you mean: {', '.join(close)}?)" if close else f" (the file has {count(len(names), 'object')})"
        raise OpError("NOT_FOUND", f"object '{name}' not found{hint}")
    return obj


def _targets(bpy, name) -> list:
    """The one named object, or every object a glob matches (sorted by name); NOT_FOUND when nothing matches."""
    if not is_pattern(name):
        return [_object(bpy, name)]
    found = sorted((o for o in bpy.data.objects if fnmatch.fnmatchcase(o.name, str(name))), key=lambda o: o.name)
    if not found:
        raise OpError("NOT_FOUND", f"no object matches '{name}'")
    return found


def _principled(mat):
    """The first Principled BSDF node of `mat`, building the default node tree first when it has none."""
    if getattr(mat, "node_tree", None) is None:
        mat.use_nodes = True  # deprecated in 5.x but still what builds the tree; new materials already have one
    tree = getattr(mat, "node_tree", None)
    node = next((n for n in tree.nodes if n.type == PRINCIPLED), None) if tree is not None else None
    if node is None:
        raise OpError("OP_FAILED", f"material '{mat.name}' has no Principled BSDF node to edit")
    return node


def _socket(bsdf, mat, name):
    try:
        return bsdf.inputs[name]
    except (KeyError, TypeError):
        raise OpError("OP_FAILED", f"material '{mat.name}' has no '{name}' input on its Principled BSDF") from None


def _slot_names(obj) -> list:
    return [m.name for m in obj.data.materials if m is not None]


def _assign(obj, mat, slot: str) -> None:
    materials = obj.data.materials
    if slot == "add":
        if all(m is not mat for m in materials):
            materials.append(mat)
    elif len(materials) == 0:
        materials.append(mat)
    else:
        materials[0] = mat


def run(params: dict) -> dict:
    import bpy

    targets = _targets(bpy, params.get("object"))
    for obj in targets:
        if getattr(obj.data, "materials", None) is None:
            raise OpError("OP_FAILED", f"'{obj.name}' ({obj.type}) cannot take a material")
    alpha = params.get("alpha")
    name = params.get("name")
    slot = params.get("slot") or "replace"
    mat = _find(bpy.data.materials, name)
    created = mat is None
    if created:
        mat = bpy.data.materials.new(name)
    try:
        bsdf = _principled(mat)
        for socket in ("Base Color", "Metallic", "Roughness", "Emission Color", "Emission Strength",
                       *(("Alpha",) if alpha is not None else ())):
            _socket(bsdf, mat, socket)
    except OpError:
        if created:
            bpy.data.materials.remove(mat)
        raise

    color, metallic, roughness = params.get("color"), params.get("metallic"), params.get("roughness")
    if color is not None:
        rgba = tuple(float(x) for x in color)
        bsdf.inputs["Base Color"].default_value = rgba
        mat.diffuse_color = rgba
    if metallic is not None:
        bsdf.inputs["Metallic"].default_value = mat.metallic = float(metallic)
    if roughness is not None:
        bsdf.inputs["Roughness"].default_value = mat.roughness = float(roughness)
    if params.get("emission") is not None:
        bsdf.inputs["Emission Color"].default_value = (*(float(x) for x in params["emission"]), 1.0)
    if params.get("emission_strength") is not None:
        bsdf.inputs["Emission Strength"].default_value = float(params["emission_strength"])
    if alpha is not None:
        bsdf.inputs["Alpha"].default_value = float(alpha)
    for obj in targets:
        _assign(obj, mat, slot)
    first = targets[0]
    result = {
        "object": first.name, "objects": [o.name for o in targets], "material": mat.name, "created": created,
        "color": _vec(bsdf.inputs["Base Color"].default_value),
        "metallic": _num(bsdf.inputs["Metallic"].default_value),
        "roughness": _num(bsdf.inputs["Roughness"].default_value),
        "slots": _slot_names(first),
    }
    if alpha is not None:
        result["alpha"] = _num(bsdf.inputs["Alpha"].default_value)
    if is_pattern(params.get("object")):
        result["pattern"] = str(params["object"])
    return result
