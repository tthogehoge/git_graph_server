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
    return subprocess.run([NODE, "-e", 'require("jsdom")'], capture_output=True, env=os.environ).returncode == 0


class WipPanelBehaviorTests(unittest.TestCase):
    """グラフ・ファイル表示・差分で共通の WIP の一覧とコミットフォーム (wip_panel.js) を、jsdom で動かして確かめる。
    node と jsdom (npm i jsdom) が無い環境では飛ばす。"""

    @unittest.skipUnless(jsdom_available(), "node と jsdom が必要")
    def test_shared_panel_behaviour(self):
        r = subprocess.run([NODE, str(HERE / "wip_panel_behavior.js")], capture_output=True, text=True, encoding="utf-8", env=os.environ)
        self.assertEqual(r.returncode, 0, r.stderr or r.stdout)


if __name__ == "__main__":
    unittest.main()
