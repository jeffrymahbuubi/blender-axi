# Token-savings benchmark

Measured 2026-10-25 · Blender 5.2.0 LTS · blender-axi 0.2.0 · official server blender-mcp 1.0.2 (dbbf836)

Unit: **tokens**, counted with `claude-opus-5-5`.

What the agent reads for the same question, from the same live Blender: the full stdout of `blender-axi ... --live` against the text the official `blender-mcp` server returns.

![Characters or tokens per call, log scale](chart.svg)

## Fixed cost per session

| Item | Official MCP, tokens (claude-opus-5-5) | blender-axi, tokens (claude-opus-5-5) | Saving |
|---|---:|---:|---:|
| Tool definitions vs. skill text | 7,843 (26 tools, [raw](fixed/tools_list__mcp.json)) | 1,481 ([raw](../../skills/blender-axi/SKILL.md)) | 81.1% |

> The tool-list figure is a lower bound: it is the compact JSON of the server's `tools/list` reply, and real clients serialise tool schemas more verbosely.

## Scene: desk (13 objects, 0 collections)

![desk scene render](desk/render.png)

| Task | Official MCP, tokens (claude-opus-5-5) | blender-axi, tokens (claude-opus-5-5) | Saving | Note |
|---|---:|---:|---:|---|
| Scene overview | 319 ([raw](desk/scene__mcp.json)) | 327 ([raw](desk/scene__axi.txt)) | -2.5% |  |
| List objects (13) | 1,157 ([raw](desk/objects__mcp.json)) | 409 ([raw](desk/objects__axi.txt)) | 64.6% |  |
| One object: Mug | 387 ([raw](desk/object_mug__mcp.json)) | 168 ([raw](desk/object_mug__axi.txt)) | 56.6% |  |
| List objects, --json | — | 792 ([raw](desk/objects_json__axi.txt)) | — | TOON vs JSON, blender-axi only |

## Scene: city (502 objects, 21 collections)

![city scene render](city/render.png)

| Task | Official MCP, tokens (claude-opus-5-5) | blender-axi, tokens (claude-opus-5-5) | Saving | Note |
|---|---:|---:|---:|---|
| Scene overview | 310 ([raw](city/scene__mcp.json)) | 345 ([raw](city/scene__axi.txt)) | -11.3% |  |
| List objects (default --limit 100) | 41,531 ([raw, truncated](city/objects__mcp.json)) | 2,100 ([raw](city/objects__axi.txt)) | 94.9% |  |
| List all 502 objects | 41,531 ([raw, truncated](city/objects__mcp.json)) | 9,494 ([raw](city/objects_all__axi.txt)) | 77.1% | MCP call shared with objects |
| Objects in District05 | 41,531 ([raw, truncated](city/objects__mcp.json)) | 540 ([raw](city/objects_collection__axi.txt)) | 98.7% | MCP has no collection filter: full list; MCP call shared with objects |
| Objects named *Tree* | 41,531 ([raw, truncated](city/objects__mcp.json)) | 1,860 ([raw](city/objects_tree__axi.txt)) | 95.5% | MCP has no name filter: full list; MCP call shared with objects |
| One object: D05_Bldg03 | 396 ([raw](city/object_bldg__mcp.json)) | 174 ([raw](city/object_bldg__axi.txt)) | 56.1% |  |

## Notes

- Saving is `100 * (1 - blender-axi / official MCP)` in the unit shown; a negative saving means blender-axi printed more than the official server for that task.
- The tool-list figure is a lower bound: it is the compact JSON of the server's `tools/list` reply, and real clients serialise tool schemas more verbosely.
- An em dash means there is no equivalent measurement (for example a blender-axi-only row); the official server has no collection or name filters, so those rows compare against its full list.
- A call repeated within one scene is made once; the later rows reuse that measurement.
- Raw outputs are stored next to this file and cut at 20,000 characters (the `raw, truncated` links).
- Latency is recorded in `results.json` (`ms`) but is not part of the claim.
- Raw outputs are normalised so they do not depend on the machine: the checkout path is shown as `<repo>`, and the random port of the temporary server in blender-axi's hint lines is shown as `50000`.

## Reproduce

```
.venv/Scripts/python.exe benchmarks/token_bench.py
```

Needs Blender with the add-on server and the official server installed under `.tmp/mcpvenv` (see `--mcp-server`). Artifacts land in `benchmarks/results/`.

To enable exact token counts, set `BLENDER_AXI_ANTHROPIC_KEY` (recommended, e.g. via the .env launcher) or `ANTHROPIC_API_KEY` and re-run; without a key the run reports characters.
