import json
import shutil
import subprocess
import unittest

from git_graph import pages
from tests.helpers import page_with_assets


@unittest.skipUnless(shutil.which("node"), "node が必要")
class FilesWipRmSelectionTests(unittest.TestCase):
    """ファイル表示 (WIP) の左ペインで、変更のない追跡中のファイルをチェックしても git rm の対象になる。"""

    @classmethod
    def setUpClass(cls):
        page = page_with_assets(pages.render_files_page(), ("js",))
        start = page.index("function stageKey(kind, path)")
        cls.helpers = page[start:page.index("function setStageChecked(kind, path, value)", start)]
        start = page.index("function checkedStagePaths()")
        cls.target = page[start:page.index("function setStageFromBox(", start)]

    def run_case(self, status_files, overrides):
        script = ("let wipStatusFiles = %s;\nconst stageOverrides = new Map(%s);\n%s\n%s\n"
                  "console.log(JSON.stringify(checkedStagePaths().sort()));") % (
            json.dumps(status_files), json.dumps(overrides), self.helpers, self.target)
        r = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8", check=True)
        return json.loads(r.stdout)

    def test_unchanged_tracked_file_checked_in_tree_is_selected(self):
        self.assertEqual(self.run_case([], [["tree\0src/a.py", True]]), ["src/a.py"])

    def test_unchecked_tree_entry_is_not_selected(self):
        self.assertEqual(self.run_case([], [["tree\0src/a.py", False]]), [])

    def test_changed_files_still_follow_their_own_checks(self):
        status = [{"wip": "unstaged", "path": "b.py"}, {"wip": "staged", "path": "c.py"}]
        self.assertEqual(self.run_case(status, [["unstaged\0b.py", True], ["staged\0c.py", False]]), ["b.py"])

    def test_tree_entry_covering_changed_files_is_not_added_as_a_directory(self):
        status = [{"wip": "unstaged", "path": "d/x.py"}]
        self.assertEqual(self.run_case(status, [["tree\0d", True]]), ["d/x.py"])  # 変更のあるファイルとして1件だけ


if __name__ == "__main__":
    unittest.main()
