"""Real-Blender `used_by` checks: a CLI-built users.blend (0.9.0 U1) plus one `exec` step (0.9.0 U2), no fake bpy.

U1 pins the strings Blender prints for modifier Object pointers (BOOLEAN, MIRROR, ARRAY). U2 adds object constraints
(Track To, Child Of, Copy Location, Armature targets) and a collection instancer on top of the same file.
"""

from __future__ import annotations

import json
import shutil

import pytest

pytestmark = pytest.mark.blender

USERS_STEPS = ["add cube --name Ring --size 2", "add cube --name Hole --size 1", "add empty --name Anchor",
               "add cube --name Cap --size 0.5", "add cube --name Wall --location 3,0,0",
               "add cube --name Arch --location -3,0,0", "add camera --name Cam --location 0,-8,4",
               "add empty --name Inst", "modifier Ring boolean --cutter Hole", "modifier Wall mirror --object Anchor",
               "modifier Arch array --count 3 --prop offset_object=Anchor,start_cap=Cap,end_cap=Cap",
               "set Hole --hide"]
# Constraints, a collection instancer (Cap moves into Parts) and an armature object; result echoes the constraint names.
USERS_EXEC = """
sc, objs = bpy.context.scene, bpy.data.objects
objs["Cam"].constraints.new("TRACK_TO").target = objs["Anchor"]
objs["Wall"].constraints.new("CHILD_OF").target = objs["Anchor"]
objs["Arch"].constraints.new("COPY_LOCATION").target = objs["Ring"]
parts = bpy.data.collections.new("Parts")
sc.collection.children.link(parts)
for c in list(objs["Cap"].users_collection):
    c.objects.unlink(objs["Cap"])
parts.objects.link(objs["Cap"])
objs["Inst"].instance_type = "COLLECTION"
objs["Inst"].instance_collection = parts
rig = bpy.data.objects.new("Rig", bpy.data.armatures.new("Rig"))
sc.collection.objects.link(rig)
objs["Wall"].constraints.new("ARMATURE").targets.new().target = rig
result = {n: [c.name for c in objs[n].constraints] for n in ("Cam", "Wall", "Arch")}
"""
MODIFIER_ANCHOR = "Arch (Array), Wall (Mirror)"  # U1: the modifier part, unchanged by U2
ANCHOR = "Arch (Array), Cam (Track To), Wall (Mirror), Wall (Child Of)"  # modifiers, then constraints, owners by name
CAP = "Arch (Array), Inst (instance of Parts)"


def axi_json(capsys, *argv):
    from blender_axi import cli

    code, out = cli.main(["--json", *map(str, argv)]), capsys.readouterr()
    assert out.err == ""
    return code, json.loads(out.out)


@pytest.fixture(scope="module")
def users(blender_info):
    from blender_axi import cli, paths

    work = paths.tmp_root() / "usage"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    blend, steps = work / "users.blend", work / "users.json"
    steps.write_text(json.dumps(USERS_STEPS), encoding="utf-8")
    assert cli.main(["--json", "new", str(blend), "--force"]) == 0
    assert cli.main(["--json", "apply", str(steps), str(blend), "--in-place"]) == 0
    assert cli.main(["--json", "exec", str(blend), "--allow-exec", "--in-place", "--code", USERS_EXEC]) == 0
    return blend


def test_exec_step_created_the_default_constraint_names(capsys, users):
    # A2: the names the used_by labels below rely on, as Blender 5.2 reports them
    from blender_axi import cli

    assert cli.main(["--json", "exec", str(users), "--allow-exec", "--code",
                     "result = {n: [c.name for c in bpy.data.objects[n].constraints] for n in ('Cam', 'Wall', 'Arch')}"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["result"] == {"Cam": ["Track To"], "Wall": ["Child Of", "Armature"], "Arch": ["Copy Location"]}


def test_objects_used_by_names_modifier_constraint_and_instance_users(capsys, users):
    code, data = axi_json(capsys, "objects", users, "--fields", "used_by", "--limit", "50")
    assert code == 0
    by = {o["name"]: o for o in data["objects"]}
    assert by["Anchor"]["used_by"] == ANCHOR  # offset_object, mirror_object, then Track To / Child Of targets
    assert by["Ring"]["used_by"] == "Arch (Copy Location)"
    assert by["Cap"]["used_by"] == CAP and by["Cap"]["collection"] == "Parts"  # A1: moved into Parts
    assert by["Rig"]["used_by"] == "Wall (Armature)"  # ARMATURE targets[0].target
    assert by["Hole"]["used_by"] == "Ring (Boolean)" and by["Hole"]["visible"] is False
    for name in ("Wall", "Arch", "Cam", "Inst"):
        assert by[name]["used_by"] == "none", name


def test_modifier_part_is_still_there_in_order(capsys, users):
    _, data = axi_json(capsys, "objects", users, "--fields", "used_by", "--limit", "50")
    by = {o["name"]: o["used_by"] for o in data["objects"]}
    modifiers = [e for e in by["Anchor"].split(", ") if e in MODIFIER_ANCHOR.split(", ")]
    assert modifiers == MODIFIER_ANCHOR.split(", ")
    assert by["Cap"].split(", ")[0] == "Arch (Array)" and by["Hole"] == "Ring (Boolean)"


def test_object_shows_used_by_after_children(capsys, users):
    _, anchor = axi_json(capsys, "object", "Anchor", users)
    _, cap = axi_json(capsys, "object", "Cap", users)
    assert anchor["used_by"] == ANCHOR and cap["used_by"] == CAP
    keys = list(anchor)
    assert keys.index("used_by") == keys.index("children") + 1


def test_objects_default_rows_have_no_used_by(capsys, users):
    code, data = axi_json(capsys, "objects", users, "--limit", "50")
    assert code == 0
    assert all(set(o) == {"name", "type", "collection", "visible"} for o in data["objects"])
