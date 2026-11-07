import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "git_graph", "static")
# ページに直接書かれていて、pages.py が埋め込まないもの (メイン画面の骨格と CSS、/files と /merge ページの HTML)
NOT_EMBEDDED = {"index.html", "files.html", "merge.html", "app.css"}


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
        for name in self.assets():
            m = re.search(r'^(\w+)\s*=\s*read_asset\("%s"\)' % re.escape(name), pages, re.M)
            self.assertIsNotNone(m, f"{name} は pages.py で読み込まれていない (使わないファイルは消す)")
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


if __name__ == "__main__":
    unittest.main()
