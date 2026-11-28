import os
import shutil
import subprocess
import unittest
from pathlib import Path

from tests.ui_server import start_ui_server

HERE = Path(__file__).resolve().parent
NODE = shutil.which("node")


def jsdom_available():
    if not NODE:
        return False
    return subprocess.run([NODE, "-e", 'require("jsdom")'], capture_output=True, env=os.environ, check=False).returncode == 0


@unittest.skipUnless(jsdom_available(), "node と jsdom が必要")
class SideTreeUiTests(unittest.TestCase):
    """左ペインのツリー (フォルダのクリックで展開・右ペインで開くアイコン)・ドラッグ&ドロップ・選択してまとめての移動を、
    実サーバー (別プロセス) と実際の /files ページのスクリプト (jsdom) で通しで操作する。"""

    def setUp(self):
        files = {"a/x.txt": "x\n", "a/sub/y.txt": "y\n", "b/z.txt": "z\n", "c/keep.txt": "k\n", "dest/keep.txt": "d\n",
                 "top.txt": "t\n", "other.txt": "o\n", "m1.txt": "1\n", "m2.txt": "2\n", "m3.txt": "3\n"}
        self.repo, self.port = start_ui_server(self, files, {"u.txt": "untracked\n"})  # u.txt は未追跡

    def test_tree_drag_and_drop_and_multi_select(self):
        env = dict(os.environ, GG_BASE=f"http://127.0.0.1:{self.port}", GG_REPO=self.repo)
        r = subprocess.run([NODE, str(HERE / "side_tree_ui_behavior.js")], capture_output=True, text=True, encoding="utf-8", env=env, check=False, timeout=180)
        self.assertEqual(r.returncode, 0, r.stderr or r.stdout)

    def test_tree_in_commit_view_direct_urls_and_tab_switch(self):
        head = subprocess.run(["git", "-C", self.repo, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        env = dict(os.environ, GG_BASE=f"http://127.0.0.1:{self.port}", GG_HEAD=head)
        r = subprocess.run([NODE, str(HERE / "side_tree_open_behavior.js")], capture_output=True, text=True, encoding="utf-8", env=env, check=False, timeout=180)
        self.assertEqual(r.returncode, 0, r.stderr or r.stdout)


if __name__ == "__main__":
    unittest.main()
