"""Lazy command registry: name -> "module:attr" of a CommandSpec.

Units add one line each (plan section 6). Nothing here is imported until dispatch.
"""

COMMANDS: dict[str, str] = {
    "scene": "blender_axi.commands.scene:SPEC",
    "new": "blender_axi.commands.new:SPEC",
    "add": "blender_axi.commands.add:SPEC",
    "set": "blender_axi.commands.set_cmd:SPEC",
    "delete": "blender_axi.commands.delete:SPEC",
    "material": "blender_axi.commands.material:SPEC",
    "modifier": "blender_axi.commands.modifier:SPEC",
    "configure": "blender_axi.commands.configure:SPEC",
    "import": "blender_axi.commands.import_cmd:SPEC",
    "apply": "blender_axi.commands.apply:SPEC",
    "objects": "blender_axi.commands.objects:SPEC",
    "object": "blender_axi.commands.objects:OBJECT_SPEC",
    "render": "blender_axi.commands.render:SPEC",
    "export": "blender_axi.commands.export:SPEC",
    "exec": "blender_axi.commands.exec_cmd:SPEC",
    "skill": "blender_axi.commands.skill:SPEC",
    "live": "blender_axi.commands.live:SPEC",
}

# command -> "module:BATCH"; filled by the lead after the v1.3 merges (plan §6)
BATCH: dict[str, str] = {
    "add": "blender_axi.commands.add:BATCH",
    "set": "blender_axi.commands.set_cmd:BATCH",
    "delete": "blender_axi.commands.delete:BATCH",
    "material": "blender_axi.commands.material:BATCH",
    "modifier": "blender_axi.commands.modifier:BATCH",
    "configure": "blender_axi.commands.configure:BATCH",
    "import": "blender_axi.commands.import_cmd:BATCH",
}

HOME = "blender_axi.commands.home:SPEC"
