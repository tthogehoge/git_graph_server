# pygit2 版の読み取り (libgit) の回帰テスト。
import os
import subprocess
import tempfile
import types
import unittest

from git_graph import app_state as state
from git_graph import libgit
from git_graph.i18n import UserError
from git_graph.query import graph as qgraph


def run(repo, *args):
    subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True)


@unittest.skipUnless(libgit.available(), "pygit2 is not installed")
class LibgitReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = self.tmp.name
        run(self.repo, "init", "-q", "-b", "main")
        run(self.repo, "config", "user.email", "t@example.com")
        run(self.repo, "config", "user.name", "t")
        self.old_repo = state.REPO
        state.REPO = self.repo
        libgit.reset_cache()

    def tearDown(self):
        state.REPO = self.old_repo
        libgit.reset_cache()
        self.tmp.cleanup()

    def write(self, name, text):
        with open(os.path.join(self.repo, name), "w", encoding="utf-8", newline="") as f:
            f.write(text)

    def test_blame_splits_lines_only_on_newline(self):
        self.write("f.txt", "a\fb\nsecond\n")  # \f は git では行区切りではない
        run(self.repo, "add", "f.txt")
        run(self.repo, "commit", "-qm", "c1")
        lines = libgit.blame_lines("f.txt")
        self.assertEqual([l["line"] for l in lines], [1, 2])
        self.assertEqual(lines[0]["content"], "a\fb")

    def test_blame_decodes_cp932(self):
        with open(os.path.join(self.repo, "j.txt"), "wb") as f:
            f.write("テスト\n".encode("cp932"))
        run(self.repo, "add", "j.txt")
        run(self.repo, "commit", "-qm", "c1")
        self.assertEqual(libgit.blame_lines("j.txt")[0]["content"], "テスト")

    def test_staged_rename_is_one_entry_with_old_path(self):
        self.write("a.txt", "\n".join(str(i) for i in range(50)) + "\n")
        run(self.repo, "add", "a.txt")
        run(self.repo, "commit", "-qm", "c1")
        run(self.repo, "mv", "a.txt", "b.txt")
        staged = libgit.status_lists()["staged"]
        self.assertEqual(staged, [{"path": "b.txt", "old_path": "a.txt", "st": "R"}])

    def test_unrelated_add_and_delete_are_not_paired(self):
        self.write("a.txt", "one\n")
        run(self.repo, "add", "a.txt")
        run(self.repo, "commit", "-qm", "c1")
        run(self.repo, "rm", "-q", "a.txt")
        self.write("b.txt", "completely different\n" * 5)
        run(self.repo, "add", "b.txt")
        staged = {f["path"]: f["st"] for f in libgit.status_lists()["staged"]}
        self.assertEqual(staged, {"a.txt": "D", "b.txt": "A"})

    def test_user_revs_are_strict_and_range_syntax_is_left_to_cli(self):
        self.write("a.txt", "1\n")
        run(self.repo, "add", "a.txt")
        run(self.repo, "commit", "-qm", "c1")
        with self.assertRaises(UserError):
            libgit.rev_list_topo_parents(["no-such-branch"], strict=True)
        self.assertEqual(libgit.rev_list_topo_parents(["no-such-branch"]), [])  # 自動で集めた ref は従来どおり読み飛ばす
        self.assertTrue(libgit.is_plain_rev("main~1"))
        for rev in ("A..B", "^A", "--all", "A^@"):
            self.assertFalse(libgit.is_plain_rev(rev))

    def test_build_model_accepts_a_range_via_cli_and_rejects_a_typo(self):
        self.write("a.txt", "1\n")
        run(self.repo, "add", "a.txt")
        run(self.repo, "commit", "-qm", "c1")
        self.write("a.txt", "2\n")
        run(self.repo, "commit", "-qam", "c2")
        opts = types.SimpleNamespace(revs=["HEAD~1..HEAD"], no_remotes=False, no_tags=False, include_tips=False,
                                     no_stash=True, no_count=False, show_merges=False)
        self.assertEqual(len(qgraph.build_model(opts)["parents"]), 1)
        opts.revs = ["typo"]
        with self.assertRaises(UserError):
            qgraph.build_model(opts)

    def test_status_lists_matches_porcelain_for_a_mixed_working_tree(self):
        """engine は status を「いま変更ありと報告されているパス」の確認に使う。CLI 版 (parse_status) と同じ結果になること。"""
        from git_graph.gitstate import parse_status, read_status_raw
        for name in ("keep.txt", "edit.txt", "gone.txt", "staged_del.txt", "ren.txt"):
            self.write(name, "".join(f"{name} {i}\n" for i in range(30)))
        run(self.repo, "add", "-A")
        run(self.repo, "commit", "-qm", "base")
        run(self.repo, "checkout", "-qb", "side")
        self.write("c.txt", "side\n")
        run(self.repo, "add", "c.txt")
        run(self.repo, "commit", "-qm", "side")
        run(self.repo, "checkout", "-q", "main")
        self.write("c.txt", "main\n")
        run(self.repo, "add", "c.txt")
        run(self.repo, "commit", "-qm", "main")
        subprocess.run(["git", "-C", self.repo, "merge", "side"], capture_output=True)  # c.txt が競合する
        self.write("edit.txt", "staged\n" * 30)
        run(self.repo, "add", "edit.txt")
        self.write("edit.txt", "staged\nunstaged\n" * 15)  # ステージ済み + 未ステージ
        os.remove(os.path.join(self.repo, "gone.txt"))  # 未ステージの削除
        run(self.repo, "rm", "-q", "staged_del.txt")  # ステージ済みの削除
        run(self.repo, "mv", "ren.txt", "renamed.txt")  # ステージ済みのリネーム
        self.write("new.txt", "untracked\n")
        os.makedirs(os.path.join(self.repo, "dir"))
        self.write("dir/deep.txt", "untracked in dir\n")
        mine = libgit.status_lists(limit=10 ** 9)
        cli = parse_status(read_status_raw(), limit=10 ** 9)
        for key in ("staged", "unstaged", "untracked", "conflicts"):
            norm = lambda rows: sorted((f["path"], f["st"], f.get("old_path")) for f in rows)
            self.assertEqual(norm(mine[key]), norm(cli[key]), key)
        self.assertEqual([f["path"] for f in mine["conflicts"]], ["c.txt"])


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(libgit.available(), "pygit2 is not installed")
class GitstateReadParityTests(unittest.TestCase):
    """gitstate の読み取りは pygit2 があれば pygit2、無ければ CLI。両方が同じ答えを返すこと。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = self.tmp.name
        self.remote = os.path.join(base, "remote.git")
        self.repo = os.path.join(base, "repo")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", self.remote], check=True)
        subprocess.run(["git", "init", "-q", "-b", "main", self.repo], check=True)
        for k, v in (("user.email", "t@example.com"), ("user.name", "t")):
            run(self.repo, "config", k, v)
        with open(os.path.join(self.repo, "a.txt"), "w") as f:
            f.write("1\n")
        run(self.repo, "add", "a.txt")
        run(self.repo, "commit", "-qm", "c1")
        run(self.repo, "remote", "add", "origin", self.remote)
        run(self.repo, "push", "-q", "-u", "origin", "main")
        run(self.repo, "tag", "-a", "v1", "-m", "annotated")
        run(self.repo, "tag", "light")
        run(self.repo, "push", "-q", "origin", "v1")
        run(self.repo, "config", "url.https://example.com/.insteadOf", "git@example.com:")
        run(self.repo, "remote", "add", "rw", "git@example.com:x/y.git")
        run(self.repo, "branch", "no-up")
        run(self.repo, "config", "branch.no-up.remote", "origin")
        run(self.repo, "config", "branch.no-up.merge", "refs/heads/no-up")  # upstream の設定はあるが、リモートに ref が無い
        self.old_repo = state.REPO
        state.REPO = self.repo
        libgit.reset_cache()

    def tearDown(self):
        state.REPO = self.old_repo
        libgit.reset_cache()
        self.tmp.cleanup()

    def both(self, fn, *args):
        """(pygit2 の結果, CLI の結果)。"""
        fast = fn(*args)
        real = libgit.available
        libgit.available = lambda: False
        try:
            slow = fn(*args)
        finally:
            libgit.available = real
        return fast, slow

    def test_config_upstream_remotes_and_refs_match_the_cli(self):
        from git_graph import gitstate as gs
        for args in ((gs.git_config, "remote.pushDefault"), (gs.git_config, "user.name"),
                     (gs.branch_config, "main", "remote"), (gs.branch_config, "main", "merge"),
                     (gs.branch_config, "main", "pushRemote"), (gs.branch_config, "no-such", "remote"),
                     (gs.list_remotes,), (gs.refs_snapshot,), (gs.upstream_of_head,)):
            fast, slow = self.both(*args)
            self.assertEqual(fast, slow, args[0].__name__ + str(args[1:]))
        self.assertEqual(gs.upstream_of_head(), "origin/main")
        self.assertEqual([r["name"] for r in gs.list_remotes()], ["origin", "rw"])
        self.assertEqual(dict((r["name"], r["url"]) for r in gs.list_remotes())["rw"], "https://example.com/x/y.git")

    def test_upstream_is_none_when_detached_or_remote_ref_is_missing(self):
        from git_graph import gitstate as gs
        run(self.repo, "checkout", "-q", "no-up")
        self.assertEqual(self.both(gs.upstream_of_head), (None, None))
        run(self.repo, "checkout", "-q", "--detach")
        self.assertEqual(self.both(gs.upstream_of_head), (None, None))

    def test_config_is_not_stale_after_a_cli_write(self):
        from git_graph import gitstate as gs
        self.assertIsNone(gs.git_config("remote.pushDefault"))
        run(self.repo, "config", "remote.pushDefault", "rw")
        self.assertEqual(gs.git_config("remote.pushDefault"), "rw")

    def test_paths_addable_and_amend_changes_match_the_cli(self):
        from git_graph import gitstate as gs
        with open(os.path.join(self.repo, "b.txt"), "w") as f:
            f.write("2\n")
        os.makedirs(os.path.join(self.repo, "d"))
        with open(os.path.join(self.repo, "d", "x.txt"), "w") as f:
            f.write("3\n")
        run(self.repo, "add", "-A")
        run(self.repo, "commit", "-qm", "c2")
        run(self.repo, "rm", "-q", "b.txt")
        paths = ["a.txt", "b.txt", "d", "d/x.txt", "missing.txt"]
        for args in ((gs.paths_in_head, paths), (gs.addable_paths, set(paths)),
                     (gs.amend_head_changes, gs.amend_base())):
            fast, slow = self.both(*args)
            self.assertEqual(fast, slow, args[0].__name__)
        self.assertEqual(gs.paths_in_head(paths), {"a.txt", "b.txt", "d/x.txt"})  # d はディレクトリなので配下のファイルになる

    def test_read_head_in_a_linked_worktree_and_before_the_first_commit(self):
        from git_graph import gitstate as gs
        wt = os.path.join(self.tmp.name, "wt")
        run(self.repo, "worktree", "add", "-q", wt, "-b", "wtb")
        head = subprocess.check_output(["git", "-C", self.repo, "rev-parse", "HEAD"], text=True).strip()
        state.REPO = wt
        libgit.reset_cache()
        gs._git_dir_cache = (None, None)
        self.assertEqual(gs.read_head(), (head, "wtb"))
        empty = os.path.join(self.tmp.name, "empty")
        subprocess.run(["git", "init", "-q", "-b", "trunk", empty], check=True)
        state.REPO = empty
        libgit.reset_cache()
        gs._git_dir_cache = (None, None)
        self.assertEqual(gs.read_head(), ("", "trunk"))  # コミット前でもブランチ名は分かる
        gs._git_dir_cache = (None, None)

    def test_stash_conflict_state_needs_a_marker_and_an_unresolved_conflict(self):
        from git_graph import gitstate as gs
        self.assertFalse(libgit.has_conflicts())
        run(self.repo, "checkout", "-qb", "side")
        with open(os.path.join(self.repo, "a.txt"), "w") as f:
            f.write("side\n")
        run(self.repo, "commit", "-qam", "side")
        run(self.repo, "checkout", "-q", "main")
        with open(os.path.join(self.repo, "a.txt"), "w") as f:
            f.write("main\n")
        run(self.repo, "commit", "-qam", "main")
        subprocess.run(["git", "-C", self.repo, "merge", "side"], capture_output=True)
        self.assertTrue(libgit.has_conflicts())
