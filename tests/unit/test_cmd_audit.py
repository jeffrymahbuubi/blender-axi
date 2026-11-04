"""`audit` command with a fake transport (0.6.0 plan §3.4, U3). Registered via monkeypatch (the lead splices registry.py)."""

from __future__ import annotations

import json

import pytest

from blender_axi import cli, flags, registry, toon
from blender_axi.blender_side.audit_checks import CHECK_IDS
from blender_axi.commands import audit as cmd
from blender_axi.errors import AxiError
from blender_axi.runner import RunOutcome

UNLIT = "unlit scene: no light, no world background and no emissive material; EEVEE renders black"


def finding(check, level, items):
    return {"check": check, "level": level, "count": len(items), "items": items}


def result(findings=(), n_objects=502, checks=11, skipped=False, **extra):
    return {"filepath": "D:/work/scene.blend", "objects_checked": n_objects,
            "checks_run": [f"c{i}" for i in range(checks)], "scene_checks_skipped": skipped,
            "findings": list(findings), **extra}


class FakeTransport:
    name = "fake"

    def __init__(self, res, error=None, warnings=()):
        self.res, self.error, self.warnings, self.calls = res, error, list(warnings), []

    def run(self, op, params, *, blend=None, save=None, timeout=None):
        self.calls.append((op, params, blend))
        if self.error:
            raise self.error
        return RunOutcome(result=self.res, modified=False, saved=None, blender_version="5.2.0 LTS", duration_ms=12,
                          stdout="", warnings=self.warnings)


@pytest.fixture(autouse=True)
def blend_dir(tmp_path, monkeypatch):
    (tmp_path / "scene.blend").write_bytes(b"x")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def invoke(argv, transport):
    inv, wants_help = flags.parse(cmd.SPEC, argv)
    assert not wants_help
    inv.transport = transport
    return cmd.SPEC.run(inv)


def go(argv, res=None, **kw):
    t = FakeTransport(res if res is not None else result(), **kw)
    return invoke(argv, t), t


MIXED = [finding("camera", "warn", ["no scene camera"]), finding("scale", "warn", [f"S{i} (2,2,2)" for i in range(8)]),
         finding("materials", "warn", ["Bare"]), finding("ngons", "info", ["P (3 faces)"])]


# -- params and shape ------------------------------------------------------------------------------

def test_params_default_and_filters():
    res, t = go([])
    assert t.calls[0][0] == "audit" and t.calls[0][1] == {"checks": None, "objects": None, "collection": None}
    assert t.calls[0][2].name == "scene.blend"
    _, t = go(["--checks", "scale,materials", "--object", "A,B", "--object", "C", "--collection", "Props"])
    assert t.calls[0][1] == {"checks": ["scale", "materials"], "objects": ["A,B", "C"], "collection": "Props"}


def test_clean_state():
    res, _ = go([])
    assert list(res.data) == ["file", "checked", "status", "duration_ms"]
    assert res.data["file"] == "scene.blend" and res.data["checked"] == "11 checks, 502 objects"
    assert res.data["status"] == "clean" and "findings" not in res.data
    assert res.exit_code == 0 and len(res.help) == 1 and "export <file> --out <path.glb>" in res.help[0]


def test_data_order_with_findings_and_warning():
    res, _ = go([], result(MIXED), warnings=["transport note"])
    assert list(res.data) == ["file", "checked", "findings", "status", "duration_ms", "warning"]
    assert res.data["status"] == "4 findings (3 warn, 1 info)"
    assert res.data["warning"] == "transport note"
    assert res.data["findings"][0] == {"check": "camera", "level": "warn", "count": 1, "items": "no scene camera"}
    toon.encode({**res.data, "help": res.help})  # valid TOON


def test_op_warnings_merge_with_transport_warnings():
    res, _ = go([], result(warnings=["mesh checks unavailable in this session: nonmanifold, loose"]),
                warnings=["other"])
    assert res.data["warning"] == "mesh checks unavailable in this session: nonmanifold, loose; other"


def test_checked_texts():
    res, _ = go(["--object", "Cube"], result(n_objects=1, checks=2, skipped=True))
    assert res.data["checked"] == "2 checks, 1 object (scene-level checks skipped)"
    res, _ = go([], result(n_objects=0, checks=1))
    assert res.data["checked"] == "1 check, 0 objects"


# -- items cap and --full --------------------------------------------------------------------------

def test_items_capped_at_five_with_more_and_full_shows_all():
    res, _ = go([], result(MIXED))
    assert res.data["findings"][1]["items"] == "S0 (2,2,2), S1 (2,2,2), S2 (2,2,2), S3 (2,2,2), S4 (2,2,2), +3 more"
    assert res.data["findings"][1]["count"] == 8
    inv, _ = flags.parse(cmd.SPEC, ["--full"])
    inv.transport = FakeTransport(result(MIXED))
    full = cmd.SPEC.run(inv)
    assert full.data["findings"][1]["items"].endswith("S7 (2,2,2)") and "more" not in full.data["findings"][1]["items"]
    assert not any("--full" in h for h in full.help)


# -- strict ----------------------------------------------------------------------------------------

def test_strict_exit_1_keeps_the_report():
    res, _ = go(["--strict"], result(MIXED))
    assert res.exit_code == 1
    assert res.data["status"] == "4 findings (3 warn, 1 info); --strict: exit 1" and len(res.data["findings"]) == 4


def test_strict_info_only_exits_0():
    res, _ = go(["--strict"], result([finding("ngons", "info", ["P (3 faces)"])]))
    assert res.exit_code == 0 and res.data["status"] == "1 finding (0 warn, 1 info)"


def test_not_strict_exits_0_on_warn():
    res, _ = go([], result(MIXED))
    assert res.exit_code == 0 and "--strict" not in res.data["status"]


def test_strict_exit_code_through_cli(monkeypatch, capsys):
    monkeypatch.setitem(registry.COMMANDS, "audit", "blender_axi.commands.audit:SPEC")
    monkeypatch.setattr(cli, "_LazyHeadless", lambda g: FakeTransport(result(MIXED)))
    code = cli.main(["--json", "audit", "scene.blend", "--strict"])
    out = json.loads(capsys.readouterr().out)
    assert code == 1 and out["findings"][0]["check"] == "camera" and out["status"].endswith("--strict: exit 1")
    code = cli.main(["--json", "audit", "scene.blend"])
    assert code == 0 and "findings" in json.loads(capsys.readouterr().out)


# -- levels, --fail-on, --ignore -------------------------------------------------------------------

FILES = finding("files", "error", ["image Tex (missing: /x/tex.png)"])


def test_status_with_an_error_finding_counts_errors_first():
    res, _ = go([], result([FILES, *MIXED]))
    assert res.data["status"] == "5 findings (1 error, 3 warn, 1 info)"
    assert res.data["findings"][0] == {"check": "files", "level": "error", "count": 1,
                                       "items": "image Tex (missing: /x/tex.png)"}
    res, _ = go([], result(MIXED))
    assert res.data["status"] == "4 findings (3 warn, 1 info)"  # no error: byte-identical to 0.9.0


def test_fail_on_error_exits_1_only_with_an_error_finding():
    res, _ = go(["--fail-on", "error"], result([FILES, *MIXED]))
    assert res.exit_code == 1 and res.data["status"].endswith("(1 error, 3 warn, 1 info); --fail-on error: exit 1")
    res, _ = go(["--fail-on", "error"], result(MIXED))
    assert res.exit_code == 0 and "--fail-on" not in res.data["status"]
    res, _ = go(["--fail-on", "error"], result())
    assert res.exit_code == 0 and res.data["status"] == "clean"


def test_fail_on_warn_counts_errors_and_warns_but_not_info():
    res, _ = go(["--fail-on", "warn"], result(MIXED))
    assert res.exit_code == 1 and res.data["status"].endswith("--fail-on warn: exit 1")
    res, _ = go(["--fail-on", "warn"], result([FILES]))
    assert res.exit_code == 1
    res, _ = go(["--fail-on", "warn"], result([finding("ngons", "info", ["P (3 faces)"])]))
    assert res.exit_code == 0


def test_strict_is_fail_on_warn_and_also_fails_on_errors():
    res, _ = go(["--strict"], result([FILES]))
    assert res.exit_code == 1 and res.data["status"] == "1 finding (1 error, 0 warn, 0 info); --strict: exit 1"
    res, _ = go(["--strict", "--fail-on", "warn"], result(MIXED))
    assert res.exit_code == 1 and res.data["status"].endswith("--strict: exit 1")


def test_strict_with_fail_on_error_is_a_contradiction():
    with pytest.raises(AxiError) as e:
        go(["--strict", "--fail-on", "error"])
    assert e.value.exit_code == 2 and "--strict means --fail-on warn; pass one of them" in e.value.message


def test_fail_on_rejects_other_levels():
    with pytest.raises(AxiError) as e:
        flags.parse(cmd.SPEC, ["--fail-on", "info"])
    assert e.value.exit_code == 2 and "must be one of error, warn" in e.value.message


def test_ignore_reaches_params_and_checked_text():
    res, t = go(["--ignore", "scale,materials"], result(checks=9))
    assert t.calls[0][1] == {"checks": None, "objects": None, "collection": None, "ignore": ["scale", "materials"]}
    assert res.data["checked"] == "9 checks, 502 objects (ignored: scale, materials)"
    _, t = go([])
    assert "ignore" not in t.calls[0][1]


def test_ignore_with_scene_skip_text_order():
    res, _ = go(["--ignore", "scale", "--object", "A"], result(n_objects=1, checks=3, skipped=True))
    assert res.data["checked"] == "3 checks, 1 object (ignored: scale) (scene-level checks skipped)"


def test_ignore_checks_contradiction_and_ignoring_everything_exit_2():
    with pytest.raises(AxiError) as e:
        go(["--ignore", "scale", "--checks", "camera"])
    assert e.value.exit_code == 2 and e.value.message == "--checks already selects the checks; drop --ignore"
    with pytest.raises(AxiError) as e:
        go(["--ignore", ",".join(CHECK_IDS)])
    assert e.value.exit_code == 2 and "--ignore would skip every check" in e.value.message
    with pytest.raises(AxiError) as e:
        go(["--ignore", ","])
    assert e.value.exit_code == 2 and "--ignore needs at least one check id" in e.value.message
    with pytest.raises(AxiError) as e:
        flags.parse(cmd.SPEC, ["--ignore", "bogus"])
    assert e.value.exit_code == 2


def test_replay_hint_keeps_ignore():
    many = [finding("scale", "warn", ["x"] * 7)]
    res, _ = go(["--ignore", "ngons"], result(many))
    assert res.help[0] == "Run `blender-axi audit <file> --ignore ngons --full` to list every item"


def test_fail_on_exit_code_through_cli(monkeypatch, capsys):
    monkeypatch.setitem(registry.COMMANDS, "audit", "blender_axi.commands.audit:SPEC")
    monkeypatch.setattr(cli, "_LazyHeadless", lambda g: FakeTransport(result([FILES])))
    assert cli.main(["--json", "audit", "scene.blend", "--fail-on", "error"]) == 1
    assert json.loads(capsys.readouterr().out)["status"].endswith("--fail-on error: exit 1")


# -- hints -----------------------------------------------------------------------------------------

def test_hint_priority_and_cap():
    all_findings = [finding("camera", "warn", ["no scene camera"]), finding("lighting", "warn", [UNLIT]),
                    finding("scale", "warn", [f"S{i} (2,2,2)" for i in range(8)]), finding("materials", "warn", ["Bare"])]
    res, _ = go([], result(all_findings))
    assert len(res.help) == 3
    assert "add light scene.blend" not in res.help[0] and "add light <file> --type SUN --in-place" in res.help[0]
    assert "add camera <file> --location 7,-7,5 --rotation 63.6,0,46.7 --in-place" in res.help[1]
    assert "material Bare <file> --name Mat --color 0.8,0.8,0.8 --in-place" in res.help[2]


def test_hint_truncated_and_cleanup_text():
    res, _ = go(["--checks", "scale"], result([finding("scale", "warn", [f"S{i} (2,2,2)" for i in range(8)])]))
    assert res.help[0] == "Run `blender-axi audit <file> --checks scale --full` to list every item"
    assert res.help[1] == "Apply scale (Ctrl+A) and clean up meshes in Blender, or via exec --allow-exec"


def test_hint_material_name_with_space_is_quoted():
    res, _ = go([], result([finding("materials", "warn", ["Big Box"])]))
    assert 'material "Big Box" <file>' in res.help[0]


def test_hint_replay_keeps_filters():
    many = [finding("scale", "warn", [f"S{i} (2,2,2)" for i in range(8)])]
    res, _ = go(["--object", "A B", "--collection", "Props"], result(many, skipped=True))
    assert 'audit <file> --object "A B" --collection Props --full' in res.help[0]


def test_live_hints_drop_file_and_in_place(monkeypatch):
    inv, _ = flags.parse(cmd.SPEC, ["--live", "--port", "9999"])
    inv.transport = FakeTransport(result([finding("lighting", "warn", [UNLIT]),
                                          finding("camera", "warn", ["no scene camera"])]))
    res = cmd.SPEC.run(inv)
    assert inv.transport.calls[0][2] is None and res.data["file"] == "D:/work/scene.blend"
    assert res.help[0] == "Run `blender-axi add light --type SUN --live --port 9999` to light the scene"
    assert res.help[1].startswith("Run `blender-axi add camera --location 7,-7,5") and "--in-place" not in res.help[1]
    assert not any("<file>" in h for h in res.help)


# -- validation and errors -------------------------------------------------------------------------

def test_bad_check_id_exit_2():
    with pytest.raises(AxiError) as e:
        flags.parse(cmd.SPEC, ["--checks", "bogus"])
    assert e.value.exit_code == 2 and "must be one of" in e.value.message


@pytest.mark.parametrize("argv, text", [
    (["--object", ""], "--object needs an object name"),
    (["--checks", ","], "--checks needs at least one check id"),
    (["--collection", " "], "--collection needs a collection name"),
    (["--object", "A", "--checks", "camera,files"], "scene-level"),
])
def test_empty_or_contradictory_filters_exit_2(argv, text):
    with pytest.raises(AxiError) as e:
        go(argv)
    assert e.value.code == "VALIDATION_ERROR" and e.value.exit_code == 2 and text in e.value.message


def test_not_found_is_translated_to_the_objects_hint():
    err = AxiError("NOT_FOUND", "object 'Cub' not found (did you mean: Cube?)", help=["generic"])
    with pytest.raises(AxiError) as e:
        go(["--object", "Cub"], error=err)
    assert e.value.help == ["Run `blender-axi objects <file>` to list object and collection names"]


def test_other_errors_pass_through():
    err = AxiError("OP_FAILED", "boom", help=["generic"])
    with pytest.raises(AxiError) as e:
        go([], error=err)
    assert e.value.help == ["generic"]


def test_unsaved_live_file_label():
    inv, _ = flags.parse(cmd.SPEC, ["--live"])
    inv.transport = FakeTransport({**result(), "filepath": ""})
    assert cmd.SPEC.run(inv).data["file"] == "(unsaved)"


def test_help_lists_flags(capsys, monkeypatch):
    monkeypatch.setitem(registry.COMMANDS, "audit", "blender_axi.commands.audit:SPEC")
    assert cli.main(["audit", "--help"]) == 0
    out = capsys.readouterr().out
    assert "--checks" in out and "--strict" in out and "--object" in out and "--collection" in out
    assert "--ignore" in out and "--fail-on" in out and "Same as --fail-on warn" in out
