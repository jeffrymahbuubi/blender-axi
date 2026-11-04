# qa_cleanup · axi · repeat 1

model claude-sonnet-5-5 · effort medium · cost $0.1666 (estimate) · turns 13 · stop end_turn · check passed

## Step 1 · Bash · 5108 ms

```
blender-axi audit work.blend --full; blender-axi scene work.blend; blender-axi objects work.blend --full
```

```
file: work.blend
checked: "11 checks, 505 objects"
findings[6]{check,level,count,items}:
  scale,warn,400,"D00_Bldg00 (0.9,0.9,4.846), D00_Bldg01 (0.9,0.9,1.958), D00_Bldg02 (0.9,0.9,7.048), D00_Bldg03 (0.9,0.9,5.945), D00_Bldg04 (0.9,0.9,5.779), D00_Bldg05 (0.9,0.9,7.704), D00_Bldg06 (0.9,0.9,1.581), D00_Bldg07 (0.9,0.9,2.624), D00_Bldg08 (0.9,0.9,1.084), D00_Bldg09 (0.9,0.9,5.124), D0 ...
  materials,warn,103,"Debug_Marker01, Debug_Marker02, Debug_Marker03, Tree00, Tree00_Crown, Tree01, Tree01_Crown, Tree02, Tree02_Crown, Tree03, Tree03_Crown, Tree04, Tree04_Crown, Tree05, Tree05_Crown, Tree06, Tree06_Crown, Tree07, Tree07_Crown, Tree08, Tree08_Crown, Tree09, Tree09_Crown, Tree10, Tr ...
  ngons,info,100,"Tree00 (2 faces), Tree00_Crown (1 face), Tree01 (2 faces), Tree01_Crown (1 face), Tree02 (2 faces), Tree02_Crown (1 face), Tree03 (2 faces), Tree03_Crown (1 face), Tree04 (2 faces), Tree04_Crown (1 face), Tree05 (2 faces), Tree05_Crown (1 face), Tree06 (2 faces), Tree06_Crown (1 fa ...
  visibility,info,6,"D05_Bldg02 (render off), D05_Bldg09 (render off), D05_Bldg15 (render off), Debug_Marker01 (render off), Debug_Marker02 (render off), Debug_Marker03 (render off)"
  files,warn,1,"image missing_tex.png (missing: <tmp>/runs/qa_cleanup/axi/r1/textures/missing_tex.png)"
  collections,info,2,"Tmp_A, Tmp_B"
status: "6 findings (3 warn, 3 info)"
duration_ms: 16
help[2]:
  - "Run `blender-axi material Debug_Marker01 <file> --name Mat --color 0.8,0.8,0.8 --in-place` to give it a material"
  - "Apply scale (Ctrl+A) and clean up meshes in Blender, or via exec --allow-exec"
file: work.blend
blender: 5.2.0 LTS
scene: Scene
engine: EEVEE
resolution: 1920x1080@100%
frames: 1-250 (current 1)
... (115 more lines)
```

## Step 2 · Bash · 216 ms

```
blender-axi exec --help
```

```
usage: "blender-axi exec [<file>] [flags]"
summary: "Run Python inside Blender on a .blend, or the open session with --live (requires --allow-exec; original only changed with --in-place)"
positionals[1]{name,required,help}:
  file,false,"The .blend file (default: the single .blend in the current directory)"
flags[6]{flag,default,help}:
  "--code <text>","",Python to run; `bpy` is predefined; assign a dict to `result`
  "--script <path>","",Path to a Python file to run instead of --code
  "--allow-exec","","Required: confirm that arbitrary Python may run inside Blender"
  "--in-place","","Save changes into the original file (a .blend1 backup may appear); with --live, the open file"
  "--out <path>","","Save changes as a copy at this .blend path (default: a copy under .tmp/exec/); not with --live"
  "--force","",Allow --out to overwrite an existing file
global_flags[11]{flag,default,help}:
  "--help","",Show help for the command
  "--json","",Emit JSON instead of TOON
  "--full","",Do not truncate long text fields
  "--debug","",Print tracebacks and Blender stderr to stderr
  "--keep-tmp","",Keep per-run temp directories under .tmp/
  "--blender <path>","",Path to the Blender executable (or set BLENDER_AXI_BLENDER)
  "--user-prefs","","Headless only: load your preferences and add-ons instead of --factory-startup"
  "--timeout <n>","","Timeout in seconds (default 120, render 600, live 300)"
... (4 more lines)
```

## Step 3 · Bash · 27 ms

```
cat > /tmp/dummy 2>/dev/null; blender-axi exec work.blend --allow-exec --out out/clean.blend --code '
import bpy
def cnt(): return dict(objects=len(bpy.data.objects),collections=len(bpy.data.collections),materials=len(bpy.data.materials),images=len(bpy.data.images))
before=cnt()
info={}
info["debug_objs"]=[o.name for o in bpy.data.objects if o.name.startswith("Debug_")]
d05=bpy.data.collections.get("District05")
info["d05_hidden"]=[(o.name,o.hide_viewport,o.hide_render,o.hide_get()) for o in d05.all_objects if o.hide_viewport or o.hide_render or o.hide_get()] if d05 else None
info["d05_coll_hidden"]=[d05.hide_viewport,d05.hide_render] if d05 else None
info["mat_users"]={m.name:m.users for m in bpy.data.materials}
# delete debug
meshes=[]
for n in info["debug_objs"]:
    o=bpy.data.objects[n]
    if o.type=="MESH": meshes.append(o.data)
    bpy.data.objects.remove(o,do_unlink=True)
for me in meshes:
    if me.users==0: bpy.data.meshes.remove(me)
# visibility
if d05:
    d05.hide_viewport=False; d05.hide_render=False
    for lc in [lc for vl in bpy.context.scene.view_layers for lc in [None]]: pass
    def walk(lc,out):
        out.append(lc)
        for c in lc.children: walk(c,out)
    for vl in bpy.context.scene.view_layers:
        l=[];walk(vl.layer_collection,l)
        for lc in l:
            if lc.collection==d05: lc.hide_viewport=False
    for o in d05.all_objects:
        o.hide_viewport=False;o.hide_render=False
        try: o.hide_set(False)
        except Exception: pass
# empty collections (iterative)
removed=[]
ch=True
while ch:
    ch=False
    for c in list(bpy.data.collections):
        if not c.objects and not c.children:
            removed.append(c.name); bpy.data.collections.remove(c); ch=True
# unused materials
used=set()
for o in bpy.data.objects:
    for s in o.material_slots:
        if s.material: used.add(s.material.name)
um=[m.name for m in bpy.data.materials if m.name not in used]
for n in um: bpy.data.materials.remove(bpy.data.materials[n])
# missing images
mi=[]
for im in list(bpy.data.images):
    if im.source=="FILE" and im.filepath and not __import__("os").path.exists(bpy.path.abspath(im.filepath)):
        mi.append(im.name); bpy.data.images.remove(im)
info["removed_collections"]=removed; info["removed_materials"]=um; info["removed_images"]=mi
result=dict(before=before,after=cnt(),info=info)
' --full
```

```
Newline followed by # inside a quoted argument can hide arguments from path validation
```

(tool result flagged as an error)

## Step 4 · Bash · 1896 ms

```
blender-axi exec work.blend --allow-exec --out out/clean.blend --full --code '
import bpy, os
def cnt(): return dict(objects=len(bpy.data.objects),collections=len(bpy.data.collections),materials=len(bpy.data.materials),images=len(bpy.data.images))
before=cnt()
info={}
info["debug_objs"]=[o.name for o in bpy.data.objects if o.name.startswith("Debug_")]
d05=bpy.data.collections.get("District05")
info["d05_hidden"]=[(o.name,o.hide_viewport,o.hide_render,o.hide_get()) for o in d05.all_objects if o.hide_viewport or o.hide_render or o.hide_get()]
info["d05_coll_hidden"]=[d05.hide_viewport,d05.hide_render]
meshes=[]
for n in info["debug_objs"]:
    o=bpy.data.objects[n]
    if o.type=="MESH": meshes.append(o.data)
    bpy.data.objects.remove(o,do_unlink=True)
for me in meshes:
    if me.users==0: bpy.data.meshes.remove(me)
d05.hide_viewport=False; d05.hide_render=False
def walk(lc):
    if lc.collection==d05: lc.hide_viewport=False
    for c in lc.children: walk(c)
for vl in bpy.context.scene.view_layers: walk(vl.layer_collection)
for o in d05.all_objects:
    o.hide_viewport=False;o.hide_render=False
    o.hide_set(False)
removed=[]
ch=True
while ch:
    ch=False
    for c in list(bpy.data.collections):
        if not c.objects and not c.children:
            removed.append(c.name); bpy.data.collections.remove(c); ch=True
used=set()
for o in bpy.data.objects:
    for s in o.material_slots:
        if s.material: used.add(s.material.name)
um=[m.name for m in bpy.data.materials if m.name not in used]
for n in um: bpy.data.materials.remove(bpy.data.materials[n])
mi=[]
for im in list(bpy.data.images):
    if im.source=="FILE" and im.filepath and not os.path.exists(bpy.path.abspath(im.filepath)):
        mi.append(im.name); bpy.data.images.remove(im)
info["removed_collections"]=removed; info["removed_materials"]=um; info["removed_images"]=mi
result=dict(before=before,after=cnt(),info=info)
'
```

```
file: work.blend
status: ok
result:
  before:
    objects: 505
    collections: 23
    materials: 34
    images: 1
  after:
    objects: 502
    collections: 21
    materials: 30
    images: 0
  info:
    debug_objs[3]: Debug_Marker01,Debug_Marker02,Debug_Marker03
    d05_hidden[6]:
      - [4]: D05_Bldg02,false,true,false
      - [4]: D05_Bldg09,false,true,false
      - [4]: D05_Bldg15,false,true,false
      - [4]: Debug_Marker01,true,true,false
... (12 more lines)
```

## Step 5 · Bash · 1397 ms

```
blender-axi audit out/clean.blend | grep -v '^  scale' | cut -c1-200; ls out
```

```
file: out/clean.blend
checked: "11 checks, 502 objects"
findings[4]{check,level,count,items}:
  materials,warn,100,"Tree00, Tree00_Crown, Tree01, Tree01_Crown, Tree02, +95 more"
  ngons,info,100,"Tree00 (2 faces), Tree00_Crown (1 face), Tree01 (2 faces), Tree01_Crown (1 face), Tree02 (2 faces), +95 more"
  visibility,info,1,D05_Bldg15 (render off)
status: "4 findings (2 warn, 2 info)"
duration_ms: 16
help[3]:
  - "Run `blender-axi material Tree00 <file> --name Mat --color 0.8,0.8,0.8 --in-place` to give it a material"
  - Run `blender-axi audit <file> --full` to list every item
  - "Apply scale (Ctrl+A) and clean up meshes in Blender, or via exec --allow-exec"
clean.blend
```

## Step 6 · Bash · 2378 ms

```
blender-axi object D05_Bldg15 out/clean.blend; blender-axi object D05_Bldg02 out/clean.blend | head -20
```

```
name: D05_Bldg15
type: MESH
collection: District05
parent: none
children: none
used_by: none
location: "60,6.6,7.183"
rotation_deg: "0,0,0"
scale: "0.9,0.9,7.183"
dimensions: "1.8,1.8,14.365"
visible: true
verts: 8
faces: 6
materials[1]: Facade01
modifiers[1]{name,type}:
  Bevel,BEVEL
custom_props: "floors=21, district=5"
name: D05_Bldg02
type: MESH
collection: District05
... (14 more lines)
```

## Step 7 · Bash · 1673 ms

```
blender-axi exec out/clean.blend --allow-exec --code '
result={o.name:(o.hide_render,o.hide_viewport,o.hide_get(),[c.name for c in o.users_collection]) for o in bpy.data.objects if o.name in ("D05_Bldg15","D05_Bldg02","D05_Bldg09")}
result["cols"]={c.name:(c.hide_render,c.hide_viewport) for c in bpy.data.collections if c.hide_render or c.hide_viewport}
'
```

```
file: out/clean.blend
status: ok
result:
  D05_Bldg02[4]:
    - false
    - false
    - false
    - [1]: District05
  D05_Bldg09[4]:
    - false
    - false
    - false
    - [1]: District05
  D05_Bldg15[4]:
    - true
    - false
    - false
    - [1]: District05
  cols:
stdout: ""
... (3 more lines)
```

## Step 8 · Bash · 1774 ms

```
blender-axi exec out/clean.blend --allow-exec --code '
o=bpy.data.objects["D05_Bldg15"]
ad=o.animation_data
result={"anim":bool(ad), "drivers":[d.data_path for d in ad.drivers] if ad else [], "action":ad.action.name if ad and ad.action else None, "inall":o in list(bpy.data.collections["District05"].all_objects)}
o.hide_render=False
result["after"]=o.hide_render
' ; ls out
```

```
file: out/clean.blend
status: ok
result:
  anim: false
  drivers: []
  action: null
  inall: true
  after: false
stdout: ""
modified: true
saved: "<repo>/.tmp/exec/clean-20261104-033914-6c8868.blend"
duration_ms: 434
help[1]:
  - "Inspect the result: `blender-axi objects <repo>/.tmp/exec/clean-20261104-033914-6c8868.blend`"
clean.blend
```

## Step 9 · Bash · 39 ms

```
rm -f "<repo>/.tmp/exec/clean-20261104-033914-6c8868.blend"; blender-axi exec work.blend --allow-exec --out out/clean.blend --force --full --code '
import bpy, os
def cnt(): return dict(objects=len(bpy.data.objects),collections=len(bpy.data.collections),materials=len(bpy.data.materials),images=len(bpy.data.images))
before=cnt()
meshes=[]
for o in [o for o in bpy.data.objects if o.name.startswith("Debug_")]:
    if o.type=="MESH": meshes.append(o.data)
    bpy.data.objects.remove(o,do_unlink=True)
for me in meshes:
    if me.users==0: bpy.data.meshes.remove(me)
d05=bpy.data.collections["District05"]
d05.hide_viewport=False; d05.hide_render=False
def walk(lc):
    if lc.collection==d05: lc.hide_viewport=False
    for c in lc.children: walk(c)
for vl in bpy.context.scene.view_layers: walk(vl.layer_collection)
for o in list(d05.all_objects):
    o.hide_viewport=False
    o.hide_render=False
    o.hide_set(False)
for c in list(bpy.data.collections):
    if not c.objects and not c.children: bpy.data.collections.remove(c)
used={s.material.name for o in bpy.data.objects for s in o.material_slots if s.material}
for m in [m for m in bpy.data.materials if m.name not in used]: bpy.data.materials.remove(m)
for im in list(bpy.data.images):
    if im.source=="FILE" and im.filepath and not os.path.exists(bpy.path.abspath(im.filepath)): bpy.data.images.remove(im)
result=dict(before=before,after=cnt(),still_hidden=[o.name for o in d05.all_objects if o.hide_render or o.hide_viewport or o.hide_get()])
'; blender-axi audit out/clean.blend | grep -E "visib|findings"; ls out ../../../../../exec 2>/dev/null | head
```

```
This Bash command contains multiple operations. The following parts require approval: rm -f "<repo>/.tmp/exec/clean-20261104-033914-6c8868.blend"; blender-axi exec work.blend --allow-exec --out out/clean.blend --force --full --code '
import bpy, os
def cnt(): return dict(objects=len(bpy.data.objects),collections=len(bpy.data.collections),materials=len(bpy.data.materials),images=len(bpy.data.images))
before=cnt()
meshes=[]
for o in [o for o in bpy.data.objects if o.name.startswith("Debug_")]:
    if o.type=="MESH": meshes.append(o.data)
    bpy.data.objects.remove(o,do_unlink=True)
for me in meshes:
    if me.users==0: bpy.data.meshes.remove(me)
d05=bpy.data.collections["District05"]
d05.hide_viewport=False; d05.hide_render=False
def walk(lc):
    if lc.collection==d05: lc.hide_viewport=False
    for c in lc.children: walk(c)
for vl in bpy.context.scene.view_layers: walk(vl.layer_collection)
for o in list(d05.all_objects):
    o.hide_viewport=False
    o.hide_render=False
    o.hide_set(False)
... (8 more lines)
```

(tool result flagged as an error)

## Step 10 · Bash · 21 ms

```
rm -f "<repo>/.tmp/exec/clean-20261104-033914-6c8868.blend"
```

```
rm in '<repo>/.tmp/exec/clean-20261104-033914-6c8868.blend' needs approval. The path is outside the working directories for this session ('<tmp>\runs\qa_cleanup\axi\r1'). Allowing runs the command as writ ...
```

(tool result flagged as an error)

## Step 11 · Bash · 3113 ms

```
blender-axi exec work.blend --allow-exec --out out/clean.blend --force --full --code '
import bpy, os
def cnt(): return dict(objects=len(bpy.data.objects),collections=len(bpy.data.collections),materials=len(bpy.data.materials),images=len(bpy.data.images))
before=cnt()
meshes=[]
for o in [o for o in bpy.data.objects if o.name.startswith("Debug_")]:
    if o.type=="MESH": meshes.append(o.data)
    bpy.data.objects.remove(o,do_unlink=True)
for me in meshes:
    if me.users==0: bpy.data.meshes.remove(me)
d05=bpy.data.collections["District05"]
d05.hide_viewport=False; d05.hide_render=False
def walk(lc):
    if lc.collection==d05: lc.hide_viewport=False
    for c in lc.children: walk(c)
for vl in bpy.context.scene.view_layers: walk(vl.layer_collection)
for o in list(d05.all_objects):
    o.hide_viewport=False
    o.hide_render=False
    o.hide_set(False)
for c in list(bpy.data.collections):
    if not c.objects and not c.children: bpy.data.collections.remove(c)
used={s.material.name for o in bpy.data.objects for s in o.material_slots if s.material}
for m in [m for m in bpy.data.materials if m.name not in used]: bpy.data.materials.remove(m)
for im in list(bpy.data.images):
    if im.source=="FILE" and im.filepath and not os.path.exists(bpy.path.abspath(im.filepath)): bpy.data.images.remove(im)
result=dict(before=before,after=cnt(),still_hidden=[o.name for o in d05.all_objects if o.hide_render or o.hide_viewport or o.hide_get()])
'; blender-axi audit out/clean.blend | grep -E "visib|findings"
```

```
file: work.blend
status: ok
result:
  before:
    objects: 505
    collections: 23
    materials: 34
    images: 1
  after:
    objects: 502
    collections: 21
    materials: 30
    images: 0
  still_hidden: []
stdout: ""
modified: true
saved: out/clean.blend
duration_ms: 438
help[1]:
  - "Inspect the result: `blender-axi objects out/clean.blend`"
... (2 more lines)
```

## Step 12 · Write · 20 ms

```
{
 "file_path": "<tmp>\\runs\\qa_cleanup\\axi\\r1\\out\\report.md",
 "content": "# work.blend cleanup report\n\nOutput: `out/clean.blend` (`work.blend` is unchanged).\n\n## Before / after\n\n| Item | Before | After | Change |\n|---|---|---|---|\n| Objects | 505 | 502 | -3 |\n| Collections (excl. Scene Collection) | 23 | 21 | -2 |\n| Materials | 34 | 30 | -4 |\n| Ima ...
}
```

```
File created successfully at: <tmp>\runs\qa_cleanup\axi\r1\out\report.md (file state is current in your context — no need to Read it back)
```

**Assistant:**

I saved the cleaned file as `out/clean.blend` and wrote the before/after report to `out/report.md`. A re-audit of `out/clean.blend` shows none of the problems you listed. Only the three out-of-scope findings below remain, and `work.blend` is unchanged.

| | Before | After |
|---|---|---|
| Objects | 505 | 502 |
| Collections (not counting the Scene Collection) | 23 | 21 |
| Materials | 34 | 30 |
| Images | 1 | 0 |

- **Deleted:** `Debug_Marker01`, `Debug_Marker02` and `Debug_Marker03`. I also deleted their leftover mesh data.
- **District05:** `D05_Bldg02`, `D05_Bldg09` and `D05_Bldg15` had rendering switched off. All District05 objects are now visible and renderable.
- **Removed:** the empty collections `Tmp_A` and `Tmp_B`. I also removed the unused materials `OldConcrete`, `OldGlass`, `OldMetal` and `OldPaint`, and the image `missing_tex.png`.
- **Not changed:** 400 buildings have unapplied scale. 100 `Tree*` objects have no material, and those same 100 have n-gon faces. These are listed in the report as out of scope.

I broke one of your rules by accident. A diagnostic `exec` run saved a stray copy at `<repo>/.tmp/exec/clean-20261104-033914-6c8868.blend`, outside `out`. My attempt to delete it needed permission and wasn't approved, so it's still there and you can delete it.

My first cleanup pass left `D05_Bldg15` render-disabled, so I reran the whole fix from `work.blend` and overwrote `out/clean.blend`. I didn't find out why the first pass missed it.
