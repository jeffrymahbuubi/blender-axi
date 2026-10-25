"""Benchmark scene "city": 502 objects in 21 collections (20 districts of 20 buildings, a park with 50 trees + crowns).

Run with `blender-axi exec <empty.blend> --script benchmarks/scenes/city.py --out city.blend --allow-exec`.
Deterministic: fixed seed, fixed names. Plus one sun and one camera aimed at the city centre.
"""

import random

import bpy
from mathutils import Vector

random.seed(7)
mats = []
for i in range(30):
    m = bpy.data.materials.new(f"Facade{i:02d}")
    m.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (
        random.random(), random.random(), random.random(), 1)
    mats.append(m)
root = bpy.context.scene.collection
for d in range(20):
    col = bpy.data.collections.new(f"District{d:02d}")
    root.children.link(col)
    for b in range(20):
        bpy.ops.mesh.primitive_cube_add(location=(d * 12 + (b % 5) * 2.2, (b // 5) * 2.2, 0))
        o = bpy.context.active_object
        o.name = f"D{d:02d}_Bldg{b:02d}"
        h = random.uniform(1, 8)
        o.scale = (0.9, 0.9, h)
        o.location.z = h
        o.data.materials.append(random.choice(mats))
        o.modifiers.new("Bevel", "BEVEL").width = 0.05
        if b % 4 == 0:
            arr = o.modifiers.new("Floors", "ARRAY")
            arr.count = 2
        o["floors"] = int(h * 3)
        o["district"] = d
        for c in list(o.users_collection):
            c.objects.unlink(o)
        col.objects.link(o)
park = bpy.data.collections.new("Park")
root.children.link(park)
for t in range(50):
    bpy.ops.mesh.primitive_cylinder_add(radius=0.1, depth=1, location=(random.uniform(0, 240), -6, 0.5))
    trunk = bpy.context.active_object
    trunk.name = f"Tree{t:02d}"
    bpy.ops.mesh.primitive_cone_add(radius1=0.6, depth=1.5, location=(0, 0, 1.2))
    crown = bpy.context.active_object
    crown.name = f"Tree{t:02d}_Crown"
    crown.parent = trunk
    for o in (trunk, crown):
        for c in list(o.users_collection):
            c.objects.unlink(o)
        park.objects.link(o)
bpy.ops.object.light_add(type="SUN")
bpy.ops.object.camera_add(location=(120, -80, 60))
cam = bpy.context.active_object
cam.rotation_euler = (Vector((120, 3, 4)) - cam.location).to_track_quat("-Z", "Y").to_euler()
cam.data.clip_end = 1000
bpy.context.scene.camera = cam
result = {"objects": len(bpy.data.objects), "collections": len(bpy.data.collections),
          "materials": len(bpy.data.materials)}
