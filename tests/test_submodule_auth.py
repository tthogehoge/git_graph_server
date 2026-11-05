import base64
import functools
import http.server
import os
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

import git_graph_server as app  # noqa: F401  (各モジュールの @route 登録を実行させる)
from git_graph import app_state as state
from git_graph import engine, gitrun
from git_graph.i18n import UserError
from git_graph.query import history as qh
from git_graph.routing import ROUTES
from git_graph.submodules import api_submodule_add, api_submodule_fetch, api_submodule_update

USER, PASSWORD = "user1", "s3cret"  # ユーザー名に "user" を含む (パスワードの質問と取り違えないことの確認も兼ねる)
GOOD = {"username": USER, "password": PASSWORD}


class AuthHandler(http.server.SimpleHTTPRequestHandler):
    """Basic 認証を要求して、bare リポジトリのファイルをそのまま返す (git の dumb HTTP)。"""
    authorized = []  # 認証に成功したリクエストのパス (テストで、認証情報が渡ったかを見る)

    def log_message(self, *args):
        pass

    def _ok(self):
        header = self.headers.get("Authorization", "")
        expected = "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
        if header == expected:
            AuthHandler.authorized.append(self.path)
            return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="test"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def do_GET(self):
        if self._ok():
            super().do_GET()

    def do_HEAD(self):
        if self._ok():
            super().do_HEAD()


class HttpAuthTestBase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = self.temp_dir.name
        # 認証が必要な HTTP サーバーに置く submodule のもと
        work = os.path.join(root, "work")
        self.served = os.path.join(root, "served")
        os.makedirs(work)
        self.git(work, "init", "-q", "-b", "main")
        self.git(work, "config", "user.name", "Test User")
        self.git(work, "config", "user.email", "test@example.com")
        with open(os.path.join(work, "lib.txt"), "w", encoding="utf-8") as f:
            f.write("lib\n")
        self.git(work, "add", ".")
        self.git(work, "commit", "-q", "-m", "commit in submodule")
        self.sub_commit = self.git(work, "rev-parse", "HEAD")
        os.makedirs(self.served)
        subprocess.check_call(["git", "clone", "-q", "--bare", work, os.path.join(self.served, "lib.git")])
        self.git(os.path.join(self.served, "lib.git"), "update-server-info")
        handler = functools.partial(AuthHandler, directory=self.served)
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.origin = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.url = self.origin + "/lib.git"
        AuthHandler.authorized = []
        # 親リポジトリ
        self.repo = os.path.join(root, "parent")
        os.makedirs(self.repo)
        self.git(self.repo, "init", "-q", "-b", "main")
        self.git(self.repo, "config", "user.name", "Test User")
        self.git(self.repo, "config", "user.email", "test@example.com")
        with open(os.path.join(self.repo, "README.md"), "w", encoding="utf-8") as f:
            f.write("parent\n")
        self.git(self.repo, "add", ".")
        self.git(self.repo, "commit", "-q", "-m", "initial commit")
        names = ("REPO", "UI_LANG", "WIP_ENABLED", "HAS_WORKTREE", "WRITABLE")
        saved = [getattr(state, k) for k in names]
        self.addCleanup(lambda: [setattr(state, k, v) for k, v in zip(names, saved)])
        state.REPO, state.UI_LANG, state.WIP_ENABLED, state.HAS_WORKTREE, state.WRITABLE = self.repo, "en", True, True, True
        gitrun.HTTP_AUTH_CACHE.clear()
        self.addCleanup(gitrun.HTTP_AUTH_CACHE.clear)
        # 認証情報ヘルパーなど、この環境の設定に左右されないようにする (askpass だけで認証する)
        env = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
        env.start()
        self.addCleanup(env.stop)

    @staticmethod
    def git(repo, *args):
        return subprocess.check_output(["git", "-C", repo, *args], text=True, encoding="utf-8").strip()

    def gitlinks(self):
        out = self.git(self.repo, "ls-files", "-s")
        return sorted(line.split("\t", 1)[1] for line in out.splitlines() if line.startswith("160000 "))

    def register_uninitialized(self, path="lib"):
        """HTTP の URL を持つ未初期化の submodule を、親に登録する (取得はしない)。"""
        self.git(self.repo, "config", "-f", ".gitmodules", f"submodule.{path}.path", path)
        self.git(self.repo, "config", "-f", ".gitmodules", f"submodule.{path}.url", self.url)
        self.git(self.repo, "update-index", "--add", "--cacheinfo", f"160000,{self.sub_commit},{path}")
        self.git(self.repo, "add", ".gitmodules")
        self.git(self.repo, "commit", "-q", "-m", "register " + path)


class OriginHelpersTests(unittest.TestCase):
    def test_http_origin(self):
        cases = {
            "fatal: could not read Username for 'https://GitHub.com': terminal prompts disabled": "https://github.com",
            "fatal: Authentication failed for 'https://user:pw@example.com:8443/a/b.git/'": "https://example.com:8443",
            "unable to access 'http://127.0.0.1:8000/x.git/': The requested URL returned error: 401": "http://127.0.0.1:8000",
            "fatal: Could not read from remote repository.": None,
            "ssh://git@host/x.git": None,
            "": None,
        }
        for text, expected in cases.items():
            self.assertEqual(gitrun.http_origin(text), expected, text)

    def test_askpass_decides_username_by_the_start_of_the_prompt(self):
        with tempfile.TemporaryDirectory() as d:
            script = os.path.join(d, "askpass.py")
            with open(script, "w", encoding="utf-8") as f:
                f.write(gitrun.ASKPASS_SCRIPT)
            env = dict(os.environ, GIT_GRAPH_HTTP_USERNAME="alice", GIT_GRAPH_HTTP_PASSWORD="pw", GIT_GRAPH_HTTP_HOST="example.com")

            def ask(prompt, **override):
                return subprocess.check_output(["python3", script, prompt], env=dict(env, **override), text=True).strip()
            self.assertEqual(ask("Username for 'https://example.com': "), "alice")
            self.assertEqual(ask("Password for 'https://user@example.com': "), "pw")  # URL の中の user に引きずられない
            self.assertEqual(ask("Password for 'https://alice@Example.COM:8443': "), "pw")
            self.assertEqual(ask("Username for 'https://evil.example.org': "), "")  # 別のホストには答えない
            self.assertEqual(ask("Password for 'https://example.com.evil.org': "), "")
            self.assertEqual(ask("Username for 'https://evil.org': ", GIT_GRAPH_HTTP_HOST=""), "alice")  # ホストの指定が無ければ従来どおり


class HttpAuthTests(HttpAuthTestBase):
    def test_credentials_are_only_given_to_the_named_host(self):
        r = gitrun.run_git_net("ls-remote", self.url, auth=GOOD, auth_host="other.example")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(AuthHandler.authorized, [])  # 別のホスト向けの指定では、認証情報を渡さない
        r = gitrun.run_git_net("ls-remote", self.url, auth=GOOD, auth_host="127.0.0.1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(AuthHandler.authorized)

    def test_add_asks_for_authentication_and_changes_nothing_until_it_is_given(self):
        res = api_submodule_add({"url": self.url, "path": "lib"})
        self.assertEqual(res, {"auth_required": True, "auth_requests": [{"origin": self.origin}]})
        self.assertEqual(self.gitlinks(), [])
        self.assertEqual(engine._load()["entries"], [])  # 履歴にも積まない

    def test_add_with_credentials_then_the_cache_is_used(self):
        res = api_submodule_add({"url": self.url, "path": "lib", "auth": GOOD, "auth_origin": self.origin})
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.gitlinks(), ["lib"])
        self.assertTrue(os.path.exists(os.path.join(self.repo, "lib", "lib.txt")))
        res = api_submodule_add({"url": self.url, "path": "lib2"})  # 覚えている認証情報で、尋ねずに通る
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.gitlinks(), ["lib", "lib2"])

    def test_wrong_password_is_an_error_not_another_request_and_is_not_remembered(self):
        bad = {"username": USER, "password": "wrong"}
        with self.assertRaises(UserError) as cm:
            api_submodule_add({"url": self.url, "path": "lib", "auth": bad, "auth_origin": self.origin})
        self.assertEqual(cm.exception.key, "submodule_add_failed")
        self.assertEqual(gitrun.HTTP_AUTH_CACHE, {})
        self.assertEqual(self.gitlinks(), [])

    def test_credentials_for_another_origin_are_not_used(self):
        res = api_submodule_add({"url": self.url, "path": "lib", "auth": GOOD, "auth_origin": "http://other.example"})
        self.assertTrue(res["auth_required"])  # この URL の origin には、渡されていない
        self.assertEqual(AuthHandler.authorized, [])

    def test_update_groups_paths_by_origin_and_retries_only_those(self):
        self.register_uninitialized("lib")
        self.register_uninitialized("lib2")
        res = api_submodule_update({"paths": ["lib", "lib2"]})
        self.assertFalse(res["ok"])
        self.assertTrue(res["auth_required"])
        self.assertEqual(res["auth_requests"], [{"origin": self.origin, "paths": ["lib", "lib2"]}])
        self.assertTrue(all(x["auth_origin"] == self.origin for x in res["results"]))
        res = api_submodule_update({"paths": ["lib", "lib2"], "auth": GOOD, "auth_origin": self.origin})
        self.assertTrue(res["ok"], res)
        self.assertNotIn("auth_required", res)
        self.assertTrue(all(s["initialized"] for s in qh.submodules()["submodules"]))
        self.assertTrue(os.path.exists(os.path.join(self.repo, "lib2", "lib.txt")))

    def test_update_uses_remembered_credentials_without_asking(self):
        self.register_uninitialized("lib")
        self.register_uninitialized("lib2")
        self.assertTrue(api_submodule_update({"paths": ["lib"], "auth": GOOD, "auth_origin": self.origin})["ok"])
        res = api_submodule_update({"paths": ["lib2"]})
        self.assertTrue(res["ok"], res)

    def test_update_with_wrong_credentials_does_not_ask_again(self):
        self.register_uninitialized("lib")
        res = api_submodule_update({"paths": ["lib"], "auth": {"username": USER, "password": "x"}, "auth_origin": self.origin})
        self.assertFalse(res["ok"])
        self.assertNotIn("auth_required", res)
        self.assertTrue(res["results"][0]["error"])

    def test_fetch_asks_for_authentication_too(self):
        self.register_uninitialized("lib")
        self.assertTrue(api_submodule_update({"paths": ["lib"], "auth": GOOD, "auth_origin": self.origin})["ok"])
        gitrun.HTTP_AUTH_CACHE.clear()
        res = api_submodule_fetch({"paths": ["lib"]})
        self.assertTrue(res["auth_required"], res)
        self.assertEqual(res["auth_requests"], [{"origin": self.origin, "paths": ["lib"]}])
        res = api_submodule_fetch({"paths": ["lib"], "auth": GOOD, "auth_origin": self.origin})
        self.assertTrue(res["ok"], res)

    def test_bad_auth_arguments_are_rejected(self):
        self.register_uninitialized("lib")
        for body in ({"paths": ["lib"], "auth": "x", "auth_origin": self.origin},
                     {"paths": ["lib"], "auth": GOOD},
                     {"paths": ["lib"], "auth": {"username": 1}, "auth_origin": self.origin},
                     {"paths": ["lib"], "auth": GOOD, "auth_origin": ""}):
            with self.assertRaises(UserError, msg=body) as cm:
                api_submodule_update(body)
            self.assertEqual(cm.exception.key, "auth_invalid")

    def test_add_over_http_can_be_undone(self):
        res = engine.run("/api/submodule-add", ROUTES[("POST", "/api/submodule-add")],
                         {"url": self.url, "path": "lib", "auth": GOOD, "auth_origin": self.origin})
        self.assertTrue(res["ok"], res)
        self.assertEqual(len(engine._load()["entries"]), 1)
        self.assertTrue(ROUTES[("POST", "/api/undo")]({})["ok"])
        self.assertEqual(self.gitlinks(), [])


if __name__ == "__main__":
    unittest.main()
