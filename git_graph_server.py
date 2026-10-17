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
  /diff?from=<hash>&to=<hash>&path=<file>[&old=<旧パス>]     2コミット間の差分ページ
  /api/graph                                                 グラフ(SVG + ノード/エッジ情報。未コミットの変更があれば WIP ノードつき)
  /api/status                                                作業ツリーの変更 (ステージ済み/未ステージ/未追跡/競合)
  /diff?wip=staged|unstaged|untracked|conflict&path=<file>   作業ツリーの変更の差分ページ
  /api/compare?from=<hash>&to=<hash>                         2コミット間の変更ファイル一覧
  POST /api/branch | /api/tag | /api/checkout (JSON)         ブランチ作成 / タグ作成 / チェックアウト
     書き込みは 127.0.0.1 で待ち受けているときだけ有効 (--read-only で無効化、--allow-write で他アドレスでも許可)
"""
import argparse
import html
import itertools
import json
import os
import re
import subprocess
import sys
import threading
import webbrowser
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

REPO = "."
OPTS = None
WRITABLE = False  # ブランチ/タグ作成・チェックアウト等の書き込み操作を許可するか
WIP_ENABLED = False  # 未コミットの変更を WIP ノードとしてグラフに出すか
HAS_WORKTREE = False  # ワークツリーのあるリポジトリか (bare でないか)
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

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>git graph</title>
<style>
  html, body { height: 100%; margin: 0; font-family: system-ui, sans-serif; font-size: 13px; color: #222; }
  body { display: flex; }
  #graphWrap { flex: 1; min-width: 0; position: relative; background: #fff; }
  #graph { position: absolute; inset: 0; overflow: auto; padding: 12px; box-sizing: border-box; }
  #reload { position: absolute; top: 8px; right: 14px; z-index: 5; margin: 0; }
  #compareControls { position: absolute; top: 42px; right: 14px; z-index: 5; display: flex; align-items: center; gap: 6px; padding: 6px; border: 1px solid #ccc; border-radius: 4px; background: #fff; box-shadow: 0 1px 4px #0002; }
  #compareControls[hidden] { display: none; }
  #compareControls .cmp-hash { font-family: monospace; white-space: nowrap; }
  #panel { width: 480px; display: flex; flex-direction: column; border-left: 1px solid #ccc; background: #fafafa; box-sizing: border-box; }
  #listView, #detailView { flex: 1; min-height: 0; overflow: auto; padding: 14px; box-sizing: border-box; }
  #detailView { display: none; background: #fff; }
  h2 { font-size: 14px; margin: 0 0 8px; }
  pre { white-space: pre-wrap; word-break: break-word; background: #fff; border: 1px solid #ddd; padding: 8px; margin: 6px 0; font-size: 12px; }
  code { background: #eee; padding: 1px 4px; border-radius: 3px; user-select: all; }
  .chip { display: inline-block; padding: 0 6px; border-radius: 3px; margin: 0 4px 2px 0; font-weight: bold; }
  .tag { display: inline-block; padding: 0 6px; border-radius: 3px; margin-right: 4px; font-weight: bold; color: #fff; }
  .tagA { background: #06c; } .tagB { background: #e67e00; }
  .meta { color: #555; margin: 2px 0; }
  .hint { color: #777; }
  .copy { cursor: pointer; font-size: 12px; margin-left: 6px; user-select: none; text-decoration: none; border-radius: 3px; padding: 0 2px; }
  .copy:hover { background: #e4e4e4; }
  td.h .copy { opacity: .45; } tr:hover td.h .copy { opacity: 1; }
  .actions { display: none; border: 1px solid #ddd; background: #fafafa; border-radius: 4px; padding: 8px; margin: 8px 0; }
  .actions.open { display: block; }
  .tglbtn { border: 1px solid #bbb; background: #f3f3f3; border-radius: 3px; cursor: pointer; padding: 0 6px; margin-left: 4px; font-size: 12px; line-height: 1.4; }
  .tglbtn:hover { background: #e4e4e4; }
  .actions .arow { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin: 4px 0; }
  .actions input[type=text] { padding: 3px 6px; border: 1px solid #bbb; border-radius: 3px; min-width: 0; flex: 1; }
  .abtn { background: #e8eefc; border: 1px solid #b8c7ee; padding: 3px 8px; border-radius: 3px; cursor: pointer; }
  .abtn:hover { background: #d6e2fb; } .abtn:disabled { opacity: .5; cursor: default; }
  .amsg { margin-top: 4px; white-space: pre-wrap; } .amsg.ok { color: #1a7f37; } .amsg.ng { color: #cf222e; }
  h3 { font-size: 13px; margin: 10px 0 2px; }
  .st { display: inline-block; min-width: 1.3em; text-align: center; font-weight: bold; color: #fff; border-radius: 2px; margin-right: 4px; }
  .stM { background: #d29922; } .stA { background: #1a7f37; } .stD { background: #cf222e; } .stR { background: #6f42c1; } .stU { background: #cf222e; } .stN { background: #888; }
  .sbtn:disabled { opacity: .5; cursor: default; }
  table.log { border-collapse: collapse; width: 100%; margin-top: 6px; }
  table.log td { border-bottom: 1px solid #e3e3e3; padding: 4px; vertical-align: top; }
  table.log tr.clickable { cursor: pointer; }
  table.log tr.clickable:hover td { background: #eef; }
  table.log tr.cur td { background: #ffe9a0; }
  table.log tr.clickable.cmpA td { background: #cfe3ff; }
  table.log tr.clickable.cmpB td { background: #ffdcb0; }
  table.log td.h { font-family: monospace; white-space: nowrap; color: #b60; }
  table.log .meta { font-size: 12px; color: #888; margin: 0; }
  table.files { border-collapse: collapse; margin: 6px 0; font-family: monospace; font-size: 12px; width: 100%; }
  table.files td { padding: 2px 6px; vertical-align: top; }
  tr.frow { cursor: pointer; } tr.frow:hover td { background: #eef; }
  td.fp { color: #06c; word-break: break-all; } td.fc { text-align: right; white-space: nowrap; } td.fb { white-space: nowrap; }
  td.fp a { color: inherit; text-decoration: none; } td.fp a:hover { text-decoration: underline; }
  .plus { color: #1a7f37; } .minus { color: #cf222e; }
  g.node, g.edge { cursor: pointer; }
  g.node:hover polygon { stroke: #e88; } g.edge:hover path { stroke: #e88; }
  g.node.sel polygon { stroke: #d22; stroke-width: 3; }
  g.node.cmpA polygon { stroke: #06c; stroke-width: 3; }
  g.node.cmpB polygon { stroke: #e67e00; stroke-width: 3; }
  g.edge.sel path { stroke: #d22; stroke-width: 2.5; } g.edge.sel polygon { stroke: #d22; fill: #d22; }
  g.edge path.hit { fill: none; stroke: transparent; stroke-width: 14; pointer-events: stroke; }
  .back-btn, .sbtn { background: #ddd; border: none; padding: 4px 8px; border-radius: 3px; cursor: pointer; margin: 0 6px 8px 0; }
  .back-btn:hover, .sbtn:hover { background: #ccc; }
  #loading { display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; gap: 12px; color: #555; }
  .spin { width: 36px; height: 36px; border: 4px solid #ddd; border-top-color: #06c; border-radius: 50%; animation: spin 1s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>
<div id="graphWrap">
<button id="reload" class="sbtn" disabled title="グラフを最新の状態に更新">↻ 更新</button>
<div id="compareControls" hidden>
  <span class="cmp-hash" id="cmpA"></span><span class="cmp-hash" id="cmpB"></span>
  <button class="sbtn" id="compareBtn" disabled>比較</button><button class="sbtn" id="clearCmpBtn">クリア</button>
</div>
<div id="graph">
  <div id="loading">
    <div class="spin"></div>
    <div>コミットグラフを作成中…</div>
    <div class="hint" id="lsub">リポジトリが大きいと時間がかかります</div>
  </div>
</div>
</div>
<div id="panel">
  <div id="listView"><p class="hint">ノードをクリックすると、そのコミットから最初（ルート）までのログを表示します。エッジをクリックすると省略された区間のログを表示します。</p>
    <p class="hint">Ctrl（Mac は ⌘）+クリックで比較対象 A を選び、別のログのコミットをクリックして比較対象 B を選びます。</p></div>
  <div id="detailView"></div>
</div>
<script>
let DATA = {nodes: {}, edges: {}};
const COLORS = {branch: "#b7e4a7", remote: "#a7c8f0", tag: "#ffe9a0", head: "#f4a7a7"};
const CHUNK = 500;
const listView = document.getElementById("listView"), detailView = document.getElementById("detailView");
const views = {list: listView, detail: detailView}, savedScroll = {list: 0, detail: 0};
let curView = "list", selected = null, selectedKey = null, token = 0, observer = null, curRow = null;
let curCtx = null, curFiles = [];
let cmp = [];  // 比較用コミットのハッシュ (先頭が A、次が B)
const nodeEl = {};  // ハッシュ -> グラフ上のノード要素
const homeHtml = listView.innerHTML;
if (!history.state || !history.state.screen) history.replaceState({screen: "home"}, "");

function esc(s) {
  return String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
}
function chips(h) {
  const n = DATA.nodes[h];
  if (!n) return "";
  return n.refs.map(r => '<span class="chip" style="background:' + COLORS[r.kind] + '">' + esc(r.name) + "</span>").join("");
}
/*__COPY_JS__*/
function copyLink(h) {  // フルハッシュをコピーするリンク
  return '<a class="copy" data-copy="' + h + '" title="ハッシュ (40桁) をコピー">📋</a>';
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
function pushScreen(state) {
  history.replaceState(Object.assign({}, history.state, {scroll: views[curView].scrollTop}), "");
  history.pushState(state, "");
}
function rowHtml(c) {  // c = [hash, short, author, date, subject]
  return '<tr class="clickable" data-h="' + c[0] + '"><td class="h">' + esc(c[1]) + copyLink(c[0]) + "</td><td>" +
    '<span class="rc">' + chips(c[0]) + "</span>" + esc(c[4]) + '<div class="meta">' + esc(c[2]) + " / " + esc(c[3]) + "</div></td></tr>";
}

// git log を CHUNK 件ずつ取得して、末尾が見えたら続きを読み込む (全件読むまで続く)
function showLog(titleHtml, rev, topHtml, fromHistory, restoreScroll) {
  if (!fromHistory) pushScreen({screen: "log", titleHtml: titleHtml, rev: rev, topHtml: topHtml});
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
      updateCmpClasses();
      skip += r.commits.length;
      if (fromHistory && restoreScroll) {
        listView.scrollTop = Math.max(0, Math.min(restoreScroll, listView.scrollHeight - listView.clientHeight));
      }
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

// 差分は別ページ (/diff) で開く。ctx は {hash} (1コミット) または {from, to} (2コミット間の比較)
function diffUrl(ctx, f) {
  const p = Object.assign({}, ctx, {path: f.path});
  if (f.old_path) p.old = f.old_path;
  if (f.wip) p.wip = f.wip;  // 作業ツリーの変更 (staged / unstaged / untracked / conflict)
  return "/diff?" + new URLSearchParams(p);
}

const ST_CLASS = {M: "stM", T: "stM", A: "stA", D: "stD", R: "stR", C: "stR", U: "stU", "?": "stN"};
function badge(f) {  // 変更の種類 (M/A/D/R...) のバッジ。WIP の一覧で使う
  return f.st ? '<span class="st ' + (ST_CLASS[f.st] || "stM") + '">' + esc(f.st) + "</span>" : "";
}
function filesHtml(ctx, files, offset) {
  offset = offset || 0;
  if (!files.length) return '<p class="hint">変更されたファイルはありません</p>';
  let add = 0, del = 0, max = 0;
  files.forEach(f => { add += f.added || 0; del += f.deleted || 0; max = Math.max(max, (f.added || 0) + (f.deleted || 0)); });
  const scale = max > 40 ? 40 / max : 1;
  const rows = files.map((f, k) => {
    const a = f.added || 0, d = f.deleted || 0;
    const pa = a ? Math.max(1, Math.round(a * scale)) : 0, pd = d ? Math.max(1, Math.round(d * scale)) : 0;
    const name = f.old_path ? f.old_path + " => " + f.path : f.path;
    return '<tr class="frow" data-k="' + (offset + k) + '"><td class="fp">' + badge(f) + '<a href="' + esc(diffUrl(ctx, f)) +
      '">' + esc(name) + '</a></td><td class="fc">' +
      (f.label ? esc(f.label) : f.added === null ? "Bin" : a + d) + '</td><td class="fb"><span class="plus">' + "+".repeat(pa) +
      '</span><span class="minus">' + "-".repeat(pd) + "</span></td></tr>";
  }).join("");
  return '<div class="meta">' + files.length + (files.some(f => f.added !== undefined)
      ? " files changed, " + add + " insertions(+), " + del + " deletions(-)" : " files") + "</div>" +
    '<table class="files">' + rows + "</table>";
}

let curDetailHash = null;

function localBranches(h) {
  const n = DATA.nodes[h];
  return n ? n.refs.filter(r => r.kind === "branch").map(r => r.name) : [];
}
function checkoutBtns(h) {
  return localBranches(h).map(b =>
    '<button class="abtn" data-act="checkout-branch" data-branch="' + esc(b) + '">checkout ' + esc(b) + "</button>").join(" ");
}
function toggleBtn() {  // ハッシュの行に置く [+] ボタン。押すとブランチ作成などの操作パネルが開く
  return DATA.writable ? '<button class="tglbtn" id="actToggle" aria-expanded="false" title="ブランチ作成・タグ作成・チェックアウト">+</button>' : "";
}
function actionsHtml(h) {  // ブランチ作成 / タグ作成 / チェックアウト (書き込みが有効なときだけ)
  if (!DATA.writable) return "";
  return '<div class="actions" id="actions">' +
    '<div class="arow"><input type="text" id="brName" placeholder="新しいブランチ名">' +
    '<label><input type="checkbox" id="brCo"> 作成後にチェックアウト</label>' +
    '<button class="abtn" data-act="branch">ブランチ作成</button></div>' +
    '<div class="arow"><input type="text" id="tagName" placeholder="タグ名">' +
    '<input type="text" id="tagMsg" placeholder="メッセージ (入れると注釈付きタグ)">' +
    '<button class="abtn" data-act="tag">タグ作成</button></div>' +
    '<div class="arow"><button class="abtn" data-act="checkout">チェックアウト (detached HEAD)</button>' +
    '<span id="coBtns">' + checkoutBtns(h) + "</span></div>" +
    '<div class="amsg" id="actMsg"></div></div>';
}
function showDetail(h, fromHistory) {
  if (!fromHistory) pushScreen({screen: "detail", hash: h});
  const back = '<button class="back-btn">← 履歴一覧に戻る</button>';
  detailView.innerHTML = back + '<p class="hint">読み込み中…</p>';
  showView("detail");
  api("/api/commit", {hash: h}).then(c => {
    curCtx = {hash: c.hash}; curFiles = c.files; curDetailHash = c.hash;
    const parents = c.parents.map(p => esc(p.slice(0, 7))).join(", ") || "(なし)";
    detailView.innerHTML = back + "<h2>コミット " + esc(c.hash.slice(0, 7)) + "</h2>" +
      '<span id="dChips">' + chips(c.hash) + "</span>" +
      '<div class="meta">' + esc(c.hash) + copyLink(c.hash) + toggleBtn() + "</div>" +
      '<div class="meta">Author: ' + esc(c.author) + "</div>" +
      '<div class="meta">Date: ' + esc(c.date) + "</div>" +
      '<div class="meta">Parent: ' + parents + "</div>" +
      "<pre>" + esc(c.message) + "</pre>" + actionsHtml(c.hash) + filesHtml(curCtx, c.files) +
      '<div class="meta"><code>git show ' + esc(c.hash.slice(0, 7)) + "</code></div>";
  }).catch(e => {
    detailView.innerHTML = back + '<p class="hint">エラー: ' + esc(e.message) + "</p>";
  });
}

function post(path, body) {
  return fetch(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)})
    .then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }));
}
function setActMsg(text, ok) {
  const el = detailView.querySelector("#actMsg");
  if (el) { el.textContent = text; el.className = "amsg " + (ok ? "ok" : "ng"); }
}
function inputVal(sel) {
  const el = detailView.querySelector(sel);
  return el ? el.value.trim() : "";
}
function doAction(btn) {
  const h = curDetailHash, act = btn.dataset.act;
  let url, body, ask = null;
  if (act === "branch") {
    const name = inputVal("#brName");
    if (!name) { setActMsg("ブランチ名を入力してください", false); return; }
    url = "/api/branch"; body = {hash: h, name: name, checkout: detailView.querySelector("#brCo").checked};
    if (body.checkout) ask = "ブランチ " + name + " を作成してチェックアウトします (作業ツリーが切り替わります)。よろしいですか？";
  } else if (act === "tag") {
    const name = inputVal("#tagName");
    if (!name) { setActMsg("タグ名を入力してください", false); return; }
    url = "/api/tag"; body = {hash: h, name: name, message: inputVal("#tagMsg")};
  } else if (act === "checkout") {
    url = "/api/checkout"; body = {hash: h};
    ask = "コミット " + h.slice(0, 7) + " を detached HEAD でチェックアウトします (作業ツリーが切り替わります)。よろしいですか？";
  } else if (act === "checkout-branch") {
    url = "/api/checkout"; body = {branch: btn.dataset.branch};
    ask = "ブランチ " + btn.dataset.branch + " をチェックアウトします (作業ツリーが切り替わります)。よろしいですか？";
  } else return;
  if (ask && !confirm(ask)) return;
  btn.disabled = true; setActMsg("実行中…", true);
  post(url, body)
    .then(r => refreshGraph().then(() => r.message, () => r.message + "\n(グラフの更新に失敗しました。再読み込みしてください)"))
    .then(msg => {
      if (curDetailHash !== h) return;  // 実行中に別のコミットへ移った
      setActMsg(msg, true);
      const c = detailView.querySelector("#dChips"), co = detailView.querySelector("#coBtns");
      if (c) c.innerHTML = chips(h);
      if (co) co.innerHTML = checkoutBtns(h);
    })
    .catch(e => { if (curDetailHash === h) setActMsg(e.message, false); })
    .finally(() => { btn.disabled = false; });
}

// 操作のあと、グラフを(表示したまま)作り直して新しいブランチ/タグ/HEAD を反映する
function refreshGraph() {
  return fetch("/api/graph")
    .then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }))
    .then(r => {
      const keys = new Set(Object.keys(DATA.nodes).concat(Object.keys(r.nodes)));
      const gc = document.getElementById("graph"), top0 = gc.scrollTop, left0 = gc.scrollLeft;
      DATA = {nodes: r.nodes, edges: r.edges, writable: r.writable};
      gc.innerHTML = r.svg;
      gc.scrollTop = top0; gc.scrollLeft = left0;
      selected = null;
      for (const k in nodeEl) delete nodeEl[k];
      init();
      if (selectedKey) {
        const g = [...gc.querySelectorAll("g.node, g.edge")].find(x => {
          const t = x.querySelector("title"); return t && t.textContent === selectedKey;
        });
        if (g) { g.classList.add("sel"); selected = g; }
      }
      updateCmpClasses();
      keys.forEach(h => listView.querySelectorAll('tr[data-h="' + h + '"] .rc').forEach(el => { el.innerHTML = chips(h); }));
    });
}

// ---- 2つのノードの比較 (Ctrl+クリック) ----
function commitLine(label, c) {
  return '<div class="meta"><span class="tag tag' + label + '">' + label + "</span>" + chips(c.hash) +
    esc(c.hash.slice(0, 7)) + copyLink(c.hash) + " — " + esc(c.message.split("\n")[0]) +
    '<div class="hint">' + esc(c.author) + " / " + esc(c.date) + "</div></div>";
}
function showCompare(a, b, fromHistory) {  // a, b はコミットのハッシュ
  if (!fromHistory) pushScreen({screen: "compare", a: a, b: b});
  const btns = '<button class="back-btn">← 履歴一覧に戻る</button><button class="sbtn">A ⇄ B 入れ替え</button>';
  detailView.innerHTML = btns + '<p class="hint">読み込み中…</p>';
  showView("detail");
  api("/api/compare", {from: a, to: b}).then(r => {
    curCtx = {from: r.from.hash, to: r.to.hash}; curFiles = r.files;
    detailView.innerHTML = btns + "<h2>比較 " + esc(r.from.hash.slice(0, 7)) + " → " + esc(r.to.hash.slice(0, 7)) + "</h2>" +
      commitLine("A", r.from) + commitLine("B", r.to) + filesHtml(curCtx, r.files) +
      '<div class="meta"><code>git diff ' + esc(r.from.hash.slice(0, 7)) + " " + esc(r.to.hash.slice(0, 7)) + "</code></div>";
  }).catch(e => {
    detailView.innerHTML = btns + '<p class="hint">エラー: ' + esc(e.message) + "</p>";
  });
}
function updateCmpClasses() {  // 選択中のコミットを、グラフのノードとログの行の両方でハイライトする
  document.querySelectorAll(".cmpA, .cmpB").forEach(el => el.classList.remove("cmpA", "cmpB"));
  cmp.forEach((h, i) => {
    const cls = i === 0 ? "cmpA" : "cmpB";
    if (nodeEl[h]) nodeEl[h].classList.add(cls);
    listView.querySelectorAll('tr[data-h="' + h + '"]').forEach(tr => tr.classList.add(cls));
  });
  const controls = document.getElementById("compareControls");
  controls.hidden = cmp.length === 0;
  document.getElementById("cmpA").textContent = cmp.length ? "A: " + cmp[0].slice(0, 7) : "";
  document.getElementById("cmpB").textContent = cmp.length > 1 ? "B: " + cmp[1].slice(0, 7) : "B: 未選択";
  document.getElementById("compareBtn").disabled = cmp.length !== 2;
}
function clearCmp() { cmp = []; updateCmpClasses(); }
function selectCmpA(hash) {
  cmp = [hash];
  if (selected) { selected.classList.remove("sel"); selected = null; selectedKey = null; }
  if (curRow) { curRow.classList.remove("cur"); curRow = null; }
  updateCmpClasses();
}
function selectCmpB(hash) {
  if (cmp.length < 1 || cmp[0] === hash) return;
  cmp = [cmp[0], hash];
  updateCmpClasses();
}

function restoreScreen(state) {
  if (state.screen === "log") showLog(state.titleHtml, state.rev, state.topHtml, true, state.scroll || 0);
  else if (state.screen === "detail") showDetail(state.hash, true);
  else if (state.screen === "compare") {
    cmp = [state.a, state.b];
    updateCmpClasses();
    showCompare(state.a, state.b, true);
  }
  else if (state.screen === "wip") showWip(true);
  else {
    if (observer) { observer.disconnect(); observer = null; }
    ++token;
    listView.innerHTML = homeHtml;
    showView("list");
  }
}
window.addEventListener("popstate", ev => restoreScreen(ev.state || {screen: "home"}));

listView.addEventListener("click", ev => {
  const tr = ev.target.closest("tr.clickable");
  if (!tr) return;
  if (ev.ctrlKey || ev.metaKey) { selectCmpA(tr.dataset.h); return; }
  if (cmp.length) { selectCmpB(tr.dataset.h); return; }
  clearCmp();
  if (curRow) curRow.classList.remove("cur");
  curRow = tr; tr.classList.add("cur");
  showDetail(tr.dataset.h);
});
detailView.addEventListener("click", ev => {
  const tg = ev.target.closest("#actToggle");
  if (tg) {
    const panel = detailView.querySelector("#actions");
    const open = panel.classList.toggle("open");
    tg.textContent = open ? "\u2212" : "+"; tg.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) { const f = panel.querySelector("#brName"); if (f) f.focus(); }
    return;
  }
  if (ev.target.closest("#wipReload")) { showWip(true); refreshGraph().catch(() => {}); return; }
  const ab = ev.target.closest(".abtn");
  if (ab) { doAction(ab); return; }
  if (ev.target.closest(".sbtn")) {
    cmp.reverse(); updateCmpClasses();
    if (cmp.length === 2) showCompare(cmp[0], cmp[1]);
    return;
  }
  if (ev.target.closest(".back-btn")) { history.back(); return; }
  const tr = ev.target.closest("tr.frow");
  if (tr && !ev.target.closest("a") && curCtx)  // 行のどこをクリックしても差分を開く
    location.href = diffUrl(curCtx, curFiles[+tr.dataset.k]);
});
document.getElementById("compareControls").addEventListener("click", ev => {
  if (ev.target.closest("#compareBtn") && cmp.length === 2) showCompare(cmp[0], cmp[1]);
  if (ev.target.closest("#clearCmpBtn")) clearCmp();
});

function select(g) {
  if (selected) selected.classList.remove("sel");
  selected = g; g.classList.add("sel");
  const t = g.querySelector("title");
  selectedKey = t ? t.textContent : null;  // グラフを作り直したあとで選択を復元するため
}

function onNodeClick(ev, g, n) {
  if (n.wip) {  // 作業ツリーの変更 (WIP) ノード: ログではなく変更ファイルの一覧を出す
    if (ev.ctrlKey || ev.metaKey) return;
    clearCmp(); select(g); showWip();
    return;
  }
  if (ev.ctrlKey || ev.metaKey) { selectCmpA(n.hash); return; }
  select(g);
  showLog("git log --oneline " + esc(n.hash.slice(0, 7)), n.hash,
          chips(n.hash) + '<div class="meta">最初のコミットまでの全ログ</div>');
}

function init() {
  document.querySelectorAll("svg g.node").forEach(g => {
    const t = g.querySelector("title");
    const n = t && DATA.nodes[t.textContent];
    if (n) { nodeEl[n.hash] = g; g.addEventListener("click", ev => onNodeClick(ev, g, n)); }
  });
  document.querySelectorAll("svg g.edge").forEach(g => {
    const t = g.querySelector("title");
    const e = t && DATA.edges[t.textContent];
    if (!e) return;
    const path = g.querySelector("path");
    if (path) { const hit = path.cloneNode(); hit.setAttribute("class", "hit"); g.appendChild(hit); }
    g.addEventListener("click", () => {
      clearCmp(); select(g);
      const range = e.from.slice(0, 7) + ".." + e.to.slice(0, 7);
      showLog(esc(e.from.slice(0, 7)) + " → " + esc(e.to.slice(0, 7)), e.from + ".." + e.to,
              '<div class="meta"><code>git log ' + esc(range) + "</code></div>");
    });
  });
}

// ---- 作業ツリーの変更 (WIP) ----
function showWip(fromHistory) {
  if (!fromHistory) pushScreen({screen: "wip"});
  const btns = '<button class="back-btn">← 履歴一覧に戻る</button><button class="sbtn" id="wipReload">↻ 更新</button>';
  detailView.innerHTML = btns + '<p class="hint">読み込み中…</p>';
  showView("detail"); curDetailHash = null;
  api("/api/status", {}).then(s => {
    const sections = [["conflict", "競合", s.conflicts], ["staged", "ステージ済み (git add 済み)", s.staged],
                      ["unstaged", "未ステージの変更", s.unstaged], ["untracked", "未追跡のファイル", s.untracked]];
    let files = [], body = "";
    sections.forEach(([kind, title, items]) => {
      if (!items.length) return;
      const list = items.map(f => Object.assign({wip: kind}, f));
      body += "<h3>" + title + " (" + list.length + ")</h3>" + filesHtml({}, list, files.length);
      files = files.concat(list);
    });
    curCtx = {}; curFiles = files;
    detailView.innerHTML = btns + "<h2>作業ツリーの変更 (WIP)</h2>" +
      '<div class="meta">HEAD: ' + (s.head ? chips(s.head) + esc(s.head.slice(0, 7)) : "(コミットなし)") + "</div>" +
      (body || '<p class="hint">変更はありません</p>') +
      (s.truncated ? '<p class="hint">ファイルが多いため先頭のみ表示しています</p>' : "");
  }).catch(e => {
    detailView.innerHTML = btns + '<p class="hint">エラー: ' + esc(e.message) + "</p>";
  });
}

document.getElementById("reload").addEventListener("click", ev => {  // グラフを最新の状態に更新
  const b = ev.currentTarget;
  b.disabled = true; b.textContent = "更新中…";
  refreshGraph().catch(e => alert("更新に失敗しました: " + e.message))
    .finally(() => { b.disabled = false; b.textContent = "↻ 更新"; });
});

// ---- グラフの読み込み (サーバー側の作成に時間がかかるので、読み込み中画面を出す) ----
const t0 = Date.now();
const timer = setInterval(() => {
  const el = document.getElementById("lsub");
  if (el) el.textContent = Math.round((Date.now() - t0) / 1000) + " 秒経過（リポジトリが大きいと時間がかかります）";
}, 1000);
fetch("/api/graph")
  .then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }))
  .then(r => {
    clearInterval(timer);
    DATA = {nodes: r.nodes, edges: r.edges, writable: r.writable};
    document.getElementById("graph").innerHTML = r.svg;
    init();
    document.getElementById("reload").disabled = false;
    restoreScreen(history.state || {screen: "home"});
  })
  .catch(e => {
    clearInterval(timer);
    document.getElementById("graph").innerHTML = '<div id="loading"><div>グラフの作成に失敗しました</div><pre>' +
      esc(e.message) + '</pre><button class="sbtn" onclick="location.reload()">再読み込み</button></div>';
  });
</script>
</body>
</html>
"""

HTML_TEMPLATE = HTML_TEMPLATE.replace("/*__COPY_JS__*/", COPY_JS)

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
            res["untracked"].append({"path": path, "st": "?", "label": "新規"})
        elif xy in CONFLICT_CODES:
            res["conflicts"].append({"path": path, "st": "U", "label": "競合"})
        else:
            x, y = xy[0], xy[1]
            if x != " ":
                res["staged"].append({"path": path, "old_path": old if x in "RC" else None, "st": x})
            if y != " ":
                res["unstaged"].append({"path": path, "old_path": None, "st": y})
    return res


def wip_count(status):
    return len({f["path"] for k in ("staged", "unstaged", "untracked", "conflicts") for f in status[k]})


def api_status(q):
    """作業ツリーの変更 (WIP)。ステージ済み/未ステージには追加・削除行数も付ける。"""
    if not WIP_ENABLED:
        raise ValueError("作業ツリーの変更の表示は無効です")
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
    return s


def wip_diff(kind, path, specs):
    if not HAS_WORKTREE:
        raise ValueError("ワークツリーがありません")
    if kind == "staged":
        return git("diff", "--cached", "--no-color", "-M", "--", *specs, literal=True)
    if kind in ("unstaged", "conflict"):
        return git("diff", "--no-color", "--", path, literal=True)
    if kind == "untracked":
        # 任意のファイルを読ませないよう、いま未追跡と報告されているパスだけを対象にする
        known = {f["path"] for f in parse_status(read_status_raw())["untracked"]}
        if path not in known:
            raise ValueError("未追跡ファイルではありません")
        full = os.path.join(REPO, path)
        if os.path.islink(full):
            return f"symlink -> {os.readlink(full)}\n"
        r = subprocess.run(["git", "-C", REPO, "diff", "--no-index", "--no-color", "--", "/dev/null", path],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode not in (0, 1):  # 1 は「差分あり」
            raise ValueError(r.stderr.strip() or "git diff に失敗しました")
        return r.stdout
    raise ValueError("bad wip")


def add_wip(dot_text, nodes, head, count):
    """HEAD の子として、破線の WIP ノードを dot とノード情報に追加する。"""
    label = ('<<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="3" STYLE="dashed" BGCOLOR="#fff8dc">'
             f'<TR><TD><B>WIP</B></TD></TR><TR><TD>{count} 件の変更</TD></TR></TABLE>>')
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
        key = git("for-each-ref") + head
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
        svg = subprocess.run(["dot", "-Tsvg"], input=dot_text, capture_output=True,
                             text=True, check=True).stdout
        svg = svg[svg.index("<svg"):]
        payload = json.dumps({"svg": svg, "nodes": nodes, "edges": edges, "writable": WRITABLE},
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


def api_compare(q):
    """2つのコミット間の差分 (from → to) のファイル一覧。"""
    a = commit_info(q.get("from", [""])[0])
    b = commit_info(q.get("to", [""])[0])
    raw = git("diff", "--numstat", "-z", "-M", a["hash"], b["hash"], "--")
    return {"from": a, "to": b, "files": parse_numstat(raw)}


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
        out = git("diff", "--no-color", "-M", a, b, "--", *specs, literal=True)
    else:
        h = q.get("hash", [""])[0]
        if not HASH_RE.fullmatch(h):
            raise ValueError("bad hash")
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
  #side .grp { margin: 10px 0 2px; font-weight: bold; }
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


WIP_TITLES = {"conflict": "競合", "staged": "ステージ済み", "unstaged": "未ステージの変更", "untracked": "未追跡のファイル"}


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
            f'<a class="copy" data-copy="{c["hash"]}" title="ハッシュ (40桁) をコピー">📋</a> — '
            f'{html.escape(subject)} <span class="hint">({html.escape(c["author"])} / '
            f'{html.escape(c["date"])})</span></div>')


def wip_files():
    """WIP の全ファイルを 競合 → ステージ済み → 未ステージ → 未追跡 の順に、種別(wip)つきで返す。"""
    s = api_status({})
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
    if wip:  # 作業ツリーの変更
        files, ctx, ref = wip_files(), {}, "WIP"
        info = f'<div class="meta">作業ツリーの変更 (WIP) — {html.escape(WIP_TITLES.get(wip, wip))}</div>'
        cmd = {"staged": f"git diff --cached -- {path}",
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
        info = (f'<div class="meta">コミット {ref}'
                f'<a class="copy" data-copy="{c["hash"]}" title="ハッシュ (40桁) をコピー">📋</a>'
                f' — {html.escape(subject)}</div>'
                f'<div class="meta">{html.escape(c["author"])} / {html.escape(c["date"])}</div>')
        cmd = f"git show {ref} -- {path}"
    name = f"{old} => {path}" if old else path

    side, last = [], None
    for f in files:
        if wip and f.get("wip") != last:
            last = f.get("wip")
            side.append(f'<div class="grp">{html.escape(WIP_TITLES[last])}</div>')
        fname = f"{f['old_path']} => {f['path']}" if f.get("old_path") else f["path"]
        if f.get("label"):
            counts = html.escape(f["label"])
        elif "added" not in f:
            counts = ""
        elif f["added"] is None:
            counts = "Bin"
        else:
            counts = (f'<span class="plus">+{f["added"]}</span> '
                      f'<span class="minus">-{f["deleted"]}</span>')
        cur = "cur" if f["path"] == path and (not wip or f.get("wip") == wip) else ""
        side.append(f'<a class="{cur}" href="{html.escape(diff_url(ctx, f))}">'
                    f'<span>{html.escape(fname)}</span><span class="n">{counts}</span></a>')

    return f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8">
<title>{html.escape(path)} @ {ref}</title>
<style>{DIFF_CSS}</style></head><body>
<div id="side">
  <div class="meta">{len(files)} files changed</div>
  {"".join(side)}
</div>
<div id="main">
  <h1>{html.escape(name)}</h1>
  {info}
  <div class="meta"><code>{html.escape(cmd)}</code></div>
  {'<p class="hint">大きいため先頭のみ表示しています</p>' if d["truncated"] else ""}
  <div class="diff">{diff_lines_html(d["diff"]) or '<p class="hint">差分はありません</p>'}</div>
</div>
<script>{COPY_JS}</script>
</body></html>
"""


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
        raise ValueError("コミットが見つかりません")


def check_ref_name(kind, name):
    prefix = {"branch": "refs/heads/", "tag": "refs/tags/"}[kind]
    label = {"branch": "ブランチ", "tag": "タグ"}[kind]
    if not name or name.startswith("-") or "\0" in name:
        raise ValueError(f"{label}名が不正です")
    r = subprocess.run(["git", "-C", REPO, "check-ref-format", prefix + name], capture_output=True)
    if r.returncode != 0:
        raise ValueError(f"{name!r} は{label}名として使えません")


def run_git_write(*args):
    """git を実行し、失敗したら git のエラーメッセージを ValueError にして返す。"""
    r = subprocess.run(["git", "-C", REPO, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise ValueError((r.stderr or r.stdout).strip() or "git コマンドが失敗しました")
    return (r.stdout + r.stderr).strip()


def api_branch(body):
    h = resolve_commit(body.get("hash", ""))
    name = str(body.get("name", "")).strip()
    check_ref_name("branch", name)
    if body.get("checkout"):
        run_git_write("checkout", "-b", name, h)
        return {"ok": True, "message": f"ブランチ {name} を作成してチェックアウトしました"}
    run_git_write("branch", name, h)
    return {"ok": True, "message": f"ブランチ {name} を作成しました"}


def api_tag(body):
    h = resolve_commit(body.get("hash", ""))
    name = str(body.get("name", "")).strip()
    msg = str(body.get("message", "")).strip()
    check_ref_name("tag", name)
    if msg:
        run_git_write("tag", "-a", name, "-m", msg, h)
        return {"ok": True, "message": f"注釈付きタグ {name} を作成しました"}
    run_git_write("tag", name, h)
    return {"ok": True, "message": f"タグ {name} を作成しました"}


def api_checkout(body):
    branch = body.get("branch")
    if branch:
        branch = str(branch)
        check_ref_name("branch", branch)
        run_git_write("checkout", branch, "--")
        return {"ok": True, "message": f"ブランチ {branch} をチェックアウトしました"}
    h = resolve_commit(body.get("hash", ""))
    run_git_write("checkout", "--detach", h)
    return {"ok": True, "message": f"{h[:7]} をチェックアウトしました (detached HEAD)"}


POST_ROUTES = {"/api/branch": api_branch, "/api/tag": api_tag, "/api/checkout": api_checkout}


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
                self._send(200, "text/html", HTML_TEMPLATE)
            elif u.path == "/api/log":
                self._send(200, "application/json", json.dumps(api_log(q), ensure_ascii=False))
            elif u.path == "/api/status":
                self._send(200, "application/json", json.dumps(api_status(q), ensure_ascii=False))
            elif u.path == "/api/graph":
                self._send(200, "application/json", graph_payload())
            elif u.path == "/api/compare":
                self._send(200, "application/json", json.dumps(api_compare(q), ensure_ascii=False))
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

    def _check_post(self):
        """他サイトのページからの書き込みを防ぐ (CSRF / DNS rebinding 対策)。"""
        if not WRITABLE:
            raise Forbidden("書き込み操作は無効です (--read-only 指定、またはローカル以外で待ち受け中)")
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        if ctype != "application/json":  # 他サイトからは preflight が必要になるため送れない
            raise ValueError("Content-Type は application/json にしてください")
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        if origin is not None and origin != "http://" + host:
            raise Forbidden("Origin が一致しません")
        if is_loopback(OPTS.host):
            name = host if host.endswith("]") or ":" not in host else host.rsplit(":", 1)[0]
            if name not in ("127.0.0.1", "localhost", "[::1]"):
                raise Forbidden("Host が不正です")

    def do_POST(self):
        try:
            route = POST_ROUTES.get(urlparse(self.path).path)
            if route is None:
                self._send(404, "text/plain", "not found")
                return
            self._check_post()
            n = int(self.headers.get("Content-Length") or 0)
            if n > 100000:
                raise ValueError("body too large")
            body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("bad body")
            with _write_lock:
                res = route(body)
            self._send(200, "application/json", json.dumps(res, ensure_ascii=False))
        except Forbidden as e:
            self._send(403, "text/plain", str(e))
        except ValueError as e:  # JSON の不正も含む
            self._send(400, "text/plain", str(e))

    def log_message(self, fmt, *args):  # アクセスログは出さない
        pass


def main():
    global REPO, OPTS, WRITABLE, WIP_ENABLED, HAS_WORKTREE
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
    ap.add_argument("--no-wip", action="store_true", help="未コミットの変更 (WIP ノード) を表示しない")
    ap.add_argument("--read-only", action="store_true", help="書き込み操作 (ブランチ/タグ作成・チェックアウト) を無効にする")
    ap.add_argument("--allow-write", action="store_true",
                    help="127.0.0.1 以外で待ち受けるときも書き込み操作を許可する (既定では無効)")
    OPTS = ap.parse_args()
    REPO = OPTS.repo
    WRITABLE = (not OPTS.read_only) and (is_loopback(OPTS.host) or OPTS.allow_write)

    try:
        git("rev-parse", "--git-dir")
    except (subprocess.CalledProcessError, FileNotFoundError):
        sys.exit(f"{REPO!r} は git リポジトリではありません")
    # サブディレクトリが指定されても、パスの扱い (status / diff はルート基準) がずれないようルートに揃える
    r = subprocess.run(["git", "-C", REPO, "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    HAS_WORKTREE = r.returncode == 0
    if HAS_WORKTREE:
        REPO = r.stdout.strip()
    WIP_ENABLED = HAS_WORKTREE and not OPTS.no_wip

    if OPTS.dot:
        print(build_graph(OPTS)[0])
        return

    server = ThreadingHTTPServer((OPTS.host, OPTS.port), Handler)
    url = f"http://{OPTS.host}:{OPTS.port}/"
    print(f"serving {url}  (Ctrl+C で終了)  書き込み操作: {'有効' if WRITABLE else '無効'}")
    if OPTS.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
