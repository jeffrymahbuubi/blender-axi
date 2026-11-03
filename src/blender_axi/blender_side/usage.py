"""Which objects other objects' modifiers point at (boolean cutters, mirror/array offset objects, ...).

`modifier_users(bpy)` walks every modifier's RNA once; `used_by(bpy, obj)` is the lookup for one object.
Scope: modifier pointer properties whose type is Object. Constraints, drivers and collection operands are out.
"""


def object_pointers(mod) -> list[str]:
    """Identifiers of the modifier properties that are pointers to an Object."""
    out = []
    props = getattr(getattr(mod, "bl_rna", None), "properties", None) or []
    for prop in props:
        if getattr(prop, "type", None) != "POINTER":
            continue
        if getattr(getattr(prop, "fixed_type", None), "identifier", None) == "Object":
            out.append(prop.identifier)
    return out


def modifier_users(bpy) -> dict[str, list[str]]:
    """Object name -> ["Owner (Modifier)", ...]; owners sorted by name, then modifier order, each entry once."""
    users: dict[str, list[str]] = {}
    for owner in sorted(bpy.data.objects, key=lambda o: o.name):
        for mod in owner.modifiers:
            label = f"{owner.name} ({mod.name})"
            for pid in object_pointers(mod):
                try:
                    target = getattr(mod, pid)
                except Exception:  # a pointer that cannot be read in this context is simply skipped
                    continue
                if target is None:
                    continue
                entry = users.setdefault(target.name, [])
                if label not in entry:
                    entry.append(label)
    return users


def used_by(bpy, obj) -> list[str]:
    return modifier_users(bpy).get(obj.name, [])
