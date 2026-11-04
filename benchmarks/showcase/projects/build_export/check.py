"""build_export judge (plain bpy, never imports blender_axi).

    blender --background --factory-startup --python check.py -- <out_dir> [<start.blend> | -]

Opens `<out_dir>/lamp.blend`, reads `lamp.png` (IHDR) and `lamp.glb` (JSON chunk). Prints one `CHECK {json}` line.
Checks: lamp_blend, meshes (>= 4), camera, light, bevel, materials (>= 2), emission, settings (EEVEE, 640x360, 16
samples), png (>= 640 wide), glb_magic, glb_meshes (>= 4), glb_clean (no camera node, no punctual light).
"""

import json
import struct
import sys
from pathlib import Path

MIN_MESHES, MIN_MATERIALS, MIN_WIDTH = 4, 2, 640


def png_width(path):
    try:
        head = Path(path).read_bytes()[:24]
    except OSError:
        return 0
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return 0
    return int.from_bytes(head[16:20], "big")


def glb_json(path):
    """(magic ok, parsed JSON chunk or {}) of a binary glTF file."""
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return False, {}
    if len(raw) < 20 or raw[:4] != b"glTF":
        return False, {}
    length, kind = struct.unpack("<II", raw[12:20])
    if kind != 0x4E4F534A:  # "JSON"
        return True, {}
    try:
        return True, json.loads(raw[20:20 + length].decode("utf-8"))
    except ValueError:
        return True, {}


def has_emission(bpy):
    """True when a material emits: Principled `Emission Strength` > 0, or an Emission node with Strength > 0."""
    for mat in bpy.data.materials:
        if not mat.use_nodes or mat.node_tree is None:
            continue
        for node in mat.node_tree.nodes:
            if node.type == "BSDF_PRINCIPLED":
                sock = node.inputs.get("Emission Strength")
                if sock is not None and sock.default_value > 0:
                    return True
            elif node.type == "EMISSION":
                sock = node.inputs.get("Strength")
                if sock is not None and sock.default_value > 0:
                    return True
    return False


def main():
    import bpy

    argv = sys.argv[sys.argv.index("--") + 1:]
    out_dir = Path(argv[0])
    lamp = out_dir / "lamp.blend"
    checks, notes = {}, []

    checks["lamp_blend"] = lamp.is_file() and lamp.stat().st_size > 0
    opened = False
    if checks["lamp_blend"]:
        try:
            bpy.ops.wm.open_mainfile(filepath=str(lamp))
            opened = True
        except Exception as exc:
            notes.append(f"lamp.blend does not open: {str(exc)[:160]}")
    if opened:
        objs = list(bpy.data.objects)
        meshes = [o for o in objs if o.type == "MESH"]
        checks["meshes"] = len(meshes) >= MIN_MESHES
        checks["camera"] = any(o.type == "CAMERA" for o in objs)
        checks["light"] = any(o.type == "LIGHT" for o in objs)
        checks["bevel"] = any(m.type == "BEVEL" for o in meshes for m in o.modifiers)
        used = [m for m in bpy.data.materials if m.users > 0]
        checks["materials"] = len(used) >= MIN_MATERIALS
        checks["emission"] = has_emission(bpy)
        scene = bpy.context.scene
        r = scene.render
        eevee = r.engine.startswith("BLENDER_EEVEE")
        samples = getattr(scene.eevee, "taa_render_samples", None)
        checks["settings"] = bool(eevee and (r.resolution_x, r.resolution_y) == (640, 360) and samples == 16)
        notes.append(f"{len(meshes)} meshes, {len(used)} materials, engine {r.engine}, "
                     f"{r.resolution_x}x{r.resolution_y}, {samples} samples")
    else:
        for key in ("meshes", "camera", "light", "bevel", "materials", "emission", "settings"):
            checks[key] = False

    width = png_width(out_dir / "lamp.png")
    checks["png"] = width >= MIN_WIDTH
    magic, doc = glb_json(out_dir / "lamp.glb")
    checks["glb_magic"] = magic
    checks["glb_meshes"] = len(doc.get("meshes", [])) >= MIN_MESHES
    nodes = doc.get("nodes", [])
    punctual = "KHR_lights_punctual" in doc.get("extensions", {}) or any(
        "KHR_lights_punctual" in n.get("extensions", {}) for n in nodes)
    checks["glb_clean"] = magic and bool(doc) and not any("camera" in n for n in nodes) and not punctual
    notes.append(f"lamp.png width {width}; glb meshes {len(doc.get('meshes', []))}, nodes {len(nodes)}")
    print("CHECK " + json.dumps({"passed": all(checks.values()), "checks": checks, "notes": notes}))


main()
