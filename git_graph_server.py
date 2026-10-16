#!/usr/bin/env python3
"""git のコミットグラフを、ブランチ/タグ/末端 + それらの分岐点(merge-base)だけに省略して
Graphviz dot で出力する。

残すコミット:
  - ブランチ / リモートブランチ / タグ / HEAD が指すコミット
  - 末端 (子がいないコミット)
  - 上記どうしを2つずつ組み合わせたときの merge-base (最初の共通祖先)
マージコミット等でもこれらに該当しなければ省略する。
エッジは「残したコミット同士の祖先関係」(推移簡約済み)。ラベル +N は区間内の省略コミット数。

使い方:
  python git_graph.py | dot -Tsvg -o graph.svg
  python git_graph.py --html graph.html   # クリックで git log を表示 (dot が必要)
  python git_graph.py --no-remotes --no-tags main dev
"""
import argparse
import html
import itertools
import json
import subprocess
import sys
from collections import defaultdict


def git(*args):
    return subprocess.check_output(["git", *args], text=True)


def esc(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8"><title>git graph</title>
<style>
  html,body{height:100%;margin:0;font-family:system-ui,sans-serif;font-size:13px;color:#222}
  body{display:flex}
  #graph{flex:1;overflow:auto;padding:12px;background:#fff}
  #panel{width:520px;display:flex;flex-direction:column;border-left:1px solid #ccc;background:#fafafa;box-sizing:border-box}
  #top{flex:1;min-height:0;overflow:auto;padding:12px 14px}
  #detail{flex:0 0 38%;overflow:auto;padding:12px 14px;border-top:2px solid #ccc;background:#fff;box-sizing:border-box}
  h2{font-size:14px;margin:0 0 8px}
  pre{white-space:pre-wrap;word-break:break-word;margin:6px 0 0;font-family:monospace}
  code{background:#eee;padding:1px 4px;border-radius:3px;user-select:all}
  .chip{display:inline-block;padding:0 6px;border-radius:3px;margin:0 4px 2px 0;font-weight:bold}
  .meta{color:#555;margin:2px 0 6px}
  .hint{color:#777}
  .row{font-family:monospace;padding:1px 2px;border-bottom:1px solid #eee;word-break:break-word;cursor:pointer}
  .row:hover{background:#eef}
  .row.cur{background:#ffe9a0}
  .row .h{color:#b60}
  .row .m{color:#888;font-family:system-ui,sans-serif;font-size:12px}
  g.node{cursor:pointer} g.edge{cursor:pointer}
  g.node:hover polygon{stroke:#e88} g.edge:hover path{stroke:#e88}
  g.node.sel polygon{stroke:#d22;stroke-width:3}
  g.edge.sel path{stroke:#d22;stroke-width:2.5} g.edge.sel polygon{stroke:#d22;fill:#d22}
  g.edge path.hit{fill:none;stroke:transparent;stroke-width:14;pointer-events:stroke}
</style></head><body>
<div id="graph">__SVG__</div>
<div id="panel">
  <div id="top"><p class="hint">ノードをクリックすると、そのコミットから最初のコミットまでの <code>git log --oneline</code> を全部表示します。エッジをクリックすると省略された区間のログを表示します。</p></div>
  <div id="detail"><p class="hint">ログの行をクリックすると、そのコミットの <code>git log -1</code> を表示します。</p></div>
</div>
<script>
const DATA = __DATA__;
const COLORS = {branch:"#b7e4a7", remote:"#a7c8f0", tag:"#ffe9a0", head:"#f4a7a7"};
const top_ = document.getElementById("top"), detail = document.getElementById("detail");
const N = DATA.commits.length;
const IDX = new Map(DATA.commits.map((c, i) => [c[0], i]));
let selected = null, curRow = null;

function esc(s){return String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));}
function chips(refs){
  return refs.map(r => '<span class="chip" style="background:'+COLORS[r.kind]+'">'+esc(r.name)+"</span>").join("");
}
function row(i, extra){
  const c = DATA.commits[i], nd = DATA.nodes[c[0]];
  return '<div class="row" data-i="'+i+'"><span class="h">'+esc(c[0].slice(0,7))+"</span> "+
    (nd ? chips(nd.refs) : "")+esc(c[1])+(extra || "")+"</div>";
}
function ancestors(i){  // そのコミットから到達できる全コミット (git log の順)
  const seen = new Uint8Array(N), st = [i]; seen[i] = 1;
  while(st.length){ const x = st.pop(); for(const p of DATA.par[x]) if(!seen[p]){ seen[p] = 1; st.push(p); } }
  const out = []; for(let k = 0; k < N; k++) if(seen[k]) out.push(k);
  return out;
}
function setCur(r){
  if(curRow) curRow.classList.remove("cur");
  curRow = r; if(r) r.classList.add("cur");
}
function showCommit(i){  // git log -1 相当
  const c = DATA.commits[i], nd = DATA.nodes[c[0]];
  const parents = DATA.par[i].map(p => DATA.commits[p][0].slice(0,7)).join(" ");
  const msg = (c[4] || c[1]).split("\n").map(l => "    "+l).join("\n");
  detail.innerHTML = "<h2>git log -1 "+esc(c[0].slice(0,7))+"</h2>" + (nd ? chips(nd.refs) : "") +
    "<pre>commit "+esc(c[0])+"\nParent: "+esc(parents || "(なし)")+"\nAuthor: "+esc(DATA.authors[c[2]])+
    "\nDate:   "+esc(c[3])+"\n\n"+esc(msg)+"</pre>";
  detail.scrollTop = 0;
}
function showNode(n){
  const list = ancestors(n.i);
  top_.innerHTML = "<h2>git log --oneline "+esc(n.hash.slice(0,7))+"</h2>" + chips(n.refs) +
    '<div class="meta">最初のコミットまで全 '+list.length+" 件</div>" + list.map(k => row(k)).join("");
  top_.scrollTop = 0;
  setCur(top_.querySelector(".row")); showCommit(n.i);
}
function showEdge(e){
  const rows = e.log.map(l => {
    const i = IDX.get(l[0]);
    return i === undefined ? "" : row(i, ' <span class="m">'+esc(l[2])+" / "+esc(l[3])+"</span>");
  }).join("");
  top_.innerHTML = "<h2>"+esc(e.from)+" → "+esc(e.to)+"</h2>" +
    '<div class="meta"><code>git log '+esc(e.range)+"</code> ("+e.log.length+(e.truncated ? "+" : "")+" 件)</div>" +
    (rows || '<p class="hint">この区間にコミットはありません</p>');
  top_.scrollTop = 0; setCur(null);
  detail.innerHTML = '<p class="hint">ログの行をクリックすると、そのコミットの <code>git log -1</code> を表示します。</p>';
}
function select(g){
  if(selected) selected.classList.remove("sel");
  selected = g; g.classList.add("sel");
}
top_.addEventListener("click", ev => {
  const r = ev.target.closest(".row");
  if(!r || r.dataset.i === undefined) return;
  setCur(r); showCommit(+r.dataset.i);
});
document.querySelectorAll("svg g.node").forEach(g => {
  const n = DATA.nodes[g.querySelector("title").textContent];
  if(n) g.addEventListener("click", () => { select(g); showNode(n); });
});
document.querySelectorAll("svg g.edge").forEach(g => {
  const e = DATA.edges[g.querySelector("title").textContent];
  if(!e) return;
  const path = g.querySelector("path");
  if(path){ const hit = path.cloneNode(); hit.setAttribute("class", "hit"); g.appendChild(hit); }
  g.addEventListener("click", () => { select(g); showEdge(e); });
});
</script></body></html>
"""


def write_html(path, dot_text, keep, edges, labels, parents, subjects, revs):
    try:
        svg = subprocess.run(["dot", "-Tsvg"], input=dot_text, capture_output=True,
                             text=True, check=True).stdout
    except FileNotFoundError:
        sys.exit("dot (Graphviz) が見つかりません。インストールしてください。")
    except subprocess.CalledProcessError as e:
        sys.exit(e.stderr)
    svg = svg[svg.index("<svg"):]

    # 全コミットの作者・日時・メッセージ (ログ内のコミットをクリックしたとき用)
    info = {}
    raw = git("log", "--date-order", *revs, "--format=%H%x1f%an <%ae>%x1f%ai%x1f%B%x1e")
    for rec in raw.split("\x1e"):
        rec = rec.lstrip("\n")
        if not rec:
            continue
        h, author, date, msg = (rec.split("\x1f", 3) + ["", "", ""])[:4]
        info[h] = (author, date, msg.strip())

    order = list(parents)  # git log --date-order の順
    idx = {h: i for i, h in enumerate(order)}
    authors, aidx, commits = [], {}, []
    for h in order:
        author, date, msg = info.get(h, ("", "", subjects[h]))
        if author not in aidx:
            aidx[author] = len(authors)
            authors.append(author)
        commits.append([h, subjects[h], aidx[author], date, "" if msg == subjects[h] else msg])
    par = [[idx[x] for x in parents[h] if x in idx] for h in order]

    nodes = {h: {"hash": h, "i": idx[h],
                 "refs": [{"name": n, "kind": k} for n, k in labels.get(h, [])]}
             for h in keep}

    LIMIT = 300
    edge_data = {}
    for d, c in edges:
        lines = git("log", "--date=short", "-n", str(LIMIT),
                    "--format=%H%x1f%s%x1f%an%x1f%ad", f"{d}..{c}").splitlines()
        edge_data[f"{d}->{c}"] = {
            "from": d[:7], "to": c[:7], "range": f"{d[:7]}..{c[:7]}",
            "log": [l.split("\x1f") for l in lines], "truncated": len(lines) >= LIMIT,
        }

    data = json.dumps({"nodes": nodes, "edges": edge_data, "commits": commits,
                       "par": par, "authors": authors},
                      ensure_ascii=False, separators=(",", ":"))
    data = data.replace("</", "<\\/")
    a, rest = HTML_TEMPLATE.split("__SVG__", 1)
    b, c = rest.split("__DATA__", 1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(a + svg + b + data + c)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("revs", nargs="*", help="対象の rev (省略時は --all)")
    ap.add_argument("--no-remotes", action="store_true", help="リモートブランチを除外")
    ap.add_argument("--no-tags", action="store_true", help="タグを除外")
    ap.add_argument("--no-count", action="store_true", help="省略数の計算(+N)をしない")
    ap.add_argument("--html", metavar="FILE", help="クリックで詳細が見られる HTML を出力 (dot が必要)")
    ap.add_argument("--rankdir", default="BT", help="BT(下から上) / TB / LR など (既定: BT)")
    args = ap.parse_args()
    sys.setrecursionlimit(100000)

    revs = args.revs or ["--all"]

    # --- コミット情報 ---
    parents, subjects = {}, {}
    children = defaultdict(list)
    for line in git("log", "--date-order", *revs, "--format=%H%x09%P%x09%s").splitlines():
        h, p, s = (line.split("\t", 2) + ["", ""])[:3]
        ps = p.split()
        parents[h] = ps
        subjects[h] = s
        for x in ps:
            children[x].append(h)

    # --- ref ---
    labels = defaultdict(list)  # commit -> [(name, kind)]
    fmt = "%(objectname)%09%(refname)%09%(*objectname)"
    for line in git("for-each-ref", f"--format={fmt}").splitlines():
        obj, ref, peeled = (line.split("\t") + [""])[:3]
        target = peeled or obj
        if target not in parents:
            continue
        if ref.startswith("refs/heads/"):
            labels[target].append((ref[len("refs/heads/"):], "branch"))
        elif ref.startswith("refs/remotes/"):
            if args.no_remotes or ref.endswith("/HEAD"):
                continue
            labels[target].append((ref[len("refs/remotes/"):], "remote"))
        elif ref.startswith("refs/tags/"):
            if args.no_tags:
                continue
            labels[target].append((ref[len("refs/tags/"):], "tag"))
    try:
        head = git("rev-parse", "HEAD").strip()
        if head in parents:
            labels[head].append(("HEAD", "head"))
    except subprocess.CalledProcessError:
        pass

    # --- 基本ノード: ref の指す先 + 末端 ---
    base = set(labels) | {h for h in parents if not children[h]}

    # --- 2つずつの merge-base を追加 ---
    keep = set(base)
    for a, b in itertools.combinations(sorted(base), 2):
        r = subprocess.run(["git", "merge-base", "--all", a, b],
                           capture_output=True, text=True)
        keep.update(x for x in r.stdout.split() if x in parents)

    # --- 残したコミット同士の直接の祖先 (間に残したコミットを挟まない) ---
    def nearest(c):
        res, seen, stack = set(), set(), list(parents[c])
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            if x in keep:
                res.add(x)
            else:
                stack.extend(parents[x])
        return res

    direct = {c: nearest(c) for c in keep}

    # --- 推移簡約 ---
    reach = {}

    def anc(c):
        if c not in reach:
            s = set()
            for d in direct[c]:
                s.add(d)
                s |= anc(d)
            reach[c] = s
        return reach[c]

    edges = []
    for c in keep:
        for d in direct[c]:
            if not any(e != d and d in anc(e) for e in direct[c]):
                edges.append((d, c))

    # --- dot 出力 ---
    colors = {"branch": "#b7e4a7", "remote": "#a7c8f0", "tag": "#ffe9a0", "head": "#f4a7a7"}
    out = [
        "digraph G {",
        f"  rankdir={args.rankdir};",
        '  node [shape=plaintext, fontname="Helvetica", fontsize=10];',
        '  edge [fontname="Helvetica", fontsize=9];',
    ]
    for h in keep:
        # ref 名はノード内に色付きの行として表示し、その下にハッシュと件名を置く
        rows = []
        for name, kind in labels.get(h, []):
            rows.append(f'<TR><TD BGCOLOR="{colors[kind]}"><B>{html.escape(name)}</B></TD></TR>')
        rows.append(f'<TR><TD>{h[:7]}<BR/>{html.escape(subjects[h][:30])}</TD></TR>')
        label = ('<<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" CELLPADDING="3">'
                 + "".join(rows) + "</TABLE>>")
        out.append(f'  "{h}" [label={label}];')

    for d, c in edges:  # 親 -> 子
        attr = ""
        if not args.no_count:
            n = int(git("rev-list", "--count", f"{d}..{c}").strip()) - 1
            if n > 0:
                attr = f' [label="+{n}", style=dashed]'
        out.append(f'  "{d}" -> "{c}"{attr};')

    out.append("}")
    dot_text = "\n".join(out)
    if args.html:
        write_html(args.html, dot_text, keep, edges, labels, parents, subjects, revs)
    else:
        print(dot_text)


if __name__ == "__main__":
    main()
