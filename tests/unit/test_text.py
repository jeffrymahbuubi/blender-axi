from blender_axi.spec import TRUNCATE_CHARS
from blender_axi.text import truncate


def test_short_text_untouched():
    assert truncate("abc") == ("abc", 3, False)


def test_exact_limit_not_truncated():
    s = "x" * TRUNCATE_CHARS
    assert truncate(s) == (s, TRUNCATE_CHARS, False)


def test_over_limit_truncated():
    s = "x" * (TRUNCATE_CHARS + 5)
    shown, total, truncated = truncate(s)
    assert (len(shown), total, truncated) == (TRUNCATE_CHARS, TRUNCATE_CHARS + 5, True)


def test_custom_limit():
    assert truncate("abcdef", limit=3) == ("abc", 6, True)


def test_full_never_cuts():
    s = "y" * 10_000
    assert truncate(s, full=True) == (s, 10_000, False)


def test_empty():
    assert truncate("") == ("", 0, False)
