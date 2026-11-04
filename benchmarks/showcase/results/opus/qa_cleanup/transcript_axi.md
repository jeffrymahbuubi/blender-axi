# qa_cleanup · axi · repeat 1

model claude-opus-5-5 · effort medium · cost $0.1227 (estimate) · turns 6 · stop end_turn · check passed

## Step 1 · Bash · 4416 ms

```
cd <tmp>/runs/qa_cleanup/axi/r1 && ls; ls out; blender-axi audit work.blend; blender-axi scene work.blend
```

```
out
snapshots
stderr.txt
stream.jsonl
work.blend
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
duration_ms: 16
help[3]:
  - "Run `blender-axi material Debug_Marker01 <file> --name Mat --color 0.8,0.8,0.8 --in-place` to give it a material"
  - Run `blender-axi audit <file> --full` to list every item
  - "Apply scale (Ctrl+A) and clean up meshes in Blender, or via exec --allow-exec"
... (14 more lines)
```

## Step 2 · Bash · 3018 ms

```
cd <tmp>/runs/qa_cleanup/axi/r1 && blender-axi audit work.blend --full --json > /dev/null; blender-axi exec work.blend --allow-exec --code "
d=bpy.data
result={'debug':[o.name for o in d.objects if o.name.startswith('Debug_')],
'd05':[(o.name,o.hide_viewport,o.hide_render,o.hide_get(),o.hide_select) for o in d.collections['District05'].all_objects if o.hide_viewport or o.hide_render or o.hide_get()],
'd05coll':(d.collections['District05'].hide_viewport,d.collections['District05'].hide_render),
'empty':[c.name for c in d.collections if not c.objects and not c.children],
'unused_mats':[(m.name,m.users,m.use_fake_user) for m in d.materials if m.users==0 or (m.users==1 and m.use_fake_user)],
'images':[(i.name,i.filepath,i.users,i.source) for i in d.images],
'counts':[len(d.objects),len(d.collections),len(d.materials),len(d.images)]}
"
```

```
file: work.blend
status: ok
result:
  debug[3]: Debug_Marker01,Debug_Marker02,Debug_Marker03
  d05[6]:
    - [5]: D05_Bldg02,false,true,false,false
    - [5]: D05_Bldg09,false,true,false,false
    - [5]: D05_Bldg15,false,true,false,false
    - [5]: Debug_Marker01,true,true,false,false
    - [5]: Debug_Marker02,true,true,false,false
    - [5]: Debug_Marker03,true,true,false,false
  d05coll[2]: false,false
  empty[2]: Tmp_A,Tmp_B
  unused_mats[4]:
    - [3]: OldConcrete,1,true
    - [3]: OldGlass,1,true
    - [3]: OldMetal,1,true
    - [3]: OldPaint,1,true
  images[1]:
    - [4]: missing_tex.png,"//textures\\missing_tex.png",1,FILE
... (5 more lines)
```

## Step 3 · Bash · 2279 ms

```
cd <tmp>/runs/qa_cleanup/axi/r1 && blender-axi exec --help | head -30; blender-axi exec work.blend --allow-exec --code "
d=bpy.data
img=d.images['missing_tex.png']
result={'fake':img.use_fake_user,'mat_users':[m.name for m in d.materials if m.node_tree and any(getattr(n,'image',None)==img for n in m.node_tree.nodes)],
'lc':[(l.name,l.exclude,l.hide_viewport) for vl in bpy.context.scene.view_layers for l in [vl.layer_collection.children.get('District05')] if l]}"
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
... (15 more lines)
```

## Step 4 · Bash · 6617 ms

```
cd <tmp>/runs/qa_cleanup/axi/r1 && blender-axi exec work.blend --allow-exec --out out/clean.blend --code "
d=bpy.data
deleted=[o.name for o in d.objects if o.name.startswith('Debug_')]
for n in deleted: d.objects.remove(d.objects[n], do_unlink=True)
shown=[]
for o in d.collections['District05'].all_objects:
    if o.hide_viewport or o.hide_render or o.hide_get():
        o.hide_viewport=False; o.hide_render=False; o.hide_set(False); shown.append(o.name)
colls=[c.name for c in d.collections if not c.objects and not c.children]
for n in colls: d.collections.remove(d.collections[n])
mats=[m.name for m in d.materials if m.users==0 or (m.users==1 and m.use_fake_user)]
for n in mats: d.materials.remove(d.materials[n])
imgs=[i.name for i in d.images if i.source=='FILE' and not __import__('os').path.exists(bpy.path.abspath(i.filepath))]
for n in imgs: d.images.remove(d.images[n])
result={'deleted':deleted,'shown':shown,'colls':colls,'mats':mats,'imgs':imgs,'counts':[len(d.objects),len(d.collections),len(d.materials),len(d.images)]}"
blender-axi audit out/clean.blend; blender-axi diff work.blend out/clean.blend; blender-axi scene out/clean.blend
```

```
file: work.blend
status: ok
result:
  deleted[3]: Debug_Marker01,Debug_Marker02,Debug_Marker03
  shown[3]: D05_Bldg02,D05_Bldg09,D05_Bldg15
  colls[2]: Tmp_A,Tmp_B
  mats[4]: OldConcrete,OldGlass,OldMetal,OldPaint
  imgs[1]: missing_tex.png
  counts[4]: 502,21,30,0
stdout: ""
modified: true
saved: out/clean.blend
duration_ms: 539
help[1]:
  - "Inspect the result: `blender-axi objects out/clean.blend`"
file: out/clean.blend
checked: "11 checks, 502 objects"
findings[3]{check,level,count,items}:
  scale,warn,400,"D00_Bldg00 (0.9,0.9,4.846), D00_Bldg01 (0.9,0.9,1.958), D00_Bldg02 (0.9,0.9,7.048), D00_Bldg03 (0.9,0.9,5.945), D00_Bldg04 (0.9,0.9,5.779), +395 more"
  materials,warn,100,"Tree00, Tree00_Crown, Tree01, Tree01_Crown, Tree02, +95 more"
... (36 more lines)
```

## Step 5 · Write · 24 ms

```
{
 "file_path": "<tmp>\\runs\\qa_cleanup\\axi\\r1\\out\\report.md",
 "content": "# Cleanup report: `work.blend` → `out/clean.blend`\n\n## Before / after counts\n\n| Item | Before | After |\n|---|---|---|\n| Objects | 505 (503 mesh, 1 camera, 1 light) | 502 (500 mesh, 1 camera, 1 light) |\n| Collections (excluding Scene Collection) | 23 | 21 |\n| Materials | 34 | 30  ...
}
```

```
File created successfully at: <tmp>\runs\qa_cleanup\axi\r1\out\report.md (file state is current in your context — no need to Read it back)
```

**Assistant:**

I've saved the cleaned scene as `out/clean.blend` and the before/after summary as `out/report.md`. `work.blend` wasn't changed. Comparing the two files shows only the intended changes, and the scene settings are the same.

| | Before | After |
|---|---|---|
| Objects | 505 | 502 |
| Collections (not counting the top-level Scene Collection) | 23 | 21 |
| Materials | 34 | 30 |
| Images | 1 | 0 |

What I fixed:
- **Debug objects:** deleted `Debug_Marker01`, `Debug_Marker02` and `Debug_Marker03`. All three were in `District05`.
- **`District05`:** `D05_Bldg02`, `D05_Bldg09` and `D05_Bldg15` were set not to render; they are now visible and renderable. The collection itself was already visible.
- **Empty collections:** removed `Tmp_A` and `Tmp_B`.
- **Unused materials:** removed `OldConcrete`, `OldGlass`, `OldMetal` and `OldPaint`. The audit didn't flag these, because a "fake user" setting kept each one in the file even though nothing used it. I found them by checking separately.
- **Missing image:** removed `missing_tex.png`, which pointed to a file that doesn't exist.

The audit also found problems you didn't ask me to fix, so they're still in the file and listed in the report:
- 400 building objects have scale that hasn't been applied.
- 100 tree meshes have no material.
- 100 tree meshes have faces with more than four sides (n-gons).
