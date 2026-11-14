"""差分エディタ (/api/diff-sources と、/diff の「差分エディタ」ボタン) のテスト。一時リポジトリで、差分の種類ごとに両側の内容を確かめる。"""
import os
import subprocess
import tempfile
import unittest

from git_graph import app_state as state
from git_graph import history
from git_graph import pages
from git_graph.i18n import UserError
from git_graph.routing import ROUTES

ONE = "one\ntwo\nthree\n"
TWO = "one\nTWO\nthree\n"


class DiffSourcesTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = self.temp_dir.name
        self.saved = (state.REPO, state.WIP_ENABLED, state.HAS_WORKTREE, state.WRITABLE, state.UI_LANG)
        state.REPO, state.WIP_ENABLED, state.HAS_WORKTREE, state.WRITABLE, state.UI_LANG = self.repo, True, True, True, "en"
        self.git("init", "-q")
        self.git("config", "user.name", "Test User")
        self.git("config", "user.email", "test@example.com")
        self.git("branch", "-M", "main")
        self.write("f.txt", ONE)
        self.commit("first")
        self.first = self.rev("HEAD")
        self.write("f.txt", TWO)
        self.commit("second")
        self.second = self.rev("HEAD")

    def tearDown(self):
        state.REPO, state.WIP_ENABLED, state.HAS_WORKTREE, state.WRITABLE, state.UI_LANG = self.saved
        self.temp_dir.cleanup()

    def git(self, *args):
        r = subprocess.run(["git", "-C", self.repo, *args], capture_output=True)
        if r.returncode != 0:
            raise AssertionError(r.stderr.decode("utf-8", "replace"))
        return r.stdout.decode("utf-8", "replace")

    def rev(self, name):
        return self.git("rev-parse", name).strip()

    def write(self, path, content, binary=False):
        full = os.path.join(self.repo, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(content if binary else content.encode("utf-8"))

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def sources(self, **q):
        """/api/diff-sources を、/diff のクエリと同じ形 (値は 1 つの文字列) で呼ぶ。"""
        return ROUTES[("GET", "/api/diff-sources")]({k: [v] for k, v in q.items()})

    def test_route_is_registered_and_read_only(self):
        self.assertIn(("GET", "/api/diff-sources"), ROUTES)
        self.assertNotIn(("POST", "/api/diff-sources"), ROUTES)

    def test_commit_compares_with_the_first_parent(self):
        r = self.sources(hash=self.second, path="f.txt")
        self.assertEqual((r["old"]["content"], r["new"]["content"]), (ONE, TWO))
        self.assertEqual((r["old"]["label"], r["new"]["label"]), ("Parent " + self.first[:7], self.second[:7]))
        self.assertTrue(r["old"]["exists"] and r["new"]["exists"])
        self.assertEqual((r["old"]["eol"], r["new"]["encoding"] != ""), ("lf", True))

    def test_root_commit_has_no_old_side(self):
        r = self.sources(hash=self.first, path="f.txt")
        self.assertFalse(r["old"]["exists"])
        self.assertEqual(r["old"]["content"], "")
        self.assertEqual(r["new"]["content"], ONE)

    def test_added_and_deleted_files(self):
        self.write("new.txt", "hello\n")
        self.commit("add")
        added = self.rev("HEAD")
        self.git("rm", "-q", "new.txt")
        self.commit("remove")
        removed = self.rev("HEAD")
        r = self.sources(hash=added, path="new.txt")
        self.assertEqual((r["old"]["exists"], r["new"]["content"]), (False, "hello\n"))
        r = self.sources(hash=removed, path="new.txt")
        self.assertEqual((r["old"]["content"], r["new"]["exists"]), ("hello\n", False))

    def test_rename_reads_the_old_side_from_the_old_path(self):
        self.git("mv", "f.txt", "g.txt")
        self.commit("rename")
        r = self.sources(hash=self.rev("HEAD"), path="g.txt", old="f.txt")
        self.assertEqual((r["old"]["content"], r["new"]["content"]), (TWO, TWO))
        self.assertEqual(r["old_path"], "f.txt")
        self.assertFalse(self.sources(hash=self.rev("HEAD"), path="g.txt")["old"]["exists"])  # old を渡さなければ、新しいパスで探す

    def test_between_two_commits(self):
        r = self.sources(**{"from": self.first, "to": self.second, "path": "f.txt"})
        self.assertEqual((r["old"]["content"], r["new"]["content"]), (ONE, TWO))
        self.assertEqual((r["old"]["label"], r["new"]["label"]), ("A " + self.first[:7], "B " + self.second[:7]))

    def test_compare_with_the_working_tree(self):
        self.write("f.txt", "work\n")
        r = self.sources(**{"from": self.first, "wip": "compare", "path": "f.txt"})
        self.assertEqual((r["old"]["content"], r["new"]["content"]), (ONE, "work\n"))
        self.assertEqual((r["old"]["label"], r["new"]["label"]), ("A " + self.first[:7], "Working tree"))

    def test_wip_staged_unstaged_untracked(self):
        self.write("f.txt", "staged\n")
        self.git("add", "f.txt")
        self.write("f.txt", "worktree\n")
        r = self.sources(wip="staged", path="f.txt")
        self.assertEqual((r["old"]["content"], r["new"]["content"]), (TWO, "staged\n"))
        self.assertEqual((r["old"]["label"], r["new"]["label"]), ("HEAD", "Index (staged)"))
        r = self.sources(wip="unstaged", path="f.txt")
        self.assertEqual((r["old"]["content"], r["new"]["content"]), ("staged\n", "worktree\n"))
        self.write("u.txt", "untracked\n")
        r = self.sources(wip="untracked", path="u.txt")
        self.assertFalse(r["old"]["exists"])
        self.assertEqual((r["old"]["label"], r["new"]["content"]), ("(none)", "untracked\n"))

    def test_wip_amend_compares_the_parent_of_the_last_commit(self):
        self.write("f.txt", "work\n")
        r = self.sources(wip="amend", path="f.txt")
        self.assertEqual((r["old"]["content"], r["new"]["content"]), (ONE, "work\n"))
        self.assertEqual(r["old"]["label"], "Parent of the last commit " + self.first[:7])

    def test_wip_amend_of_the_root_commit_has_no_old_side(self):
        self.git("checkout", "-q", "--orphan", "fresh")
        self.git("rm", "-q", "-rf", ".")
        self.write("f.txt", "root\n")
        self.commit("root")
        r = self.sources(wip="amend", path="f.txt")
        self.assertFalse(r["old"]["exists"])
        self.assertEqual(r["old"]["label"], "Parent of the last commit")

    def test_conflict_compares_head_with_the_working_tree_file(self):
        self.git("checkout", "-q", "-b", "side", self.first)
        self.write("f.txt", "side\n")
        self.commit("side")
        self.git("checkout", "-q", "main")
        r = subprocess.run(["git", "-C", self.repo, "merge", "side"], capture_output=True)
        self.assertNotEqual(r.returncode, 0)
        r = self.sources(wip="conflict", path="f.txt")
        self.assertEqual(r["old"]["content"], TWO)
        self.assertIn("<<<<<<<", r["new"]["content"])

    def test_binary_and_line_endings_and_truncation(self):
        self.write("b.bin", b"\x00\x01\x02binary", binary=True)
        self.write("c.txt", b"a\r\nb\r\n", binary=True)
        self.commit("more")
        h = self.rev("HEAD")
        self.assertTrue(self.sources(hash=h, path="b.bin")["new"]["binary"])
        self.assertEqual(self.sources(hash=h, path="b.bin")["new"]["content"], "")
        self.assertEqual(self.sources(hash=h, path="c.txt")["new"]["eol"], "crlf")
        self.write("big.txt", "x" * 2_100_000)
        r = self.sources(wip="untracked", path="big.txt")
        self.assertTrue(r["new"]["truncated"])
        self.assertEqual(len(r["new"]["content"]), 2_000_000)

    def test_a_directory_or_missing_path_has_no_content_on_either_side(self):
        r = self.sources(hash=self.second, path="nothing.txt")
        self.assertFalse(r["old"]["exists"] or r["new"]["exists"])

    def test_bad_input_is_rejected(self):
        for q in ({"hash": self.second, "path": ""}, {"hash": self.second, "path": "../x"}, {"hash": self.second, "path": "a\0b"},
                  {"hash": "nothex", "path": "f.txt"}, {"wip": "bogus", "path": "f.txt"},
                  {"from": "zz", "to": self.second, "path": "f.txt"}, {"hash": self.second, "path": "f.txt", "old": "../x"}):
            with self.assertRaises(ValueError, msg=str(q)):
                self.sources(**q)

    def test_wip_needs_a_working_tree(self):
        state.HAS_WORKTREE = False
        with self.assertRaises(UserError):
            self.sources(wip="unstaged", path="f.txt")


class DiffEditorPageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = self.temp_dir.name
        self.saved = (state.REPO, state.WIP_ENABLED, state.HAS_WORKTREE, state.WRITABLE, state.UI_LANG)
        state.REPO, state.WIP_ENABLED, state.HAS_WORKTREE, state.WRITABLE, state.UI_LANG = self.repo, True, True, False, "en"
        run = lambda *a: subprocess.run(["git", "-C", self.repo, *a], capture_output=True, check=True)
        run("init", "-q")
        run("config", "user.name", "Test User")
        run("config", "user.email", "test@example.com")
        with open(os.path.join(self.repo, "f.txt"), "w", encoding="utf-8") as f:
            f.write(ONE)
        run("add", "-A")
        run("commit", "-q", "-m", "first")
        with open(os.path.join(self.repo, "f.txt"), "w", encoding="utf-8") as f:
            f.write(TWO)
        run("add", "-A")
        run("commit", "-q", "-m", "second")
        self.head = subprocess.run(["git", "-C", self.repo, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()

    def tearDown(self):
        state.REPO, state.WIP_ENABLED, state.HAS_WORKTREE, state.WRITABLE, state.UI_LANG = self.saved
        self.temp_dir.cleanup()

    def test_button_and_box_are_on_a_file_diff_page_even_when_read_only(self):
        page = pages.render_diff_page({"hash": [self.head], "path": ["f.txt"]})
        for needle in ('id="dedit"', 'id="dbox"', ">Diff editor<", "function createDiffEditorView", '"diff_editor_close"', '"diff_editor_prev"',
                       '"diff_editor_collapse"', '"editor_find"', '"edit_vim"', '"vim_readonly_tip"'):
            self.assertIn(needle, page)
        self.assertIn("/api/diff-sources", page)
        self.assertIn("/static/vendor/text_editor.js", page)  # エディタ本体は、ボタンを押したときに読み込む
        self.assertNotIn("function createDiffEditor(", page)  # ページの HTML にはエディタ (1MB) を埋め込まない

    def test_the_files_page_shows_the_diff_in_the_right_pane(self):
        files_html = pages.read_asset("files.html")
        self.assertIn('fileDiffBtn.onclick = () => { if (currentFile && diffParams(currentFile)) openView("diff"); };', files_html)
        self.assertIn('else if (view === "diff") loadFileDiff(currentFile);', files_html)  # view=diff は、再読み込み・戻る/進むでも復元する
        self.assertNotIn('location.href = "/diff?" + new URLSearchParams({...p', files_html)  # 別のページへは移らない
        page = pages.render_files_page()
        self.assertIn("function createDiffEditorView", page)  # 共通の表示部品が、files ページにも入る
        self.assertNotIn("/*__DIFF_EDITOR_VIEW__*/", page)
        self.assertIn('id="viewVim"', files_html)  # ファイルの表示 (読み取り専用) にも Vim モードの切り替えがある
        self.assertIn("readOnly: true, vim: vimOn", files_html)
        self.assertIn("vim: {get: () => vimOn, set: setVimPref}", files_html)  # 差分エディタも、表示・編集と同じ設定
        self.assertNotIn("function createDiffEditor(", page)  # エディタ本体は埋め込まない
        diff_page = pages.render_diff_page({"hash": [self.head], "path": ["f.txt"], "editor": ["1"]})
        self.assertIn('get("editor") === "1"', diff_page)  # /diff は、editor=1 でも最初から差分エディタで出す

    def test_legacy_diff_urls_map_to_file_view_queries(self):
        self.assertEqual(
            pages.diff_page_url({"hash": [self.head], "path": ["f.txt"]}),
            f"/files?hash={self.head}&path=&file=f.txt&view=diff",
        )
        self.assertEqual(
            pages.diff_page_url({"from": [self.head], "wip": ["compare"], "path": ["f.txt"]}),
            f"/files?hash=WIP&path=&file=f.txt&view=diff&dfrom={self.head}&dto=WIP",
        )

    def test_the_files_page_left_pane_switches_between_the_file_list_and_the_changed_files(self):
        page = pages.render_files_page()
        for needle in ('id="sideTabs"', 'id="sideTreeBtn"', 'id="sideChangesBtn"', 'id="changes"', "Files</button>", "Changed files</button>"):
            self.assertIn(needle, page)
        files_html = pages.read_asset("files.html")
        self.assertIn('sideTabs.hidden = !hash;', files_html)  # コミットも作業ツリーも出す
        self.assertIn('changesEl.hidden = !changes;', files_html)
        self.assertIn('openWipChange(from && to', files_html)  # WIP の比較差分は、右ペインで開く
        self.assertIn('p.set("dwip", diffSel.wip);', files_html)  # 差分の種類は URL に残し、再読み込みで復元する
        self.assertIn("loadWipChanges();", files_html)  # WIP タブの比較一覧を読み込む
        self.assertIn('return compareTo === "WIP" && compareFrom ? compareFrom : wipHead;', files_html)
        self.assertIn("const seq = ++wipChangesSeq, head = wipCompareFrom();", files_html)
        self.assertIn("wipCompareChanges[f.path] = f;", files_html)
        self.assertIn("changeBadge(e, path, wipCompareChanges, wipCompareDirs, COMPARE_MARK_KEY", files_html)
        self.assertIn("function wipStatusForPath(path)", files_html)
        self.assertIn('status.untracked || []).map(f => Object.assign({wip: "untracked"}, f))', files_html)
        self.assertIn("return Object.assign({}, f, {st: st});", files_html)
        self.assertIn("function wipSelectionEnabled()", files_html)
        self.assertIn("compareFrom === wipHead", files_html)
        self.assertIn("selBar.hidden = !wipSelectionEnabled();", files_html)
        self.assertIn("const check = wipSelectionEnabled() ?", files_html)
        self.assertIn("function activeSelectionBoxes()", files_html)
        self.assertIn('id="wipCommitView"', files_html)
        self.assertNotIn('id="wipCommitOpen"', files_html)
        self.assertIn('id="wipCommitMenuBtn"', page)
        self.assertLess(page.index('id="wipCommitMenuBtn"'), page.index('id="pushBtn"'))
        commit_menu = page[page.index('id="wipCommitMenuBtn"'):page.index("</button>", page.index('id="wipCommitMenuBtn"'))]
        self.assertNotIn("hidden", commit_menu)
        self.assertIn("wipCommitMenuBtn.disabled = !CAN_COPY;", files_html)
        self.assertIn('else location.href = "/files?hash=WIP&wip_commit=1";', files_html)
        self.assertIn('mountWipPanel(document.getElementById("wipCommitPanel"), {onFiles: wipFilesChanged})', pages.render_files_page())
        self.assertIn('history.pushState({path: current, file: path, view: "diff"}', files_html)  # 変更ファイルは、右ペインの差分で開く
        self.assertIn("gitGraphFilesSide", files_html)  # 選んだ表示は引き継ぐ
        self.assertIn('const rows = [...activeList().querySelectorAll(".entry")];', files_html)  # ↑↓ Enter は、出している方の一覧で働く

    def test_no_button_when_no_file_is_selected(self):
        page = pages.render_diff_page({"hash": [self.head]})
        self.assertNotIn('id="dedit"', page)
        self.assertNotIn('id="dbox"', page)
        self.assertNotIn("/api/diff-sources", page)

    def test_the_button_comes_after_the_links_so_the_keyboard_focus_stays_on_the_first_link(self):
        page = pages.render_diff_page({"path": ["f.txt"], "wip": ["unstaged"]})
        h1 = page[page.index("<h1>"):page.index("</h1>")]
        self.assertIn('id="dedit"', h1)
        for link in ("view=edit", "/files?"):
            if link in h1:
                self.assertLess(h1.index(link), h1.index('id="dedit"'))


if __name__ == "__main__":
    unittest.main()
