import os
import shutil
import subprocess
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
NODE = shutil.which("node")


def jsdom_available():
    if not NODE:
        return False
    return subprocess.run([NODE, "-e", 'require("jsdom")'], capture_output=True, env=os.environ, check=False).returncode == 0


class WebviewFindBarTests(unittest.TestCase):
    """pywebview の Ctrl+F の検索バー (git_graph_webview.py の _FIND_BAR_JS) が、CodeMirror のエディタの検索 (Ctrl+F / F3) を
    邪魔しないことを、jsdom で動かして確かめる。node と jsdom (npm i jsdom) が無い環境では飛ばす。"""

    @unittest.skipUnless(jsdom_available(), "node と jsdom が必要")
    def test_find_bar_leaves_editor_search_alone(self):
        r = subprocess.run([NODE, str(HERE / "webview_find_bar_behavior.js")], capture_output=True, text=True, encoding="utf-8", env=os.environ, check=False)
        self.assertEqual(r.returncode, 0, r.stderr or r.stdout)


if __name__ == "__main__":
    unittest.main()
