# qa_cleanup · mcp · repeat 1

model claude-sonnet-5-5 · effort medium · cost $0.0938 (estimate) · turns 5 · stop end_turn · check passed

## Step 1 · mcp__blender__get_blendfile_summary_path_info · 33 ms

```
{}
```

```
{"status":"ok","result":{"status":"ok","filepath":"<tmp>\\runs\\qa_cleanup\\mcp\\r1\\work.blend","is_saved":true,"is_dirty":false,"age_seconds":4.3,"file_size_bytes":362955,"backups":[]}}
```

## Step 2 · mcp__blender__execute_blender_code · 22 ms

```
import bpy
def cols(c,d=0):
    out=[]
    for ch in c.children:
        out.append((ch.name,len(ch.objects),len(ch.children),ch.hide_viewport,ch.hide_render)); out+=cols(ch)
    return out
objs=[(o.name,o.type,[c.name for c in o.users_collection],o.hide_viewport,o.hide_render,o.hide_get() if o.name in bpy.context.view_layer.objects else None, [s.material.name if s.material else None for s in o.material_slots]) for o in bpy.data.objects]
result=dict(objs=objs,cols=cols(bpy.context.scene.collection),allcols=[c.name for c in bpy.data.collections],
 mats=[(m.name,m.users,m.use_fake_user) for m in bpy.data.materials],
 imgs=[(i.name,i.filepath,i.users,i.has_data,i.packed_file is not None) for i in bpy.data.images],
 scenes=[s.name for s in bpy.data.scenes])
```

```
{"status":"ok","result":{"objs":[["Camera","CAMERA",["Scene Collection"],false,false,false,[]],["D00_Bldg00","MESH",["District00"],false,false,false,["Facade28"]],["D00_Bldg01","MESH",["District00"],false,false,false,["Facade13"]],["D00_Bldg02","MESH",["District00"],false,false,false,["Facade08"]],[ ...
```

## Step 3 · mcp__blender__execute_blender_code · 36 ms

```
import bpy
D=bpy.data
before=dict(objects=len(D.objects),collections=len(D.collections),materials=len(D.materials),images=len(D.images))
# image users
img=D.images[0]
img_users=[(m.name) for m in D.materials if m.node_tree and any(n.type=='TEX_IMAGE' and n.image==img for n in m.node_tree.nodes)]
dbg=[o.name for o in D.objects if o.name.startswith("Debug_")]
for n in dbg: D.objects.remove(D.objects[n],do_unlink=True)
d5=D.collections["District05"]
def find_lc(lc,name):
    if lc.name==name: return lc
    for c in lc.children:
        r=find_lc(c,name)
        if r: return r
lc=find_lc(bpy.context.view_layer.layer_collection,"District05")
lcstate=(lc.exclude,lc.hide_viewport)
fixed=[]
for o in d5.objects:
    ch=o.hide_render or o.hide_viewport or o.hide_get() or o.hide_select
    if o.hide_render or o.hide_viewport or o.hide_get(): fixed.append(o.name)
    o.hide_render=False; o.hide_viewport=False; o.hide_set(False)
d5.hide_viewport=False; d5.hide_render=False; lc.exclude=False; lc.hide_viewport=False
empties=[c.name for c in D.collections if not c.objects and not c.children]
for n in empties: D.collections.remove(D.collections[n])
unused=[m.name for m in D.materials if m.users-(1 if m.use_fake_user else 0)==0]
for n in unused: D.materials.remove(D.materials[n])
missing=[i.name for i in D.images if not i.packed_file and not __import__('os').path.exists(bpy.path.abspath(i.filepath))]
for n in missing: D.images.remove(D.images[n])
after=dict(objects=len(D.objects),collections=len(D.collections),materials=len(D.materials),images=len(D.images))
bpy.ops.wm.save_as_mainfile(filepath=bpy.path.abspath("//out/clean.blend"))
result=dict(before=before,after=after,dbg=dbg,fixed=fixed,lcstate=lcstate,empties=empties,unused=unused,missing=missing,img_users=img_users,path=bpy.data.filepath,
 d5=[(o.name,o.hide_render,o.hide_viewport,o.hide_get()) for o in d5.objects if o.hide_render or o.hide_viewport or o.hide_get()])
```

```
{"status":"ok","result":{"before":{"objects":505,"collections":23,"materials":34,"images":1},"after":{"objects":502,"collections":21,"materials":30,"images":0},"dbg":["Debug_Marker01","Debug_Marker02","Debug_Marker03"],"fixed":["D05_Bldg02","D05_Bldg09","D05_Bldg15"],"lcstate":[false,false],"empties ...
```

## Step 4 · Write · 26 ms

```
{
 "file_path": "<tmp>\\runs\\qa_cleanup\\mcp\\r1\\out\\report.md",
 "content": "# work.blend cleanup report\n\nOutput: `out/clean.blend` (`work.blend` was not modified).\n\n## Before / after\n\n| Item        | Before | After | Change |\n|-------------|-------:|------:|-------:|\n| Objects     | 505    | 502   | -3     |\n| Collections | 23     | 21    | -2     |\n| ...
}
```

```
File created successfully at: <tmp>\runs\qa_cleanup\mcp\r1\out\report.md (file state is current in your context — no need to Read it back)
```

**Assistant:**

I saved the cleaned scene as `out/clean.blend` and wrote the before/after summary to `out/report.md`. `work.blend` is unchanged. I haven't reopened `clean.blend` to check it. The last check ran in the live session and found no hidden or non-renderable `District05` objects left.

Before and after counts:

| Item | Before | After |
|---|---:|---:|
| Objects | 505 | 502 |
| Collections | 23 | 21 |
| Materials | 34 | 30 |
| Images | 1 | 0 |

What I found and fixed:
- **Debug objects:** I deleted `Debug_Marker01`, `Debug_Marker02` and `Debug_Marker03`.
- **`District05` visibility:** `D05_Bldg02`, `D05_Bldg09` and `D05_Bldg15` had rendering turned off. They are now visible and renderable, and the collection itself was already neither hidden nor excluded.
- **Empty collections:** I removed `Tmp_A` and `Tmp_B`.
- **Unused materials:** I removed `OldConcrete`, `OldGlass`, `OldMetal` and `OldPaint`. Each had no users except a fake user.
- **Missing image:** I removed `missing_tex.png`. Its file wasn't on disk, and no material used it.

Nothing else was changed.
