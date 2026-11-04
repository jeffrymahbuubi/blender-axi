# qa_cleanup · axi · repeat 1

model claude-haiku-5-5 · effort medium · cost $0.0176 (estimate) · turns 14 · stop end_turn · check passed

**Assistant:**

I'll start by checking what's in the working directory and running the audit on `work.blend`.

## Step 1 · Bash · 3949 ms

```
pwd; ls -la; ls -la out 2>/dev/null; blender-axi --help 2>&1 | head -5
```

```
<tmp>/runs/qa_cleanup/axi/r1
total 360
drwxr-xr-x 1 <user> 197121      0 Nov  4 02:05 .
drwxr-xr-x 1 <user> 197121      0 Nov  4 02:05 ..
drwxr-xr-x 1 <user> 197121      0 Nov  4 02:05 out
drwxr-xr-x 1 <user> 197121      0 Nov  4 02:05 snapshots
-rw-r--r-- 1 <user> 197121      0 Nov  4 02:05 stderr.txt
-rw-r--r-- 1 <user> 197121      0 Nov  4 02:05 stream.jsonl
-rw-r--r-- 1 <user> 197121 363004 Nov  4 02:05 work.blend
total 4
drwxr-xr-x 1 <user> 197121 0 Nov  4 02:05 .
drwxr-xr-x 1 <user> 197121 0 Nov  4 02:05 ..
usage: "blender-axi [<command>] [<file.blend>] [flags]"
summary: Agent-ergonomic CLI for Blender. No command shows the home view.
commands[19]{command,summary}:
  scene,"Overview of a .blend file: engine, resolution, frames, camera, object and collection counts"
  new,"Create a new .blend file (empty, or with Blender's default Camera, Cube and Light)"
```

## Step 2 · Bash · 5190 ms

```
blender-axi audit work.blend 2>&1
```

```
file: work.blend
checked: "11 checks, 505 objects"
findings[6]{check,level,count,items}:
  scale,warn,400,"D00_Bldg00 (0.9,0.9,4.846), D00_Bldg01 (0.9,0.9,1.958), D00_Bldg02 (0.9,0.9,7.048), D00_Bldg03 (0.9,0.9,5.945), D00_Bldg04 (0.9,0.9,5.779), +395 more"
  materials,warn,103,"Debug_Marker01, Debug_Marker02, Debug_Marker03, Tree00, Tree00_Crown, +98 more"
  ngons,info,100,"Tree00 (2 faces), Tree00_Crown (1 face), Tree01 (2 faces), Tree01_Crown (1 face), Tree02 (2 faces), +95 more"
  visibility,info,6,"D05_Bldg02 (render off), D05_Bldg09 (render off), D05_Bldg15 (render off), Debug_Marker01 (render off), Debug_Marker02 (render off), +1 more"
  files,warn,1,"image missing_tex.png (missing: <tmp>/runs/qa_cleanup/axi/r1/textures/missing_tex.png)"
  collections,info,2,"Tmp_A, Tmp_B"
status: "6 findings (3 warn, 3 info)"
duration_ms: 17
help[3]:
  - "Run `blender-axi material Debug_Marker01 <file> --name Mat --color 0.8,0.8,0.8 --in-place` to give it a material"
  - Run `blender-axi audit <file> --full` to list every item
  - "Apply scale (Ctrl+A) and clean up meshes in Blender, or via exec --allow-exec"
```

## Step 3 · Bash · 7319 ms

```
blender-axi scene work.blend 2>&1; blender-axi objects work.blend 2>&1
```

```
file: work.blend
blender: 5.2.0 LTS
scene: Scene
engine: EEVEE
resolution: 1920x1080@100%
frames: 1-250 (current 1)
fps: 24
camera: Camera
objects: "505 (MESH 503, CAMERA 1, LIGHT 1)"
collections: "24 (Scene Collection, District00, District01, District02, District03, +19 more)"
materials: "34 (Facade00, Facade01, Facade02, Facade03, Facade04, +29 more)"
help[2]:
  - Run `blender-axi objects <file>` to list objects
  - Run `blender-axi render <file> --out <path.png>` to render a still
file: work.blend
count: 100 of 505 total
objects[100]{name,type,collection,visible}:
  Camera,CAMERA,Scene Collection,true
  D00_Bldg00,MESH,District00,true
  D00_Bldg01,MESH,District00,true
... (101 more lines)
```

**Assistant:**

The audit shows 3 warnings and 3 info items. The CLI has no commands for removing collections, materials, or images, so I'll need `exec`. Let me check its options and collect the full inventory read-only first.

## Step 4 · Bash · 263 ms

```
blender-axi exec --help 2>&1 | head -60
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

## Step 5 · Bash · 1291 ms

```
blender-axi audit work.blend --full 2>&1 | head -80
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
```

**Assistant:**

The audit has 6 finding groups. Before I change anything, I need the exact sets: Debug_ objects, District05 members, empty collections, unused materials, and the missing image. I'll run a read-only `exec` that only reports and never saves.

## Step 6 · Bash · 2184 ms

```
blender-axi exec work.blend --allow-exec --json --code "
import bpy
def cols(c):
    return c
dbg=[o.name for o in bpy.data.objects if o.name.startswith('Debug_')]
d5=[(o.name,o.hide_viewport,o.hide_render,o.hide_get() if False else None) for o in bpy.data.collections.get('District05').all_objects] if bpy.data.collections.get('District05') else 'NO District05'
empty=[c.name for c in bpy.data.collections if len(c.objects)==0 and len(c.children)==0]
allcols=[c.name for c in bpy.data.collections]
mats=[(m.name,m.users,m.use_fake_user) for m in bpy.data.materials]
unused=[m.name for m in bpy.data.materials if m.users==0]
fakeonly=[m.name for m in bpy.data.materials if m.users==(1 if m.use_fake_user else 0) and m.use_fake_user]
imgs=[(i.name,i.filepath,i.source,i.users,i.packed_file is not None) for i in bpy.data.images]
result={'debug':dbg,'d05_count':len(d5) if isinstance(d5,list) else d5,'d05_hidden_or_norender':[x for x in d5 if isinstance(x,tuple) and (x[1] or x[2])] if isinstance(d5,list) else None,'empty_cols':empty,'all_cols':allcols,'mat_count':len(mats),'unused_mats':unused,'fakeonly':fakeonly,'images':img ...
" 2>&1
```

```
{
  "file": "work.blend",
  "status": "ok",
  "result": {
    "debug": [
      "Debug_Marker01",
      "Debug_Marker02",
      "Debug_Marker03"
    ],
    "d05_count": 23,
    "d05_hidden_or_norender": [
      [
        "D05_Bldg02",
        false,
        true,
        null
      ],
      [
        "D05_Bldg09",
        false,
... (100 more lines)
```

**Assistant:**

Here is what the read-only pass found:

- **Debug_ objects:** 3 (`Debug_Marker01`–`03`), all in `District05`.
- **District05:** 23 objects. `D05_Bldg02`, `D05_Bldg09`, `D05_Bldg15` have render off. The three Debug markers are hidden and render-off too, but they're being deleted.
- **Empty collections:** `Tmp_A`, `Tmp_B`.
- **Unused materials:** no materials with zero users. Four have only a fake user and nothing uses them: `OldConcrete`, `OldGlass`, `OldMetal`, `OldPaint`.
- **Missing image:** `missing_tex.png`, one user.

Before deleting, I need to know which materials and meshes the Debug objects and the missing image depend on, so cleanup doesn't leave things orphaned.

## Step 7 · Bash · 2300 ms

```
blender-axi exec work.blend --allow-exec --json --code "
import bpy
r={}
r['debug_mesh']=[(o.name, o.data.name if o.data else None, (o.data.users if o.data else None), [m.name if m else None for m in (o.data.materials if o.data else [])], [s.material.name if s.material else None for s in o.material_slots]) for o in bpy.data.objects if o.name.startswith('Debug_')]
r['mat_users']={m.name:[u.name for u in []] for m in []}
mu={}
for m in bpy.data.materials:
    objs=[o.name for o in bpy.data.objects if any(s.material==m for s in o.material_slots)]
    meshes=[me.name for me in bpy.data.meshes if m.name in [x.name if x else None for x in me.materials]]
    mu[m.name]={'objects':objs[:6],'n_objects':len(objs),'meshes':meshes[:4],'fake':m.use_fake_user,'users':m.users}
r['mat_users']=mu
img=bpy.data.images['missing_tex.png']
used_in=[]
for m in bpy.data.materials:
    if m.use_nodes and m.node_tree:
        for n in m.node_tree.nodes:
            if n.type=='TEX_IMAGE' and n.image==img: used_in.append(m.name)
r['img_used_by_mats']=used_in
r['meshes_debug_only']=[me.name for me in bpy.data.meshes if me.users==0]
r['d05_flags']=[(o.name,o.hide_viewport,o.hide_render,[vl.name for vl in bpy.context.scene.view_layers if o.hide_get(view_layer=vl)]) for o in bpy.data.collections['District05'].all_objects if o.hide_viewport or o.hide_render or any(o.hide_get(view_layer=vl) for vl in bpy.context.scene.view_layers)]
r['tmp_parents']=[(c.name,[p.name for p in bpy.data.collections if c.name in [x.name for x in p.children]]) for c in bpy.data.collections if c.name in ('Tmp_A','Tmp_B')]
" 2>&1 | head -150
```

```
{
  "file": "work.blend",
  "status": "ok",
  "result": "none (the code did not assign result)",
  "stdout": "",
  "modified": false,
  "saved": "none (no changes)",
  "duration_ms": 508
}
```

**Assistant:**

The script didn't assign `result`, so nothing came back. Rerunning with that fixed.

## Step 8 · Bash · 2186 ms

```
blender-axi exec work.blend --allow-exec --json --code "
import bpy
r={}
r['debug_mesh']=[(o.name, o.data.name if o.data else None, (o.data.users if o.data else None), [s.material.name if s.material else None for s in o.material_slots]) for o in bpy.data.objects if o.name.startswith('Debug_')]
mu={}
for m in bpy.data.materials:
    objs=[o.name for o in bpy.data.objects if any(s.material==m for s in o.material_slots)]
    mu[m.name]={'n_objects':len(objs),'objects':objs[:4],'fake':m.use_fake_user,'users':m.users}
r['mat_users']=mu
img=bpy.data.images['missing_tex.png']
used_in=[]
for m in bpy.data.materials:
    if m.use_nodes and m.node_tree:
        for n in m.node_tree.nodes:
            if n.type=='TEX_IMAGE' and n.image==img: used_in.append(m.name)
r['img_used_by_mats']=used_in
r['d05_flags']=[(o.name,o.hide_viewport,o.hide_render,[vl.name for vl in bpy.context.scene.view_layers if o.hide_get(view_layer=vl)]) for o in bpy.data.collections['District05'].all_objects if o.hide_viewport or o.hide_render or any(o.hide_get(view_layer=vl) for vl in bpy.context.scene.view_layers)]
r['tmp_parents']=[(c.name,[p.name for p in bpy.data.collections if c.name in [x.name for x in p.children]], len(c.objects)) for c in bpy.data.collections if c.name in ('Tmp_A','Tmp_B')]
result=r
" 2>&1 | head -200
```

```
{
  "file": "work.blend",
  "status": "ok",
  "result": {
    "debug_mesh": [
      [
        "Debug_Marker01",
        "Cube.400",
        1,
        []
      ],
      [
        "Debug_Marker02",
        "Cube.401",
        1,
        []
      ],
      [
        "Debug_Marker03",
        "Cube.402",
... (180 more lines)
```

**Assistant:**

The output was cut off at 200 lines. The Debug objects have no material slots, and their meshes (`Cube.400`–`402`) have one user each, so deleting the objects leaves those meshes orphaned. I still need the rest of the material list and the image and collection details.

## Step 9 · Bash · 2686 ms

```
blender-axi exec work.blend --allow-exec --json --code "
import bpy
r={}
mu={}
for m in bpy.data.materials:
    if m.name.startswith('Facade') or m.name.startswith('Tree'): continue
    objs=[o.name for o in bpy.data.objects if any(s.material==m for s in o.material_slots)]
    mu[m.name]={'n_objects':len(objs),'objects':objs[:4],'fake':m.use_fake_user,'users':m.users}
r['other_mats']=mu
r['all_mat_names']=[m.name for m in bpy.data.materials]
img=bpy.data.images['missing_tex.png']
used_in=[]
for m in bpy.data.materials:
    if m.use_nodes and m.node_tree:
        for n in m.node_tree.nodes:
            if n.type=='TEX_IMAGE' and n.image==img: used_in.append(m.name)
r['img_used_by_mats']=used_in
r['d05_flags']=[(o.name,o.hide_viewport,o.hide_render,[vl.name for vl in bpy.context.scene.view_layers if o.hide_get(view_layer=vl)]) for o in bpy.data.collections['District05'].all_objects if o.hide_viewport or o.hide_render or any(o.hide_get(view_layer=vl) for vl in bpy.context.scene.view_layers)]
r['tmp_parents']=[(c.name,[p.name for p in bpy.data.collections if c.name in [x.name for x in p.children]], len(c.objects)) for c in bpy.data.collections if c.name in ('Tmp_A','Tmp_B')]
result=r
" 2>&1 | head -120
```

```
{
  "file": "work.blend",
  "status": "ok",
  "result": {
    "other_mats": {
      "OldConcrete": {
        "n_objects": 0,
        "objects": [],
        "fake": true,
        "users": 1
      },
      "OldGlass": {
        "n_objects": 0,
        "objects": [],
        "fake": true,
        "users": 1
      },
      "OldMetal": {
        "n_objects": 0,
        "objects": [],
... (100 more lines)
```

**Assistant:**

I now have the full picture:

- **Materials:** 34 total. `Facade00`–`29` are in use. `OldConcrete`, `OldGlass`, `OldMetal`, `OldPaint` are fake-user-only with zero objects. Those four are the unused materials. The 103 audit "materials" items are objects with no material slot (3 Debug markers plus 100 Tree objects). That's not a material cleanup target, so I'll leave it alone.
- **Image:** `missing_tex.png` isn't referenced by any material node.
- **Empty collections:** `Tmp_A` and `Tmp_B`, both top-level.
- **District05:** only the three render-off buildings need fixing.

Now I'll apply the cleanup to a copy saved as `out/clean.blend`. `work.blend` stays untouched because `--out` is used and `--in-place` is not.

## Step 10 · Bash · 2027 ms

```
blender-axi exec work.blend --allow-exec --json --out out/clean.blend --code "
import bpy
rep={}
dbg=[o for o in bpy.data.objects if o.name.startswith('Debug_')]
meshes=[o.data for o in dbg if o.type=='MESH' and o.data]
rep['deleted_objects']=[o.name for o in dbg]
for o in dbg:
    bpy.data.objects.remove(o, do_unlink=True)
rep['orphan_meshes_removed']=[]
for me in meshes:
    if me.users==0:
        rep['orphan_meshes_removed'].append(me.name)
        bpy.data.meshes.remove(me)
c=bpy.data.collections['District05']
fixed=[]
for o in c.all_objects:
    changed=[]
    if o.hide_viewport:
        o.hide_viewport=False; changed.append('viewport')
    if o.hide_render:
        o.hide_render=False; changed.append('render')
    for vl in bpy.context.scene.view_layers:
        if o.hide_get(view_layer=vl):
            o.hide_set(False, view_layer=vl); changed.append('view_layer:'+vl.name)
    if changed: fixed.append([o.name]+changed)
rep['district05_fixed']=fixed
empties=[cc.name for cc in bpy.data.collections if len(cc.objects)==0 and len(cc.children)==0]
for n in empties:
    bpy.data.collections.remove(bpy.data.collections[n])
rep['empty_collections_removed']=empties
unused=[m.name for m in bpy.data.materials if m.users-(1 if m.use_fake_user else 0)==0]
for n in unused:
    m=bpy.data.materials[n]
    m.use_fake_user=False
    bpy.data.materials.remove(m)
rep['materials_removed']=unused
img=bpy.data.images.get('missing_tex.png')
rep['image_removed']=None
if img is not None and img.source=='FILE' and not img.packed_file:
    rep['image_removed']=img.name
    bpy.data.images.remove(img)
rep['after']={'objects':len(bpy.data.objects),'collections':len(bpy.data.collections),'materials':len(bpy.data.materials),'images':len(bpy.data.images)}
result=rep
" 2>&1
```

```
Exit code 1
{
  "error": "AttributeError: 'NoneType' object has no attribute 'hide_viewport' (line 18 of the exec code)",
  "code": "EXEC_FAILED",
  "help": [
    "Rerun with `--debug` for the traceback"
  ]
}
```

(tool result flagged as an error)

**Assistant:**

The script failed before saving. The Debug markers were removed while the `District05` iterator still held them, so a stale `None` came back. Nothing was written. I'll capture the District05 names before deleting, then rerun.

## Step 11 · Bash · 2371 ms

```
ls -la <tmp>/runs/qa_cleanup/axi/r1/out; blender-axi exec work.blend --allow-exec --json --out out/clean.blend --code "
import bpy
rep={}
c=bpy.data.collections['District05']
d5names=[o.name for o in c.all_objects]
dbg=[o for o in bpy.data.objects if o.name.startswith('Debug_')]
meshes=[o.data for o in dbg if o.type=='MESH' and o.data]
rep['deleted_objects']=[o.name for o in dbg]
for o in dbg:
    bpy.data.objects.remove(o, do_unlink=True)
rep['orphan_meshes_removed']=[]
for me in meshes:
    if me.users==0:
        rep['orphan_meshes_removed'].append(me.name)
        bpy.data.meshes.remove(me)
fixed=[]
for nm in d5names:
    if nm.startswith('Debug_'): continue
    o=bpy.data.objects.get(nm)
    if o is None: continue
    changed=[]
    if o.hide_viewport:
        o.hide_viewport=False; changed.append('viewport')
    if o.hide_render:
        o.hide_render=False; changed.append('render')
    for vl in bpy.context.scene.view_layers:
        if o.hide_get(view_layer=vl):
            o.hide_set(False, view_layer=vl); changed.append('view_layer:'+vl.name)
    if changed: fixed.append([o.name]+changed)
rep['district05_fixed']=fixed
rep['district05_object_count']=len(d5names)-len(dbg)
empties=[cc.name for cc in bpy.data.collections if len(cc.objects)==0 and len(cc.children)==0]
for n in empties:
    bpy.data.collections.remove(bpy.data.collections[n])
rep['empty_collections_removed']=empties
unused=[m.name for m in bpy.data.materials if m.users-(1 if m.use_fake_user else 0)==0]
for n in unused:
    m=bpy.data.materials[n]
    m.use_fake_user=False
    bpy.data.materials.remove(m)
rep['materials_removed']=unused
img=bpy.data.images.get('missing_tex.png')
rep['image_removed']=None
if img is not None and img.source=='FILE' and not img.packed_file:
    rep['image_removed']=img.name
    bpy.data.images.remove(img)
rep['after']={'objects':len(bpy.data.objects),'collections':len(bpy.data.collections),'materials':len(bpy.data.materials),'images':len(bpy.data.images)}
result=rep
" 2>&1
```

```
total 4
drwxr-xr-x 1 <user> 197121 0 Nov  4 02:05 .
drwxr-xr-x 1 <user> 197121 0 Nov  4 02:05 ..
{
  "file": "work.blend",
  "status": "ok",
  "result": {
    "deleted_objects": [
      "Debug_Marker01",
      "Debug_Marker02",
      "Debug_Marker03"
    ],
    "orphan_meshes_removed": [
      "Cube.400",
      "Cube.401",
      "Cube.402"
    ],
    "district05_fixed": [
      [
        "D05_Bldg02",
... (38 more lines)
```

**Assistant:**

The cleanup saved to `out/clean.blend`. The result shows 20 non-debug objects in District05, not 23; the earlier 23 included the three Debug markers. Next I'll verify the output with the audit and a read-only check, and confirm `work.blend` is unchanged.

## Step 12 · Bash · 6375 ms

```
ls -la out work.blend && blender-axi audit out/clean.blend 2>&1 | head -30; blender-axi scene out/clean.blend 2>&1; blender-axi exec out/clean.blend --allow-exec --json --code "
import bpy
c=bpy.data.collections['District05']
objs=list(c.all_objects)
bad=[o.name for o in objs if o.hide_viewport or o.hide_render or any(o.hide_get(view_layer=vl) for vl in bpy.context.scene.view_layers)]
result={'d05':len(objs),'d05_not_visible_or_renderable':bad,'debug':[o.name for o in bpy.data.objects if o.name.startswith('Debug_')],'empty_cols':[x.name for x in bpy.data.collections if not x.objects and not x.children],'mats':[m.name for m in bpy.data.materials if m.name.startswith('Old')],'image ...
" 2>&1 | head -40
```

```
-rw-r--r-- 1 <user> 197121 363004 Nov  4 02:05 work.blend

out:
total 352
drwxr-xr-x 1 <user> 197121      0 Nov  4 02:06 .
drwxr-xr-x 1 <user> 197121      0 Nov  4 02:05 ..
-rw-r--r-- 1 <user> 197121 355925 Nov  4 02:06 clean.blend
file: out/clean.blend
checked: "11 checks, 502 objects"
findings[3]{check,level,count,items}:
  scale,warn,400,"D00_Bldg00 (0.9,0.9,4.846), D00_Bldg01 (0.9,0.9,1.958), D00_Bldg02 (0.9,0.9,7.048), D00_Bldg03 (0.9,0.9,5.945), D00_Bldg04 (0.9,0.9,5.779), +395 more"
  materials,warn,100,"Tree00, Tree00_Crown, Tree01, Tree01_Crown, Tree02, +95 more"
  ngons,info,100,"Tree00 (2 faces), Tree00_Crown (1 face), Tree01 (2 faces), Tree01_Crown (1 face), Tree02 (2 faces), +95 more"
status: "3 findings (2 warn, 1 info)"
duration_ms: 23
help[3]:
  - "Run `blender-axi material Tree00 <file> --name Mat --color 0.8,0.8,0.8 --in-place` to give it a material"
  - Run `blender-axi audit <file> --full` to list every item
  - "Apply scale (Ctrl+A) and clean up meshes in Blender, or via exec --allow-exec"
file: out/clean.blend
... (29 more lines)
```

**Assistant:**

The output checks out. `work.blend` is unchanged (same size and timestamp), and the cleaned file has no Debug objects, no empty collections, no Old materials, no image, and all 20 District05 buildings are visible and renderable. Now I'll write the report.

## Step 13 · Write · 23 ms

```
{
 "file_path": "<tmp>\\runs\\qa_cleanup\\axi\\r1\\out\\report.md",
 "content": "# work.blend cleanup report\n\nSource: `work.blend` (unchanged)\nOutput: `out/clean.blend`\n\n## Before / after counts\n\n| Item | Before | After | Change |\n|---|---|---|---|\n| Objects | 505 (MESH 503, CAMERA 1, LIGHT 1) | 502 (MESH 500, CAMERA 1, LIGHT 1) | -3 |\n| Collections (incl. ...
}
```

```
File created successfully at: <tmp>\runs\qa_cleanup\axi\r1\out\report.md (file state is current in your context — no need to Read it back)
```

**Assistant:**

The cleanup is saved to `out/clean.blend`, and `out/report.md` is written. `work.blend` is unchanged.

**What I changed**
- **Debug_ objects:** deleted `Debug_Marker01`, `02`, `03`. Their orphaned meshes went with them.
- **District05:** three buildings had render turned off (`D05_Bldg02`, `D05_Bldg09`, `D05_Bldg15`). All 20 District05 objects are now visible and renderable.
- **Empty collections:** removed `Tmp_A` and `Tmp_B`.
- **Unused materials:** removed `OldConcrete`, `OldGlass`, `OldMetal`, `OldPaint`.
- **Missing image:** removed `missing_tex.png`. No material used it.

| | Before | After |
|---|---|---|
| Objects | 505 | 502 |
| Collections | 24 | 22 |
| Materials | 34 | 30 |
| Images | 1 | 0 |

**Left alone, as you asked:** 400 objects with unapplied scale, 100 Tree objects with no material slot, and n-gons on 100 Tree meshes. The audit still reports these after cleanup.

**Judgment calls to check**
- The four `Old*` materials had only a fake user. No object used them, so I treated them as unused and cleared the fake user before removing them. If they were meant to stay as library assets, they can be restored from `work.blend`.
- The Debug markers were hidden and render-off, so deleting them was the only option. Deleting them also removed the 103 "missing material" audit items that were Debug markers, which brought that count to 100.

**Process notes**
- The first cleanup attempt failed on a stale object reference and wrote nothing. The rerun succeeded.
- I used `blender-axi exec` for the cleanup and the read-only checks because the CLI has no commands for removing collections, materials, or images. The read-only runs saved no files. The CLI may still use its own temp directories under `.tmp/` by default.
