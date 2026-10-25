import pytest

from blender_axi import flags
from blender_axi.errors import AxiError
from blender_axi.spec import GLOBAL_FLAGS, CommandSpec, Flag, GlobalOpts, Positional


def make(*fl, pos=(), examples=()):
    return CommandSpec("demo", "d", pos, tuple(fl), tuple(examples), lambda i: None)


def err(spec, argv):
    with pytest.raises(AxiError) as ei:
        flags.parse(spec, argv)
    return ei.value


def test_value_forms():
    s = make(Flag("--type", "str"), Flag("--limit", "int"))
    inv, h = flags.parse(s, ["--type", "MESH", "--limit=5"])
    assert not h and inv.flags == {"type": "MESH", "limit": 5} and inv.command == "demo"
    assert inv.transport is None


def test_defaults_applied_and_bool_default_false():
    s = make(Flag("--limit", "int", default=100), Flag("--force", "bool"), Flag("--q", "str"))
    inv, _ = flags.parse(s, [])
    assert inv.flags == {"limit": 100, "force": False, "q": None}


def test_bool_and_no_bool():
    s = make(Flag("--force", "bool"), Flag("--keep", "bool", default=True))
    assert flags.parse(s, ["--force"])[0].flags["force"] is True
    assert flags.parse(s, ["--no-keep"])[0].flags["keep"] is False
    assert flags.parse(s, ["--force=false"])[0].flags["force"] is False


def test_list_repeated_and_comma():
    s = make(Flag("--fields", "list"))
    inv, _ = flags.parse(s, ["--fields", "a,b", "--fields=c", "--fields", "d, e"])
    assert inv.flags["fields"] == ["a", "b", "c", "d", "e"]


def test_dash_keys_normalised_and_lookup_either_way():
    s = make(Flag("--name-contains", "str"))
    inv, _ = flags.parse(s, ["--name-contains", "x"])
    assert inv.flags["name_contains"] == "x" and inv.flags["name-contains"] == "x"
    assert inv.flags.get("name-contains") == "x" and "name-contains" in inv.flags


def test_positionals_in_order_and_double_dash():
    s = make(Flag("--type", "str"), pos=(Positional("name", True, ""), Positional("file", False, "")))
    inv, _ = flags.parse(s, ["Cube", "a.blend", "--type", "X"])
    assert inv.positionals == {"name": "Cube", "file": "a.blend"}
    inv, _ = flags.parse(s, ["--", "--weird"])
    assert inv.positionals == {"name": "--weird"}


def test_negative_number_value_and_positional():
    s = make(Flag("--frame", "int"), pos=(Positional("n", False, ""),))
    inv, _ = flags.parse(s, ["--frame", "-3", "-2"])
    assert inv.flags["frame"] == -3 and inv.positionals == {"n": "-2"}


def test_unknown_flag_exit_2_with_valid_flags_line():
    s = make(Flag("--type", "str"), Flag("--limit", "int"))
    e = err(s, ["--typo", "x"])
    assert (e.code, e.exit_code) == ("VALIDATION_ERROR", 2)
    assert "--typo" in e.message and "`demo`" in e.message
    assert "--type" in e.help[0] and "--limit" in e.help[0]
    assert e.help[-1] == "blender-axi demo --help"


def test_unknown_short_flag():
    assert err(make(), ["-x"]).exit_code == 2


def test_renamed_from_hint():
    s = make(Flag("--state", "str", renamed_from=("--status",)))
    e = err(s, ["--status", "x"])
    assert e.message == "--status was renamed; use --state instead" and e.exit_code == 2


def test_missing_required_flag():
    e = err(make(Flag("--out", "path", required=True), examples=("blender-axi demo --out a.png",)), [])
    assert e.exit_code == 2 and "--out" in e.message
    assert any("blender-axi demo --out a.png" in h for h in e.help)


def test_missing_required_positional_and_extra():
    s = make(pos=(Positional("name", True, ""),))
    assert "<name>" in err(s, []).message
    assert "unexpected argument 'b'" in err(s, ["a", "b"]).message


def test_missing_value():
    s = make(Flag("--type", "str"), Flag("--limit", "int"))
    assert "requires a value" in err(s, ["--type"]).message
    assert "requires a value" in err(s, ["--type", "--limit", "3"]).message


def test_bad_int_float_bool():
    s = make(Flag("--n", "int"), Flag("--t", "float"), Flag("--f", "bool"))
    assert "integer" in err(s, ["--n", "x"]).message
    assert "number" in err(s, ["--t", "x"]).message
    assert flags.parse(s, ["--t", "1.5"])[0].flags["t"] == 1.5
    assert "true or false" in err(s, ["--f=maybe"]).message


def test_bad_choice_lists_accepted_and_is_case_insensitive():
    s = make(Flag("--engine", "str", choices=("WORKBENCH", "EEVEE")), Flag("--k", "list", choices=("a", "b")))
    e = err(s, ["--engine", "nope"])
    assert e.exit_code == 2 and "WORKBENCH, EEVEE" in e.message
    assert flags.parse(s, ["--engine", "eevee"])[0].flags["engine"] == "EEVEE"
    assert err(s, ["--k", "a,z"]).exit_code == 2


def test_help_short_circuits_validation():
    s = make(Flag("--out", "path", required=True), Flag("--n", "int"), pos=(Positional("f", True, ""),))
    inv, h = flags.parse(s, ["--n", "bad", "--bogus", "--help"])
    assert h is True
    inv, h = flags.parse(s, ["-h"])
    assert h is True


def test_help_still_fills_globals():
    inv, h = flags.parse(make(), ["--json", "--help", "--port", "abc"])
    assert h and inv.globals.json is True


def test_globals_accepted_on_every_command_anywhere():
    s = make(Flag("--type", "str"))
    inv, _ = flags.parse(s, ["--json", "--type", "X", "--full", "--debug", "--keep-tmp", "--blender", "C:/b.exe",
                             "--timeout", "2.5", "--live", "--host=h", "--port", "1234"])
    assert inv.flags == {"type": "X"}
    assert inv.globals == GlobalOpts(json=True, full=True, debug=True, keep_tmp=True, blender="C:/b.exe",
                                     timeout=2.5, live=True, host="h", port=1234)


def test_global_defaults_and_no_global():
    inv, _ = flags.parse(make(), [])
    assert inv.globals == GlobalOpts()
    inv, _ = flags.parse(make(), ["--json", "--no-json"])
    assert inv.globals.json is False


def test_global_bad_values():
    assert "integer" in err(make(), ["--port", "x"]).message
    assert "number" in err(make(), ["--timeout", "x"]).message


@pytest.mark.parametrize("g", [f for f in GLOBAL_FLAGS if f.name != "--help"], ids=lambda f: f.name)
def test_every_global_never_unknown(g):
    argv = [g.name] if g.kind == "bool" else [g.name, "3" if g.kind in ("int", "float") else "v"]
    flags.parse(make(), argv)


def test_command_flag_colliding_with_global_goes_to_globals():
    s = make(Flag("--timeout", "float"))
    inv, _ = flags.parse(s, ["--timeout", "9"])
    assert inv.globals.timeout == 9.0 and "timeout" not in inv.flags


def test_unknown_flag_help_when_no_own_flags():
    e = err(make(), ["--x"])
    assert "no flags besides the global" in e.help[0]
