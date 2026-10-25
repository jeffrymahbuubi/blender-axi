import pytest

from blender_axi.errors import CODE_EXIT, AxiError, to_result

TABLE = {
    "VALIDATION_ERROR": 2, "EXEC_NOT_ALLOWED": 2, "NOT_FOUND": 1, "BLENDER_NOT_FOUND": 1,
    "BLENDER_FAILED": 1, "OP_FAILED": 1, "EXEC_FAILED": 1, "WRITE_REFUSED": 1, "TIMEOUT": 1,
    "LIVE_UNREACHABLE": 1, "LIVE_ERROR": 1, "INTERNAL": 1,
}


def test_exit_code_table_matches_plan():
    assert CODE_EXIT == TABLE


@pytest.mark.parametrize("code,exit_code", TABLE.items())
def test_default_exit_code_from_table(code, exit_code):
    assert AxiError(code, "m").exit_code == exit_code


def test_unknown_code_defaults_to_1_and_explicit_wins():
    assert AxiError("WEIRD", "m").exit_code == 1
    assert AxiError("NOT_FOUND", "m", exit_code=5).exit_code == 5


def test_attributes_and_str():
    e = AxiError("NOT_FOUND", "no such file", help=("a", "b"), detail="trace")
    assert (e.code, e.message, e.help, e.detail) == ("NOT_FOUND", "no such file", ["a", "b"], "trace")
    assert str(e) == "no such file"


def test_to_result_shape_and_order():
    r = to_result(AxiError("VALIDATION_ERROR", "bad", help=["try x"], detail="secret"))
    assert list(r.data) == ["error", "code", "help"]
    assert r.data == {"error": "bad", "code": "VALIDATION_ERROR", "help": ["try x"]}
    assert r.help == ["try x"] and r.exit_code == 2


def test_detail_never_in_stdout_data():
    r = to_result(AxiError("INTERNAL", "boom", detail="Traceback..."))
    assert "detail" not in r.data
    assert "Traceback" not in repr(r.data)


def test_no_help_omits_key():
    assert "help" not in to_result(AxiError("INTERNAL", "boom")).data
