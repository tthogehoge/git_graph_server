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
