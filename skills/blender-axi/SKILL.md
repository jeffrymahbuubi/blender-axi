---
name: blender-axi
description: Inspect, render, export and script Blender (.blend) files from the terminal with the blender-axi CLI. Use when asked what is in a .blend file, to list its objects, render a still or frames, export glb/fbx/obj/stl, run a Blender Python script headlessly, create a new .blend, add, move or delete objects and assign materials with typed commands (no exec gate), or run Python inside an open Blender (`--live`).
---

# blender-axi

Agent-ergonomic CLI for Blender: inspect, render, export and script .blend files headlessly.

Run `blender-axi` with no arguments for the live home view: Blender location, live-session reachability and the `.blend` files in the current directory. Output is TOON on stdout (use `--json` for JSON); every response ends with `help` next steps.

Install with `uv tool install git+https://github.com/jeffrymahbuubi/blender-axi` (or run once with `uvx --from git+https://github.com/jeffrymahbuubi/blender-axi blender-axi ...`); from a checkout use `.venv/Scripts/blender-axi`.

## Commands

| Command | What it does | Example |
|---|---|---|
| `scene` | Overview of a .blend file: engine, resolution, frames, camera, object and collection counts | `blender-axi scene scene.blend` |
| `new` | Create a new .blend file (empty, or with Blender's default Camera, Cube and Light) | `blender-axi new scene.blend` |
| `add` | Add a primitive, camera, light or empty to a .blend (or the open session with --live) | `blender-axi add cube scene.blend --name Box --location 0,0,1 --in-place` |
| `set` | Rename, move, rotate, scale, reparent, re-collect, hide or show one object in a .blend (or the open session with --live) | `blender-axi set Cube scene.blend --location 1,2,3 --rotation 0,0,90 --in-place` |
| `delete` | Delete objects from a .blend (or the open session with --live); children stay in place | `blender-axi delete Cube scene.blend --in-place` |
| `material` | Give an object a material (Principled BSDF) in a .blend (or the open session with --live) | `blender-axi material Cube scene.blend --name Red --color 1,0,0 --in-place` |
| `objects` | List objects in a .blend file (name, type, collection, visible) with optional filters and columns | `blender-axi objects scene.blend` |
| `object` | Details of one object: transform, dimensions, parent/children, materials, modifiers, custom props | `blender-axi object Cube scene.blend` |
| `render` | Render a frame or frame range of a .blend to image files | `blender-axi render scene.blend --engine WORKBENCH --resolution 640x360 --out preview.png` |
| `export` | Export a scene, collection or objects to glb, fbx, obj, stl, ply, usd or abc | `blender-axi export scene.blend --out model.glb` |
| `exec` | Run Python inside Blender on a .blend, or the open session with --live (requires --allow-exec; original only changed with --in-place) | `blender-axi exec scene.blend --code "result={'n': len(bpy.data.objects)}" --allow-exec` |
| `skill` | Agent skill file: `skill gen` writes it, `skill gen --check` verifies it | `blender-axi skill gen` |
| `live` | Check a running Blender (MCP add-on server) and show the open file; use --live on other commands | `blender-axi live` |

Run `blender-axi <command> --help` for flags and examples.

## Global flags

- `--help`: Show help for the command
- `--json`: Emit JSON instead of TOON
- `--full`: Do not truncate long text fields
- `--debug`: Print tracebacks and Blender stderr to stderr
- `--keep-tmp`: Keep per-run temp directories under .tmp/
- `--blender <path>`: Path to the Blender executable (or set BLENDER_AXI_BLENDER)
- `--user-prefs`: Headless only: load your preferences and add-ons instead of --factory-startup
- `--timeout <n>`: Timeout in seconds (default 120, render 600, live 300)
- `--live`: Talk to a running Blender instead of starting one
- `--host <text>`: Live-mode host
- `--port <n>`: Live-mode port

## Safety

- Read-only commands never modify the `.blend`; Blender runs headless with `--factory-startup`.
- Outputs are never overwritten without `--force` (`WRITE_REFUSED`, exit 1).
- `exec` refuses to run without `--allow-exec` (`EXEC_NOT_ALLOWED`, exit 2); it works on a copy unless `--in-place` is given.
- With `--live`, `exec` changes the open session directly (no copy); `--in-place` saves it.
- Modeling commands (`add`, `set`, `delete`, `material`) write a file only with `--in-place` (a `.blend1` backup is kept) or `--out <copy.blend>`; with `--live` they change the open session. For a whole scene prefer `new` + `exec --script`.
- `--user-prefs` loads your add-ons; the default is `--factory-startup`.
- Temp files stay under the project-local `.tmp/` (or `BLENDER_AXI_TMP`), never the system temp.
- Errors are data on stdout with a `code` and `help`; exit 2 means a usage problem, 1 a runtime failure.
