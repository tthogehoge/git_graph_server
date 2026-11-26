import os
import shutil
import subprocess
import unittest
from pathlib import Path

from git_graph import pages

HERE = Path(__file__).resolve().parent
NODE = shutil.which("node")


def jsdom_available():
    if not NODE:
        return False
    return subprocess.run([NODE, "-e", 'require("jsdom")'], capture_output=True, env=os.environ, check=False).returncode == 0


class MdAttachTests(unittest.TestCase):
    """Markdown の編集で、画像のドロップ / 図のペーストを attachments へアップロードしてリンクにする (files/js/06_md_attach.js)。"""

    def test_files_page_loads_the_script_and_the_markdown_editor_uses_it(self):
        html = pages.render_files_page()
        self.assertIn('<script src="/static/files/js/06_md_attach.js"></script>', html)
        edit = pages.read_asset("files/js/06_edit.js")
        start = edit.index("if (mdEdit) {\n      MdAttach.bind(host")  # Markdown の編集だけ (HTML や、ほかのファイルの編集には付けない)
        self.assertIn("getView: () => textEditor && textEditor.view", edit[start:start + 400])
        self.assertEqual(edit.count("MdAttach.bind("), 1)

    @unittest.skipUnless(jsdom_available(), "node と jsdom が必要")
    def test_paste_and_drop_upload_to_attachments_and_insert_links(self):
        r = subprocess.run([NODE, str(HERE / "md_attach_behavior.js")], capture_output=True, text=True, encoding="utf-8", env=os.environ, check=False)
        self.assertEqual(r.returncode, 0, r.stderr or r.stdout)


if __name__ == "__main__":
    unittest.main()
