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


def obj(name, *mods):
    return NS(name=name, modifiers=list(mods))


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
