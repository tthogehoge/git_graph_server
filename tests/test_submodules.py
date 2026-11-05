import os
import subprocess
import tempfile
import unittest
from unittest import mock

import git_graph_server as app  # noqa: F401  (各モジュールの @route 登録を実行させる)
from git_graph import app_state as state
from git_graph.i18n import UserError
from git_graph.query import history as qh
from git_graph.submodules import api_submodule_update

SUB_DIR = "sub dir"  # 空白を含むパスでも更新できること
LOCAL_PROTOCOL = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "protocol.file.allow", "GIT_CONFIG_VALUE_0": "always"}


class SubmoduleUpdateTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
