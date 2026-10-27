import copy
import heapq
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
from .routing import route
from .wip import wip_count


FAST_LAYOUT_NODES = 300

COLORS = {"branch": "#b7e4a7", "current": "#b7e4a7", "remote": "#a7c8f0", "tag": "#ffe9a0", "head": "#f4a7a7", "stash": "#d9c7f2"}

SUBJECT_WIDTH = 30
MERGE_EDGE_COLOR = "#d35400"
DEFAULT_MERGE_MAX_NODES = 2200  # マージ表示でグラフ全体のノード数がこれを超えない範囲で、新しいマージから出す (dot は 2,500 ノード前後から急に遅くなる)
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

def stash_key():
    """stash の一覧が変わったか調べるための文字列。refs/stash は先頭の stash しか指さないので、一覧全体を見る。"""
    try:
        return git("stash", "list", "--format=%H")
    except subprocess.CalledProcessError:
        return ""

def list_stashes(shown):
    """グラフに出す stash を [(stash のハッシュ, 基点コミット, "stash@{n}", 件名)] で返す (新しい順)。
    stash のコミットは、親に index 用 (と --include-untracked の管理外ファイル用) のコミットを持つ。
    それらは出さず、stash を第1親 (stash した時点の HEAD) の子として表示する。基点がグラフに無い stash は出さない。"""
    try:
        out = git("stash", "list", "--format=%H%x09%P%x09%gd%x09%s")
    except subprocess.CalledProcessError:
        return []
    res = []
    for line in out.splitlines():
        h, p, name, subject = (line.split("\t", 3) + ["", "", ""])[:4]
        ps = p.split()
        if ps and ps[0] in shown and h not in shown:
            res.append((h, ps[0], name, subject))
    return res

def is_ancestor(parents, pos, a, d):
    """a は d の祖先 (a == d も含む) か。祖先は子より後ろに並ぶので、a より後ろに並ぶコミットへは進まない。"""
    if a == d:
        return True
    limit = pos[a]
    if limit < pos[d]:
        return False
    stack, seen = [d], {d}
    while stack:
        for q in parents[stack.pop()]:
            if q == a:
                return True
            if q in seen or q not in pos or pos[q] > limit:
                continue
            seen.add(q)
            stack.append(q)
    return False


def merge_bases(parents, pos, a, b):
    """a と b の最良共通祖先 (git merge-base --all 相当) をメモリ上で求める。
    --topo-order の位置 (pos) が小さい (= 新しい) 方から親へ辿り、a 側と b 側の両方から届いた最初のコミットを返す。
    見つけたコミットの先祖は stale (4) にして、冗長な共通祖先を出さない。
    親は必ず子より後ろに並ぶので、コミットを取り出した時点でそのコミットへの印は出そろっている。"""
    if a == b:
        return [a]
    flags = {a: 1, b: 2}  # 1: a から届く, 2: b から届く, 4: stale
    heap = [(pos[a], a), (pos[b], b)]
    heapq.heapify(heap)
    live = 2  # heap の中で stale でないものの数 (0 になったら終わり)
    found = []
    while heap and live:
        _, c = heapq.heappop(heap)
        f = flags[c]
        if not f & 4:
            live -= 1
            if f & 3 == 3:
                found.append(c)
                f |= 4
        for p in parents[c]:
            if p not in pos:
                continue
            old = flags.get(p)
            if old is None:
                flags[p] = f
                heapq.heappush(heap, (pos[p], p))
                if not f & 4:
                    live += 1
            else:
                new = old | f
                if new != old:
                    flags[p] = new
                    if not old & 4 and new & 4:
                        live -= 1
    return found

def build_model(opts):
    """残すコミットとエッジを決める (dot にするのは render_plain / render_clustered)。"""
    sys.setrecursionlimit(100000)
    revs = opts.revs
    revs_by_stdin = not revs  # 自動で集めた ref が何千もあると、Windows のコマンドライン長 (約 32,000 文字) を超えるので標準入力で渡す
    if not revs:
        refs = git("for-each-ref", "--format=%(refname)").splitlines()
        revs = [
            ref for ref in refs
            if not ref.endswith("/HEAD")
            and ref != "refs/stash"  # stash は下で別扱いにする (親の index / untracked 用コミットまで出さない)
            and not ref.startswith("refs/git_graph/")  # 元に戻す用の退避 (engine.py)
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

    parents, subjects, order, dates = {}, {}, [], {}  # order: 子が親より先 (--topo-order)
    children = defaultdict(list)
    # 件名はグラフに出す少数のコミット分だけ後で取る (全コミットぶん整形・解析すると遅い)
    if not revs:
        log = ""
    elif revs_by_stdin:
        log = git("rev-list", "--stdin", "--topo-order", "--parents", "--timestamp", input="\n".join(revs) + "\n")
    else:
        log = git("rev-list", *revs, "--topo-order", "--parents", "--timestamp")
    for line in log.splitlines():
        ct, h, *ps = line.split()
        parents[h] = ps
        dates[h] = int(ct) if ct.isdigit() else 0
        order.append(h)
        for x in ps:
            children[x].append(h)

    stashes = list_stashes(parents) if not getattr(opts, "no_stash", False) else []
    for h, base_commit, name, subject in stashes:  # 子は親より先の順なので、先頭に足す (stash は誰の親にもならない)
        parents[h] = [base_commit]
        subjects[h] = subject  # stash の件名は stash list から
        dates[h] = dates.get(base_commit, 0)
        order.insert(0, h)
        children[base_commit].append(h)

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
    head = ""
    try:
        head = git("rev-parse", "HEAD").strip()
        if head in parents:
            labels[head].append(("HEAD", "head"))
    except subprocess.CalledProcessError:
        pass
    for h, _, name, _ in stashes:
        labels[h].append((name, "stash"))

    # ブランチ / タグ / HEAD の指す先 + ルート + それらの merge-base。
    # --include-tips では、名前のない末端コミットも対象に加える。
    # merge-base を git で総当たり (O(n^2) 回のプロセス起動) せず、メモリ上で求める。
    # desc[x] = x の子孫である base コミットの集合 (ビットマスク)。
    # x が (a, b) の merge-base <=> a, b が desc[x] に入り、どの子の desc にも a, b が揃って入っていない。
    # 参照も子孫もないルートコミットも、履歴の始点として必ず表示する。
    named_tips = {h for h, refs in labels.items()
                  if any(kind in ("branch", "current", "remote", "tag", "head", "stash") for _, kind in refs)}
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

    # マージ表示: マージコミットと、そのマージされた側の分岐元 (親同士の merge-base) も残す。
    # ブランチを消したあとのマージは、通常の省略では分岐も合流も見えなくなるため。
    show_merges = getattr(opts, "show_merges", False)
    extra = set()
    if show_merges:
        # 大きなリポジトリではマージが何万もあり、全部出すと分岐元探しも dot も終わらない。
        # グラフ全体のノード数が max_nodes を超えない範囲で、新しいマージから出す (0 で無制限)。
        max_nodes = getattr(opts, "merge_max_nodes", DEFAULT_MERGE_MAX_NODES)
        pos = {h: i for i, h in enumerate(order)}
        opts.merge_truncated = False
        for h in order:  # 新しい順
            ps = [p for p in parents[h] if p in pos]
            if len(ps) < 2:
                continue
            found = {h}
            for i in range(len(ps)):
                for j in range(i + 1, len(ps)):
                    found.update(merge_bases(parents, pos, ps[i], ps[j]))
            added = found - keep - extra
            if max_nodes and len(keep) + len(extra) + len(added) > max_nodes:
                opts.merge_truncated = True
                break
            extra |= added
        extra -= keep
        keep |= extra

    need = [h for h in keep if h not in subjects]  # 残したコミットの件名だけ取る
    if need:
        for line in git("log", "--no-walk=unsorted", "--stdin", "--format=%H%x09%s", input="\n".join(need) + "\n").splitlines():
            h, _, s = line.partition("\t")
            subjects[h] = s
    keep_order = [h for h in reversed(order) if h in keep]  # 親が先
    edges = []  # (祖先, 子, 経由した親)。経由した親はマージコミットの親ごとの線だけ入り、それ以外は None
    counts = {}  # 線ごとの、間で省略されるコミット数
    if show_merges:
        # マージコミットを全て残すので、残したコミットの間は 1 本道になる (省略されるコミットの親は必ず 1 つ)。
        # 親をたどるだけで、線と省略数が (ビットマスクの集合演算なしで) 求まる。
        # nxt[h]: h 以前で最も近い残したコミット, dist[h]: そこまでの間にあって省略されるコミット数
        nxt, dist = {}, {}
        for h in reversed(order):
            if h in keep:
                nxt[h], dist[h] = h, 0
                continue
            p = next((p for p in parents[h] if p in nxt), None)
            nxt[h], dist[h] = (nxt[p], dist[p] + 1) if p is not None else (None, 0)
        for c in keep_order:
            ps = [p for p in parents[c] if p in nxt]
            for p in ps:  # マージコミットは親ごとに別の線を引く (同じ祖先へ 2 本の線が出る)
                d = nxt[p]
                if d is not None:
                    via = p if len(ps) > 1 else None
                    edges.append((d, c, via))
                    counts[(d, c, via)] = dist[p]
        del nxt, dist
    else:
        # 残したコミットは番号 (keep_order の位置) を持ち、コミットの集合はビットマスクで持つ (集合の和が速い)。
        # up[h]: h 以前で最も近い残したコミットの集合, direct[c]: c から間に残したコミットを挟まない直接の祖先の集合
        kbit = {h: 1 << i for i, h in enumerate(keep_order)}
        up, direct = {}, {}
        for h in reversed(order):  # 親 -> 子 の順に 1 回なめるだけ
            m = 0
            for p in parents[h]:
                m |= up.get(p, 0)
            if h in keep:
                direct[h] = m
                up[h] = kbit[h]
            else:
                up[h] = m
        del up

        # 推移簡約 (到達可能性もビットマスクで持つ)
        reach = {}
        for c in keep_order:
            dm = direct[c]
            deep, nodes = 0, []  # deep: 直接の祖先たちのさらに先の祖先
            m = dm
            while m:
                low = m & -m
                e = keep_order[low.bit_length() - 1]
                nodes.append(e)
                deep |= reach[e]
                m ^= low
            reach[c] = deep | dm
            for d in sorted(nodes):
                if not deep & kbit[d]:  # d が他の直接祖先の祖先でなければ残す
                    edges.append((d, c, None))
        if not opts.no_count:
            # 線の間に省略されるコミットがあるか (破線 / 実線の判定に使うだけで、個数は表示しない)。
            # 間に省略するコミットが無いのは、子 c の親が d だけのとき、または d 以外の親が全て d の祖先のとき
            # (他に d の祖先でない親があれば、その先が省略される)。後者は、親が複数あるときだけ調べる。
            pos = None
            for d, c, via in edges:
                vis = [p for p in parents[c] if p in parents]
                none_omitted = vis == [d]
                if not none_omitted and d in vis:
                    if pos is None:
                        pos = {h: i for i, h in enumerate(order)}
                    none_omitted = all(is_ancestor(parents, pos, p, d) for p in vis if p != d)
                counts[(d, c, via)] = 0 if none_omitted else 1
    keep_order.reverse()  # 出力の順序を毎回同じにする (レイアウトが安定する)

    return {"keep": keep, "keep_order": keep_order, "extra": extra, "edges": edges, "counts": counts,
            "labels": labels, "subjects": subjects, "parents": parents, "dates": dates, "head": head,
            "truncated": show_merges and getattr(opts, "merge_truncated", False)}


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
    key = git("for-each-ref") + "\0" + head + "\0" + current_branch + "\0" + stash_key()
    if cache["base_key"] != key:
        cache.update(base_key=key, model=build_model(opts), payload_key=None, payload=None)
    return cache, opts, head, key


def _is_clustered(model, opts):
    threshold = getattr(opts, "cluster_min_nodes", CLUSTER_MIN_NODES)
    return bool(threshold) and len(model["keep"]) > threshold


def _dot_for(model, opts, head, raw, requested, minimal=False):
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
    if raw is not None and head in model["keep"]:  # raw が None のときは WIP が無効。"" は変更なし (それでも WIP ノードは出す: パッチ適用などでファイル表示に入れるように)
        dot_text, nodes = add_wip(dot_text, nodes, head, wip_count(parse_status(raw)), anchor)
    return dot_text, nodes, edges, extra


def dot_text_for_cli(opts):
    """--dot: 最初にサーバーが dot に渡すのと同じ dot を返す (大きいリポジトリで dot が失敗するとき、手で試すため)。"""
    try:
        head = git("rev-parse", "HEAD").strip()
    except subprocess.CalledProcessError:
        head = ""
    return _dot_for(build_model(opts), opts, head, None, [])[0]


def _render_payload(model, opts, head, raw, requested, minimal=False):
    """モデルから dot を作り、SVG にして返す (dot が失敗したときは例外)。"""
    dot_text, nodes, edges, extra = _dot_for(model, opts, head, raw, requested, minimal)
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
        raw = None  # None: WIP を出さない / "": 変更なしの WIP
        if state.WIP_ENABLED:
            try:
                raw = read_status_raw()
            except subprocess.CalledProcessError:
                raw = None
        if cache["payload_key"] == (key, raw, tuple(requested)):
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
                payload = _render_payload(model, vopts, head, raw, requested, minimal)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
                error = e
                continue
            notice = tr(notice_key) if notice_key else (tr("graph_notice_merge_cap") if model["truncated"] else "")
            if notice:
                payload["notice"] = notice
            break
        else:
            raise error
        cache.update(payload_key=(key, raw, tuple(requested)), payload=payload)
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
                    ts = int(git("log", "-1", "--format=%ct", h).strip())
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
