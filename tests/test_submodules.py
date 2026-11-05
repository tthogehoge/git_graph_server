import os
import subprocess
import tempfile
import unittest
from unittest import mock

import git_graph_server as app  # noqa: F401  (各モジュールの @route 登録を実行させる)
from git_graph import app_state as state
from git_graph.i18n import UserError
from git_graph.query import history as qh
from git_graph import engine
from git_graph.routing import ROUTES
from git_graph.submodules import api_submodule_add, api_submodule_fetch, api_submodule_update

SUB_DIR = "sub dir"  # 空白を含むパスでも更新できること
LOCAL_PROTOCOL = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "protocol.file.allow", "GIT_CONFIG_VALUE_0": "always"}


class SubmoduleTestBase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = self.temp_dir.name
        self.sub_src = os.path.join(root, "sub_src")
        self.repo = os.path.join(root, "parent")
        for path in (self.sub_src, self.repo):
            os.makedirs(path)
            self.git(path, "init", "-q", "-b", "main")
            self.git(path, "config", "user.name", "Test User")
            self.git(path, "config", "user.email", "test@example.com")
        self.sub_first = self.commit(self.sub_src, "lib.txt", "one\n", "first in submodule")
        self.commit(self.repo, "README.md", "parent\n", "initial commit")
        self.git(self.repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q", self.sub_src, SUB_DIR)
        self.git(self.repo, "commit", "-q", "-m", "add submodule")
        previous = (state.REPO, state.UI_LANG)
        self.addCleanup(lambda: (setattr(state, "REPO", previous[0]), setattr(state, "UI_LANG", previous[1])))
        state.REPO, state.UI_LANG = self.repo, "en"
        env = mock.patch.dict(os.environ, LOCAL_PROTOCOL)  # ローカルのパスの submodule を取得できるようにする (テスト用)
        env.start()
        self.addCleanup(env.stop)

    @staticmethod
    def git(repo, *args):
        return subprocess.check_output(["git", "-C", repo, *args], text=True, encoding="utf-8").strip()

    def commit(self, repo, name, content, message):
        with open(os.path.join(repo, name), "w", encoding="utf-8") as f:
            f.write(content)
        self.git(repo, "add", ".")
        self.git(repo, "commit", "-q", "-m", message)
        return self.git(repo, "rev-parse", "HEAD")

    def sub_head(self):
        return self.git(os.path.join(self.repo, SUB_DIR), "rev-parse", "HEAD")


class SubmoduleUpdateTests(SubmoduleTestBase):

    def test_update_initializes_a_deinitialized_submodule(self):
        self.git(self.repo, "submodule", "deinit", "-q", "-f", "--", SUB_DIR)
        self.assertFalse(qh.submodules()["submodules"][0]["initialized"])
        res = api_submodule_update({"paths": [SUB_DIR]})
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["results"], [{"path": SUB_DIR, "ok": True, "error": ""}])
        self.assertEqual(res["message"], "Updated 1 of 1 submodule(s)")
        self.assertTrue(qh.submodules()["submodules"][0]["initialized"])
        self.assertEqual(self.sub_head(), self.sub_first)

    def test_update_returns_to_the_recorded_commit(self):
        sub = os.path.join(self.repo, SUB_DIR)
        self.git(sub, "config", "user.name", "Test User")
        self.git(sub, "config", "user.email", "test@example.com")
        self.commit(sub, "extra.txt", "x\n", "local commit in submodule")
        self.assertNotEqual(self.sub_head(), self.sub_first)
        self.git(sub, "checkout", "-q", "--detach", "HEAD")  # submodule は detached HEAD で動く。そこで、記録とは別のコミットを作る
        local = self.commit(sub, "extra2.txt", "y\n", "another local commit")
        self.assertEqual(self.sub_head(), local)
        res = api_submodule_update({"paths": [SUB_DIR]})
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.sub_head(), self.sub_first)

    def test_update_remote_moves_to_the_latest_remote_commit(self):
        latest = self.commit(self.sub_src, "lib.txt", "two\n", "second in submodule")
        res = api_submodule_update({"paths": [SUB_DIR], "remote": True})
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.sub_head(), latest)
        self.assertEqual(qh.submodules()["commits"][0][2], "modified")  # このリポジトリでは変更として現れる
        res = api_submodule_update({"paths": [SUB_DIR]})  # 記録されたコミットへの更新で戻る
        self.assertEqual(self.sub_head(), self.sub_first)

    def test_a_failure_is_reported_per_submodule_and_does_not_stop_the_others(self):
        # 取得元が存在しない submodule を登録する (second の取得は失敗する)
        missing = os.path.join(self.temp_dir.name, "no_such_repo")
        self.git(self.repo, "config", "-f", ".gitmodules", "submodule.second.path", "second")
        self.git(self.repo, "config", "-f", ".gitmodules", "submodule.second.url", missing)
        self.git(self.repo, "update-index", "--add", "--cacheinfo", f"160000,{self.sub_first},second")
        self.git(self.repo, "add", ".gitmodules")
        self.git(self.repo, "commit", "-q", "-m", "add second")
        self.git(self.repo, "submodule", "deinit", "-q", "-f", "--", SUB_DIR)
        res = api_submodule_update({"paths": ["second", SUB_DIR]})
        by_path = {x["path"]: x for x in res["results"]}
        self.assertFalse(res["ok"])
        self.assertFalse(by_path["second"]["ok"])
        self.assertTrue(by_path["second"]["error"])
        self.assertTrue(by_path[SUB_DIR]["ok"], by_path[SUB_DIR])  # 失敗のあとでも、残りは実行される
        self.assertEqual(res["message"], "Updated 1 of 2 submodule(s)")

    def test_local_changes_in_the_submodule_are_not_overwritten(self):
        sub = os.path.join(self.repo, SUB_DIR)
        latest = self.commit(self.sub_src, "lib.txt", "two\n", "second in submodule")
        api_submodule_update({"paths": [SUB_DIR], "remote": True})
        self.assertEqual(self.sub_head(), latest)
        with open(os.path.join(sub, "lib.txt"), "w", encoding="utf-8") as f:
            f.write("edited locally\n")
        res = api_submodule_update({"paths": [SUB_DIR]})  # 記録されたコミットへ戻したいが、編集中のファイルは上書きしない
        self.assertFalse(res["ok"])
        with open(os.path.join(sub, "lib.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "edited locally\n")

    def test_rejects_bad_requests_before_running_anything(self):
        for body in ({}, {"paths": []}, {"paths": "x"}, {"paths": [1]}):
            with self.assertRaises(ValueError, msg=body):
                api_submodule_update(body)
        with self.assertRaises(ValueError):
            api_submodule_update({"paths": [f"p{i}" for i in range(201)]})
        for path in ("README.md", "missing", "../parent"):
            with self.assertRaises(ValueError, msg=path):  # UserError も ValueError
                api_submodule_update({"paths": [SUB_DIR, path]})
        # 1 つでも登録されていなければ、どれも実行しない
        self.git(self.repo, "submodule", "deinit", "-q", "-f", "--", SUB_DIR)
        with self.assertRaises(UserError):
            api_submodule_update({"paths": [SUB_DIR, "README.md"]})
        self.assertFalse(qh.submodules()["submodules"][0]["initialized"])

    def test_duplicate_paths_are_updated_once(self):
        res = api_submodule_update({"paths": [SUB_DIR, SUB_DIR]})
        self.assertEqual(len(res["results"]), 1)

    def test_recursive_update_also_initializes_nested_submodules(self):
        inner_src = os.path.join(self.temp_dir.name, "inner_src")
        os.makedirs(inner_src)
        self.git(inner_src, "init", "-q", "-b", "main")
        self.git(inner_src, "config", "user.name", "Test User")
        self.git(inner_src, "config", "user.email", "test@example.com")
        self.commit(inner_src, "inner.txt", "inner\n", "inner commit")
        self.git(self.sub_src, "-c", "protocol.file.allow=always", "submodule", "add", "-q", inner_src, "inner")
        self.git(self.sub_src, "commit", "-q", "-m", "add inner")
        nested = os.path.join(self.repo, SUB_DIR, "inner", "inner.txt")
        res = api_submodule_update({"paths": [SUB_DIR], "remote": True})  # 入れ子を持つ新しいコミットへ (入れ子は取得しない)
        self.assertTrue(res["ok"], res)
        self.assertFalse(os.path.exists(nested))
        res = api_submodule_update({"paths": [SUB_DIR], "remote": True, "recursive": True})
        self.assertTrue(res["ok"], res)
        self.assertTrue(os.path.exists(nested))
        sub = os.path.join(self.repo, SUB_DIR)
        self.assertTrue(os.path.exists(os.path.join(sub, "inner", ".git")))


class SubmoduleFetchTests(SubmoduleTestBase):
    def test_fetch_brings_new_commits_without_changing_the_working_tree(self):
        latest = self.commit(self.sub_src, "lib.txt", "two\n", "second in submodule")
        res = api_submodule_fetch({"paths": [SUB_DIR]})
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["message"], "Fetched 1 of 1 submodule(s)")
        sub = os.path.join(self.repo, SUB_DIR)
        self.assertEqual(self.git(sub, "rev-parse", "origin/main"), latest)
        self.assertEqual(self.sub_head(), self.sub_first)  # 作業ツリーは動かない
        self.assertTrue(api_submodule_fetch({"paths": [SUB_DIR], "recursive": True})["ok"])

    def test_fetch_reports_an_uninitialized_submodule(self):
        self.git(self.repo, "submodule", "deinit", "-q", "-f", "--", SUB_DIR)
        res = api_submodule_fetch({"paths": [SUB_DIR]})
        self.assertFalse(res["ok"])
        self.assertIn(SUB_DIR, res["results"][0]["error"])

    def test_fetch_rejects_unregistered_paths(self):
        with self.assertRaises(ValueError):
            api_submodule_fetch({"paths": [SUB_DIR, "README.md"]})


class SubmoduleAddTests(SubmoduleTestBase):
    def gitlinks(self):
        out = self.git(self.repo, "ls-files", "-s")
        return sorted(line.split("\t", 1)[1] for line in out.splitlines() if line.startswith("160000 "))

    def test_add_registers_a_new_submodule(self):
        res = api_submodule_add({"url": self.sub_src, "path": "libs/extra/", "branch": "main"})
        self.assertTrue(res["ok"])
        self.assertEqual(res["path"], "libs/extra")  # 末尾の / は除く
        self.assertEqual(self.gitlinks(), ["libs/extra", SUB_DIR])
        self.assertEqual(self.git(self.repo, "config", "-f", ".gitmodules", "submodule.libs/extra.branch"), "main")
        self.assertTrue(os.path.exists(os.path.join(self.repo, "libs", "extra", "lib.txt")))
        self.assertTrue(any(s["path"] == "libs/extra" and s["initialized"] for s in qh.submodules()["submodules"]))

    def test_add_with_recursive_fetches_nested_submodules(self):
        inner_src = os.path.join(self.temp_dir.name, "inner_src")
        os.makedirs(inner_src)
        self.git(inner_src, "init", "-q", "-b", "main")
        self.git(inner_src, "config", "user.name", "Test User")
        self.git(inner_src, "config", "user.email", "test@example.com")
        self.commit(inner_src, "inner.txt", "inner\n", "inner commit")
        self.git(self.sub_src, "-c", "protocol.file.allow=always", "submodule", "add", "-q", inner_src, "inner")
        self.git(self.sub_src, "commit", "-q", "-m", "add inner")
        api_submodule_add({"url": self.sub_src, "path": "plain"})
        self.assertFalse(os.path.exists(os.path.join(self.repo, "plain", "inner", "inner.txt")))
        api_submodule_add({"url": self.sub_src, "path": "deep", "recursive": True})
        self.assertTrue(os.path.exists(os.path.join(self.repo, "deep", "inner", "inner.txt")))

    def test_add_rejects_bad_input_without_running_git(self):
        marker = os.path.join(self.temp_dir.name, "pwned")
        for url in ("", "  ", "-x", "--upload-pack=touch " + marker, "a\nb", "x" * 2001, "ext::sh -c 'touch " + marker + "' %s"):
            with self.assertRaises(UserError, msg=url) as cm:
                api_submodule_add({"url": url, "path": "p"})
            self.assertIn(cm.exception.key, ("submodule_url_invalid", "submodule_add_failed"), url)
        self.assertFalse(os.path.exists(marker))
        for path in ("", "/abs", "../out", "a\0b"):
            with self.assertRaises(ValueError, msg=path):
                api_submodule_add({"url": self.sub_src, "path": path})
        for branch in ("-b", "bad branch name", "a..b"):
            with self.assertRaises(UserError, msg=branch):
                api_submodule_add({"url": self.sub_src, "path": "p", "branch": branch})
        for body in ({}, {"url": 1, "path": "p"}, {"url": "u", "path": None}):
            with self.assertRaises(ValueError):
                api_submodule_add(body)
        self.assertEqual(self.gitlinks(), [SUB_DIR])

    def test_add_reports_git_failures(self):
        with self.assertRaises(UserError) as cm:  # 取得元が存在しない
            api_submodule_add({"url": os.path.join(self.temp_dir.name, "missing"), "path": "p"})
        self.assertEqual(cm.exception.key, "submodule_add_failed")
        with self.assertRaises(UserError):  # すでにあるパス
            api_submodule_add({"url": self.sub_src, "path": "README.md"})
        self.assertEqual(self.gitlinks(), [SUB_DIR])

    def test_add_goes_into_the_undo_history_and_can_be_undone(self):
        saved = (state.WIP_ENABLED, state.HAS_WORKTREE, state.WRITABLE)
        state.WIP_ENABLED, state.HAS_WORKTREE, state.WRITABLE = True, True, True
        self.addCleanup(lambda: [setattr(state, k, v) for k, v in zip(("WIP_ENABLED", "HAS_WORKTREE", "WRITABLE"), saved)])
        before = self.git(self.repo, "ls-files", "-s")
        res = engine.run("/api/submodule-add", ROUTES[("POST", "/api/submodule-add")], {"url": self.sub_src, "path": "added"})
        self.assertTrue(res["ok"])
        self.assertEqual(self.gitlinks(), ["added", SUB_DIR])
        self.assertEqual(len(engine._load()["entries"]), 1)
        self.assertTrue(ROUTES[("POST", "/api/undo")]({})["ok"])
        self.assertEqual(self.git(self.repo, "ls-files", "-s"), before)  # インデックスは元に戻る
        self.assertEqual(self.git(self.repo, "status", "--short", "--", ".gitmodules"), "")  # .gitmodules も元に戻る
        # 取得したディレクトリは消さない (中身を勝手に消さない)。管理外の既存のリポジトリとして残り、同じ場所へ追加し直せる
        self.assertTrue(os.path.exists(os.path.join(self.repo, "added", ".git")))
        self.assertEqual(self.gitlinks(), [SUB_DIR])
        self.assertTrue(api_submodule_add({"url": self.sub_src, "path": "added"})["ok"])
        self.assertEqual(self.gitlinks(), ["added", SUB_DIR])


if __name__ == "__main__":
    unittest.main()
