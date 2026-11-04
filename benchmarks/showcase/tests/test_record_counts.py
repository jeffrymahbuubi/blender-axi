"""Blender-start counting on the command shapes real agents send (seen in the 2026-11-04 pilot)."""

from sc.record import blender_starts_in


def test_counts_each_blender_axi_segment_of_a_chained_command():
    cmd = 'cd "<run>" && blender-axi objects work.blend 2>&1; blender-axi object Mug work.blend'
    assert blender_starts_in("Bash", cmd) == 2


def test_ignores_help_version_other_tools_and_non_bash():
    assert blender_starts_in("Bash", 'cd "<run>" && blender-axi add --help 2>&1 | head -60') == 0
    assert blender_starts_in("Bash", "blender-axi --version") == 0
    assert blender_starts_in("Bash", 'cd "<run>" && ls -la out && ls -la work.blend') == 0
    assert blender_starts_in("Read", "blender-axi scene work.blend") == 0


def test_plain_command_counts_once():
    assert blender_starts_in("Bash", "blender-axi render work.blend --fit --out out/hero.png") == 1
