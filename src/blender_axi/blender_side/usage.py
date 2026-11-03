"""Which objects other objects point at: modifiers, object constraints and collection instancers.

`users(bpy)` walks every owner once; `used_by(bpy, obj)` is the lookup for one object.
Scope: modifier Object pointers (cutter, mirror/offset object, ...), object-constraint Object pointers (`target`,
`pole_target`, `space_object`, `camera`, `depth_object`, ARMATURE `targets[i].target`) and collection instancers
(`instance_type == "COLLECTION"` with a collection set). Drivers, geometry-nodes inputs, pose-bone constraints and
parents are out.
"""


def object_pointers(x) -> list[str]:
    """Identifiers of the properties of a modifier or constraint that are pointers to an Object."""
    out = []
    props = getattr(getattr(x, "bl_rna", None), "properties", None) or []
    for prop in props:
        if getattr(prop, "type", None) != "POINTER":
            continue
        if getattr(getattr(prop, "fixed_type", None), "identifier", None) == "Object":
            out.append(prop.identifier)
    return out


def _targets(x, with_targets: bool = False) -> list:
    """The Objects `x` points at (set and readable pointers only); `with_targets` adds ARMATURE `targets[i].target`."""
    found = []
    for pid in object_pointers(x):
        try:
            target = getattr(x, pid)
        except Exception:  # a pointer that cannot be read in this context is simply skipped
            continue
        if target is not None:
            found.append(target)
    if with_targets:
        for item in getattr(x, "targets", None) or []:
            try:
                target = getattr(item, "target", None)
            except Exception:
                continue
            if target is not None:
                found.append(target)
    return found


def _add(result: dict[str, list[str]], target, label: str) -> None:
    entry = result.setdefault(target.name, [])
    if label not in entry:
        entry.append(label)


def modifier_users(bpy) -> dict[str, list[str]]:
    """Object name -> ["Owner (Modifier)", ...]; owners sorted by name, then modifier order, each entry once."""
    result: dict[str, list[str]] = {}
    for owner in sorted(bpy.data.objects, key=lambda o: o.name):
        for mod in owner.modifiers:
            for target in _targets(mod):
                _add(result, target, f"{owner.name} ({mod.name})")
    return result


def users(bpy) -> dict[str, list[str]]:
    """Object name -> ["Owner (Label)", ...]: owners by name; per owner modifiers, constraints, then the instance."""
    result: dict[str, list[str]] = {}
    for owner in sorted(bpy.data.objects, key=lambda o: o.name):
        for mod in owner.modifiers:
            for target in _targets(mod):
                _add(result, target, f"{owner.name} ({mod.name})")
        for con in getattr(owner, "constraints", None) or []:
            for target in _targets(con, with_targets=True):
                _add(result, target, f"{owner.name} ({con.name})")
        coll = getattr(owner, "instance_collection", None)
        if getattr(owner, "instance_type", None) == "COLLECTION" and coll is not None:
            for member in getattr(coll, "all_objects", None) or []:
                _add(result, member, f"{owner.name} (instance of {coll.name})")
    return result


def used_by(bpy, obj) -> list[str]:
    return users(bpy).get(obj.name, [])
