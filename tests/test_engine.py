import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import git_graph_server as app  # noqa: F401  (各モジュールの @route 登録を実行させる)
from git_graph import app_state as state
from git_graph import engine, tmpdir
from git_graph.i18n import UserError
from git_graph.routing import ENGINE_ROUTES, ROUTES


class RouteTableTests(unittest.TestCase):
    def test_every_post_route_is_classified(self):
        """新しい書き込み API を足したら、engine の操作を呼ぶ route (engine=True) にするか、engine.WRITE_OPS / engine.IGNORED に登録する (登録漏れの検出)。"""
        posts = {path for (method, path) in ROUTES if method == "POST"}
        unknown = posts - set(engine.WRITE_OPS) - engine.IGNORED - ENGINE_ROUTES
        self.assertEqual(unknown, set())
        stale = (set(engine.WRITE_OPS) | engine.IGNORED | ENGINE_ROUTES) - posts
        self.assertEqual(stale, set())
        self.assertEqual(set(engine.WRITE_OPS) & ENGINE_ROUTES, set())  # 二重に記録しない
        self.assertEqual(engine.IGNORED & ENGINE_ROUTES, set())

    def test_remote_side_operations_are_not_recorded(self):
        # push などリモート側を変える操作は、engine の関数でも @operation にしない (元に戻せないので履歴に積まない)
        from git_graph.engine import refs, remote
        for fn in (remote.push, remote.push_tag, refs.delete_remote_branch):
            self.assertFalse(hasattr(fn, "operation_name"), fn.__name__)
        for fn in (remote.fetch, remote.pull, remote.add_remote, remote.remove_remote):
            self.assertTrue(hasattr(fn, "operation_name"), fn.__name__)
        self.assertEqual(engine.write_op_for("/api/not-listed", {}), ("other", "names"))  # 未登録は安全側 (作業ツリーも管理外ファイルの名前も記録)

    def test_route_modules_that_use_the_engine_do_not_run_git_themselves(self):
        """route のモジュールは、git を直接実行しない (書き込みは engine、読み取りは query に置く)。"""
        package = Path(engine.__file__).parent.parent
        forbidden = ("subprocess", "run_git_write", "run_git_net", "git_c_locale", "gitrun")
        for name in ROUTE_ONLY_MODULES:
            text = (package / name).read_text(encoding="utf-8")
            used = [word for word in forbidden if word in text]
            self.assertEqual(used, [], f"{name} は git を直接実行している")

    def test_every_operation_has_a_history_label(self):
        for name in engine.OPERATIONS:
            self.assertIn(name, engine.LABEL_KEYS)


ROUTE_ONLY_MODULES = ["refops.py", "remote.py", "rebase.py", "history.py", "wip.py", "files.py"]  # engine に移し終えて、route だけになったモジュール (移すたびに足す)


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
        self.assertEqual(os.path.dirname(env["GIT_OBJECT_DIRECTORY"]), tmpdir.instance_dir())  # インスタンスの一時ディレクトリの中

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


class EngineOperationTests(EngineTests):
    """engine の操作 (git_graph.engine.refs / worktree) を、HTTP を通さずに直接呼ぶ。"""

    def entries(self):
        return engine._load()["entries"]

    def test_operation_records_itself_and_can_be_undone(self):
        from git_graph.engine import refs
        result = refs.create_branch(self.head, "feature")

        self.assertEqual(result, {"name": "feature", "checked_out": False, "stashed": None})
        self.assertEqual([(e["op"], e["detail"]) for e in self.entries()], [("branch", "feature")])
        self.assertTrue(self.undo()["ok"])
        self.assertEqual(self.git("branch", "--list", "feature").strip(), "")

    def test_operation_detail_uses_short_hash(self):
        from git_graph.engine import refs
        second = self.commit_file("two.txt", "two\n", "second")

        refs.reset(self.head, "hard")

        self.assertEqual([e["detail"] for e in self.entries()], [self.head[:7]])
        self.assertNotEqual(self.git("rev-parse", "HEAD").strip(), second)

    def test_nested_operations_are_recorded_as_one_entry(self):
        from git_graph.engine import refs
        saved = dict(engine.OPERATIONS)
        try:
            outer = engine.operation("tag", touches_worktree=False, detail=lambda *_: "outer")(
                lambda: (refs.create_branch(self.head, "inner-branch"), refs.create_tag(self.head, "inner-tag")))
            outer()
        finally:
            engine.OPERATIONS.clear()
            engine.OPERATIONS.update(saved)

        self.assertEqual([(e["op"], e["detail"]) for e in self.entries()], [("tag", "outer")])
        self.assertTrue(self.undo()["ok"])  # 1回の取り消しで両方戻る
        self.assertEqual(self.git("branch", "--list", "inner-branch").strip(), "")
        self.assertEqual(self.git("tag", "--list", "inner-tag").strip(), "")

    def test_failed_operation_still_raises_and_records_partial_changes_only_when_something_changed(self):
        from git_graph.engine import refs
        with self.assertRaises(UserError):
            refs.create_branch(self.head, "bad name with spaces")
        self.assertEqual(self.entries(), [])

    def test_operation_without_label_is_rejected(self):
        with self.assertRaises(ValueError):
            engine.operation("no_such_operation", touches_worktree=False)(lambda: None)

    def test_no_undo_option_runs_the_operation_without_recording(self):
        from git_graph.engine import refs
        class Opts:
            no_undo = True
        saved, state.OPTS = getattr(state, "OPTS", None), Opts()
        try:
            refs.create_tag(self.head, "quiet")
        finally:
            state.OPTS = saved
        self.assertEqual(self.entries(), [])
        self.assertEqual(self.git("tag", "--list", "quiet").strip(), "quiet")

    def test_commit_operation_commits_checked_files_and_records_history(self):
        from git_graph.engine import commit
        self.write("a.txt", "a\n")
        self.write("b.txt", "b\n")

        result = commit.commit("add a", ["a.txt"])

        self.assertEqual(result["amend"], False)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["hash"], self.git("rev-parse", "HEAD").strip())
        self.assertEqual(self.git("show", "--name-only", "--format=", "HEAD").strip(), "a.txt")
        self.assertEqual([e["op"] for e in self.entries()], ["commit"])
        self.assertTrue(self.undo()["ok"])
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.head)

    def test_commit_operation_to_new_branch_records_branch_name(self):
        from git_graph.engine import commit
        self.write("a.txt", "a\n")

        commit.commit("on feature", ["a.txt"], branch="feature")

        self.assertEqual(self.git("branch", "--show-current").strip(), "feature")
        self.assertEqual([e["detail"] for e in self.entries()], ["feature"])

    def test_commit_operation_amend_reports_dropped_files(self):
        from git_graph.engine import commit
        self.write("one.txt", "1\n")
        self.write("two.txt", "2\n")
        self.git("add", "one.txt", "two.txt")
        self.git("commit", "-q", "-m", "add both")

        result = commit.commit("only one", ["one.txt"], amend=True)  # two.txt はチェックを外す = コミットから外す

        self.assertEqual((result["amend"], result["count"], result["dropped"]), (True, 1, 1))
        self.assertEqual(self.git("show", "--name-only", "--format=", "HEAD").strip(), "one.txt")
        self.assertEqual(self.git("log", "-1", "--format=%s").strip(), "only one")

    def test_commit_operation_validates_before_touching_anything(self):
        from git_graph.engine import commit
        self.write("a.txt", "a\n")
        for kwargs, args in (({}, ("", ["a.txt"])), ({}, ("m", [])), ({}, ("m", "a.txt")), ({"amend": True, "branch": "x"}, ("m", ["a.txt"]))):
            with self.assertRaises(UserError):
                commit.commit(*args, **kwargs)
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.head)
        self.assertEqual(self.entries(), [])

    def make_bare_remote(self):
        bare = tempfile.mkdtemp(prefix="git_graph_test_bare_")
        self.addCleanup(shutil.rmtree, bare, True)
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", bare], check=True)
        return bare

    def clone_of(self, bare):
        path = tempfile.mkdtemp(prefix="git_graph_test_clone_")
        self.addCleanup(shutil.rmtree, path, True)
        subprocess.run(["git", "clone", "-q", bare, path], check=True, capture_output=True)
        for k, v in (("user.name", "Other"), ("user.email", "other@example.com")):
            subprocess.run(["git", "-C", path, "config", k, v], check=True)
        return path

    def test_add_remote_is_recorded_and_undoable(self):
        from git_graph.engine import remote
        bare = self.make_bare_remote()

        result = remote.add_remote("origin", bare, fetch=True)

        self.assertEqual((result["name"], result["fetched"]), ("origin", True))
        self.assertEqual([(e["op"], e["detail"]) for e in self.entries()], [("remote_add", "origin")])
        self.assertTrue(self.undo()["ok"])
        self.assertEqual(self.git("remote").strip(), "")

    def test_add_remote_validates_and_remove_remote_only_removes_registered_names(self):
        from git_graph.engine import remote
        for name, url in (("-bad", "x"), ("ok", ""), ("ok", "ext::sh -c id"), ("ok", "-oops")):
            with self.assertRaises(UserError):
                remote.add_remote(name, url)
        with self.assertRaises(UserError):
            remote.remove_remote("nope")
        self.assertEqual(self.entries(), [])

    def test_push_fetch_and_pull_round_trip_with_a_bare_remote(self):
        from git_graph.engine import remote
        bare = self.make_bare_remote()
        remote.add_remote("origin", bare)

        needs = remote.push()
        self.assertTrue(needs["needs_upstream"])
        pushed = remote.push(set_upstream=True)
        self.assertEqual((pushed["branch"], pushed["remote"], pushed["changed"]), ("main", "origin", True))
        self.assertFalse(remote.push()["changed"])  # もう送るものがない

        other = self.clone_of(bare)
        subprocess.run(["git", "-C", other, "commit", "--allow-empty", "-q", "-m", "from other"], check=True)
        subprocess.run(["git", "-C", other, "push", "-q", "origin", "HEAD:main"], check=True, capture_output=True)

        fetched = remote.fetch()
        self.assertEqual(fetched["changed"], 1)
        pulled = remote.pull()
        self.assertEqual((pulled["branch"], pulled["up"], pulled["updated"]), ("main", "origin/main", True))
        self.assertEqual(self.git("log", "-1", "--format=%s").strip(), "from other")
        self.assertFalse(remote.pull()["updated"])
        ops = [e["op"] for e in self.entries()]
        self.assertIn("fetch", ops)
        self.assertIn("pull", ops)
        self.assertNotIn("push", ops)  # push はリモート側を変えるので記録しない

    def test_pull_conflict_keeps_merge_state_and_reports_it(self):
        from git_graph.engine import remote
        bare = self.make_bare_remote()
        remote.add_remote("origin", bare)
        remote.push(set_upstream=True)
        other = self.clone_of(bare)
        with open(os.path.join(other, "tracked.txt"), "w", encoding="utf-8") as f:
            f.write("remote side\n")
        subprocess.run(["git", "-C", other, "commit", "-aq", "-m", "remote change"], check=True)
        subprocess.run(["git", "-C", other, "push", "-q", "origin", "HEAD:main"], check=True, capture_output=True)
        self.commit_file("tracked.txt", "local side\n", "local change")

        result = remote.pull(mode="merge")

        self.assertTrue(result["conflict"])
        self.assertEqual(result["up"], "origin/main")
        self.assertTrue(os.path.exists(os.path.join(self.repo, ".git", "MERGE_HEAD")))

    def test_push_tag_needs_a_remote_when_ambiguous(self):
        from git_graph.engine import remote
        a, b = self.make_bare_remote(), self.make_bare_remote()
        remote.add_remote("origin", a)
        remote.add_remote("backup", b)
        self.git("tag", "v1")

        ask = remote.push_tag("v1")
        self.assertEqual((ask["needs_remote"], ask["remote"], sorted(ask["remotes"])), (True, "origin", ["backup", "origin"]))
        done = remote.push_tag("v1", remote="backup")

        self.assertEqual((done["remote"], done["up_to_date"]), ("backup", False))
        self.assertEqual(subprocess.run(["git", "-C", b, "tag", "--list"], capture_output=True, text=True).stdout.strip(), "v1")
        self.assertTrue(remote.push_tag("v1", remote="backup")["up_to_date"])
        with self.assertRaises(UserError):
            remote.push_tag("-x")
        with self.assertRaises(UserError):
            remote.push_tag("missing", remote="origin")

    def test_auth_retry_semantics(self):
        from git_graph.engine.remote import Auth
        auth = Auth({"username": "u", "password": "p"}, "origin", True)
        self.assertTrue(auth.retrying_for("origin"))
        self.assertFalse(auth.retrying_for("backup"))
        self.assertFalse(Auth().retrying_for("origin"))
        self.assertEqual(auth.for_remote("origin"), {"username": "u", "password": "p"})  # 明示された認証情報を優先する

    def patch_for(self, name, content, message):
        """別のコミットとして作って format-patch に出し、元に戻した状態のパッチ (bytes) を返す。"""
        self.commit_file(name, content, message)
        raw = subprocess.run(["git", "-C", self.repo, "format-patch", "-1", "--stdout"], check=True, capture_output=True).stdout
        self.git("reset", "-q", "--hard", "HEAD~1")
        return raw

    def test_apply_patch_changes_the_worktree_only_and_is_recorded(self):
        from git_graph.engine import patch
        original = self.read("tracked.txt")
        raw = self.patch_for("tracked.txt", "patched: a much longer line than the original\n", "to apply")  # 元と同じ長さだと、同じ秒の変更を git の stat が見落とすことがある

        result = patch.apply_patch(raw)

        self.assertEqual(result, {"files": 1})
        self.assertEqual(self.read("tracked.txt"), "patched: a much longer line than the original\n")
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.head)  # コミットもステージもしない
        self.assertEqual(self.git("diff", "--cached", "--name-only").strip(), "")
        self.assertEqual([e["op"] for e in self.entries()], ["patch_apply"])
        self.assertTrue(self.undo()["ok"])
        self.assertEqual(self.read("tracked.txt"), original)

    def test_am_patch_adds_commits_and_a_failure_leaves_nothing_behind(self):
        from git_graph.engine import patch
        raw = self.patch_for("amed.txt", "amed\n", "to am")

        result = patch.am_patch(raw)

        self.assertEqual(result, {"commits": 1})
        self.assertEqual(self.git("log", "-1", "--format=%s").strip(), "to am")
        self.assertEqual([e["op"] for e in self.entries()], ["patch_am"])
        head = self.git("rev-parse", "HEAD").strip()
        with self.assertRaises(ValueError):
            patch.am_patch(raw)  # もう適用済みなので失敗する
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), head)
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".git", "rebase-apply")))

    def test_patch_operations_reject_empty_input_and_am_refuses_during_another_operation(self):
        from git_graph.engine import patch
        for fn in (patch.apply_patch, patch.am_patch):
            with self.assertRaises(UserError):
                fn(b"  \n")
        busy = os.path.join(self.repo, ".git", "rebase-apply")
        os.makedirs(busy)  # 別の rebase / am の途中。am --abort がこれを壊してはいけない
        with self.assertRaises(UserError):
            patch.am_patch(b"From x\n")
        self.assertTrue(os.path.isdir(busy))

    def diverged_branch(self, name="other", content="other side\n"):
        """main から分かれて tracked.txt を変えたブランチを作って、main に戻る。"""
        self.git("checkout", "-q", "-b", name)
        tip = self.commit_file("tracked.txt", content, f"{name} change")
        self.git("checkout", "-q", "main")
        return tip

    def test_merge_reports_updated_uptodate_and_conflict(self):
        from git_graph.engine import integrate
        side = self.diverged_branch("side")
        merged = integrate.merge(side)
        self.assertEqual((merged["hash"], merged["updated"]), (side, True))
        self.assertEqual(self.entries()[-1]["op"], "merge")
        self.assertFalse(integrate.merge(side)["updated"])  # もう取り込み済み

        self.git("reset", "-q", "--hard", self.head)
        self.commit_file("tracked.txt", "main side\n", "main change")
        clash = integrate.merge(side)
        self.assertTrue(clash["conflict"])
        self.assertTrue(os.path.exists(os.path.join(self.repo, ".git", "MERGE_HEAD")))  # マージ途中で止まる
        with self.assertRaises(UserError):
            integrate.merge(side)  # マージ中は、さらにマージできない

    def test_abort_operation_returns_the_aborted_command(self):
        from git_graph.engine import integrate
        side = self.diverged_branch("side")
        self.commit_file("tracked.txt", "main side\n", "main change")
        integrate.merge(side)

        self.assertEqual(integrate.abort_operation(), {"cmd": "merge"})
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".git", "MERGE_HEAD")))
        with self.assertRaises(UserError):
            integrate.abort_operation()

    def test_resolve_conflicts_takes_one_side_and_rejects_non_conflicts(self):
        from git_graph.engine import integrate
        side = self.diverged_branch("side", "their side\n")
        self.commit_file("tracked.txt", "our side\n", "main change")
        integrate.merge(side)

        with self.assertRaises(UserError):
            integrate.resolve_conflicts("middle", ["tracked.txt"])
        with self.assertRaises(UserError):
            integrate.resolve_conflicts("theirs", ["not-a-conflict.txt"])
        result = integrate.resolve_conflicts("theirs", ["tracked.txt"])

        self.assertEqual(result, {"count": 1, "side": "theirs"})
        self.assertEqual(self.read("tracked.txt"), "their side\n")
        self.assertEqual(self.git("diff", "--name-only", "--diff-filter=U").strip(), "")

    def test_cherry_pick_and_revert_report_what_happened(self):
        from git_graph.engine import integrate
        picked = self.diverged_branch("pick", "picked\n")

        result = integrate.cherry_pick(picked)
        self.assertEqual((result["noop"], self.read("tracked.txt")), (False, "picked\n"))
        with self.assertRaises(UserError):
            integrate.cherry_pick(picked)  # 変更が空になる (既に取り込み済み): 中止して元に戻す
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".git", "CHERRY_PICK_HEAD")))

        head = self.git("rev-parse", "HEAD").strip()
        undone = integrate.revert(head)
        self.assertEqual((undone["merge"], self.read("tracked.txt") != "picked\n"), (False, True))
        self.assertEqual([e["op"] for e in self.entries()], ["cherry_pick", "revert"])

    def test_integration_operations_stop_when_a_local_edit_blocks_them(self):
        from git_graph.engine import integrate
        side = self.diverged_branch("side")
        self.write("tracked.txt", "local edit\n")

        blocked = integrate.merge(side)

        self.assertTrue(blocked["blocked"])
        self.assertEqual(self.read("tracked.txt"), "local edit\n")  # 何も壊さない

    def topic_on_old_base(self):
        """base から分かれた topic (2コミット) と、その後に進んだ main を作って topic に切り替える。"""
        base = self.head
        self.git("checkout", "-q", "-b", "topic")
        first = self.commit_file("t1.txt", "one\n", "topic one")
        second = self.commit_file("t2.txt", "two\n", "topic two")
        self.git("checkout", "-q", "main")
        target = self.commit_file("target.txt", "target\n", "target change")
        self.git("checkout", "-q", "topic")
        return base, first, second, target

    def test_rebase_operations_return_facts_and_record_one_entry_each(self):
        from git_graph.engine import rebase
        base, first, second, target = self.topic_on_old_base()

        done = rebase.rebase(target)
        self.assertEqual((done["branch"], done["hash"], done["noop"]), ("topic", target, False))
        self.assertEqual(self.git("rev-parse", "HEAD~2").strip(), target)
        self.assertTrue(rebase.rebase(target)["noop"])  # もう載っている

        self.assertEqual([e["op"] for e in self.entries()], ["rebase"])  # noop は状態が変わらないので記録されない

    def test_rebase_range_returns_moved_tag_count_and_noop_flag(self):
        from git_graph.engine import rebase
        base, first, second, target = self.topic_on_old_base()
        self.git("tag", "mid", first)

        result = rebase.rebase_range(base, target)

        self.assertEqual((result["branch"], result["from"], result["to"], result["noop"], result["moved_tags"]),
                         ("topic", base, target, False, 1))
        self.assertEqual(self.git("rev-parse", "mid^{commit}").strip(), self.git("rev-parse", "HEAD~1").strip())

    def test_rebase_range_ignores_untracked_files_that_nothing_will_overwrite(self):
        from git_graph.engine import rebase
        base, first, second, target = self.topic_on_old_base()
        self.write("scratch.txt", "unrelated\n")  # 付け替えで作られるどのパスとも重ならない管理外ファイル
        os.makedirs(os.path.join(self.repo, "notes"))
        self.write("notes/todo.txt", "unrelated too\n")

        result = rebase.rebase_range(base, target)

        self.assertNotIn("blocked", result)
        self.assertEqual(self.git("rev-parse", "HEAD~2").strip(), target)
        self.assertEqual(self.read("scratch.txt"), "unrelated\n")  # 管理外ファイルはそのまま
        self.assertEqual(self.read("notes/todo.txt"), "unrelated too\n")

    def test_rebase_range_blocks_only_on_untracked_files_it_would_overwrite(self):
        from git_graph.engine import rebase
        base, first, second, target = self.topic_on_old_base()
        self.write("scratch.txt", "unrelated\n")
        self.write("target.txt", "mine\n")  # B (target) が追加するファイルと同名

        blocked = rebase.rebase_range(base, target)

        self.assertTrue(blocked["blocked"])
        self.assertEqual(blocked["untracked"], ["target.txt"])  # scratch.txt は含まれない
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), second)  # まだ何も変わっていない

        done = rebase.rebase_range(base, target, on_conflict="discard")  # 止めているファイルだけ破棄して進む

        self.assertNotIn("blocked", done)
        self.assertEqual(self.read("target.txt"), "target\n")  # B の内容になった (管理外の同名ファイルは破棄された)
        self.assertEqual(self.read("scratch.txt"), "unrelated\n")  # 関係ないファイルは残る
        self.assertEqual(self.git("rev-parse", "HEAD~2").strip(), target)

    def test_rebase_descendants_ignores_unrelated_untracked_files_but_blocks_on_overwritten_ones(self):
        from git_graph.engine import rebase
        base, first, second, target = self.topic_on_old_base()
        self.write("scratch.txt", "unrelated\n")
        self.write("target.txt", "mine\n")
        blocked = rebase.rebase_descendants(base, target)
        self.assertEqual(blocked["untracked"], ["target.txt"])
        os.remove(os.path.join(self.repo, "target.txt"))

        moved = rebase.rebase_descendants(base, target)

        self.assertNotIn("blocked", moved)
        self.assertEqual(self.read("scratch.txt"), "unrelated\n")

    def test_rebase_descendants_blocks_on_files_added_by_other_branches_too(self):
        from git_graph.engine import rebase
        base, first, second, target = self.topic_on_old_base()  # HEAD は topic
        self.git("checkout", "-q", "-b", "side", base)
        self.commit_file("side.txt", "side\n", "side adds a file")
        self.git("checkout", "-q", "topic")  # side.txt は topic では管理外 (side ブランチだけが持つ)
        self.write("side.txt", "mine\n")
        self.write("scratch.txt", "unrelated\n")

        blocked = rebase.rebase_descendants(base, target)

        self.assertEqual(blocked["untracked"], ["side.txt"])  # HEAD 以外のブランチが追加するファイルとの衝突も拾う
        done = rebase.rebase_descendants(base, target, on_conflict="discard")

        self.assertNotIn("blocked", done)
        self.assertEqual(done["branches"], 2)
        self.assertEqual(self.read("scratch.txt"), "unrelated\n")
        self.assertEqual(self.git("rev-parse", "side~1").strip(), target)

    def test_rebase_range_still_blocks_on_edited_tracked_files(self):
        from git_graph.engine import rebase
        base, first, second, target = self.topic_on_old_base()
        self.write("t1.txt", "edited\n")

        blocked = rebase.rebase_range(base, target)

        self.assertEqual(blocked["modified"], ["t1.txt"])

    def test_untracked_file_in_the_way_of_a_directory_is_detected(self):
        from git_graph.engine import rebase
        base = self.head
        self.git("checkout", "-q", "-b", "topic")
        self.commit_file("t1.txt", "one\n", "topic one")
        self.git("checkout", "-q", "main")
        os.makedirs(os.path.join(self.repo, "lib"))
        target = self.commit_file("lib/mod.txt", "target\n", "target adds lib/")
        self.git("checkout", "-q", "topic")
        self.write("lib", "a file where B has a directory\n")  # 管理外のファイル lib と、B のディレクトリ lib/ が衝突する

        blocked = rebase.rebase_range(base, target)

        self.assertEqual(blocked["untracked"], ["lib"])

    def test_rebase_conflict_is_aborted_and_reported_as_an_error(self):
        from git_graph.engine import rebase
        self.git("checkout", "-q", "-b", "topic")
        self.commit_file("tracked.txt", "topic side\n", "topic change")
        self.git("checkout", "-q", "main")
        target = self.commit_file("tracked.txt", "main side\n", "main change")
        self.git("checkout", "-q", "topic")
        before = self.git("rev-parse", "HEAD").strip()

        with self.assertRaises(UserError):
            rebase.rebase(target)

        self.assertEqual(self.git("rev-parse", "HEAD").strip(), before)  # 元に戻っている
        self.assertFalse(os.path.isdir(os.path.join(self.repo, ".git", "rebase-merge")))

    def test_rebase_descendants_and_equivalent_noop_results(self):
        from git_graph.engine import rebase
        base, first, second, target = self.topic_on_old_base()

        same = rebase.rebase_descendants(base, base)
        self.assertEqual((same["noop"], same["branches"], same["moved_tags"]), (True, 0, 0))
        moved = rebase.rebase_descendants(base, target)
        self.assertEqual((moved["noop"], moved["branches"]), (False, 1))

        self.assertTrue(rebase.rebase_equivalent(second, second)["noop"])

    def test_rebase_operations_refuse_detached_head_and_missing_worktree(self):
        from git_graph.engine import rebase
        base, first, second, target = self.topic_on_old_base()
        self.git("checkout", "-q", "--detach")
        for call in (lambda: rebase.rebase(target), lambda: rebase.rebase_range(base, target),
                     lambda: rebase.rebase_descendants(base, target), lambda: rebase.rebase_equivalent(second, target)):
            with self.assertRaises(UserError):
                call()

    def todo_for(self, hashes, actions, messages=None):
        return [{"action": a, "hash": h, "message": (messages or {}).get(h, "")} for h, a in zip(hashes, actions)]

    def test_rebase_todo_lists_commits_and_interactive_rebase_squashes_and_rewords(self):
        from git_graph.engine import rebase
        base, first, second, target = self.topic_on_old_base()
        third = self.commit_file("t3.txt", "three\n", "topic three")

        listing = rebase.rebase_todo(base)
        self.assertEqual((listing["branch"], [c["hash"] for c in listing["commits"]]), ("topic", [first, second, third]))

        todo = self.todo_for([first, second, third], ["pick", "squash", "reword"], {third: "reworded three"})
        result = rebase.rebase_interactive(base, todo)

        self.assertEqual((result["branch"], result["noop"], result["counts"]), ("topic", False, {"pick": 1, "squash": 1, "reword": 1}))
        self.assertEqual(self.git("log", "--format=%s", f"{base}..HEAD").split("\n")[:2], ["reworded three", "topic one"])
        self.assertEqual(self.git("rev-list", "--count", f"{base}..HEAD").strip(), "2")
        self.assertEqual([(e["op"], e["detail"]) for e in self.entries()], [("rebase_i", base[:7])])

    def test_interactive_rebase_validates_the_todo_before_running_git(self):
        from git_graph.engine import rebase
        base, first, second, target = self.topic_on_old_base()
        head = self.git("rev-parse", "HEAD").strip()
        bad_todos = [
            [],
            "pick",
            [{"action": "pick", "hash": "abc"}],
            self.todo_for([first, second], ["pick", "explode"]),
            self.todo_for([first, second], ["pick", "reword"]),            # reword はメッセージ必須
            self.todo_for([first], ["pick"]),                              # 対象のコミットが足りない
            self.todo_for([first, second], ["squash", "pick"]),            # 先頭は squash / fixup にできない
        ]
        for todo in bad_todos:
            with self.assertRaises(UserError):
                rebase.rebase_interactive(base, todo)
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), head)
        self.assertEqual(self.entries(), [])

    def test_interactive_rebase_reports_noop_and_aborts_on_conflict(self):
        from git_graph.engine import rebase
        base, first, second, target = self.topic_on_old_base()
        same = rebase.rebase_interactive(base, self.todo_for([first, second], ["pick", "pick"]))
        self.assertTrue(same["noop"])

        self.git("checkout", "-q", "-b", "clash", base)
        a = self.commit_file("tracked.txt", "first edit\n", "edit 1")
        b = self.commit_file("tracked.txt", "second edit\n", "edit 2")
        before = self.git("rev-parse", "HEAD").strip()
        with self.assertRaises(UserError):  # 順序を入れ替えると競合する → 中止して元に戻す
            rebase.rebase_interactive(base, self.todo_for([b, a], ["pick", "pick"]))
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), before)
        self.assertFalse(os.path.isdir(os.path.join(self.repo, ".git", "rebase-merge")))

    def conflicting_stash(self, with_new_file=False):
        """tracked.txt を変えた stash を作り、HEAD 側でも tracked.txt を変えて、stash の適用が競合する状態にする。"""
        self.write("tracked.txt", "stashed edit\n")
        if with_new_file:
            self.write("brand-new.txt", "new in stash\n")
            self.git("add", "brand-new.txt")
        self.git("stash", "push", "-q", "--include-untracked" if not with_new_file else "--keep-index")
        if with_new_file:
            self.git("reset", "-q", "--hard", "HEAD")
        self.commit_file("tracked.txt", "head side edit\n", "head change")
        return self.git("rev-parse", "stash@{0}").strip()

    def test_stash_pop_conflict_can_be_aborted_back_to_the_state_before(self):
        from git_graph.engine import integrate, worktree
        from git_graph.gitstate import stash_conflict_state
        stash = self.conflicting_stash()
        before_head = self.git("rev-parse", "HEAD").strip()

        with self.assertRaises(ValueError):
            worktree.stash_apply(stash, "pop")

        self.assertEqual(stash_conflict_state()["stash"], stash)  # 競合が残っていて、中断できる状態
        self.assertIn("<<<<<<<", self.read("tracked.txt"))
        self.assertEqual(self.git("stash", "list", "--format=%H").strip(), stash)  # pop が競合したので stash は残っている

        result = integrate.abort_operation()

        self.assertEqual(result, {"cmd": "stash"})
        self.assertEqual(self.read("tracked.txt"), "head side edit\n")  # HEAD の内容に戻っている
        self.assertEqual(self.git("status", "--porcelain").strip(), "")  # 競合の印も残らない
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), before_head)
        self.assertEqual(self.git("stash", "list", "--format=%H").strip(), stash)  # stash はそのまま
        self.assertIsNone(stash_conflict_state())
        with self.assertRaises(UserError):
            integrate.abort_operation()  # もう中断するものはない

    def test_redo_of_a_conflicting_stash_apply_brings_back_the_conflict_state(self):
        from git_graph.engine import integrate, worktree
        from git_graph.gitstate import stash_conflict_state
        stash = self.conflicting_stash()
        with self.assertRaises(ValueError):
            worktree.stash_apply(stash, "apply")
        conflicted = self.git("ls-files", "-u")
        self.assertIn("tracked.txt", conflicted)

        self.assertTrue(self.undo()["ok"])
        self.assertEqual(self.git("status", "--porcelain").strip(), "")  # 適用前に戻った
        self.assertEqual(self.read("tracked.txt"), "head side edit\n")

        self.assertTrue(self.redo()["ok"])

        self.assertEqual(self.git("ls-files", "-u"), conflicted)  # 競合の状態 (base / ours / theirs) が同じに戻る
        self.assertEqual(self.git("status", "--porcelain").strip(), "UU tracked.txt")  # M ではなく競合
        self.assertIn("<<<<<<<", self.read("tracked.txt"))
        self.assertEqual(stash_conflict_state()["stash"], stash)  # WIP の「中断」も使える
        integrate.abort_operation()
        self.assertEqual(self.git("status", "--porcelain").strip(), "")

    def stash_with_new_file(self):
        self.write("brand-new.txt", "new in stash\n")
        self.git("add", "brand-new.txt")
        self.write("tracked.txt", "edit\n")
        self.git("stash", "push", "-q")
        return self.git("rev-parse", "stash@{0}").strip()

    def test_redo_of_stash_apply_runs_it_again_so_the_staged_new_file_stays_staged(self):
        from git_graph.engine import worktree
        stash = self.stash_with_new_file()
        worktree.stash_apply(stash, "apply")
        applied = self.git("status", "--porcelain")
        self.assertIn("A  brand-new.txt", applied)
        entries = len(engine._load()["entries"])

        self.undo()
        self.assertEqual(self.git("status", "--porcelain").strip(), "")
        self.redo()

        self.assertEqual(self.git("status", "--porcelain"), applied)  # ?? ではなく A (ステージ済み) のまま
        self.assertEqual(len(engine._load()["entries"]), entries)  # 再実行は新しい履歴として積まれない
        self.assertEqual(engine._load()["cursor"], entries)

    def test_redo_of_stash_pop_drops_the_stash_again(self):
        from git_graph.engine import worktree
        stash = self.stash_with_new_file()
        worktree.stash_apply(stash, "pop")
        self.assertEqual(self.git("stash", "list").strip(), "")

        self.undo()
        self.assertEqual(self.git("stash", "list", "--format=%H").strip(), stash)  # stash が戻る
        self.redo()

        self.assertEqual(self.git("stash", "list").strip(), "")
        self.assertIn("A  brand-new.txt", self.git("status", "--porcelain"))

    def test_redo_falls_back_to_the_saved_state_when_running_again_gives_a_different_result(self):
        from git_graph.engine import worktree
        stash = self.conflicting_stash()
        with self.assertRaises(ValueError):
            worktree.stash_apply(stash, "apply")
        conflicted = self.git("ls-files", "-u")
        self.undo()
        original = engine.REPLAYABLE["stash"]
        engine.REPLAYABLE["stash"] = lambda *a, **k: None  # 再実行しても何も起きない (結果が記録と違う) 状況にする
        self.addCleanup(engine.REPLAYABLE.__setitem__, "stash", original)

        self.assertTrue(self.redo()["ok"])

        self.assertEqual(self.git("ls-files", "-u"), conflicted)  # 退避からの復元で、競合の状態まで合わせる
        self.assertIn("<<<<<<<", self.read("tracked.txt"))

    def test_entry_without_replay_info_still_redoes_from_the_saved_state(self):
        from git_graph.engine import worktree
        stash = self.stash_with_new_file()
        worktree.stash_apply(stash, "apply")
        for entry in engine._load()["entries"]:
            entry.pop("replay", None)  # 引数を記録できなかった操作と同じ扱い
        self.undo()
        self.assertTrue(self.redo()["ok"])
        self.assertEqual(self.read("tracked.txt"), "edit\n")

    def test_undo_redo_cycle_of_a_conflicting_stash_apply_is_stable(self):
        from git_graph.engine import worktree
        stash = self.conflicting_stash()
        with self.assertRaises(ValueError):
            worktree.stash_apply(stash, "apply")
        conflicted = self.git("ls-files", "-u")
        for _ in range(2):
            self.assertTrue(self.undo()["ok"])
            self.assertEqual(self.git("ls-files", "-u"), "")  # 元に戻すと競合の印も消える
            self.assertTrue(self.redo()["ok"])
            self.assertEqual(self.git("ls-files", "-u"), conflicted)

    def test_redo_restores_a_modify_delete_conflict_too(self):
        from git_graph.engine import worktree
        self.write("tracked.txt", "stashed edit\n")
        self.git("stash", "push", "-q")
        self.git("rm", "-q", "tracked.txt")
        self.git("commit", "-q", "-m", "delete tracked.txt")  # HEAD 側は削除、stash 側は変更
        stash = self.git("rev-parse", "stash@{0}").strip()
        with self.assertRaises(ValueError):
            worktree.stash_apply(stash, "apply")
        status = self.git("status", "--porcelain").strip()
        self.assertTrue(status.startswith("UD") or status.startswith("DU"), status)

        self.undo()
        self.assertEqual(self.git("status", "--porcelain").strip(), "")
        self.redo()

        self.assertEqual(self.git("status", "--porcelain").strip(), status)

    def test_abort_stash_conflict_removes_files_the_stash_added(self):
        from git_graph.engine import integrate, worktree
        stash = self.conflicting_stash(with_new_file=True)

        with self.assertRaises(ValueError):
            worktree.stash_apply(stash, "apply")
        self.assertTrue(os.path.exists(os.path.join(self.repo, "brand-new.txt")))  # stash が作ったファイル

        integrate.abort_operation()

        self.assertFalse(os.path.exists(os.path.join(self.repo, "brand-new.txt")))
        self.assertEqual(self.git("status", "--porcelain").strip(), "")

    def test_stash_conflict_marker_is_ignored_once_conflicts_are_resolved(self):
        from git_graph.engine import integrate, worktree
        from git_graph.gitstate import stash_conflict_state
        stash = self.conflicting_stash()
        with self.assertRaises(ValueError):
            worktree.stash_apply(stash, "apply")
        integrate.resolve_conflicts("ours", ["tracked.txt"])  # 競合を解決した

        self.assertIsNone(stash_conflict_state())  # 目印が残っていても、未解決の競合がなければ中断の対象にならない
        with self.assertRaises(UserError):
            integrate.abort_operation()

    def test_stash_conflict_marker_is_kept_in_memory_and_writes_no_file(self):
        from git_graph import gitstate
        from git_graph.engine import integrate, worktree
        legacy = gitstate.git_path(gitstate.STASH_MARKER)
        with open(legacy, "w", encoding="utf-8") as f:  # 以前の版が .git に残した目印
            f.write("{}")
        stash = self.conflicting_stash()

        with self.assertRaises(ValueError):
            worktree.stash_apply(stash, "apply")

        self.assertEqual(gitstate.stash_conflict_state(), {"stash": stash, "action": "apply"})
        self.assertFalse(os.path.exists(legacy))  # 古い版の目印は片付く
        self.assertFalse([n for n in os.listdir(os.path.join(self.repo, ".git")) if "git_graph" in n])  # .git に目印のファイルは作らない
        integrate.abort_operation()
        self.assertIsNone(gitstate.stash_conflict_state())  # 中断すると目印も消える

    def test_stash_conflict_marker_is_per_repository_and_ignored_once_resolved(self):
        from git_graph import gitstate
        gitstate.set_stash_marker("a" * 40, "pop")
        self.addCleanup(gitstate.clear_stash_marker)
        self.assertIsNone(gitstate.stash_conflict_state())  # 未解決の競合が無ければ、古い目印として無視する
        old = state.REPO
        state.REPO = os.path.join(self.repo, "other-repo-path")
        try:
            self.assertIsNone(gitstate.stash_conflict_state())
            self.assertNotIn(state.REPO, gitstate._STASH_MARKERS)  # 別のリポジトリの目印は見えない
        finally:
            state.REPO = old

    def test_wip_status_reports_stash_conflict(self):
        from git_graph.engine import worktree
        stash = self.conflicting_stash()
        self.assertFalse(ROUTES[("GET", "/api/status")]({})["stash_conflict"])
        with self.assertRaises(ValueError):
            worktree.stash_apply(stash, "apply")

        self.assertTrue(ROUTES[("GET", "/api/status")]({})["stash_conflict"])

    def sha_of(self, name):
        import hashlib
        with open(os.path.join(self.repo, name), "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    def test_save_file_writes_records_and_can_be_undone(self):
        from git_graph.engine import files
        original = self.read("tracked.txt")

        result = files.save_file("tracked.txt", "edited in the app\n", self.sha_of("tracked.txt"))

        self.assertEqual((result["path"], result["unchanged"]), ("tracked.txt", False))
        self.assertEqual(result["sha"], self.sha_of("tracked.txt"))
        self.assertEqual(self.read("tracked.txt"), "edited in the app\n")
        self.assertEqual([e["op"] for e in self.entries()], ["file_save"])
        self.assertTrue(self.undo()["ok"])
        self.assertEqual(self.read("tracked.txt"), original)

    def test_save_file_keeps_crlf_and_reports_unchanged_without_recording(self):
        from git_graph.engine import files
        with open(os.path.join(self.repo, "crlf.txt"), "wb") as f:
            f.write(b"one\r\ntwo\r\n")
        sha = self.sha_of("crlf.txt")

        same = files.save_file("crlf.txt", "one\ntwo\n", sha)  # 改行コードは元に合わせて戻すので、内容は変わらない
        self.assertEqual((same["unchanged"], same["sha"]), (True, sha))
        self.assertEqual(self.entries(), [])

        files.save_file("crlf.txt", "one\nthree\n", sha)
        with open(os.path.join(self.repo, "crlf.txt"), "rb") as f:
            self.assertEqual(f.read(), b"one\r\nthree\r\n")

    def test_save_file_refuses_stale_or_invalid_requests_and_leaves_the_file_alone(self):
        from git_graph.engine import files
        before = self.read("tracked.txt")
        stale = "0" * 64
        for args, error in (
            (("tracked.txt", "x\n", stale), UserError),             # 開いた後に別の場所で変更された
            (("missing.txt", "x\n", stale), UserError),             # ファイルでない
            (("tracked.txt", None, stale), ValueError),              # 不正な本文
            (("../outside.txt", "x\n", stale), ValueError),         # リポジトリの外
        ):
            with self.assertRaises(error):
                files.save_file(*args)
        self.assertEqual(self.read("tracked.txt"), before)
        self.assertEqual(self.entries(), [])
        self.assertEqual([n for n in os.listdir(self.repo) if n.startswith(".git_graph_edit_")], [])  # 一時ファイルも残らない

    def test_blocked_checkout_is_returned_not_raised(self):
        from git_graph.engine import refs
        self.git("checkout", "-q", "-b", "other")
        self.commit_file("tracked.txt", "other side\n", "other change")
        self.git("checkout", "-q", "main")
        self.write("tracked.txt", "local edit\n")

        result = refs.checkout("other", branch=True)

        self.assertTrue(result["blocked"])
        self.assertEqual(result["modified"], ["tracked.txt"])
        self.assertEqual(self.git("branch", "--show-current").strip(), "main")


if __name__ == "__main__":
    unittest.main()
