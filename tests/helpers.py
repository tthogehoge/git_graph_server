# テスト用: ページが <script src> / <link> で読む asset を、実際の配信と同じ内容・順序で連結する。
# (ページの HTML に JavaScript が丸ごと埋め込まれていた頃の assert (assertIn への部品の断片) を、
#  意図を変えずに置き換えるために使う)
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "git_graph" / "static"

ASSET_TAG = re.compile(r'<script src="([^"]+)"></script>|<link rel="stylesheet" href="([^"]+)">')

def asset_body(url):
    """配信 URL (/static/...) に対応するファイルの本文。配信されないもの (外部 URL など) は空文字。"""
    if not url.startswith("/static/"):
        return ""
    rel = url[len("/static/"):]
    if rel == "i18n.js":  # server.py が client_translations() から作る (ファイルには無い)
        from git_graph.i18n import client_translations
        return "const I18N = " + client_translations() + ";\n"
    path = STATIC / rel
    return path.read_text(encoding="utf-8") if path.suffix in (".js", ".css") else ""


def page_with_assets(page, exts=("js", "css")):
    """ページが参照する script / link の中身を、ページでの出現順に連結した文字列。
    exts で種類 (.js か .css か) を絞れる。各 asset の先頭には、元のファイルが分かる目印のコメントを付ける。"""
    parts = []
    for url1, url2 in ASSET_TAG.findall(page):
        url = url1 or url2
        body = asset_body(url)
        if body and url.rsplit(".", 1)[-1] in exts:
            parts.append(f"/* ---- {url} ---- */\n" + body)
    return "\n".join(parts)
