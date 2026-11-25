import json
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from types import SimpleNamespace

from git_graph import app_state as state
from git_graph import prefs as prefs_mod
from git_graph.graph import CLUSTER_MIN_NODES, DEFAULT_MERGE_MAX_NODES


class PrefsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = self.tmp.name
        subprocess.check_call(["git", "init", "-q", self.repo])
        self.saved = {
            "REPO": state.REPO, "NEED_REPO": state.NEED_REPO, "OPTS": state.OPTS,
            "WORKTREE_PREFS": state.WORKTREE_PREFS, "WORKTREE_PREFS_PATH": state.WORKTREE_PREFS_PATH,
        }
        state.REPO = self.repo
        state.NEED_REPO = False
        state.OPTS = SimpleNamespace(
            cluster_min_nodes=CLUSTER_MIN_NODES,
            merge_max_nodes=DEFAULT_MERGE_MAX_NODES,
            full_layout=False,
            no_count=False,
        )
        state.WORKTREE_PREFS = {}
        state.WORKTREE_PREFS_PATH = ""

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(state, k, v)

    def test_load_applies_graph_and_ui_keys(self):
        path = os.path.join(self.repo, ".git", "git-graph.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "editor_vim": True,
                "show_merges": True,
                "cluster_min_nodes": 123,
                "full_layout": True,
            }, f)
        prefs = prefs_mod.load_into_state()
        self.assertTrue(prefs["editor_vim"])
        self.assertEqual(state.OPTS.cluster_min_nodes, 123)
        self.assertTrue(state.OPTS.full_layout)
        self.assertTrue(path.endswith("git-graph.json"))
        self.assertEqual(state.WORKTREE_PREFS_PATH, path)

    def test_merge_prefs_writes_file(self):
        written = prefs_mod.merge_prefs({"editor_vim": True, "merge_max_nodes": 50})
        self.assertTrue(written["editor_vim"])
        self.assertEqual(state.OPTS.merge_max_nodes, 50)
        path = prefs_mod.prefs_path()
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.assertEqual(data["merge_max_nodes"], 50)
        self.assertTrue(data["editor_vim"])

    def test_api_get_and_post(self):
        r = prefs_mod.api_prefs_get()
        self.assertEqual(r["prefs"], {})
        r = prefs_mod.api_prefs_post({"prefs": {"editor_wrap": False, "show_merges": True}})
        self.assertTrue(r["ok"])
        self.assertFalse(r["prefs"]["editor_wrap"])
        self.assertTrue(r["prefs"]["show_merges"])

    def test_api_post_include_opts_writes_graph_opts(self):
        state.OPTS.cluster_min_nodes = 777
        state.OPTS.merge_max_nodes = 888
        r = prefs_mod.api_prefs_post({"prefs": {"editor_vim": True}, "include_opts": True})
        self.assertTrue(r["ok"])
        self.assertEqual(r["prefs"]["cluster_min_nodes"], 777)
        self.assertEqual(r["prefs"]["merge_max_nodes"], 888)
        self.assertTrue(r["prefs"]["editor_vim"])
