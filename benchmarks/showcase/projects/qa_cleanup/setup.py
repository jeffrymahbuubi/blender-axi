"""qa_cleanup start file: the 502-object city plus a deterministic mess (plan section 5, U2).

Run with `blender-axi exec city.blend --script setup.py --in-place --allow-exec` (bpy is predefined, `result` is returned).
Adds, all in or next to District05:
  - 3 objects `Debug_*` in `District05`, hidden in the viewport and disabled for rendering;
  - 2 empty collections `Tmp_A`, `Tmp_B` under the scene collection;
  - 4 materials `Old*` and 1 image `missing_tex.png` (file path that does not exist) with no real user. Blender does not
    write zero-user data to a .blend, so each carries a fake user (users == 1, `use_fake_user` True): "unused" below
    means users minus the fake user equals 0, which is what Blender's Outliner calls orphan data;
  - 3 buildings `D05_Bldg02`, `D05_Bldg09`, `D05_Bldg15` with `hide_render = True`.
"""

import bpy

district = bpy.data.collections["District05"]
root = bpy.context.scene.collection

for i in range(3):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(5 * 12 + i * 3.0, -4.0, 0.5))
    dbg = bpy.context.active_object
    dbg.name = f"Debug_Marker{i + 1:02d}"
    for col in list(dbg.users_collection):
        col.objects.unlink(dbg)
    district.objects.link(dbg)
    dbg.hide_viewport = True
    dbg.hide_render = True

for name in ("Tmp_A", "Tmp_B"):
    root.children.link(bpy.data.collections.new(name))

for i, name in enumerate(("OldConcrete", "OldGlass", "OldMetal", "OldPaint")):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (0.2 + 0.2 * i, 0.3, 0.4, 1.0)
    mat.use_fake_user = True

img = bpy.data.images.new("missing_tex.png", 8, 8)
img.source = "FILE"
img.filepath = "//textures/missing_tex.png"
img.use_fake_user = True

for n in (2, 9, 15):
    bpy.data.objects[f"D05_Bldg{n:02d}"].hide_render = True

result = {
    "objects": len(bpy.data.objects),
    "collections": len(bpy.data.collections),
    "materials": len(bpy.data.materials),
    "images": len(bpy.data.images),
    "debug_objects": sum(1 for o in bpy.data.objects if o.name.startswith("Debug_")),
}
