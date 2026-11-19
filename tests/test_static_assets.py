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
# ページに直接書かれていて、pages.py が埋め込まないもの (メイン画面の骨格と CSS、/files と /merge ページの HTML、全画面共通のメニューバーの HTML、リポジトリ未選択のときの設定画面。いずれも i18n.py が読む)
NOT_EMBEDDED = {"index.html", "files.html", "merge.html", "menubar.html", "setup.html", "app.css"}


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class StaticAssetTests(unittest.TestCase):
    """static/ のファイルと、pages.py の埋め込み、DEVELOPMENT.md の説明が食い違わないこと。
    読み込まれないファイルに CSS を足しても、ページに効かない (そのことで画面が崩れたことがある)。"""

    def assets(self):
        return sorted(f for f in os.listdir(STATIC) if os.path.isfile(os.path.join(STATIC, f)) and f not in NOT_EMBEDDED)

    def test_every_asset_is_embedded_by_pages_py(self):
        pages = read("git_graph", "pages.py")
        i18n = read("git_graph", "i18n.py")
        for name in self.assets():
            m = re.search(r'^(\w+)\s*=\s*read_asset\("%s"\)' % re.escape(name), pages, re.M)
            if not m:
                # 画面共通の部品 (toast.js / other_lists.js など) は i18n.py が APP_JS / FILES_JS に連結する
                self.assertRegex(
                    i18n, r'STATIC_DIR / "%s"\)' % re.escape(name),
                    f"{name} は pages.py でも i18n.py でも読み込まれていない (使わないファイルは消す)")
                continue
            uses = len(re.findall(r"\b%s\b" % m.group(1), pages)) - 1  # 定義の 1 回を除く
            self.assertGreater(uses, 0, f"{name} は読み込んでいるが、ページに埋め込まれていない")

    def test_pages_py_only_reads_assets_that_exist(self):
        for name in re.findall(r'read_asset\("([^"]+)"\)', read("git_graph", "pages.py")):
            self.assertTrue(os.path.isfile(os.path.join(STATIC, name)), name)

    def test_development_md_matches_the_assets(self):
        doc = read("DEVELOPMENT.md")
        for name in self.assets() + sorted(NOT_EMBEDDED):
            self.assertTrue(name in doc, f"{name} が DEVELOPMENT.md に載っていない")  # assertIn は失敗時に文書全体を出してしまう
        for name in set(re.findall(r"`(?:git_graph/static/)?([a-z_]+\.(?:css|js|html))`", doc)):
            self.assertTrue(os.path.isfile(os.path.join(STATIC, name)), f"DEVELOPMENT.md にある {name} が static/ に無い")

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
        st, headers, body = self.get("/static/toast.js")
        self.assertEqual(st, 200)
        self.assertTrue(headers["Content-Type"].startswith("application/javascript"))
        self.assertEqual(headers["Cache-Control"], "no-cache")
        self.assertTrue(headers["ETag"])
        with open(os.path.join(STATIC, "toast.js"), "rb") as f:
            self.assertEqual(body, f.read())
    def test_matching_etag_returns_304(self):
        _, headers, _ = self.get("/static/toast.js")
        st, _, body = self.get("/static/toast.js", {"If-None-Match": headers["ETag"]})
        self.assertEqual(st, 304)
        self.assertEqual(body, b"")
        st, _, _ = self.get("/static/toast.js", {"If-None-Match": '"nope"'})
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
        for path in ("/static/index.html", "/static/nope.js", "/static/", "/static/menubar.html",
                     "/static/" + ".." + "/server.py", "/static/js/" + "..%2F" + "server.py"):
            st, _, body = self.get(path)
            self.assertEqual(st, 404, path)
            self.assertEqual(body, b"not found")


if __name__ == "__main__":
    unittest.main()
