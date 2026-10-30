import copy
import html
import re
import subprocess
import sys
import threading
import time
import unicodedata
from collections import Counter, defaultdict
from . import app_state as state
from .gitrun import git
from .gitstate import parse_status, read_status_raw
from .i18n import tr
from .query.graph import (
    DEFAULT_MERGE_MAX_NODES, build_model, head_and_branch, is_ancestor, list_stashes,
    merge_bases, model_cache_key, commit_timestamp, stash_key,
)
from .routing import route
from .wip import wip_count

FAST_LAYOUT_NODES = 300

COLORS = {"branch": "#b7e4a7", "current": "#b7e4a7", "remote": "#a7c8f0", "tag": "#ffe9a0", "head": "#f4a7a7", "stash": "#d9c7f2"}

SUBJECT_WIDTH = 30
MERGE_EDGE_COLOR = "#d35400"
CLUSTER_MIN_NODES = 1000  # 残すノードがこれを超えたら、年 / 月 / 日のクラスターにまとめて表示する (0 でまとめない)
CLUSTER_MAX_NODES = 150  # 1 つのクラスターに入れるノード数の目安。超えたら年 -> 月 -> 日と細かく分ける
OPEN_NODE_BUDGET = 500  # 開いているクラスターのノード数の合計の上限。超えたら使っていない (古い) 方から閉じる
CLUSTER_REF_ROWS = 8  # 閉じたクラスターに書くブランチ / タグ名の最大数
_CLUSTER_TIME_FORMATS = ("%Y", "%Y-%m", "%Y-%m-%d")
_KIND_ORDER = {"head": 0, "current": 1, "branch": 2, "tag": 3, "remote": 4, "stash": 5}
DOT_TIMEOUT = 60  # dot の最大実行時間 (秒)。超えたら表示を減らして描き直す
DOT_COLOR = "black"

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




def _dot_header(opts, n_nodes):
    font = (getattr(opts, "font", None) or "Helvetica").replace("\\", "").replace('"', "")  # dot の文字列に埋めるため引用符は除く
    return [
        "digraph G {",
        f"  rankdir={opts.rankdir};",
        *(["  nslimit=1; nslimit1=1; mclimit=0.3;"]  # ノードが多いときは dot のレイアウト最適化を軽くして高速化
          if n_nodes > FAST_LAYOUT_NODES and not getattr(opts, "full_layout", False) else []),
        f'  node [shape=plaintext, fontname="{font}", fontsize=10];',
        f'  edge [fontname="{font}", fontsize=9];',
    ]


def _node_decl(h, model):
    if h in model["extra"]:  # マージ表示のためだけに出したノードは、文字なしの小さな点にする
        return (f'  "{h}" [shape=circle, label="", width=0.14, height=0.14, fixedsize=true, '
                f'style=filled, fillcolor="{DOT_COLOR}", color="{DOT_COLOR}"];')
    rows = []
    for name, kind in model["labels"].get(h, []):
        marker = "★ " if kind == "current" else ""
        rows.append(f'<TR><TD BGCOLOR="{COLORS[kind]}"><B>{marker}{html.escape(name)}</B></TD></TR>')
    rows.append(f'<TR><TD>{h[:7]}<BR/>{html.escape(truncate_width(model["subjects"][h], SUBJECT_WIDTH))}</TD></TR>')
    label = ('<<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" CELLPADDING="3">'
             + "".join(rows) + "</TABLE>>")
    return f'  "{h}" [label={label}];'


def _edge_line(d, c, via, model, opts):
    attrs = []
    n = model["counts"].get((d, c, via), 0)
    # 個数のラベルは付けない (線が多いと dot が非常に遅くなる)。間に省略したコミットがなければ破線、あれば実線にする。
    # --no-count のときは省略数を数えないので、線種の区別はしない。
    if not opts.no_count and n == 0:
        attrs.append("style=dashed")
    if via is not None and via != model["parents"][c][0]:  # 第1親以外 = マージされてきた側の線は色を変える
        attrs += [f'color="{MERGE_EDGE_COLOR}"', f'fontcolor="{MERGE_EDGE_COLOR}"']
    if via is not None:  # 同じ組の線を区別できるよう id を付ける (クリックしたとき、どの親の側かが分かる)
        attrs.append(f'id="m_{d}_{c}_{via}"')
    attr = f' [{", ".join(attrs)}]' if attrs else ""
    return f'  "{d}" -> "{c}"{attr};'


def _node_data(model):
    labels = model["labels"]
    return {h: {"hash": h, "refs": [{"name": n, "kind": k} for n, k in labels.get(h, [])]}
            for h in model["keep"]}


def _edge_data(edges):
    data = {f"{d}->{c}": {"from": d, "to": c} for d, c, _ in edges}
    for d, c, via in edges:
        if via is not None:
            data[f"m_{d}_{c}_{via}"] = {"from": d, "to": c, "via": via}
    return data


def render_plain(model, opts):
    """全ノードをそのまま描く dot / ノード情報 / エッジ情報を返す。"""
    out = _dot_header(opts, len(model["keep"]))
    out += [_node_decl(h, model) for h in model["keep_order"]]
    out += [_edge_line(d, c, via, model, opts) for d, c, via in model["edges"]]
    out.append("}")
    return "\n".join(out), _node_data(model), _edge_data(model["edges"])


def build_graph(opts):
    return render_plain(build_model(opts), opts)
