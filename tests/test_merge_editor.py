"""マージエディタ (/merge, /api/merge-sources) のテスト。一時リポジトリで本物の競合を作って確認する。"""
import os
import subprocess
import tempfile
import threading
import unittest
from types import SimpleNamespace
from urllib.request import urlopen

import git_graph_server as app
from git_graph import app_state as state
from git_graph import pages as viewer
from git_graph.i18n import UserError
from git_graph.query import merge as qm

BASE = "one\ntwo\nthree\nfour\nfive\n"
MAIN = "one\nTWO main\nthree\nfour\nfive\n"
SIDE = "one\nTWO side\nthree\nfour\nfive\n"


class MergeEditorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = self.temp_dir.name
        self.saved = (state.REPO, state.WIP_ENABLED, state.HAS_WORKTREE, state.WRITABLE)
        state.REPO, state.WIP_ENABLED, state.HAS_WORKTREE, state.WRITABLE = self.repo, True, True, True
        self.git("init", "-q")
        self.git("config", "user.name", "Test User")
        self.git("config", "user.email", "test@example.com")
        self.git("branch", "-M", "main")

    def tearDown(self):
        state.REPO, state.WIP_ENABLED, state.HAS_WORKTREE, state.WRITABLE = self.saved
        self.temp_dir.cleanup()

    def git(self, *args, check=True):
        r = subprocess.run(["git", "-C", self.repo, *args], capture_output=True)
        if check and r.returncode != 0:
            raise AssertionError(r.stderr.decode("utf-8", "replace"))
        return r.stdout.decode("utf-8", "replace")

    def write(self, path, content, binary=False):
        with open(os.path.join(self.repo, path), "wb") as f:
            f.write(content if binary else content.encode("utf-8"))

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def conflict(self, path="f.txt", base=BASE, main=MAIN, side=SIDE):
        """main と side が同じファイルを変えて、マージが競合して止まった状態を作る。"""
        self.write(path, base)
        self.commit("base")
        self.git("checkout", "-q", "-b", "side")
        self.write(path, side)
        self.commit("side")
        self.git("checkout", "-q", "main")
        self.write(path, main)
        self.commit("main")
        self.git("merge", "side", check=False)

    def test_sources_hold_the_three_versions_and_a_diff3_merge(self):
        self.conflict()
        d = app.api_merge_sources({"path": ["f.txt"]})
        self.assertTrue(d["supported"])
        self.assertEqual((d["ours"], d["theirs"], d["base"]), (MAIN, SIDE, BASE))
        self.assertTrue(d["has_base"])
        self.assertEqual(d["merge_conflicts"], 1)
        self.assertEqual(d["merged"], "one\n<<<<<<< ours\nTWO main\n||||||| base\ntwo\n=======\nTWO side\n>>>>>>> theirs\nthree\nfour\nfive\n")
        self.assertIn("<<<<<<<", d["working"])  # 作業ツリーのファイルは、git が書いた競合マーカーのまま
        self.assertEqual(d["working"], self.git("show", ":0:f.txt", check=False) or d["working"])
        self.assertEqual(len(d["sha"]), 64)
        self.assertFalse(d["rebasing"])

    def test_non_overlapping_changes_merge_cleanly_so_there_is_nothing_to_edit(self):
        self.write("f.txt", "a\nb\nc\nd\ne\nf\ng\nh\ni\n")
        self.commit("base")
        self.git("checkout", "-q", "-b", "side")
        self.write("f.txt", "a\nb\nc\nd\ne\nf\ng\nh\nI side\n")
        self.commit("side")
        self.git("checkout", "-q", "main")
        self.write("f.txt", "A main\nb\nc\nd\ne\nf\ng\nh\ni\n")
        self.commit("main")
        self.git("merge", "-q", "side", "-m", "merge side")
        with self.assertRaises(UserError) as cm:
            qm.merge_sources("f.txt")
        self.assertEqual(cm.exception.key, "merge_not_conflict")

    def test_not_in_conflict_is_an_error(self):
        self.write("f.txt", "x\n")
        self.commit("init")
        with self.assertRaises(UserError) as cm:
            app.api_merge_sources({"path": ["f.txt"]})
        self.assertEqual(cm.exception.key, "merge_not_conflict")

    def test_bad_path_is_rejected(self):
        self.conflict()
        for bad in ("", "../x", ".git/config"):
            with self.assertRaises(ValueError, msg=bad):
                app.api_merge_sources({"path": [bad]})

    def test_add_add_conflict_has_no_base(self):
        self.write("keep.txt", "k\n")
        self.commit("init")
        self.git("checkout", "-q", "-b", "side")
        self.write("new.txt", "side version\n")
        self.commit("side adds")
        self.git("checkout", "-q", "main")
        self.write("new.txt", "main version\n")
        self.commit("main adds")
        self.git("merge", "side", check=False)
        d = app.api_merge_sources({"path": ["new.txt"]})
        self.assertTrue(d["supported"])
        self.assertFalse(d["has_base"])
        self.assertEqual(d["base"], "")
        self.assertIn("<<<<<<< ours", d["merged"])

    def test_modify_delete_conflict_is_unsupported(self):
        self.write("f.txt", BASE)
        self.commit("base")
        self.git("checkout", "-q", "-b", "side")
        self.git("rm", "-q", "f.txt")
        self.commit("side deletes")
        self.git("checkout", "-q", "main")
        self.write("f.txt", MAIN)
        self.commit("main edits")
        self.git("merge", "side", check=False)
        d = app.api_merge_sources({"path": ["f.txt"]})
        self.assertFalse(d["supported"])
        self.assertEqual(d["reason"], "merge_one_side_deleted")

    def test_binary_conflict_is_unsupported(self):
        self.conflict("b.bin", base="a\0b", main="a\0main", side="a\0side")
        d = app.api_merge_sources({"path": ["b.bin"]})
        self.assertFalse(d["supported"])
        self.assertEqual(d["reason"], "edit_binary")

    def test_rebase_flag_is_reported(self):
        self.write("f.txt", BASE)
        self.commit("base")
        self.git("checkout", "-q", "-b", "topic")
        self.write("f.txt", SIDE)
        self.commit("topic")
        self.git("checkout", "-q", "main")
        self.write("f.txt", MAIN)
        self.commit("main")
        self.git("checkout", "-q", "topic")
        self.git("rebase", "main", check=False)
        d = app.api_merge_sources({"path": ["f.txt"]})
        self.assertTrue(d["rebasing"])
        self.assertEqual(d["ours"], MAIN)  # リベース中の ours は、土台 (リベース先) 側
        self.assertEqual(d["theirs"], SIDE)

    def test_shift_jis_file_is_decoded_and_saved_back_in_its_encoding(self):
        sjis = lambda s: s.encode("cp932")
        self.conflict("j.txt", base="あ\nい\nう\n".encode("cp932").decode("cp932"), main="あ\nい main\nう\n", side="あ\nい side\nう\n")
        # conflict() は UTF-8 で書くので、作業ツリーを Shift_JIS に書き直して保存の往復を確かめる
        self.write("j.txt", sjis("あ\n<<<<<<< HEAD\nい main\n=======\nい side\n>>>>>>> side\nう\n"), binary=True)
        d = app.api_merge_sources({"path": ["j.txt"]})
        self.assertEqual(d["encoding"], "Shift_JIS (CP932)")
        self.assertIn("い main", d["working"])
        app.api_file_save({"path": "j.txt", "content": "あ\nい main\nい side\nう\n", "sha": d["sha"]})
        with open(os.path.join(self.repo, "j.txt"), "rb") as f:
            self.assertEqual(f.read(), sjis("あ\nい main\nい side\nう\n"))

    def test_crlf_file_keeps_its_line_endings_on_save(self):
        self.conflict("c.txt", base=BASE.replace("\n", "\r\n"), main=MAIN.replace("\n", "\r\n"), side=SIDE.replace("\n", "\r\n"))
        d = app.api_merge_sources({"path": ["c.txt"]})
        self.assertEqual(d["eol"], "crlf")
        app.api_file_save({"path": "c.txt", "content": "one\nTWO both\nthree\nfour\nfive\n", "sha": d["sha"]})
        with open(os.path.join(self.repo, "c.txt"), "rb") as f:
            self.assertEqual(f.read(), b"one\r\nTWO both\r\nthree\r\nfour\r\nfive\r\n")

    def test_save_then_resolve_current_stages_the_file(self):
        self.conflict()
        d = app.api_merge_sources({"path": ["f.txt"]})
        result = "one\nTWO main\nTWO side\nthree\nfour\nfive\n"
        app.api_file_save({"path": "f.txt", "content": result, "sha": d["sha"]})
        app.api_resolve({"side": "current", "paths": ["f.txt"]})
        self.assertEqual(self.git("ls-files", "-u"), "")  # 競合は解決済み
        self.assertEqual(self.git("show", ":f.txt"), result)
        with self.assertRaises(UserError):
            app.api_merge_sources({"path": ["f.txt"]})  # もう競合していない

    def test_save_is_refused_when_the_file_changed_after_loading(self):
        self.conflict()
        d = app.api_merge_sources({"path": ["f.txt"]})
        self.write("f.txt", "changed elsewhere\n")
        with self.assertRaises(UserError):
            app.api_file_save({"path": "f.txt", "content": "mine\n", "sha": d["sha"]})

    def test_temp_files_are_cleaned_up(self):
        self.conflict()
        before = set(os.listdir(state_tmp_root())) if state_tmp_root() else set()
        app.api_merge_sources({"path": ["f.txt"]})
        left = [n for n in os.listdir(state_tmp_root()) if n.startswith("merge_")]
        self.assertEqual(left, [])
        del before

    def test_merge_page_embeds_editor_and_script(self):
        self.conflict()
        page = viewer.render_merge_page({"path": ["f.txt"]})
        self.assertIn("GGMerge", page)  # CodeMirror のバンドル
        self.assertIn("createMergeEditor", page)
        self.assertIn('const MERGE_PATH = "f.txt";', page)
        self.assertIn("const I18N =", page)
        self.assertIn('href="/diff?wip=conflict&amp;path=f.txt"', page)  # 差分ページへ戻るリンク
        for placeholder in ("/*__MERGE", "__PATH__", "__BACK__", "__PROJECT__", "__FAVICON__", "{{"):
            self.assertNotIn(placeholder, page, placeholder)
        self.assertEqual(page.count("</script>"), page.count("<script>"))  # 埋め込んだ JS の中に </script> が混ざっていない

    def test_merge_page_has_both_modes_with_resizable_panes(self):
        self.conflict()
        page = viewer.render_merge_page({"path": ["f.txt"]})
        for needle in ('id="modeMarkersBtn"', 'id="modeBaseBtn"',            # 方式の切り替え
                       'id="markerMode"', 'id="baseMode"',
                       'id="oursPair"', 'id="theirsPair"',                   # ベース編集方式の 2 行 (ours | base と theirs | base)
                       'id="markerSplitter"', 'id="baseSplitter"', 'role="separator"',  # 上下の高さを変える仕切り
                       "createBaseMergeEditor", "createMergeEditor"):
            self.assertIn(needle, page, needle)
        self.assertEqual(page.count('class="splitter"'), 2)
        self.assertNotIn("resize: vertical", page)  # 高さは仕切りで変える

    def test_merge_page_escapes_the_path(self):
        page = viewer.render_merge_page({"path": ['a"><script>x.txt']})
        self.assertIn("&quot;&gt;&lt;script&gt;x.txt", page)  # 見出しとタイトルは HTML エスケープ
        self.assertIn("a%22%3E%3Cscript%3Ex.txt", page)  # 戻るリンクは URL エンコード
        script_data = [line for line in page.split("\n") if "const MERGE_PATH" in line]
        self.assertEqual(len(script_data), 1)  # スクリプトの中には JSON 文字列として入る (<script> の入れ子にはならない)
        rest = page.replace(script_data[0], "")
        self.assertNotIn("<script>x", rest)
        self.assertNotIn('"><script', rest)

    def test_merge_page_rejects_bad_paths(self):
        for bad in ("", "../x", "/etc/passwd", "a//b", "a\0b"):
            with self.assertRaises(ValueError, msg=bad):
                viewer.render_merge_page({"path": [bad]})

    def test_diff_page_of_a_conflicted_file_links_to_the_merge_editor(self):
        self.conflict()
        page = viewer.render_diff_page({"path": ["f.txt"], "wip": ["conflict"]})
        self.assertIn('href="/merge?path=f.txt"', page)
        self.git("checkout", "-q", "--ours", "f.txt")
        self.git("add", "f.txt")
        self.write("g.txt", "g\n")
        self.git("add", "g.txt")
        plain = viewer.render_diff_page({"path": ["g.txt"], "wip": ["staged"]})
        self.assertNotIn("/merge?path=", plain)  # 競合していないファイルには出さない

    def test_diff_page_hides_the_merge_editor_when_read_only(self):
        self.conflict()
        state.WRITABLE = False
        page = viewer.render_diff_page({"path": ["f.txt"], "wip": ["conflict"]})
        self.assertNotIn("/merge?path=", page)

    def test_route_is_registered(self):
        self.assertIs(app.ROUTES[("GET", "/api/merge-sources")], app.api_merge_sources)


class EditorBundleTests(unittest.TestCase):
    """CodeMirror のバンドルは 2 つ。マージエディタのページには merge_editor.js を埋め込み、
    /files (ファイルの表示と編集) には text_editor.js (構文ハイライトと Vim モードで大きい) を、最初にファイルの内容を出すときに
    /static/vendor/ から読み込ませる。"""

    def test_files_page_uses_codemirror_for_viewing_and_editing(self):
        page = viewer.render_files_page()
        self.assertEqual(page.count("GGText.createTextEditor"), 2)  # 表示 (readOnly) と編集
        self.assertIn("readOnly: true", page)
        self.assertIn("/static/vendor/text_editor.js", page)
        self.assertNotIn("<textarea", page)
        self.assertNotIn("createMergeEditor", page)
        self.assertNotIn("createTextEditor(opts)", page)  # バンドルそのものは埋め込まない (読み込ませる)

    def test_files_page_has_no_highlighter_of_its_own(self):
        """構文ハイライトは CodeMirror に一本化した。highlight.js (CDN) と、自前の簡易ハイライトは持たない。"""
        page = viewer.render_files_page()
        for leftover in ("hljs", "highlight.js", "highlight.min.js", "KEYWORDS", "numberedCode", "languageFor", "tok-keyword", "splitHighlightedLines"):
            self.assertNotIn(leftover, page, leftover)
        self.assertNotIn("cdnjs.cloudflare.com", page)  # 外部の CDN に頼らない (オフラインでも同じように表示できる)

    def test_merge_page_does_not_carry_the_text_editor(self):
        self.assertNotIn("createTextEditor", viewer.MERGE_EDITOR_JS)
        self.assertIn("createMergeEditor", viewer.MERGE_EDITOR_JS)
        self.assertIn("createTextEditor", viewer.TEXT_EDITOR_JS)
        self.assertNotIn("createMergeEditor", viewer.TEXT_EDITOR_JS)

    def test_bundle_route_serves_the_javascript(self):
        previous = (state.OPTS, state.WRITABLE)
        state.OPTS = SimpleNamespace(host="127.0.0.1", revs=[], no_remotes=False, no_tags=False, rankdir="BT", no_count=False)
        server = app.QuietServer(("127.0.0.1", 0), app.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with urlopen(f"http://127.0.0.1:{server.server_address[1]}/static/vendor/text_editor.js") as response:
                body = response.read().decode("utf-8")
                self.assertIn("application/javascript", response.headers.get("Content-Type"))
        finally:
            server.shutdown()
            server.server_close()
            state.OPTS, state.WRITABLE = previous
        self.assertIn("GGText", body)
        self.assertIn("createTextEditor", body)


def state_tmp_root():
    from git_graph import tmpdir
    return tmpdir.instance_dir()


if __name__ == "__main__":
    unittest.main()
