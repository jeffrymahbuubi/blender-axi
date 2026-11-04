# blender-axi showcase: three real tasks, official MCP vs blender-axi

The [token benchmark](../results/results.md) measures how big one tool answer is. This showcase asks the question a user
actually has: *give an AI agent a real Blender job, once with the official `blender-mcp` server and once with
`blender-axi`. Which finishes it, and at what cost?* The agents run for real (`claude -p`), the result is judged by a plain
`bpy` script, and every repeat is published, including the ones that fail.

## Results

<!-- showcase-results:start -->
Measured 2026-11-04, 5 repeats per cell, three models. Medians:

| Model | Project | Passed (axi / MCP) | Median cost (axi / MCP) | Median input tokens (axi / MCP) | Median wall (axi / MCP) |
|---|---|---:|---:|---:|---:|
| Opus 5.5 | turntable | 5/5 / 5/5 | $0.14 / $0.25 | 92,623 / 202,510 | 53s / 77s |
| Opus 5.5 | qa_cleanup | 5/5 / 5/5 | $0.17 / $0.22 | 126,418 / 152,734 | 78s / 66s |
| Opus 5.5 | build_export | 5/5 / 5/5 | $0.24 / $0.21 | 207,424 / 176,271 | 80s / 68s |
| Sonnet 5.5 | turntable | 4/5 / 5/5 | $0.04 / $0.16 | 76,388 / 200,874 | 27s / 73s |
| Sonnet 5.5 | qa_cleanup | 2/5 / 5/5 | $0.11 / $0.09 | 155,744 / 124,025 | 63s / 28s |
| Sonnet 5.5 | build_export | 5/5 / 5/5 | $0.10 / $0.09 | 198,735 / 126,636 | 62s / 39s |
| Haiku 5.5 | turntable | 5/5 / 4/5 | $0.01 / $0.03 | 159,999 / 626,037 | 59s / 171s |
| Haiku 5.5 | qa_cleanup | 5/5 / 5/5 | $0.01 / $0.01 | 226,559 / 236,083 | 79s / 53s |
| Haiku 5.5 | build_export | 5/5 / 4/5 | $0.01 / $0.02 | 347,721 / 396,099 | 77s / 96s |

**Where blender-axi wins:** the turntable task (one typed `render --turntable --sheet` command vs a hand-written orbit, compositing and encoding script): about half the tokens and cost on Opus, under 40% on Sonnet, on Haiku about a quarter of the input tokens. **Where it ties or loses:** building a lamp from scratch (one generated script is as cheap as many typed steps), and wall time in general (blender-axi starts Blender per command). **What it got wrong:** on Sonnet the blender-axi agent trusted `audit`, which does not report materials kept alive only by a fake user, and failed `qa_cleanup` 3 of 5 times; that is a blender-axi gap, now on the backlog. **Looks are not scored:** the judge checks files, sizes and scene facts, not beauty; with the same prompt the MCP agents more often chose EEVEE with colour and shadows, the blender-axi agents the cheaper grey WORKBENCH (see the side-by-side images).

Full pages with side-by-side images, replay GIFs, transcripts and every repeat: [Opus 5.5](results/opus/results.md) · [Sonnet 5.5](results/sonnet/results.md) · [Haiku 5.5](results/haiku/results.md).
<!-- showcase-results:end -->

Everything behind the table is in [results/results.md](results/results.md): the prompt of each project, a side-by-side
final image, a replay GIF per arm (one frame per tool call, with the scene after that step), a trimmed transcript per arm
and a table with every repeat. Costs are list-price estimates from `claude -p`, not invoices. Workflow videos (mp4) are
release assets, not part of the repository.

## Method and fairness

Both arms get the same:

- **prompt** (`projects/<name>/prompt.md`, byte-identical), **model** and **effort**, **start file** (built from the repo
  and copied into a fresh working directory) and **runner** (`claude -p`, stream-json, `--max-turns 40`,
  `--max-budget-usd 3`, no session persistence, stdin closed);
- **isolation**: a fresh Claude config directory, `--setting-sources ""` (no user or project settings, memory, hooks or
  skills), `--strict-mcp-config` (no other MCP servers), and an API key that exists only in the child process
  environment. `--bare` would isolate further but silently removes the `Write` tool in Claude Code 2.1.x, so it is
  opt-in (`run_ab.py --bare`);
- **tool budget shape**: `Read` and `Write` plus exactly one instrument.

They differ only in the instrument:

| | blender-axi arm | official MCP arm |
|---|---|---|
| Instrument | `Bash`, allowed only for `blender-axi *` | the 26 `mcp__blender__*` tools of the official server |
| Instructions | `skills/blender-axi/SKILL.md` appended to the system prompt | none beyond the tool schemas |
| Blender | headless, one start per command (batched with `apply` when the agent chooses) | one headless Blender kept alive behind the add-on server |

The **judge** is a plain `bpy` checker per project run with `blender --background --factory-startup`; it never imports
blender-axi, and neither arm sees it. The agent's own claim of success counts for nothing.

### Honesty rules

- N = 3 sequential repeats per cell, shown as median with min-max; **every repeat is listed**, failures are failures and
  are never re-rolled.
- **Where the official MCP is equal or better** is stated in `results.md` next to the numbers: one-shot builds (a single
  `execute_blender_code` script can tie or win), scene overview, fixes that have no typed blender-axi command (the agent
  falls back to `exec`, i.e. parity), API documentation search, GUI-only features, and the MCP arm's single long-lived
  Blender process.
- **Wall time is structurally biased** (Blender start per blender-axi command vs one persistent process) and is reported,
  not claimed as a result.
- The `qa_cleanup` task deliberately contains only fixes that both arms can do with typed operations or plain `bpy`; "apply
  scale" is left out because it would only compare `exec` with `execute_blender_code`.
- Token cost has a fixed part: the MCP arm pays for 26 tool schemas, the blender-axi arm for SKILL.md. Both are measured
  (first-turn cache creation) and reported separately.
- The blender-axi arm's `Bash` is limited to `blender-axi *`, so shell helpers (`mkdir`, `cat > file`, `rm`) are
  denied and counted as errors, while the MCP arm's Python can touch any file. Both prompts therefore say that the output
  folder already exists and that text files are written with the `Write` tool (a pilot run lost its report to this).
- Results come from one machine and one model at one date; they show a pattern for these tasks, not a law.

## Projects

**turntable** (start: `desk.blend`, 13 objects). The agent makes product shots of the object `Mug`: a 12-frame
turntable as ONE numbered contact sheet (at least 1280 px wide), the same 12 frames as an mp4 at 12 fps, and a
three-quarter hero still (at least 640 px wide), without touching the work file. Judge: the three files exist, PNG widths
come from the IHDR chunk, the mp4 holds at least 12 frames (Blender's movie-clip reader), and `work.blend` is
byte-identical to the start.

**qa_cleanup** (start: the 502-object city plus a deterministic mess, 505 objects). The agent audits the file, then
deletes the 3 `Debug_*` objects, makes everything in `District05` visible and renderable (3 buildings are render-off),
removes the 2 empty collections, the 4 unused `Old*` materials and the image whose file is missing, changes nothing else,
saves `clean.blend` and writes `report.md` with before/after counts. Judge (on `clean.blend`, compared with the start
file): no `Debug_*`; nothing hidden in `District05`; no empty collections; no unused materials (fake users do not count);
no image with a missing file; object names equal the start minus `Debug_*`; `District05` dimensions unchanged;
`report.md` is non-empty and mentions before and after. The planted orphan materials carry a fake user (Blender does not
save zero-user data), so blender-axi's `audit` does not report them and both arms need plain `bpy` for that step.

**build_export** (start: an empty file). The agent builds a desk lamp from a brief (dark-metal round base with a bevel,
thin stem, matte-red cone head, small emissive bulb, one camera, one light), sets EEVEE, 640x360, 16 samples, renders
`lamp.png`, exports only the lamp meshes with modifiers applied to `lamp.glb` and saves `lamp.blend`. Judge: at least 4
meshes, a camera, a light, a bevel modifier, at least 2 used materials with one emissive; EEVEE 640x360 16 samples;
`lamp.png` at least 640 wide; `lamp.glb` has the glTF magic, at least 4 meshes, no camera node and no punctual light.

Each judge was checked both ways before any agent ran: it fails on an empty output folder and passes on a reference
solution made with blender-axi itself (`benchmarks/showcase/tests/test_projects.py`).

## How the workflow video is made

After every tool result the harness snapshots the scene (blender-axi arm: a copy of `work.blend`; MCP arm:
`save_as_mainfile(copy=True)` over the add-on socket). `sc/replay.py` renders each snapshot with `blender-axi render --fit
--auto-light --engine WORKBENCH` and composes one 960x540 frame per tool call: the step's command and the first lines of
its result on the left, the scene render on the right, and a footer with running tokens and cost (approximate: the run's
totals spread over the steps by result size). The last frame shows passed or failed and the exact totals, held for 2 s.
Pillow writes the GIF (2 fps, at most 40 frames, at most 2.5 MB, no comment block). Blender itself encodes the stacked
mp4 (blender-axi on top, MCP below, H.264 through a video-sequencer scene, `sc/encode_mp4.py`), so no ffmpeg and no
browser are needed.

## Reproduce

```
.venv/Scripts/python.exe benchmarks/showcase/run_ab.py --project turntable|qa_cleanup|build_export|all --arm axi|mcp|both
   [--repeats 3] [--model claude-opus-5-5] [--effort medium] [--max-turns 40] [--max-budget-usd 3] [--tmp .tmp/showcase]
   [--results benchmarks/showcase/results] [--axi-bin .venv/Scripts] [--mcp-server <venv>/Scripts/blender-mcp.exe]
   [--blender <exe>] [--bare] [--dry-run] [--skip-replay] [--skip-report] [--claude claude]
```

`--dry-run` prints the exact `claude` command line of each arm with the key shown as `<set,len N>` and runs nothing.
Clear `.tmp/showcase/runs` before a new matrix: the report reads every run under it. Exit codes: 0 all runs completed,
1 a run crashed or was cut (error, timeout, turn or budget limit) or the scrub gate found offences, 2 setup problem.

Prerequisites, in short: Windows with Blender 5.2, `claude` on PATH, the official `blender-mcp` server installed as
described for the token benchmark, `uv pip install -e .[dev,showcase]` (Pillow is a dev-only extra), and an API key in
`BLENDER_AXI_ANTHROPIC_KEY` supplied by your launcher (never stored in the repository).

## Keeping the artifacts clean

`sc/scrub.py --check benchmarks/showcase/results` is the gate before anything is committed: it fails on PNG text chunks,
GIF comment blocks, absolute user paths, secrets, e-mail addresses, denied terms and files over the size caps (GIF 2.5 MB,
PNG 400 KB, transcript 60 KB, 20 MB in total). `--fix` strips and redacts what it can. Denied terms are not stored in the
repository: put them in the environment variable `SHOWCASE_DENY` (comma-separated) or one per line in the untracked file
`.tmp/showcase/deny.txt`; the current user name and home directory are always checked.
