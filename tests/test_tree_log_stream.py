import json
import os
import subprocess
import tempfile
import threading
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.request import urlopen

import git_graph_server as app
from git_graph import app_state as state
from git_graph import pages
from git_graph.query import files as qf


def git(*args, cwd=None):
    return subprocess.check_output(["git", *args], cwd=cwd, text=True, encoding="utf-8").strip()


def forbid_git_log():
    """git log を起動したら失敗にする (ほかの git の呼び出しは、そのまま動かす)。調べ直さずに、覚えていた結果を返したことの確認用。"""
    real = subprocess.Popen

    def guard(args, *a, **kw):
        if isinstance(args, (list, tuple)) and "log" in args:
            raise AssertionError("git log を起動してはいけない: " + " ".join(map(str, args[:6])))
        return real(args, *a, **kw)
    return mock.patch.object(qf.subprocess, "Popen", guard)


class RepoCase(unittest.TestCase):
    """src/ は最初のコミットのまま、README.md は最後のコミットで変わるリポジトリ (古い項目と新しい項目がある)。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = os.path.realpath(self.tmp.name)
        git("init", "-q", self.repo)
        git("-C", self.repo, "config", "user.name", "Test User")
        git("-C", self.repo, "config", "user.email", "test@example.com")
        self.write("src/app.py", "print(1)\n")
        self.write("README.md", "one\n")
        self.commit("first")
        self.first = git("-C", self.repo, "rev-parse", "HEAD")
        self.write("README.md", "two\n")
        self.commit("second")
        self.write("README.md", "three\n")
        self.commit("third")
        self.head = git("-C", self.repo, "rev-parse", "HEAD")
        self.saved = {n: getattr(state, n) for n in ("REPO", "HAS_WORKTREE", "WIP_ENABLED")}
        state.REPO, state.HAS_WORKTREE, state.WIP_ENABLED = self.repo, True, True
        self.addCleanup(lambda: [setattr(state, k, v) for k, v in self.saved.items()])
        qf.clear_tree_log_cache()
        self.addCleanup(qf.clear_tree_log_cache)

    def write(self, name, text):
        path = os.path.join(self.repo, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def commit(self, msg):
        git("-C", self.repo, "add", "-A")
        git("-C", self.repo, "commit", "-q", "-m", msg)


class TreeLogEventsTests(RepoCase):
    def test_events_carry_found_entries_then_a_final_summary(self):
        events = list(qf.tree_log_events(self.head))
        merged = {}
        for ev in events[:-1]:
            self.assertEqual(list(ev), ["commits"])  # 途中の知らせは、見つかった項目だけ
            merged.update(ev["commits"])
        self.assertEqual({n: c[0] for n, c in merged.items()}, {"README.md": self.head, "src": self.first})
        self.assertEqual(events[-1], {"hash": self.head, "path": "", "complete": True})

    def test_tree_log_still_returns_one_complete_result(self):
        res = qf.tree_log(self.head)
        self.assertEqual(sorted(res), ["commits", "complete", "hash", "path"])
        self.assertTrue(res["complete"])
        self.assertEqual(res["commits"]["src"][0], self.first)
        self.assertEqual(qf.tree_log(self.head, "src")["commits"]["app.py"][0], self.first)

    def test_slow_entries_do_not_hold_back_the_fast_ones(self):
        with mock.patch.object(qf, "TREE_LOG_EVENT_INTERVAL", 0), mock.patch.object(qf, "TREE_LOG_READ_SIZE", 32):
            gen = qf.tree_log_events(self.head)
            first = next(gen)
            self.assertIn("README.md", first["commits"])  # 新しい項目は、古い項目を探し終える前に出る
            self.assertNotIn("src", first["commits"])
            rest = list(gen)
        self.assertIn("src", {n for ev in rest for n in ev.get("commits", {})})
        self.assertTrue(rest[-1]["complete"])

    def test_closing_the_generator_stops_git_log(self):
        procs = []
        real = subprocess.Popen

        def spy(args, *a, **kw):
            p = real(args, *a, **kw)
            if isinstance(args, (list, tuple)) and "log" in args:
                procs.append(p)
            return p
        with mock.patch.object(qf.subprocess, "Popen", spy), mock.patch.object(qf, "TREE_LOG_EVENT_INTERVAL", 0), \
                mock.patch.object(qf, "TREE_LOG_READ_SIZE", 32):
            gen = qf.tree_log_events(self.head)
            next(gen)  # 古い項目 (src) がまだ見つからない途中
            gen.close()
        self.assertEqual(len(procs), 1)
        self.assertIsNotNone(procs[0].poll())  # 終わっている (kill されて回収済み)
        self.assertEqual(qf._TREE_LOG_CACHE, {})  # 途中でやめた結果は覚えない

    def test_finished_results_are_remembered(self):
        first = qf.tree_log(self.head)
        with forbid_git_log():
            again = qf.tree_log(self.head)
        self.assertEqual(again, first)

    def test_working_tree_reuses_the_head_result(self):
        qf.tree_log(self.head)
        self.write("untracked.txt", "x\n")  # 未追跡の項目は、最終コミットを探さない
        with forbid_git_log():
            res = qf.tree_log("WIP")
        self.assertEqual(sorted(res["commits"]), ["README.md", "src"])
        self.assertTrue(res["complete"])

    def test_entries_not_in_the_cache_are_searched_again(self):
        qf.tree_log(self.head)
        self.write("README2.md", "y\n")
        self.commit("fourth")
        new_head = git("-C", self.repo, "rev-parse", "HEAD")
        res = qf.tree_log(new_head)  # コミットが変わったので別のキー
        self.assertEqual(res["commits"]["README2.md"][0], new_head)

    def test_incomplete_results_are_not_remembered(self):
        with mock.patch.object(qf, "TREE_LOG_MAX_COMMITS", 1):
            self.assertFalse(qf.tree_log(self.head)["complete"])
        self.assertTrue(qf.tree_log(self.head)["complete"])  # 上限でやめた結果を、完全な結果として返さない
        self.assertEqual(qf.tree_log(self.head)["commits"]["src"][0], self.first)

    def test_cache_is_bounded(self):
        with mock.patch.object(qf, "TREE_LOG_CACHE_SIZE", 2):
            for path in ("", "src", "src"):
                qf.tree_log(self.head, path)
            self.write("a/b.txt", "z\n")
            self.commit("add a")
            h2 = git("-C", self.repo, "rev-parse", "HEAD")
            qf.tree_log(h2, "a")
            self.assertLessEqual(len(qf._TREE_LOG_CACHE), 2)


class SilentGitLog:
    """出力が止まったままの git log (パスで絞った探索で、変更のないコミットが続いている状態) の代わり。kill すると読み取りが終わる。"""

    def __init__(self):
        self.killed = threading.Event()
        self.stdout = self

    def read(self, size):
        self.killed.wait()
        return b""

    def kill(self):
        self.killed.set()

    def close(self):
        pass

    def wait(self):
        return 0

    def poll(self):
        return 0 if self.killed.is_set() else None


class TreeLogHeartbeatTests(RepoCase):
    def silent_log(self):
        proc = SilentGitLog()
        real = subprocess.Popen

        def fake(args, *a, **kw):
            return proc if isinstance(args, (list, tuple)) and "log" in args else real(args, *a, **kw)
        return proc, mock.patch.object(qf.subprocess, "Popen", fake)

    def test_a_signal_is_sent_while_git_log_prints_nothing(self):
        proc, patched = self.silent_log()
        with patched, mock.patch.object(qf, "TREE_LOG_HEARTBEAT", 0.01):
            gen = qf.tree_log_events(self.head)
            self.assertEqual([next(gen), next(gen)], [{}, {}])  # 見つからない間も、合図が来る
            gen.close()
        self.assertTrue(proc.killed.is_set())  # 受け取る側がやめたら、git log も止める

    def test_the_time_limit_applies_even_when_git_log_prints_nothing(self):
        proc, patched = self.silent_log()
        with patched, mock.patch.object(qf, "TREE_LOG_HEARTBEAT", 0.01), mock.patch.object(qf, "TREE_LOG_MAX_SECONDS", 0.03):
            events = list(qf.tree_log_events(self.head))
        self.assertEqual(events[-1], {"hash": self.head, "path": "", "complete": False})
        self.assertTrue(proc.killed.is_set())
        self.assertEqual(qf._TREE_LOG_CACHE, {})  # 上限でやめた結果は覚えない

    def test_tree_log_ignores_the_signals(self):
        proc, patched = self.silent_log()
        with patched, mock.patch.object(qf, "TREE_LOG_HEARTBEAT", 0.01), mock.patch.object(qf, "TREE_LOG_MAX_SECONDS", 0.03):
            res = qf.tree_log(self.head)
        self.assertEqual((res["commits"], res["complete"]), ({}, False))


class TreeLogStreamHttpTests(RepoCase):
    def setUp(self):
        super().setUp()
        httpd = app.QuietServer(("127.0.0.1", 0), app.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        self.base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def test_stream_sends_one_json_object_per_line(self):
        with urlopen(f"{self.base}/api/tree-log?hash={self.head}&stream=1") as res:
            self.assertEqual(res.headers["Content-Type"], "application/x-ndjson; charset=utf-8")
            self.assertEqual(res.headers["Cache-Control"], "no-store")
            lines = res.read().decode("utf-8").strip().split("\n")
        events = [json.loads(line) for line in lines]
        self.assertEqual(events[-1], {"hash": self.head, "path": "", "complete": True})
        names = {n for ev in events[:-1] for n in ev["commits"]}
        self.assertEqual(names, {"README.md", "src"})

    def test_without_stream_the_plain_json_api_is_unchanged(self):
        with urlopen(f"{self.base}/api/tree-log?hash={self.head}") as res:
            body = json.loads(res.read())
        self.assertEqual(sorted(body), ["commits", "complete", "hash", "path"])

    def test_errors_before_the_first_event_are_normal_error_responses(self):
        with self.assertRaises(HTTPError) as cm:
            urlopen(f"{self.base}/api/tree-log?hash={self.head}&path=../x&stream=1")
        self.assertEqual(cm.exception.code, 400)
        cm.exception.close()

    def test_a_client_that_hangs_up_does_not_break_the_server(self):
        import socket
        host, port = self.base.rsplit(":", 1)[0].split("//")[1], int(self.base.rsplit(":", 1)[1])
        with mock.patch.object(qf, "TREE_LOG_EVENT_INTERVAL", 0):
            s = socket.create_connection((host, port))
            s.sendall(f"GET /api/tree-log?hash={self.head}&stream=1 HTTP/1.0\r\n\r\n".encode())
            s.recv(10)
            s.close()
        with urlopen(f"{self.base}/api/tree-log?hash={self.head}") as res:  # そのあとも普通に応答する
            self.assertTrue(json.loads(res.read())["complete"])


class FilesPageUsesTheStreamTests(unittest.TestCase):
    def test_directory_view_fills_rows_as_they_arrive_and_cancels_stale_requests(self):
        page = pages.render_files_page()
        self.assertIn("async function apiStream(path, params, onEvent, signal)", page)
        self.assertIn('apiStream("/api/tree-log", {hash: hash, path: path}', page)
        self.assertIn("if (treeLogAbort) treeLogAbort.abort();", page)  # 別のディレクトリ / ログへ移るとき
        self.assertEqual(page.count("if (treeLogAbort) treeLogAbort.abort();"), 2)  # showDir と showDirLog
        self.assertIn("ev.complete === false", page)  # 上限でやめたときの案内は、これまでどおり


if __name__ == "__main__":
    unittest.main()
