# turntable · axi · repeat 1

model claude-sonnet-5-5 · effort medium · cost $0.0428 (estimate) · turns 5 · stop end_turn · check passed

## Step 1 · Bash · 3268 ms

```
blender-axi render --help; blender-axi object Mug work.blend; ls
```

```
usage: "blender-axi render [<file>] [flags]"
summary: "Render a frame, frame range or turntable (png or mp4) of a .blend to image files"
positionals[1]{name,required,help}:
  file,false,"The .blend file (default: the single .blend in the cwd)"
flags[18]{flag,default,help}:
  "--frame <n>","","Frame to render (default: the scene's current frame)"
  "--frames <text>","","Frame range <a..b>, inclusive; writes one file per frame"
  "--out <path>","",Output path; `#` runs are the frame number (default ./<stem>_f####.png)
  "--engine <WORKBENCH|EEVEE|CYCLES>","","Render engine (default: the scene's own)"
  "--resolution <text>","","Output size <width>x<height>, e.g. 1280x720"
  "--percent <n>","","Resolution percentage, e.g. 50"
  "--samples <n>","","Sample count (CYCLES, EEVEE)"
  "--camera <text>","","Camera object name (default: the scene camera)"
  "--fit","",Move the camera back along its view axis so every visible object is in frame (the file is not changed)
  "--fit-margin <n>","1.1","Extra room around the fitted objects, 1 = tight"
  "--auto-light","","If the scene is unlit, render with a temporary Sun and world fill; nothing is saved"
  "--turntable <n>","","Render N stills of the current frame with the camera orbited 360 degrees around the visible geometry (name_####.png, or one mp4 with --out name.mp4); the file is not changed"
  "--subject <a,b>","","Objects to orbit and fit around, comma-separated (default: all visible geometry minus a ground plane)"
  "--sheet","","Write one contact-sheet image (grid of all frames, each tile numbered) instead of N files (--turntable or --frames; --out name.png, .jpg, .webp, .exr, .tif, .bmp or .tga)"
  "--labels","true",Number the sheet tiles (default); --no-labels writes the grid without numbers
... (38 more lines)
```

## Step 2 · Bash · 6 ms

```
B="blender-axi render work.blend --engine WORKBENCH --subject Mug,MugHandle"
$B --turntable 12 --fit --sheet --resolution 480x480 --out out/sheet.png 2>&1 | head -20
$B --turntable 12 --fit --resolution 480x480 --fps 12 --out out/turntable.mp4 2>&1 | head -20
ls out
```

```
A variable in this command can't be checked before it runs
```

(tool result flagged as an error)

## Step 3 · Bash · 4844 ms

```
blender-axi render work.blend --engine WORKBENCH --subject Mug,MugHandle --turntable 12 --fit --sheet --resolution 480x480 --out out/sheet.png 2>&1 | head -20
blender-axi render work.blend --engine WORKBENCH --subject Mug,MugHandle --turntable 12 --fit --resolution 480x480 --fps 12 --out out/turntable.mp4 2>&1 | head -20
ls out
```

```
rendered: 12 frames -> 1 sheet
output: out/sheet.png
size: 480x480
engine: WORKBENCH
samples: n/a
fit: "Camera moved 6.4,-6.8,4.6 -> 1.377,-0.681,2.113"
subject: "2 named (Mug, MugHandle)"
aim: "Camera turned 68.918,0,43.264 -> 72.944,0,39.168 toward 0.698,0.2,1.83"
turntable: "12 steps around 0.698,0.2,1.83 (Camera)"
sheet: "4x3 tiles, 1920x1440, 2244 KB"
duration_ms: 1040
help[3]:
  - "Run `blender-axi set Camera work.blend --location 1.377,-0.681,2.113 --rotation 72.944,0,39.168 --in-place` to keep this framing"
  - Run `blender-axi render <file> --turntable 12 --fit --sheet --out <sheet.png>` for a finer turn
  - Run `blender-axi export <file> --out <path.glb>` to export geometry
rendered: 12 frames -> 1 video
output: out/turntable.mp4
size: 480x480
engine: WORKBENCH
samples: n/a
... (12 more lines)
```

## Step 4 · Bash · 2172 ms

```
blender-axi render work.blend --engine WORKBENCH --subject Mug,MugHandle --fit --resolution 960x720 --out out/hero.png 2>&1 | head -8; ls out
```

```
rendered: 1 frame
output: out/hero.png
size: 960x720
engine: WORKBENCH
samples: n/a
fit: "Camera moved 6.4,-6.8,4.6 -> 1.49,-0.683,2.236"
subject: "2 named (Mug, MugHandle)"
duration_ms: 613
hero.png
sheet.png
turntable.mp4
```

**Assistant:**

All three product shots are in `out/`, rendered with WORKBENCH. I didn't open the images, so I haven't checked the framing by eye.

- **`out/sheet.png`:** a numbered 4×3 contact sheet of the 12 turntable frames, 1920×1440.
- **`out/turntable.mp4`:** the same 12 frames as H264 at 12 fps, 480×480.
- **`out/hero.png`:** a 960×720 still from the scene camera's existing angle, which looks down at the Mug from front-left. I took that as the three-quarter view and didn't try other angles.

The turntable orbits only `Mug` and its child `MugHandle`, so the floor is ignored. I named the handle explicitly so it stays in frame. Each render took about a second.

All the camera moves were temporary, so `work.blend` is unchanged. I wrote nothing outside `out/`.
