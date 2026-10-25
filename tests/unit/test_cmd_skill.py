import sys
import types

import pytest

from blender_axi import cli, flags, paths, registry
from blender_axi.commands import skill
from blender_axi.errors import AxiError
from blender_axi.spec import GLOBAL_FLAGS, CommandSpec, Flag, Positional, Result


def _fake_spec(name, summary="Does a thing | with a pipe"):
    return CommandSpec(name=name, summary=summary, positionals=(Positional("file", False, ""),),
                       flags=(Flag("--x", "str"),), examples=(f"blender-axi {name} a.blend",),
                       run=lambda inv: Result(data={}))


@pytest.fixture
def fake_registry(monkeypatch):
    mod = types.ModuleType("fake_cmds_for_skill")
    mod.ALPHA = _fake_spec("alpha")
    mod.EXEC = _fake_spec("exec", "Run code")
    monkeypatch.setitem(sys.modules, "fake_cmds_for_skill", mod)
    monkeypatch.setattr(registry, "COMMANDS",
                        {"alpha": "fake_cmds_for_skill:ALPHA", "exec": "fake_cmds_for_skill:EXEC"})
    return mod


@pytest.fixture
def out_path():
    d = paths.tmp_root() / "skill_unit"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "SKILL.md"
    p.unlink(missing_ok=True)
    return p


def _run(argv):
    inv, _ = flags.parse(skill.GEN_SPEC, argv)
    return skill.run_gen(inv)


def test_gen_spec_name_matches_parser_hints():
    assert skill.GEN_SPEC.name == "skill gen"
    assert skill.SPEC.subcommands == {"gen": skill.GEN_SPEC}


def test_render_contains_frontmatter_table_flags_safety(fake_registry):
    text = skill.render_skill(skill._specs())
    lines = text.splitlines()
    assert lines[0] == "---" and lines[1] == "name: blender-axi" and lines[2].startswith("description: ")
    assert lines[3] == "---"
    assert "| `alpha` | Does a thing \\| with a pipe | `blender-axi alpha a.blend` |" in text
    for f in GLOBAL_FLAGS:
        assert f"`{f.name}" in text
    assert "--allow-exec" in text
    assert text.endswith("\n") and not text.endswith("\n\n")
    assert "\r" not in text


def test_safety_bullets_follow_registry(monkeypatch):
    monkeypatch.setattr(registry, "COMMANDS", {})
    text = skill.render_skill(skill._specs())
    assert "--allow-exec" not in text
    assert "Outputs are never overwritten" not in text
    assert "`.tmp/`" in text


def test_render_is_deterministic_and_static(fake_registry):
    a = skill.render_skill(skill._specs())
    assert a == skill.render_skill(skill._specs())
    assert "localhost:9876" not in a  # no live values


def test_gen_writes_then_unchanged_then_check_ok(fake_registry, out_path):
    res = _run(["--out", str(out_path)])
    assert res.data["status"] == "written" and res.data["commands"] == 2
    assert out_path.read_text(encoding="utf-8").startswith("---\nname: blender-axi")
    assert _run(["--out", str(out_path)]).data["status"] == "unchanged"
    ok = _run(["--check", "--out", str(out_path)])
    assert ok.data["status"] == "fresh" and ok.exit_code == 0


def test_check_missing_and_stale_exit_1(fake_registry, out_path):
    with pytest.raises(AxiError) as e:
        _run(["--check", "--out", str(out_path)])
    assert e.value.exit_code == 1 and e.value.code == "VALIDATION_ERROR" and "missing" in e.value.message
    assert any("blender-axi skill gen" in h for h in e.value.help)
    out_path.write_text("old", encoding="utf-8")
    with pytest.raises(AxiError) as e:
        _run(["--check", "--out", str(out_path)])
    assert "stale" in e.value.message and e.value.exit_code == 1
    assert out_path.read_text(encoding="utf-8") == "old"  # --check never writes


def test_check_tolerates_crlf_checkout(fake_registry, out_path):
    _run(["--out", str(out_path)])
    out_path.write_bytes(out_path.read_bytes().replace(b"\n", b"\r\n"))
    assert _run(["--check", "--out", str(out_path)]).data["status"] == "fresh"


def test_written_file_uses_lf(fake_registry, out_path):
    _run(["--out", str(out_path)])
    assert b"\r" not in out_path.read_bytes()


def test_default_path_is_skills_dir():
    assert skill._default_path().parts[-3:] == ("skills", "blender-axi", "SKILL.md")


def test_bare_skill_is_validation_error():
    inv, _ = flags.parse(skill.SPEC, [])
    with pytest.raises(AxiError) as e:
        skill.run_skill(inv)
    assert e.value.exit_code == 2 and any("skill gen" in h for h in e.value.help)


def test_end_to_end_through_cli(fake_registry, out_path, capsys):
    registry.COMMANDS["skill"] = "blender_axi.commands.skill:SPEC"  # monkeypatched dict, restored after
    assert cli.main(["skill", "gen", "--out", str(out_path)]) == 0
    out = capsys.readouterr()
    assert "status: written" in out.out and out.err == ""
    assert cli.main(["skill", "gen", "--check", "--out", str(out_path)]) == 0
    capsys.readouterr()
    out_path.write_text("stale", encoding="utf-8")
    assert cli.main(["skill", "gen", "--check", "--out", str(out_path)]) == 1
    assert "blender-axi skill gen" in capsys.readouterr().out
    assert cli.main(["skill", "gen", "--bogus"]) == 2
    assert "skill gen --help" in capsys.readouterr().out
