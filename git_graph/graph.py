import html
import re
import subprocess
import sys
import threading
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

def build_graph(opts):
    """残すコミットとエッジを決め、dot / ノード情報 / エッジ情報を返す。"""
    sys.setrecursionlimit(100000)
    revs = opts.revs
    if not revs:
        refs = git("for-each-ref", "--format=%(refname)").splitlines()
        revs = [
            ref for ref in refs
            if not ref.endswith("/HEAD")
            and ref != "refs/stash"  # stash は下で別扱いにする (親の index / untracked 用コミットまで出さない)
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

    stashes = list_stashes(parents) if not getattr(opts, "no_stash", False) else []
    for h, base_commit, name, subject in stashes:  # 子は親より先の順なので、先頭に足す (stash は誰の親にもならない)
        parents[h] = [base_commit]
        subjects[h] = subject
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

_cache = {"base_key": None, "base": None, "payload_key": None, "payload": None}

_lock = threading.Lock()

def add_wip(dot_text, nodes, head, count):
    """HEAD の子として、破線の WIP ノードを dot とノード情報に追加する。"""
    label = ('<<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="3" STYLE="dashed" BGCOLOR="#fff8dc">'
             f'<TR><TD><B>WIP</B></TD></TR><TR><TD>{html.escape(tr("n_changes", n=count))}</TD></TR></TABLE>>')
    lines = f'  "WIP" [label={label}];\n  "{head}" -> "WIP" [style=dashed];\n'
    i = dot_text.rindex("}")
    nodes = dict(nodes)
    nodes["WIP"] = {"hash": "WIP", "refs": [], "wip": True, "count": count}
    return dot_text[:i] + lines + dot_text[i:], nodes

@route("/api/graph")
def graph_payload(q=None):
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
        key = git("for-each-ref") + "\0" + head + "\0" + current_branch + "\0" + stash_key()
        if _cache["base_key"] != key:
            _cache.update(base_key=key, base=build_graph(state.OPTS), payload_key=None, payload=None)
        raw = ""
        if state.WIP_ENABLED:
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
        payload = {"svg": svg, "nodes": nodes, "edges": edges, "writable": state.WRITABLE,
                   "merge": state.WRITABLE and state.HAS_WORKTREE}
        _cache.update(payload_key=(key, raw), payload=payload)
        return payload


__all__ = [
    "COLORS", "FAST_LAYOUT_NODES", "SUBJECT_WIDTH", "add_wip", "build_graph",
    "center_node_text", "graph_payload", "list_stashes", "stash_key", "truncate_width",
]
