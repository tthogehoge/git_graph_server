import json
import os
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import git_graph_server as app  # noqa: F401  (各モジュールの @route 登録を実行させる)
from git_graph import app_state as state
from git_graph import engine
from git_graph import launcher
from git_graph import server as server_module
from git_graph.i18n import UserError
from git_graph.query import history as qh

SUB_DIR = "sub dir"  # 空白を含むパスでも開けること


def make_opts(**override):
    opts = dict(host="127.0.0.1", no_remotes=False, no_tags=False, include_tips=False, full_layout=False, no_count=False,
                no_wip=False, read_only=False, allow_write=False, no_undo=False, desktop=False,
                merge_max_nodes=2000, cluster_min_nodes=800, rankdir="BT", font="Helvetica", start="wip", config=None,
                port=8000)
    opts.update(override)
    return SimpleNamespace(**opts)


class ForwardedArgsTests(unittest.TestCase):
    def setUp(self):
        previous = (state.OPTS, state.UI_LANG)
        self.addCleanup(lambda: (setattr(state, "OPTS", previous[0]), setattr(state, "UI_LANG", previous[1])))
        state.UI_LANG = "en"

    def test_port_and_repository_are_never_forwarded(self):
        state.OPTS = make_opts(port=8765, repo="/some/repo", revs=["main"])
        args = launcher.forwarded_args()
        self.assertNotIn("--port", args)
        self.assertNotIn("/some/repo", args)
        self.assertNotIn("main", args)

    def test_options_are_forwarded(self):
        state.OPTS = make_opts(host="0.0.0.0", allow_write=True, read_only=False, no_tags=True, font="Yu Gothic",
                               rankdir="TB", config="my.json", desktop=True)
        args = launcher.forwarded_args()
        self.assertEqual(args[args.index("--host") + 1], "0.0.0.0")
        self.assertEqual(args[args.index("--font") + 1], "Yu Gothic")
        self.assertEqual(args[args.index("--rankdir") + 1], "TB")
        self.assertEqual(args[args.index("--config") + 1], "my.json")
        self.assertEqual(args[args.index("--lang") + 1], "en")
        for flag in ("--allow-write", "--no-tags", "--desktop"):
            self.assertIn(flag, args)
        self.assertNotIn("--read-only", args)
        self.assertNotIn("--no-wip", args)

    def test_default_host_is_not_forwarded(self):
        state.OPTS = make_opts()
        self.assertNotIn("--host", launcher.forwarded_args())

    def test_every_forwarded_option_is_accepted_by_the_server(self):
        # サーバーの引数解析が受け付けない名前を転送していないこと (オプション名の打ち間違いの検出)
        state.OPTS = make_opts(**{name: True for name in launcher.FLAG_OPTIONS}, config="x.json")
        r = subprocess.run([sys.executable, app.__file__, "--check-i18n", *launcher.forwarded_args()],
                           capture_output=True, text=True)
        self.assertNotIn("unrecognized arguments", r.stderr)
        self.assertNotIn("invalid", r.stderr)


class SubmoduleOpenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        root = cls.temp_dir.name
        cls.sub_src = os.path.join(root, "sub_src")
        cls.repo = os.path.join(root, "parent")
        for path in (cls.sub_src, cls.repo):
            os.makedirs(path)
            cls.git(path, "init", "-q")
            cls.git(path, "config", "user.name", "Test User")
            cls.git(path, "config", "user.email", "test@example.com")
            cls.git(path, "branch", "-M", "main")
        with open(os.path.join(cls.sub_src, "lib.txt"), "w", encoding="utf-8") as f:
            f.write("lib\n")
        cls.git(cls.sub_src, "add", ".")
        cls.git(cls.sub_src, "commit", "-q", "-m", "commit in submodule")
        with open(os.path.join(cls.repo, "README.md"), "w", encoding="utf-8") as f:
            f.write("parent\n")
        cls.git(cls.repo, "add", ".")
        cls.git(cls.repo, "commit", "-q", "-m", "initial commit")
        cls.git(cls.repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q", cls.sub_src, SUB_DIR)
        cls.git(cls.repo, "commit", "-q", "-m", "add submodule")

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    @staticmethod
    def git(repo, *args):
        return subprocess.check_output(["git", "-C", repo, *args], text=True, encoding="utf-8")

    def setUp(self):
        previous = (state.REPO, state.OPTS, state.UI_LANG, state.CHILD_CMD, state.WRITABLE, state.HAS_WORKTREE)
        self.addCleanup(lambda: [setattr(state, k, v) for k, v in zip(
            ("REPO", "OPTS", "UI_LANG", "CHILD_CMD", "WRITABLE", "HAS_WORKTREE"), previous)])
        state.REPO, state.OPTS, state.UI_LANG, state.CHILD_CMD = self.repo, make_opts(), "en", None
        state.WRITABLE, state.HAS_WORKTREE = True, True
        launcher._CHILDREN.clear()
        self.addCleanup(self.stop_children)

    def stop_children(self):
        for proc, _ in launcher._CHILDREN.values():
            proc.terminate()
            proc.wait(timeout=10)
        launcher._CHILDREN.clear()

    def test_submodule_list_has_path_url_and_initialized_flag(self):
        res = qh.submodules()
        self.assertEqual(len(res["commits"]), len(res["submodules"]))
        info = res["submodules"][0]
        self.assertEqual((info["path"], info["url"], info["initialized"]), (SUB_DIR, self.sub_src, True))
        self.assertTrue(res["commits"][0][4].startswith(SUB_DIR))

    def test_rejects_paths_that_are_not_registered_submodules(self):
        for path in ("README.md", "missing", "sub"):
            with self.assertRaises(UserError, msg=path) as cm:
                launcher.api_submodule_open({"path": path})
            self.assertEqual(cm.exception.key, "submodule_not_found")
        for path in ("../parent", "/etc", "", "a//b"):
            with self.assertRaises(ValueError, msg=path):
                launcher.api_submodule_open({"path": path})
        with self.assertRaises(ValueError):
            launcher.api_submodule_open({"path": 1})

    def test_rejects_uninitialized_submodule(self):
        self.git(self.repo, "submodule", "deinit", "-q", "-f", "--", SUB_DIR)
        self.addCleanup(self.git, self.repo, "-c", "protocol.file.allow=always", "submodule", "update", "-q", "--init")
        self.assertFalse(qh.submodules()["submodules"][0]["initialized"])
        with self.assertRaises(UserError) as cm:
            launcher.api_submodule_open({"path": SUB_DIR})
        self.assertEqual(cm.exception.key, "submodule_not_initialized")

    def test_opens_submodule_in_another_process_on_another_port(self):
        state.OPTS = make_opts(port=8000)
        with mock.patch.object(launcher, "free_port", wraps=launcher.free_port) as free_port:
            res = launcher.api_submodule_open({"path": SUB_DIR})
        free_port.assert_called_once_with("127.0.0.1")
        self.assertTrue(res["ok"])
        port = res["port"]
        self.assertNotEqual(port, state.OPTS.port)  # 同じポートは使えない
        proc = launcher._CHILDREN[SUB_DIR][0]
        self.assertIsNone(proc.poll())
        head = self.git(self.sub_src, "rev-parse", "HEAD").strip()
        with urlopen(f"http://127.0.0.1:{port}/api/log?rev={head}") as response:
            subjects = [c[4] for c in json.load(response)["commits"]]
        self.assertEqual(subjects, ["commit in submodule"])  # 親ではなく submodule のログが出る
        self.assertEqual(launcher.api_submodule_open({"path": SUB_DIR})["port"], port)  # 開き直しても、同じ子を使う
        self.assertEqual(len(launcher._CHILDREN), 1)
        req = Request(f"http://127.0.0.1:{port}/api/shutdown", data=b"{}", method="POST",
                      headers={"Content-Type": "application/json"})
        with urlopen(req):
            pass
        proc.wait(timeout=10)

    def test_a_closed_child_is_started_again(self):
        port = launcher.api_submodule_open({"path": SUB_DIR})["port"]
        proc = launcher._CHILDREN[SUB_DIR][0]
        proc.terminate()
        proc.wait(timeout=10)
        again = launcher.api_submodule_open({"path": SUB_DIR})
        self.assertTrue(again["ok"])
        self.assertIsNot(launcher._CHILDREN[SUB_DIR][0], proc)
        self.assertIsNone(launcher._CHILDREN[SUB_DIR][0].poll())
        self.assertIsNotNone(port)

    def test_window_mode_runs_the_hook_command_without_a_port(self):
        seen = []

        def hook(path, start_path=""):
            seen.append(path)
            return [sys.executable, "-c", "import time; time.sleep(30)"]
        state.CHILD_CMD = hook
        with mock.patch.object(launcher, "EARLY_EXIT_WAIT", 0.3):
            res = launcher.api_submodule_open({"path": SUB_DIR})
        self.assertEqual(seen, [os.path.join(self.repo, SUB_DIR)])
        self.assertTrue(res["ok"])
        self.assertIsNone(res["port"])

    def test_window_mode_reports_a_child_that_fails_to_start(self):
        state.CHILD_CMD = lambda path, start_path="": [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"]
        with mock.patch.object(launcher, "EARLY_EXIT_WAIT", 5):
            with self.assertRaises(UserError) as cm:
                launcher.api_submodule_open({"path": SUB_DIR})
        self.assertEqual(cm.exception.key, "submodule_start_failed")
        self.assertIn("boom", str(cm.exception))
        self.assertNotIn(SUB_DIR, launcher._CHILDREN)

    def test_browser_mode_reports_a_child_that_fails_to_start(self):
        with mock.patch.object(launcher, "SCRIPT", os.path.join(self.repo, "missing_script.py")):
            with self.assertRaises(UserError) as cm:
                launcher.api_submodule_open({"path": SUB_DIR})
        self.assertEqual(cm.exception.key, "submodule_start_failed")
        self.assertNotIn(SUB_DIR, launcher._CHILDREN)

    def test_free_port_returns_a_bindable_port(self):
        self.assertGreater(launcher.free_port("127.0.0.1"), 0)


class SubmoduleOpenPermissionTests(unittest.TestCase):
    """--read-only でも、ローカルからなら開ける (リポジトリを変更しないため)。他の端末からは使わせない。"""

    def post(self, handler_host, writable):
        previous = (state.WRITABLE, state.OPTS)
        self.addCleanup(lambda: (setattr(state, "WRITABLE", previous[0]), setattr(state, "OPTS", previous[1])))
        state.WRITABLE, state.OPTS = writable, SimpleNamespace(host=handler_host)
        server = app.QuietServer(("127.0.0.1", 0), app.Handler)
        import threading
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        called = []
        with mock.patch.dict(app.POST_ROUTES, {"/api/submodule-open": lambda body: called.append(body) or {"ok": True}}):
            req = Request(f"http://127.0.0.1:{server.server_address[1]}/api/submodule-open", data=b'{"path": "x"}',
                          headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urlopen(req) as response:
                    return response.status, called
            except HTTPError as e:
                e.close()
                return e.code, called

    def test_local_server_allows_it_even_when_read_only(self):
        with mock.patch.object(engine, "run", side_effect=lambda path, handler, body: handler(body)), \
                mock.patch.object(server_module, "warm_graph") as warm:
            status, called = self.post("127.0.0.1", writable=False)
        self.assertEqual((status, called), (200, [{"path": "x"}]))
        warm.assert_not_called()  # グラフを作り直す必要はない

    def test_network_server_without_write_permission_refuses_it(self):
        status, called = self.post("0.0.0.0", writable=False)
        self.assertEqual((status, called), (403, []))


if __name__ == "__main__":
    unittest.main()
