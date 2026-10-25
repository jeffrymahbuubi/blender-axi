"""The measured tasks per scene (plan section 4.1).

Each `Task` pairs a blender-axi command with the official-MCP tool call that answers the same question. The runner
appends `--live --host 127.0.0.1 --port N` to `axi`. An identical `(tool, args)` within one scene is called once.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Task:
    id: str                          # file-safe: "objects", "object_mug"
    label: str                       # table text
    axi: tuple[str, ...]             # args after `python -m blender_axi`
    mcp: tuple[str, dict] | None     # (tool, arguments); None = blender-axi-only row
    note: str = ""                   # shown in the table


SCENES: dict[str, tuple[Task, ...]] = {
    "desk": (
        Task("scene", "Scene overview", ("scene",), ("get_blendfile_summary_datablocks", {})),
        Task("objects", "List objects (13)", ("objects",), ("get_objects_summary", {})),
        Task("object_mug", "One object: Mug", ("object", "Mug"), ("get_object_detail_summary", {"name": "Mug"})),
        Task("objects_json", "List objects, --json", ("objects", "--json"), None, "TOON vs JSON, blender-axi only"),
    ),
    "city": (
        Task("scene", "Scene overview", ("scene",), ("get_blendfile_summary_datablocks", {})),
        Task("objects", "List objects (default --limit 100)", ("objects",), ("get_objects_summary", {})),
        Task("objects_all", "List all 502 objects", ("objects", "--limit", "1000"), ("get_objects_summary", {})),
        Task("objects_collection", "Objects in District05", ("objects", "--collection", "District05"),
             ("get_objects_summary", {}), "MCP has no collection filter: full list"),
        Task("objects_tree", "Objects named *Tree*", ("objects", "--name-contains", "Tree"),
             ("get_objects_summary", {}), "MCP has no name filter: full list"),
        Task("object_bldg", "One object: D05_Bldg03", ("object", "D05_Bldg03"),
             ("get_object_detail_summary", {"name": "D05_Bldg03"})),
    ),
}
