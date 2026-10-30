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
from .gitstate import status_lists
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
    """残すコミットとエッジを決め、dot / ノード情報 / エッジ情報を返す。"""
    return render_plain(build_model(opts), opts)


# ---- クラスター表示 (ノードが多すぎるとき、時期ごとにまとめて、開いたものだけ個別に描く)

def assign_clusters(model):
    """残すコミットを、年 -> 月 -> 日の順に、1 つが CLUSTER_MAX_NODES 以下になるまで細かく分ける。
    返す値: (クラスターの id -> 情報, コミット -> クラスターの id)。"""
    dates = model["dates"]
    clusters, cluster_of = {}, {}

    def split(members, level):
        groups = defaultdict(list)
        for h in members:
            groups[time.strftime(_CLUSTER_TIME_FORMATS[level], time.gmtime(dates.get(h, 0)))].append(h)
        for key in sorted(groups):
            g = groups[key]
            if len(g) > CLUSTER_MAX_NODES and level + 1 < len(_CLUSTER_TIME_FORMATS):
                split(g, level + 1)
                continue
            cid = "c:" + key
            ts = [dates.get(h, 0) for h in g]
            clusters[cid] = {"id": cid, "label": key, "members": g, "start": min(ts), "end": max(ts)}
            for h in g:
                cluster_of[h] = cid

    split(model["keep_order"], 0)
    return clusters, cluster_of


def _clusters_of(model):
    if "clusters" not in model:
        model["clusters"] = assign_clusters(model)
    return model["clusters"]


def cluster_for_time(clusters, ts):
    """日時 ts のコミットが入るクラスターの id (残すコミットが無い時期は、一番近いクラスター)。"""
    for fmt in reversed(_CLUSTER_TIME_FORMATS):
        cid = "c:" + time.strftime(fmt, time.gmtime(ts))
        if cid in clusters:
            return cid
    if not clusters:
        return None
    return min(clusters, key=lambda c: 0 if clusters[c]["start"] <= ts <= clusters[c]["end"]
               else min(abs(ts - clusters[c]["start"]), abs(ts - clusters[c]["end"])))


def resolve_open(clusters, head_cluster, requested):
    """開くクラスターを決める。requested は古い順 (最後が最近使ったもの)。
    何も指定が無ければ HEAD のクラスター。ノード数の合計が OPEN_NODE_BUDGET を超えるときは、
    新しい方から数えて収まる分だけ残し、使っていない (古い) ものから閉じる。"""
    want = []
    for cid in requested:
        if cid in clusters:
            if cid in want:
                want.remove(cid)
            want.append(cid)
    if not want and head_cluster:
        want = [head_cluster]
    chosen, total = [], 0
    for cid in reversed(want):
        n = len(clusters[cid]["members"])
        if chosen and total + n > OPEN_NODE_BUDGET:
            break
        chosen.append(cid)
        total += n
    return chosen[::-1]


def _cluster_node_decl(cl, model):
    refs = sorted((_KIND_ORDER.get(kind, 9), -model["dates"].get(h, 0), name, kind)
                  for h in cl["members"] for name, kind in model["labels"].get(h, []))
    rows = [f'<TR><TD><B>{html.escape(cl["label"])}</B><BR/>{html.escape(tr("cluster_nodes", n=len(cl["members"])))}</TD></TR>']
    for _, _, name, kind in refs[:CLUSTER_REF_ROWS]:
        rows.append(f'<TR><TD BGCOLOR="{COLORS[kind]}">{html.escape(truncate_width(name, SUBJECT_WIDTH))}</TD></TR>')
    if len(refs) > CLUSTER_REF_ROWS:
        rows.append(f'<TR><TD>+{len(refs) - CLUSTER_REF_ROWS}</TD></TR>')
    label = ('<<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" CELLPADDING="3">' + "".join(rows) + "</TABLE>>")
    return f'  "{cl["id"]}" [shape=box, style=filled, fillcolor="#eef3f8", color="#7a8aa0", label={label}];'


def render_clustered(model, opts, clusters, cluster_of, open_ids):
    """開いたクラスターは中のノードを囲んで描き、閉じたクラスターは 1 つの箱にして、クラスター同士の線だけ引く。
    返す値: dot, ノード情報 (全ノード), エッジ情報 (両端を描いている線だけ)。"""
    open_set = set(open_ids)
    rank = {h: i for i, h in enumerate(model["keep_order"])}

    def rep(h):  # 描くときのノード名 (開いていれば自分、閉じていればクラスター)
        return h if cluster_of[h] in open_set else cluster_of[h]

    drawn = sum(len(clusters[c]["members"]) for c in open_ids) + len(clusters) - len(open_ids)
    out = _dot_header(opts, drawn)
    for cid in open_ids:
        cl = clusters[cid]
        out.append(f'  subgraph "cluster_{cid}" {{')
        out.append(f'    label="{cl["label"]}"; labeljust=l; style="rounded,dashed"; color="#888888"; fontcolor="#555555";')
        out += ["  " + _node_decl(h, model) for h in sorted(cl["members"], key=rank.get)]
        out.append("  }")
    out += [_cluster_node_decl(cl, model) for cid, cl in clusters.items() if cid not in open_set]
    shown, seen = [], set()
    for d, c, via in model["edges"]:
        rd, rc = rep(d), rep(c)
        if rd == d and rc == c:  # 両端とも描いている線は、そのまま
            out.append(_edge_line(d, c, via, model, opts))
            shown.append((d, c, via))
        elif rd != rc and (rd, rc) not in seen:  # 閉じたクラスターに繋がる線は、クラスター同士 / ノードとクラスターで 1 本にまとめる
            seen.add((rd, rc))
            out.append(f'  "{rd}" -> "{rc}";')
    out.append("}")
    return "\n".join(out), _node_data(model), _edge_data(shown)


_cache = {}  # マージ表示の on / off ごとのキャッシュ

_lock = threading.Lock()

def add_wip(dot_text, nodes, head, count, anchor=None):
    """HEAD の子として、破線の WIP ノードを dot とノード情報に追加する。
    anchor は線を引く先のノード名 (HEAD を含むクラスターが閉じているときは、そのクラスター)。"""
    label = ('<<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="3" STYLE="dashed" BGCOLOR="#fff8dc">'
             f'<TR><TD><B>WIP</B></TD></TR><TR><TD>{html.escape(tr("n_changes", n=count) if count else tr("wip_clean"))}</TD></TR></TABLE>>')
    lines = f'  "WIP" [label={label}];\n  "{anchor or head}" -> "WIP" [style=dashed];\n'
    i = dot_text.rindex("}")
    nodes = dict(nodes)
    nodes["WIP"] = {"hash": "WIP", "refs": [], "wip": True, "count": count}
    return dot_text[:i] + lines + dot_text[i:], nodes

def render_dot(dot_text):
    """dot で SVG にする。Windows では text=True がロケール(cp932等)で符号化し、★ などが壊れて
    dot が "not well-formed" になるため、UTF-8 のバイト列で入出力する。"""
    return subprocess.run(["dot", "-Tsvg"], input=dot_text.encode("utf-8"), capture_output=True,
                          check=True, timeout=DOT_TIMEOUT).stdout.decode("utf-8", errors="replace")


def _children_of(model):
    if "children" not in model:
        children = defaultdict(list)
        for h, ps in model["parents"].items():
            for p in ps:
                if p in model["parents"]:
                    children[p].append(h)
        model["children"] = children
    return model["children"]


def locate_edge(model, h):
    """残していないコミット h が省略されている線 (祖先, 子, 経由した親) を返す (無ければ None)。
    h から祖先側・子孫側へ、それぞれ最も近い残したコミットまでたどり、その組になっている線を選ぶ。
    子孫側の様子 (子の、h 側の隣のコミット) で、マージコミットの親ごとの線も区別する。
    もう 1 つ返す値は、線が見つからなかったときの代わり (近い端のノード)。"""
    parents, keep = model["parents"], model["keep"]
    if h not in parents or h in keep:
        return None, None
    below, seen, stack = set(), {h}, [h]
    while stack:
        for p in parents[stack.pop()]:
            if p in seen or p not in parents:
                continue
            seen.add(p)
            if p in keep:
                below.add(p)
            else:
                stack.append(p)
    children = _children_of(model)
    above, seen, stack = {}, {h}, [h]  # 残した子孫 -> その真下 (h 側) の、省略されたコミット
    while stack:
        x = stack.pop()
        for c in children.get(x, ()):
            if c in seen:
                continue
            seen.add(c)
            if c in keep:
                above.setdefault(c, set()).add(x)
            else:
                stack.append(c)
    hits = [(d, c, via) for d, c, via in model["edges"] if d in below and c in above]
    for d, c, via in hits:
        if via is None or via in above[c]:
            return (d, c, via), None
    if hits:
        return hits[0], None
    near = sorted(below)[:1] + sorted(above)[:1]
    return None, near


def _edge_key(d, c, via):
    return f"{d}->{c}" if via is None else f"m_{d}_{c}_{via}"  # グラフの線のキー (_edge_data と同じ)


def _model_for(merges):
    """作業ツリー以外 (ref / HEAD / stash) が変わらない限りキャッシュしたモデルを返す。_lock の中で呼ぶ。
    返す値: (キャッシュ, opts, HEAD, キャッシュのキー)"""
    cache = _cache.setdefault(merges, {"base_key": None, "model": None, "payload_key": None, "payload": None})
    opts = copy.copy(state.OPTS)
    opts.show_merges = merges
    # 同じコミットを指すブランチ間で checkout すると current (★) 表示がキャッシュされたままになるのを防ぐ
    head, current_branch = head_and_branch()
    key = model_cache_key(head, current_branch)
    if cache["base_key"] != key:
        cache.update(base_key=key, model=build_model(opts), payload_key=None, payload=None)
    return cache, opts, head, key


def _is_clustered(model, opts):
    threshold = getattr(opts, "cluster_min_nodes", CLUSTER_MIN_NODES)
    return bool(threshold) and len(model["keep"]) > threshold


def _dot_for(model, opts, head, wip_n, requested, minimal=False):
    """サーバーが描くのと同じ dot / ノード情報 / エッジ情報と、クラスター表示のときの追加情報を返す。"""
    clustered = _is_clustered(model, opts)
    extra, anchor = {}, head
    if clustered:
        clusters, cluster_of = _clusters_of(model)
        open_ids = resolve_open(clusters, cluster_of.get(head), requested)
        if minimal:
            open_ids = open_ids[-1:]
        dot_text, nodes, edges = render_clustered(model, opts, clusters, cluster_of, open_ids)
        if cluster_of.get(head) not in open_ids:
            anchor = cluster_of.get(head)  # HEAD を含むクラスターが閉じているときは、そのクラスターに WIP を繋ぐ
        extra = {"clusters": {cid: {"id": cid, "label": cl["label"], "count": len(cl["members"])}
                              for cid, cl in clusters.items()},
                 "clusterOf": cluster_of, "open": open_ids}
    else:
        dot_text, nodes, edges = render_plain(model, opts)
    if wip_n is not None and head in model["keep"]:  # wip_n が None のときは WIP が無効。0 は変更なし (それでも WIP ノードは出す: パッチ適用などでファイル表示に入れるように)
        dot_text, nodes = add_wip(dot_text, nodes, head, wip_n, anchor)
    return dot_text, nodes, edges, extra


def dot_text_for_cli(opts):
    """--dot: 最初にサーバーが dot に渡すのと同じ dot を返す (大きいリポジトリで dot が失敗するとき、手で試すため)。"""
    head, _ = head_and_branch()
    return _dot_for(build_model(opts), opts, head, None, [])[0]


def _render_payload(model, opts, head, wip_n, requested, minimal=False):
    """モデルから dot を作り、SVG にして返す (dot が失敗したときは例外)。"""
    dot_text, nodes, edges, extra = _dot_for(model, opts, head, wip_n, requested, minimal)
    svg = render_dot(dot_text)
    return {"svg": center_node_text(svg[svg.index("<svg"):]), "nodes": nodes, "edges": edges,
            "writable": state.WRITABLE, "merge": state.WRITABLE and state.HAS_WORKTREE, **extra}


@route("/api/graph")
def graph_payload(q=None):
    """グラフ(SVG + ノード/エッジ情報)の JSON を返す。
    重い部分 (残すコミットの決定) は ref と HEAD が変わらない限りキャッシュし、
    作業ツリーの変更 (WIP ノード) だけを毎回付け替える。
    ?merges=1 でマージ表示、?open=c:2024-03,c:2023 で開いておくクラスター (古い順) を指定する。"""
    merges = bool(q) and q.get("merges", [""])[0] == "1"
    requested = [x for x in (q.get("open", [""])[0].split(",") if q else []) if x]
    with _lock:  # 同時に複数回作らない (2つ目以降はキャッシュを使う)
        cache, opts, head, key = _model_for(merges)
        wip_n = None  # None: WIP を出さない / 0: 変更なしの WIP (ノードに出すのは変更ファイル数だけ)
        if state.WIP_ENABLED:
            try:
                wip_n = wip_count(status_lists())
            except subprocess.CalledProcessError:
                wip_n = None
        if cache["payload_key"] == (key, wip_n, tuple(requested)):
            return cache["payload"]

        # dot が落ちる / 終わらないほど大きいときは、表示を減らして描き直す (通知を付けて返す)
        steps = [(None, {}, False)]
        if _is_clustered(cache["model"], opts):
            steps.append(("graph_notice_fewer_open", {}, True))
        else:  # 描くノードを大きく減らせるクラスター表示にして、開くのは 1 つだけにする
            steps.append(("graph_notice_clustered", {"cluster_min_nodes": 1}, True))
        if merges:
            steps.append(("graph_notice_no_merges", {"show_merges": False}, False))
        if not opts.no_tags:
            steps.append(("graph_notice_no_tags", {"show_merges": False, "no_tags": True}, False))
        steps.append(("graph_notice_no_remotes", {"show_merges": False, "no_tags": True, "no_remotes": True}, False))
        error = None
        for notice_key, overrides, minimal in steps:
            vopts = copy.copy(opts)
            vopts.__dict__.update(overrides)
            # 描き方だけ変えるときは、残すコミットの決定 (重い) をやり直さない
            model = cache["model"] if set(overrides) <= {"cluster_min_nodes"} else build_model(vopts)
            try:
                payload = _render_payload(model, vopts, head, wip_n, requested, minimal)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
                error = e
                continue
            notice = tr(notice_key) if notice_key else (tr("graph_notice_merge_cap") if model["truncated"] else "")
            if notice:
                payload["notice"] = notice
            break
        else:
            raise error
        cache.update(payload_key=(key, wip_n, tuple(requested)), payload=payload)
        return payload


@route("/api/graph_focus")
def graph_focus(q=None):
    """コミットがグラフのどこにあるかを返す。ログで選んだコミットまで、画面側でグラフをスクロールするために使う。
    cluster: 含むクラスターの id (クラスター表示でないときは None)
    edge / from / to: ノードでないコミットが省略されている線のキーと両端 (ノードのコミットは from = to = そのコミット)"""
    h = q.get("hash", [""])[0] if q else ""
    merges = bool(q) and q.get("merges", [""])[0] == "1"
    result = {"cluster": None, "edge": None, "from": None, "to": None}
    if not re.fullmatch(r"[0-9a-f]{40}", h):
        return result
    with _lock:
        cache, opts, head, key = _model_for(merges)
        model = cache["model"]
        if h in model["keep"]:
            result.update({"from": h, "to": h})
        else:
            edge, near = locate_edge(model, h)
            if edge:
                d, c, via = edge
                result.update({"edge": _edge_key(d, c, via), "from": d, "to": c})
            elif near:
                result.update({"from": near[0], "to": near[-1]})
        if _is_clustered(model, opts):
            clusters, cluster_of = _clusters_of(model)
            if h in cluster_of:
                result["cluster"] = cluster_of[h]
            else:
                endpoints = [x for x in (result["to"], result["from"]) if x and x in cluster_of]
                try:
                    ts = commit_timestamp(h)
                    if endpoints:
                        # 線の両端ノードのクラスターの中から、コミット日時に最も近い方を選ぶ
                        result["cluster"] = min(
                            (cluster_of[x] for x in endpoints),
                            key=lambda cid: 0 if clusters[cid]["start"] <= ts <= clusters[cid]["end"]
                            else min(abs(ts - clusters[cid]["start"]), abs(ts - clusters[cid]["end"]))
                        )
                    else:
                        result["cluster"] = cluster_for_time(clusters, ts)
                except (subprocess.CalledProcessError, ValueError):
                    if endpoints:
                        result["cluster"] = cluster_of[endpoints[0]]
        return result


__all__ = [
    "COLORS", "CLUSTER_MIN_NODES", "assign_clusters", "build_model", "is_ancestor", "render_clustered", "render_plain", "resolve_open", "DEFAULT_MERGE_MAX_NODES", "FAST_LAYOUT_NODES", "MERGE_EDGE_COLOR", "SUBJECT_WIDTH", "add_wip", "build_graph",
    "center_node_text", "dot_text_for_cli", "graph_focus", "graph_payload", "locate_edge", "render_dot", "list_stashes", "merge_bases", "stash_key", "truncate_width",
]
