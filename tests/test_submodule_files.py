import http.client
import json
import os
import subprocess
import sys
import time
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.request import urlopen

import git_graph_server as app
from git_graph import app_state as state
from git_graph import launcher
from git_graph import server as server_module
from git_graph.i18n import UserError
from git_graph.query import files as qf
from tests.test_submodules import SUB_DIR, SubmoduleTestBase


class SubmoduleFileViewTests(SubmoduleTestBase):
    """ファイル表示 (ツリー) で、submodule をファイルと取り違えない。"""

    def setUp(self):
        super().setUp()
        names = ("WIP_ENABLED", "HAS_WORKTREE", "WRITABLE", "CHILD_CMD", "OPTS")
        saved = [getattr(state, k) for k in names]
        self.addCleanup(lambda: [setattr(state, k, v) for k, v in zip(names, saved)])
        state.WIP_ENABLED = state.HAS_WORKTREE = state.WRITABLE = True
        state.CHILD_CMD = None
        state.OPTS = mock.Mock(host="127.0.0.1", **{k: False for k in launcher.FLAG_OPTIONS}, merge_max_nodes=2000,
                               cluster_min_nodes=800, rankdir="BT", font="Helvetica", start="wip", config=None)
        launcher._CHILDREN.clear()
        self.addCleanup(self.stop_children)
        self.head = self.git(self.repo, "rev-parse", "HEAD")
        os.makedirs(os.path.join(self.repo, "docs"))
        self.commit(self.repo, "docs/a.txt", "a\n", "add docs")
        self.head = self.git(self.repo, "rev-parse", "HEAD")

    def stop_children(self):
        for proc, _ in launcher._CHILDREN.values():
            proc.terminate()
            proc.wait(timeout=10)
        launcher._CHILDREN.clear()

    def test_commit_tree_marks_a_submodule_and_gives_its_recorded_commit(self):
        entries = {e["name"]: e for e in qf.tree(self.head)["entries"]}
        self.assertEqual(entries[SUB_DIR]["type"], "commit")
        self.assertEqual(entries[SUB_DIR]["sha"], self.sub_first)
        self.assertEqual(entries["docs"]["type"], "tree")
        self.assertNotIn("sha", entries["README.md"])

    def test_working_tree_marks_a_submodule(self):
        entries = {e["name"]: e for e in qf.tree(qf.WIP_HASH)["entries"]}
        self.assertEqual(entries[SUB_DIR]["type"], "commit")
        self.assertEqual(entries["docs"]["type"], "tree")

    def test_reading_a_submodule_as_a_file_says_it_is_a_submodule(self):
        calls = [lambda: qf.file_content(self.head, SUB_DIR), lambda: qf.file_content(qf.WIP_HASH, SUB_DIR),
                 lambda: qf.file_bytes(self.head, SUB_DIR), lambda: qf.file_bytes(qf.WIP_HASH, SUB_DIR),
                 lambda: qf.require_blob(self.head, SUB_DIR)]
        for call in calls:
            with self.assertRaises(UserError) as cm:
                call()
            self.assertEqual(cm.exception.key, "is_submodule")

    def test_a_plain_directory_is_still_not_a_file(self):
        for call in (lambda: qf.file_content(self.head, "docs"), lambda: qf.file_content(qf.WIP_HASH, "docs"),
                     lambda: qf.file_bytes(self.head, "docs"), lambda: qf.require_blob(self.head, "docs")):
            with self.assertRaises(UserError) as cm:
                call()
            self.assertEqual(cm.exception.key, "not_a_file")

    def test_without_libgit_the_error_is_still_a_submodule_error(self):
        with mock.patch.object(qf.libgit, "available", return_value=False):
            for call in (lambda: qf.file_content(self.head, SUB_DIR), lambda: qf.file_bytes(self.head, SUB_DIR),
                         lambda: qf.require_blob(self.head, SUB_DIR)):
                with self.assertRaises(UserError) as cm:
                    call()
                self.assertEqual(cm.exception.key, "is_submodule")

    def get(self, port, path):
        with urlopen(f"http://127.0.0.1:{port}{path}") as response:
            return response.status, response.read().decode("utf-8")

    def test_opening_a_submodule_from_the_file_view_shows_its_files_at_the_recorded_commit(self):
        res = launcher.api_submodule_open({"path": SUB_DIR, "files_hash": self.sub_first})
        self.assertEqual(res["start_path"], f"/files?hash={self.sub_first}")
        status, page = self.get(res["port"], res["start_path"])  # 子のサーバーが、submodule のファイル表示を返す
        self.assertEqual(status, 200)
        self.assertIn("<title>", page)
        with self.assertRaises(HTTPError) as cm:  # 子は submodule のリポジトリを見ている: 親のコミットは、そこには無い
            self.get(res["port"], f"/files?hash={self.head}")
        self.assertEqual(cm.exception.code, 400)
        cm.exception.close()
        _, body = self.get(res["port"], f"/api/tree?hash={self.sub_first}&path=")
        self.assertEqual([e["name"] for e in json.loads(body)["entries"]], ["lib.txt"])  # 親のファイルではなく submodule のファイル

    def test_opening_the_working_tree_of_a_submodule(self):
        res = launcher.api_submodule_open({"path": SUB_DIR, "files_hash": "WIP"})
        self.assertEqual(res["start_path"], "/files?hash=WIP")
        self.assertEqual(self.get(res["port"], res["start_path"])[0], 200)
        _, body = self.get(res["port"], "/api/tree?hash=WIP&path=")
        self.assertIn("lib.txt", [e["name"] for e in json.loads(body)["entries"]])

    def test_a_commit_the_submodule_does_not_have_is_reported_before_starting(self):
        with self.assertRaises(UserError) as cm:
            launcher.api_submodule_open({"path": SUB_DIR, "files_hash": "0" * 40})
        self.assertEqual(cm.exception.key, "submodule_commit_missing")
        self.assertEqual(launcher._CHILDREN, {})

    def test_bad_hashes_are_rejected(self):
        for bad in ("zz", "../x", "", "abc", "A" * 40, 1, ["a"], "WIP; rm", "--help"):
            with self.assertRaises(ValueError, msg=bad):
                launcher.api_submodule_open({"path": SUB_DIR, "files_hash": bad})

    def test_without_files_hash_the_start_path_is_empty(self):
        self.assertEqual(launcher.api_submodule_open({"path": SUB_DIR})["start_path"], "")

    def test_without_files_hash_starts_the_child_with_graph(self):
        """一覧から開くときは --start graph (親が --start wip でも WIP 画面にしない)。"""
        seen = []
        real_popen = subprocess.Popen

        def capture(*args, **kwargs):
            argv = list(args[0]) if args else list(kwargs.get("args", []))
            if str(launcher.SCRIPT) in argv or (argv and "git_graph_server" in str(argv[0])):
                seen.append(argv)
            return real_popen(*args, **kwargs)

        with mock.patch.object(subprocess, "Popen", side_effect=capture):
            res = launcher.api_submodule_open({"path": SUB_DIR})
        self.assertTrue(res["ok"])
        self.assertEqual(res["start_path"], "")
        self.assertTrue(seen, "child Popen was not called")
        argv = seen[0]
        # 最後の --start が graph (forwarded の wip を上書き)
        starts = [argv[i + 1] for i, a in enumerate(argv) if a == "--start"]
        self.assertTrue(starts, argv)
        self.assertEqual(starts[-1], "graph")
        self.assertNotIn("--start-path", argv)

    def test_with_files_hash_passes_start_path_to_the_child(self):
        """ファイル表示から開くときは子に --start-path を渡す。"""
        seen = []
        real_popen = subprocess.Popen

        def capture(*args, **kwargs):
            argv = list(args[0]) if args else list(kwargs.get("args", []))
            if str(launcher.SCRIPT) in argv or (argv and "git_graph_server" in str(argv[0])):
                seen.append(argv)
            return real_popen(*args, **kwargs)

        with mock.patch.object(subprocess, "Popen", side_effect=capture):
            res = launcher.api_submodule_open({"path": SUB_DIR, "files_hash": self.sub_first})
        self.assertEqual(res["start_path"], f"/files?hash={self.sub_first}")
        self.assertTrue(seen, "child Popen was not called")
        argv = seen[0]
        self.assertIn("--start-path", argv)
        self.assertEqual(argv[argv.index("--start-path") + 1], f"/files?hash={self.sub_first}")

    def test_window_mode_passes_the_start_path_to_the_command(self):
        seen = []

        def hook(path, start_path=""):
            seen.append((path, start_path))
            return [sys.executable, "-c", "import time; time.sleep(30)"]
        state.CHILD_CMD = hook
        with mock.patch.object(launcher, "EARLY_EXIT_WAIT", 0.3):
            res = launcher.api_submodule_open({"path": SUB_DIR, "files_hash": self.sub_first})
        self.assertEqual(seen, [(os.path.join(self.repo, SUB_DIR), f"/files?hash={self.sub_first}")])
        self.assertIsNone(res["port"])


class FilesPageStyleTests(unittest.TestCase):
    def test_the_styles_for_the_submodule_marks_reach_the_page(self):
        # ファイル表示の CSS は files.html の <style> にある (static/files.css は読み込まれない)。
        # そこへ書き漏らすと、印の装飾が効かず、名前と印がくっついて見える
        page = server_module.render_files_page()
        for rule in (".sm-badge {", ".sm-msg {", ".sm-msg.err {"):
            self.assertIn(rule, page)

    def test_the_badge_is_separated_from_the_name_even_without_styles(self):
        page = server_module.render_files_page()
        self.assertIn("""' <span class="sm-badge">'""", page)


class StartPathTests(unittest.TestCase):
    def setUp(self):
        saved = (state.START_PATH, state.START_WIP)
        self.addCleanup(lambda: (setattr(state, "START_PATH", saved[0]), setattr(state, "START_WIP", saved[1])))

    def test_start_path_is_used_once_and_replaces_the_wip_page(self):
        state.START_PATH, state.START_WIP = "/files?hash=WIP", True
        self.assertEqual(server_module.start_page_url(), "/files?hash=WIP")
        self.assertEqual(state.START_PATH, "")
        self.assertFalse(state.START_WIP)
        self.assertIsNone(server_module.start_page_url())  # 2 回目以降はグラフ


class StartPathProcessTests(unittest.TestCase):
    """実際にサーバーを起動して、最初の / だけが指定のパスへ移ることと、不正な指定が拒否されることを見る。"""

    def setUp(self):
        import tempfile
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repo = self.temp_dir.name
        for args in (("init", "-q", "-b", "main"), ("config", "user.name", "T"), ("config", "user.email", "t@e")):
            subprocess.check_call(["git", "-C", self.repo, *args])
        with open(os.path.join(self.repo, "a.txt"), "w", encoding="utf-8") as f:
            f.write("a\n")
        subprocess.check_call(["git", "-C", self.repo, "add", "."])
        subprocess.check_call(["git", "-C", self.repo, "commit", "-q", "-m", "init"])

    def start(self, *extra):
        port = launcher.free_port("127.0.0.1")
        proc = subprocess.Popen([sys.executable, app.__file__, self.repo, "--port", str(port), *extra],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (proc.terminate(), proc.wait(timeout=10)))
        for _ in range(100):
            try:
                http.client.HTTPConnection("127.0.0.1", port, timeout=1).request("GET", "/static/app.css")
                break
            except OSError:
                time.sleep(0.1)
        return port

    @staticmethod
    def get(port, path):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
        conn.request("GET", path)
        r = conn.getresponse()
        r.read()
        return r.status, r.getheader("Location")

    def test_first_root_access_is_redirected_once(self):
        port = self.start("--start-path", "/files?hash=WIP")
        self.assertEqual(self.get(port, "/"), (302, "/files?hash=WIP"))
        self.assertEqual(self.get(port, "/"), (200, None))  # 2 回目以降はグラフ。WIP の画面へも移らない

    def test_invalid_start_paths_are_rejected(self):
        for bad in ("http://evil.example/", "//evil.example/x", "files", "/a\nb", "javascript:alert(1)"):
            r = subprocess.run([sys.executable, app.__file__, self.repo, "--start-path", bad], capture_output=True, text=True,
                               timeout=30)
            self.assertEqual(r.returncode, 2, bad)
            self.assertIn("--start-path", r.stderr)


if __name__ == "__main__":
    unittest.main()
