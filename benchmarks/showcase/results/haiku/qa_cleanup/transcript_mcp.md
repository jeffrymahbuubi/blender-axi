# qa_cleanup · mcp · repeat 1

model claude-haiku-5-5 · effort medium · cost $0.0120 (estimate) · turns 13 · stop end_turn · check passed

## Step 1 · mcp__blender__get_blendfile_summary_path_info · 15 ms

```
{}
```

```
{"status":"ok","result":{"status":"ok","filepath":"<tmp>\\runs\\qa_cleanup\\mcp\\r1\\work.blend","is_saved":true,"is_dirty":false,"age_seconds":5.6,"file_size_bytes":363004,"backups":[]}}
```

## Step 2 · mcp__blender__get_blendfile_summary_datablocks · 8 ms

```
{}
```

```
{"status":"ok","result":{"status":"ok","datablock_counts":{"all_ids":1092,"cameras":1,"collections":23,"images":1,"lights":1,"materials":34,"meshes":503,"objects":505,"scenes":1,"screens":11,"window_managers":1,"workspaces":11},"render_engine":"BLENDER_EEVEE","scene_name":"Scene","workspaces":["Anim ...
```

## Step 3 · mcp__blender__execute_blender_code · 21 ms

```
import bpy
def coll_tree(c, depth=0, out=None):
    out.append({"name": c.name, "depth": depth, "objects": len(c.objects), "children": [ch.name for ch in c.children], "users": c.users})
    for ch in c.children:
        coll_tree(ch, depth+1, out)
    return out
scene = bpy.context.scene
tree = coll_tree(scene.collection, 0, [])
all_colls = [c.name for c in bpy.data.collections]
debug_objs = [o.name for o in bpy.data.objects if o.name.startswith("Debug_")]
d5 = bpy.data.collections.get("District05")
d5_info = None
if d5:
    d5_info = [{"name": o.name, "hide_viewport": o.hide_viewport, "hide_render": o.hide_render, "hide_get": None, "type": o.type} for o in d5.all_objects]
empty_colls = [c.name for c in bpy.data.collections if len(c.objects)==0 and len(c.children)==0]
mats = [{"name": m.name, "users": m.users, "fake": m.use_fake_user} for m in bpy.data.materials]
imgs = [{"name": i.name, "filepath": i.filepath, "source": i.source, "users": i.users, "exists": (i.packed_file is not None) or __import__('os').path.exists(bpy.path.abspath(i.filepath)) if i.filepath else None, "packed": i.packed_file is not None, "fake": i.use_fake_user} for i in bpy.data.images]
objs_per_coll_hidden = []
for c in bpy.data.collections:
    for o in c.all_objects:
        pass
result = {
  "counts": {"objects": len(bpy.data.objects), "collections": len(bpy.data.collections), "materials": len(bpy.data.materials), "images": len(bpy.data.images), "meshes": len(bpy.data.meshes)},
  "tree": tree,
  "debug_objects": debug_objs,
  "district05": d5_info,
  "empty_collections": empty_colls,
  "materials": mats,
  "images": imgs,
  "view_layer_excluded": [c.name for c in scene.collection.children_recursive if c.name in [lc.name for lc in bpy.context.view_layer.layer_collection.children] ] ,
}
# layer collection exclude/hide states
def lc_states(lc, out):
    out.append({"name": lc.name, "exclude": lc.exclude, "hide_viewport": lc.hide_viewport, "holdout": lc.holdout})
    for ch in lc.children: lc_states(ch, out)
    return out
result["layer_states"] = lc_states(bpy.context.view_layer.layer_collection, [])
result["object_names_sample"] = [o.name for o in bpy.data.objects][:10]
```

```
{"status":"ok","result":{"counts":{"objects":505,"collections":23,"materials":34,"images":1,"meshes":503},"tree":[{"name":"Scene Collection","depth":0,"objects":2,"children":["District00","District01","District02","District03","District04","District05","District06","District07","District08","Distric ...
```

## Step 4 · mcp__blender__execute_blender_code_for_cli · 223 ms

```
import bpy
vl = bpy.context.view_layer
d5 = bpy.data.collections["District05"]
d5_view_hidden = [o.name for o in d5.all_objects if not o.visible_get()]
d5_select_off = [o.name for o in d5.all_objects if o.hide_select]
dbg = [o for o in bpy.data.objects if o.name.lower().startswith("debug")]
dbg_info = [{"name": o.name, "colls": [c.name for c in o.users_collection], "data": o.data.name if o.data else None, "mats": [s.material.name if s.material else None for s in o.material_slots], "children": [c.name for c in o.children], "parent": o.parent.name if o.parent else None} for o in dbg]
# materials used by non-debug objects
used_by_others = set()
for o in bpy.data.objects:
    if o in dbg: continue
    for s in o.material_slots:
        if s.material: used_by_others.add(s.material.name)
mesh_mat_refs = {}
for m in bpy.data.meshes:
    mesh_mat_refs[m.name] = [x.name if x else None for x in m.materials]
old_mats_used = {n: (n in used_by_others) for n in ["OldConcrete","OldGlass","OldMetal","OldPaint"]}
facades_only_on_debug = [m.name for m in bpy.data.materials if m.name.startswith("Facade") and m.name not in used_by_others]
mesh_users = {m.name: m.users for m in bpy.data.meshes if m.users != 1}
# image references
img = bpy.data.images[0]
img_refs = []
for mat in bpy.data.materials:
    if mat.node_tree:
        for n in mat.node_tree.nodes:
            if n.type == 'TEX_IMAGE' and n.image == img: img_refs.append(mat.name)
result = {
 "d5_view_hidden": d5_view_hidden,
 "d5_select_off": d5_select_off,
 "debug": dbg_info,
 "facade_unused_by_non_debug": facades_only_on_debug,
 "old_mats_used_by_objects": old_mats_used,
 "mesh_users_not_1": mesh_users,
 "image_refs_in_materials": img_refs,
 "orphan_mesh_count": sum(1 for m in bpy.data.meshes if m.users == 0),
 "fake_users": {m.name: m.use_fake_user for m in bpy.data.materials if m.use_fake_user},
 "scene_names": [s.name for s in bpy.data.scenes],
 "camera_coll": [c.name for c in bpy.data.objects["Camera"].users_collection],
}
```

```
Error executing tool execute_blender_code_for_cli: Blender executable not found at 'blender'. Set the BLENDER_PATH environment variable to the correct path.
```

(tool result flagged as an error)

**Assistant:**

The background CLI isn't available here, so I'll run the read-only checks in the live session.

## Step 5 · mcp__blender__execute_blender_code · 9 ms

```
import bpy
d5 = bpy.data.collections["District05"]
d5_view_hidden = [o.name for o in d5.all_objects if not o.visible_get()]
d5_select_off = [o.name for o in d5.all_objects if o.hide_select]
dbg = [o for o in bpy.data.objects if o.name.lower().startswith("debug")]
dbg_info = [{"name": o.name, "colls": [c.name for c in o.users_collection], "data": o.data.name if o.data else None, "mats": [s.material.name if s.material else None for s in o.material_slots], "children": [c.name for c in o.children], "parent": o.parent.name if o.parent else None} for o in dbg]
used_by_others = set()
for o in bpy.data.objects:
    if o in dbg: continue
    for s in o.material_slots:
        if s.material: used_by_others.add(s.material.name)
facades_unused = [m.name for m in bpy.data.materials if m.name.startswith("Facade") and m.name not in used_by_others]
mesh_users = {m.name: m.users for m in bpy.data.meshes if m.users != 1}
img = bpy.data.images[0]
img_refs = []
for mat in bpy.data.materials:
    if mat.node_tree:
        for n in mat.node_tree.nodes:
            if n.type == 'TEX_IMAGE' and n.image == img: img_refs.append(mat.name)
result = {
 "d5_view_hidden": d5_view_hidden,
 "d5_select_off": d5_select_off,
 "debug": dbg_info,
 "facade_unused_by_non_debug": facades_unused,
 "old_mats_used_by_objects": {n: (n in used_by_others) for n in ["OldConcrete","OldGlass","OldMetal","OldPaint"]},
 "mesh_users_not_1": mesh_users,
 "image_refs_in_materials": img_refs,
 "orphan_mesh_count": sum(1 for m in bpy.data.meshes if m.users == 0),
 "camera_coll": [c.name for c in bpy.data.objects["Camera"].users_collection],
}
```

```
{"status":"ok","result":{"d5_view_hidden":["Debug_Marker01","Debug_Marker02","Debug_Marker03"],"d5_select_off":[],"debug":[{"name":"Debug_Marker01","colls":["District05"],"data":"Cube.400","mats":[],"children":[],"parent":null},{"name":"Debug_Marker02","colls":["District05"],"data":"Cube.401","mats" ...
```

**Assistant:**

Audit findings are clear. Now applying the fixes in the live session and saving a copy to `out/clean.blend` (`copy=True`, so `work.blend` is not written).

## Step 6 · mcp__blender__execute_blender_code · 29 ms

```
import bpy, os
BASE = r"<tmp>\runs\qa_cleanup\mcp\r1"
OUT = os.path.join(BASE, "out", "clean.blend")
before = {"objects": len(bpy.data.objects), "collections": len(bpy.data.collections), "materials": len(bpy.data.materials), "images": len(bpy.data.images)}
log = {}

# 1. delete Debug_ objects (and their now-orphaned mesh data)
dbg = [o for o in bpy.data.objects if o.name.startswith("Debug_")]
log["deleted_objects"] = [o.name for o in dbg]
dbg_meshes = [o.data for o in dbg if o.data and o.data.users == 1]
for o in dbg:
    bpy.data.objects.remove(o, do_unlink=True)
for m in dbg_meshes:
    if m.users == 0:
        bpy.data.meshes.remove(m)
log["deleted_meshes"] = [m.name for m in dbg_meshes]

# 2. District05: visible + renderable
d5 = bpy.data.collections["District05"]
changed = []
for o in d5.all_objects:
    if o.hide_render or o.hide_viewport or o.hide_get():
        changed.append(o.name)
    o.hide_render = False
    o.hide_viewport = False
    o.hide_set(False)
log["district05_fixed"] = changed

# 3. empty collections
empty = [c for c in bpy.data.collections if len(c.objects) == 0 and len(c.children) == 0]
log["removed_collections"] = [c.name for c in empty]
for c in empty:
    bpy.data.collections.remove(c)

# 4. unused materials (no real users; only fake user)
unused = [m for m in bpy.data.materials if m.users - (1 if m.use_fake_user else 0) == 0]
log["removed_materials"] = [m.name for m in unused]
for m in unused:
    m.use_fake_user = False
    bpy.data.materials.remove(m)

# 5. image with missing file and no users
bad_imgs = [i for i in bpy.data.images if i.source == 'FILE' and i.filepath and not os.path.exists(bpy.path.abspath(i.filepath)) and i.packed_file is None]
log["removed_images"] = [i.name for i in bad_imgs]
for i in bad_imgs:
    i.use_fake_user = False
    bpy.data.images.remove(i)

after = {"objects": len(bpy.data.objects), "collections": len(bpy.data.collections), "materials": len(bpy.data.materials), "images": len(bpy.data.images)}
bpy.ops.wm.save_as_mainfile(filepath=OUT, copy=True)
result = {"before": before, "after": after, "log": log, "saved": os.path.exists(OUT), "out": OUT}
```

```
{"status":"error","message":"Traceback (most recent call last):\n  File \"<home>\\AppData\\Roaming\\Blender Foundation\\Blender\\5.2\\extensions\\user_default\\mcp\\mcp_to_blender_server.py\", line 252, in _execute_code\n    exec(code, namespace)\n    ~~~~^^^^^^^^^^^^^^^^^\n  File \"< ...
```

**Assistant:**

The script failed partway through step 1 (a mesh name was read after removal). Checking the live state before continuing so nothing is applied twice.

## Step 7 · mcp__blender__execute_blender_code · 6 ms

```
import bpy
result = {
 "objects": len(bpy.data.objects),
 "debug_left": [o.name for o in bpy.data.objects if o.name.startswith("Debug_")],
 "meshes": len(bpy.data.meshes),
 "cube400_exists": "Cube.400" in bpy.data.meshes,
 "d5_count": len(bpy.data.collections["District05"].all_objects),
 "collections": len(bpy.data.collections),
}
```

```
{"status":"ok","result":{"objects":502,"debug_left":[],"meshes":500,"cube400_exists":false,"d5_count":20,"collections":23}}
```

**Assistant:**

Step 1 completed (502 objects, 3 Debug meshes removed). Continuing with steps 2–5 and the save.

## Step 8 · mcp__blender__execute_blender_code · 8 ms

```
import bpy, os
BASE = r"<tmp>\runs\qa_cleanup\mcp\r1"
OUT = os.path.join(BASE, "out", "clean.blend")
before = {"objects": 505, "collections": 23, "materials": 34, "images": 1, "meshes": 503}
log = {"deleted_objects": ["Debug_Marker01", "Debug_Marker02", "Debug_Marker03"]}

# 2. District05 visible + renderable
d5 = bpy.data.collections["District05"]
changed = []
for o in d5.all_objects:
    if o.hide_render or o.hide_viewport or o.hide_get():
        changed.append(o.name)
    o.hide_render = False
    o.hide_viewport = False
    o.hide_set(False)
log["district05_fixed"] = changed

# 3. empty collections
empty = [c for c in bpy.data.collections if len(c.objects) == 0 and len(c.children) == 0]
log["removed_collections"] = [c.name for c in empty]
for c in empty:
    bpy.data.collections.remove(c)

# 4. unused materials
unused = [m for m in bpy.data.materials if m.users - (1 if m.use_fake_user else 0) == 0]
log["removed_materials"] = [m.name for m in unused]
for m in unused:
    m.use_fake_user = False
    bpy.data.materials.remove(m)

# 5. missing-file image with no real users
bad = [i for i in bpy.data.images if i.source == 'FILE' and i.filepath and i.packed_file is None and not os.path.exists(bpy.path.abspath(i.filepath))]
log["removed_images"] = [i.name for i in bad]
for i in bad:
    i.use_fake_user = False
    bpy.data.images.remove(i)

after = {"objects": len(bpy.data.objects), "collections": len(bpy.data.collections), "materials": len(bpy.data.materials), "images": len(bpy.data.images), "meshes": len(bpy.data.meshes)}
bpy.ops.wm.save_as_mainfile(filepath=OUT, copy=True)
result = {"before": before, "after": after, "log": log, "saved": os.path.exists(OUT), "out": OUT, "size": os.path.getsize(OUT) if os.path.exists(OUT) else None}
```

```
{"status":"error","message":"Traceback (most recent call last):\n  File \"<home>\\AppData\\Roaming\\Blender Foundation\\Blender\\5.2\\extensions\\user_default\\mcp\\mcp_to_blender_server.py\", line 252, in _execute_code\n    exec(code, namespace)\n    ~~~~^^^^^^^^^^^^^^^^^\n  File \"< ...
```

## Step 9 · mcp__blender__execute_blender_code · 44 ms

```
import bpy
d5 = bpy.data.collections["District05"]
objs = [o for o in bpy.data.objects if o is not None and "District05" in [c.name for c in o.users_collection]]
result = {"d5_via_users": len(objs), "d5_all_objects_raw": [ (o.name if o else None) for o in d5.all_objects][:30], "d5_objects_raw": [(o.name if o else None) for o in d5.objects]}
```

```
{"status":"ok","result":{"d5_via_users":20,"d5_all_objects_raw":["D05_Bldg00","D05_Bldg01","D05_Bldg02","D05_Bldg03","D05_Bldg04","D05_Bldg05","D05_Bldg06","D05_Bldg07","D05_Bldg08","D05_Bldg09","D05_Bldg10","D05_Bldg11","D05_Bldg12","D05_Bldg13","D05_Bldg14","D05_Bldg15","D05_Bldg16","D05_Bldg17"," ...
```

**Assistant:**

The stale iterator was a side effect of the deletion in the same session. The collection is clean now (20 objects), so I'll rerun steps 2–5 using explicit object lists.

## Step 10 · mcp__blender__execute_blender_code · 68 ms

```
import bpy, os
BASE = r"<tmp>\runs\qa_cleanup\mcp\r1"
OUT = os.path.join(BASE, "out", "clean.blend")
before = {"objects": 505, "collections": 23, "materials": 34, "images": 1, "meshes": 503}
log = {"deleted_objects": ["Debug_Marker01", "Debug_Marker02", "Debug_Marker03"]}

# 2. District05 visible + renderable
d5_objs = [o for o in bpy.data.objects if "District05" in [c.name for c in o.users_collection]]
changed = []
for o in d5_objs:
    if o.hide_render or o.hide_viewport or o.hide_get():
        changed.append(o.name)
    o.hide_render = False
    o.hide_viewport = False
    o.hide_set(False)
log["district05_fixed"] = changed

# 3. empty collections
empty = [c for c in bpy.data.collections if len(c.objects) == 0 and len(c.children) == 0]
log["removed_collections"] = [c.name for c in empty]
for c in empty:
    bpy.data.collections.remove(c)

# 4. unused materials
unused = [m for m in bpy.data.materials if m.users - (1 if m.use_fake_user else 0) == 0]
log["removed_materials"] = [m.name for m in unused]
for m in unused:
    m.use_fake_user = False
    bpy.data.materials.remove(m)

# 5. missing-file image with no real users
bad = [i for i in bpy.data.images if i.source == 'FILE' and i.filepath and i.packed_file is None and not os.path.exists(bpy.path.abspath(i.filepath))]
log["removed_images"] = [i.name for i in bad]
for i in bad:
    i.use_fake_user = False
    bpy.data.images.remove(i)

after = {"objects": len(bpy.data.objects), "collections": len(bpy.data.collections), "materials": len(bpy.data.materials), "images": len(bpy.data.images), "meshes": len(bpy.data.meshes)}
bpy.ops.wm.save_as_mainfile(filepath=OUT, copy=True)
result = {"before": before, "after": after, "log": log, "saved": os.path.exists(OUT), "out": OUT, "size": os.path.getsize(OUT) if os.path.exists(OUT) else None}
```

```
{"status":"ok","result":{"before":{"objects":505,"collections":23,"materials":34,"images":1,"meshes":503},"after":{"objects":502,"collections":21,"materials":30,"images":0,"meshes":500},"log":{"deleted_objects":["Debug_Marker01","Debug_Marker02","Debug_Marker03"],"district05_fixed":["D05_Bldg15"],"r ...
```

**Assistant:**

Save succeeded. The log only lists `D05_Bldg15` as changed, but `Bldg02` and `Bldg09` were already fixed by the earlier partially-run pass (the loop processed them before erroring). Verifying the final District05 state directly rather than trusting the log.

## Step 11 · mcp__blender__execute_blender_code · 16 ms

```
import bpy
d5_objs = [o for o in bpy.data.objects if "District05" in [c.name for c in o.users_collection]]
bad = [o.name for o in d5_objs if o.hide_render or o.hide_viewport or o.hide_get() or not o.visible_get()]
result = {
 "d5_count": len(d5_objs),
 "d5_not_visible_or_renderable": bad,
 "debug_left": [o.name for o in bpy.data.objects if o.name.startswith("Debug_")],
 "collections": sorted(c.name for c in bpy.data.collections),
 "materials": sorted(m.name for m in bpy.data.materials)[:3] + ["...total %d" % len(bpy.data.materials)],
 "facade_count": sum(1 for m in bpy.data.materials if m.name.startswith("Facade")),
 "images": [i.name for i in bpy.data.images],
 "orphan_meshes": sum(1 for m in bpy.data.meshes if m.users == 0),
 "filepath_now": bpy.data.filepath,
}
```

```
{"status":"ok","result":{"d5_count":20,"d5_not_visible_or_renderable":[],"debug_left":[],"collections":["District00","District01","District02","District03","District04","District05","District06","District07","District08","District09","District10","District11","District12","District13","District14"," ...
```

**Assistant:**

Final state checks pass. Writing the report now.

## Step 12 · Write · 23 ms

```
{
 "file_path": "<tmp>\\runs\\qa_cleanup\\mcp\\r1\\out\\report.md",
 "content": "# Asset cleanup report: work.blend -> out/clean.blend\n\n## Before / after\n\n| Datablock   | Before | After | Change |\n|-------------|-------:|------:|-------:|\n| Objects     | 505    | 502   | -3     |\n| Collections | 23     | 21    | -2     |\n| Materials   | 34     | 30    | -4   ...
}
```

```
File created successfully at: <tmp>\runs\qa_cleanup\mcp\r1\out\report.md (file state is current in your context — no need to Read it back)
```

**Assistant:**

Both outputs are written: `out/clean.blend` and `out/report.md`. `work.blend` was not modified on disk.

**Before → after:** objects 505 → 502, collections 23 → 21, materials 34 → 30, images 1 → 0, meshes 503 → 500 (the three Debug meshes went with their objects).

**Fixed:**
- Deleted `Debug_Marker01–03`, which were in `District05`.
- Enabled render on `D05_Bldg02`, `D05_Bldg09`, `D05_Bldg15`. All 20 remaining `District05` objects are now visible and renderable.
- Removed empty collections `Tmp_A` and `Tmp_B`.
- Removed unused materials `OldConcrete`, `OldGlass`, `OldMetal`, `OldPaint`. These had only a fake user. All 30 `Facade` materials are in use and were kept.
- Removed image `missing_tex.png`, which is missing on disk and had no real users.

**Method notes:**
- The background Blender CLI wasn't available, so I did the work in the live session and saved a copy with `save_as_mainfile(copy=True)`. The session still points at `work.blend`.
- Two script runs failed partway through. I checked the resulting state and re-ran the remaining steps, then verified the final state directly rather than relying on the logs.
- The report says the same, along with the full before/after table.
