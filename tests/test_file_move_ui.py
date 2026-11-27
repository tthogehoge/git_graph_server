import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.request import urlopen

from git_graph import pages
from tests.helpers import page_with_assets

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
NODE = shutil.which("node")


def jsdom_available():
    if not NODE:
        return False
    return subprocess.run([NODE, "-e", 'require("jsdom")'], capture_output=True, env=os.environ, check=False).returncode == 0


class FileMoveWiringTests(unittest.TestCase):
    """ファイル表示の「名前の変更・移動」の部品がそろっていること (node が無くても確かめられる分)。"""

    def test_buttons_are_in_the_page_and_hidden_while_editing(self):
        html = pages.render_files_page()
        self.assertIn('id="fileMoveBtn"', html)
        self.assertIn('id="dirMoveBtn"', html)
        edit = (ROOT / "git_graph" / "static" / "files" / "js" / "06_edit.js").read_text(encoding="utf-8")
        self.assertEqual(edit.count("fileUploadBtn, fileMoveBtn, fileExtEditBtn"), 2)  # 編集に入るとき (2 か所) に隠す

    def test_every_text_the_dialog_uses_is_translated(self):
        script = page_with_assets(pages.render_files_page(), ("js",))
        template = (ROOT / "git_graph" / "static" / "files" / "files.html").read_text(encoding="utf-8")  # 描画前の {{key}} を読む
        keys = set(re.findall(r'\bt\("(move_\w+)"', script)) | set(re.findall(r'\{\{(\w*move\w*)\}\}', template))
        self.assertTrue({"move_hint", "move_done", "tip_move_file", "tip_move_dir", "move_btn"} <= keys, keys)  # 拾えていること
        keys |= {"op_label_file_move", "move_exists", "move_missing", "move_into_self", "move_same", "move_dest_invalid"}  # サーバーが返す文言
        for lang in ("ja", "en"):
            data = json.loads((ROOT / "git_graph" / "locales" / f"{lang}.json").read_text(encoding="utf-8"))
            self.assertEqual(sorted(k for k in keys if k not in data), [], lang)


@unittest.skipUnless(jsdom_available(), "node と jsdom が必要")
class FileMoveUiTests(unittest.TestCase):
    """実サーバー (別プロセス) と、実際の /files ページのスクリプト (jsdom) で、名前の変更・移動のダイアログを通しで操作する。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = self.tmp.name

        def git(*args):
            subprocess.run(["git", "-C", self.repo, *args], check=True, capture_output=True)

        git("init", "-q")
        git("config", "user.name", "Test User")
        git("config", "user.email", "test@example.com")
        for path, text in (("dir/a.txt", "a\n"), ("keep.txt", "k\n")):
            full = Path(self.repo, path)
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(text, encoding="utf-8")
        git("add", ".")
        git("commit", "-q", "-m", "init")
        Path(self.repo, "u.txt").write_text("untracked\n", encoding="utf-8")  # 未追跡
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.server = subprocess.Popen([sys.executable, str(ROOT / "git_graph_server.py"), self.repo, "--port", str(self.port), "--lang", "en", "--allow-write"],
                                       cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(self.stop)
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                urlopen(f"http://127.0.0.1:{self.port}/", timeout=1).close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            self.fail("サーバーが起動しない")

    def stop(self):
        self.server.terminate()
        try:
            self.server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.server.kill()
        self.tmp.cleanup()

    def test_rename_and_move_through_the_dialog(self):
        env = dict(os.environ, GG_BASE=f"http://127.0.0.1:{self.port}", GG_REPO=self.repo)
        r = subprocess.run([NODE, str(HERE / "file_move_ui_behavior.js")], capture_output=True, text=True, encoding="utf-8", env=env, check=False, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr or r.stdout)


if __name__ == "__main__":
    unittest.main()
