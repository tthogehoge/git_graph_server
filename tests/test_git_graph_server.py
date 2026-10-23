import codecs
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import git_graph_server as app
from git_graph import app_state as state
from git_graph import pages as viewer
from git_graph.graph import MERGE_EDGE_COLOR as MERGE_COLOR


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


class RouteRegistrationTests(unittest.TestCase):
    def test_api_routes_are_registered_with_method_and_path(self):
        self.assertIs(app.ROUTES[("GET", "/api/log")], app.api_log)
        self.assertIs(app.ROUTES[("GET", "/api/graph")], app.graph_payload)
        self.assertIs(app.ROUTES[("POST", "/api/push")], app.api_push)
        self.assertIs(app.ROUTES[("POST", "/api/shutdown")], app.api_shutdown)
        self.assertEqual(
            app.POST_ROUTES,
            {path: handler for (method, path), handler in app.ROUTES.items()
             if method == "POST"},
        )


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
        cls.previous_repo = state.REPO
        state.REPO = cls.repo

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
        state.REPO = cls.previous_repo
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

    def test_api_log_includes_parents_for_merge_graph(self):
        result = app.api_log({"rev": [self.third_commit], "graph": ["1"]})
        self.assertEqual([row[5] for row in result["commits"]], [
            [self.second_commit],
            [self.first_commit],
            [],
        ])

        # path 指定時は、そのパスを変更したコミットへ書き換えた親を返す
        filtered = app.api_log({"rev": [self.third_commit], "path": ["src/app.py"], "graph": ["1"]})
        self.assertEqual([row[0] for row in filtered["commits"]], [self.third_commit, self.first_commit])
        self.assertEqual(filtered["commits"][0][5], [self.first_commit])

        # 範囲指定では範囲外の親を含めない
        ranged = app.api_log({"rev": [self.first_commit + ".." + self.third_commit], "graph": ["1"]})
        self.assertEqual([row[5] for row in ranged["commits"]], [[self.second_commit], []])

    def test_api_commit_includes_parent_and_changed_files(self):
        result = app.api_commit({"hash": [self.second_commit]})

        self.assertEqual(result["hash"], self.second_commit)
        self.assertEqual(result["parents"], [self.first_commit])
        self.assertEqual({item["path"] for item in result["files"]}, {"README.md", "new.txt"})

    def test_render_diff_page_includes_favicon(self):
        page = app.render_diff_page({
            "hash": [self.second_commit],
            "path": ["README.md"],
        })

        self.assertIn("<link rel=\"icon\"", page)

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

    def test_api_blame_returns_line_attribution(self):
        result = app.api_blame({"hash": [self.second_commit], "path": ["README.md"]})

        self.assertEqual(result["hash"], self.second_commit)
        self.assertEqual(result["path"], "README.md")
        self.assertEqual(result["lines"], [{
            "hash": self.second_commit,
            "line": 1,
            "author": "Test User",
            "date": self.git("show", "-s", "--format=%ad", "--date=short", self.second_commit).strip(),
            "content": "second version",
        }])

    def test_files_page_includes_blame_action(self):
        page = viewer.render_files_page()

        self.assertIn('id="fileBlameBtn"', page)
        self.assertIn(".blame-no { flex: none; width: 8ch", page)
        self.assertIn('class="blame-commit"', page)
        self.assertIn('type: "git-graph-commit"', page)
        self.assertIn('showContainingBranchLog(ev.data.hash)', app.render_app_js())
        self.assertIn('showContainingBranchLog(commitHash)', app.render_app_js())
        self.assertIn('api("/api/blame"', page)

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
        # 間に省略したコミットがあるので実線 (個数のラベルも付けない)
        self.assertIn(f'"{self.first_commit}" -> "{self.third_commit}";', dot)
        self.assertNotIn("label", dot.split("-> ")[1])

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
        self.assertNotIn("style=dashed", dot)  # 省略数を数えないので線種は区別しない


class WorkingTreeFilesTests(unittest.TestCase):
    """/files?hash=WIP: 作業ツリーのファイル一覧・内容・blame。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = self.temp_dir.name
        self.previous = (state.REPO, state.HAS_WORKTREE)
        state.REPO, state.HAS_WORKTREE = self.repo, True
        self.git("init", "-q")
        self.git("config", "user.name", "Test User")
        self.git("config", "user.email", "test@example.com")
        os.makedirs(os.path.join(self.repo, "src"))
        self.write("README.md", "first\n")
        self.write("src/app.py", "print(1)\n")
        self.write("gone.txt", "x\n")
        self.write(".gitignore", "*.log\n")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "initial")
        self.head = self.git("rev-parse", "HEAD").strip()
        self.write("README.md", "first\nsecond\n")
        self.write("new.txt", "untracked\n")
        self.write("debug.log", "ignored\n")
        os.remove(os.path.join(self.repo, "gone.txt"))

    def tearDown(self):
        state.REPO, state.HAS_WORKTREE = self.previous
        self.temp_dir.cleanup()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", self.repo, *args], text=True, encoding="utf-8")

    def write(self, path, content):
        with open(os.path.join(self.repo, path), "w", encoding="utf-8") as f:
            f.write(content)

    def test_tree_lists_working_tree_without_ignored_or_deleted(self):
        tree = app.api_tree({"hash": ["WIP"]})
        self.assertEqual(tree["head"], self.head)
        self.assertEqual([e["name"] for e in tree["entries"]], ["src", ".gitignore", "README.md", "new.txt"])
        self.assertEqual(tree["entries"][0]["type"], "tree")
        sub = app.api_tree({"hash": ["WIP"], "path": ["src"]})
        self.assertEqual([e["name"] for e in sub["entries"]], ["app.py"])

    def test_tree_marks_changed_entries(self):
        self.write("src/app.py", "print(2)\n")
        self.git("add", "src/app.py")
        os.makedirs(os.path.join(self.repo, "docs"))
        self.write("docs/a.md", "new\n")
        tree = {e["name"]: e for e in app.api_tree({"hash": ["WIP"]})["entries"]}
        self.assertEqual((tree["README.md"]["st"], tree["README.md"]["xy"]), ("M", " M"))
        self.assertEqual(tree["new.txt"]["st"], "?")
        self.assertEqual(tree["src"]["st"], "M")
        self.assertEqual(tree["docs"]["st"], "?")
        self.assertNotIn("st", tree[".gitignore"])
        sub = app.api_tree({"hash": ["WIP"], "path": ["src"]})["entries"]
        self.assertEqual((sub[0]["st"], sub[0]["xy"]), ("M", "M "))

    def test_status_mark(self):
        from git_graph.files import status_mark
        self.assertEqual([status_mark(x) for x in ("??", "UU", "A ", "AM", " D", "R ", "MM", " T")],
                         ["?", "U", "A", "A", "D", "R", "M", "M"])

    def test_file_reads_uncommitted_content(self):
        self.assertEqual(app.api_file({"hash": ["WIP"], "path": ["README.md"]})["content"], "first\nsecond\n")
        self.assertEqual(app.api_file({"hash": ["WIP"], "path": ["new.txt"]})["content"], "untracked\n")

    def test_api_compare_includes_working_tree_and_untracked_changes(self):
        previous = state.WIP_ENABLED
        state.WIP_ENABLED = True
        self.addCleanup(setattr, state, "WIP_ENABLED", previous)

        result = app.api_compare({"from": [self.head], "to": ["WIP"]})

        self.assertEqual(result["to"]["hash"], "WIP")
        self.assertEqual(
            {item["path"] for item in result["files"]},
            {"README.md", "gone.txt", "new.txt"},
        )
        self.assertIn(
            "+second",
            app.api_diff({"from": [self.head], "wip": ["compare"], "path": ["README.md"]})["diff"],
        )
        self.assertIn(
            "untracked",
            app.api_diff({"from": [self.head], "wip": ["compare"], "path": ["new.txt"]})["diff"],
        )
        page = app.render_diff_page({"from": [self.head], "wip": ["compare"], "path": ["README.md"]})
        self.assertIn("作業ツリーの変更 (WIP)", page)
        self.assertNotIn('id="cbtn"', page)

    def test_blame_marks_uncommitted_lines(self):
        lines = app.api_blame({"hash": ["WIP"], "path": ["README.md"]})["lines"]
        self.assertEqual(lines[0]["hash"], self.head)
        self.assertRegex(lines[1]["hash"], r"^0+$")
        self.assertEqual(lines[1]["content"], "second")
        with self.assertRaises(app.UserError):
            app.api_blame({"hash": ["WIP"], "path": ["new.txt"]})

    def test_rejects_paths_outside_repository(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        os.symlink(outside.name, os.path.join(self.repo, "link"))
        with open(os.path.join(outside.name, "secret.txt"), "w") as f:
            f.write("secret")
        for path in ("../x", ".git/config", "link/secret.txt"):
            with self.assertRaises(ValueError):
                app.api_file({"hash": ["WIP"], "path": [path]})

    def test_files_page_accepts_wip_hash_over_http(self):
        server = app.QuietServer(("127.0.0.1", 0), app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with urlopen(base + "/files?hash=WIP") as response:
            self.assertEqual(response.status, 200)
        with self.assertRaises(HTTPError) as cm:
            urlopen(base + "/files?hash=bad")
        self.assertEqual(cm.exception.code, 400)
        cm.exception.close()

    def write_bytes(self, path, data):
        with open(os.path.join(self.repo, path), "wb") as f:
            f.write(data)

    def read_bytes(self, path):
        with open(os.path.join(self.repo, path), "rb") as f:
            return f.read()

    def edit(self, path, change):
        """画面と同じ流れ: 読み込み → 編集欄 (改行は LF) で編集 → 保存。"""
        data = app.api_file({"hash": ["WIP"], "path": [path]})
        self.assertTrue(data["editable"], data.get("edit_reason"))
        text = data["content"].replace("\r\n", "\n").replace("\r", "\n")
        return app.api_file_save({"path": path, "content": change(text), "sha": data["sha"]})

    def test_edit_restores_encoding_bom_and_line_endings(self):
        cases = [
            ("utf8.txt", "あ\nい\n".encode("utf-8"), "う\n".encode("utf-8")),
            ("sjis.txt", "あ\r\nい\r\n".encode("cp932"), "う\r\n".encode("cp932")),
            ("euc.txt", "あ\nい\n".encode("euc_jp"), "う\n".encode("euc_jp")),
            ("bom.txt", codecs.BOM_UTF8 + "あ\r\nい\r\n".encode("utf-8"), "う\r\n".encode("utf-8")),
            ("u16.txt", codecs.BOM_UTF16_LE + "あ\r\nい\r\n".encode("utf-16-le"), "う\r\n".encode("utf-16-le")),
            ("cr.txt", b"a\rb\r", b"c\r"),
        ]
        for name, original, added in cases:
            with self.subTest(name=name):
                self.write_bytes(name, original)
                r = self.edit(name, lambda text: text + ("う" if name != "cr.txt" else "c") + "\n")
                self.assertTrue(r["ok"])
                self.assertEqual(self.read_bytes(name), original + added)

    def test_edit_unchanged_and_mixed_line_endings(self):
        self.write_bytes("mixed.txt", b"a\r\nb\r\nc\n")
        data = app.api_file({"hash": ["WIP"], "path": ["mixed.txt"]})
        self.assertEqual((data["eol"], data["eol_mixed"]), ("crlf", True))
        self.edit("mixed.txt", lambda text: text)
        self.assertEqual(self.read_bytes("mixed.txt"), b"a\r\nb\r\nc\r\n")
        self.assertTrue(self.edit("mixed.txt", lambda text: text)["unchanged"])

    def test_edit_rejects_changed_file_and_unencodable_characters(self):
        self.write_bytes("sjis.txt", "あ\n".encode("cp932"))
        data = app.api_file({"hash": ["WIP"], "path": ["sjis.txt"]})
        with self.assertRaises(app.UserError):
            app.api_file_save({"path": "sjis.txt", "content": "あ\U0001F600\n", "sha": data["sha"]})
        self.write_bytes("sjis.txt", "い\n".encode("cp932"))
        with self.assertRaises(app.UserError):
            app.api_file_save({"path": "sjis.txt", "content": "う\n", "sha": data["sha"]})
        self.assertEqual(self.read_bytes("sjis.txt"), "い\n".encode("cp932"))

    def test_edit_keeps_file_mode_and_refuses_non_text(self):
        path = os.path.join(self.repo, "run.sh")
        self.write_bytes("run.sh", b"echo 1\n")
        os.chmod(path, 0o755)
        self.edit("run.sh", lambda text: text + "echo 2\n")
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o755)
        self.write_bytes("bin.dat", b"\0\1\2")
        self.assertEqual(app.api_file({"hash": ["WIP"], "path": ["bin.dat"]})["editable"], False)
        os.symlink("README.md", os.path.join(self.repo, "link.md"))
        self.assertEqual(app.api_file({"hash": ["WIP"], "path": ["link.md"]})["edit_reason"], "edit_symlink")
        with self.assertRaises(app.UserError):
            app.api_file_save({"path": "link.md", "content": "x", "sha": ""})

    def test_edit_large_body_over_http(self):
        previous = (state.WRITABLE, state.OPTS)
        state.WRITABLE, state.OPTS = True, SimpleNamespace(host="127.0.0.1")
        self.addCleanup(lambda: (setattr(state, "WRITABLE", previous[0]), setattr(state, "OPTS", previous[1])))
        server = app.QuietServer(("127.0.0.1", 0), app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        sha = app.api_file({"hash": ["WIP"], "path": ["README.md"]})["sha"]
        body = json.dumps({"path": "README.md", "content": "x" * 300000 + "\n", "sha": sha}).encode()
        req = Request(f"http://127.0.0.1:{server.server_address[1]}/api/file-save", data=body,
                      headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(req) as response:
            self.assertTrue(json.loads(response.read())["ok"])
        self.assertEqual(len(self.read_bytes("README.md")), 300001)

    def test_diff_page_links_to_editor_for_working_tree_files(self):
        previous = (state.WRITABLE, state.WIP_ENABLED)
        self.addCleanup(setattr, state, "WRITABLE", previous[0])
        self.addCleanup(setattr, state, "WIP_ENABLED", previous[1])
        state.WRITABLE = state.WIP_ENABLED = True
        page = viewer.render_diff_page({"path": ["README.md"], "wip": ["unstaged"]})
        self.assertIn('href="/files?hash=WIP&amp;path=&amp;file=README.md&amp;view=edit"', page)
        page = viewer.render_diff_page({"path": ["src/app.py"], "wip": ["unstaged"]})
        self.assertIn("path=src&amp;file=src%2Fapp.py&amp;view=edit", page)
        self.assertNotIn('class="ebtn"', viewer.render_diff_page({"path": ["gone.txt"], "wip": ["unstaged"]}))
        self.assertNotIn('class="ebtn"', viewer.render_diff_page({"hash": [self.head], "path": ["README.md"]}))
        state.WRITABLE = False
        self.assertNotIn('class="ebtn"', viewer.render_diff_page({"path": ["README.md"], "wip": ["unstaged"]}))

    def test_requires_worktree(self):
        state.HAS_WORKTREE = False
        with self.assertRaises(app.UserError):
            app.api_tree({"hash": ["WIP"]})


class GitWriteApiIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = self.temp_dir.name
        self.previous_repo = state.REPO
        self.previous_wip_enabled = state.WIP_ENABLED
        self.previous_has_worktree = state.HAS_WORKTREE
        state.REPO = self.repo
        state.WIP_ENABLED = True
        state.HAS_WORKTREE = True

        self.git("init", "-q")
        self.git("config", "user.name", "Test User")
        self.git("config", "user.email", "test@example.com")
        self.git("branch", "-M", "main")
        self.write_file("tracked.txt", "initial\n")
        self.git("add", "tracked.txt")
        self.git("commit", "-q", "-m", "initial commit")
        self.head = self.git("rev-parse", "HEAD").strip()

    def tearDown(self):
        state.REPO = self.previous_repo
        state.WIP_ENABLED = self.previous_wip_enabled
        state.HAS_WORKTREE = self.previous_has_worktree
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
        previous_opts = state.OPTS
        previous_writable = state.WRITABLE
        state.OPTS = SimpleNamespace(
            host="127.0.0.1",
            revs=[],
            no_remotes=False,
            no_tags=False,
            rankdir="BT",
            no_count=False,
        )
        state.WRITABLE = True
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

            if shutil.which("dot"):
                with urlopen(f"{base_url}/api/graph") as response:
                    graph = json.loads(response.read())
                self.assertIsInstance(graph["nodes"], dict)
                self.assertIsInstance(graph["edges"], dict)
                self.assertTrue(graph["svg"].startswith("<svg"))
            else:  # Graphviz がない環境: グラフ API はその旨のエラーを返す (他のルートの確認は続ける)
                with self.assertRaises(HTTPError) as error:
                    urlopen(f"{base_url}/api/graph")
                self.assertEqual(error.exception.code, 500)
                self.assertEqual(error.exception.read().decode("utf-8"), app.tr("dot_missing"))
                error.exception.close()

            with urlopen(f"{base_url}/api/head") as response:
                head = json.loads(response.read())
            self.assertEqual(head["branch"], "main")

            request = Request(
                f"{base_url}/api/branch",
                data=json.dumps({"hash": self.head, "name": "http-feature"}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request) as response:
                self.assertTrue(json.loads(response.read())["ok"])
            self.assertEqual(self.git("rev-parse", "refs/heads/http-feature").strip(), self.head)

            with self.assertRaises(HTTPError) as error:
                urlopen(f"{base_url}/missing")
            self.assertEqual(error.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            state.OPTS = previous_opts
            state.WRITABLE = previous_writable

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

    def test_amend_commit_that_git_rm_removed_a_file(self):
        self.git("rm", "-q", "tracked.txt")
        self.git("commit", "-q", "-m", "remove tracked")
        count = self.git("rev-list", "--count", "HEAD").strip()

        result = app.api_wip_commit({"paths": ["tracked.txt"], "message": "remove tracked (amended)", "amend": True})

        self.assertTrue(result["ok"])
        self.assertEqual(self.git("log", "-1", "--format=%s").strip(), "remove tracked (amended)")
        self.assertEqual(self.git("ls-tree", "--name-only", "HEAD").strip(), "")
        self.assertEqual(self.git("rev-list", "--count", "HEAD").strip(), count)  # 新しいコミットは増えない (amend)

    def test_amend_can_bring_back_file_removed_by_git_rm(self):
        self.write_file("other.txt", "other\n")
        self.git("add", "other.txt")
        self.git("rm", "-q", "tracked.txt")
        self.git("commit", "-q", "-m", "add other and remove tracked")

        # tracked.txt のチェックを外すと、git rm がコミットから外れる (ファイルは HEAD に残る)
        app.api_wip_commit({"paths": ["other.txt"], "message": "keep tracked", "amend": True})

        self.assertEqual(self.git("show", "HEAD:tracked.txt"), "initial\n")
        self.assertEqual(self.git("show", "HEAD:other.txt"), "other\n")

    def test_wip_commit_staged_paths_leave_unstaged_changes_alone(self):
        self.write_file("tracked.txt", "staged version\n")
        self.git("add", "tracked.txt")
        self.write_file("tracked.txt", "staged version\nunstaged line\n")

        result = app.api_wip_commit({"paths": [], "staged_paths": ["tracked.txt"], "message": "staged only"})

        self.assertTrue(result["ok"])
        self.assertEqual(self.git("show", "HEAD:tracked.txt"), "staged version\n")
        self.assertEqual(self.read_file("tracked.txt"), "staged version\nunstaged line\n")
        status = app.parse_status(app.read_status_raw())
        self.assertEqual([f["path"] for f in status["unstaged"]], ["tracked.txt"])
        self.assertEqual(status["staged"], [])

    def test_wip_commit_paths_take_working_tree_content_even_when_also_staged(self):
        self.write_file("tracked.txt", "staged version\n")
        self.git("add", "tracked.txt")
        self.write_file("tracked.txt", "staged version\nunstaged line\n")

        app.api_wip_commit({"paths": ["tracked.txt"], "staged_paths": ["tracked.txt"], "message": "everything"})

        self.assertEqual(self.git("show", "HEAD:tracked.txt"), "staged version\nunstaged line\n")

    def test_wip_commit_rejects_staged_paths_that_are_not_staged(self):
        self.write_file("tracked.txt", "unstaged only\n")

        with self.assertRaises(app.UserError):
            app.api_wip_commit({"paths": [], "staged_paths": ["tracked.txt"], "message": "nope"})

        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.head)

    def test_wip_commit_without_any_selection_is_rejected(self):
        with self.assertRaises(app.UserError):
            app.api_wip_commit({"paths": [], "staged_paths": [], "message": "nothing"})

    def test_rm_removes_tracked_file_and_stages_deletion(self):
        result = app.api_rm({"paths": ["tracked.txt"]})

        self.assertTrue(result["ok"])
        self.assertFalse(os.path.exists(os.path.join(self.repo, "tracked.txt")))
        self.assertEqual(self.git("status", "--porcelain").strip(), "D  tracked.txt")

    def test_rm_cached_keeps_working_tree_file(self):
        result = app.api_rm({"paths": ["tracked.txt"], "cached": True})

        self.assertTrue(result["ok"])
        self.assertEqual(self.read_file("tracked.txt"), "initial\n")
        self.assertEqual(self.git("status", "--porcelain").splitlines(), ["D  tracked.txt", "?? tracked.txt"])

    def test_rm_needs_force_for_modified_file(self):
        self.write_file("tracked.txt", "edited\n")

        with self.assertRaises(ValueError):
            app.api_rm({"paths": ["tracked.txt"]})
        self.assertEqual(self.read_file("tracked.txt"), "edited\n")

        app.api_rm({"paths": ["tracked.txt"], "force": True})
        self.assertFalse(os.path.exists(os.path.join(self.repo, "tracked.txt")))

    def test_rm_rejects_untracked_files_and_directories(self):
        self.write_file("untracked.txt", "x\n")
        os.makedirs(os.path.join(self.repo, "dir"), exist_ok=True)
        self.write_file("dir/inner.txt", "x\n")
        self.git("add", "dir/inner.txt")

        for bad in ("untracked.txt", "dir", "missing.txt"):
            with self.assertRaises(app.UserError):
                app.api_rm({"paths": [bad]})
        self.assertEqual(self.read_file("untracked.txt"), "x\n")
        self.assertEqual(self.read_file("tracked.txt"), "initial\n")

    def test_wip_diff_page_has_same_actions_as_wip_pane(self):
        self.write_file("tracked.txt", "working tree change\n")
        previous_writable = state.WRITABLE
        state.WRITABLE = True
        try:
            page = viewer.render_diff_page({"path": ["tracked.txt"], "wip": ["unstaged"]})
        finally:
            state.WRITABLE = previous_writable

        for element in ('id="rbtn"', 'id="sbtn"', 'id="dbtn"'):
            self.assertIn(element, page)
        self.assertIn('"/api/rm"', page)
        self.assertIn('"/api/stash-push"', page)
        self.assertNotIn('id="abtn"', page)
        self.assertNotIn('data-resolve="ours"', page)

    def test_wip_diff_page_shows_resolve_and_abort_while_merging(self):
        self.write_file("tracked.txt", "working tree change\n")
        original = viewer.api_status

        def merging_status(q):
            st = original(q)
            st["merging"] = True
            st["conflicts"] = [{"path": "tracked.txt", "st": "U", "label": "label_conflict"}]
            return st

        previous_writable = state.WRITABLE
        state.WRITABLE = True
        viewer.api_status = merging_status
        try:
            page = viewer.render_diff_page({"path": ["tracked.txt"], "wip": ["conflict"]})
        finally:
            viewer.api_status = original
            state.WRITABLE = previous_writable

        self.assertIn('data-resolve="ours"', page)
        self.assertIn('data-resolve="theirs"', page)
        self.assertIn('id="abtn" data-cmd="merge"', page)
        self.assertIn('id="rbtn"', page)  # git rm は途中でも使える
        self.assertNotIn('id="sbtn"', page)  # stash / discard は途中では出さない
        self.assertNotIn('id="dbtn"', page)

    def test_wip_diff_page_checks_staged_entry_only_by_default(self):
        self.write_file("tracked.txt", "staged version\n")
        self.git("add", "tracked.txt")
        self.write_file("tracked.txt", "staged version\nunstaged line\n")
        previous_writable = state.WRITABLE
        state.WRITABLE = True
        try:
            page = viewer.render_diff_page({"path": ["tracked.txt"], "wip": ["staged"]})
        finally:
            state.WRITABLE = previous_writable

        self.assertRegex(page, r'class="wck" data-kind="staged"[^>]*data-path="tracked.txt" checked>')
        self.assertRegex(page, r'class="wck" data-kind="unstaged"[^>]*data-path="tracked.txt">')
        self.assertIn("staged_paths", page)

    def test_wip_diff_page_has_branch_and_discard_controls(self):
        self.write_file("tracked.txt", "working tree change\n")
        previous_writable = state.WRITABLE
        state.WRITABLE = True
        try:
            page = viewer.render_diff_page({"path": ["tracked.txt"], "wip": ["unstaged"]})
        finally:
            state.WRITABLE = previous_writable

        self.assertIn('id="cbranch"', page)
        self.assertIn('id="dbtn"', page)
        self.assertIn("branch: branch", page)
        self.assertIn('fetch("/api/discard"', page)

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

    def make_branch_changing_tracked_file(self, name="other", content="other version\n"):
        """main を動かさずに、tracked.txt を変えたブランチを作る (main へのマージは fast-forward になる)。"""
        self.git("checkout", "-q", "-b", name)
        self.write_file("tracked.txt", content)
        self.git("commit", "-a", "-q", "-m", "other version")
        tip = self.git("rev-parse", "HEAD").strip()
        self.git("checkout", "-q", "main")
        return tip

    def test_merge_reports_blocking_file_and_resolves_with_stash_or_discard(self):
        tip = self.make_branch_changing_tracked_file()
        self.write_file("tracked.txt", "local change\n")

        blocked = app.api_merge({"hash": tip})

        self.assertTrue(blocked["blocked"])
        self.assertEqual(blocked["modified"], ["tracked.txt"])
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.head)
        self.assertEqual(self.read_file("tracked.txt"), "local change\n")

        stashed = app.api_merge({"hash": tip, "on_conflict": "stash"})

        self.assertTrue(stashed["ok"])
        self.assertIn("stash@{0}", stashed["message"])
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), tip)
        self.assertEqual(self.read_file("tracked.txt"), "other version\n")
        self.assertEqual(app.api_stash_list({})["total"], 1)

    def test_merge_discard_drops_only_blocking_files(self):
        tip = self.make_branch_changing_tracked_file()
        self.write_file("tracked.txt", "local change\n")
        self.write_file("unrelated.txt", "keep me\n")

        result = app.api_merge({"hash": tip, "on_conflict": "discard"})

        self.assertTrue(result["ok"])
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), tip)
        self.assertEqual(self.read_file("tracked.txt"), "other version\n")
        self.assertEqual(self.read_file("unrelated.txt"), "keep me\n")  # 邪魔していないファイルは触らない
        self.assertEqual(app.api_stash_list({})["total"], 0)

    def test_merge_reports_blocking_untracked_file(self):
        self.git("checkout", "-q", "-b", "adds-file")
        self.write_file("new.txt", "from branch\n")
        self.git("add", "new.txt")
        self.git("commit", "-q", "-m", "add new.txt")
        tip = self.git("rev-parse", "HEAD").strip()
        self.git("checkout", "-q", "main")
        self.write_file("new.txt", "untracked local\n")

        blocked = app.api_merge({"hash": tip})
        self.assertTrue(blocked["blocked"])
        self.assertEqual(blocked["untracked"], ["new.txt"])

        result = app.api_merge({"hash": tip, "on_conflict": "discard"})
        self.assertTrue(result["ok"])
        self.assertEqual(self.read_file("new.txt"), "from branch\n")

    def test_merge_rejects_unknown_on_conflict(self):
        tip = self.make_branch_changing_tracked_file()
        with self.assertRaises(ValueError):
            app.api_merge({"hash": tip, "on_conflict": "bogus"})

    def test_pull_reports_blocking_file_and_resolves_with_stash_or_discard(self):
        origin = tempfile.TemporaryDirectory()
        self.addCleanup(origin.cleanup)
        subprocess.check_call(["git", "clone", "-q", self.repo, os.path.join(origin.name, "up")])
        up = os.path.join(origin.name, "up")
        for key, value in (("user.name", "Test User"), ("user.email", "test@example.com")):
            subprocess.check_call(["git", "-C", up, "config", key, value])
        with open(os.path.join(up, "tracked.txt"), "w", encoding="utf-8") as f:
            f.write("upstream version\n")
        subprocess.check_call(["git", "-C", up, "commit", "-a", "-q", "-m", "upstream change"])
        up_tip = subprocess.check_output(["git", "-C", up, "rev-parse", "HEAD"], text=True).strip()
        self.git("remote", "add", "origin", up)
        self.git("fetch", "-q", "origin")
        self.git("branch", "-u", "origin/main")

        for mode in ("ff-only", "merge", "rebase"):
            self.git("reset", "-q", "--hard", self.head)
            self.write_file("tracked.txt", "local change\n")

            blocked = app.api_pull({"mode": mode})
            self.assertTrue(blocked.get("blocked"), mode)
            self.assertEqual(blocked["modified"], ["tracked.txt"], mode)
            self.assertEqual(self.read_file("tracked.txt"), "local change\n")

            result = app.api_pull({"mode": mode, "on_conflict": "stash"})
            self.assertTrue(result["ok"], mode)
            self.assertEqual(self.git("rev-parse", "HEAD").strip(), up_tip, mode)
            self.assertEqual(self.read_file("tracked.txt"), "upstream version\n", mode)
            self.git("stash", "drop", "-q")

            self.git("reset", "-q", "--hard", self.head)
            self.write_file("tracked.txt", "local change\n")
            result = app.api_pull({"mode": mode, "on_conflict": "discard"})
            self.assertTrue(result["ok"], mode)
            self.assertEqual(self.git("rev-parse", "HEAD").strip(), up_tip, mode)
            self.assertEqual(self.read_file("tracked.txt"), "upstream version\n", mode)
            self.assertEqual(app.api_stash_list({})["total"], 0)

    def test_stash_push_stashes_everything_including_untracked_by_default(self):
        self.write_file("tracked.txt", "edited\n")
        self.write_file("new.txt", "untracked\n")

        result = app.api_stash_push({"message": "wip save"})

        self.assertTrue(result["ok"])
        self.assertEqual(self.git("status", "--porcelain"), "")
        listed = app.api_stash_list({})
        self.assertEqual(listed["total"], 1)
        self.assertIn("wip save", listed["commits"][0][4])
        self.assertEqual(self.git("show", "--format=", "--name-only", "stash@{0}^3").strip(), "new.txt")

    def test_stash_push_can_leave_untracked_files_and_unselected_files(self):
        self.write_file("tracked.txt", "edited\n")
        self.write_file("other.txt", "other\n")
        self.git("add", "other.txt")
        self.git("commit", "-q", "-m", "add other")
        self.write_file("other.txt", "other edited\n")
        self.write_file("new.txt", "untracked\n")

        app.api_stash_push({"untracked": False, "paths": ["tracked.txt"]})

        self.assertEqual(self.read_file("tracked.txt"), "initial\n")
        self.assertEqual(self.read_file("other.txt"), "other edited\n")  # チェックしていないファイルは残る
        self.assertEqual(self.read_file("new.txt"), "untracked\n")
        self.assertEqual(app.api_stash_list({})["total"], 1)

    def test_stash_push_rejects_nothing_to_stash_and_unknown_paths(self):
        with self.assertRaises(ValueError) as nothing:
            app.api_stash_push({})
        self.assertEqual(str(nothing.exception), app.tr("stash_nothing"))
        self.write_file("tracked.txt", "edited\n")
        with self.assertRaises(ValueError) as unknown:
            app.api_stash_push({"paths": ["../outside.txt"]})
        self.assertIn("outside.txt", str(unknown.exception))
        self.assertEqual(app.api_stash_list({})["total"], 0)

    def test_stash_push_is_refused_during_merge(self):
        tip = self.make_branch_changing_tracked_file(content="their version\n")
        self.write_file("tracked.txt", "main version\n")
        self.git("commit", "-a", "-q", "-m", "main version")
        self.assertTrue(app.api_merge({"hash": tip})["conflict"])
        with self.assertRaises(ValueError) as refused:
            app.api_stash_push({})
        self.assertEqual(str(refused.exception), app.tr("stash_push_blocked"))

    def test_stash_apply_reports_blocking_file_and_force_overwrites_it(self):
        self.write_file("tracked.txt", "stashed content\n")
        self.git("stash", "push", "-q", "-m", "test stash")
        stash_hash = self.git("rev-parse", "refs/stash").strip()
        self.write_file("tracked.txt", "local change\n")

        blocked = app.api_stash({"hash": stash_hash, "op": "apply"})

        self.assertTrue(blocked["blocked"])
        self.assertEqual(blocked["modified"], ["tracked.txt"])
        self.assertEqual(self.read_file("tracked.txt"), "local change\n")

        result = app.api_stash({"hash": stash_hash, "op": "pop", "on_conflict": "discard"})

        self.assertTrue(result["ok"])
        self.assertEqual(self.read_file("tracked.txt"), "stashed content\n")
        self.assertEqual(app.api_stash_list({})["total"], 0)  # pop なので stash は消える

    def test_stash_apply_force_overwrites_blocking_untracked_file(self):
        self.write_file("new.txt", "from stash\n")
        self.git("stash", "push", "-q", "-u", "-m", "with untracked")
        stash_hash = self.git("rev-parse", "refs/stash").strip()
        self.write_file("new.txt", "local untracked\n")

        blocked = app.api_stash({"hash": stash_hash, "op": "apply"})

        self.assertTrue(blocked["blocked"])
        self.assertEqual(blocked["untracked"], ["new.txt"])
        self.assertEqual(self.read_file("new.txt"), "local untracked\n")

        result = app.api_stash({"hash": stash_hash, "op": "apply", "on_conflict": "discard"})

        self.assertTrue(result["ok"])
        self.assertEqual(self.read_file("new.txt"), "from stash\n")

    def test_stash_apply_rejects_stash_mode(self):
        self.write_file("tracked.txt", "stashed content\n")
        self.git("stash", "push", "-q")
        stash_hash = self.git("rev-parse", "refs/stash").strip()
        with self.assertRaises(ValueError):
            app.api_stash({"hash": stash_hash, "op": "apply", "on_conflict": "stash"})

    def make_merged_and_deleted_branch(self):
        """initial - B - C(main) と B - F(feature) を merge し、feature を削除する。(分岐元 B、マージ M)"""
        self.write_file("b.txt", "b\n")
        self.git("add", "b.txt")
        self.git("commit", "-q", "-m", "B")
        b = self.git("rev-parse", "HEAD").strip()
        self.git("checkout", "-q", "-b", "feature")
        self.write_file("f.txt", "f\n")
        self.git("add", "f.txt")
        self.git("commit", "-q", "-m", "F")
        self.git("checkout", "-q", "main")
        self.write_file("c.txt", "c\n")
        self.git("add", "c.txt")
        self.git("commit", "-q", "-m", "C")
        self.git("merge", "-q", "--no-ff", "-m", "M", "feature")
        self.git("branch", "-q", "-d", "feature")
        return b, self.git("rev-parse", "HEAD").strip()

    @unittest.skipUnless(shutil.which("dot"), "Graphviz dot is not installed")
    def test_build_graph_draws_adjacent_commits_dashed_and_omitted_solid(self):
        self.git("branch", "x")
        self.write_file("b.txt", "b\n")
        self.git("add", "b.txt")
        self.git("commit", "-q", "-m", "B")
        b = self.git("rev-parse", "HEAD").strip()

        dot, nodes, edges = app.build_graph(self.graph_opts())
        self.assertIn(f'"{self.head}" -> "{b}" [style=dashed];', dot)  # 間にコミットがない

        self.write_file("c.txt", "c\n")
        self.git("add", "c.txt")
        self.git("commit", "-q", "-m", "C")
        c = self.git("rev-parse", "HEAD").strip()
        dot, nodes, edges = app.build_graph(self.graph_opts())
        self.assertIn(f'"{self.head}" -> "{c}";', dot)  # 間に B が省略されるので実線

    def graph_opts(self, **extra):
        return SimpleNamespace(revs=[], no_remotes=False, no_tags=False, include_tips=False, rankdir="BT",
                               font="Helvetica", no_count=False, full_layout=False, **extra)

    @unittest.skipUnless(shutil.which("dot"), "Graphviz dot is not installed")
    def test_build_graph_hides_merge_of_deleted_branch_by_default(self):
        b, m = self.make_merged_and_deleted_branch()

        dot, nodes, edges = app.build_graph(self.graph_opts())

        self.assertEqual(set(nodes), {self.head, m})
        self.assertEqual(set(edges), {f"{self.head}->{m}"})
        self.assertEqual(dot.count(f'"{self.head}" -> "{m}"'), 1)

    @unittest.skipUnless(shutil.which("dot"), "Graphviz dot is not installed")
    def test_build_graph_show_merges_adds_merge_commit_and_fork_point_with_two_lines(self):
        b, m = self.make_merged_and_deleted_branch()

        dot, nodes, edges = app.build_graph(self.graph_opts(show_merges=True))

        self.assertEqual(set(nodes), {self.head, b, m})
        self.assertEqual({k for k in edges if "->" in k}, {f"{self.head}->{b}", f"{b}->{m}"})
        lines = [line for line in dot.splitlines() if line.startswith(f'  "{b}" -> "{m}"')]
        self.assertEqual(len(lines), 2)  # 第1親 (C 経由) と第2親 (F 経由) の 2 本
        # 間に C / F が 1 つずつ省略されているので実線 (個数のラベルは付けない)。省略がなければ破線
        self.assertTrue(all("label" not in line and "style" not in line for line in lines))
        adjacent = [line for line in dot.splitlines() if line.startswith(f'  "{self.head}" -> "{b}"')]
        self.assertEqual(len(adjacent), 1)
        self.assertIn("style=dashed", adjacent[0])
        self.assertEqual(sum(MERGE_COLOR in line for line in lines), 1)  # マージされてきた側だけ色が変わる
        self.assertEqual(dot.count('label="", width=0.14'), 1)  # 分岐元 B だけが文字なしの点 (M は main の先端なのでそのまま)
        svg = subprocess.check_output(["dot", "-Tsvg"], input=dot.encode("utf-8"))
        self.assertIn(b"<svg", svg)

    @unittest.skipUnless(shutil.which("dot"), "Graphviz dot is not installed")
    def test_graph_payload_merges_param_switches_between_cached_graphs(self):
        b, m = self.make_merged_and_deleted_branch()
        previous = state.OPTS
        state.OPTS = self.graph_opts()
        try:
            plain = app.graph_payload({})
            merged = app.graph_payload({"merges": ["1"]})
            plain_again = app.graph_payload(None)
        finally:
            state.OPTS = previous

        self.assertNotIn(b, plain["nodes"])
        self.assertIn(b, merged["nodes"])
        self.assertEqual(set(plain_again["nodes"]), set(plain["nodes"]))
        self.assertIn("<svg", merged["svg"])

    @unittest.skipUnless(shutil.which("dot"), "Graphviz dot is not installed")
    def test_edge_log_excludes_both_endpoints(self):
        b, m = self.make_merged_and_deleted_branch()
        c, f = self.git("rev-parse", "HEAD^1").strip(), self.git("rev-parse", "HEAD^2").strip()
        previous = state.OPTS
        state.OPTS = self.graph_opts()
        try:
            payload = app.graph_payload({"merges": ["1"]})
        finally:
            state.OPTS = previous

        # マージの 2 本の線には親ごとの id が付き、どちらの側かが分かる
        self.assertEqual(payload["edges"][f"m_{b}_{m}_{c}"]["via"], c)
        self.assertEqual(payload["edges"][f"m_{b}_{m}_{f}"]["via"], f)
        self.assertIn(f'id="m_{b}_{m}_{f}"', payload["svg"])
        # 線の間のコミットだけ (B も M も含まない)
        side = app.api_log({"rev": [f"{b}..{f}"]})
        self.assertEqual([x[0] for x in side["commits"]], [f])
        # A..B^@ は B の親たちから A の祖先を除いたもの (B 自身は含まない)
        between = app.api_log({"rev": [f"{b}..{m}^@"], "graph": ["1"]})
        self.assertEqual({x[0] for x in between["commits"]}, {c, f})
        self.assertEqual(between["total"], 2)
        self.assertEqual(app.api_log({"rev": [f"{b}..{c}^@"]})["commits"], [])  # 間に何もなければ空

    def test_merge_bases_returns_best_common_ancestor_only(self):
        parents = {"d": ["b", "c"], "b": ["a"], "c": ["a"], "a": ["r"], "r": []}
        pos = {h: i for i, h in enumerate(["d", "c", "b", "a", "r"])}

        self.assertEqual(app.merge_bases(parents, pos, "b", "c"), ["a"])
        self.assertEqual(app.merge_bases(parents, pos, "b", "d"), ["b"])  # 片方がもう片方の祖先
        self.assertEqual(app.merge_bases(parents, pos, "c", "c"), ["c"])

    @unittest.skipUnless(shutil.which("dot"), "Graphviz dot is not installed")
    def test_build_graph_shows_stashes_as_children_of_their_base_commit(self):
        self.write_file("second.txt", "second\n")
        self.git("add", "second.txt")
        self.git("commit", "-q", "-m", "second")
        self.write_file("tracked.txt", "edited\n")
        self.write_file("new.txt", "untracked\n")
        self.git("stash", "push", "-q", "-u", "-m", "my stash")  # 親に index 用・管理外ファイル用のコミットを持つ
        stash_hash = self.git("rev-parse", "refs/stash").strip()
        second = self.git("rev-parse", "HEAD").strip()
        opts = SimpleNamespace(revs=[], no_remotes=False, no_tags=False, include_tips=True, rankdir="BT",
                               font="Helvetica", no_count=False, full_layout=False)

        dot, nodes, edges = app.build_graph(opts)

        self.assertIn(stash_hash, nodes)
        self.assertEqual(nodes[stash_hash]["refs"], [{"name": "stash@{0}", "kind": "stash"}])
        self.assertIn(f"{second}->{stash_hash}", edges)
        self.assertEqual(set(nodes), {self.head, second, stash_hash})  # stash の index / untracked 用コミットは出さない

        opts.no_stash = True
        _, nodes_without, _ = app.build_graph(opts)
        self.assertNotIn(stash_hash, nodes_without)

    def read_file(self, path):
        with open(os.path.join(self.repo, path), encoding="utf-8") as file:
            return file.read()


if __name__ == "__main__":
    unittest.main()
