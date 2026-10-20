import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import urlopen

import git_graph_server as app


class ParseStatusTests(unittest.TestCase):
    def test_parse_status_groups_changes_and_rename(self):
        raw = (
            "A  staged.txt\0"
            " M unstaged.txt\0"
            "?? untracked.txt\0"
            "UU conflicted.txt\0"
            "R  renamed.txt\0old-name.txt\0"
            "!! ignored.txt\0"
        )

        status = app.parse_status(raw)

        self.assertEqual(status["staged"], [
            {"path": "staged.txt", "old_path": None, "st": "A"},
            {"path": "renamed.txt", "old_path": "old-name.txt", "st": "R"},
        ])
        self.assertEqual(status["unstaged"], [
            {"path": "unstaged.txt", "old_path": None, "st": "M"},
        ])
        self.assertEqual(status["untracked"], [
            {"path": "untracked.txt", "st": "?", "label": "new_file"},
        ])
        self.assertEqual(status["conflicts"], [
            {"path": "conflicted.txt", "st": "U", "label": "label_conflict"},
        ])
        self.assertFalse(status["truncated"])

    def test_parse_status_limit_marks_truncated(self):
        status = app.parse_status(" M first\0 M second\0", limit=1)

        self.assertEqual([item["path"] for item in status["unstaged"]], ["first"])
        self.assertTrue(status["truncated"])

    def test_wip_count_counts_unique_paths_across_groups(self):
        status = {
            "staged": [{"path": "same"}],
            "unstaged": [{"path": "same"}],
            "untracked": [{"path": "new"}],
            "conflicts": [{"path": "conflict"}],
        }

        self.assertEqual(app.wip_count(status), 3)


class GitOutputParserTests(unittest.TestCase):
    def test_parse_name_status_handles_rename_and_regular_changes(self):
        raw = "M\0modified.txt\0R100\0old.txt\0new.txt\0A\0added.txt\0"

        self.assertEqual(app.parse_name_status(raw), {
            "modified.txt": ("M", None),
            "new.txt": ("R", "old.txt"),
            "added.txt": ("A", None),
        })

    def test_parse_numstat_handles_binary_files_and_renames(self):
        raw = "2\t1\ttext.txt\0-\t-\tbinary.dat\0"
        raw += "0\t0\t\0old.txt\0new.txt\0"

        self.assertEqual(app.parse_numstat(raw), [
            {"path": "text.txt", "old_path": None, "added": 2, "deleted": 1},
            {"path": "binary.dat", "old_path": None, "added": None, "deleted": None},
            {"path": "new.txt", "old_path": "old.txt", "added": 0, "deleted": 0},
        ])


class InputValidationTests(unittest.TestCase):
    def test_validate_file_path_accepts_repo_relative_paths(self):
        self.assertEqual(app.validate_file_path("src/module.py"), "src/module.py")
        self.assertEqual(app.validate_file_path(""), "")

    def test_validate_file_path_rejects_invalid_paths(self):
        for path in ("/absolute", "../outside", "src/../outside", "src//file", "a\0b"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                app.validate_file_path(path)

    def test_validate_file_path_can_require_nonempty_path(self):
        with self.assertRaises(ValueError):
            app.validate_file_path("", allow_empty=False)


class TextHandlingTests(unittest.TestCase):
    def test_truncate_width_accounts_for_wide_characters(self):
        self.assertEqual(app.truncate_width("a日本b", 4), "a日")
        self.assertEqual(app.truncate_width("abc", 3), "abc")

    def test_decode_text_detects_utf8_and_bom(self):
        self.assertEqual(app.decode_text("日本語".encode()), ("日本語", "UTF-8"))
        self.assertEqual(
            app.decode_text(b"\xef\xbb\xbfhello"),
            ("hello", "UTF-8 (BOM)"),
        )

    def test_decode_text_detects_legacy_encoding_and_binary(self):
        text, encoding = app.decode_text("日本語".encode("cp932"))
        self.assertEqual(text, "日本語")
        self.assertEqual(encoding, "Shift_JIS (CP932)")
        self.assertEqual(app.decode_text(b"a\0b"), (None, None))

    def test_decode_text_recovers_truncated_utf8_tail(self):
        self.assertEqual(app.decode_text("helloあ".encode()[:-1], truncated=True), ("hello", "ASCII"))


class GitApiIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.repo = cls.temp_dir.name
        cls.previous_repo = app.REPO
        app.REPO = cls.repo

        cls.git("init", "-q")
        cls.git("config", "user.name", "Test User")
        cls.git("config", "user.email", "test@example.com")
        cls.git("branch", "-M", "main")

        os.makedirs(os.path.join(cls.repo, "src"))
        cls.write_file("README.md", "first version\n")
        cls.write_file("src/app.py", "print('hello')\n")
        cls.git("add", ".")
        cls.git("commit", "-q", "-m", "initial commit")
        cls.first_commit = cls.git("rev-parse", "HEAD").strip()
        cls.git("branch", "feature")
        cls.git("tag", "v1.0")

        cls.write_file("README.md", "second version\n")
        cls.write_file("new.txt", "new file\n")
        cls.git("add", ".")
        cls.git("commit", "-q", "-m", "update readme")
        cls.second_commit = cls.git("rev-parse", "HEAD").strip()

        cls.write_file("src/app.py", "print('updated')\n")
        cls.git("add", "src/app.py")
        cls.git("commit", "-q", "-m", "update application")
        cls.third_commit = cls.git("rev-parse", "HEAD").strip()

    @classmethod
    def tearDownClass(cls):
        app.REPO = cls.previous_repo
        cls.temp_dir.cleanup()

    @classmethod
    def git(cls, *args):
        return subprocess.check_output(
            ["git", "-C", cls.repo, *args],
            text=True,
            encoding="utf-8",
        )

    @classmethod
    def write_file(cls, path, content):
        with open(os.path.join(cls.repo, path), "w", encoding="utf-8") as file:
            file.write(content)

    def test_api_log_returns_commit_history_and_path_filter(self):
        result = app.api_log({"rev": [self.second_commit]})

        self.assertEqual(result["total"], 2)
        self.assertEqual([row[4] for row in result["commits"]], [
            "update readme",
            "initial commit",
        ])

        filtered = app.api_log({"rev": [self.second_commit], "path": ["src/app.py"]})
        self.assertEqual(filtered["total"], 1)
        self.assertEqual(filtered["commits"][0][0], self.first_commit)

    def test_api_commit_includes_parent_and_changed_files(self):
        result = app.api_commit({"hash": [self.second_commit]})

        self.assertEqual(result["hash"], self.second_commit)
        self.assertEqual(result["parents"], [self.first_commit])
        self.assertEqual({item["path"] for item in result["files"]}, {"README.md", "new.txt"})

    def test_api_tree_and_file_read_from_commit(self):
        tree = app.api_tree({"hash": [self.second_commit]})
        self.assertEqual([entry["name"] for entry in tree["entries"]], [
            "src",
            "README.md",
            "new.txt",
        ])
        self.assertEqual(tree["entries"][0]["type"], "tree")

        file_data = app.api_file({"hash": [self.second_commit], "path": ["README.md"]})
        self.assertEqual(file_data["content"], "second version\n")
        self.assertFalse(file_data["binary"])
        self.assertEqual(file_data["encoding"], "ASCII")

    def test_api_compare_returns_file_changes_between_commits(self):
        result = app.api_compare({
            "from": [self.first_commit],
            "to": [self.second_commit],
        })

        self.assertEqual(result["from"]["hash"], self.first_commit)
        self.assertEqual(result["to"]["hash"], self.second_commit)
        self.assertEqual({item["path"] for item in result["files"]}, {"README.md", "new.txt"})

    @unittest.skipUnless(shutil.which("dot"), "Graphviz dot is not installed")
    def test_build_graph_keeps_refs_and_collapses_intermediate_commits(self):
        opts = SimpleNamespace(
            revs=[],
            no_remotes=False,
            no_tags=False,
            include_tips=False,
            rankdir="BT",
            font="Helvetica",
            no_count=False,
            full_layout=False,
        )

        dot, nodes, edges = app.build_graph(opts)

        self.assertEqual(set(nodes), {self.first_commit, self.third_commit})
        self.assertEqual(edges, {
            f"{self.first_commit}->{self.third_commit}": {
                "from": self.first_commit,
                "to": self.third_commit,
            },
        })
        self.assertEqual(
            nodes[self.first_commit]["refs"],
            [
                {"name": "feature", "kind": "branch"},
                {"name": "v1.0", "kind": "tag"},
            ],
        )
        self.assertEqual(
            nodes[self.third_commit]["refs"],
            [
                {"name": "main", "kind": "current"},
                {"name": "HEAD", "kind": "head"},
            ],
        )
        self.assertIn('[label="+1", style=dashed]', dot)

        svg = subprocess.check_output(["dot", "-Tsvg"], input=dot.encode("utf-8"))
        self.assertIn(b"<svg", svg)

    def test_build_graph_respects_tag_and_count_options(self):
        opts = SimpleNamespace(
            revs=[],
            no_remotes=False,
            no_tags=True,
            include_tips=False,
            rankdir="BT",
            font="Helvetica",
            no_count=True,
            full_layout=False,
        )

        dot, nodes, _ = app.build_graph(opts)

        first_refs = nodes[self.first_commit]["refs"]
        self.assertNotIn("v1.0", {ref["name"] for ref in first_refs})
        self.assertNotIn('[label="+1", style=dashed]', dot)


class GitWriteApiIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = self.temp_dir.name
        self.previous_repo = app.REPO
        self.previous_wip_enabled = app.WIP_ENABLED
        self.previous_has_worktree = app.HAS_WORKTREE
        app.REPO = self.repo
        app.WIP_ENABLED = True
        app.HAS_WORKTREE = True

        self.git("init", "-q")
        self.git("config", "user.name", "Test User")
        self.git("config", "user.email", "test@example.com")
        self.git("branch", "-M", "main")
        self.write_file("tracked.txt", "initial\n")
        self.git("add", "tracked.txt")
        self.git("commit", "-q", "-m", "initial commit")
        self.head = self.git("rev-parse", "HEAD").strip()

    def tearDown(self):
        app.REPO = self.previous_repo
        app.WIP_ENABLED = self.previous_wip_enabled
        app.HAS_WORKTREE = self.previous_has_worktree
        self.temp_dir.cleanup()

    def git(self, *args):
        return subprocess.check_output(
            ["git", "-C", self.repo, *args],
            text=True,
            encoding="utf-8",
        )

    def write_file(self, path, content):
        with open(os.path.join(self.repo, path), "w", encoding="utf-8") as file:
            file.write(content)

    def test_branch_and_annotated_tag_apis_create_refs(self):
        branch_result = app.api_branch({
            "hash": self.head,
            "name": "feature",
        })
        tag_result = app.api_tag({
            "hash": self.head,
            "name": "release-1",
            "message": "release one",
        })

        self.assertTrue(branch_result["ok"])
        self.assertTrue(tag_result["ok"])
        self.assertEqual(self.git("rev-parse", "refs/heads/feature").strip(), self.head)
        self.assertEqual(self.git("cat-file", "-t", "refs/tags/release-1").strip(), "tag")
        self.assertEqual(
            self.git("for-each-ref", "--format=%(contents:subject)", "refs/tags/release-1").strip(),
            "release one",
        )

    def test_branch_rename_and_ref_deletion_update_refs(self):
        app.api_branch({"hash": self.head, "name": "old-name"})
        rename_result = app.api_branch_rename({"old": "old-name", "new": "new-name"})
        self.assertTrue(rename_result["ok"])
        self.assertEqual(self.git("rev-parse", "refs/heads/new-name").strip(), self.head)
        self.assertNotEqual(
            subprocess.run(
                ["git", "-C", self.repo, "show-ref", "--verify", "--quiet", "refs/heads/old-name"],
                capture_output=True,
            ).returncode,
            0,
        )

        app.api_tag({"hash": self.head, "name": "temporary"})
        delete_tag = app.api_delete_ref({"kind": "tag", "name": "temporary"})
        delete_branch = app.api_delete_ref({"kind": "branch", "name": "new-name"})

        self.assertTrue(delete_tag["ok"])
        self.assertTrue(delete_branch["ok"])
        self.assertNotEqual(
            subprocess.run(
                ["git", "-C", self.repo, "show-ref", "--verify", "--quiet", "refs/tags/temporary"],
                capture_output=True,
            ).returncode,
            0,
        )

    def test_file_copy_copies_commit_content_without_overwriting_existing_file(self):
        copy_result = app.api_file_copy({
            "hash": self.head,
            "path": "tracked.txt",
            "dest": "copies/tracked.txt",
        })

        self.assertTrue(copy_result["ok"])
        self.assertEqual(self.read_file("copies/tracked.txt"), "initial\n")
        with self.assertRaises(app.UserError):
            app.api_file_copy({
                "hash": self.head,
                "path": "tracked.txt",
                "dest": "copies/tracked.txt",
            })
        with self.assertRaises(app.UserError):
            app.api_file_copy({
                "hash": self.head,
                "path": "tracked.txt",
                "dest": "../outside.txt",
            })
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.repo), "outside.txt")))

    def test_http_handler_routes_api_get_and_rejects_unknown_path(self):
        previous_opts = app.OPTS
        previous_writable = app.WRITABLE
        app.OPTS = SimpleNamespace(host="127.0.0.1")
        app.WRITABLE = True
        server = app.QuietServer(("127.0.0.1", 0), app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with urlopen(base_url) as response:
                page = response.read().decode("utf-8")
            self.assertIn('href="/static/app.css"', page)
            self.assertIn('src="/static/app.js"', page)
            self.assertNotIn("<style>", page)
            self.assertNotIn("<script>\n", page)

            with urlopen(f"{base_url}/static/app.js") as response:
                app_js = response.read().decode("utf-8")
                self.assertIn("application/javascript", response.headers.get("Content-Type"))
            self.assertIn("const I18N = {", app_js)
            self.assertNotIn("/*__I18N__*/", app_js)

            with urlopen(f"{base_url}/static/app.css") as response:
                app_css = response.read().decode("utf-8")
                self.assertIn("text/css", response.headers.get("Content-Type"))
            self.assertIn("#graphWrap", app_css)

            with urlopen(f"{base_url}/api/log?rev={self.head}") as response:
                payload = json.loads(response.read())
            self.assertEqual(payload["total"], 1)
            self.assertEqual(payload["commits"][0][0], self.head)

            with self.assertRaises(HTTPError) as error:
                urlopen(f"{base_url}/missing")
            self.assertEqual(error.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            app.OPTS = previous_opts
            app.WRITABLE = previous_writable

    def test_wip_commit_commits_only_selected_files_and_preserves_other_changes(self):
        self.write_file("selected.txt", "selected content\n")
        self.write_file("tracked.txt", "left out content\n")
        self.git("add", "tracked.txt")

        result = app.api_wip_commit({
            "paths": ["selected.txt"],
            "message": "commit selected file",
        })

        self.assertTrue(result["ok"])
        self.assertEqual(self.git("log", "-1", "--format=%s").strip(), "commit selected file")
        self.assertEqual(self.git("show", "HEAD:selected.txt"), "selected content\n")
        self.assertEqual(self.git("show", "HEAD:tracked.txt"), "initial\n")
        status = app.parse_status(app.read_status_raw())
        self.assertEqual([item["path"] for item in status["unstaged"]], ["tracked.txt"])
        self.assertEqual(status["staged"], [])

    def test_wip_commit_rejects_unmodified_paths_without_creating_commit(self):
        with self.assertRaises(app.UserError):
            app.api_wip_commit({
                "paths": ["missing.txt"],
                "message": "should not commit",
            })

        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.head)

    def test_mixed_reset_moves_head_and_leaves_changes_unstaged(self):
        self.write_file("tracked.txt", "second commit\n")
        self.git("commit", "-a", "-q", "-m", "second commit")
        second = self.git("rev-parse", "HEAD").strip()

        result = app.api_reset({"hash": self.head, "mode": "mixed"})

        self.assertTrue(result["ok"])
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.head)
        self.assertEqual(self.git("show", "HEAD:tracked.txt"), "initial\n")
        self.assertEqual(self.read_file("tracked.txt"), "second commit\n")
        status = app.parse_status(app.read_status_raw())
        self.assertEqual([item["path"] for item in status["unstaged"]], ["tracked.txt"])
        self.assertEqual(status["staged"], [])
        self.assertNotEqual(second, self.head)

    def test_hard_reset_restores_files_and_removes_later_additions(self):
        self.write_file("later.txt", "remove on hard reset\n")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "second commit")

        result = app.api_reset({"hash": self.head, "mode": "hard"})

        self.assertTrue(result["ok"])
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.head)
        self.assertFalse(os.path.exists(os.path.join(self.repo, "later.txt")))
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_reset_rejects_invalid_mode_without_changing_head(self):
        with self.assertRaises(app.UserError):
            app.api_reset({"hash": self.head, "mode": "invalid"})

        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.head)

    def test_discard_restores_tracked_files_and_removes_new_files(self):
        self.write_file("tracked.txt", "staged version\n")
        self.git("add", "tracked.txt")
        self.write_file("tracked.txt", "unstaged version\n")
        self.write_file("staged-new.txt", "staged new file\n")
        self.git("add", "staged-new.txt")
        self.write_file("untracked.txt", "untracked file\n")

        result = app.api_discard({
            "paths": ["tracked.txt", "staged-new.txt", "untracked.txt"],
        })

        self.assertTrue(result["ok"])
        self.assertEqual(self.read_file("tracked.txt"), "initial\n")
        self.assertFalse(os.path.exists(os.path.join(self.repo, "staged-new.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.repo, "untracked.txt")))
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_discard_rejects_unchanged_paths_without_removing_them(self):
        with self.assertRaises(app.UserError):
            app.api_discard({"paths": ["tracked.txt"]})

        self.assertEqual(self.read_file("tracked.txt"), "initial\n")
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_rebase_moves_topic_commit_onto_target_branch(self):
        self.git("checkout", "-q", "-b", "topic")
        self.write_file("topic.txt", "topic change\n")
        self.git("add", "topic.txt")
        self.git("commit", "-q", "-m", "topic change")
        topic_before = self.git("rev-parse", "HEAD").strip()

        self.git("checkout", "-q", "main")
        self.write_file("main.txt", "main change\n")
        self.git("add", "main.txt")
        self.git("commit", "-q", "-m", "main change")
        target = self.git("rev-parse", "HEAD").strip()
        self.git("checkout", "-q", "topic")

        result = app.api_rebase({"hash": target})

        self.assertTrue(result["ok"])
        rebased = self.git("rev-parse", "HEAD").strip()
        self.assertNotEqual(rebased, topic_before)
        self.assertEqual(self.git("rev-parse", "HEAD^").strip(), target)
        self.assertEqual(self.git("show", "HEAD:topic.txt"), "topic change\n")
        self.assertEqual(self.git("show", "HEAD:main.txt"), "main change\n")

    def test_rebase_conflict_aborts_and_restores_original_tip(self):
        self.git("checkout", "-q", "-b", "topic")
        self.write_file("tracked.txt", "topic version\n")
        self.git("commit", "-a", "-q", "-m", "topic version")
        topic = self.git("rev-parse", "HEAD").strip()

        self.git("checkout", "-q", "main")
        self.write_file("tracked.txt", "main version\n")
        self.git("commit", "-a", "-q", "-m", "main version")
        target = self.git("rev-parse", "HEAD").strip()
        self.git("checkout", "-q", "topic")

        with self.assertRaises(app.UserError):
            app.api_rebase({"hash": target})

        self.assertEqual(self.git("rev-parse", "HEAD").strip(), topic)
        self.assertEqual(self.read_file("tracked.txt"), "topic version\n")
        self.assertFalse(app.is_rebasing())
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_cherry_pick_applies_commit_to_current_branch(self):
        self.git("checkout", "-q", "-b", "source")
        self.write_file("picked.txt", "picked content\n")
        self.git("add", "picked.txt")
        self.git("commit", "-q", "-m", "picked change")
        picked = self.git("rev-parse", "HEAD").strip()
        self.git("checkout", "-q", "main")

        result = app.api_cherry_pick({"hash": picked})

        self.assertTrue(result["ok"])
        self.assertEqual(self.git("log", "-1", "--format=%s").strip(), "picked change")
        self.assertEqual(self.git("show", "HEAD:picked.txt"), "picked content\n")
        self.assertEqual(self.git("rev-parse", "HEAD^").strip(), self.head)

    def test_cherry_pick_conflict_aborts_without_changing_head_or_worktree(self):
        self.git("checkout", "-q", "-b", "source")
        self.write_file("tracked.txt", "source version\n")
        self.git("commit", "-a", "-q", "-m", "source version")
        picked = self.git("rev-parse", "HEAD").strip()

        self.git("checkout", "-q", "main")
        self.write_file("tracked.txt", "main version\n")
        self.git("commit", "-a", "-q", "-m", "main version")
        main_tip = self.git("rev-parse", "HEAD").strip()

        with self.assertRaises(app.UserError):
            app.api_cherry_pick({"hash": picked})

        self.assertEqual(self.git("rev-parse", "HEAD").strip(), main_tip)
        self.assertEqual(self.read_file("tracked.txt"), "main version\n")
        self.assertFalse(app.is_cherry_picking())
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_merge_no_ff_creates_two_parent_commit(self):
        self.git("checkout", "-q", "-b", "feature")
        self.write_file("feature.txt", "feature change\n")
        self.git("add", "feature.txt")
        self.git("commit", "-q", "-m", "feature change")
        feature_tip = self.git("rev-parse", "HEAD").strip()
        self.git("checkout", "-q", "main")
        main_tip = self.git("rev-parse", "HEAD").strip()

        result = app.api_merge({"hash": feature_tip, "no_ff": True})

        self.assertTrue(result["ok"])
        parents = self.git("rev-list", "--parents", "-n", "1", "HEAD").split()
        self.assertEqual(parents[1:], [main_tip, feature_tip])
        self.assertEqual(self.git("show", "HEAD:feature.txt"), "feature change\n")

    def test_merge_conflict_is_reported_and_can_be_aborted(self):
        self.git("checkout", "-q", "-b", "feature")
        self.write_file("tracked.txt", "feature version\n")
        self.git("commit", "-a", "-q", "-m", "feature version")
        feature_tip = self.git("rev-parse", "HEAD").strip()
        self.git("checkout", "-q", "main")
        self.write_file("tracked.txt", "main version\n")
        self.git("commit", "-a", "-q", "-m", "main version")
        main_tip = self.git("rev-parse", "HEAD").strip()

        result = app.api_merge({"hash": feature_tip})

        self.assertTrue(result["conflict"])
        self.assertTrue(app.is_merging())
        self.assertTrue(app.api_status({})["conflicts"])
        abort_result = app.api_abort({})
        self.assertTrue(abort_result["ok"])
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), main_tip)
        self.assertEqual(self.read_file("tracked.txt"), "main version\n")
        self.assertFalse(app.is_merging())
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_checkout_reports_modified_file_that_blocks_branch_switch(self):
        self.git("checkout", "-q", "-b", "other")
        self.write_file("tracked.txt", "branch version\n")
        self.git("commit", "-a", "-q", "-m", "branch version")
        self.git("checkout", "-q", "main")
        self.write_file("tracked.txt", "local change\n")

        result = app.api_checkout({"branch": "other"})

        self.assertTrue(result["blocked"])
        self.assertEqual(result["modified"], ["tracked.txt"])
        self.assertEqual(result["untracked"], [])
        self.assertEqual(app.api_head()["branch"], "main")
        self.assertEqual(self.read_file("tracked.txt"), "local change\n")

    def test_rebase_range_moves_commits_after_base_onto_target(self):
        base = self.head
        self.git("checkout", "-q", "-b", "topic")
        self.write_file("topic.txt", "topic change\n")
        self.git("add", "topic.txt")
        self.git("commit", "-q", "-m", "topic change")
        topic_tip = self.git("rev-parse", "HEAD").strip()
        self.git("checkout", "-q", "main")
        self.write_file("target.txt", "target change\n")
        self.git("add", "target.txt")
        self.git("commit", "-q", "-m", "target change")
        target = self.git("rev-parse", "HEAD").strip()
        self.git("checkout", "-q", "topic")

        result = app.api_rebase_range({"from": base, "to": target})

        self.assertTrue(result["ok"])
        self.assertNotEqual(self.git("rev-parse", "HEAD").strip(), topic_tip)
        self.assertEqual(self.git("rev-parse", "HEAD^").strip(), target)
        self.assertEqual(self.git("show", "HEAD:topic.txt"), "topic change\n")
        self.assertEqual(self.git("show", "HEAD:target.txt"), "target change\n")

    def test_status_and_wip_diff_cover_staged_unstaged_and_untracked_files(self):
        self.write_file("tracked.txt", "staged change\n")
        self.git("add", "tracked.txt")
        self.write_file("tracked.txt", "unstaged change\n")
        self.write_file("new.txt", "untracked content\n")

        status = app.api_status({})

        self.assertEqual([item["path"] for item in status["staged"]], ["tracked.txt"])
        self.assertEqual([item["path"] for item in status["unstaged"]], ["tracked.txt"])
        self.assertEqual([item["path"] for item in status["untracked"]], ["new.txt"])
        self.assertEqual(status["head"], self.head)
        self.assertIn(b"staged change", app.wip_diff("staged", "tracked.txt", ["tracked.txt"]))
        self.assertIn(b"unstaged change", app.wip_diff("unstaged", "tracked.txt", ["tracked.txt"]))
        self.assertIn(b"untracked content", app.wip_diff("untracked", "new.txt", ["new.txt"]))

    def test_stash_apply_and_drop_use_stash_commit_hash(self):
        self.write_file("tracked.txt", "stashed content\n")
        self.git("stash", "push", "-q", "-m", "test stash")
        stash_hash = self.git("rev-parse", "refs/stash").strip()

        listed = app.api_stash_list({})
        self.assertEqual(listed["total"], 1)
        self.assertEqual(listed["commits"][0][0], stash_hash)

        apply_result = app.api_stash({"hash": stash_hash, "op": "apply"})
        self.assertTrue(apply_result["ok"])
        self.assertEqual(self.read_file("tracked.txt"), "stashed content\n")
        self.assertEqual(app.api_stash_list({})["total"], 1)
        self.git("checkout", "--", "tracked.txt")

        drop_result = app.api_stash({"hash": stash_hash, "op": "drop"})
        self.assertTrue(drop_result["ok"])
        self.assertEqual(app.api_stash_list({})["total"], 0)

    def test_revert_creates_commit_that_restores_previous_content(self):
        self.write_file("tracked.txt", "changed version\n")
        self.git("commit", "-a", "-q", "-m", "change tracked file")
        changed = self.git("rev-parse", "HEAD").strip()

        result = app.api_revert({"hash": changed})

        self.assertTrue(result["ok"])
        self.assertEqual(self.read_file("tracked.txt"), "initial\n")
        self.assertEqual(self.git("log", "-1", "--format=%s").strip(), f"Revert \"change tracked file\"")
        self.assertEqual(self.git("rev-parse", "HEAD^").strip(), changed)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_revert_conflict_aborts_and_preserves_original_tip(self):
        self.git("checkout", "-q", "-b", "source")
        self.write_file("tracked.txt", "source version\n")
        self.git("commit", "-a", "-q", "-m", "source change")
        changed = self.git("rev-parse", "HEAD").strip()
        self.git("checkout", "-q", "main")
        self.write_file("tracked.txt", "main version\n")
        self.git("commit", "-a", "-q", "-m", "main change")
        main_tip = self.git("rev-parse", "HEAD").strip()

        with self.assertRaises(app.UserError):
            app.api_revert({"hash": changed})

        self.assertEqual(self.git("rev-parse", "HEAD").strip(), main_tip)
        self.assertEqual(self.read_file("tracked.txt"), "main version\n")
        self.assertFalse(app.is_reverting())
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_interactive_rebase_rewords_selected_commit(self):
        self.write_file("first.txt", "first\n")
        self.git("add", "first.txt")
        self.git("commit", "-q", "-m", "first topic commit")
        first_topic = self.git("rev-parse", "HEAD").strip()
        self.write_file("second.txt", "second\n")
        self.git("add", "second.txt")
        self.git("commit", "-q", "-m", "second topic commit")

        todo = app.api_rebase_todo({"hash": [self.head]})
        self.assertEqual([item["hash"] for item in todo["commits"]], [
            first_topic,
            self.git("rev-parse", "HEAD").strip(),
        ])
        items = [
            {"hash": todo["commits"][0]["hash"], "action": "reword", "message": "rewritten first"},
            {"hash": todo["commits"][1]["hash"], "action": "pick"},
        ]

        result = app.api_rebase_i({"hash": self.head, "todo": items})

        self.assertTrue(result["ok"])
        self.assertEqual(self.git("log", "-2", "--format=%s"), "second topic commit\nrewritten first\n")
        self.assertEqual(self.git("show", "HEAD:first.txt"), "first\n")
        self.assertEqual(self.git("show", "HEAD:second.txt"), "second\n")
        self.assertEqual(self.git("rev-parse", "HEAD~2").strip(), self.head)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def read_file(self, path):
        with open(os.path.join(self.repo, path), encoding="utf-8") as file:
            return file.read()


if __name__ == "__main__":
    unittest.main()
