#!/usr/bin/env python3
"""git のコミットグラフを、ブランチ/タグ/末端 + それらの分岐点(merge-base)だけに省略して
ブラウザで表示するローカルサーバー。ログやコミット詳細はクリック時に git から動的に取得する。

使い方 (標準ライブラリのみ。Graphviz の dot が必要):
  python git_graph_server.py                    # カレントディレクトリのリポジトリ
  python git_graph_server.py -C /path/to/repo --open
  python git_graph_server.py --no-remotes --no-tags main dev
  python git_graph_server.py --dot              # dot を標準出力に出して終了
  python git_graph_server.py --lang en          # UI の言語 (ja / en)。省略時は環境変数 LANGUAGE / LC_ALL / LC_MESSAGES / LANG から決める (未対応の言語は en)
  python git_graph_server.py --check-i18n       # 翻訳の漏れ (キー不一致・未翻訳のリテラル) を検査して終了

  http://127.0.0.1:8000/                 グラフ (リロードで最新の状態に更新)
  /api/log?rev=<40桁hash>[..<40桁hash>]&path=<ファイル>&skip=0&limit=500   git log (ページング、path指定でファイル限定)
  /api/commit?hash=<hash>                                    git show 相当 (変更ファイル一覧つき)
  /files?hash=<40桁hash>                                     コミットのファイル一覧 (別タブで開く)
  /api/tree?hash=<hash>&path=<directory>                     コミットのディレクトリ内容
  /api/file?hash=<hash>&path=<file>                          コミットのファイル内容
  /diff?hash=<hash>&path=<file>[&old=<旧パス>]               1ファイルの差分ページ (別タブで開く)
  /diff?from=<hash>&to=<hash>&path=<file>[&old=<旧パス>]     2コミット間の差分ページ
  /api/graph                                                 グラフ(SVG + ノード/エッジ情報。未コミットの変更があれば WIP ノードつき)
  /api/status                                                作業ツリーの変更 (ステージ済み/未ステージ/未追跡/競合)。?amend=1 で 直前のコミットの親との差分 (amend_files) も返す
  /diff?wip=staged|unstaged|untracked|conflict|amend&path=<file>   作業ツリーの変更の差分ページ
  /api/compare?from=<hash>&to=<hash>                         2コミット間の変更ファイル一覧
  POST /api/branch | /api/tag | /api/checkout (JSON)         ブランチ作成 / タグ作成 / チェックアウト (画面では、比較のAだけを選んだときの操作)
     チェックアウトが「編集中のファイル」「同名の管理外ファイル」で止まったときは {blocked:true, modified:[..], untracked:[..]} を返す。
     on_conflict="stash" (全変更を stash -u して実行) / "discard" (止めているファイルだけ破棄・削除して実行) を付けて再送する
  POST /api/merge {hash, no_ff} (JSON)                       選んだコミットを現在の HEAD にマージ
  POST /api/reset {hash, mode} (JSON)                       現在の HEAD を選んだコミットへ reset (mode: soft | mixed | hard)
  POST /api/rebase {hash} (JSON)                             現在のブランチを選んだコミットの上にリベース (競合したら中止して元に戻す)
  POST /api/rebase-range {from, to} (JSON)                   A以降の現在のブランチをBの上にリベース (A→B。競合したら中止して元に戻す)
  POST /api/rebase-descendants {from, to} (JSON)              Aの子孫にある全ローカルブランチをBへリベース (分岐構造を維持)
  POST /api/rebase-equivalent {from, to} (JSON)               A/Bの同一差分コミットを比較し、AをB側の親へ寄せる
  POST /api/find-equivalent-parents {from, to} (JSON)          A/Bを先端から調べ、同じ差分のコミットへ選択位置を移す
  POST /api/find-equivalent-branch {from, to} (JSON)          Bの子孫ブランチを先端から親へ調べ、Aに近い同一差分コミットを探す
  POST /api/cherry-pick {hash} (JSON)                        選んだコミットを現在の HEAD にチェリーピック (競合したら中止して元に戻す)
  POST /api/revert {hash} (JSON)                             選んだコミットを打ち消すコミットを作る (git revert。マージコミットは第1親基準。競合したら中止して元に戻す)
  POST /api/abort (JSON)                                     進行中のマージ / リベース / チェリーピック / リバートを --abort で中断
  POST /api/resolve {paths, side} (JSON)                     競合ファイルを side (ours | theirs) の内容で丸ごと置き換えて解決済みにする (片側で削除されていたら削除)
  POST /api/discard {paths} (JSON)                           WIP のファイルの変更を破棄 (追跡中は HEAD に戻す / 新規ファイル・未追跡は削除。取り消せない。マージなどの途中と競合ファイルは不可)
  POST /api/branch-rename {old, new} (JSON)                  ローカルブランチ名の変更 (git branch -m。リモートのブランチは変わらない)
  /api/rebase-todo?hash=<hash>                               対話的リベースの対象コミット一覧 (古い順。git rebase -i が作る todo と同じ)
  POST /api/rebase-i {hash, todo:[{hash, action, message}]}  対話的リベース (git rebase -i 相当)
     action は pick / reword (message が新しいコミットメッセージ) / squash / fixup / drop。todo の並び順がそのまま適用順になる。
     競合やエラーで止まったときは自動で中止して元に戻す (edit は未対応)
  /api/head                                                  現在のブランチ名 (マージ/リベース前の確認用)
  /api/stash-list                                            stash の一覧 (git stash list)。画面では「操作」メニューの「stash一覧」
  POST /api/stash {hash, op} (JSON)                          stash の apply / pop / drop (hash は stash のコミット。op は apply | pop | drop)。画面では、stash を比較のAに選んだときの操作
  /api/branches | /api/tags                                  ブランチ一覧 / タグ一覧 (api_log と同じ形式)。画面では「その他」メニュー
  /api/branches-containing?hash=<hash>                       コミットを含むブランチ一覧
  POST /api/shutdown (JSON)                                  サーバーを終了 (画面の左上の「終了」ボタン)
  /api/reflog?[branch=<名前>]&skip=0&limit=500               reflog (省略時は HEAD、branch 指定でそのローカルブランチ)。画面では「操作」メニューの「reflog」
  POST /api/fetch {prune} | /api/pull {mode} | /api/push {branch, force, set_upstream} (JSON)
  POST /api/push-tag {tag, remote} (JSON)                       タグを push (リモートが複数で決まらないときは needs_remote を返す)
  POST /api/file-copy {hash, path, dest} (JSON)                 コミットのファイルを作業ツリーの別名にコピー (既存ファイルは上書きしない)
     fetch (--all) / pull (mode: ff-only | merge | rebase) / push (branch 指定でそのローカルブランチ、省略で現在のブランチ。force は --force-with-lease)
     push 先は git と同じ優先順位で決める: branch.<名前>.pushRemote > remote.pushDefault > upstream (branch.<名前>.remote)。
     どれも無いとき (リモートが複数なら) {needs_upstream, remote, remotes} を返し、remote を付けて set_upstream:true で再送する
     画面では、比較のAだけを選んだときの「操作」パネルにある。認証の入力待ちで固まらないよう、端末入力を無効にして一定時間でタイムアウトする
  /api/remotes                                               登録済みのリモート [{name, url}]
  POST /api/remote-add {name, url, fetch?} / /api/remote-remove {name} (JSON)   リモートの追加 (任意で fetch) / 削除 (リモート追跡ブランチも消える)
  POST /api/wip-commit {paths, message, amend?} (JSON)        チェックしたファイルを add してコミット (amend=true なら直前のコミットを作り直す。amend のときだけ paths は空でも可)
     書き込みは 127.0.0.1 で待ち受けているときだけ有効 (--read-only で無効化、--allow-write で他アドレスでも許可)
     --lan は --host 0.0.0.0 --allow-write の省略形 (LAN の他の端末から操作できる。認証はないので信頼できるネットワークで使うこと)
"""
import argparse
import ast
import html
import itertools
import json
import codecs
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import webbrowser
from collections import Counter, defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlparse

REPO = "."
PROJECT_NAME = ""  # タブのタイトルに出すプロジェクト名 (リポジトリのフォルダ名)
_FAVICON_SVG = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
                "<path d='M16 7V25M16 16C16 11 24 13 24 7' stroke='#889' stroke-width='3' fill='none'/>"
                "<circle cx='16' cy='25' r='4' fill='#2c7be5'/><circle cx='16' cy='16' r='4' fill='#2c7be5'/>"
                "<circle cx='16' cy='7' r='4' fill='#2c7be5'/><circle cx='24' cy='7' r='4' fill='#27ae60'/></svg>")
FAVICON_LINK = '<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,' + quote(_FAVICON_SVG) + '">'
OPTS = None
UI_LANG = "ja"  # サーバー起動時に決定する UI の言語
WRITABLE = False  # ブランチ/タグ作成・チェックアウト等の書き込み操作を許可するか
WIP_ENABLED = False  # 未コミットの変更を WIP ノードとしてグラフに出すか
HAS_WORKTREE = False  # ワークツリーのあるリポジトリか (bare でないか)
FAST_LAYOUT_NODES = 300  # これより多くのノードを描くときは dot の最適化を軽くする (--full-layout で無効)
COLORS = {"branch": "#b7e4a7", "current": "#b7e4a7", "remote": "#a7c8f0", "tag": "#ffe9a0", "head": "#f4a7a7"}

# UI の翻訳データと画面資産。
ASSET_DIR = Path(__file__).resolve().parent
STATIC_DIR = ASSET_DIR / "static"
I18N_DIR = ASSET_DIR / "i18n"
TRANSLATIONS = {lang: json.loads((I18N_DIR / f"{lang}.json").read_text(encoding="utf-8"))
                for lang in ("ja", "en")}
DEFAULT_LANG = "ja"  # 環境変数から言語を決められないときの言語


def detect_language(explicit=None):
    """UI の言語を決める。優先順位は --lang > LANGUAGE > LC_ALL > LC_MESSAGES > LANG (gettext と同じ)。
    C / POSIX (C.UTF-8 を含む) と、翻訳のない言語は en。どれも設定されていなければ DEFAULT_LANG。
    サーバー起動後は変更しない。"""
    if explicit:
        return explicit
    for key in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(key, "")
        if not value:
            continue
        for candidate in value.split(":"):  # LANGUAGE は "ja:en" のように優先順で並ぶ
            name = re.split(r"[._@-]", candidate.strip(), maxsplit=1)[0].lower()
            if name in ("c", "posix"):
                return "en"
            if name in TRANSLATIONS:
                return name
        return "en"
    return DEFAULT_LANG


def tr(key, **params):
    """現在の言語の文言を返す。{name} は params[name] に置き換える。
    str.format ではないので、文言に stash@{0} のような波括弧があってもよい。
    現在の言語に無いキーは英語で代用し、英語にも無ければ KeyError。"""
    table = TRANSLATIONS.get(UI_LANG, {})
    text = table[key] if key in table else TRANSLATIONS["en"][key]
    return re.sub(r"\{(\w+)\}", lambda m: str(params[m.group(1)]) if m.group(1) in params else m.group(0), text)


class UserError(ValueError):
    """画面にそのまま出すエラー。キーとパラメータから翻訳済みの文言を作る (HTTP では 400 になる)。"""

    def __init__(self, key, **params):
        super().__init__(tr(key, **params))
        self.key, self.params = key, params


def fill_template(text):
    """HTML テンプレート中の二重波括弧で囲んだキーを、現在の言語の文言 (HTML エスケープ済み) に置き換える。"""
    return re.sub(r"\{\{(\w+)\}\}", lambda m: html.escape(tr(m.group(1)), quote=True), text)


def client_translations():
    """ページの JS に埋め込む辞書。英語を土台に現在の言語で上書きし、コマンドライン用 (cli_) は含めない。"""
    merged = {**TRANSLATIONS["en"], **TRANSLATIONS.get(UI_LANG, {})}
    data = {k: v for k, v in merged.items() if not k.startswith("cli_")}
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def with_project(page):
    """テンプレート中のプロジェクト名とアイコンの置換。"""
    return page.replace("__PROJECT__", html.escape(PROJECT_NAME)).replace("__FAVICON__", FAVICON_LINK)


def render_main_page():
    return with_project(fill_template(HTML_TEMPLATE)
            .replace('<html lang="ja">', f'<html lang="{UI_LANG}">'))


def render_app_js():
    return APP_JS.replace("/*__I18N__*/", "const I18N = " + client_translations() + ";")


def git(*args, literal=False):
    env = dict(os.environ, GIT_LITERAL_PATHSPECS="1") if literal else None  # パスをglobとして解釈しない
    return subprocess.check_output(["git", "-C", REPO, *args], text=True,
                                   encoding="utf-8", errors="replace", env=env)


def git_bytes(*args, literal=False):
    """git の出力を変換せずバイト列で取得する (ファイル内容を含む出力用)。"""
    env = dict(os.environ, GIT_LITERAL_PATHSPECS="1") if literal else None
    return subprocess.check_output(["git", "-C", REPO, *args], env=env)


# --------------------------------------------------------------------------
# グラフの構築
# --------------------------------------------------------------------------
SUBJECT_WIDTH = 30  # グラフのノードに出すコミットメッセージの表示幅 (半角=1、全角=2)


def truncate_width(text, limit):
    """表示幅 (全角・東アジアの広い文字は 2) が limit 以内になるよう切り詰める。"""
    width = 0
    for i, ch in enumerate(text):
        w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if width + w > limit:
            return text[:i]
        width += w
    return text


_SVG_TOKEN = re.compile(r'<g id="[^"]*" class="node">|<polygon [^>]*?points="([^"]+)"[^>]*/>|<text text-anchor="start" x="[^"]*"')


def center_node_text(svg):
    """ノード内の文字を、囲んでいるセルの中央に寄せる。
    dot は文字の幅を自分のフォント計測で求めて text-anchor="start" で出力するため、
    ブラウザ側のフォント幅と違う (特に日本語) と左に寄って見える。セルの中央を基準に text-anchor="middle" にする。"""
    state = {"cx": None}

    def fix(m):
        tok = m.group(0)
        if tok.startswith("<g "):
            state["cx"] = None
        elif tok.startswith("<polygon"):
            xs = [float(pt.split(",")[0]) for pt in m.group(1).split()]
            state["cx"] = (min(xs) + max(xs)) / 2
        elif state["cx"] is not None:
            return f'<text text-anchor="middle" x="{state["cx"]:g}"'
        return tok

    return _SVG_TOKEN.sub(fix, svg)


def build_graph(opts):
    """残すコミットとエッジを決め、dot / ノード情報 / エッジ情報を返す。"""
    sys.setrecursionlimit(100000)
    revs = opts.revs
    if not revs:
        refs = git("for-each-ref", "--format=%(refname)").splitlines()
        revs = [
            ref for ref in refs
            if not ref.endswith("/HEAD")
            and (getattr(opts, "include_tips", False)
                 or ref.startswith(("refs/heads/", "refs/remotes/", "refs/tags/")))
            and (not opts.no_remotes or not ref.startswith("refs/remotes/"))
            and (not opts.no_tags or not ref.startswith("refs/tags/"))
        ]
        try:
            git("rev-parse", "--verify", "--quiet", "HEAD")
            revs.append("HEAD")
        except subprocess.CalledProcessError:
            pass

    parents, subjects, order = {}, {}, []  # order: 子が親より先 (--topo-order)
    children = defaultdict(list)
    log = git("log", *revs, "--topo-order", "--format=%H%x09%P%x09%s") if revs else ""
    for line in log.splitlines():
        h, p, s = (line.split("\t", 2) + ["", ""])[:3]
        ps = p.split()
        parents[h] = ps
        subjects[h] = s
        order.append(h)
        for x in ps:
            children[x].append(h)

    labels = defaultdict(list)  # commit -> [(name, kind)]
    try:
        current_branch = git("symbolic-ref", "--short", "-q", "HEAD").strip()
    except subprocess.CalledProcessError:
        current_branch = ""
    # %(HEAD) は現在 checkout 中の ref だけ "*" になるため、
    # symbolic-ref の名前比較よりも ref 自体を確実に判定できる。
    fmt = "%(objectname)%09%(refname)%09%(*objectname)%09%(HEAD)"
    for line in git("for-each-ref", f"--format={fmt}").splitlines():
        obj, ref, peeled, head_mark = (line.split("\t") + [""])[:4]
        target = peeled or obj
        if target not in parents:
            continue
        if ref.startswith("refs/heads/"):
            name = ref[len("refs/heads/"):]
            is_current = name == current_branch or head_mark == "*"
            labels[target].append((name, "current" if is_current else "branch"))
        elif ref.startswith("refs/remotes/"):
            if opts.no_remotes or ref.endswith("/HEAD"):
                continue
            labels[target].append((ref[len("refs/remotes/"):], "remote"))
        elif ref.startswith("refs/tags/"):
            if opts.no_tags:
                continue
            labels[target].append((ref[len("refs/tags/"):], "tag"))
    try:
        head = git("rev-parse", "HEAD").strip()
        if head in parents:
            labels[head].append(("HEAD", "head"))
    except subprocess.CalledProcessError:
        pass

    # ブランチ / タグ / HEAD の指す先 + ルート + それらの merge-base。
    # --include-tips では、名前のない末端コミットも対象に加える。
    # merge-base を git で総当たり (O(n^2) 回のプロセス起動) せず、メモリ上で求める。
    # desc[x] = x の子孫である base コミットの集合 (ビットマスク)。
    # x が (a, b) の merge-base <=> a, b が desc[x] に入り、どの子の desc にも a, b が揃って入っていない。
    # 参照も子孫もないルートコミットも、履歴の始点として必ず表示する。
    named_tips = {h for h, refs in labels.items()
                  if any(kind in ("branch", "current", "remote", "tag", "head") for _, kind in refs)}
    base = named_tips | {h for h, ps in parents.items() if not ps}
    if getattr(opts, "include_tips", False):
        base.update(h for h in parents if not children[h])
    bit = {h: 1 << i for i, h in enumerate(sorted(base))}
    desc = defaultdict(int)
    for h in order:  # 子 -> 親
        d = desc[h] | bit.get(h, 0)
        desc[h] = d
        for p in parents[h]:
            if p in parents:
                desc[p] |= d
    keep = set(base)
    for x in order:
        d = desc[x]
        if d & (d - 1) == 0:  # base の子孫が 1 つ以下
            continue
        ch = children[x]
        if any(desc[y] == d for y in ch):  # 全ての組が 1 つの子で覆われる
            continue
        m = d
        while m:
            low = m & -m
            m ^= low
            covered = 0
            for y in ch:
                if desc[y] & low:
                    covered |= desc[y]
            if covered != d:  # a=low と組んで、どの子にも覆われない b がある
                keep.add(x)
                break
    del desc

    # 間に残したコミットを挟まない直接の祖先 (親 -> 子の順に 1 回なめるだけ)
    up, direct = {}, {}
    for h in reversed(order):
        ups = [up[p] for p in parents[h] if p in up]
        if h in keep:
            direct[h] = frozenset().union(*ups) if ups else frozenset()
            up[h] = frozenset((h,))
        elif len(ups) == 1:
            up[h] = ups[0]
        else:
            up[h] = frozenset().union(*ups) if ups else frozenset()
    del up

    # 推移簡約 (到達可能性もビットマスクで持つ)
    keep_order = [h for h in reversed(order) if h in keep]  # 親が先
    kbit = {h: 1 << i for i, h in enumerate(keep_order)}
    reach = {}
    edges = []
    for c in keep_order:
        deep = 0  # 直接の祖先たちのさらに先の祖先
        for e in direct[c]:
            deep |= reach[e]
        reach[c] = deep | sum(kbit[e] for e in direct[c])
        for d in sorted(direct[c]):
            if not deep & kbit[d]:  # d が他の直接祖先の祖先でなければ残す
                edges.append((d, c))
    kbit = {h: 1 << i for i, h in enumerate(keep_order)}
    keep_order.reverse()  # 出力の順序を毎回同じにする (レイアウトが安定する)

    font = (getattr(opts, "font", None) or "Helvetica").replace("\\", "").replace('"', "")  # dot の文字列に埋めるため引用符は除く
    out = [
        "digraph G {",
        f"  rankdir={opts.rankdir};",
        *(["  nslimit=1; nslimit1=1; mclimit=0.3;"]  # ノードが多いときは dot のレイアウト最適化を軽くして高速化
          if len(keep) > FAST_LAYOUT_NODES and not getattr(opts, "full_layout", False) else []),
        f'  node [shape=plaintext, fontname="{font}", fontsize=10];',
        f'  edge [fontname="{font}", fontsize=9];',
    ]
    for h in keep_order:
        rows = []
        for name, kind in labels.get(h, []):
            marker = "★ " if kind == "current" else ""
            rows.append(f'<TR><TD BGCOLOR="{COLORS[kind]}"><B>{marker}{html.escape(name)}</B></TD></TR>')
        rows.append(f'<TR><TD>{h[:7]}<BR/>{html.escape(truncate_width(subjects[h], SUBJECT_WIDTH))}</TD></TR>')
        label = ('<<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" CELLPADDING="3">'
                 + "".join(rows) + "</TABLE>>")
        out.append(f'  "{h}" [label={label}];')
    # 各エッジ d->c の省略数 = git rev-list --count d..c - 1 を、メモリ上で git を呼ばずに求める。
    # コミット x を「x の子孫である keep ノードの集合」(マスク) で分類して数え、
    # x が c の祖先かつ d の祖先でないもの (c を含み d を含まないマスク) を足し合わせる。
    counts = {}
    if not opts.no_count:
        groups, dk = Counter(), defaultdict(int)
        for h in order:  # 子 -> 親
            m = dk.pop(h, 0) | kbit.get(h, 0)
            groups[m] += 1
            for p in parents[h]:
                if p in parents:
                    dk[p] |= m
        for d, c in edges:
            bc, bd = kbit[c], kbit[d]
            counts[(d, c)] = sum(n for m, n in groups.items() if m & bc and not m & bd) - 1
    for d, c in edges:
        attr = ""
        n = counts.get((d, c), 0)
        if n > 0:
            attr = f' [label="+{n}", style=dashed]'
        out.append(f'  "{d}" -> "{c}"{attr};')
    out.append("}")

    nodes = {h: {"hash": h, "refs": [{"name": n, "kind": k} for n, k in labels.get(h, [])]}
             for h in keep}
    edge_data = {f"{d}->{c}": {"from": d, "to": c} for d, c in edges}
    return "\n".join(out), nodes, edge_data


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------
COPY_JS = """
function fallbackCopy(text) {
  const ta = document.createElement("textarea");
  ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
  document.body.appendChild(ta); ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (e) {}
  document.body.removeChild(ta);
  return ok;
}
function updatePushActions() {
  document.getElementById("pushBtn").disabled = !currentBranchLoaded || !DATA.writable;
}
function updateCurrentBranch() {
  return fetch("/api/head").then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }))
    .then(head => {
      currentBranch = head.branch; currentBranchLoaded = true;
      Object.keys(DATA.nodes).forEach(h => {
        document.querySelectorAll('tr[data-h="' + h + '"] .rc').forEach(el => { el.innerHTML = chips(h); });
      });
      const c = detailView.querySelector("#dChips");
      if (c && curDetailHash) c.innerHTML = detailChips(curDetailHash);
      updatePushActions();
    });
}
function copyText(text) {  // localhost / https なら Clipboard API、それ以外 (LAN の http など) は execCommand で代替
  if (navigator.clipboard && window.isSecureContext)
    return navigator.clipboard.writeText(text).then(() => true, () => fallbackCopy(text));
  return Promise.resolve(fallbackCopy(text));
}
// .copy リンクは data-copy の値をコピーする。行やリンク本来のクリック処理には渡さない
document.addEventListener("click", ev => {
  const a = ev.target.closest(".copy");
  if (!a) return;
  ev.preventDefault(); ev.stopPropagation();
  const label = a.dataset.label || (a.dataset.label = a.textContent);
  copyText(a.dataset.copy).then(ok => {
    const cls = ok ? "ok" : "ng";
    a.textContent = ok ? "✅" : "⚠️"; a.classList.add(cls);
    setTimeout(() => { a.textContent = label; a.classList.remove(cls); }, 1500);
  });
}, true);
"""

HTML_TEMPLATE = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
APP_JS = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
APP_CSS = (STATIC_DIR / "app.css").read_text(encoding="utf-8")


_cache = {"base_key": None, "base": None, "payload_key": None, "payload": None}
_lock = threading.Lock()

CONFLICT_CODES = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}


def read_status_raw():
    return git("status", "--porcelain=v1", "-z", "--untracked-files=all")


def parse_status(raw, limit=5000):
    """git status --porcelain=v1 -z を ステージ済み / 未ステージ / 未追跡 / 競合 に分ける (パスはリポジトリルート基準)。"""
    res = {"staged": [], "unstaged": [], "untracked": [], "conflicts": [], "truncated": False}
    toks = raw.split("\0")
    i = n = 0
    while i < len(toks):
        t = toks[i]
        i += 1
        if len(t) < 4 or t[2] != " ":
            continue
        xy, path = t[:2], t[3:]
        old = None
        if "R" in xy or "C" in xy:  # リネーム/コピーは続くトークンが元のパス
            old = toks[i] if i < len(toks) else None
            i += 1
        if xy == "!!":
            continue
        n += 1
        if n > limit:
            res["truncated"] = True
            break
        if xy == "??":
            res["untracked"].append({"path": path, "st": "?", "label": "new_file"})  # label は翻訳キー (画面側で翻訳する)
        elif xy in CONFLICT_CODES:
            res["conflicts"].append({"path": path, "st": "U", "label": "label_conflict"})
        else:
            x, y = xy[0], xy[1]
            if x != " ":
                res["staged"].append({"path": path, "old_path": old if x in "RC" else None, "st": x})
            if y != " ":
                res["unstaged"].append({"path": path, "old_path": None, "st": y})
    return res


def wip_count(status):
    return len({f["path"] for k in ("staged", "unstaged", "untracked", "conflicts") for f in status[k]})


def amend_base():
    """amend 後のコミットの親 (HEAD^)。最初のコミットなら空ツリー。"""
    try:
        return git("rev-parse", "--verify", "--quiet", "HEAD^").strip()
    except subprocess.CalledProcessError:
        return git("hash-object", "-t", "tree", "/dev/null").strip()


def parse_name_status(raw):
    """--name-status -z の出力を {新しいパス: (変更の種類(M/A/D/R...), 元のパス or None)} にする。"""
    toks = raw.split("\0")
    out, i = {}, 0
    while i < len(toks):
        t = toks[i].lstrip("\n")
        if not t:
            i += 1
            continue
        if t[0] in "RC":
            if i + 2 >= len(toks):
                break
            out[toks[i + 2]] = (t[0], toks[i + 1])
            i += 3
        else:
            if i + 1 >= len(toks):
                break
            out[toks[i + 1]] = (t[0], None)
            i += 2
    return out


def amend_head_changes(base):
    """直前のコミット (HEAD) が変更したファイル {新しいパス: 元のパス or None}。"""
    names = parse_name_status(git("diff", "--name-status", "-z", "-M", base, "HEAD", "--"))
    return {path: old for path, (_, old) in names.items()}


def amend_files(s):
    """amend 後のコミットの内容 = 「直前のコミットの親」と作業ツリーの差分のファイル一覧。
    直前のコミットで変更したファイルは in_head=True (チェックを外すとコミットから外れる)。WIP の変更があるものは tag=WIP。"""
    base = amend_base()
    letters = parse_name_status(git("diff", "--name-status", "-z", "-M", base, "--"))
    in_head = amend_head_changes(base)
    pending = {f["path"] for k in ("staged", "unstaged") for f in s[k]}
    out = []
    for f in parse_numstat(git("diff", "--numstat", "-z", "-M", base, "--")):
        f["st"] = letters.get(f["path"], ("M", None))[0]
        f["wip"] = "amend"
        if f["path"] in in_head:
            f["in_head"] = True
        if f["path"] in pending:
            f["tag"] = "WIP"
        out.append(f)
    for f in s["untracked"]:
        out.append(dict(f, wip="amend", tag="WIP"))
    return out


def api_status(q):
    """作業ツリーの変更 (WIP)。ステージ済み/未ステージには追加・削除行数も付ける。"""
    if not WIP_ENABLED:
        raise UserError("wip_disabled")
    s = parse_status(read_status_raw())
    for key, extra in (("staged", ["--cached"]), ("unstaged", [])):
        if not s[key]:
            continue
        stats = {f["path"]: f for f in parse_numstat(git("diff", *extra, "--numstat", "-z", "-M", "--"))}
        for f in s[key]:
            st = stats.get(f["path"])
            if st:
                f["added"], f["deleted"] = st["added"], st["deleted"]
    try:
        s["head"] = git("rev-parse", "--verify", "--quiet", "HEAD").strip()
    except subprocess.CalledProcessError:
        s["head"] = ""
    s["merging"] = is_merging()
    s["rebasing"] = is_rebasing()
    s["cherry_picking"] = is_cherry_picking()
    s["reverting"] = is_reverting()
    if s["head"]:
        s["head_message"] = commit_info(s["head"])["message"]  # amend のときにメッセージの初期値にする
        if q.get("amend", [""])[0] == "1" and not s["merging"] and not s["rebasing"]:
            s["amend_files"] = amend_files(s)
    return s


def wip_diff(kind, path, specs):
    if not HAS_WORKTREE:
        raise UserError("no_worktree")
    if kind == "staged":
        return git_bytes("diff", "--cached", "--no-color", "-M", "--", *specs, literal=True)
    if kind in ("unstaged", "conflict"):
        return git_bytes("diff", "--no-color", "--", path, literal=True)
    if kind == "untracked":
        # 任意のファイルを読ませないよう、いま未追跡と報告されているパスだけを対象にする
        known = {f["path"] for f in parse_status(read_status_raw())["untracked"]}
        if path not in known:
            raise UserError("not_untracked")
        full = os.path.join(REPO, path)
        if os.path.islink(full):
            return f"symlink -> {os.readlink(full)}\n".encode()
        r = subprocess.run(["git", "-C", REPO, "diff", "--no-index", "--no-color", "--", "/dev/null", path],
                           capture_output=True)
        if r.returncode not in (0, 1):  # 1 は「差分あり」
            raise ValueError(r.stderr.decode("utf-8", errors="replace").strip() or tr("git_diff_failed"))
        return r.stdout
    if kind == "amend":  # 直前のコミットの親 と 作業ツリー の差分 (未追跡ファイルは新規ファイルとして)
        if path in {f["path"] for f in parse_status(read_status_raw())["untracked"]}:
            return wip_diff("untracked", path, specs)
        return git_bytes("diff", "--no-color", "-M", amend_base(), "--", *specs, literal=True)
    raise ValueError("bad wip")


def add_wip(dot_text, nodes, head, count):
    """HEAD の子として、破線の WIP ノードを dot とノード情報に追加する。"""
    label = ('<<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="3" STYLE="dashed" BGCOLOR="#fff8dc">'
             f'<TR><TD><B>WIP</B></TD></TR><TR><TD>{html.escape(tr("n_changes", n=count))}</TD></TR></TABLE>>')
    lines = f'  "WIP" [label={label}];\n  "{head}" -> "WIP" [style=dashed];\n'
    i = dot_text.rindex("}")
    nodes = dict(nodes)
    nodes["WIP"] = {"hash": "WIP", "refs": [], "wip": True, "count": count}
    return dot_text[:i] + lines + dot_text[i:], nodes


def graph_payload():
    """グラフ(SVG + ノード/エッジ情報)の JSON を返す。
    重い部分 (merge-base の総当たり) は ref と HEAD が変わらない限りキャッシュし、
    作業ツリーの変更 (WIP ノード) だけを毎回付け替える。"""
    with _lock:  # 同時に複数回作らない (2つ目以降はキャッシュを使う)
        try:
            head = git("rev-parse", "HEAD").strip()
        except subprocess.CalledProcessError:
            head = ""
        # 同じコミットを指すブランチ間で checkout すると、HEAD と refs は
        # 変わらないため、現在ブランチを含めないと current (★) の表示が
        # キャッシュされたままになる。
        try:
            current_branch = git("symbolic-ref", "--short", "-q", "HEAD").strip()
        except subprocess.CalledProcessError:
            current_branch = ""
        key = git("for-each-ref") + "\0" + head + "\0" + current_branch
        if _cache["base_key"] != key:
            _cache.update(base_key=key, base=build_graph(OPTS), payload_key=None, payload=None)
        raw = ""
        if WIP_ENABLED:
            try:
                raw = read_status_raw()
            except subprocess.CalledProcessError:
                raw = ""
        if _cache["payload_key"] == (key, raw):
            return _cache["payload"]

        dot_text, nodes, edges = _cache["base"]
        if raw and head in nodes:
            count = wip_count(parse_status(raw))
            if count:
                dot_text, nodes = add_wip(dot_text, nodes, head, count)
        # Windows では text=True がロケール(cp932等)で符号化し、★ などが壊れて
        # dot が "not well-formed" になるため、UTF-8 のバイト列で入出力する
        svg = subprocess.run(["dot", "-Tsvg"], input=dot_text.encode("utf-8"),
                             capture_output=True, check=True).stdout.decode("utf-8", errors="replace")
        svg = center_node_text(svg[svg.index("<svg"):])
        payload = json.dumps({"svg": svg, "nodes": nodes, "edges": edges, "writable": WRITABLE,
                              "merge": WRITABLE and HAS_WORKTREE},
                             ensure_ascii=False)
        _cache.update(payload_key=(key, raw), payload=payload)
        return payload


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
REV_RE = re.compile(r"[0-9a-f]{40}(\.\.[0-9a-f]{40})?")
HASH_RE = re.compile(r"[0-9a-f]{7,40}")


def api_log(q):
    rev = q.get("rev", [""])[0]
    if not REV_RE.fullmatch(rev):
        raise ValueError("bad rev")
    path = q.get("path", [""])[0]
    if "\0" in path:
        raise ValueError("bad path")
    skip, limit = paging(q)
    args = ["log", "--date=short", f"--skip={skip}", "-n", str(limit),
            "--format=%H%x1f%h%x1f%an%x1f%ad%x1f%s", rev, "--"]
    if path:
        args.append(path)
    out = git(*args, literal=bool(path))
    commits = [l.split("\x1f") for l in out.splitlines() if l]
    res = {"commits": commits, "has_more": len(commits) == limit}
    if skip == 0:
        count_args = ["rev-list", "--count", rev, "--"]
        if path:
            count_args.append(path)
        res["total"] = int(git(*count_args, literal=bool(path)).strip())
    return res


def paging(q):
    skip = int(q.get("skip", ["0"])[0])
    limit = min(int(q.get("limit", ["500"])[0]), 2000)
    if skip < 0 or limit < 1:
        raise ValueError("bad paging")
    return skip, limit


def api_stash_list(q):
    """stash の一覧 (新しい順)。api_log と同じ [hash, short, 選択子, 日時, 件名] の形で返す。"""
    try:
        out = git("stash", "list", "--format=%H%x1f%h%x1f%gd%x1f%ci%x1f%gs")
    except subprocess.CalledProcessError:
        out = ""
    commits = []
    for l in out.splitlines():
        h, short, sel, date, subj = (l.split("\x1f") + [""] * 5)[:5]
        commits.append([h, short, sel, date[:16], subj])
    return {"commits": commits, "has_more": False, "total": len(commits)}


def _ref_list(refs, fmt, kinds):
    """for-each-ref の結果を api_log と同じ [hash, short, 名前, 日付, 件名] の形にする。"""
    out = git("for-each-ref", "--format=" + fmt, *refs)
    rows = []
    for l in out.splitlines():
        parts = (l.split("\x1f") + [""] * 6)[:6]
        rows.append(parts)
    return rows


def api_branches(q):
    """ローカルブランチ → リモート追跡ブランチの順の一覧。現在のブランチには ★ を付ける。"""
    fmt = "%(refname)%1f%(objectname)%1f%(HEAD)%1f%(committerdate:short)%1f%(subject)"
    commits = []
    for ref, name_, head, date, subj in (r[:5] for r in _ref_list(["refs/heads"], fmt, None)):
        h = git("rev-parse", ref + "^{commit}").strip()
        commits.append([h, h[:7], ("★ " if head == "*" else "") + ref[len("refs/heads/"):], date, subj])
    if not OPTS.no_remotes:
        for ref, name_, head, date, subj in (r[:5] for r in _ref_list(["refs/remotes"], fmt, None)):
            if ref.endswith("/HEAD"):
                continue
            h = git("rev-parse", ref + "^{commit}").strip()
            commits.append([h, h[:7], ref[len("refs/remotes/"):], date, subj])
    return {"commits": commits, "has_more": False, "total": len(commits)}


def api_branches_containing(q):
    """指定コミットを含むローカル / リモート追跡ブランチと各 tip を返す。"""
    h = resolve_commit(q.get("hash", [""])[0])
    refs = ["refs/heads"]
    if not OPTS.no_remotes:
        refs.append("refs/remotes")
    out = git("for-each-ref", "--contains=" + h, "--format=%(refname)%1f%(objectname)", *refs)
    branches = []
    for line in out.splitlines():
        ref, tip = line.split("\x1f", 1)
        if ref.endswith("/HEAD"):
            continue
        name = ref.removeprefix("refs/heads/").removeprefix("refs/remotes/")
        branches.append({"ref": ref, "name": name, "tip": tip})
    branches.sort(key=lambda b: (0 if b["ref"].startswith("refs/heads/") else 1, b["name"].casefold(), b["name"]))
    return {"branches": branches}


def api_tags(q):
    """タグの一覧 (新しい順)。注釈付きタグは指しているコミットを使う。"""
    fmt = "%(refname)%1f%(creatordate:short)%1f%(*subject)%1f%(subject)"
    out = git("for-each-ref", "--sort=-creatordate", "--format=" + fmt, "refs/tags")
    commits = []
    for l in out.splitlines():
        ref, date, psubj, subj = (l.split("\x1f") + [""] * 4)[:4]
        try:
            h = git("rev-parse", "--verify", "--quiet", ref + "^{commit}").strip()
        except subprocess.CalledProcessError:  # コミットを指さないタグ (ツリーなど) は出さない
            continue
        commits.append([h, h[:7], ref[len("refs/tags/"):], date, psubj or subj])
    return {"commits": commits, "has_more": False, "total": len(commits)}


def api_reflog(q):
    """reflog (新しい順)。branch 省略で HEAD、指定でそのローカルブランチ。"""
    branch = q.get("branch", [""])[0]
    if branch:
        check_ref_name("branch", branch)
        ref, label = "refs/heads/" + branch, branch
        if subprocess.run(["git", "-C", REPO, "show-ref", "--verify", "--quiet", ref]).returncode != 0:
            raise UserError("branch_not_found")
    else:
        ref, label = "HEAD", "HEAD"
    skip, limit = paging(q)
    try:  # --date=iso にすると %gd が "名前@{日時}" になる (リフログの時刻を出したい)。番号は並び順から振る
        out = git("reflog", "show", "--date=iso", f"--skip={skip}", "-n", str(limit),
                  "--format=%H%x1f%h%x1f%gd%x1f%gs", ref, "--")
    except subprocess.CalledProcessError:  # コミットがまだない / reflog がない
        out = ""
    commits = []
    for i, l in enumerate(x for x in out.splitlines() if x):
        h, short, gd, subj = (l.split("\x1f") + [""] * 4)[:4]
        when = gd[gd.find("@{") + 2:-1][:16] if "@{" in gd else ""
        commits.append([h, short, f"{label}@{{{skip + i}}}", when, subj])
    res = {"commits": commits, "has_more": len(commits) == limit}
    if skip == 0:
        res["total"] = len(commits) if not res["has_more"] else sum(
            1 for _ in git("reflog", "show", "--format=%H", ref, "--").splitlines())
    return res


def api_stash(body):
    """stash の apply / pop / drop。stash の位置 (stash@{n}) は変わるので、コミットのハッシュから毎回引き直す。
    apply / pop で競合したときは、git の通常の動作どおり作業ツリーに競合を残す (pop は stash も残る)。"""
    op = body.get("op")
    if op not in ("apply", "pop", "drop"):
        raise ValueError("bad op")
    h = resolve_commit(body.get("hash", ""))
    sel = None
    for l in git("stash", "list", "--format=%H %gd").splitlines():
        sh, _, name = l.partition(" ")
        if sh == h:
            sel = name
            break
    if sel is None:
        raise UserError("stash_not_found")
    if op == "drop":
        run_git_write("stash", "drop", sel)
        return {"ok": True, "message": tr("stash_dropped", sel=sel, hash=h[:7])}
    if not HAS_WORKTREE:
        raise UserError("stash_no_worktree")
    if is_merging() or is_rebasing() or is_cherry_picking() or is_reverting():
        raise UserError("op_in_progress")
    r = subprocess.run(["git", "-C", REPO, "stash", op, sel], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        unmerged = subprocess.run(["git", "-C", REPO, "-c", "core.quotepath=false", "diff", "--name-only", "--diff-filter=U"],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
        if unmerged:
            raise ValueError(tr("stash_conflict_pop" if op == "pop" else "stash_conflict", sel=sel) + "\n" + unmerged)
        raise ValueError((r.stderr or r.stdout).strip() or tr("stash_apply_failed"))
    return {"ok": True, "message": tr("stash_applied" if op == "apply" else "stash_popped", sel=sel)}


def commit_info(h):
    if not HASH_RE.fullmatch(h):
        raise ValueError("bad hash")
    o = git("show", "-s", "--date=iso", "--format=%H%x1f%an <%ae>%x1f%ad%x1f%P%x1f%B", h, "--")
    full, author, date, par, msg = (o.split("\x1f", 4) + [""] * 5)[:5]
    return {"hash": full, "author": author, "date": date, "parents": par.split(),
            "message": msg.strip()}


def api_commit(q):
    info = commit_info(q.get("hash", [""])[0])
    raw = git("show", "--numstat", "-z", "-M", "--first-parent", "--format=", info["hash"], "--")
    info["files"] = parse_numstat(raw)
    return info


FILE_CONTENT_LIMIT = 2_000_000


def validate_file_path(path, allow_empty=True):
    """リポジトリルートからの相対パスとして安全な形にする。"""
    if "\0" in path or (not allow_empty and not path) or path.startswith("/"):
        raise ValueError("bad path")
    parts = path.split("/") if path else []
    if any(p in ("", ".", "..") for p in parts):
        raise ValueError("bad path")
    return path


def api_tree(q):
    h = resolve_commit(q.get("hash", [""])[0])
    path = validate_file_path(q.get("path", [""])[0])
    treeish = h if not path else h + ":" + path
    try:
        raw = git("ls-tree", "-z", "-l", treeish)
    except subprocess.CalledProcessError:
        raise UserError("dir_not_found")
    entries = []
    for item in raw.split("\0"):
        if not item:
            continue
        head, name = item.split("\t", 1)
        mode, kind, obj, size = (head.split() + [""])[:4]
        entries.append({"name": name, "type": kind, "size": None if size == "-" else int(size), "mode": mode})
    return {"hash": h, "path": path, "entries": sorted(entries, key=lambda x: (x["type"] != "tree", x["name"]))}


_BOMS = ((codecs.BOM_UTF8, "utf-8-sig", "UTF-8 (BOM)"),
         (codecs.BOM_UTF32_LE, "utf-32", "UTF-32"), (codecs.BOM_UTF32_BE, "utf-32", "UTF-32"),
         (codecs.BOM_UTF16_LE, "utf-16", "UTF-16"), (codecs.BOM_UTF16_BE, "utf-16", "UTF-16"))


def _strict_decode(data, enc, truncated):
    """厳密にデコードする。先頭のみ読んだ場合は末尾の欠けた文字を最大3バイト捨てて再試行する。"""
    for cut in ((0, 1, 2, 3) if truncated else (0,)):
        try:
            return data[:len(data) - cut].decode(enc)
        except UnicodeDecodeError:
            pass
    return None


def _jp_score(text):
    """日本語として自然なほど高い点を返す (Shift_JIS と EUC-JP の判別用)。"""
    score = 0
    for ch in text:
        c = ord(ch)
        if 0x3040 <= c <= 0x30FF or 0x4E00 <= c <= 0x9FFF:
            score += 1
        elif 0xFF61 <= c <= 0xFF9F:  # 半角カナは誤判定のときに増える
            score -= 3
        elif c < 0x20 and ch not in "\t\r\n":
            score -= 5
    return score


def decode_text(data, truncated=False):
    """バイト列の文字コードを自動判別してデコードし、(text, 文字コード名) を返す。バイナリなら (None, None)。
    BOM → UTF-8 → ISO-2022-JP → Shift_JIS(CP932) / EUC-JP (点数の高い方) の順に試し、全て失敗したら UTF-8 で置換する。"""
    for bom, enc, label in _BOMS:
        if data.startswith(bom):
            text = _strict_decode(data, enc, truncated)
            if text is not None:
                return text, label
    if b"\0" in data:
        return None, None
    if b"\x1b$B" in data or b"\x1b$@" in data or b"\x1b$(" in data:  # ISO-2022-JP は 7bit なので UTF-8 より先に見る
        text = _strict_decode(data, "iso2022_jp", truncated)
        if text is not None:
            return text, "ISO-2022-JP"
    text = _strict_decode(data, "utf-8", truncated)
    if text is not None:
        return text, "ASCII" if text.isascii() else "UTF-8"
    best = None
    for enc, label in (("cp932", "Shift_JIS (CP932)"), ("euc_jp", "EUC-JP")):
        text = _strict_decode(data, enc, truncated)
        if text is not None:
            score = _jp_score(text)
            if best is None or score > best[0]:
                best = (score, text, label)
    if best:
        return best[1], best[2]
    return data.decode("utf-8", errors="replace"), "UTF-8 (invalid bytes replaced)"


def api_file(q):
    h = resolve_commit(q.get("hash", [""])[0])
    path = validate_file_path(q.get("path", [""])[0], allow_empty=False)
    spec = h + ":" + path
    kind = git("cat-file", "-t", spec).strip()
    if kind != "blob":
        raise UserError("not_a_file")
    raw = subprocess.check_output(["git", "-C", REPO, "cat-file", "blob", spec])
    truncated = len(raw) > FILE_CONTENT_LIMIT
    data = raw[:FILE_CONTENT_LIMIT]
    text, encoding = decode_text(data, truncated)
    return {"hash": h, "path": path, "content": text or "", "encoding": encoding,
            "binary": text is None, "truncated": truncated, "size": len(raw)}


def api_file_copy(body):
    """コミット内のファイルを、作業ツリーの別のパスにコピーする。既存のファイルは上書きしない。"""
    if not HAS_WORKTREE:
        raise UserError("copy_no_worktree")
    h = resolve_commit(body.get("hash", ""))
    path = validate_file_path(body.get("path", ""), allow_empty=False)
    dest = body.get("dest", "")
    if not isinstance(dest, str):
        raise UserError("copy_dest_invalid")
    dest = dest.strip()
    try:
        validate_file_path(dest, allow_empty=False)
    except ValueError:
        raise UserError("copy_dest_invalid")
    if "\\" in dest or dest.split("/")[0].lower() == ".git":
        raise UserError("copy_dest_invalid")
    root = os.path.realpath(REPO)
    target = os.path.realpath(os.path.join(root, *dest.split("/")))
    try:
        inside = os.path.commonpath([root, target]) == root
    except ValueError:  # 別ドライブなど
        inside = False
    if not inside or target == root:
        raise UserError("copy_dest_invalid")
    spec = h + ":" + path
    if git("cat-file", "-t", spec).strip() != "blob":
        raise UserError("not_a_file")
    if os.path.lexists(target):
        raise UserError("copy_dest_exists", path=dest)
    # --filters で改行コードなどを作業ツリーでの形 (autocrlf など) にそろえる。使えない git では素のまま
    r = subprocess.run(["git", "-C", REPO, "cat-file", "--filters", spec], capture_output=True)
    if r.returncode != 0:
        r = subprocess.run(["git", "-C", REPO, "cat-file", "blob", spec], capture_output=True)
        if r.returncode != 0:
            raise ValueError(r.stderr.decode("utf-8", errors="replace").strip() or tr("git_command_failed"))
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "xb") as f:
            f.write(r.stdout)
    except OSError as e:
        raise ValueError(str(e))
    return {"ok": True, "message": tr("file_copied", path=dest)}


def api_compare(q):
    """2つのコミット間の差分 (from → to) のファイル一覧。"""
    a = commit_info(q.get("from", [""])[0])
    b = commit_info(q.get("to", [""])[0])
    raw = git("diff", "--numstat", "-z", "-M", a["hash"], b["hash"], "--")
    return {"from": a, "to": b, "files": parse_numstat(raw)}


def api_merge_base(q):
    """2つのコミットの共通祖先 (最も新しいもの)。"""
    a = resolve_commit(q.get("from", [""])[0])
    b = resolve_commit(q.get("to", [""])[0])
    try:
        base = git("merge-base", a, b).strip()
    except subprocess.CalledProcessError:
        base = ""
    return {"hash": base}


def parse_numstat(raw):
    """--numstat -z の出力を [{path, old_path, added, deleted}] にする (バイナリは None)。"""
    toks = raw.split("\0")
    files, i = [], 0
    while i < len(toks):
        t = toks[i].lstrip("\n")
        if not t:
            i += 1
            continue
        a, d, path = (t.split("\t", 2) + ["", ""])[:3]
        old = None
        if path == "":  # リネーム: 続く2トークンが 旧パス, 新パス
            old, path = toks[i + 1], toks[i + 2]
            i += 3
        else:
            i += 1
        files.append({"path": path, "old_path": old,
                      "added": None if a == "-" else int(a),
                      "deleted": None if d == "-" else int(d)})
    return files


DIFF_LIMIT = 400000


def api_diff(q):
    """1ファイルの差分。hash=... (1コミット) か from=...&to=... (2コミット間) を指定する。"""
    path = q.get("path", [""])[0]
    old = q.get("old", [""])[0]
    if not path or "\0" in path or "\0" in old:
        raise ValueError("bad path")
    specs = [old, path] if old else [path]
    wip = q.get("wip", [""])[0]
    if wip:  # 作業ツリーの変更
        out = wip_diff(wip, path, specs)
    elif "from" in q or "to" in q:
        a, b = q.get("from", [""])[0], q.get("to", [""])[0]
        if not (HASH_RE.fullmatch(a) and HASH_RE.fullmatch(b)):
            raise ValueError("bad hash")
        out = git_bytes("diff", "--no-color", "-M", a, b, "--", *specs, literal=True)
    else:
        h = q.get("hash", [""])[0]
        if not HASH_RE.fullmatch(h):
            raise ValueError("bad hash")
        out = git_bytes("show", "--format=", "--first-parent", "-M", "--no-color", h, "--", *specs, literal=True)
    truncated = len(out) > DIFF_LIMIT
    diff, encoding = decode_text(out[:DIFF_LIMIT], truncated=truncated)
    if diff is None:
        # バイナリ差分でも git の "Binary files ... differ" 等は表示できるようにする。
        diff, encoding = out[:DIFF_LIMIT].decode("utf-8", errors="replace"), "UTF-8 (binary diff)"
    return {"diff": diff, "encoding": encoding, "truncated": truncated}


WIP_DIR_COLLAPSE = 10  # 1つのディレクトリの変更がこの件数以上なら畳む (画面側の JS の WIP_DIR_COLLAPSE と合わせる)


def dir_of(path):
    return path.rpartition("/")[0]


DIFF_CSS = """
  html, body { height: 100%; margin: 0; font-family: system-ui, sans-serif; font-size: 13px; color: #222; }
  body { display: flex; }
  #side { width: 320px; flex: none; overflow: auto; border-right: 1px solid #ccc; background: #fafafa; padding: 10px; box-sizing: border-box; }
  #side a { display: flex; justify-content: space-between; gap: 8px; padding: 3px 6px; color: #06c; text-decoration: none;
            font-family: monospace; font-size: 12px; border-radius: 3px; word-break: break-all; }
  #side a:hover { background: #eef; }
  #side a.cur { background: #ffe9a0; color: #222; font-weight: bold; }
  #side .n { white-space: nowrap; }
  #side .grp { margin: 10px 0 2px; font-weight: bold; }
  #side .srow { display: flex; align-items: center; } #side .srow a { flex: 1; min-width: 0; }
  #side input[type=checkbox] { margin: 0 6px 0 0; cursor: pointer; }
  .cbox { margin: 6px 0 8px; } .cbox textarea { width: 100%; box-sizing: border-box; min-height: 56px; padding: 4px 6px; border: 1px solid #bbb; border-radius: 3px; font: inherit; resize: vertical; }
  .cbox button { margin-top: 4px; } .cbox .hint { white-space: pre-wrap; }
  .wbtn { background: #1a7f37; color: #fff; border: 1px solid #176b30; padding: 3px 10px; border-radius: 3px; cursor: pointer; }
  .wbtn:hover { background: #176b30; } .wbtn:disabled { opacity: .5; cursor: default; }
  #main { flex: 1; min-width: 0; overflow: auto; padding: 14px 18px; }
  h1 { font-size: 15px; margin: 0 0 6px; word-break: break-all; }
  .meta { color: #555; margin: 2px 0; }
  code { background: #eee; padding: 1px 4px; border-radius: 3px; user-select: all; }
  .plus { color: #1a7f37; } .minus { color: #cf222e; }
  .hint { color: #777; }
  .copy { cursor: pointer; font-size: 12px; margin-left: 6px; user-select: none; text-decoration: none; border-radius: 3px; padding: 0 2px; }
  .copy:hover { background: #e4e4e4; }
  .tag { display: inline-block; padding: 0 6px; border-radius: 3px; margin-right: 4px; font-weight: bold; color: #fff; }
  .tagA { background: #06c; } .tagB { background: #e67e00; }
  .diff { font-family: monospace; font-size: 12px; white-space: pre; margin-top: 12px; border: 1px solid #ddd; }
  .dl { padding: 0 6px; min-width: max-content; }
  .dl.ad { background: #e6ffec; } .dl.de { background: #ffebe9; }
  .dl.hk { background: #ddf4ff; color: #555; } .dl.hd { color: #777; background: #f6f8fa; }
  #side .srow.dhide { display: none; }
  #side .drow { margin-top: 2px; padding: 3px 6px; background: #f1f3f6; cursor: pointer; border-radius: 3px;
                font-family: monospace; font-size: 12px; word-break: break-all; }
  #side .drow:hover { background: #e6ebf5; }
  #side .drow .dname { flex: 1; min-width: 0; }
  #side .dtog { display: inline-block; width: 1em; color: #666; }
"""


def diff_lines_html(text):
    """差分を行ごとに色分けする (ヘッダ部と本文を区別)。"""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    in_hunk, out = False, []
    for l in lines:
        cls = ""
        if l.startswith("diff "):
            in_hunk, cls = False, "hd"
        elif not in_hunk:
            if l.startswith("@@"):
                in_hunk, cls = True, "hk"
            else:
                cls = "hd"
        elif l.startswith("@@"):
            cls = "hk"
        elif l.startswith("+"):
            cls = "ad"
        elif l.startswith("-"):
            cls = "de"
        elif l.startswith("\\"):
            cls = "hd"
        out.append(f'<div class="dl {cls}">{html.escape(l) or " "}</div>')
    return "".join(out)




def localized_wip_title(kind):
    return {
        "amend": tr("amend_content"),
        "conflict": tr("conflict"),
        "staged": tr("staged"),
        "unstaged": tr("unstaged"),
        "untracked": tr("untracked"),
    }.get(kind, kind)


def diff_url(ctx, f):
    params = dict(ctx, path=f["path"])
    if f.get("wip"):
        params["wip"] = f["wip"]
    if f.get("old_path"):
        params["old"] = f["old_path"]
    return "/diff?" + urlencode(params)


def commit_line_html(label, c):
    subject = c["message"].split("\n", 1)[0]
    return (f'<div class="meta"><span class="tag tag{label}">{label}</span>{c["hash"][:7]}'
            f'<a class="copy" data-copy="{c["hash"]}" title="{html.escape(tr("copy_hash_title"))}">📋</a> — '
            f'{html.escape(subject)} <span class="hint">({html.escape(c["author"])} / '
            f'{html.escape(c["date"])})</span></div>')


def wip_files(kind="", status=None):
    """WIP の全ファイルを 競合 → ステージ済み → 未ステージ → 未追跡 の順に、種別(wip)つきで返す。
    kind が amend のときは、amend 後のコミットの内容 (直前のコミットの親との差分) を返す。"""
    if kind == "amend":
        s = api_status({"amend": ["1"]})
        if "amend_files" not in s:
            raise UserError("amend_unavailable")
        return s["amend_files"]
    s = status or api_status({})
    out = []
    for kind, key in (("conflict", "conflicts"), ("staged", "staged"),
                      ("unstaged", "unstaged"), ("untracked", "untracked")):
        out.extend(dict(f, wip=kind) for f in s[key])
    return out


def render_diff_page(q):
    """1ファイルの差分ページ。左にそのコミット(比較 / WIP)の変更ファイル一覧を出す。"""
    path = q.get("path", [""])[0]
    old = q.get("old", [""])[0]
    wip = q.get("wip", [""])[0]
    d = api_diff(q)
    wip_status = None
    if wip:  # 作業ツリーの変更
        if wip != "amend":
            wip_status = api_status({})
        files, ctx, ref = wip_files(wip, wip_status), {}, "WIP"
        info = f'<div class="meta">{html.escape(tr("working_tree"))} — {html.escape(localized_wip_title(wip))}</div>'
        cmd = {"staged": f"git diff --cached -- {path}", "amend": f"git diff HEAD^ -- {path}",
               "untracked": f"git diff --no-index /dev/null {path}"}.get(wip, f"git diff -- {path}")
    elif "from" in q:  # 2コミット間の比較
        c = api_compare(q)
        a, b = c["from"], c["to"]
        files, ctx = c["files"], {"from": a["hash"], "to": b["hash"]}
        ref = f"{a['hash'][:7]}..{b['hash'][:7]}"
        info = commit_line_html("A", a) + commit_line_html("B", b)
        cmd = f"git diff {a['hash'][:7]} {b['hash'][:7]} -- {path}"
    else:
        c = api_commit(q)
        files, ctx, ref = c["files"], {"hash": c["hash"]}, c["hash"][:7]
        subject = c["message"].split("\n", 1)[0]
        info = (f'<div class="meta">{html.escape(tr("commit_label"))} {ref}'
                f'<a class="copy" data-copy="{c["hash"]}" title="{html.escape(tr("copy_hash_title"))}">📋</a>'
                f' — {html.escape(subject)}</div>'
                f'<div class="meta">{html.escape(c["author"])} / {html.escape(c["date"])}</div>')
        cmd = f"git show {ref} -- {path}"
    name = f"{old} => {path}" if old else path

    can_commit = bool(wip_status is not None and WRITABLE and WIP_ENABLED)  # amend の差分ページには出さない
    staged_paths = {f["path"] for f in wip_status["staged"]} if can_commit else set()
    merging = bool(wip_status and wip_status.get("merging"))

    # WIP: ディレクトリごとの件数・行数 (右ペインの filesHtml と同じ集計)
    dir_count, dir_stat = Counter(), {}
    if wip:
        for f in files:
            key = (f.get("wip"), dir_of(f["path"]))
            dir_count[key] += 1
            st = dir_stat.setdefault(key, [0, 0])
            st[0] += f.get("added") or 0
            st[1] += f.get("deleted") or 0
    cur_dir_key = (wip, dir_of(path)) if wip else None  # いま開いているファイルのディレクトリは最初から開く
    seen_dirs = set()
    attr = lambda s: html.escape(s, quote=True)

    side, last = [], None
    for f in files:
        if wip and f.get("wip") != last:
            last = f.get("wip")
            gall = (f'<input type="checkbox" class="gall" data-kind="{html.escape(last)}" '
                    f'title="{html.escape(tr("select_all"))}"> ') if can_commit else ""
            side.append(f'<div class="grp">{gall}{html.escape(localized_wip_title(last))}</div>')
        fdir = dir_of(f["path"])
        key = (f.get("wip"), fdir)
        folded = bool(wip) and dir_count[key] >= WIP_DIR_COLLAPSE
        is_cur_dir = folded and key == cur_dir_key
        if folded and key not in seen_dirs:
            seen_dirs.add(key)
            dck = (f'<input type="checkbox" class="dck" data-kind="{attr(f["wip"])}" data-dir="{attr(fdir)}"> '
                   if can_commit else "")
            data_cur = ' data-cur="1"' if is_cur_dir else ""
            label = fdir + "/" if fdir else tr("dir_root")
            plus, minus = dir_stat[key]
            side.append(
                f'<div class="srow drow" data-kind="{attr(f["wip"])}" data-dir="{attr(fdir)}"'
                f'{data_cur} title="{attr(tr("dir_toggle"))}">{dck}'
                f'<span class="dname"><span class="dtog">▶</span> {html.escape(label)} '
                f'<span class="hint">({html.escape(tr("n_items", n=dir_count[key]))}) '
                f'<span class="plus">+{plus}</span> <span class="minus">-{minus}</span></span></span></div>')
        fname = f"{f['old_path']} => {f['path']}" if f.get("old_path") else f["path"]
        if f.get("label"):
            counts = html.escape(tr(f["label"]))
        elif "added" not in f:
            counts = ""
        elif f["added"] is None:
            counts = "Bin"
        else:
            counts = (f'<span class="plus">+{f["added"]}</span> '
                      f'<span class="minus">-{f["deleted"]}</span>')
        cur = "cur" if f["path"] == path and (not wip or f.get("wip") == wip) else ""
        link = (f'<a class="{cur}" href="{html.escape(diff_url(ctx, f))}">'
                f'<span>{html.escape(fname)}</span><span class="n">{counts}</span></a>')
        if wip:
            box = ""
            if can_commit:
                on = " checked" if (merging or f["path"] in staged_paths) else ""
                box = (f'<input type="checkbox" class="wck" data-kind="{attr(f["wip"])}" '
                       f'data-dir="{attr(fdir)}" data-path="{attr(f["path"])}"{on}>')
            hide = " dhide" if folded and not is_cur_dir else ""
            link = (f'<div class="srow frow{hide}" data-kind="{attr(f["wip"])}" '
                    f'data-dir="{attr(fdir)}">{box}{link}</div>')
        side.append(link)
    commit_box = ""
    commit_js = ""
    fold_js = DIFF_FOLD_JS if wip else ""
    if can_commit:
        commit_box = ('<div class="cbox"><textarea id="cmsg" placeholder="' + html.escape(tr("wip_commit_message"), quote=True) + '"></textarea>'
                      '<div><button id="cbtn" class="wbtn">' + html.escape(tr("wip_commit")) + '</button></div>'
                      '<div id="cout" class="hint"></div></div>')
        texts = {k: tr(k) for k in ("wip_commit", "message_required", "select_files", "wip_execute")}
        commit_js = "const T = " + json.dumps(texts, ensure_ascii=False).replace("</", "<\\/") + ";\n" + WIP_DIFF_COMMIT_JS
    return f"""<!DOCTYPE html>
<html lang="{UI_LANG}"><head><meta charset="utf-8">
<title>{html.escape(path)} @ {ref} - {html.escape(PROJECT_NAME)}</title>
{FAVICON_LINK}
<style>{DIFF_CSS}</style></head><body>
<div id="side">
  <div class="meta">{len(files)} {tr("files_changed")}</div>
  {commit_box}
  {"".join(side)}
</div>
<div id="main">
  <h1>{html.escape(name)}</h1>
  {info}
  <div class="meta"><code>{html.escape(cmd)}</code></div>
  <div class="meta">{html.escape(d["encoding"] or "binary")}</div>
  {'<p class="hint">' + tr("large_file_diff") + '</p>' if d["truncated"] else ""}
  <div class="diff">{diff_lines_html(d["diff"]) or '<p class="hint">' + tr("no_diff") + '</p>'}</div>
</div>
<script>{COPY_JS}</script>
<script>{fold_js}</script>
<script>{commit_js}</script>
</body></html>
"""


DIFF_FOLD_JS = r"""
const OPEN_KEY = "git-graph-wip-open-dirs";  // 右ペイン (WIP 一覧) と共有する。キーは "種別|ディレクトリ"
function loadOpen() { try { return new Set(JSON.parse(sessionStorage.getItem(OPEN_KEY) || "[]")); } catch (e) { return new Set(); } }
function saveOpen(s) { try { sessionStorage.setItem(OPEN_KEY, JSON.stringify([...s])); } catch (e) {} }
const openDirs = loadOpen();
function setDir(row, on) {
  row.querySelector(".dtog").textContent = on ? "▼" : "▶";
  document.querySelectorAll(".frow").forEach(r => {
    if (r.dataset.kind === row.dataset.kind && r.dataset.dir === row.dataset.dir) r.classList.toggle("dhide", !on);
  });
}
document.querySelectorAll(".drow").forEach(row => {
  const key = row.dataset.kind + "|" + row.dataset.dir;
  if (row.dataset.cur) openDirs.add(key);
  setDir(row, openDirs.has(key));
});
saveOpen(openDirs);
document.addEventListener("click", ev => {
  const row = ev.target.closest(".drow");
  if (!row || ev.target.closest("input")) return;
  const key = row.dataset.kind + "|" + row.dataset.dir, on = !openDirs.has(key);
  if (on) openDirs.add(key); else openDirs.delete(key);
  saveOpen(openDirs);
  setDir(row, on);
});
"""


WIP_DIFF_COMMIT_JS = r"""
const cks = () => [...document.querySelectorAll(".wck")];
function paths() { return [...new Set(cks().filter(x => x.checked).map(x => x.dataset.path))]; }
function sync() {
  const n = paths().length, b = document.getElementById("cbtn");
  if (!b) return;
  b.disabled = n === 0;
  b.textContent = T.wip_commit + " (" + n + ")";
  document.querySelectorAll(".gall").forEach(g => {
    const c = cks().filter(x => x.dataset.kind === g.dataset.kind), k = c.filter(x => x.checked).length;
    g.checked = c.length > 0 && k === c.length;
    g.indeterminate = k > 0 && k < c.length;
  });
  document.querySelectorAll(".dck").forEach(g => {
    const c = cks().filter(x => x.dataset.kind === g.dataset.kind && x.dataset.dir === g.dataset.dir), k = c.filter(x => x.checked).length;
    g.checked = c.length > 0 && k === c.length;
    g.indeterminate = k > 0 && k < c.length;
  });
}
document.addEventListener("change", ev => {
  const setPath = (p, v) => cks().filter(y => y.dataset.path === p).forEach(y => { y.checked = v; });
  const g = ev.target.closest(".gall"), d = ev.target.closest(".dck");
  if (g) cks().filter(x => x.dataset.kind === g.dataset.kind).forEach(x => setPath(x.dataset.path, g.checked));
  else if (d) cks().filter(x => x.dataset.kind === d.dataset.kind && x.dataset.dir === d.dataset.dir)
    .forEach(x => setPath(x.dataset.path, d.checked));
  else if (ev.target.closest(".wck")) setPath(ev.target.dataset.path, ev.target.checked);
  else return;
  sync();
});
const cbtn = document.getElementById("cbtn");
if (cbtn) cbtn.addEventListener("click", () => {
  const out = document.getElementById("cout"), msg = document.getElementById("cmsg").value;
  const say = (text, ok) => { out.textContent = text; out.style.color = ok ? "#1a7f37" : "#cf222e"; };
  if (!msg.trim()) { say(T.message_required, false); return; }
  if (!paths().length) { say(T.select_files, false); return; }
  cbtn.disabled = true; say(T.wip_execute, true);
  fetch("/api/wip-commit", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({paths: paths(), message: msg, amend: false})})
    .then(r => r.ok ? r.json() : r.text().then(m => { throw new Error(m || r.status); }))
    .then(r => { say(r.message, true); setTimeout(() => location.reload(), 800); })
    .catch(e => { say(e.message, false); sync(); });
});
sync();
"""


FILES_CSS = """
  html, body { height: 100%; margin: 0; font-family: system-ui, sans-serif; font-size: 13px; color: #222; }
  body { display: flex; }
  #tree { width: 360px; flex: none; overflow: auto; border-right: 1px solid #ccc; background: #fafafa; padding: 12px; box-sizing: border-box; }
  #main { flex: 1; min-width: 0; overflow: auto; padding: 14px 18px; box-sizing: border-box; }
  h1 { font-size: 15px; margin: 0 0 8px; word-break: break-all; }
  h2 { font-size: 14px; margin: 0 0 8px; }
  .meta, .hint { color: #666; margin: 3px 0; }
  .crumb { margin: 8px 0; word-break: break-all; }
  .crumb a, .entry { color: #06c; text-decoration: none; cursor: pointer; }
  .crumb a:hover, .entry:hover { text-decoration: underline; }
  .entry { display: flex; gap: 8px; padding: 4px 6px; border-radius: 3px; font-family: monospace; }
  .entry:hover { background: #eef; }
  .entry .size { margin-left: auto; color: #777; white-space: nowrap; }
  pre { white-space: pre-wrap; word-break: break-word; background: #fff; border: 1px solid #ddd; padding: 10px; font: 12px monospace; }
  pre.code { color: #24292f; tab-size: 4; }
  pre.code { padding-left: 0; }
  .ln { display: flex; }
  .ln:hover { background: #f6f8fa; }
  .ln .no { flex: none; width: var(--nw, 3ch); box-sizing: content-box; padding: 0 10px; margin-right: 10px; text-align: right; color: #8c959f; border-right: 1px solid #e3e3e3; user-select: none; }
  .ln .tx { flex: 1; min-width: 0; }
  .tok-keyword { color: #8250df; font-weight: 600; }
  .tok-string { color: #0a7b35; }
  .tok-comment { color: #6e7781; font-style: italic; }
  .tok-number { color: #0550ae; }
  .tok-type { color: #953800; }
  .tok-tag { color: #116329; }
  .tok-attr { color: #8250df; }
  .tok-regex { color: #cf222e; }
  .binary { color: #777; padding: 10px; border: 1px solid #ddd; }
  .file-actions { display: flex; gap: 6px; margin: 8px 0; }
  .file-actions button { background: #e8eefc; border: 1px solid #b8c7ee; padding: 4px 8px; border-radius: 3px; cursor: pointer; }
  .file-actions button:hover { background: #d6e2fb; }
  .file-actions .msg { margin-left: 8px; color: #0a7b35; white-space: pre-wrap; }
  .file-actions .msg.err { color: #c00; }
  table.file-log { border-collapse: collapse; width: 100%; margin-top: 8px; }
  table.file-log td { border-bottom: 1px solid #e3e3e3; padding: 5px 6px; vertical-align: top; }
  table.file-log td.hash { width: 1%; font-family: monospace; white-space: nowrap; }
  table.file-log td.date { width: 1%; color: #777; white-space: nowrap; }
  table.file-log a { color: inherit; text-decoration: none; }
  table.file-log tr:hover td { background: #eef; }
"""


FILES_PAGE = r"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8"><title>{{file_view}} - __PROJECT__</title>
__FAVICON__
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css">
<style>__CSS__</style><script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script></head>
<body><aside id="tree"><h1>{{file_view}}</h1><div class="meta" id="commit"></div><div id="list"></div></aside>
<main id="main"><h2 id="title">{{select_file}}</h2>
<div id="fileActions" class="file-actions" hidden><button id="fileLogBtn">{{file_log}}</button><button id="fileContentBtn" hidden>{{file_content}}</button><button id="fileCopyBtn" hidden>{{copy_to_work}}</button><span id="fileMsg" class="msg"></span></div>
<div id="content" class="hint">{{select_file_hint}}</div></main>
<script>
/*__FILE_I18N__*/
function t(key, values = {}) {
  let text = key in I18N ? I18N[key] : key;
  Object.entries(values).forEach(([name, value]) => { text = text.replaceAll("{" + name + "}", () => String(value)); });
  return text;
}

const PROJECT_DOCUMENT_TITLE = document.title.substring(document.title.indexOf(" - ") + 3);
function setDocumentTitle(label) {
  document.title = label ? label + " - " + PROJECT_DOCUMENT_TITLE : t("file_view") + " - " + PROJECT_DOCUMENT_TITLE;
}

const qs = new URLSearchParams(location.search), hash = qs.get("hash"), fileViewTab = qs.get("file_view") === "1";
const list = document.getElementById("list"), content = document.getElementById("content"), title = document.getElementById("title");
const fileActions = document.getElementById("fileActions"), fileLogBtn = document.getElementById("fileLogBtn"), fileContentBtn = document.getElementById("fileContentBtn");
const fileCopyBtn = document.getElementById("fileCopyBtn"), fileMsg = document.getElementById("fileMsg");
fileCopyBtn.hidden = !CAN_COPY;
let current = "", currentFile = "";
function esc(s) { return String(s).replace(/[&<>\"]/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", "\"":"&quot;"}[c])); }
function api(path, params) { return fetch(path + "?" + new URLSearchParams(params)).then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); })); }
function showError(e) { list.innerHTML = '<p class="hint">' + t("error") + esc(e.message) + '</p>'; }
function loadTree(path, push) {
  current = path;
  fileActions.hidden = true;
  if (push) history.pushState({path: path}, "", "?hash=" + encodeURIComponent(hash) + (path ? "&path=" + encodeURIComponent(path) : ""));
  api("/api/tree", {hash: hash, path: path}).then(r => {
    document.getElementById("commit").textContent = r.hash.slice(0, 7);
    const parts = path ? path.split("/") : [];
    let crumb = '<div class="crumb"><a data-dir="">📁 ' + t("root") + '</a>';
    parts.forEach((p, i) => { const d = parts.slice(0, i + 1).join("/"); crumb += ' / <a data-dir="' + esc(d) + '">' + esc(p) + '</a>'; });
    crumb += '</div>';
    list.innerHTML = crumb + (r.entries.length ? r.entries.map(e => {
      const p = path ? path + "/" + e.name : e.name;
      const icon = e.type === "tree" ? "📁" : "📄";
      const size = e.size === null ? "" : e.size.toLocaleString() + " B";
      return '<div class="entry" data-type="' + e.type + '" data-path="' + esc(p) + '"><span>' + icon + ' ' + esc(e.name) + '</span><span class="size">' + size + '</span></div>';
    }).join("") : '<p class="hint">' + t("empty_directory") + '</p>');
    list.querySelectorAll("[data-dir]").forEach(a => a.onclick = () => loadTree(a.dataset.dir, true));
    list.querySelectorAll(".entry").forEach(a => a.onclick = () => a.dataset.type === "tree" ? loadTree(a.dataset.path, true) : loadFile(a.dataset.path));
  }).catch(showError);
}
function setFileMsg(text, ok) { fileMsg.textContent = text; fileMsg.className = "msg" + (ok ? "" : " err"); }
function defaultCopyName(path) {  // 例: src/a.py → src/a_1234567.py
  const i = path.lastIndexOf("/"), dir = path.slice(0, i + 1), name = path.slice(i + 1), dot = name.lastIndexOf(".");
  const suffix = "_" + hash.slice(0, 7);
  return dir + (dot > 0 ? name.slice(0, dot) + suffix + name.slice(dot) : name + suffix);
}
fileCopyBtn.onclick = () => {
  if (!currentFile) return;
  const dest = prompt(t("prompt_copy_dest"), defaultCopyName(currentFile));
  if (dest === null || !dest.trim()) return;
  fetch("/api/file-copy", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({hash: hash, path: currentFile, dest: dest.trim()})})
    .then(r => r.ok ? r.json() : r.text().then(m => { throw new Error(m || r.status); }))
    .then(r => setFileMsg(r.message, true))
    .catch(e => setFileMsg(e.message, false));
};
function loadFile(path) {
  currentFile = path;
  setDocumentTitle(path);
  setFileMsg("", true);
  fileActions.hidden = false;
  fileLogBtn.hidden = false;
  fileContentBtn.hidden = true;
  api("/api/file", {hash: hash, path: path}).then(r => {
    const lang = languageFor(r.path);
    title.textContent = r.path + (lang ? " (" + lang + ")" : "") + (r.encoding ? " [" + r.encoding + "]" : "");
    content.className = r.binary ? "binary" : "";
    content.innerHTML = r.binary ? t("binary_file") : numberedCode(r.content, lang) + (r.truncated ? '<p class="hint">' + t("large_file_head") + '</p>' : '');
  }).catch(e => { title.textContent = path; content.className = "hint"; content.textContent = t("error") + e.message; });
}
function loadFileLog(path) {
  if (fileViewTab || (window.opener && !window.opener.closed)) {
    // 別タブでは親のグラフへ通知せず、このファイル画面にログを表示する。
    fileLogBtn.hidden = true;
    fileContentBtn.hidden = false;
    setDocumentTitle(t("file_history_title", {path: path}));
    title.textContent = t("file_history_title", {path: path});
    content.className = "hint";
    content.textContent = t("loading");
    api("/api/log", {rev: hash, path: path}).then(r => {
      const rows = r.commits.map(c => '<tr><td class="hash">' + esc(c[1]) +
        '</td><td class="date">' + esc(c[3]) + '</td><td>' + esc(c[4]) + '</td></tr>').join("");
      content.className = "";
      content.innerHTML = '<div class="meta">' + esc(t("n_commits", {n: r.total})) + '</div>' +
        (rows ? '<table class="file-log"><tbody>' + rows + '</tbody></table>' : '<p class="hint">' + t("no_history") + '</p>');
    }).catch(e => { content.className = "hint"; content.textContent = t("error") + e.message; });
    return;
  }
  // 同一タブではファイル画面にログを描画せず、グラフ画面へ戻して表示する。
  location.href = "/?file_log_hash=" + encodeURIComponent(hash) + "&file_log_path=" + encodeURIComponent(path);
}
fileLogBtn.onclick = () => { if (currentFile) loadFileLog(currentFile); };
fileContentBtn.onclick = () => { if (currentFile) loadFile(currentFile); };
const KEYWORDS = {
  python: "and as assert async await break case class continue def del elif else except finally for from global if import in is lambda match nonlocal not or pass raise return try while with yield None True False self",
  javascript: "as async await break case catch class const continue debugger default delete do else export extends false finally for from function get if import in instanceof let new null of return set static super switch this throw true try typeof undefined var void while with yield",
  typescript: "as async await break case catch class const continue debugger default delete do else export extends false finally for from function get if import in instanceof interface let new null of private protected public readonly return set static super switch this throw true try typeof type undefined var void while with yield",
  java: "abstract assert boolean break byte case catch char class const continue default do double else enum extends final finally float for if implements import instanceof int interface long native new package private protected public return short static strictfp super switch synchronized this throw throws transient try void volatile while true false null",
  c: "auto break case char const continue default do double else enum extern float for goto if inline int long register restrict return short signed sizeof static struct switch typedef union unsigned void volatile while true false NULL",
  go: "break default func interface select case defer go map struct chan else goto package switch const fallthrough if range type continue for import return var true false nil",
  rust: "as async await break const continue crate dyn else enum extern false fn for if impl in let loop match mod move mut pub ref return self Self static struct super trait true type unsafe use where while",
  sql: "select from where and or not insert into update delete create alter drop table index view join inner left right full outer on as group by order having limit offset distinct union all null is like values set",
  shell: "if then else elif fi for while in do done case esac function select time coproc true false",
  ruby: "BEGIN END alias and begin break case class def defined do else elsif end ensure false for if in module next nil not or redo rescue retry return self super then true undef unless until when while yield require attr_reader attr_writer attr_accessor include extend private protected public",
  json: "true false null"
};
Object.keys(KEYWORDS).forEach(k => KEYWORDS[k] = new Set(KEYWORDS[k].split(" ")));
function languageFor(path) {
  const name = path.toLowerCase().split("/").pop();
  if (["dockerfile", "makefile"].includes(name)) return "shell";
  const ext = name.includes(".") ? name.split(".").pop() : "";
  return ({py:"python", pyw:"python", js:"javascript", jsx:"javascript", mjs:"javascript", cjs:"javascript", ts:"typescript", tsx:"typescript", java:"java", c:"c", h:"c", cc:"c", cpp:"c", hpp:"c", go:"go", rs:"rust", rb:"ruby", ru:"ruby", sql:"sql", sh:"shell", bash:"shell", zsh:"shell", json:"json", css:"css", html:"html", htm:"html", xml:"html", md:"markdown", markdown:"markdown", yml:"yaml", yaml:"yaml"})[ext] || "";
}
function token(cls, text) { return '<span class="tok-' + cls + '">' + esc(text) + '</span>'; }
function highlight(source, lang) {
  if (!lang || lang === "markdown") return esc(source);
  if (lang === "html") return source.split(/(<[^>]*>)/g).map(x => x.startsWith("<") ? token("tag", x) : esc(x)).join("");
  const words = KEYWORDS[lang] || new Set(), out = [];
  let i = 0;
  while (i < source.length) {
    const rest = source.slice(i), ch = source[i];
    const lineComment = (["python", "ruby", "shell", "yaml"].includes(lang)) && ch === "#";
    const slashComment = (ch === "/" && (source[i + 1] === "/" || source[i + 1] === "*"));
    if (lineComment || slashComment) {
      const end = lineComment ? source.indexOf("\n", i) : (source[i + 1] === "/" ? source.indexOf("\n", i) : source.indexOf("*/", i) + 2);
      const j = end < (lineComment ? 0 : 2) ? source.length : end;
      out.push(token("comment", source.slice(i, j))); i = j; continue;
    }
    const triple = lang === "python" && (source.startsWith("'''", i) || source.startsWith('\"\"\"', i));
    if (triple || ch === "'" || ch === '"' || (ch === "`" && ["javascript", "typescript"].includes(lang))) {
      const quote = triple ? source.slice(i, i + 3) : ch; let j = i + quote.length;
      if (triple) {
        const end = source.indexOf(quote, j);
        j = end < 0 ? source.length : end + quote.length;
      } else {
        while (j < source.length) { if (source[j] === "\\") j += 2; else if (source[j++] === quote) break; }
      }
      out.push(token("string", source.slice(i, j))); i = j; continue;
    }
    const number = rest.match(/^(?:0x[\da-f]+|\d+(?:\.\d+)?(?:e[+-]?\d+)?)/i);
    if (number) { out.push(token("number", number[0])); i += number[0].length; continue; }
    const word = rest.match(/^[A-Za-z_$][\w$]*/);
    if (word) { const w = word[0]; out.push(words.has(w) ? token("keyword", w) : esc(w)); i += w.length; continue; }
    out.push(esc(ch)); i++;
  }
  return out.join("");
}
function highlightCode(source, lang) {
  if (window.hljs && lang && hljs.getLanguage(lang)) {
    try { return hljs.highlight(source, {language: lang, ignoreIllegals: true}).value; } catch (e) {}
  }
  return highlight(source, lang);
}
/* ハイライト済み HTML を行ごとに分割する。複数行にまたがる span は行末で閉じて次の行で開き直す */
function splitHighlightedLines(html) {
  const lines = [], open = [];
  let cur = "";
  html.split(/(<[^>]+>|\n)/).forEach(p => {
    if (p === "\n") { cur += "</span>".repeat(open.length); lines.push(cur); cur = open.join(""); }
    else if (p.startsWith("</")) { open.pop(); cur += p; }
    else if (p.startsWith("<")) { open.push(p); cur += p; }
    else cur += p;
  });
  lines.push(cur);
  return lines;
}
function numberedCode(source, lang) {
  const lines = splitHighlightedLines(highlightCode(source.replace(/\r\n?/g, "\n"), lang));
  if (lines.length > 1 && lines[lines.length - 1].replace(/<[^>]*>/g, "") === "") lines.pop();  // 末尾の改行は空行として数えない
  const width = String(lines.length).length;
  return '<pre class="code" style="--nw:' + width + 'ch"><code>' +
    lines.map((l, i) => '<div class="ln" id="L' + (i + 1) + '"><span class="no">' + (i + 1) + '</span><span class="tx">' + l + '</span></div>').join("") +
    '</code></pre>';
}
window.onpopstate = () => loadTree(new URLSearchParams(location.search).get("path") || "", false);
if (!hash) showError(new Error(t("commit_required"))); else loadTree(qs.get("path") || "", false);
</script></body></html>""".replace("__CSS__", FILES_CSS)


def render_files_page():
    return with_project(fill_template(FILES_PAGE)
            .replace('<html lang="ja">', f'<html lang="{UI_LANG}">')
            .replace("/*__FILE_I18N__*/", "const I18N = " + client_translations() + ";\n"
                     + "const CAN_COPY = " + ("true" if WRITABLE and HAS_WORKTREE else "false") + ";"))


_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uff00-\uffef]")


def _strip_comments(text):
    """JS / HTML / CSS のコメントを除く (コメントは日本語のままでよい)。URL の // は残す。行数は変えない。"""
    blank = lambda m: "\n" * m.group(0).count("\n")  # 行番号がずれないよう、改行は残す
    text = re.sub(r"/\*.*?\*/", blank, text, flags=re.S)
    text = re.sub(r"<!--.*?-->", blank, text, flags=re.S)
    return re.sub(r"(?<![:\w'\"])//[^\n]*", "", text)


def check_i18n():
    """翻訳の漏れを検査する (--check-i18n)。問題を表示して、あれば 1、なければ 0 を返す。
    1. 全言語でキー集合と {プレースホルダ} が en と一致しているか
    2. コードが使うキーが定義済みか / 使われていないキーがないか
    3. 辞書の外に日本語のリテラルが残っていないか (docstring とコメントは除く)
    4. 日本語以外の言語でテンプレートを展開しても、日本語や未置換のキーが残らないか"""
    problems = []
    base = TRANSLATIONS["en"]
    params_of = lambda s: sorted(set(re.findall(r"\{(\w+)\}", s)))
    for lang, table in TRANSLATIONS.items():
        problems += [f"[{lang}] missing key: {k}" for k in sorted(set(base) - set(table))]
        problems += [f"[{lang}] key not in en: {k}" for k in sorted(set(table) - set(base))]
        problems += [f"[{lang}] placeholders differ from en: {k}" for k in sorted(set(base) & set(table))
                     if params_of(base[k]) != params_of(table[k])]
        if lang == "en":
            problems += [f"[en] contains CJK text: {k}" for k, v in table.items() if _CJK.search(v)]

    with open(__file__, encoding="utf-8") as f:
        src = f.read()
    assets = "\n".join((STATIC_DIR / name).read_text(encoding="utf-8")
                       for name in ("index.html", "app.js", "app.css"))
    tree = ast.parse(src)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                docstrings.add(id(first.value))
    literals = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)) or id(node) in docstrings:
            continue
        literals.add(node.value)
        text = _strip_comments(node.value) if "\n" in node.value else node.value
        for offset, line in enumerate(text.split("\n")):
            if _CJK.search(line):
                problems.append(f"line {node.lineno + offset}: untranslated literal: {line.strip()[:80]}")

    for offset, line in enumerate(_strip_comments(assets).split("\n"), 1):
        if _CJK.search(line):
            problems.append(f"asset line {offset}: untranslated literal: {line.strip()[:80]}")

    code = src + "\n" + assets
    used = set()
    for pattern in (r"""\bt\(\s*["'](\w+)["']""", r"""\btr\(\s*["'](\w+)["']""",
                    r"""UserError\(\s*["'](\w+)["']""", r"\{\{(\w+)\}\}"):
        used |= set(re.findall(pattern, code))
    problems += [f"undefined key used: {k}" for k in sorted(used - set(base))]
    for k in sorted(base):
        if k not in used and k not in literals and not re.search("[\"']" + re.escape(k) + "[\"']", code):
            problems.append(f"unused key: {k}")

    global UI_LANG
    saved = UI_LANG
    try:
        for lang in sorted(TRANSLATIONS):
            if lang == "ja":
                continue
            UI_LANG = lang
            for name, page in (("main page", HTML_TEMPLATE), ("files page", FILES_PAGE)):
                text = _strip_comments(fill_template(page))
                problems += [f"[{lang}] {name}: untranslated text: {l.strip()[:80]}" for l in text.split("\n") if _CJK.search(l)]
                problems += [f"[{lang}] {name}: unresolved key: {k}" for k in re.findall(r"\{\{(\w+)\}\}", text)]
    finally:
        UI_LANG = saved

    for p in problems:
        print(p)
    print(f"i18n check: {len(problems)} problem(s), {len(base)} keys, languages: {', '.join(sorted(TRANSLATIONS))}")
    return 1 if problems else 0


# --------------------------------------------------------------------------
# 書き込み操作 (ブランチ作成 / タグ作成 / チェックアウト)
# --------------------------------------------------------------------------
class Forbidden(Exception):
    pass


_write_lock = threading.Lock()


def is_loopback(host):
    return host in ("localhost", "::1") or host.startswith("127.")


def resolve_commit(h):
    if not HASH_RE.fullmatch(h or ""):
        raise ValueError("bad hash")
    try:
        return git("rev-parse", "--verify", "--quiet", h + "^{commit}").strip()
    except subprocess.CalledProcessError:
        raise UserError("commit_not_found")


def check_ref_name(kind, name):
    prefix = {"branch": "refs/heads/", "tag": "refs/tags/"}[kind]
    label = {"branch": ("branch_name_invalid", "branch_name_unusable"),
             "tag": ("tag_name_invalid", "tag_name_unusable")}[kind]
    if not name or name.startswith("-") or "\0" in name:
        raise UserError(label[0])
    r = subprocess.run(["git", "-C", REPO, "check-ref-format", prefix + name], capture_output=True)
    if r.returncode != 0:
        raise UserError(label[1], name=repr(name))


def run_git_write(*args, literal=False, cwd=None):
    """git を実行し、失敗したら git のエラーメッセージを ValueError にして返す。"""
    env = dict(os.environ, GIT_LITERAL_PATHSPECS="1") if literal else None  # パスをglobとして解釈しない
    r = subprocess.run(["git", "-C", cwd or REPO, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    if r.returncode != 0:
        raise ValueError((r.stderr or r.stdout).strip() or tr("git_command_failed"))
    return (r.stdout + r.stderr).strip()


def git_c_locale(*args, cwd=None):
    """メッセージを解析するため、ロケールを C にして git を実行する (失敗しても例外にしない)。"""
    env = dict(os.environ, LC_ALL="C", LANGUAGE="C")
    return subprocess.run(["git", "-C", cwd or REPO, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)


def parse_checkout_blockers(text):
    """checkout が止まったときのエラーから、止めているファイルを取り出す。
    modified: 編集中 (ステージ済み含む) で上書きされてしまうファイル / untracked: 同名の管理外ファイル"""
    res = {"modified": [], "untracked": []}
    kind = None
    for line in text.splitlines():
        if "would be overwritten by" in line:
            if "local changes" in line:
                kind = "modified"
            elif "untracked working tree files" in line:
                kind = "untracked"
            else:
                kind = None
            continue
        if kind and line.startswith("\t") and line.strip():
            res[kind].append(line[1:])
        else:
            kind = None
    return res


def stash_all_changes():
    """未コミットの変更 (未追跡ファイルも) を stash に退避する。作った stash の名前を返す (変更がなければ None)。"""
    def top():
        r = subprocess.run(["git", "-C", REPO, "rev-parse", "-q", "--verify", "refs/stash"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        return r.stdout.strip()
    before = top()
    run_git_write("stash", "push", "--include-untracked", "-m",
                  tr("stash_commit_msg", time=time.strftime("%Y-%m-%d %H:%M:%S")))
    return "stash@{0}" if top() != before else None


def discard_blockers(blockers):
    """チェックアウトを止めているファイルだけを破棄する (編集中の変更は HEAD の状態に戻し、管理外ファイルは削除)。"""
    top = git("rev-parse", "--show-toplevel").strip()
    top_real = os.path.realpath(top)
    for path in blockers["modified"]:
        in_head = git_c_locale("cat-file", "-e", "HEAD:" + path, cwd=top).returncode == 0
        if in_head:
            run_git_write("checkout", "HEAD", "--", path, literal=True, cwd=top)
        else:  # インデックスにだけ追加されたファイル
            run_git_write("rm", "-f", "-q", "--", path, literal=True, cwd=top)
    for path in blockers["untracked"]:
        full = os.path.join(top_real, path)
        parent = os.path.realpath(os.path.dirname(full))
        if os.path.commonpath([parent, top_real]) != top_real:
            raise UserError("path_outside_repo", path=path)
        if os.path.isdir(full) and not os.path.islink(full):
            raise UserError("dir_no_autodelete", path=path)
        if os.path.lexists(full):
            os.remove(full)


def checkout_resolving(args, mode):
    """git checkout <args> を実行する。成功なら None を返す。
    編集中のファイル/同名の管理外ファイルで止まったとき、mode が None なら {blocked, modified, untracked} を返し、
    "stash" なら全変更を退避してから、"discard" なら止めているファイルだけ破棄してから再実行する。"""
    if mode not in (None, "stash", "discard"):
        raise UserError("on_conflict_invalid")
    stashed = None
    if mode == "stash":
        stashed = stash_all_changes()
    for _ in range(3):
        r = git_c_locale("checkout", *args)
        if r.returncode == 0:
            return {"stashed": stashed} if stashed else None
        text = (r.stderr + r.stdout).strip()
        blockers = parse_checkout_blockers(text)
        if not (blockers["modified"] or blockers["untracked"]):
            raise ValueError(text or tr("checkout_failed"))
        if mode is None:
            return {"blocked": True, **blockers}
        if mode == "stash":
            raise ValueError(tr("checkout_still_blocked") + "\n" + text)
        discard_blockers(blockers)
    raise UserError("checkout_failed")


def with_stash_note(res, msg):
    if res and res.get("stashed"):
        msg += tr("stash_note", stash=res["stashed"])
    return msg


def api_branch(body):
    h = resolve_commit(body.get("hash", ""))
    name = str(body.get("name", "")).strip()
    check_ref_name("branch", name)
    if body.get("checkout"):
        upstream = body.get("upstream")
        if upstream:
            if not isinstance(upstream, str) or upstream.startswith("-") or "\0" in upstream:
                raise UserError("remote_branch_not_found")
            ref = "refs/remotes/" + upstream
            r = subprocess.run(["git", "-C", REPO, "rev-parse", "--verify", "--quiet", ref + "^{commit}"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode != 0 or r.stdout.strip() != h:
                raise UserError("remote_branch_not_found")
            checkout_args = ["-b", name, "--track", upstream]
        else:
            checkout_args = ["-b", name, h]
        res = checkout_resolving(checkout_args, body.get("on_conflict"))
        if res and res.get("blocked"):
            return res
        return {"ok": True, "message": with_stash_note(res, tr("branch_created_checkout", name=name))}
    run_git_write("branch", name, h)
    return {"ok": True, "message": tr("branch_created", name=name)}


def api_tag(body):
    h = resolve_commit(body.get("hash", ""))
    name = str(body.get("name", "")).strip()
    msg = str(body.get("message", "")).strip()
    check_ref_name("tag", name)
    if msg:
        run_git_write("tag", "-a", name, "-m", msg, h)
        return {"ok": True, "message": tr("tag_created_annotated", name=name)}
    run_git_write("tag", name, h)
    return {"ok": True, "message": tr("tag_created", name=name)}


def api_delete_ref(body):
    kind = body.get("kind")
    if kind == "remote-branch":
        name = str(body.get("name", "")).strip()
        remotes = git("remote").split()
        remote = next((r for r in sorted(remotes, key=len, reverse=True) if name.startswith(r + "/")), None)
        branch = name[len(remote) + 1:] if remote else ""
        if not remote or not branch:
            raise UserError("remote_branch_not_found")
        check_ref_name("branch", branch)
        auth = auth_for_remote(remote, body)
        r = run_git_net("push", remote, "--delete", branch, auth=auth)
        if r.returncode != 0:
            if is_http_auth_error(r) and not body.get("auth_retry"):
                return {"auth_required": True, "auth_remote": remote}
            if body.get("auth_retry"):
                HTTP_AUTH_CACHE.pop(remote, None)
            raise ValueError(net_error(r, tr("git_command_failed")))
        remember_auth(remote, auth)
        return {"ok": True, "message": tr("remote_branch_deleted", name=name)}
    if kind not in ("branch", "tag"):
        raise UserError("ref_delete_only")
    name = str(body.get("name", "")).strip()
    check_ref_name(kind, name)
    ref = ("refs/heads/" if kind == "branch" else "refs/tags/") + name
    exists = subprocess.run(["git", "-C", REPO, "show-ref", "--verify", "--quiet", ref])
    if exists.returncode != 0:
        raise UserError("ref_not_exist", name=repr(name))
    if kind == "branch":
        if body.get("force") is True:
            run_git_write("branch", "-D", "--", name)
        else:
            r = subprocess.run(["git", "-C", REPO, "branch", "-d", "--", name],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode != 0:
                ancestor = subprocess.run(["git", "-C", REPO, "merge-base", "--is-ancestor", name, "HEAD"])
                if ancestor.returncode == 1:
                    return {"needs_force": True}
                raise ValueError((r.stderr or r.stdout).strip() or tr("branch_delete_failed"))
        return {"ok": True, "message": tr("branch_deleted", name=name)}
    run_git_write("tag", "-d", "--", name)
    return {"ok": True, "message": tr("tag_deleted", name=name)}


def api_checkout(body):
    branch = body.get("branch")
    if branch:
        branch = str(branch)
        check_ref_name("branch", branch)
        res = checkout_resolving([branch, "--"], body.get("on_conflict"))
        if res and res.get("blocked"):
            return res
        return {"ok": True, "message": with_stash_note(res, tr("checkout_branch_done", branch=branch))}
    h = resolve_commit(body.get("hash", ""))
    res = checkout_resolving(["--detach", h], body.get("on_conflict"))
    if res and res.get("blocked"):
        return res
    return {"ok": True, "message": with_stash_note(res, tr("checkout_detached_done", hash=h[:7]))}


def api_reset(body):
    """現在の HEAD を選択したコミットへ移動する。"""
    mode = body.get("mode", "mixed")
    if mode not in ("soft", "mixed", "hard"):
        raise UserError("reset_mode_invalid")
    if is_merging() or is_rebasing() or is_cherry_picking() or is_reverting():
        raise UserError("reset_blocked")
    h = resolve_commit(body.get("hash", ""))
    before = git("rev-parse", "HEAD").strip() if has_head() else None
    run_git_write("reset", "--" + mode, h)
    if before == h:
        return {"ok": True, "message": tr("reset_noop", hash=h[:7])}
    return {"ok": True, "message": tr("reset_done", hash=h[:7], mode=mode)}


def is_merging():
    return subprocess.run(["git", "-C", REPO, "rev-parse", "-q", "--verify", "MERGE_HEAD"],
                          capture_output=True).returncode == 0


def has_head():
    return subprocess.run(["git", "-C", REPO, "rev-parse", "-q", "--verify", "HEAD"],
                          capture_output=True).returncode == 0


def has_parent():
    return subprocess.run(["git", "-C", REPO, "rev-parse", "-q", "--verify", "HEAD^"],
                          capture_output=True).returncode == 0


def api_wip_commit(body):
    """チェックされたファイルを add してコミットする (TortoiseGit のコミットダイアログと同じ考え方)。
    ステージ済みでチェックのないファイルは、ステージから外す (作業ツリーの内容はそのまま)。"""
    if not WIP_ENABLED:
        raise UserError("wip_disabled")
    amend = body.get("amend") is True
    branch = str(body.get("branch", "")).strip()
    if branch and amend:
        raise UserError("amend_blocked")
    if branch:
        check_ref_name("branch", branch)
        current = api_head().get("branch")
        if branch != current:
            exists = subprocess.run(["git", "-C", REPO, "rev-parse", "--verify", "--quiet", "refs/heads/" + branch],
                                    capture_output=True).returncode == 0
            if exists:
                checkout = checkout_resolving([branch], None)
                if checkout and checkout.get("blocked"):
                    raise ValueError(tr("checkout_failed"))
            elif has_head():
                run_git_write("branch", branch, "HEAD")
                run_git_write("checkout", branch)
            else:
                run_git_write("checkout", "-b", branch)
    if amend:
        if not has_head():
            raise UserError("amend_no_commit")
        if is_merging() or is_rebasing():
            raise UserError("amend_blocked")
    msg = str(body.get("message", "")).strip()
    if not msg:
        raise UserError("message_required")
    paths = body.get("paths")
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths) or (not paths and not amend):
        raise UserError("select_files")  # amend はメッセージだけの変更 (paths が空) もできる
    checked = set(paths)
    s = parse_status(read_status_raw(), limit=10 ** 9)
    known = {f["path"] for k in ("staged", "unstaged", "untracked", "conflicts") for f in s[k]}
    head_changes = amend_head_changes(amend_base()) if amend else {}  # 直前のコミットが変更したファイル (チェックを外すとコミットから外す)
    if amend and head_changes and not checked:  # index を触る前に確認する (空のコミットは git が拒否する)
        raise UserError("amend_would_empty")
    bad = checked - known - set(head_changes)  # 任意のパスを add させないよう、いま変更ありと報告されているものだけを対象にする
    if bad:
        raise UserError("files_unchanged", files=", ".join(sorted(bad)[:3]))
    unstage = []
    for f in s["staged"]:
        if f["path"] not in checked:
            unstage.append(f["path"])
            if f.get("old_path"):  # リネームは元のパスも一緒に戻す
                unstage.append(f["old_path"])
    if is_merging() and (unstage or any(f["path"] not in checked for f in s["conflicts"])):
        raise UserError("merging_need_all")
    step = 200  # コマンドラインが長くなりすぎないように分割する
    if unstage:
        for i in range(0, len(unstage), step):
            part = unstage[i:i + step]
            if has_head():
                run_git_write("reset", "-q", "--", *part, literal=True)
            else:  # 最初のコミット前は HEAD がないので、インデックスから外す
                run_git_write("rm", "--cached", "-r", "-q", "-f", "--", *part, literal=True)
    drop = []  # amend: チェックを外した「直前のコミットのファイル」は、コミットから外す (親の状態に戻す。作業ツリーはそのまま)
    for path, old in head_changes.items():
        if path not in checked:
            drop.append(path)
            if old:
                drop.append(old)
    if drop:
        base = amend_base()
        for i in range(0, len(drop), step):
            part = drop[i:i + step]
            if has_parent():
                run_git_write("reset", "-q", base, "--", *part, literal=True)
            else:  # 最初のコミットには親がないので、インデックスから外す
                run_git_write("rm", "--cached", "-r", "-q", "-f", "--", *part, literal=True)
    # すでにステージ済みの削除は add -A できない (パスが見つからず失敗する) が、インデックスにあるのでそのままコミットに入る
    gone = {f["path"] for f in s["staged"] if f["st"] == "D"} - {f["path"] for k in ("unstaged", "untracked") for f in s[k]}
    todo = sorted(checked - gone)
    for i in range(0, len(todo), step):
        run_git_write("add", "-A", "--", *todo[i:i + step], literal=True)
    run_git_write("commit", *(["--amend"] if amend else []), "-m", msg)
    h = git("rev-parse", "HEAD").strip()
    if not amend:
        message = tr("wip_committed", hash=h[:7], n=len(checked))
    elif not checked:
        message = tr("wip_amended_msg_only", hash=h[:7])
    elif drop:
        message = tr("wip_amended_dropped", hash=h[:7], n=len(checked),
                     dropped=len([d for d in head_changes if d not in checked]))
    else:
        message = tr("wip_amended", hash=h[:7], n=len(checked))
    return {"ok": True, "message": message}


def is_rebasing():
    for d in ("rebase-merge", "rebase-apply"):
        r = subprocess.run(["git", "-C", REPO, "rev-parse", "--git-path", d],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        path = r.stdout.strip()
        if path and os.path.isdir(os.path.join(REPO, path)):
            return True
    return False


def api_head(q=None):
    r = subprocess.run(["git", "-C", REPO, "symbolic-ref", "--short", "-q", "HEAD"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return {"branch": r.stdout.strip() or None, "merging": (is_merging() or is_rebasing()) if HAS_WORKTREE else False}


def api_merge(body):
    """選んだコミットを、いまチェックアウトしている HEAD にマージする。
    競合したときはマージ途中の状態で止める (WIP の一覧に競合ファイルが出るので、解決してコミットする)。"""
    if not HAS_WORKTREE:
        raise UserError("merge_no_worktree")
    h = resolve_commit(body.get("hash", ""))
    if is_merging():
        raise UserError("merge_in_progress")
    if not has_head():
        raise UserError("merge_no_commits")
    before = git("rev-parse", "HEAD").strip()
    args = ["merge", "--no-edit"]
    if body.get("no_ff"):
        args.append("--no-ff")
    r = subprocess.run(["git", "-C", REPO, *args, h], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        if is_merging():  # 競合 (自動マージできなかった)
            return {"ok": True, "conflict": True,
                    "message": tr("merge_conflict", target=h[:7])}
        raise ValueError(out or tr("merge_failed"))
    if git("rev-parse", "HEAD").strip() == before:  # git の出力文言 (ロケールで変わる) ではなく、HEAD が動いたかで判定する
        return {"ok": True, "message": tr("merge_uptodate", hash=h[:7])}
    return {"ok": True, "message": tr("merge_done", hash=h[:7])}


def api_rebase(body):
    """現在のブランチを、選んだコミットの上にリベースする。競合したら自動で中止して元に戻す。"""
    if not HAS_WORKTREE:
        raise UserError("rebase_no_worktree")
    h = resolve_commit(body.get("hash", ""))
    if is_merging() or is_rebasing():
        raise UserError("merge_or_rebase_in_progress")
    head = api_head()
    if not head["branch"]:
        raise UserError("detached_rebase")
    before = git("rev-parse", "HEAD").strip()
    r = subprocess.run(["git", "-C", REPO, "rebase", h], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        if is_rebasing():  # 競合 → 元に戻す
            subprocess.run(["git", "-C", REPO, "rebase", "--abort"], capture_output=True)
            raise UserError("rebase_conflict", target=h[:7], cmd="git rebase")
        raise ValueError(out or tr("rebase_failed"))
    if git("rev-parse", "HEAD").strip() == before:
        return {"ok": True, "message": tr("rebase_noop", branch=head["branch"], hash=h[:7])}
    return {"ok": True, "message": tr("rebase_done", branch=head["branch"], hash=h[:7])}


def api_rebase_range(body):
    """A より後の現在のブランチのコミットを B の上にリベースする。
    競合したら自動で中止して元に戻す。"""
    if not HAS_WORKTREE:
        raise UserError("rebase_no_worktree")
    a = resolve_commit(body.get("from", ""))
    b = resolve_commit(body.get("to", ""))
    if is_merging() or is_rebasing():
        raise UserError("merge_or_rebase_in_progress")
    head = api_head()
    if not head["branch"]:
        raise UserError("detached_rebase")
    before = git("rev-parse", "HEAD").strip()
    r = subprocess.run(["git", "-C", REPO, "rebase", "--onto", b, a], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        if is_rebasing():
            subprocess.run(["git", "-C", REPO, "rebase", "--abort"], capture_output=True)
            raise UserError("rebase_conflict", target=b[:7], cmd="git rebase --onto")
        raise ValueError(out or tr("rebase_failed"))
    if git("rev-parse", "HEAD").strip() == before:
        return {"ok": True, "message": tr("rebase_noop", branch=head["branch"], hash=b[:7])}
    return {"ok": True, "message": tr("rebase_range_done", branch=head["branch"], **{"from": a[:7], "to": b[:7]})}


def api_rebase_descendants(body):
    """A の子孫にある全ローカルブランチを、分岐構造を保って B の上へ移す。

    A / 分岐点 / ブランチ先端を境界にして線形区間を近い順に処理する。
    各区間は一時ブランチ上で rebase --onto し、全区間が成功してから
    ローカルブランチの参照を一括更新するため、途中失敗で履歴が半端に
    書き換わらないようにする。
    """
    if not HAS_WORKTREE:
        raise UserError("rebase_no_worktree")
    a = resolve_commit(body.get("from", ""))
    b = resolve_commit(body.get("to", ""))
    if is_merging() or is_rebasing():
        raise UserError("merge_or_rebase_in_progress")
    head = api_head()
    if not head["branch"]:
        raise UserError("detached_rebase")
    if git("status", "--porcelain", "--untracked-files=all").strip():
        raise UserError("rebase_descendants_dirty")
    if a == b:
        return {"ok": True, "message": tr("rebase_noop", branch=head["branch"], hash=b[:7])}

    def parents(commit):
        fields = git("rev-list", "--parents", "-n", "1", commit).split()
        return fields[1:]

    def is_ancestor(ancestor, descendant):
        return subprocess.run(["git", "-C", REPO, "merge-base", "--is-ancestor", ancestor, descendant]).returncode == 0

    # A を祖先に持つローカルブランチだけを対象にする。B が A の
    # 子孫側にある場合は、B を含む系統（通常は main など）を土台として
    # 残し、そこから分岐したブランチだけを載せ替える。
    b_descends_from_a = is_ancestor(a, b)
    branch_tips = []
    for ref in git("for-each-ref", "--format=%(refname)", "refs/heads").splitlines():
        tip = git("rev-parse", ref + "^{commit}").strip()
        if is_ancestor(a, tip) and not (b_descends_from_a and is_ancestor(b, tip)):
            branch_tips.append((ref, tip))
    if not branch_tips:
        raise ValueError(tr("rebase_failed"))

    commits = {a}
    for _, tip in branch_tips:
        if tip != a:
            commits.update(git("rev-list", f"{a}..{tip}").splitlines())
    if b in commits:
        raise ValueError(tr("rebase_failed"))

    children = defaultdict(list)
    for commit in commits:
        ps = parents(commit)
        if commit != a and len(ps) != 1:
            raise UserError("rebase_descendants_merges")
        for parent in ps:
            if parent in commits:
                children[parent].append(commit)
            elif commit != a:
                # A より後の通常コミットが対象外の親を持つのはマージ構造。
                raise UserError("rebase_descendants_merges")
    for value in children.values():
        value.sort()

    # ここで止めるノードを、分岐点・末端・ブランチ参照の付いたコミットにする。
    boundaries = {a}
    boundaries.update(tip for _, tip in branch_tips)
    boundaries.update(commit for commit in commits if len(children[commit]) != 1)

    original_branch = head["branch"]
    temp = f"git-graph-rebase-{os.getpid()}-{threading.get_ident()}"
    while subprocess.run(["git", "-C", REPO, "show-ref", "--verify", "--quiet", f"refs/heads/{temp}"]).returncode == 0:
        temp += "-x"
    mapping = {a: b}
    queue = [a]
    temp_exists = False
    try:
        run_git_write("checkout", "-b", temp, a)
        temp_exists = True
        while queue:
            old_base = queue.pop(0)
            new_base = mapping[old_base]
            for first in children[old_base]:
                old_tip = first
                while old_tip not in boundaries:
                    next_nodes = children[old_tip]
                    if len(next_nodes) != 1:
                        raise UserError("rebase_descendants_merges")
                    old_tip = next_nodes[0]

                old_segment = git("rev-list", "--reverse", f"{old_base}..{old_tip}").splitlines()
                run_git_write("checkout", "-B", temp, old_tip)
                r = subprocess.run(["git", "-C", REPO, "rebase", "--onto", new_base, old_base],
                                   capture_output=True, text=True, encoding="utf-8", errors="replace")
                if r.returncode != 0:
                    conflicted = is_rebasing()
                    if conflicted:
                        subprocess.run(["git", "-C", REPO, "rebase", "--abort"], capture_output=True)
                        raise UserError("rebase_conflict", target=new_base[:7], cmd="git rebase --onto")
                    out = (r.stdout + r.stderr).strip()
                    if out:
                        raise ValueError(out)
                    raise ValueError(tr("rebase_failed"))
                new_tip = git("rev-parse", "HEAD").strip()
                new_segment = git("rev-list", "--reverse", f"{new_base}..{new_tip}").splitlines()
                if len(old_segment) != len(new_segment):
                    raise UserError("rebase_descendants_merges")
                mapping.update(zip(old_segment, new_segment))
                mapping[old_tip] = new_tip
                queue.append(old_tip)

        updates = [(name, tip, mapping.get(tip)) for name, tip in branch_tips]
        if any(new_tip is None for _, _, new_tip in updates):
            raise ValueError(tr("rebase_failed"))
        # 参照の更新は、全区間のリベースが成功した後にだけ行う。
        lines = [f"update {ref} {new_tip} {old_tip}" for ref, old_tip, new_tip in updates if new_tip != old_tip]
        if lines:
            r = subprocess.run(["git", "-C", REPO, "update-ref", "--stdin"], input="\n".join(lines) + "\n",
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode != 0:
                raise ValueError((r.stderr or r.stdout).strip() or tr("rebase_failed"))
        run_git_write("checkout", original_branch)
        run_git_write("branch", "-D", temp)
        temp_exists = False
    except Exception:
        if is_rebasing():
            subprocess.run(["git", "-C", REPO, "rebase", "--abort"], capture_output=True)
        if temp_exists:
            subprocess.run(["git", "-C", REPO, "checkout", original_branch], capture_output=True)
            subprocess.run(["git", "-C", REPO, "branch", "-D", temp], capture_output=True)
        raise
    changed = len([1 for _, old_tip, new_tip in updates if old_tip != new_tip])
    return {"ok": True, "message": tr("rebase_descendants_done", branches=changed, **{"from": a[:7], "to": b[:7]})}


def equivalent_commit_prefix(a, b, allow_merges=False):
    """A/B の共通親から、親相対の差分が連続して一致するコミット数を返す。"""
    try:
        base = git("merge-base", a, b).strip()
    except subprocess.CalledProcessError as e:
        raise UserError("rebase_equivalent_no_base") from e

    def commit_parents(commit):
        return git("rev-list", "--parents", "-n", "1", commit).split()[1:]

    def path_from_base(tip):
        path = git("rev-list", "--reverse", "--first-parent", f"{base}..{tip}").splitlines()
        previous = base
        for commit in path:
            ps = commit_parents(commit)
            if not ps or ps[0] != previous or (len(ps) != 1 and not allow_merges):
                raise UserError("rebase_equivalent_merges")
            previous = commit
        return path

    a_path = path_from_base(a)
    b_path = path_from_base(b)
    matched = 0
    while matched < len(a_path) and matched < len(b_path):
        old_parent = base if matched == 0 else a_path[matched - 1]
        b_parent = base if matched == 0 else b_path[matched - 1]
        a_diff = git("diff", "--no-ext-diff", "--binary", "--full-index", old_parent, a_path[matched])
        b_diff = git("diff", "--no-ext-diff", "--binary", "--full-index", b_parent, b_path[matched])
        if a_diff != b_diff:
            break
        matched += 1
    return base, a_path, b_path, matched


def first_parent_commits(tip):
    """先端からルートまでの first-parent コミットを新しい順に返す。"""
    out = git("log", "--first-parent", "--format=%H%x1f%P%x1f%s", tip)
    empty_tree = subprocess.check_output(
        ["git", "-C", REPO, "hash-object", "-t", "tree", "--stdin"], input=b""
    ).decode("ascii").strip()
    commits = []
    for line in out.splitlines():
        commit, parents, subject = (line.split("\x1f", 2) + ["", "", ""])[:3]
        parent_list = parents.split()
        commits.append({"hash": commit, "parent": parent_list[0] if parent_list else empty_tree,
                        "subject": subject})
    return commits


def nearest_equivalent_parent(a, b):
    """両方の先端から親へ探し、件名とコミット時点のツリーが一致する最初の組を返す。"""
    a_commits = first_parent_commits(a)
    b_commits = first_parent_commits(b)
    for a_index, a_commit in enumerate(a_commits):
        for b_index, b_commit in enumerate(b_commits):
            if a_commit["subject"] != b_commit["subject"]:
                continue
            comparison = subprocess.run(
                ["git", "-C", REPO, "diff", "--quiet", "--no-ext-diff",
                 a_commit["hash"], b_commit["hash"]],
                capture_output=True
            )
            if comparison.returncode == 0:
                return a_commits, b_commits, a_index, b_index
            if comparison.returncode != 1:
                raise ValueError(comparison.stderr.decode("utf-8", errors="replace").strip()
                                 or "git diff failed")
    return a_commits, b_commits, None, None


def api_find_equivalent_parents(body):
    """A/B の先端から親へ探し、件名と親相対差分が一致するコミットを返す。"""
    a = resolve_commit(body.get("from", ""))
    b = resolve_commit(body.get("to", ""))
    try:
        a_commits, b_commits, a_index, b_index = nearest_equivalent_parent(a, b)
    except UserError:
        return {"message": tr("find_equivalent_parents_none")}
    if a_index is None:
        return {"message": tr("find_equivalent_parents_none")}
    new_a, new_b = a_commits[a_index]["hash"], b_commits[b_index]["hash"]
    return {"from": new_a, "to": new_b,
            "message": tr("find_equivalent_parents_found", a=new_a[:7], b=new_b[:7])}


def api_rebase_equivalent(body):
    """A/B の先端から親へ同一差分を探し、A の一致コミット以降を B 側へ寄せる。"""
    if not HAS_WORKTREE:
        raise UserError("rebase_no_worktree")
    a = resolve_commit(body.get("from", ""))
    b = resolve_commit(body.get("to", ""))
    if is_merging() or is_rebasing():
        raise UserError("merge_or_rebase_in_progress")
    head = api_head()
    if not head["branch"]:
        raise UserError("detached_rebase")
    if git("status", "--porcelain", "--untracked-files=all").strip():
        raise UserError("rebase_descendants_dirty")
    if a == b:
        return {"ok": True, "message": tr("rebase_equivalent_noop", **{"from": a[:7], "to": b[:7]})}

    target_branches = []
    for ref in git("for-each-ref", "--format=%(refname)", "refs/heads").splitlines():
        tip = git("rev-parse", ref + "^{commit}").strip()
        if tip == a:
            target_branches.append((ref, tip))
    if not target_branches:
        raise UserError("rebase_equivalent_head", **{"from": a[:7]})

    a_commits, b_commits, a_index, b_index = nearest_equivalent_parent(a, b)
    if a_index is None:
        return {"ok": True, "message": tr("rebase_equivalent_noop", **{"from": a[:7], "to": b[:7]})}

    matched_a = a_commits[a_index]["hash"]
    matched_b = b_commits[b_index]["hash"]
    if matched_a == matched_b:
        return {"ok": True, "message": tr("rebase_equivalent_noop", **{"from": a[:7], "to": b[:7]})}
    # a_commits は先端からの順なので、一致コミットより前が A 側のリベース対象。
    replay = a_commits[:a_index]
    if not replay:
        new_tip = matched_b
        run_git_write("checkout", "--detach", a)
        try:
            for ref, old_tip in target_branches:
                run_git_write("update-ref", ref, new_tip, old_tip)
            run_git_write("checkout", head["branch"])
        except Exception:
            subprocess.run(["git", "-C", REPO, "checkout", head["branch"]], capture_output=True)
            raise
        return {"ok": True, "message": tr("rebase_equivalent_done", **{"from": a[:7], "to": new_tip[:7]}, matched=1)}

    replay_hashes = {c["hash"] for c in replay}
    for commit in replay_hashes:
        parents = git("rev-list", "--parents", "-n", "1", commit).split()[1:]
        if len(parents) > 1:
            raise UserError("rebase_equivalent_merges")

    temp = f"git-graph-rebase-equivalent-{os.getpid()}-{threading.get_ident()}"
    while subprocess.run(["git", "-C", REPO, "show-ref", "--verify", "--quiet", f"refs/heads/{temp}"]).returncode == 0:
        temp += "-x"
    try:
        run_git_write("checkout", "--detach", a)
        run_git_write("checkout", "-b", temp, a)
        r = subprocess.run(["git", "-C", REPO, "rebase", "--onto", matched_b, matched_a],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            if is_rebasing():
                subprocess.run(["git", "-C", REPO, "rebase", "--abort"], capture_output=True)
                raise UserError("rebase_conflict", target=matched_b[:7], cmd="git rebase --onto")
            raise ValueError((r.stdout + r.stderr).strip() or tr("rebase_failed"))
        new_tip = git("rev-parse", "HEAD").strip()
        for ref, old_tip in target_branches:
            run_git_write("update-ref", ref, new_tip, old_tip)
        run_git_write("checkout", head["branch"])
        run_git_write("branch", "-D", temp)
    except Exception:
        if is_rebasing():
            subprocess.run(["git", "-C", REPO, "rebase", "--abort"], capture_output=True)
        subprocess.run(["git", "-C", REPO, "checkout", head["branch"]], capture_output=True)
        subprocess.run(["git", "-C", REPO, "branch", "-D", temp], capture_output=True)
        raise
    return {"ok": True, "message": tr("rebase_equivalent_done", **{"from": a[:7], "to": new_tip[:7]}, matched=1)}


def api_find_equivalent_branch(body):
    """B の子孫ブランチから、A と最も近い連続同一差分の履歴を探す。"""
    a = resolve_commit(body.get("from", ""))
    b = resolve_commit(body.get("to", ""))
    candidates = []
    for ref in git("for-each-ref", "--format=%(refname)", "refs/heads").splitlines():
        name = ref.removeprefix("refs/heads/")
        tip = git("rev-parse", ref + "^{commit}").strip()
        if subprocess.run(["git", "-C", REPO, "merge-base", "--is-ancestor", b, tip],
                          capture_output=True).returncode != 0:
            continue
        if tip == a:
            continue
        a_commits, b_commits, a_index, b_index = nearest_equivalent_parent(a, tip)
        if a_index is not None:
            candidates.append({
                "branch": name,
                "tip": tip,
                "commit": b_commits[b_index]["hash"],
                "matched": 1,
                "remaining": a_index,
            })

    if not candidates:
        return {"message": tr("find_equivalent_branch_none", **{"from": a[:7], "to": b[:7]})}
    nearest = min(c["remaining"] for c in candidates)
    best = [c for c in candidates if c["remaining"] == nearest]
    best.sort(key=lambda c: (-c["matched"], c["branch"]))
    labels = ", ".join(tr("find_equivalent_branch_candidate", branch=c["branch"],
                         commit=c["commit"][:7], matched=c["matched"]) for c in best)
    return {"to": best[0]["tip"],
            "message": tr("find_equivalent_branch_found", branches=labels, remaining=nearest)}


def is_cherry_picking():
    return subprocess.run(["git", "-C", REPO, "rev-parse", "-q", "--verify", "CHERRY_PICK_HEAD"],
                          capture_output=True).returncode == 0


def is_reverting():
    return subprocess.run(["git", "-C", REPO, "rev-parse", "-q", "--verify", "REVERT_HEAD"],
                          capture_output=True).returncode == 0


def api_cherry_pick(body):
    """選んだコミットを、いまチェックアウトしている HEAD にチェリーピックする。
    競合したときと、変更が空になるとき (既に取り込み済み) は、中止して元に戻す。"""
    if not HAS_WORKTREE:
        raise UserError("cherry_no_worktree")
    h = resolve_commit(body.get("hash", ""))
    if is_merging() or is_rebasing() or is_cherry_picking() or is_reverting():
        raise UserError("op_in_progress")
    if not has_head():
        raise UserError("cherry_no_commits")
    if len(git("rev-list", "--parents", "-n", "1", h).split()) > 2:
        raise UserError("cherry_merge_commit")
    before = git("rev-parse", "HEAD").strip()
    r = subprocess.run(["git", "-C", REPO, "cherry-pick", h], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        if is_cherry_picking():
            unmerged = subprocess.run(["git", "-C", REPO, "-c", "core.quotepath=false", "diff", "--name-only", "--diff-filter=U"],
                                      capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
            subprocess.run(["git", "-C", REPO, "cherry-pick", "--abort"], capture_output=True)
            if not unmerged:  # 競合ではなく、変更が空になった
                raise UserError("cherry_empty", hash=h[:7])
            raise UserError("cherry_conflict", hash=h[:7])
        raise ValueError(out or tr("cherry_failed"))
    if git("rev-parse", "HEAD").strip() == before:
        return {"ok": True, "message": tr("cherry_noop", hash=h[:7])}
    return {"ok": True, "message": tr("cherry_done", hash=h[:7])}


def api_revert(body):
    """選んだコミットの変更を打ち消すコミットを、いまの HEAD の上に作る (git revert)。
    競合したときと、変更が空になるとき (既に打ち消し済み) は、中止して元に戻す。マージコミットは第1親を基準にする。"""
    if not HAS_WORKTREE:
        raise UserError("revert_no_worktree")
    h = resolve_commit(body.get("hash", ""))
    if is_merging() or is_rebasing() or is_cherry_picking() or is_reverting():
        raise UserError("op_in_progress")
    if not has_head():
        raise UserError("revert_no_commits")
    is_merge = len(git("rev-list", "--parents", "-n", "1", h).split()) > 2
    args = ["revert", "--no-edit"] + (["-m", "1"] if is_merge else [])
    r = subprocess.run(["git", "-C", REPO, *args, h], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        if is_reverting():
            unmerged = subprocess.run(["git", "-C", REPO, "-c", "core.quotepath=false", "diff", "--name-only", "--diff-filter=U"],
                                      capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
            subprocess.run(["git", "-C", REPO, "revert", "--abort"], capture_output=True)
            if not unmerged:  # 競合ではなく、変更が空になった
                raise UserError("revert_empty", hash=h[:7])
            raise UserError("revert_conflict", hash=h[:7])
        raise ValueError(out or tr("revert_failed"))
    return {"ok": True, "message": tr("revert_done_merge" if is_merge else "revert_done", hash=h[:7])}


def api_abort(body):
    """進行中のマージ / リベース / チェリーピック / リバートを中断して、開始前の状態に戻す。"""
    if not HAS_WORKTREE:
        raise UserError("abort_no_worktree")
    for cmd, active in (("merge", is_merging), ("rebase", is_rebasing),
                        ("cherry-pick", is_cherry_picking), ("revert", is_reverting)):
        if active():
            run_git_write(cmd, "--abort")
            return {"ok": True, "message": tr("aborted", cmd=cmd)}
    raise UserError("nothing_to_abort")


def unmerged_stages(path):
    """競合しているファイルが持つインデックスのステージ番号 (2 = ours, 3 = theirs)。片側で削除されていると、その番号がない。"""
    out = git("ls-files", "-u", "-z", "--", path, literal=True)
    return {rec.split("\t", 1)[0].split()[-1] for rec in out.split("\0") if "\t" in rec}


def api_resolve(body):
    """競合しているファイルを、ours か theirs の内容で丸ごと置き換えて解決済みにする (片側で削除されていたら削除する)。"""
    if not WIP_ENABLED:
        raise UserError("wip_disabled")
    side = body.get("side")
    if side not in ("ours", "theirs"):
        raise UserError("resolve_side_invalid")
    paths = body.get("paths")
    if not isinstance(paths, list) or not paths or not all(isinstance(p, str) for p in paths):
        raise UserError("select_files")
    s = parse_status(read_status_raw(), limit=10 ** 9)
    bad = set(paths) - {f["path"] for f in s["conflicts"]}  # 任意のパスを触らせない。いま競合と報告されているものだけ
    if bad:
        raise UserError("resolve_not_conflict", files=", ".join(sorted(bad)[:3]))
    stage = "2" if side == "ours" else "3"
    keep, remove = [], []
    for p in sorted(set(paths)):
        (keep if stage in unmerged_stages(p) else remove).append(p)
    step = 200
    for i in range(0, len(keep), step):
        part = keep[i:i + step]
        run_git_write("checkout", "--" + side, "--", *part, literal=True)
        run_git_write("add", "--", *part, literal=True)
    for i in range(0, len(remove), step):
        run_git_write("rm", "-q", "-f", "--", *remove[i:i + step], literal=True)
    return {"ok": True, "message": tr("resolved", n=len(keep) + len(remove), side=side)}


def paths_in_head(paths):
    """paths のうち HEAD に存在するもの。"""
    found = set()
    if not has_head():
        return found
    for i in range(0, len(paths), 200):
        out = git_bytes("ls-tree", "-r", "-z", "--name-only", "HEAD", "--", *paths[i:i + 200], literal=True)
        found.update(x for x in out.decode("utf-8", errors="replace").split("\0") if x)
    return found


def api_discard(body):
    """選んだファイルの変更を破棄する (取り消せない)。追跡中のファイルはステージ済み・未ステージとも HEAD の状態に戻し、
    HEAD にない新規ファイル (ステージ済み) は削除し、未追跡ファイルは削除する。競合中のファイルと、マージなどの途中は対象外。"""
    if not WIP_ENABLED:
        raise UserError("wip_disabled")
    if is_merging() or is_rebasing() or is_cherry_picking() or is_reverting():
        raise UserError("discard_blocked")
    paths = body.get("paths")
    if not isinstance(paths, list) or not paths or not all(isinstance(p, str) for p in paths):
        raise UserError("select_files")
    checked = set(paths)
    s = parse_status(read_status_raw(), limit=10 ** 9)
    conflict = checked & {f["path"] for f in s["conflicts"]}
    if conflict:
        raise UserError("discard_conflict", files=", ".join(sorted(conflict)[:3]))
    untracked = {f["path"] for f in s["untracked"]}
    old_of = {f["path"]: f.get("old_path") for f in s["staged"]}
    known = untracked | {f["path"] for k in ("staged", "unstaged") for f in s[k]}
    bad = checked - known  # 任意のパスを消させない。いま変更ありと報告されているものだけ
    if bad:
        raise UserError("files_unchanged", files=", ".join(sorted(bad)[:3]))
    tracked = sorted(checked - untracked)
    in_head = paths_in_head(tracked)
    clean = sorted(checked & untracked)
    remove, restore = [], set()
    for p in tracked:
        if old_of.get(p):  # リネームは元のパスも戻す
            restore.add(old_of[p])
        (restore.add(p) if p in in_head else remove.append(p))
    step = 200
    for i in range(0, len(remove), step):  # HEAD にないファイル (git add 済みの新規ファイル): インデックスと作業ツリーから消す
        run_git_write("rm", "-q", "-f", "--", *remove[i:i + step], literal=True)
    restore = sorted(restore)
    for i in range(0, len(restore), step):
        run_git_write("checkout", "HEAD", "--", *restore[i:i + step], literal=True)
    for i in range(0, len(clean), step):
        run_git_write("clean", "-f", "-q", "--", *clean[i:i + step], literal=True)
    return {"ok": True, "message": tr("discarded", n=len(checked))}


def api_branch_rename(body):
    """ローカルブランチの名前を変える (git branch -m)。リモートのブランチは変わらない。"""
    old = str(body.get("old", "")).strip()
    new = str(body.get("new", "")).strip()
    if not old or "\0" in old:
        raise UserError("branch_name_invalid")
    check_ref_name("branch", new)
    exists = lambda name: subprocess.run(["git", "-C", REPO, "show-ref", "--verify", "--quiet", "refs/heads/" + name],
                                         capture_output=True).returncode == 0
    if not exists(old):
        raise UserError("ref_not_exist", name=repr(old))
    if old == new:
        raise UserError("branch_rename_same")
    if exists(new):
        raise UserError("branch_exists", name=repr(new))
    has_remote = bool(branch_config(old, "remote"))
    run_git_write("branch", "-m", "--", old, new)
    return {"ok": True, "message": tr("branch_renamed_remote" if has_remote else "branch_renamed", old=old, new=new)}


REBASE_I_MAX = 200  # 対話的リベースで一度に扱うコミット数の上限
REBASE_I_ACTIONS = ("pick", "reword", "squash", "fixup", "drop")


def rebase_todo_hashes(base):
    """git rebase -i <base> が最初に作る todo と同じコミット列 (古い順)。"""
    out = git("rev-list", "--reverse", "--topo-order", "--right-only", "--cherry-pick",
              "--no-merges", base + "...HEAD")
    return out.split()


def check_rebase_i_range(base):
    """対話的リベースできる範囲か調べて、対象コミットのハッシュ (古い順) を返す。"""
    r = subprocess.run(["git", "-C", REPO, "rev-list", "--count", "--merges", base + "..HEAD"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode == 0 and r.stdout.strip() not in ("", "0"):
        raise UserError("ri_merges")
    hashes = rebase_todo_hashes(base)
    if not hashes:
        raise UserError("ri_nothing", base=base[:7])
    if len(hashes) > REBASE_I_MAX:
        raise UserError("ri_too_many", n=len(hashes), max=REBASE_I_MAX)
    return hashes


def api_rebase_todo(q):
    """対話的リベースの対象コミット一覧 (画面の編集ダイアログに出す)。"""
    if not HAS_WORKTREE:
        raise UserError("rebase_no_worktree")
    base = resolve_commit((q.get("hash") or [""])[0])
    head = api_head()
    if not head["branch"]:
        raise UserError("detached_rebase")
    hashes = check_rebase_i_range(base)
    out = git("log", "--no-walk=unsorted", "--date=short", "--format=%H%x1f%an%x1f%ad%x1f%s%x1f%B%x1e", *hashes)
    commits = []
    for rec in out.split("\x1e"):
        rec = rec.lstrip("\n")
        if not rec:
            continue
        h, an, ad, subj, msg = rec.split("\x1f", 4)
        commits.append({"hash": h, "short": h[:7], "author": an, "date": ad, "subject": subj, "message": msg.strip()})
    return {"base": base, "branch": head["branch"], "commits": commits}


def api_rebase_i(body):
    """git rebase -i 相当。画面で編集した todo (順序と pick/reword/squash/fixup/drop) をそのまま実行する。
    エディタは起こさず、todo は GIT_SEQUENCE_EDITOR で差し替える。reword は、そのコミット (squash/fixup を
    まとめた後) に exec で commit --amend -F を挟んで新しいメッセージにする。
    競合やエラーで止まったときは自動で rebase --abort して元に戻す。"""
    if not HAS_WORKTREE:
        raise UserError("rebase_no_worktree")
    base = resolve_commit(body.get("hash", ""))
    if is_merging() or is_rebasing():
        raise UserError("merge_or_rebase_in_progress")
    head = api_head()
    if not head["branch"]:
        raise UserError("detached_rebase")
    if not has_head():
        raise UserError("rebase_no_commits")
    items = body.get("todo")
    if not isinstance(items, list) or not items or not all(isinstance(i, dict) for i in items):
        raise UserError("todo_invalid")
    todo = []
    for it in items:
        act, h = it.get("action"), str(it.get("hash", ""))
        if act not in REBASE_I_ACTIONS or not re.fullmatch(r"[0-9a-f]{40}", h):
            raise UserError("todo_invalid")
        msg = str(it.get("message") or "").strip()
        if act == "reword" and not msg:
            raise UserError("ri_reword_msg_required", hash=h[:7])
        todo.append((act, h, msg))
    expected = check_rebase_i_range(base)
    if sorted(h for _, h, _ in todo) != sorted(expected):
        raise UserError("ri_changed")
    kept = [t for t in todo if t[0] != "drop"]
    if kept and kept[0][0] in ("squash", "fixup"):
        raise UserError("ri_first_squash")

    def fwd(path):  # sh から使うので区切りは / にそろえる (Windows 対策)
        return path.replace("\\", "/")

    before = git("rev-parse", "HEAD").strip()
    tmp = tempfile.mkdtemp(prefix="git_graph_rebase_")
    try:
        lines, pending = [], None  # pending: 直前のグループの reword 用 exec 行
        for act, h, msg in todo:
            if act in ("pick", "reword"):
                if pending:
                    lines.append(pending)
                    pending = None
                lines.append(f"pick {h}")
                if act == "reword":
                    mp = os.path.join(tmp, f"msg{len(lines)}.txt")
                    with open(mp, "w", encoding="utf-8", newline="\n") as f:
                        f.write(msg + "\n")
                    pending = f'exec git commit --amend -q -F "{fwd(mp)}"'
            else:  # squash / fixup / drop
                lines.append(f"{act} {h}")
        if pending:
            lines.append(pending)
        todo_path = os.path.join(tmp, "todo.txt")
        with open(todo_path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
        script = os.path.join(tmp, "seq_editor.py")
        with open(script, "w", encoding="utf-8") as f:
            f.write("import shutil, sys\nshutil.copyfile(sys.argv[1], sys.argv[2])\n")  # argv[2] が git の todo
        env = dict(os.environ, GIT_EDITOR=":", GIT_TERMINAL_PROMPT="0",
                   GIT_SEQUENCE_EDITOR=f'"{fwd(sys.executable)}" "{fwd(script)}" "{fwd(todo_path)}"')
        r = subprocess.run(["git", "-C", REPO, "rebase", "-i", base], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env)
        out = (r.stdout + r.stderr).strip()
        if r.returncode != 0:
            if is_rebasing():  # 競合など → 元に戻す
                subprocess.run(["git", "-C", REPO, "rebase", "--abort"], capture_output=True)
                tail = "\n".join(out.splitlines()[-6:])
                raise UserError("ri_aborted", tail=tail)
            raise ValueError(out or tr("rebase_failed"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if git("rev-parse", "HEAD").strip() == before:
        return {"ok": True, "message": tr("ri_noop", branch=head["branch"])}
    n = Counter(a for a, _, _ in todo)
    parts = [tr(key, n=n[k]) for k, key in (("reword", "ri_part_reword"), ("squash", "ri_part_squash"),
                                           ("fixup", "ri_part_fixup"), ("drop", "ri_part_drop")) if n[k]]
    if parts:
        return {"ok": True, "message": tr("ri_done_parts", branch=head["branch"], base=base[:7], parts=" / ".join(parts))}
    return {"ok": True, "message": tr("ri_done", branch=head["branch"], base=base[:7])}


# --------------------------------------------------------------------------
# リモートとの通信 (fetch / pull / push)
# --------------------------------------------------------------------------
NET_TIMEOUT = 300  # 秒。認証待ちなどで固まったままロックを握り続けないようにする
HTTP_AUTH_CACHE = {}  # アプリ実行中だけ保持する remote 名 -> {username, password}


def run_git_net(*args, timeout=None, auth=None):
    """リモートと通信する git を実行する。
    SSH の未知ホストは初回だけ known_hosts に登録し、登録済みホストの
    鍵が変わった場合は SSH に拒否させる。必要なら一時的な HTTP 認証用
    askpass を使う。
    """
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    # stdin は閉じて実行するため、SSH の yes/no 確認プロンプトは表示できない。
    # accept-new なら未知ホストだけを自動登録し、鍵が変わったホストは拒否する。
    # ユーザーが独自の SSH 設定を指定している場合はそれを優先する。
    env.setdefault("GIT_SSH_COMMAND", "ssh -o StrictHostKeyChecking=accept-new")
    timeout = timeout or NET_TIMEOUT
    askpass_dir = None
    if auth:
        if not isinstance(auth, dict) or not isinstance(auth.get("username", ""), str) or not isinstance(auth.get("password", ""), str):
            raise UserError("auth_invalid")
        askpass_dir = tempfile.mkdtemp(prefix="git_graph_askpass_")
        askpass = os.path.join(askpass_dir, "askpass.py")
        with open(askpass, "w", encoding="utf-8") as f:
            f.write("#!/usr/bin/env python3\nimport os, sys\np = (sys.argv[1] if len(sys.argv) > 1 else '').lower()\nprint(os.environ.get('GIT_GRAPH_HTTP_USERNAME' if 'username' in p or 'user' in p else 'GIT_GRAPH_HTTP_PASSWORD', ''))\n")
        os.chmod(askpass, 0o700)
        env.update(GIT_ASKPASS=askpass,
                   GIT_GRAPH_HTTP_USERNAME=auth.get("username", ""),
                   GIT_GRAPH_HTTP_PASSWORD=auth.get("password", ""))
    try:
        return subprocess.run(["git", "-C", REPO, *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env, timeout=timeout,
                              stdin=subprocess.DEVNULL, start_new_session=True)  # 制御端末を切り離し ssh が /dev/tty に尋ねないように
    except subprocess.TimeoutExpired:
        raise UserError("net_timeout", cmd=args[0], sec=timeout)
    finally:
        if askpass_dir:
            shutil.rmtree(askpass_dir, ignore_errors=True)


def net_error(r, fallback):
    text = (r.stderr or r.stdout).strip()
    text = "\n".join(l for l in text.splitlines() if not l.startswith("hint:")).strip()  # 長い hint は省く
    return text or fallback


def is_http_auth_error(r):
    text = ((r.stderr or "") + "\n" + (r.stdout or "")).lower()
    return any(s in text for s in (
        "authentication failed", "invalid username or token", "could not read username",
        "terminal prompts disabled", "http 401", "http 403",
    ))


def auth_for_remote(remote, body):
    """明示された認証情報を優先し、なければアプリ実行中のキャッシュを使う。"""
    if body.get("auth") and (body.get("auth_remote") is None or body.get("auth_remote") == remote):
        return body["auth"]
    return HTTP_AUTH_CACHE.get(remote)


def remember_auth(remote, auth):
    if auth:
        HTTP_AUTH_CACHE[remote] = {"username": auth.get("username", ""), "password": auth.get("password", "")}


def refs_snapshot():
    r = subprocess.run(["git", "-C", REPO, "for-each-ref", "--format=%(objectname) %(refname)", "refs/remotes", "refs/tags"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return {name: oid for oid, _, name in (l.partition(" ") for l in r.stdout.splitlines())}


def count_changed_refs(before, after):
    return sum(1 for k in set(before) | set(after) if before.get(k) != after.get(k))


def upstream_of_head():
    r = subprocess.run(["git", "-C", REPO, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout.strip() if r.returncode == 0 else None


def api_fetch(body):
    remotes = git("remote").split()
    before = refs_snapshot()
    errors = []
    for remote in remotes:
        args = ["fetch", remote]
        if body.get("prune") is True:
            args.append("--prune")
        auth = auth_for_remote(remote, body)
        r = run_git_net(*args, auth=auth)
        if r.returncode != 0:
            if is_http_auth_error(r) and (not body.get("auth_retry") or body.get("auth_remote") != remote):
                return {"auth_required": True, "auth_remote": remote}
            if body.get("auth_retry") and body.get("auth_remote") == remote:
                HTTP_AUTH_CACHE.pop(remote, None)
            errors.append(r)
        else:
            remember_auth(remote, auth)
    n = count_changed_refs(before, refs_snapshot())
    if errors:  # 一部のリモートだけ失敗しても、残りのリモートは取得する
        raise ValueError(net_error(errors[0], tr("fetch_failed")) + (tr("fetch_partial", n=n) if n else ""))
    return {"ok": True, "message": tr("fetch_done", n=n) if n else tr("fetch_done_none")}


def list_remotes():
    """登録済みのリモート [{name, url}] (url は fetch 用)。"""
    out = {}
    for line in git("remote", "-v").splitlines():
        m = re.fullmatch(r"(\S+)\t(.*) \((fetch|push)\)", line)
        if m and (m.group(3) == "fetch" or m.group(1) not in out):
            out[m.group(1)] = m.group(2)
    for name in git("remote").split():  # url が無いリモートも出す
        out.setdefault(name, "")
    return [{"name": n, "url": u} for n, u in out.items()]


def api_remotes(q):
    return {"remotes": list_remotes()}


def check_remote_name(name):
    if not isinstance(name, str) or not name or name.startswith("-") or "\0" in name or name != name.strip():
        raise UserError("remote_name_invalid")
    r = subprocess.run(["git", "-C", REPO, "check-ref-format", "refs/remotes/" + name + "/HEAD"], capture_output=True)
    if r.returncode != 0:
        raise UserError("remote_name_unusable", name=repr(name))


def api_remote_add(body):
    """リモートを追加する。fetch:true なら続けて fetch する (fetch に失敗してもリモートは追加済み)。"""
    name, url = body.get("name"), body.get("url")
    check_remote_name(name)
    if not isinstance(url, str) or not url.strip() or "\0" in url or "\n" in url:
        raise UserError("url_invalid")
    url = url.strip()
    if url.startswith("-") or re.match(r"(ext|fd)::", url):  # オプション注入と、任意コマンドを実行できる転送方式を避ける
        raise UserError("url_unusable")
    if name in {r["name"] for r in list_remotes()}:
        raise UserError("remote_exists", name=name)
    run_git_write("remote", "add", "--", name, url)
    msg = tr("remote_added", name=name)
    if body.get("fetch") is True:
        r = run_git_net("fetch", name)
        if r.returncode != 0:
            return {"ok": True, "fetch_failed": True,
                    "message": tr("remote_added_fetch_failed", name=name, error=net_error(r, tr("fetch_failed")))}
        msg = tr("remote_added_fetched", name=name)
    return {"ok": True, "message": msg}


def api_remote_remove(body):
    """リモートを削除する (リモート追跡ブランチと設定も消える。リモート側は変わらない)。"""
    name = body.get("name")
    if not isinstance(name, str) or name not in {r["name"] for r in list_remotes()}:  # 登録済みの名前だけ許可
        raise UserError("remote_not_found")
    run_git_write("remote", "remove", "--", name)
    return {"ok": True, "message": tr("remote_removed", name=name)}


def api_pull(body):
    """現在のブランチに upstream の変更を取り込む。
    merge で競合したときはマージ途中で止める (WIP で解決)。rebase で競合したときは中止して元に戻す。"""
    if not HAS_WORKTREE:
        raise UserError("pull_no_worktree")
    mode = body.get("mode", "ff-only")
    flags = {"ff-only": ["--ff-only"], "merge": ["--no-rebase", "--no-edit"], "rebase": ["--rebase"]}.get(str(mode))
    if flags is None:
        raise UserError("pull_mode_invalid")
    if is_merging() or is_rebasing():
        raise UserError("merge_or_rebase_in_progress")
    if not has_head():
        raise UserError("pull_no_commits")
    branch = api_head()["branch"]
    if not branch:
        raise UserError("detached_pull")
    up = upstream_of_head()
    if not up:
        raise UserError("no_upstream", branch=branch)
    remote = branch_config(branch, "remote")
    before = git("rev-parse", "HEAD").strip()
    auth = auth_for_remote(remote, body) if remote and remote != "." else None
    r = run_git_net("pull", *flags, auth=auth)
    if r.returncode != 0:
        if is_http_auth_error(r) and (not body.get("auth_retry") or body.get("auth_remote") != remote):
            return {"auth_required": True, "auth_remote": remote}
        if body.get("auth_retry") and body.get("auth_remote") == remote:
            HTTP_AUTH_CACHE.pop(remote, None)
        if is_merging():  # 競合 (自動マージできなかった)
            return {"ok": True, "conflict": True,
                    "message": tr("merge_conflict", target=up)}
        if is_rebasing():  # 競合 → 元に戻す
            subprocess.run(["git", "-C", REPO, "rebase", "--abort"], capture_output=True)
            raise UserError("rebase_conflict", target=up, cmd="git pull --rebase")
        raise ValueError(net_error(r, tr("pull_failed")) + (tr("pull_diverged_hint") if mode == "ff-only" else ""))
    if remote and remote != ".":
        remember_auth(remote, auth)
    if git("rev-parse", "HEAD").strip() == before:
        return {"ok": True, "message": tr("pull_uptodate", branch=branch)}
    return {"ok": True, "message": tr("pull_done", branch=branch, up=up)}


def branch_config(branch, key):
    r = subprocess.run(["git", "-C", REPO, "config", "--get", f"branch.{branch}.{key}"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout.strip() if r.returncode == 0 else None


def git_config(key):
    r = subprocess.run(["git", "-C", REPO, "config", "--get", key],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout.strip() if r.returncode == 0 else None


def api_push(body):
    """ブランチを push する。body.branch があればそのローカルブランチ (チェックアウト中でなくてもよい)、
    なければ現在のブランチ。body.remote が指定された場合は、その remote の同名ブランチへ push する。"""
    if not has_head():
        raise UserError("push_no_commits")
    branch = body.get("branch")
    if branch:
        if not isinstance(branch, str) or branch.startswith("-"):
            raise UserError("branch_name_invalid")
        if subprocess.run(["git", "-C", REPO, "rev-parse", "-q", "--verify", f"refs/heads/{branch}"],
                          capture_output=True).returncode != 0:
            raise UserError("local_branch_missing", branch=branch)
    else:
        branch = api_head()["branch"]
        if not branch:
            raise UserError("detached_push")
    args = ["push"]
    if body.get("force") is True:
        args.append("--force-with-lease")
    remotes = git("remote").split()
    if not remotes:
        raise UserError("no_remotes")
    upstream_remote, merge = branch_config(branch, "remote"), branch_config(branch, "merge")
    has_upstream = bool(upstream_remote and upstream_remote != "." and merge)
    # push 先: branch.<名前>.pushRemote > remote.pushDefault (git の優先順位と同じ)
    push_remote = branch_config(branch, "pushRemote") or git_config("remote.pushDefault")
    selected_remote = body.get("remote")
    if selected_remote is not None:
        if not isinstance(selected_remote, str) or selected_remote not in remotes:
            raise UserError("remote_unregistered", remote=repr(selected_remote))
        remote = selected_remote
        remote_branch = body.get("remote_branch")
        if remote_branch is None:
            remote_branch = merge[len("refs/heads/"):] if has_upstream and remote == upstream_remote and merge.startswith("refs/heads/") else branch
        if not isinstance(remote_branch, str) or not remote_branch:
            raise UserError("branch_name_invalid")
        check_ref_name("branch", remote_branch)
        dest = f"refs/heads/{remote_branch}"
        args += (["-u"] if not has_upstream else []) + [remote, f"refs/heads/{branch}:{dest}"]
    elif push_remote:
        if push_remote not in remotes:
            raise UserError("push_remote_unregistered", remote=repr(push_remote))
        remote = push_remote
        # upstream と同じリモートなら upstream のブランチ名へ、別のリモート (fork など) なら同名のブランチへ
        dest = merge if (has_upstream and push_remote == upstream_remote) else f"refs/heads/{branch}"
        args += [remote, f"refs/heads/{branch}:{dest}"]
    elif has_upstream:
        remote = upstream_remote
        args += [remote, f"refs/heads/{branch}:{merge}"]  # upstream が別名でもその先へ push する
    else:
        default = "origin" if "origin" in remotes else remotes[0]
        chosen = body.get("remote")
        if body.get("set_upstream") is not True:
            return {"needs_upstream": True, "remote": default, "remotes": remotes, "branch": branch}
        if chosen is None:
            chosen = default
        if not isinstance(chosen, str) or chosen not in remotes:  # 登録済みの名前だけ許可 (オプション注入も防ぐ)
            raise UserError("remote_unregistered", remote=repr(chosen))
        remote = chosen
        args += ["-u", remote, f"refs/heads/{branch}:refs/heads/{branch}"]
    before = refs_snapshot()
    auth = auth_for_remote(remote, body)
    r = run_git_net(*args, auth=auth)
    if r.returncode != 0:
        if not body.get("auth_retry") and is_http_auth_error(r):
            return {"auth_required": True, "auth_remote": remote}
        if body.get("auth_retry"):
            HTTP_AUTH_CACHE.pop(remote, None)
        raise ValueError(net_error(r, tr("push_failed")))
    remember_auth(remote, auth)
    changed = count_changed_refs(before, refs_snapshot()) > 0
    where = f" ({remote})" if len(remotes) > 1 else ""
    return {"ok": True, "message": tr("pushed" if changed else "pushed_nothing", branch=branch, where=where)}


def api_push_tag(body):
    """タグを push する。push 先のリモートが決まらない (複数あって pushDefault も無い) ときは、選択肢を返す。"""
    tag = body.get("tag")
    if not isinstance(tag, str) or not tag or tag.startswith("-"):
        raise UserError("tag_name_invalid")
    if subprocess.run(["git", "-C", REPO, "rev-parse", "-q", "--verify", f"refs/tags/{tag}"],
                      capture_output=True).returncode != 0:
        raise UserError("tag_missing", tag=tag)
    remotes = git("remote").split()
    if not remotes:
        raise UserError("no_remotes")
    chosen = body.get("remote")
    if chosen is None:
        default = git_config("remote.pushDefault")
        if len(remotes) == 1:
            chosen = remotes[0]
        elif default in remotes:
            chosen = default
        else:
            return {"needs_remote": True, "tag": tag, "remotes": remotes,
                    "remote": "origin" if "origin" in remotes else remotes[0]}
    if not isinstance(chosen, str) or chosen not in remotes:  # 登録済みの名前だけ許可 (オプション注入も防ぐ)
        raise UserError("remote_unregistered", remote=repr(chosen))
    auth = auth_for_remote(chosen, body)
    r = run_git_net("push", chosen, f"refs/tags/{tag}:refs/tags/{tag}", auth=auth)
    if r.returncode != 0:
        if not body.get("auth_retry") and is_http_auth_error(r):
            return {"auth_required": True, "auth_remote": chosen}
        if body.get("auth_retry"):
            HTTP_AUTH_CACHE.pop(chosen, None)
        raise ValueError(net_error(r, tr("push_tag_failed")))
    remember_auth(chosen, auth)
    where = f" ({chosen})" if len(remotes) > 1 else ""
    up_to_date = "up-to-date" in (r.stderr + r.stdout).lower() or "up to date" in (r.stderr + r.stdout).lower()
    return {"ok": True, "message": tr("pushed_tag_nothing" if up_to_date else "pushed_tag", tag=tag, where=where)}


POST_ROUTES = {"/api/stash": api_stash, "/api/rebase": api_rebase, "/api/rebase-range": api_rebase_range, "/api/rebase-descendants": api_rebase_descendants, "/api/rebase-equivalent": api_rebase_equivalent, "/api/find-equivalent-parents": api_find_equivalent_parents, "/api/find-equivalent-branch": api_find_equivalent_branch, "/api/rebase-i": api_rebase_i, "/api/cherry-pick": api_cherry_pick, "/api/revert": api_revert, "/api/abort": api_abort, "/api/resolve": api_resolve, "/api/discard": api_discard, "/api/branch-rename": api_branch_rename, "/api/merge": api_merge, "/api/branch": api_branch, "/api/tag": api_tag, "/api/ref-delete": api_delete_ref,
               "/api/checkout": api_checkout, "/api/reset": api_reset, "/api/wip-commit": api_wip_commit,
               "/api/fetch": api_fetch, "/api/pull": api_pull, "/api/push": api_push, "/api/push-tag": api_push_tag, "/api/file-copy": api_file_copy,
               "/api/remote-add": api_remote_add, "/api/remote-remove": api_remote_remove}


DEFAULT_PORT = 8000
SERVER = None


class QuietServer(ThreadingHTTPServer):
    # Windows では SO_REUSEADDR が「使用中のポートへの二重 bind」を許してしまうため、無効にする
    allow_reuse_address = os.name != "nt"
    daemon_threads = True


def api_shutdown(body):
    """終了要求を返す。サーバーの停止は HTTP 応答の送信後に行う。"""
    return {"ok": True}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, ctype, body):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path == "/":
                self._send(200, "text/html", render_main_page())
            elif u.path == "/static/app.js":
                self._send(200, "application/javascript", render_app_js())
            elif u.path == "/static/app.css":
                self._send(200, "text/css", APP_CSS)
            elif u.path == "/api/log":
                self._send(200, "application/json", json.dumps(api_log(q), ensure_ascii=False))
            elif u.path == "/api/stash-list":
                self._send(200, "application/json", json.dumps(api_stash_list(q), ensure_ascii=False))
            elif u.path == "/api/branches":
                self._send(200, "application/json", json.dumps(api_branches(q), ensure_ascii=False))
            elif u.path == "/api/branches-containing":
                self._send(200, "application/json", json.dumps(api_branches_containing(q), ensure_ascii=False))
            elif u.path == "/api/tags":
                self._send(200, "application/json", json.dumps(api_tags(q), ensure_ascii=False))
            elif u.path == "/api/reflog":
                self._send(200, "application/json", json.dumps(api_reflog(q), ensure_ascii=False))
            elif u.path == "/api/status":
                self._send(200, "application/json", json.dumps(api_status(q), ensure_ascii=False))
            elif u.path == "/api/remotes":
                self._send(200, "application/json", json.dumps(api_remotes(q), ensure_ascii=False))
            elif u.path == "/api/graph":
                self._send(200, "application/json", graph_payload())
            elif u.path == "/api/rebase-todo":
                self._send(200, "application/json", json.dumps(api_rebase_todo(q), ensure_ascii=False))
            elif u.path == "/api/head":
                self._send(200, "application/json", json.dumps(api_head(q), ensure_ascii=False))
            elif u.path == "/api/compare":
                self._send(200, "application/json", json.dumps(api_compare(q), ensure_ascii=False))
            elif u.path == "/api/merge-base":
                self._send(200, "application/json", json.dumps(api_merge_base(q), ensure_ascii=False))
            elif u.path == "/api/commit":
                self._send(200, "application/json", json.dumps(api_commit(q), ensure_ascii=False))
            elif u.path == "/api/tree":
                self._send(200, "application/json", json.dumps(api_tree(q), ensure_ascii=False))
            elif u.path == "/api/file":
                self._send(200, "application/json", json.dumps(api_file(q), ensure_ascii=False))
            elif u.path == "/files":
                h = resolve_commit(q.get("hash", [""])[0])
                self._send(200, "text/html", render_files_page().replace("__HASH__", html.escape(h)))
            elif u.path == "/diff":
                self._send(200, "text/html", render_diff_page(q))
            else:
                self._send(404, "text/plain", "not found")
        except ValueError as e:
            self._send(400, "text/plain", str(e))
        except FileNotFoundError:
            self._send(500, "text/plain", tr("dot_missing"))
        except subprocess.CalledProcessError as e:
            self._send(500, "text/plain", f"command failed: {e.cmd}\n{e.stderr or ''}")

    def _check_post(self, shutdown=False):
        """他サイトのページからの書き込みを防ぐ (CSRF / DNS rebinding 対策)。"""
        if shutdown:  # 終了は --read-only でもできる。ただし他の端末から止められないよう、ローカルで待ち受けているときだけ
            if not (WRITABLE or is_loopback(OPTS.host)):
                raise Forbidden(tr("shutdown_forbidden"))
        elif not WRITABLE:
            raise Forbidden(tr("write_disabled"))
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        if ctype != "application/json":  # 他サイトからは preflight が必要になるため送れない
            raise UserError("content_type_json")
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        if origin is not None and origin != "http://" + host:
            raise Forbidden(tr("origin_mismatch"))
        if is_loopback(OPTS.host):
            name = host if host.endswith("]") or ":" not in host else host.rsplit(":", 1)[0]
            if name not in ("127.0.0.1", "localhost", "[::1]"):
                raise Forbidden(tr("host_invalid"))

    def do_POST(self):
        try:
            path = urlparse(self.path).path
            route = POST_ROUTES.get(path) or (api_shutdown if path == "/api/shutdown" else None)
            if route is None:
                self._send(404, "text/plain", "not found")
                return
            self._check_post(shutdown=path == "/api/shutdown")
            n = int(self.headers.get("Content-Length") or 0)
            if n > 100000:
                raise ValueError("body too large")
            body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("bad body")
            with _write_lock:
                res = route(body)
            self._send(200, "application/json", json.dumps(res, ensure_ascii=False))
            if path == "/api/shutdown" and SERVER is not None:
                threading.Thread(target=SERVER.shutdown, daemon=True).start()
        except Forbidden as e:
            self._send(403, "text/plain", str(e))
        except ValueError as e:  # JSON の不正も含む
            self._send(400, "text/plain", str(e))

    def log_message(self, fmt, *args):  # アクセスログは出さない
        pass


def main():
    global REPO, OPTS, UI_LANG, WRITABLE, WIP_ENABLED, HAS_WORKTREE, PROJECT_NAME
    pre = argparse.ArgumentParser(add_help=False, allow_abbrev=False)  # --lan を --lang の省略形と誤認しない  # --help の文言も言語に合わせるため、先に --lang だけ読む
    pre.add_argument("--lang", choices=sorted(TRANSLATIONS))
    UI_LANG = detect_language(pre.parse_known_args()[0].lang)
    ap = argparse.ArgumentParser(description="git commit graph server")
    ap.add_argument("revs", nargs="*", help=tr("cli_help_revs"))
    ap.add_argument("-C", dest="repo", default=".", help=tr("cli_help_repo"))
    ap.add_argument("--host", default="127.0.0.1", help=tr("cli_help_host"))
    ap.add_argument("--port", type=int, default=None, help=tr("cli_help_port"))
    ap.add_argument("--open", action="store_true", help=tr("cli_help_open"))
    ap.add_argument("--no-remotes", action="store_true", help=tr("cli_help_no_remotes"))
    ap.add_argument("--no-tags", action="store_true", help=tr("cli_help_no_tags"))
    ap.add_argument("--include-tips", action="store_true", help=tr("cli_help_include_tips"))
    ap.add_argument("--full-layout", action="store_true", help=tr("cli_help_full_layout"))
    ap.add_argument("--no-count", action="store_true", help=tr("cli_help_no_count"))
    ap.add_argument("--rankdir", default="BT", help=tr("cli_help_rankdir"))
    ap.add_argument("--font", default="Helvetica", help=tr("cli_help_font"))
    ap.add_argument("--dot", action="store_true", help=tr("cli_help_dot"))
    ap.add_argument("--no-wip", action="store_true", help=tr("cli_help_no_wip"))
    ap.add_argument("--read-only", action="store_true", help=tr("cli_help_read_only"))
    ap.add_argument("--allow-write", action="store_true", help=tr("cli_help_allow_write"))
    ap.add_argument("--lan", action="store_true", help=tr("cli_help_lan"))
    ap.add_argument("--lang", choices=sorted(TRANSLATIONS), help=tr("cli_help_lang"))
    ap.add_argument("--check-i18n", action="store_true", help=tr("cli_help_check_i18n"))
    OPTS = ap.parse_args()
    if OPTS.check_i18n:
        sys.exit(check_i18n())
    if OPTS.lan:
        if OPTS.host == "127.0.0.1":  # --host を明示しているときはそちらを優先する
            OPTS.host = "0.0.0.0"
        OPTS.allow_write = True
    REPO = OPTS.repo
    WRITABLE = (not OPTS.read_only) and (is_loopback(OPTS.host) or OPTS.allow_write)

    try:
        git("rev-parse", "--git-dir")
    except (subprocess.CalledProcessError, FileNotFoundError):
        sys.exit(tr("cli_not_a_repo", repo=repr(REPO)))
    # サブディレクトリが指定されても、パスの扱い (status / diff はルート基準) がずれないようルートに揃える
    r = subprocess.run(["git", "-C", REPO, "rev-parse", "--show-toplevel"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    HAS_WORKTREE = r.returncode == 0
    if HAS_WORKTREE:
        REPO = r.stdout.strip()
    WIP_ENABLED = HAS_WORKTREE and not OPTS.no_wip
    PROJECT_NAME = os.path.basename(os.path.abspath(REPO)) or REPO

    if OPTS.dot:
        print(build_graph(OPTS)[0])
        return

    fill_template(HTML_TEMPLATE), fill_template(FILES_PAGE)  # 未定義の翻訳キーがあれば、起動時に KeyError で気づけるようにする
    global SERVER
    wanted = OPTS.port if OPTS.port is not None else DEFAULT_PORT
    try:
        SERVER = QuietServer((OPTS.host, wanted), Handler)
    except OSError:
        if OPTS.port is not None:  # --port を指定したときは、自動で変えずにエラーで終わる
            sys.exit(tr("cli_port_in_use", port=OPTS.port))
        SERVER = QuietServer((OPTS.host, 0), Handler)  # OS に空いているポートを選ばせる
        print(tr("cli_port_auto", port=wanted, actual=SERVER.server_address[1]))
    server = SERVER
    OPTS.port = server.server_address[1]
    url = f"http://{'localhost' if OPTS.host == '0.0.0.0' else OPTS.host}:{OPTS.port}/"  # 0.0.0.0 はブラウザで開く宛先にならない
    print(tr("cli_serving", url=url, state=tr("cli_state_on" if WRITABLE else "cli_state_off")))
    if OPTS.host == "0.0.0.0":
        print(tr("cli_listening", port=OPTS.port))
        if WRITABLE:
            print(tr("cli_lan_warning"))
    if OPTS.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
