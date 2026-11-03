"""Unit tests for blender_side/usage.py (fake bpy objects with RNA-like modifier properties)."""

from __future__ import annotations

from types import SimpleNamespace as NS

from blender_axi.blender_side import usage

OBJECT_STRUCT = NS(identifier="Object", base=NS(identifier="ID", base=None))
COLLECTION_STRUCT = NS(identifier="Collection", base=NS(identifier="ID", base=None))


def prop(identifier, kind, fixed=None):
    return NS(identifier=identifier, type=kind, fixed_type=fixed)


def modifier(name, **pointers):
    """A modifier with an INT, a Collection pointer and the given Object pointers (name -> object or None)."""
    props = [prop("segments", "INT"), prop("collection", "POINTER", COLLECTION_STRUCT)]
    props += [prop(pid, "POINTER", OBJECT_STRUCT) for pid in pointers]
    mod = NS(name=name, bl_rna=NS(properties=props), segments=2, collection=NS(name="Props"))
    for pid, target in pointers.items():
        setattr(mod, pid, target)
    return mod


def constraint(name, targets=None, **pointers):
    """A constraint with a FLOAT property and the given Object pointers; `targets` = ARMATURE target items."""
    props = [prop("influence", "FLOAT")] + [prop(pid, "POINTER", OBJECT_STRUCT) for pid in pointers]
    con = NS(name=name, bl_rna=NS(properties=props), influence=1.0, **pointers)
    if targets is not None:
        con.targets = [NS(target=t) for t in targets]
    return con


def obj(name, *mods, **extra):
    """`extra` may carry constraints=[...], instance_type=..., instance_collection=...; absent = attribute missing."""
    return NS(name=name, modifiers=list(mods), **extra)


def bpy_with(*objects):
    return NS(data=NS(objects=list(objects)))


def test_object_pointers_keeps_object_pointers_only():
    mod = modifier("Boolean", object=None, operand=None)
    assert usage.object_pointers(mod) == ["object", "operand"]


def test_object_pointers_without_rna_is_empty():
    assert usage.object_pointers(NS(name="Plain", type="BEVEL")) == []


def test_modifier_users_groups_ring_boolean_under_hole():
    hole = obj("Hole")
    ring = obj("Ring", modifier("Boolean", object=hole))
    users = usage.modifier_users(bpy_with(ring, hole))
    assert users == {"Hole": ["Ring (Boolean)"]}


def test_modifier_users_two_owners_sorted_by_name_then_modifier_order():
    anchor = obj("Anchor")
    wall = obj("Wall", modifier("Mirror", mirror_object=anchor))
    arch = obj("Arch", modifier("Array", offset_object=anchor), modifier("Mirror", mirror_object=anchor))
    users = usage.modifier_users(bpy_with(wall, anchor, arch))
    assert users == {"Anchor": ["Arch (Array)", "Arch (Mirror)", "Wall (Mirror)"]}


def test_modifier_users_two_pointers_of_one_modifier_listed_once():
    hole = obj("Hole")
    ring = obj("Ring", modifier("Boolean", object=hole, other=hole))
    assert usage.modifier_users(bpy_with(ring, hole)) == {"Hole": ["Ring (Boolean)"]}


def test_modifier_users_ignores_empty_pointers_and_non_object_pointers():
    ring = obj("Ring", modifier("Boolean", object=None))
    assert usage.modifier_users(bpy_with(ring)) == {}


def test_modifier_users_skips_unreadable_pointer():
    mod = modifier("Boolean", object=None)

    class Broken(NS):
        @property
        def object(self):
            raise RuntimeError("not readable here")

    broken = Broken(name=mod.name, bl_rna=mod.bl_rna, segments=2, collection=mod.collection)
    assert usage.modifier_users(bpy_with(obj("Ring", broken))) == {}


def test_used_by_of_unreferenced_object_is_empty():
    hole, loner = obj("Hole"), obj("Loner")
    ring = obj("Ring", modifier("Boolean", object=hole))
    bpy = bpy_with(ring, hole, loner)
    assert usage.used_by(bpy, loner) == []
    assert usage.used_by(bpy, hole) == ["Ring (Boolean)"]


# -- users(): modifiers, object constraints, collection instancers -------------------------------------------------

def test_users_constraint_pointers_target_pole_space_and_armature_targets():
    a, b, c, d = obj("A"), obj("B"), obj("C"), obj("D")
    cam = obj("Cam", constraints=[constraint("Track To", target=a), constraint("IK", target=None, pole_target=b),
                                  constraint("Copy Location", space_object=c)])
    wall = obj("Wall", constraints=[constraint("Armature", targets=[d, None])])
    users = usage.users(bpy_with(cam, wall, a, b, c, d))
    assert users == {"A": ["Cam (Track To)"], "B": ["Cam (IK)"], "C": ["Cam (Copy Location)"], "D": ["Wall (Armature)"]}


def test_users_instancer_lists_every_member_including_nested_ones():
    cap, deep = obj("Cap"), obj("Deep")
    parts = NS(name="Parts", all_objects=[cap, deep])
    inst = obj("Inst", instance_type="COLLECTION", instance_collection=parts)
    assert usage.users(bpy_with(inst, cap, deep)) == {"Cap": ["Inst (instance of Parts)"],
                                                      "Deep": ["Inst (instance of Parts)"]}


def test_users_collection_set_but_instance_type_none_lists_nothing():
    cap = obj("Cap")
    inst = obj("Inst", instance_type="NONE", instance_collection=NS(name="Parts", all_objects=[cap]))
    assert usage.users(bpy_with(inst, cap)) == {}


def test_users_modifier_then_constraint_then_instance_in_one_owner():
    anchor = obj("Anchor")
    parts = NS(name="Parts", all_objects=[anchor])
    wall = obj("Wall", modifier("Mirror", mirror_object=anchor), constraints=[constraint("Child Of", target=anchor)],
               instance_type="COLLECTION", instance_collection=parts)
    assert usage.users(bpy_with(wall, anchor)) == {"Anchor": ["Wall (Mirror)", "Wall (Child Of)",
                                                              "Wall (instance of Parts)"]}


def test_users_owners_sorted_by_name_not_by_kind():
    anchor = obj("Anchor")
    cam = obj("Cam", constraints=[constraint("Track To", target=anchor)])
    wall = obj("Wall", modifier("Mirror", mirror_object=anchor))
    arch = obj("Arch", modifier("Array", offset_object=anchor))
    assert usage.users(bpy_with(wall, cam, anchor, arch))["Anchor"] == ["Arch (Array)", "Cam (Track To)", "Wall (Mirror)"]


def test_users_two_pointers_of_one_constraint_listed_once():
    anchor = obj("Anchor")
    cam = obj("Cam", constraints=[constraint("Track To", target=anchor, space_object=anchor)])
    assert usage.users(bpy_with(cam, anchor)) == {"Anchor": ["Cam (Track To)"]}


def test_users_object_without_constraints_or_instance_attributes_works():
    hole = obj("Hole")
    plain = NS(name="Plain", modifiers=[])
    ring = obj("Ring", modifier("Boolean", object=hole))
    assert usage.users(bpy_with(plain, ring, hole)) == {"Hole": ["Ring (Boolean)"]}


def test_users_skips_unreadable_constraint_pointer():
    class Broken(NS):
        @property
        def target(self):
            raise RuntimeError("not readable here")

    con = constraint("Track To", target=None)
    broken = Broken(name=con.name, bl_rna=con.bl_rna, influence=1.0)
    assert usage.users(bpy_with(obj("Cam", constraints=[broken]))) == {}


def test_used_by_returns_the_merged_list():
    anchor = obj("Anchor")
    ring = obj("Ring", modifier("Boolean", object=anchor))
    cam = obj("Cam", constraints=[constraint("Track To", target=anchor)])
    bpy = bpy_with(ring, cam, anchor)
    assert usage.used_by(bpy, anchor) == ["Cam (Track To)", "Ring (Boolean)"]
    assert usage.modifier_users(bpy) == {"Anchor": ["Ring (Boolean)"]}
