"""Benchmark scene "desk": 13 objects (table, mug, two books, floor, two lights, camera). Runs on an empty .blend.

Run with `blender-axi exec <empty.blend> --script benchmarks/scenes/desk.py --out desk.blend --allow-exec`.
Stages in order: model, light + camera, iterate (books, red mug, animation).
"""

import math

import bpy
from mathutils import Vector


def material(name, rgb, rough=0.5, metal=0.0):
    m = bpy.data.materials.new(name)
    bsdf = m.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = rough
    bsdf.inputs["Metallic"].default_value = metal
    return m


def add(obj_op, name, mat, **kw):
    obj_op(**kw)
    o = bpy.context.active_object
    o.name = name
    o.data.materials.append(mat)
    return o


def book(name, rgb, z, rot):
    m = bpy.data.materials.new(name + "Cover")
    m.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (*rgb, 1)
    bpy.ops.mesh.primitive_cube_add(location=(-0.9, 0.1, z), rotation=(0, 0, rot))
    o = bpy.context.active_object
    o.name = name
    o.scale = (0.45, 0.32, 0.06)
    o.data.materials.append(m)


# --- 1. model -------------------------------------------------------------------------------------------------
wood = material("Wood", (0.45, 0.26, 0.12), 0.6)
floor = material("Floor", (0.75, 0.75, 0.72), 0.9)
ceramic = material("Ceramic", (0.10, 0.35, 0.75), 0.25)

add(bpy.ops.mesh.primitive_plane_add, "Floor", floor, size=12)
top = add(bpy.ops.mesh.primitive_cube_add, "TableTop", wood, location=(0, 0, 1.5))
top.scale = (2.0, 1.2, 0.08)
for i, (x, y) in enumerate([(-1.8, -1.0), (1.8, -1.0), (-1.8, 1.0), (1.8, 1.0)]):
    leg = add(bpy.ops.mesh.primitive_cube_add, f"Leg{i+1}", wood, location=(x, y, 0.71))
    leg.scale = (0.08, 0.08, 0.71)
mug = add(bpy.ops.mesh.primitive_cylinder_add, "Mug", ceramic, radius=0.25, depth=0.5,
          vertices=32, location=(0.6, 0.2, 1.83))
bevel = mug.modifiers.new("Bevel", "BEVEL")
bevel.width = 0.03
bevel.segments = 3
handle = add(bpy.ops.mesh.primitive_torus_add, "MugHandle", ceramic, major_radius=0.13,
             minor_radius=0.035, location=(0.88, 0.2, 1.85), rotation=(1.5708, 0, 0))
for o in (mug, handle):
    bpy.ops.object.select_all(action="DESELECT")
    o.select_set(True)
    bpy.context.view_layer.objects.active = o
    bpy.ops.object.shade_smooth()

# --- 2. light + camera ----------------------------------------------------------------------------------------
scene = bpy.context.scene
bpy.ops.object.light_add(type="SUN", location=(4, -4, 8))
sun = bpy.context.active_object
sun.name = "Sun"
sun.data.energy = 3.0
sun.rotation_euler = (0.7, 0.2, 0.6)
bpy.ops.object.light_add(type="AREA", location=(-1.5, -1.0, 3.5))
lamp = bpy.context.active_object
lamp.name = "DeskLamp"
lamp.data.energy = 250
lamp.data.color = (1.0, 0.85, 0.65)
lamp.data.size = 1.0

bpy.ops.object.camera_add(location=(4.2, -4.6, 3.6))
cam = bpy.context.active_object
cam.name = "Camera"
target = bpy.data.objects["Mug"].location
cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()
cam.data.lens = 45
scene.camera = cam

world = scene.world or bpy.data.worlds.new("World")
scene.world = world
world.use_nodes = True
world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.05, 0.06, 0.08, 1)

scene.render.engine = "BLENDER_EEVEE"
scene.render.resolution_x, scene.render.resolution_y = 1280, 720
scene.frame_end = 48

# --- 3. iterate: red mug, two books, wider camera, spinning mug -----------------------------------------------
ceramic.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.75, 0.08, 0.06, 1)
book("BookGreen", (0.10, 0.40, 0.20), 1.64, 0.15)
book("BookYellow", (0.85, 0.65, 0.10), 1.76, -0.10)

cam.location = (6.4, -6.8, 4.6)
cam.rotation_euler = (Vector((0, 0, 1.0)) - cam.location).to_track_quat("-Z", "Y").to_euler()

handle.parent = mug
handle.matrix_parent_inverse = mug.matrix_world.inverted()
for frame, angle in ((1, 0.0), (48, 2 * math.pi)):
    mug.rotation_euler.z = angle
    mug.keyframe_insert("rotation_euler", index=2, frame=frame)
action = mug.animation_data.action
for fc in action.fcurves if hasattr(action, "fcurves") else []:
    for kp in fc.keyframe_points:
        kp.interpolation = "LINEAR"

result = {"objects": len(bpy.data.objects), "collections": len(bpy.data.collections)}
