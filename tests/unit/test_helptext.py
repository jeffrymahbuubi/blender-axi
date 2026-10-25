from blender_axi import helptext
from blender_axi.spec import GLOBAL_FLAGS, CommandSpec, Flag, Positional


def _spec(**kw):
    base = dict(
        name="objects", summary="List objects",
        positionals=(Positional("file", False, "A .blend file"),),
        flags=(Flag("--type", "str", help="Object type", choices=("MESH", "CAMERA")),
               Flag("--limit", "int", default=100, help="Max rows"),
               Flag("--out", "path", required=True, help="Output"),
               Flag("--force", "bool", help="Overwrite")),
        examples=("blender-axi objects a.blend --type MESH",),
        run=lambda i: None,
    )
    base.update(kw)
    return CommandSpec(**base)


def test_render_sections_and_order():
    d = helptext.render(_spec())
    assert list(d) == ["usage", "summary", "positionals", "flags", "global_flags", "examples"]
    assert d["summary"] == "List objects"
    assert d["examples"] == ["blender-axi objects a.blend --type MESH"]


def test_usage_marks_optional_positional_and_required_flag():
    assert helptext.render(_spec())["usage"] == "blender-axi objects [<file>] --out <path> [flags]"


def test_flag_rows():
    rows = {r["flag"]: r for r in helptext.render(_spec())["flags"]}
    assert rows["--type <MESH|CAMERA>"]["help"] == "Object type"
    assert rows["--limit <n>"]["default"] == "100"
    assert rows["--out <path>"]["help"] == "Output (required)"
    assert "--force" in rows and rows["--force"]["default"] == ""


def test_positionals_rows():
    assert helptext.render(_spec())["positionals"] == [{"name": "file", "required": False, "help": "A .blend file"}]


def test_globals_listed_once_and_not_in_flags():
    d = helptext.render(_spec(flags=(Flag("--json", "bool"), Flag("--type", "str"))))
    assert [r["flag"] for r in d["flags"]] == ["--type <text>"]
    names = [r["flag"].split()[0] for r in d["global_flags"]]
    assert names == [f.name for f in GLOBAL_FLAGS]
    assert names.count("--json") == 1


def test_custom_prog():
    assert helptext.render(_spec(positionals=(), flags=()), prog="x")["usage"] == "x objects [flags]"


def test_subcommands():
    sub = _spec(name="gen", summary="Generate")
    d = helptext.render(_spec(name="skill", subcommands={"gen": sub}))
    assert d["subcommands"] == [{"name": "gen", "summary": "Generate"}]
    assert "<subcommand>" in d["usage"]
