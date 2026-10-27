import os
import subprocess
import tempfile
import unittest

import git_graph_server as app  # noqa: F401  (各モジュールの @route 登録を実行させる)
from git_graph import app_state as state
from git_graph import engine
from git_graph.i18n import UserError
from git_graph.routing import ROUTES


class RouteTableTests(unittest.TestCase):
    def test_every_post_route_is_classified(self):
        """新しい書き込み API を足したら、engine.WRITE_OPS か engine.IGNORED に登録する (登録漏れの検出)。"""
        posts = {path for (method, path) in ROUTES if method == "POST"}
        unknown = posts - set(engine.WRITE_OPS) - engine.IGNORED
        self.assertEqual(unknown, set())
        stale = (set(engine.WRITE_OPS) | engine.IGNORED) - posts
        self.assertEqual(stale, set())

    def test_remote_side_operations_are_not_recorded(self):
        self.assertIsNone(engine.write_op_for("/api/push", {}))
        self.assertIsNone(engine.write_op_for("/api/ref-delete", {"kind": "remote-branch", "name": "origin/x"}))
        self.assertEqual(engine.write_op_for("/api/ref-delete", {"kind": "branch"}), ("delete_ref", False))
        self.assertEqual(engine.write_op_for("/api/not-listed", {}), ("other", "names"))  # 未登録は安全側 (作業ツリーも管理外ファイルの名前も記録)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = self.temp_dir.name
        self.saved = (state.REPO, state.WIP_ENABLED, state.HAS_WORKTREE, state.WRITABLE)
        state.REPO, state.WIP_ENABLED, state.HAS_WORKTREE, state.WRITABLE = self.repo, True, True, True
        self.git("init", "-q")
        self.git("config", "user.name", "Test User")
        self.git("config", "user.email", "test@example.com")
        self.git("branch", "-M", "main")
        self.write("tracked.txt", "initial\n")
        self.git("add", "tracked.txt")
        self.git("commit", "-q", "-m", "initial commit")
        self.head = self.git("rev-parse", "HEAD").strip()

    def tearDown(self):
        engine.cleanup()
        state.REPO, state.WIP_ENABLED, state.HAS_WORKTREE, state.WRITABLE = self.saved
        self.temp_dir.cleanup()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", self.repo, *args], text=True, encoding="utf-8")

    def write(self, path, content):
        full = os.path.join(self.repo, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)

    def read(self, path):
        with open(os.path.join(self.repo, path), encoding="utf-8") as f:
            return f.read()

    def exists(self, path):
        return os.path.exists(os.path.join(self.repo, path))

    def ref_exists(self, ref):
        return subprocess.run(["git", "-C", self.repo, "show-ref", "--verify", "--quiet", ref]).returncode == 0

    def do(self, path, body):
        """server.do_POST と同じく engine.run を通して API を実行する。"""
        return engine.run(path, ROUTES[("POST", path)], body)

    def undo(self, **body):
        return ROUTES[("POST", "/api/undo")](body)

    def redo(self, **body):
        return ROUTES[("POST", "/api/redo")](body)

    def commit_file(self, name, content, msg):
        self.write(name, content)
        self.git("add", name)
        self.git("commit", "-q", "-m", msg)
        return self.git("rev-parse", "HEAD").strip()

    # ---- ref 操作

    def test_branch_create_undo_redo(self):
        self.do("/api/branch", {"hash": self.head, "name": "feature"})
        self.assertTrue(self.ref_exists("refs/heads/feature"))
        self.assertEqual(len(engine._load()["entries"]), 1)

        self.assertTrue(self.undo()["ok"])
        self.assertFalse(self.ref_exists("refs/heads/feature"))
        self.assertTrue(self.redo()["ok"])
        self.assertEqual(self.git("rev-parse", "refs/heads/feature").strip(), self.head)

    def test_branch_delete_and_rename_undo(self):
        self.do("/api/branch", {"hash": self.head, "name": "old-name"})
        self.do("/api/branch-rename", {"old": "old-name", "new": "new-name"})
        self.assertTrue(self.ref_exists("refs/heads/new-name"))
        self.undo()
        self.assertTrue(self.ref_exists("refs/heads/old-name"))
        self.assertFalse(self.ref_exists("refs/heads/new-name"))
        self.do("/api/ref-delete", {"kind": "branch", "name": "old-name"})
        self.assertFalse(self.ref_exists("refs/heads/old-name"))
        self.undo()
        self.assertEqual(self.git("rev-parse", "refs/heads/old-name").strip(), self.head)

    def test_nothing_changed_records_nothing(self):
        with self.assertRaises(UserError):
            self.do("/api/branch", {"hash": self.head, "name": "bad name"})  # 失敗して何も変わらない
        self.assertEqual(engine._load()["entries"], [])
        with self.assertRaises(UserError):
            self.undo()

    def test_new_operation_discards_redo_tail(self):
        self.do("/api/branch", {"hash": self.head, "name": "a"})
        self.undo()
        self.do("/api/branch", {"hash": self.head, "name": "b"})
        with self.assertRaises(UserError):
            self.redo()
        self.assertEqual([e["detail"] for e in engine._load()["entries"]], ["b"])

    # ---- 作業ツリー

    def test_commit_undo_keeps_untracked_file_untracked(self):
        self.write("new.txt", "brand new\n")
        self.write("tracked.txt", "edited\n")
        self.do("/api/wip-commit", {"paths": ["new.txt", "tracked.txt"], "message": "c2"})
        self.assertEqual(self.git("status", "--porcelain").strip(), "")
        self.undo()
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.head)
        self.assertEqual(self.read("new.txt"), "brand new\n")  # コミットを戻しても、ファイルは消えない
        self.assertEqual(self.read("tracked.txt"), "edited\n")
        self.assertIn("?? new.txt", self.git("status", "--porcelain"))

    def test_hard_reset_undo_restores_modified_tracked_files(self):
        second = self.commit_file("second.txt", "two\n", "second")
        self.write("tracked.txt", "staged edit\n")
        self.git("add", "tracked.txt")
        self.write("second.txt", "unstaged edit\n")
        self.write("scratch.txt", "untracked\n")

        self.do("/api/reset", {"hash": self.head, "mode": "hard"})
        self.assertFalse(self.exists("second.txt"))
        self.assertEqual(self.read("tracked.txt"), "initial\n")

        self.undo()
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), second)
        self.assertEqual(self.read("tracked.txt"), "staged edit\n")
        self.assertEqual(self.read("second.txt"), "unstaged edit\n")
        self.assertEqual(self.read("scratch.txt"), "untracked\n")  # 管理外ファイルには触れない
        # index (ステージ状態) は対象外: HEAD に合わせられ、変更は未ステージとして残る
        self.assertEqual(self.git("diff", "--cached", "--name-only").strip(), "")
        self.assertIn(" M tracked.txt", self.git("status", "--porcelain"))

        self.redo()
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.head)
        self.assertFalse(self.exists("second.txt"))
        self.assertEqual(self.read("scratch.txt"), "untracked\n")

    def test_untracked_files_are_neither_restored_nor_stored(self):
        self.write("scratch.txt", "precious and unique 8c1f\n")
        self.do("/api/discard", {"paths": ["scratch.txt"]})
        self.assertFalse(self.exists("scratch.txt"))
        self.assertEqual(engine._load()["entries"], [])  # 管理外ファイルだけの変化は履歴に積まない (戻せない)

    def test_snapshot_does_not_store_untracked_content_or_index(self):
        self.write("junk.bin", "untracked blob content 77ab\n")
        blob = subprocess.check_output(["git", "-C", self.repo, "hash-object", "junk.bin"], text=True).strip()
        self.write("tracked.txt", "edit\n")
        self.do("/api/discard", {"paths": ["tracked.txt"]})
        missing = subprocess.run(["git", "-C", self.repo, "cat-file", "-e", blob]).returncode != 0
        self.assertTrue(missing)  # 管理外ファイルの blob は .git に書かれない
        entry = engine._load()["entries"][0]
        for w in entry["wt"]:
            self.assertEqual(sorted(w), ["protect", "tree"])  # index も管理外の名前の一覧も持たない
            self.assertEqual(w["protect"], [])

    def test_checkout_branch_undo_returns_to_previous_branch(self):
        self.do("/api/branch", {"hash": self.head, "name": "other"})
        self.do("/api/checkout", {"branch": "other"})
        self.assertEqual(self.git("symbolic-ref", "--short", "HEAD").strip(), "other")
        self.undo()
        self.assertEqual(self.git("symbolic-ref", "--short", "HEAD").strip(), "main")

    def test_stash_push_and_drop_undo(self):
        self.write("tracked.txt", "wip\n")
        self.do("/api/stash-push", {})
        self.assertEqual(self.read("tracked.txt"), "initial\n")
        stash = self.git("rev-parse", "refs/stash").strip()
        self.undo()
        self.assertEqual(self.read("tracked.txt"), "wip\n")
        self.assertEqual(self.git("stash", "list").strip(), "")
        self.redo()
        self.assertEqual(self.git("rev-parse", "refs/stash").strip(), stash)

        self.do("/api/stash", {"hash": stash, "op": "drop"})
        self.assertEqual(self.git("stash", "list").strip(), "")
        self.undo()
        self.assertEqual(self.git("rev-parse", "refs/stash").strip(), stash)  # drop したstashも一覧ごと戻る

    def test_stash_push_undo_brings_back_untracked_files(self):
        self.write("tracked.txt", "wip\n")
        self.write("notes.txt", "untracked note\n")
        self.do("/api/stash-push", {})  # 管理外ファイルもstashに入り、作業ツリーから消える
        self.assertFalse(self.exists("notes.txt"))
        self.undo()
        self.assertEqual(self.read("notes.txt"), "untracked note\n")  # stashの中にしか無い状態にしない
        self.assertEqual(self.read("tracked.txt"), "wip\n")
        self.redo()
        self.assertFalse(self.exists("notes.txt"))
        self.assertEqual(self.read("tracked.txt"), "initial\n")

    # ---- 途中停止 (競合)

    def make_conflict(self):
        self.git("checkout", "-q", "-b", "side")
        self.commit_file("tracked.txt", "side\n", "side change")
        self.git("checkout", "-q", "main")
        self.commit_file("tracked.txt", "main\n", "main change")
        return self.git("rev-parse", "side").strip()

    def test_clean_merge_undo(self):
        self.git("checkout", "-q", "-b", "side")
        side = self.commit_file("side.txt", "s\n", "side")
        self.git("checkout", "-q", "main")
        main = self.commit_file("main.txt", "m\n", "main")
        self.do("/api/merge", {"hash": side, "no_ff": True})
        self.assertNotEqual(self.git("rev-parse", "HEAD").strip(), main)
        self.undo()
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), main)
        self.assertFalse(self.exists("side.txt"))

    def test_conflicting_merge_is_one_entry_until_it_finishes(self):
        side = self.make_conflict()
        main = self.git("rev-parse", "HEAD").strip()
        try:
            self.do("/api/merge", {"hash": side})
        except ValueError:
            pass
        self.assertTrue(engine.in_progress())
        h = engine._load()
        self.assertEqual(h["entries"], [])          # 途中は履歴に積まない
        self.assertIsNotNone(h["pending"])
        with self.assertRaises(UserError):          # 途中では undo できない
            self.undo()

        self.do("/api/abort", {})                    # 中断すれば元の状態なので、履歴は空のまま
        h = engine._load()
        self.assertEqual((h["entries"], h["pending"]), ([], None))
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), main)

    def test_resolved_merge_becomes_single_undoable_entry(self):
        side = self.make_conflict()
        main = self.git("rev-parse", "HEAD").strip()
        try:
            self.do("/api/merge", {"hash": side})
        except ValueError:
            pass
        self.write("tracked.txt", "resolved\n")
        self.do("/api/wip-commit", {"paths": ["tracked.txt"], "message": "merge side"})
        h = engine._load()
        self.assertEqual([e["op"] for e in h["entries"]], ["merge"])  # 最初の操作の名前で1件にまとまる
        self.undo()
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), main)
        self.assertEqual(self.read("tracked.txt"), "main\n")

    # ---- 安全装置

    def test_undo_refused_when_ref_moved_by_someone_else(self):
        self.do("/api/branch", {"hash": self.head, "name": "feature"})
        second = self.commit_file("x.txt", "x\n", "x")
        self.git("branch", "-f", "feature", second)  # このアプリの外で動かされた
        with self.assertRaises(UserError) as ctx:
            self.undo()
        self.assertEqual(ctx.exception.key, "undo_state_changed")
        self.assertEqual(self.git("rev-parse", "feature").strip(), second)  # 何も壊していない

    def test_worktree_edit_after_operation_needs_force_and_can_be_redone(self):
        self.write("tracked.txt", "edit1\n")
        self.do("/api/discard", {"paths": ["tracked.txt"]})
        self.write("tracked.txt", "edit2\n")  # discard のあとに、また編集した
        r = self.undo()
        self.assertTrue(r["worktree_changed"])
        self.assertEqual(self.read("tracked.txt"), "edit2\n")  # 確認なしでは上書きしない
        r = self.undo(force=True)
        self.assertTrue(r["ok"])
        self.assertEqual(self.read("tracked.txt"), "edit1\n")
        self.assertTrue(self.redo()["ok"])               # 上書きされた edit2 は、やり直しで戻る
        self.assertEqual(self.read("tracked.txt"), "edit2\n")
        self.assertTrue(self.undo()["ok"])               # 元に戻す先は、この状態の前 (edit1) のまま
        self.assertEqual(self.read("tracked.txt"), "edit1\n")

    def test_nothing_is_written_to_git_dir(self):
        """退避は一時ディレクトリに書く: .git/objects にも refs にも何も残さない。"""
        self.write("tracked.txt", "modified content unique 91cf\n")
        blob = subprocess.check_output(["git", "-C", self.repo, "hash-object", "tracked.txt"], text=True).strip()
        self.do("/api/discard", {"paths": ["tracked.txt"]})
        self.assertEqual(self.read("tracked.txt"), "initial\n")
        self.assertNotEqual(subprocess.run(["git", "-C", self.repo, "cat-file", "-e", blob]).returncode, 0)  # .git には無い
        self.assertTrue(self.undo()["ok"])                     # それでも元に戻せる (一時ディレクトリから読む)
        self.assertEqual(self.read("tracked.txt"), "modified content unique 91cf\n")
        self.assertTrue(self.redo()["ok"])
        self.assertEqual(self.read("tracked.txt"), "initial\n")

    def test_object_env_follows_repository_alternates(self):
        alt = os.path.join(self.repo, ".git", "objects", "info", "alternates")
        os.makedirs(os.path.dirname(alt), exist_ok=True)
        with open(alt, "w", encoding="utf-8") as f:
            f.write("/some/other/objects\n")
        engine.cleanup()
        env = engine._object_env()
        self.assertIn("/some/other/objects", env["GIT_ALTERNATE_OBJECT_DIRECTORIES"].split(os.pathsep))
        self.assertTrue(env["GIT_OBJECT_DIRECTORY"].startswith(tempfile.gettempdir()))

    def test_no_undo_option_bypasses_recording(self):
        class Opts:
            no_undo = True
        saved, state.OPTS = getattr(state, "OPTS", None), Opts()
        try:
            self.do("/api/branch", {"hash": self.head, "name": "plain"})
        finally:
            state.OPTS = saved
        self.assertTrue(self.ref_exists("refs/heads/plain"))
        self.assertEqual(engine._load()["entries"], [])

    def test_undo_redo_buttons_are_disabled_when_nothing_to_do(self):
        hist = ROUTES[("GET", "/api/undo-history")]
        res = hist({})
        self.assertEqual((res["can_undo"], res["can_redo"]), (False, False))  # 履歴が空
        self.do("/api/branch", {"hash": self.head, "name": "x"})
        res = hist({})
        self.assertEqual((res["can_undo"], res["can_redo"]), (True, False))   # 戻す先だけある
        self.undo()
        res = hist({})
        self.assertEqual((res["can_undo"], res["can_redo"]), (False, True))   # やり直す先だけある

    def test_buttons_disabled_with_no_undo_and_read_only(self):
        hist = ROUTES[("GET", "/api/undo-history")]
        self.do("/api/branch", {"hash": self.head, "name": "x"})
        state.WRITABLE = False
        self.assertFalse(hist({})["can_undo"])
        state.WRITABLE = True

        class Opts:
            no_undo = True
        saved, state.OPTS = getattr(state, "OPTS", None), Opts()
        try:
            res = hist({})
        finally:
            state.OPTS = saved
        self.assertEqual((res["can_undo"], res["can_redo"], res["entries"]), (False, False, []))

    def test_history_is_memory_only_and_cleanup_removes_scratch_dir(self):
        self.write("tracked.txt", "edit\n")
        self.do("/api/discard", {"paths": ["tracked.txt"]})
        scratch = engine._SCRATCH[self.repo][0]
        self.assertTrue(os.path.isdir(scratch))
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".git", "git_graph_undo.json")))  # ファイルには保存しない
        engine.cleanup()  # 起動時・終了時
        self.assertFalse(os.path.exists(scratch))
        self.assertEqual(engine._load()["entries"], [])
        with self.assertRaises(UserError):
            self.undo()

    def test_history_is_limited(self):
        for i in range(engine.HIST_LIMIT + 5):
            self.do("/api/tag", {"hash": self.head, "name": f"t{i}"})
        self.assertEqual(len(engine._load()["entries"]), engine.HIST_LIMIT)

    def test_history_api_lists_entries_newest_first(self):
        self.do("/api/branch", {"hash": self.head, "name": "one"})
        self.do("/api/tag", {"hash": self.head, "name": "two"})
        self.undo()
        res = ROUTES[("GET", "/api/undo-history")]({})
        self.assertEqual([e["done"] for e in res["entries"]], [False, True])
        self.assertTrue(res["can_undo"])
        self.assertTrue(res["can_redo"])
        self.assertIn("one", res["next_undo"])
        self.assertIn("two", res["next_redo"])

    def test_recording_failure_never_blocks_the_operation(self):
        original = engine.take
        engine.take = lambda with_worktree: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            self.do("/api/branch", {"hash": self.head, "name": "still-works"})
        finally:
            engine.take = original
        self.assertTrue(self.ref_exists("refs/heads/still-works"))

    def test_remote_add_undo_removes_config_and_tracking_refs(self):
        with tempfile.TemporaryDirectory() as other:
            subprocess.check_call(["git", "clone", "-q", self.repo, other])
            self.do("/api/remote-add", {"name": "up", "url": other, "fetch": True})
            self.assertIn("up", self.git("remote").split())
            self.undo()
            self.assertNotIn("up", self.git("remote").split())
            self.redo()
            self.assertIn("up", self.git("remote").split())
            self.assertTrue(self.ref_exists("refs/remotes/up/main"))


if __name__ == "__main__":
    unittest.main()
