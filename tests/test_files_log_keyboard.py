import unittest

from git_graph import pages
from tests.helpers import page_with_assets


class FilesLogKeyboardTests(unittest.TestCase):
    """ファイル表示のログの行は、グラフ画面のログと同じキー操作 (↑ / ↓ で選択、Enter で遷移) にする。"""

    @classmethod
    def setUpClass(cls):
        page = page_with_assets(pages.render_files_page(), ("js",))
        start = page.index("// ---- ログの行のキーボード操作 (右ペイン)")
        cls.code = page[start:page.index("function loadFileDiff(path)", start)]

    def test_only_up_down_and_enter_are_handled(self):
        self.assertIn('ev.key !== "ArrowUp" && ev.key !== "ArrowDown" && ev.key !== "Enter"', self.code)
        self.assertNotIn('" "', self.code)  # Space は特別扱いしない (グラフ画面と同じ)

    def test_up_down_move_the_selection_not_a_separate_cursor(self):
        self.assertIn("window.mbSetTarget(rows[next].dataset.h)", self.code)  # 移動と同時に、操作の対象にする
        self.assertNotIn("kbcur", self.code)
        self.assertNotIn("kbcur", pages.read_asset("common/commit_log.css"))

    def test_enter_opens_the_selected_commit_like_a_click(self):
        self.assertIn("rows[idx].click()", self.code)
        self.assertIn("if (onButton || idx < 0) return;", self.code)  # ボタンの上の Enter は、そのボタンの操作

    def test_only_commit_rows_and_never_inside_inputs_or_editors(self):
        self.assertIn("tr.clickable[data-h]", self.code)
        self.assertIn(".cm-editor", self.code)
        self.assertIn("input, textarea, select", self.code)

    def test_keyboard_help_lists_the_keys(self):
        self.assertIn("{{keyboard_help_files_log}}", pages.read_asset("common/menubar.html"))


if __name__ == "__main__":
    unittest.main()
