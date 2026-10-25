"""Unit tests for blender_axi.toon (plan U1)."""

import json
import math
from pathlib import Path

import pytest

from blender_axi.toon import encode, encode_json


def lines(value, **kw):
    return encode(value, **kw).split("\n")


# -- scalar quoting (spec 7.2), one assertion per rule ----------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("", '""'),
        (" lead", '" lead"'),
        ("trail ", '"trail "'),
        ("\tx", '"\\tx"'),
        ("true", '"true"'),
        ("false", '"false"'),
        ("null", '"null"'),
        ("42", '"42"'),
        ("-3.14", '"-3.14"'),
        ("05", '"05"'),
        ("+1", '"+1"'),
        ("1e-6", '"1e-6"'),
        ("a:b", '"a:b"'),
        ('say "hi"', '"say \\"hi\\""'),
        ("back\\slash", '"back\\\\slash"'),
        ("a[0]", '"a[0]"'),
        ("a]", '"a]"'),
        ("{x}", '"{x}"'),
        ("a,b", '"a,b"'),
        ("-x", '"-x"'),
        ("#tag", '"#tag"'),
        ("line1\nline2", '"line1\\nline2"'),
        ("a\rb", '"a\\rb"'),
        ("a\x01b", '"a\\u0001b"'),
        ("\x1f", '"\\u001f"'),
    ],
)
def test_string_quoting(text, expected):
    assert encode({"k": text}) == f"k: {expected}"


@pytest.mark.parametrize("text", ["True", "NULL", "hello world", "a-b", "x#y", "héllo", "😀", "1.2.3", "v1", "e5", "3 of 3 total"])
def test_safe_strings_are_unquoted(text):
    assert encode({"k": text}) == f"k: {text}"


def test_root_scalar_and_bom():
    assert encode("hello") == "hello"
    assert encode(42) == "42"
    assert encode(True) == "true"
    assert encode(None) == "null"
    assert encode("\ufeffx") == '"\ufeffx"'


def test_unpaired_surrogate_errors():
    with pytest.raises(ValueError):
        encode({"k": "a\ud800b"})


# -- numbers (spec 2, 3) ------------------------------------------------------


@pytest.mark.parametrize(
    "num, expected",
    [
        (1.0, "1"),
        (1.5, "1.5"),
        (-0.0, "0"),
        (0, "0"),
        (-7, "-7"),
        (1e6, "1000000"),
        (1e-6, "0.000001"),
        (0.00001, "0.00001"),
        (1.5e-5, "0.000015"),
        (1e16, "10000000000000000"),
        (1.234e16, "12340000000000000"),
        (-2.5e-5, "-0.000025"),
        (0.1, "0.1"),
        (123456.789, "123456.789"),
        (1e-7, "1e-7"),
        (1.5e-7, "1.5e-7"),
        (1e21, "1e+21"),
        (-1.5e300, "-1.5e+300"),
        (10**30, "1" + "0" * 30),
    ],
)
def test_numbers(num, expected):
    assert encode({"n": num}) == f"n: {expected}"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_is_null(bad):
    assert encode({"n": bad}) == "n: null"
    assert encode([bad]) == "[1]: null"


def test_bool_not_treated_as_int():
    assert encode({"a": True, "b": False, "c": None}) == "a: true\nb: false\nc: null"


# -- objects and keys -----------------------------------------------------------


def test_nested_object():
    assert encode({"user": {"id": 1, "name": "Ada", "geo": {"lat": 1.5}}}) == (
        "user:\n  id: 1\n  name: Ada\n  geo:\n    lat: 1.5"
    )


def test_empty_dict_value_and_root():
    assert encode({"a": {}}) == "a:"
    assert encode({}) == ""


def test_key_quoting():
    assert encode({"my-key": 1}) == '"my-key": 1'
    assert encode({"a b": 1}) == '"a b": 1'
    assert encode({"1x": 1}) == '"1x": 1'
    assert encode({"": 1}) == '"": 1'
    assert encode({"ключ": 1}) == '"ключ": 1'
    assert encode({"a.b_c": 1, "_x": 2}) == "a.b_c: 1\n_x: 2"
    assert encode({"my-key": [1, 2]}) == '"my-key"[2]: 1,2'


def test_key_order_preserved():
    assert lines({"z": 1, "a": 2, "m": 3}) == ["z: 1", "a: 2", "m: 3"]


def test_custom_indent():
    assert encode({"a": {"b": 1}}, indent=4) == "a:\n    b: 1"


# -- arrays ---------------------------------------------------------------------


def test_empty_list():
    assert encode({"k": []}) == "k: []"
    assert encode([]) == "[]"


def test_inline_primitive_list():
    assert encode({"tags": ["a", "b"]}) == "tags[2]: a,b"
    assert encode({"tags": ["a,b", "c"]}) == 'tags[2]: "a,b",c'
    assert encode({"v": [1, 2.5, True, None, "x"]}) == "v[5]: 1,2.5,true,null,x"
    assert encode({"v": ["", "42"]}) == 'v[2]: "","42"'


def test_tabular():
    value = {"objects": [{"name": "Cube", "type": "MESH"}, {"name": "A, B", "type": "CAM:1"}]}
    assert encode(value) == 'objects[2]{name,type}:\n  Cube,MESH\n  "A, B","CAM:1"'


def test_tabular_uses_first_object_key_order_and_quotes_fields():
    value = [{"b": 1, "a-x": 2}, {"a-x": 4, "b": 3}]
    assert encode(value) == '[2]{b,"a-x"}:\n  1,2\n  3,4'


def test_tabular_primitive_cells():
    value = {"r": [{"a": None, "b": True}, {"a": 1.0, "b": False}]}
    assert encode(value) == "r[2]{a,b}:\n  null,true\n  1,false"


def test_non_uniform_falls_back_to_list_form():
    value = {"items": [{"name": "x", "n": 1}, {"name": "y"}]}
    assert encode(value) == "items[2]:\n  - name: x\n    n: 1\n  - name: y"


def test_same_keys_but_nested_cell_is_list_form():
    value = {"items": [{"a": 1, "b": [1]}, {"a": 2, "b": [2]}]}
    assert encode(value) == "items[2]:\n  - a: 1\n    b[1]: 1\n  - a: 2\n    b[1]: 2"


def test_empty_object_in_array_is_list_form():
    assert encode({"a": [{}, {"x": 1}]}) == "a[2]:\n  -\n  - x: 1"


def test_mixed_list():
    assert encode({"m": [1, "two", {"k": "v"}, [3, 4]]}) == (
        "m[4]:\n  - 1\n  - two\n  - k: v\n  - [2]: 3,4"
    )


def test_list_of_lists():
    assert encode({"pairs": [[1, 2], [3, 4]]}) == "pairs[2]:\n  - [2]: 1,2\n  - [2]: 3,4"
    assert encode({"p": [[], [1]]}) == "p[2]:\n  - [0]:\n  - [1]: 1"


def test_list_of_nested_non_primitive_lists():
    assert encode({"p": [[{"a": 1}]]}) == "p[1]:\n  - [1]:\n    - a: 1"


def test_list_item_object_first_field_scopes():
    value = {"xs": [{"inner": {"a": 1}, "z": 2}, {"t": [{"a": 1}, {"a": 2}], "z": 3}]}
    assert encode(value) == (
        "xs[2]:\n"
        "  - inner:\n"
        "      a: 1\n"
        "    z: 2\n"
        "  - t[2]{a}:\n"
        "      1\n"
        "      2\n"
        "    z: 3"
    )


def test_root_arrays():
    assert encode([1, 2]) == "[2]: 1,2"
    assert encode([{"a": 1}, {"a": 2}]) == "[2]{a}:\n  1\n  2"
    assert encode([1, {"a": 1}]) == "[2]:\n  - 1\n  - a: 1"


# -- list_keys (D1) ---------------------------------------------------------------


def test_help_is_list_form_by_default():
    assert encode({"help": ["Run `a`", "Run `b`"]}) == "help[2]:\n  - Run `a`\n  - Run `b`"


def test_help_items_are_quoted_when_needed():
    out = encode({"help": ["a: b", "plain", ""]})
    assert out == 'help[3]:\n  - "a: b"\n  - plain\n  - ""'


def test_list_keys_override():
    assert encode({"help": ["a", "b"]}, list_keys=frozenset()) == "help[2]: a,b"
    assert encode({"notes": ["a", "b"]}, list_keys=frozenset({"notes"})) == "notes[2]:\n  - a\n  - b"


def test_list_keys_only_for_string_lists():
    assert encode({"help": [1, 2]}) == "help[2]: 1,2"
    assert encode({"help": []}) == "help: []"


def test_list_keys_nested_depth():
    assert encode({"a": {"help": ["x"]}}) == "a:\n  help[1]:\n    - x"


# -- whole-document properties --------------------------------------------------------


def test_documented_example():
    value = {
        "count": "3 of 3 total",
        "objects": [{"name": "Cube", "type": "MESH"}, {"name": "Camera", "type": "CAMERA"}],
        "help": ["Run `blender-axi object <name>`"],
    }
    assert encode(value) == (
        "count: 3 of 3 total\n"
        "objects[2]{name,type}:\n"
        "  Cube,MESH\n"
        "  Camera,CAMERA\n"
        "help[1]:\n"
        "  - Run `blender-axi object <name>`"
    )


def test_no_trailing_newline_blank_lines_or_cr():
    value = {"a": {"b": [1, {"c": "x\r\ny"}]}, "help": ["h"], "t": [{"n": 1}, {"n": 2}]}
    out = encode(value)
    assert not out.endswith("\n")
    assert "\r" not in out
    assert "" not in out.split("\n")
    assert all(line == line.rstrip(" ") for line in out.split("\n"))


def test_pathlike_and_tuple_and_unknown():
    assert encode({"p": Path("a") / "b"}) in ("p: a/b", 'p: "a\\\\b"')
    assert encode({"t": (1, 2)}) == "t[2]: 1,2"
    assert encode({"o": object()}) == "o: null"


def test_non_string_keys_coerced():
    assert encode({1: "a"}) == '"1": a'


# -- encode_json ---------------------------------------------------------------------------


def test_encode_json_roundtrip_and_unicode():
    value = {"name": "Würfel", "n": [1, 2.5, None], "d": {"x": True}}
    out = encode_json(value)
    assert "Würfel" in out
    assert json.loads(out) == value
    assert out == json.dumps(value, ensure_ascii=False, indent=2)


def test_nan_in_nested_is_null():
    assert encode({"a": {"b": [math.nan]}}) == "a:\n  b[1]: null"
