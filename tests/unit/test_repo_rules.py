"""Repo-wide rules (plan U7 / section 7): file size, temp discipline, lazy imports, help, silent stderr."""

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from blender_axi import cli, helptext, registry

REPO = Path(__file__).resolve().parents[2]
LIMIT = 500
SKIP_DIRS = {"__pycache__", ".venv", ".tmp", "worktrees", ".git"}


def _files(base: str, pattern: str):
    for p in sorted((REPO / base).rglob(pattern)):
        if not SKIP_DIRS & set(p.relative_to(REPO).parts):
            yield p


def _line_count(p: Path) -> int:
    return len(p.read_text(encoding="utf-8").splitlines())


@pytest.mark.parametrize("base,pattern", [("src", "*.py"), ("tests", "*.py"), ("benchmarks", "*.py"), ("docs", "*.md")])
def test_files_under_500_lines(base, pattern):
    files = list(_files(base, pattern))
    if not files and base == "docs":
        pytest.skip("no docs/ on this branch (the public main is product-only)")
    assert files, f"no files found under {base}"
    too_long = {str(p.relative_to(REPO)): _line_count(p) for p in files if _line_count(p) >= LIMIT}
    assert not too_long, f"files must stay under {LIMIT} lines: {too_long}"


def test_tempfile_only_in_paths():
    offenders = []
    for p in _files("src", "*.py"):
        if p.name == "paths.py" and p.parent.name == "blender_axi":
            continue
        text = p.read_text(encoding="utf-8")
        if "import tempfile" in text or "from tempfile" in text:
            offenders.append(str(p.relative_to(REPO)))
    assert not offenders, f"only paths.py may import tempfile: {offenders}"


def test_version_fast_path_imports_nothing_heavy():
    code = (
        "import sys, json\n"
        "from blender_axi.cli import main\n"
        "rc = main(['--version'])\n"
        "heavy = [m for m in ('blender_axi.runner', 'blender_axi.toon', 'blender_axi.flags') if m in sys.modules]\n"
        "sys.stderr.write(json.dumps({'rc': rc, 'heavy': heavy}))\n"
    )
    env = dict(os.environ, PYTHONPATH=str(REPO / "src"))
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stderr) == {"rc": 0, "heavy": []}
    assert proc.stdout.strip() == importlib.import_module("blender_axi").__version__


def _load(target: str):
    module, _, attr = target.partition(":")
    return getattr(importlib.import_module(module), attr)


def test_registry_entries_resolve():
    targets = {**registry.COMMANDS, "<home>": registry.HOME}
    for name, target in targets.items():
        spec = _load(target)
        assert spec.name and spec.summary and callable(spec.run), name
        assert spec.examples, f"{name} needs at least one example"
        assert all(ex.startswith("blender-axi") for ex in spec.examples), name


def test_every_command_help_exits_zero(capsys):
    for name, target in registry.COMMANDS.items():
        spec = _load(target)
        assert cli.main([name, "--help"]) == 0, name
        out = capsys.readouterr()
        assert "usage:" in out.out and out.err == "", name
        for sub in spec.subcommands or {}:
            assert cli.main([name, sub, "--help"]) == 0, f"{name} {sub}"
            out = capsys.readouterr()
            assert "usage:" in out.out and out.err == ""
    assert helptext.render(_load(registry.HOME))["usage"].startswith("blender-axi home")


def test_top_level_help_is_silent_on_stderr(capsys):
    assert cli.main(["--help"]) == 0
    out = capsys.readouterr()
    assert out.err == ""
    assert "usage:" in out.out
