import os
import socket
import sys
import time
from pathlib import Path

import pytest

from blender_axi import flags, toon
from blender_axi.commands import home
from blender_axi.errors import AxiError
from blender_axi.locate import BlenderInfo


def _inv(argv=()):
    inv, _ = flags.parse(home.SPEC, list(argv))
    return inv


@pytest.fixture
def fake_blender(monkeypatch):
    info = BlenderInfo(Path.home() / "apps" / "blender.exe", (5, 2, 0), "5.2.0 LTS")
    monkeypatch.setattr("blender_axi.locate.find_blender", lambda explicit=None: info)
    return info


@pytest.fixture
def no_live(monkeypatch):
    monkeypatch.setattr(home, "_live_reachable", lambda host, port: False)


def test_collapse_home():
    h = Path.home()
    assert home.collapse_home(h / "x" / "y.exe") == "~/x/y.exe"
    assert home.collapse_home(h) == "~"
    assert not home.collapse_home("/definitely/elsewhere").startswith("~")


def test_data_order_and_blender_line(fake_blender, no_live, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    res = home.run(_inv())
    assert list(res.data) == ["bin", "description", "blender", "live", "blends"]
    assert res.data["blender"] == "5.2.0 LTS (~/apps/blender.exe)"
    assert res.data["live"] == "unreachable localhost:9876"
    assert res.data["blends"].startswith("0 .blend files in ")
    assert res.exit_code == 0


def test_blender_missing_never_raises(monkeypatch, no_live, tmp_path):
    monkeypatch.chdir(tmp_path)

    def boom(explicit=None):
        raise AxiError("BLENDER_NOT_FOUND", "Blender executable not found")

    monkeypatch.setattr("blender_axi.locate.find_blender", boom)
    res = home.run(_inv())
    assert res.data["blender"] == "not found"
    assert any("--blender <path>" in h for h in res.help)


def test_hints_absent_when_blender_found_and_live_down(fake_blender, no_live, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    res = home.run(_inv())
    assert not any("--blender" in h for h in res.help)
    assert not any("--live" in h for h in res.help)
    assert 3 <= len(res.help) <= 5


def test_blends_listing_newest_first(fake_blender, no_live, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    now = time.time()
    for i, name in enumerate(["old.blend", "mid.BLEND", "new.blend"]):
        p = tmp_path / name
        p.write_bytes(b"x" * 2048)
        os.utime(p, (now - 1000 + i * 100, now - 1000 + i * 100))
    (tmp_path / "notes.txt").write_text("no")
    (tmp_path / "dir.blend").mkdir()
    res = home.run(_inv())
    rows = res.data["blends"]
    assert [r["file"] for r in rows] == ["new.blend", "mid.BLEND", "old.blend"]
    assert rows[0]["size_kb"] == 2
    assert set(rows[0]) == {"file", "size_kb", "modified"}
    assert "blends[3]{file,size_kb,modified}:" in toon.encode(res.data)


def test_blends_capped_at_20_with_hint(fake_blender, no_live, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for i in range(23):
        (tmp_path / f"f{i:02d}.blend").write_bytes(b"x")
    res = home.run(_inv())
    assert len(res.data["blends"]) == 20
    assert any("20 of 23" in h for h in res.help)


def test_live_reachable_probe_and_hint(fake_blender, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        res = home.run(_inv(["--host", "127.0.0.1", "--port", str(port)]))
    finally:
        srv.close()
    assert res.data["live"] == f"reachable 127.0.0.1:{port}"
    assert any("add `--live`" in h for h in res.help)


def test_live_unreachable_real_probe(fake_blender, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.close()
    res = home.run(_inv(["--host", "127.0.0.1", "--port", str(port)]))
    assert res.data["live"] == f"unreachable 127.0.0.1:{port}"


def test_live_flags_carried_into_hints(fake_blender, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(home, "_live_reachable", lambda h, p: True)
    res = home.run(_inv(["--live", "--port", "9999"]))
    assert "blender-axi --live --port 9999 scene <file>" in res.help[0]
    assert not any("add `--live`" in h for h in res.help)


def test_bin_prefers_script_then_module(monkeypatch, tmp_path):
    exe = tmp_path / "blender-axi.exe"
    exe.write_text("")
    monkeypatch.setattr(sys, "argv", [str(exe)])
    assert home._bin_path().endswith("blender-axi.exe")
    monkeypatch.setattr(sys, "argv", ["__main__.py"])
    assert home._bin_path().endswith("-m blender_axi")


def test_spec_shape():
    assert home.SPEC.name == "home" and home.SPEC.run is home.run
    assert home.SPEC.positionals == () and home.SPEC.flags == ()


def test_bin_adds_exe_when_launcher_argv0_has_none(monkeypatch, tmp_path):
    (tmp_path / "blender-axi.exe").write_text("")
    monkeypatch.setattr(sys, "argv", [str(tmp_path / "blender-axi")])
    assert home._bin_path().endswith("blender-axi.exe")


def test_bin_falls_back_to_path_lookup(monkeypatch, tmp_path):
    exe = tmp_path / "blender-axi.exe"
    exe.write_text("")
    monkeypatch.setattr(sys, "argv", ["blender-axi"])
    monkeypatch.setattr(home.shutil, "which", lambda name: str(exe) if name == "blender-axi" else None)
    assert home._bin_path().endswith("blender-axi.exe")
    monkeypatch.setattr(home.shutil, "which", lambda name: None)
    assert home._bin_path().endswith("-m blender_axi")
