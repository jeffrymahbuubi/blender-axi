"""Raw benchmark outputs must not depend on the machine: random ports and absolute local paths are normalised."""

from pathlib import PureWindowsPath

import token_bench as tb

REPO = PureWindowsPath("D:/Work/blender-axi")
SCENES = PureWindowsPath("D:/Work/blender-axi/.tmp/bench/scenes")
HOME = PureWindowsPath("C:/Users/someone")


def norm(text):
    return tb.normalise(text, REPO, SCENES, HOME)


def test_scenes_dir_and_repo_root_in_all_spellings():
    assert norm('file: "D:/Work/blender-axi/.tmp/bench/scenes/desk.blend"') == \
        'file: "<repo>/.tmp/bench/scenes/desk.blend"'
    assert norm(r"D:\Work\blender-axi\.tmp\bench\scenes\desk.blend") == r"<repo>/.tmp/bench/scenes\desk.blend"
    assert norm(r'{"file":"D:\\Work\\blender-axi\\.tmp\\bench\\scenes\\desk.blend"}') == \
        r'{"file":"<repo>/.tmp/bench/scenes\\desk.blend"}'
    assert norm("d:/work/BLENDER-AXI/src/x.py") == "<repo>/src/x.py"


def test_home_directory_and_other_text():
    assert norm("C:/Users/someone/.cache/x") == "<home>/.cache/x"
    assert norm("objects[3]: Camera,Light") == "objects[3]: Camera,Light"


def test_port_is_replaced_by_a_same_length_value():
    src = "Run `blender-axi objects --live --host 127.0.0.1 --port 49584` now"
    out = norm(src)
    assert "--port 50000`" in out and len(out) == len(src)


def test_no_home_means_no_home_replacement():
    assert tb.normalise("C:/Users/someone/x", REPO, SCENES) == "C:/Users/someone/x"
