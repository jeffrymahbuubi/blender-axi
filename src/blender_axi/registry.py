"""Lazy command registry: name -> "module:attr" of a CommandSpec.

Units add one line each (plan section 6). Nothing here is imported until dispatch.
"""

COMMANDS: dict[str, str] = {
    "scene": "blender_axi.commands.scene:SPEC",
    "objects": "blender_axi.commands.objects:SPEC",
    "object": "blender_axi.commands.objects:OBJECT_SPEC",
    "render": "blender_axi.commands.render:SPEC",
    "export": "blender_axi.commands.export:SPEC",
    "exec": "blender_axi.commands.exec_cmd:SPEC",
    "skill": "blender_axi.commands.skill:SPEC",
    "live": "blender_axi.commands.live:SPEC",
}

HOME = "blender_axi.commands.home:SPEC"
