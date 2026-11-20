import json
import os
import subprocess
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import git_graph_server as app
from git_graph import app_state as state
from git_graph import engine, repo_setup, server
from git_graph.i18n import UserError, check_i18n, render_setup_page


def git(*args, cwd=None):
    return subprocess.check_output(["git", *args], cwd=cwd, text=True, encoding="utf-8")


def make_repo(path):
    os.makedirs(path, exist_ok=True)
    git("init", "-q", path)
    git("-C", path, "config", "user.name", "Test User")
    git("-C", path, "config", "user.email", "test@example.com")
    with open(os.path.join(path, "a.txt"), "w", encoding="utf-8") as f:
        f.write("one\n")
    git("-C", path, "add", ".")
    git("-C", path, "commit", "-q", "-m", "initial")


class StateGuard(unittest.TestCase):
    """state を使うテストの前後で、書き換えた値を元に戻す。"""

    NAMES = ("REPO", "HAS_WORKTREE", "WIP_ENABLED", "PROJECT_NAME", "START_WIP", "NEED_REPO", "SETUP_PATH", "WRITABLE", "OPTS")

    def setUp(self):
        self.saved = {name: getattr(state, name) for name in self.NAMES}
        self.addCleanup(lambda: [setattr(state, k, v) for k, v in self.saved.items()])
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.realpath(self.tmp.name)
        state.OPTS = SimpleNamespace(host="127.0.0.1", no_wip=False, start="wip")
        state.WRITABLE = True


class OpenRepoTests(StateGuard):
    def test_open_repo_sets_the_state(self):
        repo = os.path.join(self.root, "proj")
        make_repo(repo)
        state.NEED_REPO = True
        repo_setup.open_repo(repo)
        self.assertEqual(os.path.realpath(state.REPO), repo)
        self.assertTrue(state.HAS_WORKTREE)
        self.assertTrue(state.WIP_ENABLED)
        self.assertEqual(state.PROJECT_NAME, "proj")
        self.assertTrue(state.START_WIP)
        self.assertFalse(state.NEED_REPO)

    def test_open_repo_uses_the_top_of_the_repository_for_a_subfolder(self):
        repo = os.path.join(self.root, "proj")
        make_repo(repo)
        os.makedirs(os.path.join(repo, "src", "deep"))
        repo_setup.open_repo(os.path.join(repo, "src", "deep"))
        self.assertEqual(os.path.realpath(state.REPO), repo)
        self.assertEqual(state.PROJECT_NAME, "proj")

    def test_open_repo_honors_no_wip(self):
        repo = os.path.join(self.root, "proj")
        make_repo(repo)
        state.OPTS.no_wip = True
        repo_setup.open_repo(repo)
        self.assertFalse(state.WIP_ENABLED)

    def test_bare_repository_has_no_worktree(self):
        bare = os.path.join(self.root, "bare.git")
        git("init", "-q", "--bare", bare)
        repo_setup.open_repo(bare)
        self.assertFalse(state.HAS_WORKTREE)
        self.assertFalse(state.WIP_ENABLED)
        self.assertEqual(os.path.realpath(state.REPO), os.path.realpath(bare))

    def test_open_repo_keeps_the_state_when_it_fails(self):
        state.REPO, state.NEED_REPO, state.PROJECT_NAME = "before", True, "before"
        plain = os.path.join(self.root, "plain")
        os.makedirs(plain)
        with self.assertRaises(UserError) as cm:
            repo_setup.open_repo(plain)
        self.assertEqual(cm.exception.key, "setup_not_a_repo")
        with self.assertRaises(UserError) as cm:
            repo_setup.open_repo(os.path.join(self.root, "missing"))
        self.assertEqual(cm.exception.key, "setup_path_not_found")
        self.assertEqual((state.REPO, state.NEED_REPO, state.PROJECT_NAME), ("before", True, "before"))

    def test_path_text_is_cleaned(self):
        self.assertEqual(repo_setup.clean_path_text('  "C:\\work\\proj"  '), "C:\\work\\proj")
        self.assertEqual(repo_setup.clean_path_text("'/tmp/x'"), "/tmp/x")
        self.assertEqual(repo_setup.clean_path_text("/tmp/x"), "/tmp/x")


class SetupApiTests(StateGuard):
    def setUp(self):
        super().setUp()
        state.NEED_REPO = True
        state.REPO = self.root
        self.source = os.path.join(self.root, "source")
        make_repo(self.source)

    def test_opens_an_existing_repository_without_cloning(self):
        with mock.patch.object(repo_setup, "clone_repo") as clone:
            res = repo_setup.api_setup({"path": self.source})
        clone.assert_not_called()
        self.assertEqual(res, {"ok": True, "cloned": False})
        self.assertFalse(state.NEED_REPO)
        self.assertEqual(state.PROJECT_NAME, "source")

    def test_clones_into_the_folder_and_opens_it(self):
        dest = os.path.join(self.root, "work", "copy")  # 途中のフォルダが無くてもよい
        res = repo_setup.api_setup({"path": dest, "url": self.source})
        self.assertEqual(res, {"ok": True, "cloned": True})
        self.assertTrue(os.path.isfile(os.path.join(dest, "a.txt")))
        self.assertFalse(state.NEED_REPO)
        self.assertEqual(os.path.realpath(state.REPO), os.path.realpath(dest))
        self.assertEqual(state.PROJECT_NAME, "copy")

    def test_clones_into_an_empty_existing_folder(self):
        dest = os.path.join(self.root, "empty")
        os.makedirs(dest)
        repo_setup.api_setup({"path": dest, "url": self.source})
        self.assertTrue(os.path.isfile(os.path.join(dest, "a.txt")))

    def test_refuses_a_destination_that_is_not_empty(self):
        dest = os.path.join(self.root, "busy")
        os.makedirs(dest)
        with open(os.path.join(dest, "keep.txt"), "w") as f:
            f.write("x")
        with self.assertRaises(UserError) as cm:
            repo_setup.api_setup({"path": dest, "url": self.source})
        self.assertEqual(cm.exception.key, "setup_dest_not_empty")
        self.assertTrue(state.NEED_REPO)
        self.assertEqual(os.listdir(dest), ["keep.txt"])

    def test_refuses_addresses_that_look_like_options(self):
        for url in ("--upload-pack=touch /tmp/pwned", "-c", "a\nb", "a\0b"):
            with self.assertRaises(UserError) as cm:
                repo_setup.api_setup({"path": os.path.join(self.root, "x"), "url": url})
            self.assertEqual(cm.exception.key, "setup_url_invalid")
        self.assertFalse(os.path.exists(os.path.join(self.root, "x")))

    def test_clone_failure_returns_the_git_message_and_keeps_waiting(self):
        dest = os.path.join(self.root, "nothing")
        with self.assertRaises(ValueError) as cm:
            repo_setup.api_setup({"path": dest, "url": os.path.join(self.root, "no-such-repo")})
        self.assertNotIsInstance(cm.exception, UserError)
        self.assertIn("no-such-repo", str(cm.exception))
        self.assertTrue(state.NEED_REPO)

    def test_asks_for_authentication_when_the_server_requires_it(self):
        result = {"auth_required": True, "auth_origin": "https://example.com"}
        with mock.patch.object(repo_setup, "run_git_net_origin",
                               return_value=(subprocess.CompletedProcess([], 128, "", "x"), "https://example.com")) as run:
            res = repo_setup.api_setup({"path": os.path.join(self.root, "d"), "url": "https://example.com/a.git"})
        self.assertEqual(res, result)
        self.assertTrue(state.NEED_REPO)
        self.assertEqual(run.call_args.kwargs["origin"], "https://example.com")

    def test_passes_the_entered_credentials_with_their_origin(self):
        ok = subprocess.CompletedProcess([], 0, "", "")
        dest = os.path.join(self.root, "d")
        with mock.patch.object(repo_setup, "run_git_net_origin", return_value=(ok, None)) as run, \
                mock.patch.object(repo_setup, "open_repo") as opened:
            repo_setup.api_setup({"path": dest, "url": "https://example.com/a.git",
                                  "auth": {"username": "u", "password": "p"}, "auth_origin": "https://example.com"})
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["auth"], {"username": "u", "password": "p"})
        self.assertEqual(kwargs["origin"], "https://example.com")
        self.assertIn("protocol.ext.allow=never", run.call_args.args)  # 危険な転送は使わせない
        opened.assert_called_once_with(dest)

    def test_clone_needs_write_permission_but_opening_does_not(self):
        state.WRITABLE = False
        with self.assertRaises(UserError) as cm:
            repo_setup.api_setup({"path": os.path.join(self.root, "c"), "url": self.source})
        self.assertEqual(cm.exception.key, "write_disabled")
        repo_setup.api_setup({"path": self.source})  # 開くだけならできる
        self.assertFalse(state.NEED_REPO)

    def test_path_is_required_and_state_must_be_waiting(self):
        with self.assertRaises(UserError) as cm:
            repo_setup.api_setup({"path": "  ", "url": self.source})
        self.assertEqual(cm.exception.key, "setup_path_required")
        state.NEED_REPO = False
        with self.assertRaises(UserError) as cm:
            repo_setup.api_setup({"path": self.source})
        self.assertEqual(cm.exception.key, "setup_already_open")

    def test_route_is_registered_and_not_recorded_by_the_engine(self):
        self.assertIn("/api/setup", app.POST_ROUTES)
        self.assertIn("/api/setup", engine.IGNORED)


class SetupPageTests(StateGuard):
    def test_page_has_the_initial_path_escaped(self):
        state.SETUP_PATH = '/tmp/"><script>alert(1)</script>'
        page = render_setup_page()
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&quot;&gt;&lt;script&gt;", page)
        self.assertNotIn("{{", page)
        self.assertNotIn("__PATH__", page)

    def test_page_language_follows_the_ui_language(self):
        saved = state.UI_LANG
        self.addCleanup(setattr, state, "UI_LANG", saved)
        state.UI_LANG = "en"
        page = render_setup_page()
        self.assertIn('<html lang="en">', page)
        self.assertIn("Open a repository", page)

    def test_translations_have_no_missing_or_unused_keys(self):
        with mock.patch("builtins.print"):
            self.assertEqual(check_i18n(), 0)


class SetupHttpTests(StateGuard):
    def setUp(self):
        super().setUp()
        self.source = os.path.join(self.root, "source")
        make_repo(self.source)
        state.NEED_REPO = True
        state.SETUP_PATH = os.path.join(self.root, "dest")
        state.REPO = self.root
        httpd = app.QuietServer(("127.0.0.1", 0), app.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        self.base = f"http://127.0.0.1:{httpd.server_address[1]}"
        patch = mock.patch.object(server, "warm_graph")
        self.warm = patch.start()
        self.addCleanup(patch.stop)

    def post(self, path, body):
        req = Request(self.base + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
        return urlopen(req)

    def test_root_shows_the_setup_page_and_other_pages_return_to_it(self):
        with urlopen(self.base + "/") as res:
            page = res.read().decode("utf-8")
        self.assertIn('id="setupForm"', page)
        self.assertIn(html_escape(state.SETUP_PATH), page)
        with urlopen(self.base + "/files?hash=WIP") as res:  # 302 を辿って設定画面になる
            self.assertEqual(res.geturl(), self.base + "/")
            self.assertIn('id="setupForm"', res.read().decode("utf-8"))

    def test_setup_page_assets_are_served_before_a_repository_is_chosen(self):
        # 設定画面は CSS / JS / 翻訳を /static/ から読む。リポジトリが決まる前でも、/ へ戻されずに配られる
        with urlopen(self.base + "/") as res:
            page = res.read().decode("utf-8")
        for url in ("/static/setup/setup.css", "/static/i18n.js", "/static/setup/setup.js"):
            self.assertIn(url, page)
            with urlopen(self.base + url) as res:
                self.assertEqual(res.geturl(), self.base + url)  # 302 で / に飛ばされていない
                self.assertGreater(len(res.read()), 0)
        for url in ("/static/graph/index.html", "/static/nope.js"):  # 配らないもの (テンプレート・無いファイル) は、/ へ戻さず静的配信の判断で 404
            with self.assertRaises(HTTPError) as cm:
                urlopen(self.base + url)
            self.assertEqual(cm.exception.code, 404)
            cm.exception.close()

    def test_other_write_apis_are_refused_until_a_repository_is_chosen(self):
        with self.assertRaises(HTTPError) as cm:
            self.post("/api/fetch", {})
        self.assertEqual(cm.exception.code, 400)
        cm.exception.close()

    def test_clone_over_http_then_the_normal_page_is_served(self):
        dest = os.path.join(self.root, "dest")
        with self.post("/api/setup", {"path": dest, "url": self.source}) as res:
            self.assertEqual(json.loads(res.read()), {"ok": True, "cloned": True})
        self.assertFalse(state.NEED_REPO)
        for _ in range(100):  # サーバーは応答を返したあとで warm_graph を呼ぶ (別スレッド)。呼ばれるのを待つ
            if self.warm.called:
                break
            threading.Event().wait(0.01)
        self.warm.assert_called()
        with urlopen(self.base + "/") as res:  # 通常の画面 (WIP のファイル表示へ移る)
            self.assertNotIn('id="setupForm"', res.read().decode("utf-8"))
        with self.assertRaises(HTTPError) as cm:  # 決まったあとは、設定 API は使えない
            self.post("/api/setup", {"path": dest})
        self.assertEqual(cm.exception.code, 400)
        cm.exception.close()

    def test_error_is_returned_as_text_and_the_page_stays(self):
        with self.assertRaises(HTTPError) as cm:
            self.post("/api/setup", {"path": os.path.join(self.root, "missing")})
        self.assertEqual(cm.exception.code, 400)
        self.assertIn("missing", cm.exception.read().decode("utf-8"))
        cm.exception.close()
        self.assertTrue(state.NEED_REPO)
        self.warm.assert_not_called()


def html_escape(text):
    import html
    return html.escape(text, quote=True)


if __name__ == "__main__":
    unittest.main()
