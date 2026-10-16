#!/usr/bin/env python3
"""git のコミットグラフを、ブランチ/タグ/末端 + それらの分岐点(merge-base)だけに省略して
ブラウザで表示するローカルサーバー。ログやコミット詳細はクリック時に git から動的に取得する。

使い方 (標準ライブラリのみ。Graphviz の dot が必要):
  python git_graph_server.py                    # カレントディレクトリのリポジトリ
  python git_graph_server.py -C /path/to/repo --open
  python git_graph_server.py --no-remotes --no-tags main dev
  python git_graph_server.py --dot              # dot を標準出力に出して終了

  http://127.0.0.1:8000/                 グラフ (リロードで最新の状態に更新)
  /api/log?rev=<40桁hash>[..<40桁hash>]&skip=0&limit=500   git log (ページング)
  /api/commit?hash=<hash>                                    git show 相当 (変更ファイル一覧つき)
  /diff?hash=<hash>&path=<file>[&old=<旧パス>]               1ファイルの差分ページ (別タブで開く)
"""
import argparse
import html
import itertools
import json
import os
import re
import subprocess
import sys
import webbrowser
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

REPO = "."
OPTS = None
COLORS = {"branch": "#b7e4a7", "remote": "#a7c8f0", "tag": "#ffe9a0", "head": "#f4a7a7"}


def git(*args, literal=False):
    env = dict(os.environ, GIT_LITERAL_PATHSPECS="1") if literal else None  # パスをglobとして解釈しない
    return subprocess.check_output(["git", "-C", REPO, *args], text=True,
                                   encoding="utf-8", errors="replace", env=env)


# --------------------------------------------------------------------------
# グラフの構築
# --------------------------------------------------------------------------
def build_graph(opts):
    """残すコミットとエッジを決め、dot / ノード情報 / エッジ情報を返す。"""
    sys.setrecursionlimit(100000)
    revs = opts.revs or ["--all"]

    parents, subjects = {}, {}
    children = defaultdict(list)
    for line in git("log", *revs, "--format=%H%x09%P%x09%s").splitlines():
        h, p, s = (line.split("\t", 2) + ["", ""])[:3]
        ps = p.split()
        parents[h] = ps
        subjects[h] = s
        for x in ps:
            children[x].append(h)

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

    # ref の指す先 + 末端 + それらの merge-base
    base = set(labels) | {h for h in parents if not children[h]}
    keep = set(base)
    for a, b in itertools.combinations(sorted(base), 2):
        r = subprocess.run(["git", "-C", REPO, "merge-base", "--all", a, b],
                           capture_output=True, text=True)
        keep.update(x for x in r.stdout.split() if x in parents)

    def nearest(c):  # 間に残したコミットを挟まない直接の祖先
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
    reach = {}

    def anc(c):
        if c not in reach:
            s = set()
            for d in direct[c]:
                s.add(d)
                s |= anc(d)
            reach[c] = s
        return reach[c]

    edges = []  # 推移簡約
    for c in keep:
        for d in direct[c]:
            if not any(e != d and d in anc(e) for e in direct[c]):
                edges.append((d, c))

    out = [
        "digraph G {",
        f"  rankdir={opts.rankdir};",
        '  node [shape=plaintext, fontname="Helvetica", fontsize=10];',
        '  edge [fontname="Helvetica", fontsize=9];',
    ]
    for h in keep:
        rows = [f'<TR><TD BGCOLOR="{COLORS[kind]}"><B>{html.escape(name)}</B></TD></TR>'
                for name, kind in labels.get(h, [])]
        rows.append(f'<TR><TD>{h[:7]}<BR/>{html.escape(subjects[h][:30])}</TD></TR>')
        label = ('<<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" CELLPADDING="3">'
                 + "".join(rows) + "</TABLE>>")
        out.append(f'  "{h}" [label={label}];')
    for d, c in edges:
        attr = ""
        if not opts.no_count:
            n = int(git("rev-list", "--count", f"{d}..{c}").strip()) - 1
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
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>git graph</title>
<style>
  html, body { height: 100%; margin: 0; font-family: system-ui, sans-serif; font-size: 13px; color: #222; }
  body { display: flex; }
  #graph { flex: 1; overflow: auto; padding: 12px; background: #fff; }
  #panel { width: 480px; display: flex; flex-direction: column; border-left: 1px solid #ccc; background: #fafafa; box-sizing: border-box; }
  #listView, #detailView { flex: 1; min-height: 0; overflow: auto; padding: 14px; box-sizing: border-box; }
  #detailView { display: none; background: #fff; }
  h2 { font-size: 14px; margin: 0 0 8px; }
  pre { white-space: pre-wrap; word-break: break-word; background: #fff; border: 1px solid #ddd; padding: 8px; margin: 6px 0; font-size: 12px; }
  code { background: #eee; padding: 1px 4px; border-radius: 3px; user-select: all; }
  .chip { display: inline-block; padding: 0 6px; border-radius: 3px; margin: 0 4px 2px 0; font-weight: bold; }
  .meta { color: #555; margin: 2px 0; }
  .hint { color: #777; }
  table.log { border-collapse: collapse; width: 100%; margin-top: 6px; }
  table.log td { border-bottom: 1px solid #e3e3e3; padding: 4px; vertical-align: top; }
  table.log tr.clickable { cursor: pointer; }
  table.log tr.clickable:hover td { background: #eef; }
  table.log tr.cur td { background: #ffe9a0; }
  table.log td.h { font-family: monospace; white-space: nowrap; color: #b60; }
  table.log .meta { font-size: 12px; color: #888; margin: 0; }
  g.node, g.edge { cursor: pointer; }
  g.node:hover polygon { stroke: #e88; } g.edge:hover path { stroke: #e88; }
  g.node.sel polygon { stroke: #d22; stroke-width: 3; }
  g.edge.sel path { stroke: #d22; stroke-width: 2.5; } g.edge.sel polygon { stroke: #d22; fill: #d22; }
  g.edge path.hit { fill: none; stroke: transparent; stroke-width: 14; pointer-events: stroke; }
  table.files { border-collapse: collapse; margin: 6px 0; font-family: monospace; font-size: 12px; width: 100%; }
  table.files td { padding: 2px 6px; vertical-align: top; }
  tr.frow { cursor: pointer; } tr.frow:hover td { background: #eef; }
  td.fp { color: #06c; word-break: break-all; } td.fc { text-align: right; white-space: nowrap; } td.fb { white-space: nowrap; }
  .plus { color: #1a7f37; } .minus { color: #cf222e; }
  td.fp a { color: inherit; text-decoration: none; } td.fp a:hover { text-decoration: underline; }
  .back-btn { background: #ddd; border: none; padding: 4px 8px; border-radius: 3px; cursor: pointer; margin-bottom: 8px; }
  .back-btn:hover { background: #ccc; }
</style>
</head>
<body>
<div id="graph">__SVG__</div>
<div id="panel">
  <div id="listView"><p class="hint">ノードをクリックすると、そのコミットから最初（ルート）までのログを表示します。エッジをクリックすると省略された区間のログを表示します。</p></div>
  <div id="detailView"></div>
</div>
<script>
const DATA = __DATA__;
const COLORS = {branch: "#b7e4a7", remote: "#a7c8f0", tag: "#ffe9a0", head: "#f4a7a7"};
const CHUNK = 500;
const listView = document.getElementById("listView"), detailView = document.getElementById("detailView");
const views = {list: listView, detail: detailView}, savedScroll = {list: 0, detail: 0};
let curView = "list", selected = null, token = 0, observer = null, curRow = null;
let curCommit = null, curFiles = [];

function esc(s) {
  return String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
}
function chips(h) {
  const n = DATA.nodes[h];
  if (!n) return "";
  return n.refs.map(r => '<span class="chip" style="background:' + COLORS[r.kind] + '">' + esc(r.name) + "</span>").join("");
}
function api(path, params) {
  return fetch(path + "?" + new URLSearchParams(params)).then(r =>
    r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }));
}
function showView(name, keepScroll) {  // keepScroll: 戻ってきたときに以前のスクロール位置を復元
  savedScroll[curView] = views[curView].scrollTop;
  for (const k in views) views[k].style.display = (k === name) ? "block" : "none";
  curView = name;
  views[name].scrollTop = keepScroll ? savedScroll[name] : 0;
}
function rowHtml(c) {  // c = [hash, short, author, date, subject]
  return '<tr class="clickable" data-h="' + c[0] + '"><td class="h">' + esc(c[1]) + "</td><td>" +
    chips(c[0]) + esc(c[4]) + '<div class="meta">' + esc(c[2]) + " / " + esc(c[3]) + "</div></td></tr>";
}

// git log を CHUNK 件ずつ取得して、末尾が見えたら続きを読み込む (全件読むまで続く)
function showLog(titleHtml, rev, topHtml) {
  const my = ++token;
  if (observer) { observer.disconnect(); observer = null; }
  curRow = null;
  listView.innerHTML = "<h2>" + titleHtml + "</h2>" + topHtml +
    '<div class="meta" id="count">読み込み中…</div>' +
    '<table class="log"><tbody id="rows"></tbody></table><div id="sentinel" class="hint"></div>';
  showView("list");
  const rows = listView.querySelector("#rows"), count = listView.querySelector("#count"),
        sentinel = listView.querySelector("#sentinel");
  let skip = 0, done = false, loading = false, total = null;

  async function more() {
    if (loading || done || my !== token) return;
    loading = true;
    try {
      const r = await api("/api/log", {rev: rev, skip: skip, limit: CHUNK});
      if (my !== token) return;
      rows.insertAdjacentHTML("beforeend", r.commits.map(rowHtml).join(""));
      skip += r.commits.length;
      done = !r.has_more;
      if (r.total !== undefined) total = r.total;
      count.textContent = total === null ? skip + " 件"
        : (done ? "全 " + total + " 件" : skip + " / " + total + " 件を読み込み済み");
      sentinel.textContent = done ? "" : "読み込み中…";
    } catch (e) {
      sentinel.textContent = "エラー: " + e.message; done = true;
    } finally {
      loading = false;
    }
  }

  if ("IntersectionObserver" in window) {
    const recheck = () => {
      if (!done && observer && my === token) { observer.unobserve(sentinel); observer.observe(sentinel); }
    };
    observer = new IntersectionObserver(es => { if (es[0].isIntersecting) more().then(recheck); },
                                        {root: listView, rootMargin: "600px"});
    observer.observe(sentinel);
  } else {
    (async () => { while (!done && my === token) await more(); })();
  }
}

function diffUrl(h, f) {  // 差分は別ページ (/diff) で開く
  const p = {hash: h, path: f.path};
  if (f.old_path) p.old = f.old_path;
  return "/diff?" + new URLSearchParams(p);
}

function filesHtml(h, files) {
  if (!files.length) return "";
  let add = 0, del = 0, max = 0;
  files.forEach(f => { add += f.added || 0; del += f.deleted || 0; max = Math.max(max, (f.added || 0) + (f.deleted || 0)); });
  const scale = max > 40 ? 40 / max : 1;
  const rows = files.map((f, k) => {
    const a = f.added || 0, d = f.deleted || 0;
    const pa = a ? Math.max(1, Math.round(a * scale)) : 0, pd = d ? Math.max(1, Math.round(d * scale)) : 0;
    const name = f.old_path ? f.old_path + " => " + f.path : f.path;
    return '<tr class="frow" data-k="' + k + '"><td class="fp"><a href="' + esc(diffUrl(h, f)) + '" target="_blank" rel="noopener">' + esc(name) + '</a></td><td class="fc">' +
      (f.added === null ? "Bin" : a + d) + '</td><td class="fb"><span class="plus">' + "+".repeat(pa) +
      '</span><span class="minus">' + "-".repeat(pd) + "</span></td></tr>";
  }).join("");
  return '<div class="meta">' + files.length + " files changed, " + add + " insertions(+), " + del + " deletions(-)</div>" +
    '<table class="files">' + rows + "</table>";
}

function showDetail(h) {
  const back = '<button class="back-btn">← 履歴一覧に戻る</button>';
  detailView.innerHTML = back + '<p class="hint">読み込み中…</p>';
  showView("detail");
  api("/api/commit", {hash: h}).then(c => {
    curCommit = c.hash; curFiles = c.files;
    const parents = c.parents.map(p => esc(p.slice(0, 7))).join(", ") || "(なし)";
    detailView.innerHTML = back + "<h2>コミット " + esc(c.hash.slice(0, 7)) + "</h2>" + chips(c.hash) +
      '<div class="meta">' + esc(c.hash) + "</div>" +
      '<div class="meta">Author: ' + esc(c.author) + "</div>" +
      '<div class="meta">Date: ' + esc(c.date) + "</div>" +
      '<div class="meta">Parent: ' + parents + "</div>" +
      "<pre>" + esc(c.message) + "</pre>" + filesHtml(c.hash, c.files) +
      '<div class="meta"><code>git show ' + esc(c.hash.slice(0, 7)) + "</code></div>";
  }).catch(e => {
    detailView.innerHTML = back + '<p class="hint">エラー: ' + esc(e.message) + "</p>";
  });
}

listView.addEventListener("click", ev => {
  const tr = ev.target.closest("tr.clickable");
  if (!tr) return;
  if (curRow) curRow.classList.remove("cur");
  curRow = tr; tr.classList.add("cur");
  showDetail(tr.dataset.h);
});
detailView.addEventListener("click", ev => {
  if (ev.target.closest(".back-btn")) { showView("list", true); return; }
  const tr = ev.target.closest("tr.frow");
  if (tr && !ev.target.closest("a") && curCommit)  // 行のどこをクリックしても別タブで差分を開く
    window.open(diffUrl(curCommit, curFiles[+tr.dataset.k]), "_blank", "noopener");
});

function select(g) {
  if (selected) selected.classList.remove("sel");
  selected = g; g.classList.add("sel");
}
document.querySelectorAll("svg g.node").forEach(g => {
  const t = g.querySelector("title");
  const n = t && DATA.nodes[t.textContent];
  if (!n) return;
  g.addEventListener("click", () => {
    select(g);
    showLog("git log --oneline " + esc(n.hash.slice(0, 7)), n.hash,
            chips(n.hash) + '<div class="meta">最初のコミットまでの全ログ</div>');
  });
});
document.querySelectorAll("svg g.edge").forEach(g => {
  const t = g.querySelector("title");
  const e = t && DATA.edges[t.textContent];
  if (!e) return;
  const path = g.querySelector("path");
  if (path) { const hit = path.cloneNode(); hit.setAttribute("class", "hit"); g.appendChild(hit); }
  g.addEventListener("click", () => {
    select(g);
    const range = e.from.slice(0, 7) + ".." + e.to.slice(0, 7);
    showLog(esc(e.from.slice(0, 7)) + " → " + esc(e.to.slice(0, 7)), e.from + ".." + e.to,
            '<div class="meta"><code>git log ' + esc(range) + "</code></div>");
  });
});
</script>
</body>
</html>
"""

_cache = {"key": None, "html": None}


def render_page():
    """グラフ HTML を返す。ref と HEAD が変わっていなければキャッシュを使う。"""
    try:
        head = git("rev-parse", "HEAD")
    except subprocess.CalledProcessError:
        head = ""
    key = git("for-each-ref") + head
    if _cache["key"] == key:
        return _cache["html"]

    dot_text, nodes, edges = build_graph(OPTS)
    svg = subprocess.run(["dot", "-Tsvg"], input=dot_text, capture_output=True,
                         text=True, check=True).stdout
    svg = svg[svg.index("<svg"):]
    data = json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False).replace("</", "<\\/")
    a, rest = HTML_TEMPLATE.split("__SVG__", 1)
    b, c = rest.split("__DATA__", 1)
    page = a + svg + b + data + c
    _cache.update(key=key, html=page)
    return page


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
REV_RE = re.compile(r"[0-9a-f]{40}(\.\.[0-9a-f]{40})?")
HASH_RE = re.compile(r"[0-9a-f]{7,40}")


def api_log(q):
    rev = q.get("rev", [""])[0]
    if not REV_RE.fullmatch(rev):
        raise ValueError("bad rev")
    skip = int(q.get("skip", ["0"])[0])
    limit = min(int(q.get("limit", ["500"])[0]), 2000)
    if skip < 0 or limit < 1:
        raise ValueError("bad paging")
    out = git("log", "--date=short", f"--skip={skip}", "-n", str(limit),
              "--format=%H%x1f%h%x1f%an%x1f%ad%x1f%s", rev, "--")
    commits = [l.split("\x1f") for l in out.splitlines() if l]
    res = {"commits": commits, "has_more": len(commits) == limit}
    if skip == 0:
        res["total"] = int(git("rev-list", "--count", rev, "--").strip())
    return res


def api_commit(q):
    h = q.get("hash", [""])[0]
    if not HASH_RE.fullmatch(h):
        raise ValueError("bad hash")
    o = git("show", "-s", "--date=iso", "--format=%H%x1f%an <%ae>%x1f%ad%x1f%P%x1f%B", h, "--")
    full, author, date, par, msg = (o.split("\x1f", 4) + [""] * 5)[:5]
    raw = git("show", "--numstat", "-z", "-M", "--first-parent", "--format=", h, "--")
    return {"hash": full, "author": author, "date": date, "parents": par.split(),
            "message": msg.strip(), "files": parse_numstat(raw)}


def parse_numstat(raw):
    """git show --numstat -z の出力を [{path, old_path, added, deleted}] にする (バイナリは None)。"""
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
    h = q.get("hash", [""])[0]
    path = q.get("path", [""])[0]
    old = q.get("old", [""])[0]
    if not HASH_RE.fullmatch(h):
        raise ValueError("bad hash")
    if not path or "\0" in path or "\0" in old:
        raise ValueError("bad path")
    specs = [old, path] if old else [path]
    out = git("show", "--format=", "--first-parent", "-M", "--no-color", h, "--", *specs, literal=True)
    return {"diff": out[:DIFF_LIMIT], "truncated": len(out) > DIFF_LIMIT}


DIFF_CSS = """
  html, body { height: 100%; margin: 0; font-family: system-ui, sans-serif; font-size: 13px; color: #222; }
  body { display: flex; }
  #side { width: 320px; flex: none; overflow: auto; border-right: 1px solid #ccc; background: #fafafa; padding: 10px; box-sizing: border-box; }
  #side a { display: flex; justify-content: space-between; gap: 8px; padding: 3px 6px; color: #06c; text-decoration: none;
            font-family: monospace; font-size: 12px; border-radius: 3px; word-break: break-all; }
  #side a:hover { background: #eef; }
  #side a.cur { background: #ffe9a0; color: #222; font-weight: bold; }
  #side .n { white-space: nowrap; }
  #main { flex: 1; min-width: 0; overflow: auto; padding: 14px 18px; }
  h1 { font-size: 15px; margin: 0 0 6px; word-break: break-all; }
  .meta { color: #555; margin: 2px 0; }
  code { background: #eee; padding: 1px 4px; border-radius: 3px; user-select: all; }
  .plus { color: #1a7f37; } .minus { color: #cf222e; }
  .hint { color: #777; }
  .diff { font-family: monospace; font-size: 12px; white-space: pre; margin-top: 12px; border: 1px solid #ddd; }
  .dl { padding: 0 6px; min-width: max-content; }
  .dl.ad { background: #e6ffec; } .dl.de { background: #ffebe9; }
  .dl.hk { background: #ddf4ff; color: #555; } .dl.hd { color: #777; background: #f6f8fa; }
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


def diff_url(h, f):
    params = {"hash": h, "path": f["path"]}
    if f["old_path"]:
        params["old"] = f["old_path"]
    return "/diff?" + urlencode(params)


def render_diff_page(q):
    """1ファイルの差分ページ。左にそのコミットの変更ファイル一覧を出す。"""
    c = api_commit({"hash": q.get("hash", [""])})
    d = api_diff(q)
    path = q["path"][0]
    old = q.get("old", [""])[0]
    name = f"{old} => {path}" if old else path

    side = []
    for f in c["files"]:
        fname = f"{f['old_path']} => {f['path']}" if f["old_path"] else f["path"]
        if f["added"] is None:
            counts = "Bin"
        else:
            counts = (f'<span class="plus">+{f["added"]}</span> '
                      f'<span class="minus">-{f["deleted"]}</span>')
        cur = "cur" if f["path"] == path else ""
        side.append(f'<a class="{cur}" href="{html.escape(diff_url(c["hash"], f))}">'
                    f'<span>{html.escape(fname)}</span><span class="n">{counts}</span></a>')

    subject = c["message"].split("\n", 1)[0]
    short = c["hash"][:7]
    return f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8">
<title>{html.escape(path)} @ {short}</title>
<style>{DIFF_CSS}</style></head><body>
<div id="side">
  <div class="meta">{len(c["files"])} files changed</div>
  {"".join(side)}
</div>
<div id="main">
  <h1>{html.escape(name)}</h1>
  <div class="meta">コミット {short} — {html.escape(subject)}</div>
  <div class="meta">{html.escape(c["author"])} / {html.escape(c["date"])}</div>
  <div class="meta"><code>git show {short} -- {html.escape(path)}</code></div>
  {'<p class="hint">大きいため先頭のみ表示しています</p>' if d["truncated"] else ""}
  <div class="diff">{diff_lines_html(d["diff"]) or '<p class="hint">差分はありません</p>'}</div>
</div>
</body></html>
"""


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
                self._send(200, "text/html", render_page())
            elif u.path == "/api/log":
                self._send(200, "application/json", json.dumps(api_log(q), ensure_ascii=False))
            elif u.path == "/api/commit":
                self._send(200, "application/json", json.dumps(api_commit(q), ensure_ascii=False))
            elif u.path == "/diff":
                self._send(200, "text/html", render_diff_page(q))
            else:
                self._send(404, "text/plain", "not found")
        except ValueError as e:
            self._send(400, "text/plain", str(e))
        except FileNotFoundError:
            self._send(500, "text/plain", "dot (Graphviz) が見つかりません。インストールしてください。")
        except subprocess.CalledProcessError as e:
            self._send(500, "text/plain", f"command failed: {e.cmd}\n{e.stderr or ''}")

    def log_message(self, fmt, *args):  # アクセスログは出さない
        pass


def main():
    global REPO, OPTS
    ap = argparse.ArgumentParser(description="git commit graph server")
    ap.add_argument("revs", nargs="*", help="対象の rev (省略時は --all)")
    ap.add_argument("-C", dest="repo", default=".", help="リポジトリのパス (既定: カレント)")
    ap.add_argument("--host", default="127.0.0.1", help="待ち受けアドレス (既定: 127.0.0.1)")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--open", action="store_true", help="起動後にブラウザで開く")
    ap.add_argument("--no-remotes", action="store_true", help="リモートブランチを除外")
    ap.add_argument("--no-tags", action="store_true", help="タグを除外")
    ap.add_argument("--no-count", action="store_true", help="省略数 (+N) の計算をしない")
    ap.add_argument("--rankdir", default="BT", help="BT(下から上) / TB / LR (既定: BT)")
    ap.add_argument("--dot", action="store_true", help="dot を標準出力に出して終了")
    OPTS = ap.parse_args()
    REPO = OPTS.repo

    try:
        git("rev-parse", "--git-dir")
    except (subprocess.CalledProcessError, FileNotFoundError):
        sys.exit(f"{REPO!r} は git リポジトリではありません")

    if OPTS.dot:
        print(build_graph(OPTS)[0])
        return

    server = ThreadingHTTPServer((OPTS.host, OPTS.port), Handler)
    url = f"http://{OPTS.host}:{OPTS.port}/"
    print(f"serving {url}  (Ctrl+C で終了)")
    if OPTS.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
