import os
import re
import threading
import unittest
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import git_graph_server as app
from git_graph import app_state as state
from git_graph.i18n import client_translations

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "git_graph", "static")
# Python 側で組み立てず、そのままのテンプレートとして i18n.py が読むもの (メイン画面・/files・/merge の HTML、全画面共通のメニューバーの HTML、リポジトリ未選択のときの設定画面)
NOT_EMBEDDED = {os.path.join("graph", "index.html"), os.path.join("files", "files.html"), os.path.join("merge", "merge.html"),
                os.path.join("common", "menubar.html"), os.path.join("setup", "setup.html")}

def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class StaticAssetTests(unittest.TestCase):
    """static/ のファイルと、各ページ・pages.py の配信、DEVELOPMENT.md の説明が食い違わないこと。
    読み込まれないファイルに CSS を足しても、ページに効かない (そのことで画面が崩れたことがある)。"""

    def assets(self):
        out = []
        for base, dirs, files in os.walk(STATIC):
            dirs[:] = [d for d in dirs if d != "vendor" and d != "__pycache__"]
            out += [os.path.relpath(os.path.join(base, f), STATIC) for f in files]
        return sorted(out)

    def referenced(self):
        """/static/ 下の .js / .css を参照する URL の集合。各ページのテンプレートと、pages.py / server.py に書かれた <script src> / <link href> / script_tag()。"""
        urls = set()
        templates = [read("git_graph", "static", *name.split("/"))
                     for name in ("graph/index.html", "files/files.html", "merge/merge.html", "setup/setup.html")]
        for src in templates + [read("git_graph", "pages.py"), read("git_graph", "server.py")]:
            urls |= {m for m in re.findall(r"/static/([\w./-]+\.(?:js|css))", src) if m != "i18n.js"}
            urls |= set(re.findall(r'script_tag\("([\w./-]+\.(?:js|css))"\)', src))
        return urls

    def test_pages_reference_existing_files(self):
        for rel in self.referenced():
            self.assertTrue(os.path.isfile(os.path.join(STATIC, *rel.split("/"))), f"{rel} が static/ に無い")

    def test_every_asset_is_read_somewhere(self):
        refs = self.referenced()  # ページが <script src> / <link> で読むもの
        dynamic = set()
        for src in (read("git_graph", "pages.py"), read("git_graph", "server.py")):  # pages.py が差し込む (文言の場所など、URL の形では立てないもの)
            dynamic |= set(re.findall(r'read_asset\("([\w./-]+)"\)', src))
        i18n_src = read("git_graph", "i18n.py")
        dynamic |= {"/".join(m) for m in re.findall(r'STATIC_DIR / "(\w+)" / "([\w.-]+)"', i18n_src)}  # テンプレート (i18n.py)
        for name in self.assets():
            rel = name.replace(os.sep, "/")
            self.assertTrue(rel in refs or rel in dynamic,
                            f"{name} はどこからも読まれていない (使わないファイルは消す)")

    def test_pages_py_only_reads_assets_that_exist(self):
        for name in re.findall(r'read_asset\("([^"]+)"\)', read("git_graph", "pages.py")):
            self.assertTrue(os.path.isfile(os.path.join(STATIC, *name.split("/"))), name)

    def test_development_md_matches_the_assets(self):
        doc = read("DEVELOPMENT.md")
        for name in self.assets() + sorted(NOT_EMBEDDED):
            rel = name.replace(os.sep, "/")
            self.assertTrue(rel in doc or rel.rsplit("/", 1)[0] + "/" in doc,
                            f"{name} が DEVELOPMENT.md に載っていない")  # assertIn は失敗時に文書全体を出してしまう
        for name in set(re.findall(r"`(?:git_graph/static/)?([\w/]+\.(?:css|js|html))`", doc)):
            if name.startswith(("tools/", "tests/")):  # ビルド元 (tools/merge_editor) と tests 下は static に無い
                continue
            if name.startswith("/"):  # URL は static 下のパスではない (配信経路は StaticDeliveryTests が見る)
                continue
            if name.startswith("static/"):  # `static/setup/setup.html` のような言及
                name = name[len("static/"):]
            self.assertTrue(os.path.isfile(os.path.join(STATIC, *name.split("/"))), f"DEVELOPMENT.md にある {name} が static/ に無い")

class StaticDeliveryTests(unittest.TestCase):
    """/static/<path> の汎用配信。ETag での 304、静的でないものや外への脱出の拒否。"""

    def setUp(self):
        saved = (state.NEED_REPO, state.OPTS)
        state.NEED_REPO, state.OPTS = False, SimpleNamespace(host="127.0.0.1", port=0)
        self.addCleanup(setattr, state, "NEED_REPO", saved[0])
        self.addCleanup(setattr, state, "OPTS", saved[1])
        httpd = app.QuietServer(("127.0.0.1", 0), app.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        self.base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def get(self, path, headers=None):
        req = Request(self.base + path, headers=headers or {})
        try:
            with urlopen(req) as res:
                return res.status, dict(res.headers), res.read()
        except HTTPError as e:
            with e:
                return e.code, dict(e.headers), e.read()

    def test_serves_a_static_file_with_etag(self):
        st, headers, body = self.get("/static/common/toast.js")
        self.assertEqual(st, 200)
        self.assertTrue(headers["Content-Type"].startswith("application/javascript"))
        self.assertEqual(headers["Cache-Control"], "no-cache")
        self.assertTrue(headers["ETag"])
        with open(os.path.join(STATIC, "common", "toast.js"), "rb") as f:
            self.assertEqual(body, f.read())

    def test_matching_etag_returns_304(self):
        _, headers, _ = self.get("/static/common/toast.js")
        st, _, body = self.get("/static/common/toast.js", {"If-None-Match": headers["ETag"]})
        self.assertEqual(st, 304)
        self.assertEqual(body, b"")
        st, _, _ = self.get("/static/common/toast.js", {"If-None-Match": '"nope"'})
        self.assertEqual(st, 200)

    def test_serves_translations_dynamically(self):
        saved = state.UI_LANG
        self.addCleanup(setattr, state, "UI_LANG", saved)
        state.UI_LANG = "ja"
        _, headers_ja, body_ja = self.get("/static/i18n.js")
        self.assertTrue(body_ja.startswith(b"const I18N = "))
        self.assertIn("リポジトリ".encode(), body_ja)
        state.UI_LANG = "en"
        _, headers_en, body_en = self.get("/static/i18n.js")
        self.assertNotEqual(headers_en["ETag"], headers_ja["ETag"])
        self.assertEqual(body_en, ("const I18N = " + client_translations() + ";\n").encode("utf-8"))

    def test_refuses_non_files_and_paths_outside_static(self):
        for path in ("/static/graph/index.html", "/static/nope.js", "/static/", "/static/common/menubar.html",
                     "/static/" + ".." + "/server.py", "/static/graph/js/" + "..%2F" + "server.py"):
            st, _, body = self.get(path)
            self.assertEqual(st, 404, path)
            self.assertEqual(body, b"not found")


if __name__ == "__main__":
    unittest.main()
