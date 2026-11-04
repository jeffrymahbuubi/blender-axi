"""0.10.0 U5: `modifier array --constant` is in the object's local units: help text and the `notes` -> `warning` line."""

import shutil
from pathlib import Path

import pytest

from blender_axi import flags as flagparser
from blender_axi.commands import modifier as cmd
from blender_axi.runner import RunOutcome, SaveSpec

WORK = Path(__file__).resolve().parents[2] / ".tmp" / "tests" / "unit_cmd_modifier_constant"
RESULT = {"object": "Dimms", "modifier": "Array", "type": "ARRAY", "created": True, "removed": False,
          "changed": [{"field": "count", "from": 2, "to": 5}], "stack": ["Array"]}
NOTE = "constant offset is in local units: Dimms scale 0.3,2.4,1.4 makes 0.65,0,0 = 0.195,0,0 scene units"


class Fake:
    name = "fake"

    def __init__(self, result, warnings=None):
        self.result, self.warnings = result, warnings or []

    def run(self, op, params, *, blend=None, save=SaveSpec("none"), timeout=None):
        saved = save.path if save.mode != "none" else None
        return RunOutcome(self.result, True, saved, "5.2.0 LTS", 33, "", warnings=list(self.warnings))


@pytest.fixture
def work(monkeypatch):
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    (WORK / "scene.blend").write_bytes(b"BLEND")
    monkeypatch.chdir(WORK)
    return WORK


def invoke(result, warnings=None):
    inv, wants_help = flagparser.parse(cmd.SPEC, ["Dimms", "scene.blend", "array", "--count", "5",
                                                  "--constant", "0.65,0,0", "--in-place"])
    assert not wants_help
    inv.transport = Fake(result, warnings)
    return cmd.run(inv)


def test_constant_help_names_local_units():
    flag = next(f for f in cmd.SPEC.flags if f.name == "--constant")
    assert "local units" in flag.help and "multiplies it by the object's scale" in flag.help
    assert "scene units" not in flag.help


def test_notes_become_the_warning_line(work):
    res = invoke({**RESULT, "notes": [NOTE]})
    assert res.data["warning"] == NOTE
    assert list(res.data)[-2:] == ["duration_ms", "warning"]


def test_notes_follow_the_transport_warnings(work):
    res = invoke({**RESULT, "notes": [NOTE]}, warnings=["old session"])
    assert res.data["warning"] == f"old session; {NOTE}"


def test_no_notes_no_warning(work):
    res = invoke(RESULT)
    assert "warning" not in res.data
    assert "warning" in invoke(RESULT, warnings=["old session"]).data
