#!/usr/bin/env python3
"""git のコミットグラフを、ブランチ/タグ/末端 + それらの分岐点(merge-base)だけに省略して
ブラウザで表示するローカルサーバー。ログやコミット詳細はクリック時に git から動的に取得する。

使い方 (標準ライブラリのみ。Graphviz の dot が必要):
  python git_graph_server.py                    # カレントディレクトリのリポジトリ
  python git_graph_server.py -C /path/to/repo --open
  python git_graph_server.py --no-remotes --no-tags main dev
  python git_graph_server.py --dot              # dot を標準出力に出して終了

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
  POST /api/cherry-pick {hash} (JSON)                        選んだコミットを現在の HEAD にチェリーピック (競合したら中止して元に戻す)
  /api/rebase-todo?hash=<hash>                               対話的リベースの対象コミット一覧 (古い順。git rebase -i が作る todo と同じ)
  POST /api/rebase-i {hash, todo:[{hash, action, message}]}  対話的リベース (git rebase -i 相当)
     action は pick / reword (message が新しいコミットメッセージ) / squash / fixup / drop。todo の並び順がそのまま適用順になる。
     競合やエラーで止まったときは自動で中止して元に戻す (edit は未対応)
  /api/head                                                  現在のブランチ名 (マージ/リベース前の確認用)
  /api/stash-list                                            stash の一覧 (git stash list)。画面では「操作」メニューの「stash一覧」
  POST /api/stash {hash, op} (JSON)                          stash の apply / pop / drop (hash は stash のコミット。op は apply | pop | drop)。画面では、stash を比較のAに選んだときの操作
  /api/reflog?[branch=<名前>]&skip=0&limit=500               reflog (省略時は HEAD、branch 指定でそのローカルブランチ)。画面では「操作」メニューの「reflog」
  POST /api/fetch {prune} | /api/pull {mode} | /api/push {branch, force, set_upstream} (JSON)
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
import html
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from collections import Counter, defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

REPO = "."
OPTS = None
WRITABLE = False  # ブランチ/タグ作成・チェックアウト等の書き込み操作を許可するか
WIP_ENABLED = False  # 未コミットの変更を WIP ノードとしてグラフに出すか
HAS_WORKTREE = False  # ワークツリーのあるリポジトリか (bare でないか)
FAST_LAYOUT_NODES = 300  # これより多くのノードを描くときは dot の最適化を軽くする (--full-layout で無効)
COLORS = {"branch": "#b7e4a7", "current": "#b7e4a7", "remote": "#a7c8f0", "tag": "#ffe9a0", "head": "#f4a7a7"}


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

    parents, subjects, order = {}, {}, []  # order: 子が親より先 (--topo-order)
    children = defaultdict(list)
    for line in git("log", *revs, "--topo-order", "--format=%H%x09%P%x09%s").splitlines():
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
    fmt = "%(objectname)%09%(refname)%09%(*objectname)"
    for line in git("for-each-ref", f"--format={fmt}").splitlines():
        obj, ref, peeled = (line.split("\t") + [""])[:3]
        target = peeled or obj
        if target not in parents:
            continue
        if ref.startswith("refs/heads/"):
            name = ref[len("refs/heads/"):]
            labels[target].append((name, "current" if name == current_branch else "branch"))
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
    # merge-base を git で総当たり (O(n^2) 回のプロセス起動) せず、メモリ上で求める。
    # desc[x] = x の子孫である base コミットの集合 (ビットマスク)。
    # x が (a, b) の merge-base <=> a, b が desc[x] に入り、どの子の desc にも a, b が揃って入っていない。
    base = set(labels) | {h for h in parents if not children[h]}
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
    keep_order.reverse()  # 出力の順序を毎回同じにする (レイアウトが安定する)

    out = [
        "digraph G {",
        f"  rankdir={opts.rankdir};",
        *(["  nslimit=1; nslimit1=1; mclimit=0.3;"]  # ノードが多いときは dot のレイアウト最適化を軽くして高速化
          if len(keep) > FAST_LAYOUT_NODES and not getattr(opts, "full_layout", False) else []),
        '  node [shape=plaintext, fontname="Helvetica", fontsize=10];',
        '  edge [fontname="Helvetica", fontsize=9];',
    ]
    for h in keep_order:
        rows = []
        for name, kind in labels.get(h, []):
            marker = "★ " if kind == "current" else ""
            rows.append(f'<TR><TD BGCOLOR="{COLORS[kind]}"><B>{marker}{html.escape(name)}</B></TD></TR>')
        rows.append(f'<TR><TD>{h[:7]}<BR/>{html.escape(subjects[h][:30])}</TD></TR>')
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
  const current = document.getElementById("pushCurrentBtn");
  current.textContent = "カレントブランチを push" + (currentBranch ? " (" + currentBranch + ")" : "");
  current.title = currentBranch ? currentBranch + " を push" : "カレントブランチを push";
  current.disabled = !currentBranchLoaded || !currentBranch || !DATA.writable;
  const branches = currentBranchLoaded && actionHash
    ? localBranches(actionHash).filter(branch => branch !== currentBranch) : [];
  document.getElementById("selectedPushActions").innerHTML = branches.map(branch =>
    '<button class="abtn" data-cact="push" data-branch="' + esc(branch) + '">' + esc(branch) + " を push</button>"
  ).join("");
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
  #graphStatus { position: absolute; bottom: 12px; left: 50%; transform: translateX(-50%); z-index: 5;
    display: flex; flex-direction: column-reverse; gap: 6px; width: max-content; max-width: calc(100% - 32px);
    max-height: 45%; overflow-y: auto; pointer-events: auto; }
  #graphStatus:empty { display: none; }
  .graph-toast { display: flex; align-items: flex-start; gap: 10px; padding: 8px 10px 8px 12px;
    border: 1px solid #ccc; border-radius: 4px; background: #fff; color: #555;
    box-shadow: 0 2px 8px #0003; white-space: pre-wrap; overflow-wrap: anywhere; }
  .graph-toast.success { color: #1a7f37; border-color: #8cce9c; }
  .graph-toast.error { color: #cf222e; border-color: #e7a0a4; }
  .graph-toast-message { flex: 1; min-width: 0; }
  .graph-toast-close { flex: none; border: 0; background: transparent; color: inherit; cursor: pointer; font: inherit; padding: 0 2px; }
  #compareControls { position: absolute; top: 8px; right: 14px; z-index: 5; }
  #compareControls [hidden] { display: none; }
  #compareToolbar, #cmpActions { padding: 6px; border: 1px solid #ccc; border-radius: 4px; background: #fff; box-shadow: 0 1px 4px #0002; }
  #compareToolbar { display: flex; align-items: center; gap: 6px; }
  #compareToolbar .sbtn { margin: 0; }
  #cmpActions { position: absolute; top: 100%; right: 0; display: flex; flex-direction: column; gap: 6px; box-sizing: border-box; min-width: 250px; max-width: min(440px, calc(100vw - 24px)); max-height: calc(100vh - 70px); overflow: auto; margin-top: 4px; }
  #cmpActions[hidden] { display: none; }
  #cmpActions label { white-space: nowrap; }
  #cmpActions select { max-width: 100%; }
  .menu-row { display: flex; flex-direction: column; gap: 4px; }
  .menu-row > .abtn { align-self: stretch; text-align: left; }
  .menu-section { display: flex; flex-direction: column; gap: 4px; }
  .menu-separator { border: 0; border-top: 1px solid #ccc; width: 100%; margin: 0; }
  #compareControls .cmp-hash { font-family: monospace; white-space: nowrap; }
  #compareControls #actionTarget { color: #6f42c1; }
  #splitter { flex: 0 0 6px; cursor: col-resize; background: #eee; border-left: 1px solid #ccc; border-right: 1px solid #ccc; box-sizing: border-box; touch-action: none; }
  #splitter:hover, body.resizing #splitter { background: #c8d8f0; }
  body.resizing { cursor: col-resize; user-select: none; }
  #panel { flex: 0 0 var(--panel-width, 480px); min-width: 240px; display: flex; flex-direction: column; border-left: 1px solid #ccc; background: #fafafa; box-sizing: border-box; }
  #listView, #detailView { flex: 1; min-height: 0; overflow: auto; padding: 14px; box-sizing: border-box; }
  #detailView { display: none; background: #fff; }
  h2 { font-size: 14px; margin: 0 0 8px; }
  pre { white-space: pre-wrap; word-break: break-word; background: #fff; border: 1px solid #ddd; padding: 8px; margin: 6px 0; font-size: 12px; }
  code { background: #eee; padding: 1px 4px; border-radius: 3px; user-select: all; }
  .chip { display: inline-block; padding: 0 6px; border-radius: 3px; margin: 0 4px 2px 0; font-weight: bold; }
  .chip .ref-delete { border: 0; background: transparent; color: #555; cursor: pointer; font: inherit; font-weight: normal; line-height: inherit; margin-left: 4px; padding: 0 2px; }
  .ref-delete:hover { color: #cf222e; }
  .tag { display: inline-block; padding: 0 6px; border-radius: 3px; margin-right: 4px; font-weight: bold; color: #fff; }
  .tagA { background: #06c; } .tagB { background: #e67e00; }
  .meta { color: #555; margin: 2px 0; }
  .hint { color: #777; }
  .copy { cursor: pointer; font-size: 12px; margin-left: 6px; user-select: none; text-decoration: none; border-radius: 3px; padding: 0 2px; }
  .copy:hover { background: #e4e4e4; }
  td.h .copy { opacity: .45; } tr:hover td.h .copy { opacity: 1; }
  .actions { border: 1px solid #ddd; background: #fafafa; border-radius: 4px; padding: 8px; margin: 8px 0; }
  .actions .arow { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin: 4px 0; }
  .actions input[type=text] { padding: 3px 6px; border: 1px solid #bbb; border-radius: 3px; min-width: 0; flex: 1; }
  .abtn { background: #e8eefc; border: 1px solid #b8c7ee; padding: 3px 8px; border-radius: 3px; cursor: pointer; }
  .abtn:hover { background: #d6e2fb; } .abtn:disabled { opacity: .5; cursor: default; }
  #coModal, #actionModal { position: fixed; inset: 0; background: rgba(0,0,0,.4); display: flex; align-items: center; justify-content: center; z-index: 1000; }
  #coModal[hidden] { display: none; }
  #actionModal[hidden] { display: none; }
  .comodal { background: #fff; border-radius: 6px; padding: 14px 16px; max-width: 560px; max-height: 80vh; overflow: auto; box-shadow: 0 4px 20px rgba(0,0,0,.3); }
  .action-dialog { background: #fff; border-radius: 6px; padding: 14px 16px; width: min(420px, calc(100vw - 48px)); max-height: 80vh; overflow: auto; box-shadow: 0 4px 20px rgba(0,0,0,.3); }
  .action-dialog h2 { font-size: 16px; margin: 0 0 12px; }
  .action-fields { display: flex; flex-direction: column; gap: 10px; }
  .action-fields label { display: flex; flex-direction: column; gap: 4px; }
  .action-fields label[hidden] { display: none; }
  .action-fields label.check { flex-direction: row; align-items: center; }
  .action-fields input[type=text], .action-fields select { box-sizing: border-box; width: 100%; padding: 5px 7px; }
  .dialog-buttons { display: flex; justify-content: flex-end; gap: 6px; margin-top: 14px; }
  .dialog-buttons button { margin: 0; }
  .comodal ul { margin: 2px 0 8px; padding-left: 20px; font-family: monospace; word-break: break-all; }
  .comodal .arow { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
  .action-dialog.wide { width: min(760px, calc(100vw - 48px)); }
  .ri-note { color: #555; margin-bottom: 4px; }
  .ri-list { display: flex; flex-direction: column; gap: 4px; }
  .ri-row { border: 1px solid #ddd; border-radius: 4px; padding: 4px 6px; background: #fff; }
  .ri-main { display: flex; align-items: center; gap: 6px; }
  .ri-main select { flex: none; width: 90px; padding: 2px; }
  .ri-main code { flex: none; color: #555; }
  .ri-subj { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .ri-author { flex: none; color: #888; font-size: 11px; }
  .ri-main button { flex: none; padding: 0 6px; cursor: pointer; }
  .ri-row textarea { box-sizing: border-box; width: 100%; margin-top: 4px; font: inherit; padding: 4px 6px; }
  .ri-row textarea[hidden] { display: none; }
  .ri-row.drop { background: #f6f6f6; } .ri-row.drop .ri-subj { text-decoration: line-through; color: #999; }
  .ri-row.squash, .ri-row.fixup { margin-left: 22px; background: #f3f8ff; }
  #actionError { margin-top: 8px; white-space: pre-wrap; } #actionError:empty { display: none; }
  .abtn.danger { background: #fde8e8; border-color: #eeb8b8; } .abtn.danger:hover { background: #fbd3d3; }
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
  table.log td.h { width: 1%; font-family: monospace; white-space: nowrap; color: #b60; }
  table.log .cmp-pick { display: flex; gap: 3px; margin-top: 3px; opacity: 0; }
  table.log tr:hover .cmp-pick, table.log tr:focus-within .cmp-pick { opacity: 1; }
  table.log .cmp-pick .sbtn { margin: 0; padding: 1px 5px; font-size: 11px; }
  table.log tr.actionTarget td { box-shadow: inset 0 2px #6f42c1, inset 0 -2px #6f42c1; }
  g.node.actionTarget polygon { stroke: #6f42c1; stroke-width: 3; }
  @media (hover: none) { table.log .cmp-pick { opacity: 1; } }
  table.log .meta { font-size: 12px; color: #888; margin: 0; }
  table.files { border-collapse: collapse; margin: 6px 0; font-family: monospace; font-size: 12px; width: 100%; }
  table.files td { padding: 2px 6px; vertical-align: top; }
  tr.frow { cursor: pointer; } tr.frow:hover td { background: #eef; }
  td.fp { color: #06c; word-break: break-all; } td.fc { text-align: right; white-space: nowrap; } td.fb { white-space: nowrap; }
  td.fp a { color: inherit; text-decoration: none; } td.fp a:hover { text-decoration: underline; }
  td.fk { width: 1%; padding-right: 0; } td.fk input, h3 .wipall { margin: 0 6px 0 0; vertical-align: middle; cursor: pointer; }
  .wipbox { border: 1px solid #ddd; background: #fafafa; border-radius: 4px; padding: 8px; margin: 8px 0; }
  .wipbox textarea { width: 100%; box-sizing: border-box; min-height: 56px; padding: 4px 6px; border: 1px solid #bbb; border-radius: 3px; font: inherit; resize: vertical; }
  .wipbox .arow { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 6px; }
  .wipbox .amendrow { display: block; margin: 0 0 6px; cursor: pointer; }
  .wipbox .amendrow input { margin: 0 6px 0 0; vertical-align: middle; }
  .wbtn { background: #1a7f37; color: #fff; border: 1px solid #176b30; padding: 3px 10px; border-radius: 3px; cursor: pointer; }
  .wbtn:hover { background: #176b30; } .wbtn:disabled { opacity: .5; cursor: default; }
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
<div id="graphStatus" role="region" aria-label="通知"></div>
<div id="compareControls">
  <div id="compareToolbar">
    <button id="reload" class="sbtn" disabled title="グラフを最新の状態に更新">↻ 更新</button>
    <span class="cmp-hash" id="actionTarget"></span>
    <span class="cmp-hash" id="cmpA"></span><span class="cmp-hash" id="cmpB"></span>
    <button class="sbtn" id="cmpActToggle" aria-expanded="false" aria-controls="cmpActions">操作 ▾</button>
    <button class="sbtn" id="clearCmpBtn">クリア</button>
  </div>
  <div class="actions" id="cmpActions" hidden>
    <div class="menu-section" id="remoteActions">
      <div class="menu-section" id="pushActions">
        <button class="abtn" id="pushCurrentBtn" data-cact="push">push</button>
        <div class="menu-section" id="selectedPushActions"></div>
      </div>
      <button class="abtn" data-cact="pull">pull</button>
      <button class="abtn" data-cact="fetch">fetch</button>
    </div>
    <hr class="menu-separator" id="selectionMenuSeparator" hidden>
    <div class="menu-section" id="aActions" hidden>
      <button class="abtn" id="filesBtn" title="対象コミットのファイル一覧を別タブで開く">ファイル表示</button>
      <button class="abtn" id="mergeBtn" title="Aを現在のHEADにマージ">マージ</button>
      <button class="abtn" id="rebaseBtn" title="現在のブランチをAの上にリベース">リベース</button>
      <button class="abtn" id="irebaseBtn" title="現在のブランチをAの上に、コミットの並べ替え・squash・メッセージ変更・削除をしながらリベース (git rebase -i)">対話的リベース</button>
      <button class="abtn" id="cherryBtn" title="Aを現在のHEADにチェリーピック">チェリーピック</button>
      <button class="abtn danger" id="resetBtn" title="現在のHEADを対象コミットへリセット">対象へリセット</button>
      <button class="abtn" data-cact="branch">ブランチ作成</button>
      <button class="abtn" data-cact="tag">タグ作成</button>
      <button class="abtn" data-cact="checkout">チェックアウト</button>
    </div>
    <hr class="menu-separator" id="compareMenuSeparator" hidden>
    <button class="abtn" id="compareBtn" hidden>AB比較</button>
    <hr class="menu-separator">
    <div class="menu-section" id="viewActions">
      <button class="abtn" id="stashListBtn" title="stash の一覧 (git stash list)">stash一覧</button>
      <button class="abtn" id="reflogBtn" title="reflog (HEAD。ブランチは画面で切り替え)">reflog</button>
    </div>
    <div class="menu-section" id="stashActions" hidden>
      <button class="abtn" id="stashApplyBtn" title="この stash の変更を作業ツリーに適用 (stash は残す)">stash 適用 (apply)</button>
      <button class="abtn" id="stashPopBtn" title="この stash の変更を作業ツリーに適用して、stash を削除">stash 適用して削除 (pop)</button>
      <button class="abtn danger" id="stashDropBtn" title="この stash を削除">stash 削除 (drop)</button>
    </div>
    <hr class="menu-separator">
    <div class="menu-section" id="remoteConfigActions">
      <button class="abtn" data-cact="remote-add" title="リモートを追加 (git remote add)">remote 追加</button>
      <button class="abtn danger" data-cact="remote-remove" title="リモートを削除 (git remote remove)。リモート追跡ブランチも消えます">remote 削除</button>
    </div>
  </div>
</div>
<div id="actionModal" hidden>
  <form class="action-dialog" id="actionForm" role="dialog" aria-modal="true" aria-labelledby="actionTitle">
    <h2 id="actionTitle"></h2>
    <div class="action-fields" id="actionFields"></div>
    <div class="amsg ng" id="actionError" role="alert"></div>
    <div class="dialog-buttons">
      <button type="button" class="abtn" data-dialog-cancel>キャンセル</button>
      <button type="submit" class="abtn" id="actionSubmit"></button>
    </div>
  </form>
</div>
<div id="coModal" hidden><div class="comodal" role="dialog" aria-modal="true">
  <div><b>チェックアウトできません</b> — 次のファイルが邪魔をしています。</div>
  <div id="coFiles"></div>
  <div class="hint">stash: 編集中の変更と管理外ファイルをすべて stash に退避します (あとで git stash pop で戻せます)。<br>
  削除: 上のファイルだけを破棄します。編集内容は HEAD の状態に戻り、管理外ファイルは削除されます (元に戻せません)。</div>
  <div class="arow"><button class="abtn" data-co="stash">stash してチェックアウト</button>
    <button class="abtn danger" data-co="discard">削除してチェックアウト</button>
    <button class="abtn" data-co="cancel">キャンセル</button></div>
</div></div>
<div id="graph">
  <div id="loading">
    <div class="spin"></div>
    <div>コミットグラフを作成中…</div>
    <div class="hint" id="lsub">リポジトリが大きいと時間がかかります</div>
  </div>
</div>
</div>
<div id="splitter" role="separator" aria-orientation="vertical" aria-label="右ペインの幅を調整" tabindex="0"></div>
<div id="panel">
  <div id="listView"><p class="hint">ノードをクリックすると、そのコミットから最初（ルート）までのログを表示します。エッジをクリックすると省略された区間のログを表示します。</p>
    <p class="hint">コミットをクリックすると操作対象になります。ログ行の上にマウスを置くと、比較 A / B に設定するボタンが表示されます。</p></div>
  <div id="detailView"></div>
</div>
<script>
let DATA = {nodes: {}, edges: {}};
const COLORS = {branch: "#b7e4a7", current: "#b7e4a7", remote: "#a7c8f0", tag: "#ffe9a0", head: "#f4a7a7"};
const CHUNK = 500;
const listView = document.getElementById("listView"), detailView = document.getElementById("detailView");
const views = {list: listView, detail: detailView}, savedScroll = {list: 0, detail: 0};
let curView = "list", selected = null, selectedKey = null, token = 0, observer = null, curRow = null;
let curCtx = null, curFiles = [];
let compareAHash = null, compareBHash = null;
let actionHash = null;  // マージ / リベースなどの操作対象
let currentBranch = null;
let currentBranchLoaded = false;
const nodeEl = {};  // ハッシュ -> グラフ上のノード要素
const homeHtml = listView.innerHTML;
if (!history.state || !history.state.screen) history.replaceState({screen: "home"}, "");

const splitter = document.getElementById("splitter");
let resizing = false;
splitter.addEventListener("pointerdown", ev => {
  resizing = true;
  splitter.setPointerCapture(ev.pointerId);
  document.body.classList.add("resizing");
  ev.preventDefault();
});
splitter.addEventListener("pointermove", ev => {
  if (!resizing) return;
  const width = Math.max(240, Math.min(window.innerWidth - 166, window.innerWidth - ev.clientX));
  document.documentElement.style.setProperty("--panel-width", width + "px");
});
function stopResizing() {
  resizing = false;
  document.body.classList.remove("resizing");
}
splitter.addEventListener("pointerup", stopResizing);
splitter.addEventListener("pointercancel", stopResizing);

function esc(s) {
  return String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
}
function chips(h) {
  const n = DATA.nodes[h];
  if (!n) return "";
  return n.refs.map(r => {
    const current = (r.kind === "branch" || r.kind === "current") && r.name === currentBranch;
    return '<span class="chip" style="background:' + COLORS[r.kind] + '"' +
      (current ? ' title="カレントブランチ"' : "") + '>' + (current ? "★ " : "") + esc(r.name) + "</span>";
  }).join("");
}
function detailChips(h) {
  const n = DATA.nodes[h];
  if (!n) return "";
  return n.refs.map(r => {
    const current = (r.kind === "branch" || r.kind === "current") && r.name === currentBranch;
    const deletable = DATA.writable && (r.kind === "branch" || r.kind === "current" || r.kind === "tag");
    const refKind = r.kind === "current" ? "branch" : r.kind;
    return '<span class="chip" style="background:' + COLORS[r.kind] + '"' +
      (current ? ' title="カレントブランチ"' : "") + '>' + (current ? "★ " : "") + esc(r.name) +
      (deletable ? '<button class="abtn ref-delete" data-act="delete-ref" data-kind="' + refKind +
        '" data-name="' + esc(r.name) + '" title="' + refKind + ' ' + esc(r.name) + ' を削除" aria-label="' +
        refKind + ' ' + esc(r.name) + ' を削除">×</button>' : "") + "</span>";
  }).join("");
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
  const uiState = {compareA: compareAHash, compareB: compareBHash, actionHash: actionHash, cmpActOpen: cmpActOpen};
  history.replaceState(Object.assign({}, history.state, uiState, {scroll: views[curView].scrollTop}), "");
  history.pushState(Object.assign({}, state, uiState), "");
}
function saveUiState() {
  if (!history.state) return;
  history.replaceState(Object.assign({}, history.state, {
    compareA: compareAHash, compareB: compareBHash, actionHash: actionHash, cmpActOpen: cmpActOpen
  }), "");
}
function rowHtml(c) {  // c = [hash, short, author, date, subject]
  return '<tr class="clickable" data-h="' + c[0] + '"><td class="h">' + esc(c[1]) + copyLink(c[0]) +
    '<div class="cmp-pick"><button type="button" class="sbtn" data-cmp="A" title="比較 A に設定">A</button>' +
    '<button type="button" class="sbtn" data-cmp="B" title="比較 B に設定">B</button></div></td><td>' +
    '<span class="rc">' + chips(c[0]) + "</span>" + esc(c[4]) + '<div class="meta">' + esc(c[2]) + " / " + esc(c[3]) + "</div></td></tr>";
}

// git log を CHUNK 件ずつ取得して、末尾が見えたら続きを読み込む (全件読むまで続く)
// src を渡すと /api/log の代わりに src.path (src.params を付けて) から同じ形式の一覧を取る (stash 一覧 / reflog)
function showLog(titleHtml, rev, topHtml, fromHistory, restoreScroll, src) {
  if (!fromHistory) pushScreen({screen: "log", titleHtml: titleHtml, rev: rev, topHtml: topHtml, src: src || null});
  const my = ++token;
  if (observer) { observer.disconnect(); observer = null; }
  curRow = null;
  listView.innerHTML = "<h2>" + titleHtml + "</h2>" + topHtml +
    '<div class="meta" id="count">読み込み中…</div>' +
    '<table class="log"><tbody id="rows"></tbody></table><div id="sentinel" class="hint"></div>';
  showView("list");
  listView.dataset.src = src ? src.path : "";
  const rows = listView.querySelector("#rows"), count = listView.querySelector("#count"),
        sentinel = listView.querySelector("#sentinel");
  let skip = 0, done = false, loading = false, total = null;

  async function more() {
    if (loading || done || my !== token) return;
    loading = true;
    try {
      const r = src ? await api(src.path, Object.assign({}, src.params, {skip: skip, limit: CHUNK}))
                    : await api("/api/log", {rev: rev, skip: skip, limit: CHUNK});
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
      sentinel.textContent = done ? (skip === 0 ? "(なし)" : "") : "読み込み中…";
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
function filesHtml(ctx, files, offset, withCheck) {
  offset = offset || 0;
  if (!files.length) return '<p class="hint">変更されたファイルはありません</p>';
  let add = 0, del = 0, max = 0;
  files.forEach(f => { add += f.added || 0; del += f.deleted || 0; max = Math.max(max, (f.added || 0) + (f.deleted || 0)); });
  const scale = max > 40 ? 40 / max : 1;
  const rows = files.map((f, k) => {
    const a = f.added || 0, d = f.deleted || 0;
    const pa = a ? Math.max(1, Math.round(a * scale)) : 0, pd = d ? Math.max(1, Math.round(d * scale)) : 0;
    const name = f.old_path ? f.old_path + " => " + f.path : f.path;
    return '<tr class="frow" data-k="' + (offset + k) + '">' + (withCheck ? wipCheckCell(f) : "") + '<td class="fp">' + badge(f) + (f.tag ? '<span class="hint">[' + esc(f.tag) + '] </span>' : "") + '<a href="' + esc(diffUrl(ctx, f)) +
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
  return n ? n.refs.filter(r => r.kind === "branch" || r.kind === "current").map(r => r.name) : [];
}
function showStashList(fromHistory) {
  loadStashes();
  showLog("stash 一覧", "", '<div class="meta">git stash list (新しい順)。クリックで内容 (HEAD との差分) を表示します。' +
          "操作対象に選ぶと、「操作」メニューで apply / pop / drop できます</div>",
          !!fromHistory, 0, {path: "/api/stash-list", params: {}});
}
function doStash(op) {
  if (!actionHash) return;
  const h = actionHash, sel = stashByHash[h];
  if (!sel) return;
  const label = {apply: "適用", pop: "適用して削除", drop: "削除"}[op];
  const msg = {
    apply: sel + " の変更を作業ツリーに適用します (stash は残ります)。競合したときは作業ツリーに競合が残ります。よろしいですか？",
    pop: sel + " の変更を作業ツリーに適用し、成功したら stash を削除します。競合したときは作業ツリーに競合が残り、stash も残ります。よろしいですか？",
    drop: sel + " を削除します。よろしいですか？ (削除直後なら git stash store " + h.slice(0, 7) + " で戻せます)",
  }[op];
  if (!confirm(msg)) return;
  document.querySelectorAll("#stashActions button").forEach(b => { b.disabled = true; });
  setGraphStatus("stash を処理中…");
  const reload = () => Promise.all([loadStashes(), refreshGraph().catch(() => {})]).then(() => {
    if (curView === "list" && listView.dataset.src === "/api/stash-list") showStashList(true);
  });
  post("/api/stash", {hash: h, op: op})
    .then(r => { clearCmp(); return reload().then(() => setGraphStatus(r.message)); })
    .catch(e => reload().then(() => setGraphStatus("stash の" + label + "ができませんでした: " + e.message)))
    .finally(() => updateCmpClasses());
}
function showReflog(branch) {  // branch が空なら HEAD の reflog
  const names = [];
  Object.values(DATA.nodes).forEach(n => n.refs.forEach(r => {
    if ((r.kind === "branch" || r.kind === "current") && !names.includes(r.name)) names.push(r.name);
  }));
  names.sort();
  if (branch && !names.includes(branch)) names.push(branch);
  const opts = ['<option value=""' + (branch ? "" : " selected") + ">HEAD</option>"].concat(names.map(n =>
    '<option value="' + esc(n) + '"' + (n === branch ? " selected" : "") + ">" + esc(n) + "</option>")).join("");
  const top = '<div class="meta"><label>対象: <select class="reflog-ref">' + opts + "</select></label></div>" +
    '<div class="meta">git reflog (新しい順)。クリックでそのコミットの内容を表示します</div>';
  showLog("reflog " + esc(branch || "HEAD"), "", top, false, 0,
          {path: "/api/reflog", params: branch ? {branch: branch} : {}});
}
listView.addEventListener("change", ev => {
  const sel = ev.target.closest(".reflog-ref");
  if (sel) showReflog(sel.value);
});
function showDetail(h, fromHistory) {
  if (!fromHistory) pushScreen({screen: "detail", hash: h});
  const back = '<button class="back-btn">← 履歴一覧に戻る</button>';
  detailView.innerHTML = back + '<p class="hint">読み込み中…</p>';
  showView("detail");
  api("/api/commit", {hash: h}).then(c => {
    curCtx = {hash: c.hash}; curFiles = c.files; curDetailHash = c.hash;
    const parents = c.parents.map(p => esc(p.slice(0, 7))).join(", ") || "(なし)";
    detailView.innerHTML = back + "<h2>コミット " + esc(c.hash.slice(0, 7)) + "</h2>" +
      '<span id="dChips">' + detailChips(c.hash) + "</span>" +
      '<div class="meta">' + esc(c.hash) + copyLink(c.hash) + "</div>" +
      '<div class="meta">Author: ' + esc(c.author) + "</div>" +
      '<div class="meta">Date: ' + esc(c.date) + "</div>" +
      '<div class="meta">Parent: ' + parents + "</div>" +
      "<pre>" + esc(c.message) + "</pre>" + filesHtml(curCtx, c.files) +
      '<div class="meta"><code>git show ' + esc(c.hash.slice(0, 7)) + "</code></div>";
  }).catch(e => {
    detailView.innerHTML = back + '<p class="hint">エラー: ' + esc(e.message) + "</p>";
  });
}

function post(path, body) {
  return fetch(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)})
    .then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }));
}
function doAction(btn) {  // ブランチ/タグのチップの × (削除)
  if (btn.dataset.act !== "delete-ref") return;
  const kind = btn.dataset.kind, name = btn.dataset.name;
  const url = "/api/ref-delete", body = {kind: kind, name: name};
  if (!confirm((kind === "branch" ? "ブランチ" : "タグ") + " " + name + " を削除します。よろしいですか？")) return;
  btn.disabled = true; setGraphStatus("削除中…");
  post(url, body)
    .then(r => {
      if (!r.needs_force) return r;
      if (!confirm("ブランチ " + name + " は未マージです。強制削除すると未マージのコミットがブランチから到達できなくなる可能性があります。削除しますか？")) return null;
      return post(url, Object.assign({}, body, {force: true}));
    })
    .then(r => {
      if (r === null) { setGraphStatus("強制削除をキャンセルしました"); return; }
      return refreshGraph().catch(() => {}).then(() => setGraphStatus(r.message));
    })
    .catch(e => setGraphStatus("削除できませんでした: " + e.message, "error", true))
    .finally(() => { btn.disabled = false; });
}

const graphStatusTimers = new Map();
function setGraphStatus(message, type, clearProgress) {
  const container = document.getElementById("graphStatus");
  if (clearProgress) {
    container.querySelectorAll(".graph-toast.progress").forEach(toast => {
      clearTimeout(graphStatusTimers.get(toast));
      graphStatusTimers.delete(toast);
      toast.remove();
    });
  }
  if (!message) {
    container.querySelectorAll(".graph-toast.progress").forEach(toast => {
      clearTimeout(graphStatusTimers.get(toast));
      graphStatusTimers.delete(toast);
      toast.remove();
    });
    return;
  }
  const progress = message.includes("中…") || message.startsWith("実行中…");
  const toast = [...container.children].find(el =>
    el.dataset.message === message && el.classList.contains(progress ? "progress" : (type || "info")));
  if (toast) {
    clearTimeout(graphStatusTimers.get(toast));
    graphStatusTimers.delete(toast);
  }
  const item = toast || document.createElement("div");
  item.className = "graph-toast " + (type || "info") + (progress ? " progress" : "");
  item.setAttribute("role", "status");
  item.setAttribute("aria-live", "polite");
  item.dataset.message = message;
  item.innerHTML = '<span class="graph-toast-message"></span><button type="button" class="graph-toast-close" aria-label="通知を閉じる" title="通知を閉じる">×</button>';
  item.querySelector(".graph-toast-message").textContent = message;
  item.querySelector(".graph-toast-close").addEventListener("click", () => {
    clearTimeout(graphStatusTimers.get(item));
    graphStatusTimers.delete(item);
    item.remove();
  });
  if (!toast) container.appendChild(item);
  if (!progress) {
    graphStatusTimers.set(item, setTimeout(() => {
      item.remove();
      graphStatusTimers.delete(item);
    }, 15000));
  }
}

// 操作のあと、グラフを(表示したまま)作り直して新しいブランチ/タグ/HEAD を反映する
function refreshGraph() {
  setGraphStatus("グラフ更新中…");
  return fetch("/api/graph")
    .then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }))
    .then(r => {
      const keys = new Set(Object.keys(DATA.nodes).concat(Object.keys(r.nodes)));
      const gc = document.getElementById("graph"), top0 = gc.scrollTop, left0 = gc.scrollLeft;
      DATA = {nodes: r.nodes, edges: r.edges, writable: r.writable, merge: r.merge};
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
      loadStashes();
      updateCurrentBranch().catch(e => setGraphStatus("カレントブランチを取得できませんでした: " + e.message, "error"));
      keys.forEach(h => listView.querySelectorAll('tr[data-h="' + h + '"] .rc').forEach(el => { el.innerHTML = chips(h); }));
      const c = detailView.querySelector("#dChips");
      if (c && curDetailHash) c.innerHTML = detailChips(curDetailHash);
      setGraphStatus("");
    })
    .catch(e => {
      setGraphStatus("グラフ更新に失敗しました: " + e.message, "error");
      throw e;
    });
}

// ---- コミット比較 ----
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
  document.querySelectorAll(".cmpA, .cmpB, .actionTarget").forEach(el => el.classList.remove("cmpA", "cmpB", "actionTarget"));
  if (compareAHash) {
    if (nodeEl[compareAHash]) nodeEl[compareAHash].classList.add("cmpA");
    listView.querySelectorAll('tr[data-h="' + compareAHash + '"]').forEach(tr => tr.classList.add("cmpA"));
  }
  if (compareBHash) {
    if (nodeEl[compareBHash]) nodeEl[compareBHash].classList.add("cmpB");
    listView.querySelectorAll('tr[data-h="' + compareBHash + '"]').forEach(tr => tr.classList.add("cmpB"));
  }
  if (actionHash) {
    if (nodeEl[actionHash]) nodeEl[actionHash].classList.add("actionTarget");
    listView.querySelectorAll('tr[data-h="' + actionHash + '"]').forEach(tr => tr.classList.add("actionTarget"));
  }
  document.getElementById("actionTarget").textContent = actionHash ? "対象: " + actionHash.slice(0, 7) : "";
  document.getElementById("cmpA").textContent = compareAHash ? "A: " + compareAHash.slice(0, 7) : "";
  document.getElementById("cmpB").textContent = compareBHash ? "B: " + compareBHash.slice(0, 7) : "";
  document.getElementById("cmpActToggle").setAttribute("aria-expanded", cmpActOpen ? "true" : "false");
  document.getElementById("cmpActToggle").textContent = cmpActOpen ? "操作 ▴" : "操作 ▾";
  document.getElementById("clearCmpBtn").disabled = !compareAHash && !compareBHash && !actionHash;
  document.getElementById("cmpActions").hidden = !cmpActOpen;
  document.getElementById("compareBtn").hidden = !compareAHash || !compareBHash;
  document.getElementById("compareMenuSeparator").hidden = !compareAHash || !compareBHash;
  const isStash = !!actionHash && !!stashByHash[actionHash];
  document.getElementById("aActions").hidden = !actionHash || isStash;
  document.getElementById("stashActions").hidden = !isStash;
  document.getElementById("selectionMenuSeparator").hidden = !actionHash && !compareAHash && !compareBHash;
  const canMerge = !!actionHash && !isStash && !!DATA.writable && !!DATA.merge;
  document.getElementById("mergeBtn").disabled = !canMerge;
  document.getElementById("rebaseBtn").disabled = !canMerge;
  document.getElementById("irebaseBtn").disabled = !canMerge;
  document.getElementById("cherryBtn").disabled = !canMerge;
  document.getElementById("resetBtn").disabled = !canMerge;
  const canWrite = !!DATA.writable;
  document.getElementById("filesBtn").disabled = !actionHash || isStash;
  document.querySelectorAll("#aActions button")
    .forEach(el => { if (el.id !== "filesBtn") el.disabled = !canWrite || (["mergeBtn", "rebaseBtn", "irebaseBtn", "cherryBtn", "resetBtn"].includes(el.id) && !DATA.merge); });
  document.querySelectorAll("#remoteActions button")
    .forEach(el => { el.disabled = !canWrite || (el.dataset.cact === "pull" && !DATA.merge); });
  document.querySelectorAll("#remoteConfigActions button").forEach(el => { el.disabled = !canWrite; });
  updatePushActions();
  document.querySelector('#remoteActions [data-cact="pull"]').title =
    DATA.merge ? "" : "pull には書き込み可能なワークツリーが必要です";
  document.getElementById("stashApplyBtn").disabled = !DATA.merge;  // apply / pop はワークツリーが必要
  document.getElementById("stashPopBtn").disabled = !DATA.merge;
  document.getElementById("stashDropBtn").disabled = !canWrite;
}
let cmpActOpen = false;
let stashByHash = {};  // stash のコミットのハッシュ -> stash@{n}
function loadStashes() {
  return fetch("/api/stash-list").then(r => r.ok ? r.json() : null).then(r => {
    if (!r) return;
    stashByHash = {};
    r.commits.forEach(c => { stashByHash[c[0]] = c[2]; });
    updateCmpClasses();
  }).catch(() => {});
}
function askCheckoutConflict(r) {  // チェックアウトを止めているファイルを見せて、stash / 削除 / キャンセルを選んでもらう
  return new Promise(resolve => {
    const m = document.getElementById("coModal");
    const list = fs => "<ul>" + fs.map(f => "<li>" + esc(f) + "</li>").join("") + "</ul>";
    let html = "";
    if (r.modified.length) html += "<h3>編集中のファイル</h3>" + list(r.modified);
    if (r.untracked.length) html += "<h3>チェックアウト先に同名がある管理外ファイル</h3>" + list(r.untracked);
    document.getElementById("coFiles").innerHTML = html;
    const done = v => { m.hidden = true; m.onclick = null; resolve(v); };
    m.hidden = false;
    m.onclick = ev => {
      const b = ev.target.closest("[data-co]");
      if (b) done(b.dataset.co === "cancel" ? null : b.dataset.co);
      else if (ev.target === m) done(null);
    };
  });
}
function openActionDialog(title, fields, submitLabel, opts) {  // opts: {wide, validate(values) -> エラー文 or null}
  opts = opts || {};
  return new Promise(resolve => {
    const modal = document.getElementById("actionModal"), form = document.getElementById("actionForm");
    form.className = "action-dialog" + (opts.wide ? " wide" : "");
    document.getElementById("actionError").textContent = "";
    let cleanup = null;
    const finish = value => {
      if (cleanup) cleanup();
      modal.hidden = true;
      form.onsubmit = null;
      modal.onclick = null;
      window.removeEventListener("keydown", onKeyDown);
      resolve(value);
    };
    const onKeyDown = ev => { if (ev.key === "Escape") finish(null); };
    document.getElementById("actionTitle").textContent = title;
    document.getElementById("actionFields").innerHTML = fields;
    document.getElementById("actionSubmit").textContent = submitLabel;
    modal.hidden = false;
    if (opts.onOpen) cleanup = opts.onOpen(form) || null;
    form.onsubmit = ev => {
      ev.preventDefault();
      const values = Object.fromEntries(new FormData(form).entries());
      form.querySelectorAll('input[type="checkbox"][name]').forEach(input => { values[input.name] = input.checked; });
      if (opts.validate) {
        const err = opts.validate(values);
        document.getElementById("actionError").textContent = err || "";
        if (err) return;
      }
      finish(values);
    };
    modal.onclick = ev => {
      if (ev.target === modal || ev.target.closest("[data-dialog-cancel]")) finish(null);
    };
    window.addEventListener("keydown", onKeyDown);
    const first = form.querySelector("input, select");
    if (first) first.focus();
  });
}
function setCmpMsg(text, ok) {
  setGraphStatus(text, ok ? "success" : "error");
}
function setRemoteMsg(text, ok) {
  setGraphStatus(text, ok ? "success" : "error");
}
async function doCmpAction(btn) {  // 選択対象に対するブランチ作成 / タグ作成 / チェックアウト
  if (!actionHash) return;
  const h = actionHash, act = btn.dataset.cact;
  let values, url, body, ask = null;
  if (act === "branch") {
    values = await openActionDialog("ブランチ作成", '<label>ブランチ名<input type="text" name="name" required autofocus></label>' +
      '<label class="check"><input type="checkbox" name="checkout"> 作成後にチェックアウト</label>', "作成");
    if (!values) return;
    url = "/api/branch"; body = {hash: h, name: values.name.trim(), checkout: values.checkout};
    if (!body.name) { setCmpMsg("ブランチ名を入力してください", false); return; }
    if (body.checkout) ask = "ブランチ " + body.name + " を作成してチェックアウトします (作業ツリーが切り替わります)。よろしいですか？";
  } else if (act === "tag") {
    values = await openActionDialog("タグ作成", '<label>タグ名<input type="text" name="name" required autofocus></label>' +
      '<label>メッセージ (任意)<input type="text" name="message"></label>', "作成");
    if (!values) return;
    url = "/api/tag"; body = {hash: h, name: values.name.trim(), message: values.message.trim()};
    if (!body.name) { setCmpMsg("タグ名を入力してください", false); return; }
  } else if (act === "checkout") {
    const branches = localBranches(h);
    values = await openActionDialog("チェックアウト", '<label>切り替え先<select name="branch">' +
      branches.map(branch => '<option value="' + esc(branch) + '">' + esc(branch) + '</option>').join("") +
      '<option value="">コミットを detached HEAD でチェックアウト</option>' +
      '<option value="__new_branch__">新しいブランチを作成してチェックアウト</option>' +
      '</select></label>' +
      '<label id="newBranchField" hidden>新しいブランチ名<input type="text" name="newBranch" placeholder="新規作成時のみ入力"></label>', "チェックアウト", {
        onOpen: form => {
          const select = form.elements.branch, field = form.querySelector("#newBranchField");
          const update = () => { field.hidden = select.value !== "__new_branch__"; };
          select.addEventListener("change", update);
          update();
          return () => select.removeEventListener("change", update);
        },
        validate: values => values.branch === "__new_branch__" && !values.newBranch.trim() ?
          "新しいブランチ名を入力してください" : null
      });
    if (!values) return;
    if (values.branch === "__new_branch__") {
      const name = values.newBranch.trim();
      url = "/api/branch"; body = {hash: h, name: name, checkout: true};
      ask = "ブランチ " + name + " を作成してチェックアウトします (作業ツリーが切り替わります)。よろしいですか？";
    } else if (values.branch) {
      url = "/api/checkout"; body = {branch: values.branch};
      ask = "ブランチ " + values.branch + " をチェックアウトします (作業ツリーが切り替わります)。よろしいですか？";
    } else {
      url = "/api/checkout"; body = {hash: h};
      ask = "コミット " + h.slice(0, 7) + " を detached HEAD でチェックアウトします (作業ツリーが切り替わります)。よろしいですか？";
    }
  } else return;
  if (ask && !confirm(ask)) return;
  btn.disabled = true; setCmpMsg("実行中…", true);
  const run = extra => post(url, Object.assign({}, body, extra || {}));
  run()
    .then(r => {
      if (!r.blocked) return r;
      setCmpMsg("", true);
      return askCheckoutConflict(r).then(mode => {
        if (!mode) return {cancelled: true, message: "チェックアウトをキャンセルしました"};
        setCmpMsg("実行中…", true);
        return run({on_conflict: mode});
      });
    })
    .then(r => {
      if (r.cancelled) { setCmpMsg(r.message, false); return; }
      return refreshGraph().then(() => setCmpMsg(r.message, true));
    })
    .catch(e => setCmpMsg(e.message, false))
    .finally(() => { btn.disabled = false; });
}
async function doRemoteAction(btn) {  // fetch / pull / push (別ブランチの push はブランチごとのボタン)
  const act = btn.dataset.cact;
  const url = "/api/" + act;
  let body = {}, ask = null;
  if (act === "fetch") {
    const values = await openActionDialog("fetch", '<label class="check"><input type="checkbox" name="prune"> --prune (リモートで削除された追跡ブランチも削除)</label>', "fetch");
    if (!values) return;
    body = {prune: values.prune};
  } else if (act === "pull") {
    const values = await openActionDialog("pull", '<label>取り込み方法<select name="mode">' +
      '<option value="ff-only">fast-forwardのみ (--ff-only)</option>' +
      '<option value="merge">マージ (--no-rebase)</option>' +
      '<option value="rebase">リベース (--rebase)</option></select></label>', "pull");
    if (!values) return;
    const mode = values.mode;
    body = {mode: mode};
    ask = "リモートの変更を pull します (" + {"ff-only": "fast-forwardのみ", "merge": "マージ", "rebase": "リベース"}[mode] +
          ")。作業ツリーが変わります。よろしいですか？";
  } else if (act === "push") {
    const target = btn.dataset.branch || null;
    const values = await openActionDialog("push",
      '<label class="check"><input type="checkbox" name="force"> --force-with-lease (リモート履歴を書き換える)</label>', "push");
    if (!values) return;
    body = {force: values.force};
    if (target) body.branch = target;
    const what = target ? "ブランチ " + target : "現在のブランチ";
    ask = body.force
      ? what + " を --force-with-lease で push します。リモートの履歴が書き換わります。よろしいですか？"
      : what + " を push します。よろしいですか？";
  } else return;
  if (ask && !confirm(ask)) return;
  btn.disabled = true; setRemoteMsg("実行中… (リモートとの通信に時間がかかることがあります)", true);
  let p = post(url, body);
  if (act === "push") {
    p = p.then(r => {
      if (!r.needs_upstream) return r;
      const cancelled = {cancelled: true, message: "push をキャンセルしました"};
      let remote = r.remote;
      if (r.remotes && r.remotes.length > 1) {  // リモートが複数: push 先を選ばせる
        const input = prompt("ブランチ " + r.branch + " には upstream が設定されていません。\npush 先のリモートを入力してください (" +
                             r.remotes.join(" / ") + ")。\n" + r.branch + " として push し、upstream に設定します。", r.remote);
        if (input === null) return cancelled;
        remote = input.trim();
        if (r.remotes.indexOf(remote) < 0) throw new Error("リモート " + remote + " は登録されていません");
      } else if (!confirm("ブランチ " + r.branch + " には upstream が設定されていません。" + r.remote + "/" + r.branch +
                          " として push し、upstream に設定しますか？")) return cancelled;
      setRemoteMsg("実行中… (リモートとの通信に時間がかかることがあります)", true);
      return post(url, Object.assign({}, body, {set_upstream: true, remote: remote}));
    });
  }
  p.then(r => {
    if (r.cancelled) { setRemoteMsg(r.message, false); return; }
    return refreshGraph().then(() => {
      if (r.conflict) { setGraphStatus(r.message, "error"); clearCmp(); showWip(); }
      else setRemoteMsg(r.message, true);
    });
  })
    .catch(e => setRemoteMsg(e.message, false))
    .finally(() => { btn.disabled = false; });
}
async function doRemoteConfig(btn) {  // remote 追加 / 削除
  let url, body, ask = null;
  if (btn.dataset.cact === "remote-add") {
    const values = await openActionDialog("remote 追加",
      '<label>名前<input type="text" name="name" placeholder="origin" required autofocus></label>' +
      '<label>URL<input type="text" name="url" required></label>' +
      '<label class="check"><input type="checkbox" name="fetch" checked> 追加後に fetch する</label>', "追加");
    if (!values) return;
    body = {name: values.name.trim(), url: values.url.trim(), fetch: !!values.fetch};
    if (!body.name || !body.url) { setRemoteMsg("名前と URL を入力してください", false); return; }
    url = "/api/remote-add";
  } else {
    let list;
    try { list = (await api("/api/remotes", {})).remotes; } catch (e) { setRemoteMsg(e.message, false); return; }
    if (!list.length) { setRemoteMsg("リモートが登録されていません", false); return; }
    const values = await openActionDialog("remote 削除",
      '<label>削除するリモート<select name="name">' + list.map(r =>
        '<option value="' + esc(r.name) + '">' + esc(r.name) + (r.url ? " — " + esc(r.url) : "") + "</option>").join("") +
      "</select></label>", "削除");
    if (!values) return;
    body = {name: values.name};
    url = "/api/remote-remove";
    ask = "リモート " + body.name + " を削除します。\nこのリモートのリモート追跡ブランチ (" + body.name + "/…) と設定も消えます (リモート側は変わりません)。よろしいですか？";
  }
  if (ask && !confirm(ask)) return;
  btn.disabled = true;
  setRemoteMsg(body.fetch ? "実行中… (fetch に時間がかかることがあります)" : "実行中…", true);
  post(url, body)
    .then(r => refreshGraph().then(() => setRemoteMsg(r.message, r.ok !== false && !r.fetch_failed)))
    .catch(e => setRemoteMsg(e.message, false))
    .finally(() => { btn.disabled = false; });
}
function clearCmp() {
  compareAHash = null; compareBHash = null; actionHash = null;
  updateCmpClasses();
  saveUiState();
}
function selectActionTarget(hash) { actionHash = hash; updateCmpClasses(); saveUiState(); }
function doRebase() {
  if (!actionHash) return;
  const h = actionHash, btn = document.getElementById("rebaseBtn");
  btn.disabled = true;
  fetch("/api/head").then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }))
    .then(hd => {
      if (hd.merging) throw new Error("マージまたはリベースの途中です。先に完了してください");
      if (!hd.branch) throw new Error("ブランチをチェックアウトしていません (detached HEAD ではリベースできません)");
      if (!confirm("ブランチ " + hd.branch + " を コミット " + h.slice(0, 7) + " の上にリベースします。" +
                   hd.branch + " のコミットは作り直され、履歴が書き換わります (競合したときは自動で元に戻します)。よろしいですか？")) return null;
      setGraphStatus("リベース中…");
      return post("/api/rebase", {hash: h});
    })
    .then(r => {
      if (r === null) { setGraphStatus(""); return; }
      clearCmp();
      return refreshGraph().catch(() => {}).then(() => setGraphStatus(r.message));
    })
    .catch(e => { setGraphStatus("リベースできませんでした: " + e.message, "error"); })
    .finally(() => { btn.disabled = false; });
}
function doCherryPick() {
  if (!actionHash) return;
  const h = actionHash, btn = document.getElementById("cherryBtn");
  btn.disabled = true;
  fetch("/api/head").then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }))
    .then(hd => {
      if (hd.merging) throw new Error("マージまたはリベースの途中です。先に完了してください");
      if (!confirm("コミット " + h.slice(0, 7) + " を " + (hd.branch ? "ブランチ " + hd.branch : "現在の HEAD (detached)") +
                   " にチェリーピックします。新しいコミットが作られます (競合したときは自動で元に戻します)。よろしいですか？")) return null;
      setGraphStatus("チェリーピック中…");
      return post("/api/cherry-pick", {hash: h});
    })
    .then(r => {
      if (r === null) { setGraphStatus(""); return; }
      clearCmp();
      return refreshGraph().catch(() => {}).then(() => setGraphStatus(r.message));
    })
    .catch(e => { setGraphStatus("チェリーピックできませんでした: " + e.message); })
    .finally(() => { btn.disabled = false; });
}
async function doReset() {
  if (!actionHash) return;
  const h = actionHash, btn = document.getElementById("resetBtn");
  const options = await openActionDialog("対象へリセット",
    '<label>リセット方式<select name="mode">' +
    '<option value="mixed" selected>mixed（インデックスを対象に合わせる）</option>' +
    '<option value="soft">soft（HEADだけを対象へ移動）</option>' +
    '<option value="hard">hard（作業ツリーも対象に合わせる）</option>' +
    '</select></label>', "リセット");
  if (!options) return;
  const mode = options.mode;
  const description = mode === "hard" ? "インデックスと作業ツリーの変更も破棄" :
    mode === "soft" ? "インデックスと作業ツリーの変更を残す" : "インデックスを対象に合わせる";
  if (!confirm("HEADをコミット " + h.slice(0, 7) + " へ " + mode + " リセットします。\n" +
               description + "します。よろしいですか？")) return;
  btn.disabled = true;
  setGraphStatus("リセット中…");
  try {
    const r = await post("/api/reset", {hash: h, mode: mode});
    clearCmp();
    await refreshGraph().catch(() => {});
    setGraphStatus(r.message, "success");
  } catch (e) {
    setGraphStatus("リセットできませんでした: " + e.message, "error");
  } finally {
    btn.disabled = false;
  }
}
async function doRebaseI() {  // 対話的リベース (git rebase -i 相当)
  if (!actionHash) return;
  const h = actionHash, btn = document.getElementById("irebaseBtn");
  btn.disabled = true;
  try {
    const hd = await fetch("/api/head").then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }));
    if (hd.merging) throw new Error("マージまたはリベースの途中です。先に完了してください");
    setGraphStatus("対象のコミットを読み込み中…");
    const info = await fetch("/api/rebase-todo?hash=" + encodeURIComponent(h))
      .then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }));
    setGraphStatus("");
    const msgs = {};
    const opt = (v, label) => '<option value="' + v + '">' + label + "</option>";
    const rows = info.commits.map(c => {
      msgs[c.hash] = c.message;
      return '<div class="ri-row pick" data-h="' + c.hash + '"><div class="ri-main">' +
        '<select name="act_' + c.hash + '">' + opt("pick", "pick") + opt("reword", "reword") + opt("squash", "squash") +
        opt("fixup", "fixup") + opt("drop", "drop") + "</select>" +
        "<code>" + esc(c.short) + '</code><span class="ri-subj" title="' + esc(c.subject) + '">' + esc(c.subject) + "</span>" +
        '<span class="ri-author">' + esc(c.author) + " " + esc(c.date) + "</span>" +
        '<button type="button" data-ri="up" title="上へ (先に適用)">▲</button>' +
        '<button type="button" data-ri="down" title="下へ (後で適用)">▼</button></div>' +
        '<textarea name="msg_' + c.hash + '" rows="3" hidden>' + esc(c.message) + "</textarea></div>";
    }).join("");
    const fields = '<div class="ri-note">ブランチ <b>' + esc(info.branch) + "</b> を <code>" + esc(h.slice(0, 7)) +
      "</code> の上にリベースします。上から順に適用されます (上が古いコミット)。<br>" +
      "squash / fixup は直前のコミットにまとめます。reword を選ぶとメッセージを編集できます " +
      "(reword したコミットにまとめた場合は、ここで書いたメッセージが使われます)。</div>" +
      '<div class="ri-list">' + rows + "</div>";
    const fieldsEl = document.getElementById("actionFields");
    const validate = values => {
      const acts = Object.keys(values).filter(k => k.startsWith("act_"));
      const kept = acts.filter(k => values[k] !== "drop");
      if (kept.length && ["squash", "fixup"].includes(values[kept[0]]))
        return "先頭のコミットは squash / fixup にできません (まとめ先のコミットが必要です)";
      const bad = acts.find(k => values[k] === "reword" && !(values["msg_" + k.slice(4)] || "").trim());
      if (bad) return "reword するコミットのメッセージを入力してください";
      return null;
    };
    const p = openActionDialog("対話的リベース", fields, "リベース実行", {wide: true, validate: validate});
    fieldsEl.onclick = ev => {  // 並べ替え
      const b = ev.target.closest("[data-ri]");
      if (!b) return;
      const row = b.closest(".ri-row");
      if (b.dataset.ri === "up" && row.previousElementSibling) row.parentNode.insertBefore(row, row.previousElementSibling);
      if (b.dataset.ri === "down" && row.nextElementSibling) row.parentNode.insertBefore(row.nextElementSibling, row);
    };
    fieldsEl.onchange = ev => {  // 操作の種類に合わせて表示を切り替える
      const sel = ev.target.closest("select");
      if (!sel) return;
      const row = sel.closest(".ri-row");
      row.className = "ri-row " + sel.value;
      row.querySelector("textarea").hidden = sel.value !== "reword";
    };
    const values = await p;
    fieldsEl.onclick = null; fieldsEl.onchange = null;
    if (!values) return;
    const todo = Object.keys(values).filter(k => k.startsWith("act_"))
      .map(k => ({hash: k.slice(4), action: values[k], message: values["msg_" + k.slice(4)] || ""}));
    const n = a => todo.filter(t => t.action === a).length;
    const note = [n("reword") && "メッセージ変更 " + n("reword"), n("squash") && "squash " + n("squash"),
                  n("fixup") && "fixup " + n("fixup"), n("drop") && "削除 " + n("drop")].filter(Boolean).join(" / ");
    if (!confirm("ブランチ " + info.branch + " の " + todo.length + " 件のコミットを、コミット " + h.slice(0, 7) +
                 " の上に対話的リベースします" + (note ? " (" + note + ")" : "") + "。\n" +
                 "コミットは作り直され、履歴が書き換わります (競合などで止まったときは自動で元に戻します)。よろしいですか？")) return;
    setGraphStatus("リベース中…");
    const r = await post("/api/rebase-i", {hash: h, todo: todo});
    clearCmp();
    await refreshGraph().catch(() => {});
    setGraphStatus(r.message);
  } catch (e) {
    setGraphStatus("対話的リベースできませんでした: " + e.message);
  } finally {
    btn.disabled = false;
  }
}
async function doMerge() {
  if (!actionHash) return;
  const options = await openActionDialog("マージ", '<label class="check" title="fast-forwardできる場合でもマージコミットを作る">' +
    '<input type="checkbox" name="noFf"> --no-ff</label>', "マージ");
  if (!options) return;
  const h = actionHash, noFf = options.noFf;
  const btn = document.getElementById("mergeBtn");
  btn.disabled = true;
  fetch("/api/head").then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }))
    .then(hd => {
      if (hd.merging) throw new Error("マージの途中です。先に競合を解決してコミットしてください");
      const into = hd.branch ? "ブランチ " + hd.branch : "detached HEAD";
      if (!confirm("コミット " + h.slice(0, 7) + " を " + into + " にマージします" + (noFf ? " (--no-ff)" : "") +
                   " (作業ツリーが変わります)。よろしいですか？")) return null;
      setGraphStatus("マージ中…");
      return post("/api/merge", {hash: h, no_ff: noFf});
    })
    .then(r => {
      if (r === null) { setGraphStatus(""); return; }
      clearCmp();
      return refreshGraph().catch(() => {}).then(() => {
        setGraphStatus(r.message);
        if (r.conflict) showWip();
      });
    })
    .catch(e => { setGraphStatus("マージできませんでした: " + e.message, "error"); })
    .finally(() => { btn.disabled = false; });
}
function selectCmpA(hash) {
  compareAHash = hash;
  if (compareBHash === hash) compareBHash = null;
  updateCmpClasses();
  saveUiState();
}
function selectCompareHash(slot, hash) {
  if (slot === "A") selectCmpA(hash);
  else selectCmpB(hash);
}
function selectCmpB(hash) {
  compareBHash = hash;
  if (compareAHash === hash) compareAHash = null;
  updateCmpClasses();
  saveUiState();
}
function restoreScreen(state) {
  if (Object.prototype.hasOwnProperty.call(state, "compareA")) compareAHash = state.compareA;
  if (Object.prototype.hasOwnProperty.call(state, "compareB")) compareBHash = state.compareB;
  if (Object.prototype.hasOwnProperty.call(state, "actionHash")) actionHash = state.actionHash;
  if (Object.prototype.hasOwnProperty.call(state, "cmpActOpen")) cmpActOpen = state.cmpActOpen;
  if (state.screen === "log") showLog(state.titleHtml, state.rev, state.topHtml, true, state.scroll || 0, state.src);
  else if (state.screen === "detail") showDetail(state.hash, true);
  else if (state.screen === "compare") {
    compareAHash = state.a; compareBHash = state.b;
    showCompare(state.a, state.b, true);
  }
  else if (state.screen === "wip") showWip(true);
  else {
    if (observer) { observer.disconnect(); observer = null; }
    ++token;
    listView.innerHTML = homeHtml;
    showView("list");
  }
  updateCmpClasses();
}
window.addEventListener("popstate", ev => restoreScreen(ev.state || {screen: "home"}));
window.addEventListener("message", ev => {
  if (ev.origin !== location.origin || !ev.data || ev.data.type !== "git-graph-file-log") return;
  const hash = ev.data.hash, path = ev.data.path;
  if (typeof hash !== "string" || typeof path !== "string" || !hash || !path) return;
  showLog("ファイルログ: " + esc(path), hash,
          '<div class="meta"><code>git log ' + esc(hash.slice(0, 7)) + " -- " + esc(path) + "</code></div>",
          false, 0, {path: "/api/log", params: {rev: hash, path: path}});
  if (ev.source && ev.source !== window) {
    try { ev.source.close(); } catch (e) {}
  }
});

listView.addEventListener("click", ev => {
  if (ev.detail > 1) return;
  const tr = ev.target.closest("tr.clickable");
  if (!tr) return;
  const cmpButton = ev.target.closest("[data-cmp]");
  if (cmpButton) {
    ev.preventDefault();
    ev.stopPropagation();
    selectCompareHash(cmpButton.dataset.cmp, tr.dataset.h);
    return;
  }
  if (ev.target.closest(".copy")) return;
  selectActionTarget(tr.dataset.h);
  if (curRow) curRow.classList.remove("cur");
  curRow = tr; tr.classList.add("cur");
  showDetail(tr.dataset.h);
});
detailView.addEventListener("click", ev => {
  if (ev.target.closest("#wipReload")) { showWip(true); refreshGraph().catch(() => {}); return; }
  if (ev.target.closest("#wipCommit")) { doWipCommit(); return; }
  const ab = ev.target.closest(".abtn");
  if (ab) { doAction(ab); return; }
  if (ev.target.closest(".sbtn")) {
    [compareAHash, compareBHash] = [compareBHash, compareAHash];
    updateCmpClasses();
    saveUiState();
    if (compareAHash && compareBHash) showCompare(compareAHash, compareBHash);
    return;
  }
  if (ev.target.closest(".back-btn")) { history.back(); return; }
  const tr = ev.target.closest("tr.frow");
  if (tr && !ev.target.closest("a, input, td.fk") && curCtx)  // 行のどこをクリックしても差分を開く
    location.href = diffUrl(curCtx, curFiles[+tr.dataset.k]);
});
document.getElementById("cmpActions").addEventListener("click", ev => {
  if (ev.target.closest("#compareBtn") && compareAHash && compareBHash) {
    showCompare(compareAHash, compareBHash);
    return;
  }
  if (ev.target.closest("#filesBtn") && actionHash && !stashByHash[actionHash]) {
    window.open("/files?hash=" + encodeURIComponent(actionHash), "_blank");
    return;
  }
  if (ev.target.closest("#stashListBtn")) { showStashList(); return; }
  if (ev.target.closest("#stashApplyBtn")) { doStash("apply"); return; }
  if (ev.target.closest("#stashPopBtn")) { doStash("pop"); return; }
  if (ev.target.closest("#stashDropBtn")) { doStash("drop"); return; }
  if (ev.target.closest("#reflogBtn")) { showReflog(""); return; }
  if (ev.target.closest("#mergeBtn")) { doMerge(); return; }
  if (ev.target.closest("#rebaseBtn")) { doRebase(); return; }
  if (ev.target.closest("#irebaseBtn")) { doRebaseI(); return; }
  if (ev.target.closest("#cherryBtn")) { doCherryPick(); return; }
  if (ev.target.closest("#resetBtn")) { doReset(); return; }
  const cab = ev.target.closest(".abtn[data-cact]");
  if (cab) {
    const act = cab.dataset.cact;
    (["fetch", "pull", "push"].includes(act) ? doRemoteAction : act.startsWith("remote-") ? doRemoteConfig : doCmpAction)(cab);
    return;
  }
});
document.getElementById("compareToolbar").addEventListener("click", ev => {
  if (ev.target.closest("#cmpActToggle")) {
    cmpActOpen = !cmpActOpen;
    updateCmpClasses();
    saveUiState();
  } else if (ev.target.closest("#clearCmpBtn")) {
    clearCmp();
  }
});
function select(g) {
  if (selected) selected.classList.remove("sel");
  selected = g; g.classList.add("sel");
  const t = g.querySelector("title");
  selectedKey = t ? t.textContent : null;  // グラフを作り直したあとで選択を復元するため
}

function onNodeClick(g, n) {
  if (n.wip) {  // 作業ツリーの変更 (WIP) ノード: ログではなく変更ファイルの一覧を出す
    clearCmp(); select(g); showWip();
    return;
  }
  selectActionTarget(n.hash);
  select(g);
  showLog("git log --oneline " + esc(n.hash.slice(0, 7)), n.hash,
          chips(n.hash) + '<div class="meta">最初のコミットまでの全ログ</div>');
}

function init() {
  document.querySelectorAll("svg g.node").forEach(g => {
    const t = g.querySelector("title");
    const n = t && DATA.nodes[t.textContent];
    if (n) {
      nodeEl[n.hash] = g;
      const box = g.getBBox(), hit = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      hit.setAttribute("x", box.x); hit.setAttribute("y", box.y);
      hit.setAttribute("width", box.width); hit.setAttribute("height", box.height);
      hit.setAttribute("fill", "transparent"); hit.setAttribute("pointer-events", "all");
      g.insertBefore(hit, g.children[1] || null);
      g.addEventListener("click", ev => { if (ev.detail <= 1) onNodeClick(g, n); });
    }
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
// チェックしたファイルだけを add してコミットする (TortoiseGit のコミットダイアログと同じ考え方)。
// 既定ではステージ済みのファイルがチェック済み。チェックを外したステージ済みファイルはステージから外れる。
let wipSel = {}, wipStaged = new Set(), wipMerging = false, wipMsg = "";  // wipSel: パス → ユーザーが付け外ししたチェック状態
// amend モード: チェック中は「直前のコミットの親」と作業ツリーの差分を出し、メッセージも直前のものを入れる
let wipAmendOn = false, wipMsgNormal = "", wipHeadMsg = "";  // wipMsgNormal: amend にする前に書いていたメッセージ (解除で戻す)
function wipIsChecked(f) {  // amend: 直前のコミットのファイルは、外さない限りコミットに残る (チェック済みが既定)
  return f.path in wipSel ? wipSel[f.path] : (wipMerging || !!f.in_head || wipStaged.has(f.path));
}
function wipCheckCell(f) {
  return '<td class="fk"><input type="checkbox" class="wipck" data-kind="' + esc(f.wip) + '" data-path="' + esc(f.path) + '"' +
    (wipIsChecked(f) ? " checked" : "") + (f.in_head ? ' title="外すと、このファイルは直前のコミットから外れます (作業ツリーの内容は残ります)"' : "") + "></td>";
}
function wipCks(kind) {  // 操作できる (disabled でない) チェックボックス
  return [...detailView.querySelectorAll(".wipck" + (kind ? '[data-kind="' + kind + '"]' : ""))].filter(x => !x.disabled);
}
function wipPaths() {  // チェックされたパス (同じファイルが ステージ済み と 未ステージ の両方に出ても1件)
  const s = new Set();
  wipCks().forEach(el => { if (el.checked) s.add(el.dataset.path); });
  return [...s];
}
function setWipPath(p, v) {  // 同じパスのチェックボックスをまとめて切り替える
  wipSel[p] = v;
  wipCks().forEach(x => { if (x.dataset.path === p) x.checked = v; });
}
function wipSync() {  // コミットボタンの件数と、グループの全選択チェックを更新
  const n = wipPaths().length, b = detailView.querySelector("#wipCommit");
  if (b) {
    b.disabled = n === 0;
    b.textContent = "チェックした " + n + " 件を add して" + (wipAmendOn ? " amend" : "コミット");
  }
  detailView.querySelectorAll(".wipall").forEach(all => {
    const cks = wipCks(all.dataset.kind), c = cks.filter(x => x.checked).length;
    all.disabled = cks.length === 0;
    all.checked = cks.length > 0 && c === cks.length;
    all.indeterminate = c > 0 && c < cks.length;
  });
}
function wipSay(text, ok) {
  const el = detailView.querySelector("#wipMsgOut");
  if (el) { el.textContent = text; el.className = "amsg " + (ok ? "ok" : "ng"); }
}
function setWipAmend(on) {  // amend のチェックを切り替える: 差分の一覧とメッセージを入れ替えて再表示
  if (on === wipAmendOn) return;
  wipSel = {};  // 一覧が変わるので、付け外ししたチェックはリセット
  if (on) { wipMsgNormal = wipMsg; wipMsg = wipHeadMsg; }  // 直前のコミットのメッセージを入れておく
  else { wipMsg = wipMsgNormal; wipMsgNormal = ""; }
  wipAmendOn = on;
  showWip(true);
}
function doWipCommit() {
  const amend = wipAmendOn, paths = wipPaths(), ta = detailView.querySelector("#wipMsg"), msg = ta ? ta.value : "";
  if (!msg.trim()) { wipSay("コミットメッセージを入力してください", false); return; }
  if (!amend && !paths.length) { wipSay("コミットするファイルにチェックを付けてください", false); return; }
  if (amend) {
    const chk = new Set(paths), drops = curFiles.filter(f => f.in_head && !chk.has(f.path)).length;
    if (!confirm("直前のコミットを作り直します (メッセージも置き換えます)。" +
        (drops ? "\n直前のコミットから " + drops + " 件のファイルを外します (作業ツリーの内容は残ります)。" : "") +
        "\n履歴が書き換わります。続けますか？")) return;
  }
  const btn = detailView.querySelector("#wipCommit");
  if (btn) btn.disabled = true;
  wipSay("実行中…", true);
  post("/api/wip-commit", {paths: paths, message: msg, amend: amend})
    .then(r => {
      wipMsg = ""; wipMsgNormal = ""; wipSel = {}; wipAmendOn = false;
      showWip(true, r.message);
      refreshGraph().catch(() => {});
    })
    .catch(e => { wipSay(e.message, false); wipSync(); });
}
detailView.addEventListener("change", ev => {
  if (ev.target.id === "wipAmendCk") { setWipAmend(ev.target.checked); return; }
  const ck = ev.target.closest(".wipck"), all = ev.target.closest(".wipall");
  if (ck) setWipPath(ck.dataset.path, ck.checked);
  else if (all) wipCks(all.dataset.kind).forEach(x => setWipPath(x.dataset.path, all.checked));
  else return;
  wipSync();
});
detailView.addEventListener("input", ev => { if (ev.target.id === "wipMsg") wipMsg = ev.target.value; });

function showWip(fromHistory, okMsg) {
  if (!fromHistory) pushScreen({screen: "wip"});
  const btns = '<button class="back-btn">← 履歴一覧に戻る</button><button class="sbtn" id="wipReload">↻ 更新</button>';
  detailView.innerHTML = btns + '<p class="hint">読み込み中…</p>';
  showView("detail"); curDetailHash = null;
  api("/api/status", wipAmendOn ? {amend: 1} : {}).then(s => {
    wipStaged = new Set(s.staged.map(f => f.path)); wipMerging = !!s.merging; wipHeadMsg = s.head_message || "";
    const canAmend = !!(s.head && !s.merging && !s.rebasing);
    if (wipAmendOn && !s.amend_files) {  // マージ中になったなどで amend できなくなった: 通常のコミットに戻す
      wipMsg = wipMsgNormal; wipMsgNormal = ""; wipAmendOn = false;
    }
    const canCommit = !!DATA.writable;  // 書き込みが無効 (--read-only など) のときはチェック欄を出さない
    const sections = wipAmendOn
      ? [["amend", "amend 後のコミットの内容 (直前のコミットの親との差分)", s.amend_files]]
      : [["conflict", "競合", s.conflicts], ["staged", "ステージ済み (git add 済み)", s.staged],
         ["unstaged", "未ステージの変更", s.unstaged], ["untracked", "未追跡のファイル", s.untracked]];
    let files = [], body = "";
    sections.forEach(([kind, title, items]) => {
      if (!items.length) return;
      const list = items.map(f => Object.assign({wip: kind}, f));
      body += "<h3>" + (canCommit ? '<input type="checkbox" class="wipall" data-kind="' + kind + '" title="このグループを全選択/解除">' : "") +
        title + " (" + list.length + ")</h3>" + filesHtml({}, list, files.length, canCommit);
      files = files.concat(list);
    });
    curCtx = {}; curFiles = files;
    const hint = wipAmendOn
      ? "直前のコミットのファイルはチェック済みで、外すとコミットから外れます (作業ツリーはそのまま)。[WIP] は未コミットの変更で、チェックすると取り込みます"
      : "チェックを外したステージ済みファイルはステージから外れます (作業ツリーはそのまま)";
    const box = canCommit && (files.length || wipAmendOn)
      ? '<div class="wipbox">' +
        '<textarea id="wipMsg" placeholder="コミットメッセージ"></textarea>' +
        (canAmend ? '<label class="amendrow"><input type="checkbox" id="wipAmendCk"' + (wipAmendOn ? " checked" : "") +
          '>直前のコミットを amend する (履歴が書き換わります)</label>' : "") +
        '<div class="arow"><button class="wbtn" id="wipCommit">コミット</button>' +
        '<span class="hint">' + hint + '</span></div>' +
        '<div class="amsg" id="wipMsgOut"></div></div>' : "";
    detailView.innerHTML = btns + "<h2>作業ツリーの変更 (WIP)</h2>" +
      '<div class="meta">HEAD: ' + (s.head ? chips(s.head) + esc(s.head.slice(0, 7)) : "(コミットなし)") + "</div>" +
      (body || '<p class="hint">変更はありません</p>') + box +
      (s.truncated ? '<p class="hint">ファイルが多いため先頭のみ表示しています</p>' : "");
    const ta = detailView.querySelector("#wipMsg");
    if (ta) ta.value = wipMsg;
    if (okMsg) wipSay(okMsg, true);
    wipSync();
  }).catch(e => {
    detailView.innerHTML = btns + '<p class="hint">エラー: ' + esc(e.message) + "</p>";
  });
}

document.getElementById("reload").addEventListener("click", ev => {  // グラフを最新の状態に更新
  const b = ev.currentTarget;
  b.disabled = true; b.textContent = "更新中…";
  refreshGraph().catch(() => {}).finally(() => { b.disabled = false; b.textContent = "↻ 更新"; });
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
    DATA = {nodes: r.nodes, edges: r.edges, writable: r.writable, merge: r.merge};
    document.getElementById("graph").innerHTML = r.svg;
    init();
    updateCmpClasses();
    document.getElementById("reload").disabled = false;
    loadStashes();
    updateCurrentBranch().catch(e => setGraphStatus("カレントブランチを取得できませんでした: " + e.message, "error"));
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
    s["merging"] = is_merging()
    s["rebasing"] = is_rebasing()
    if s["head"]:
        s["head_message"] = commit_info(s["head"])["message"]  # amend のときにメッセージの初期値にする
        if q.get("amend", [""])[0] == "1" and not s["merging"] and not s["rebasing"]:
            s["amend_files"] = amend_files(s)
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
    if kind == "amend":  # 直前のコミットの親 と 作業ツリー の差分 (未追跡ファイルは新規ファイルとして)
        if path in {f["path"] for f in parse_status(read_status_raw())["untracked"]}:
            return wip_diff("untracked", path, specs)
        return git("diff", "--no-color", "-M", amend_base(), "--", *specs, literal=True)
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


def api_reflog(q):
    """reflog (新しい順)。branch 省略で HEAD、指定でそのローカルブランチ。"""
    branch = q.get("branch", [""])[0]
    if branch:
        check_ref_name("branch", branch)
        ref, label = "refs/heads/" + branch, branch
        if subprocess.run(["git", "-C", REPO, "show-ref", "--verify", "--quiet", ref]).returncode != 0:
            raise ValueError("ブランチが見つかりません")
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
        raise ValueError("stash が見つかりません (一覧を開き直してください)")
    if op == "drop":
        run_git_write("stash", "drop", sel)
        return {"ok": True, "message": f"{sel} を削除しました (戻すときは git stash store {h[:7]})"}
    if not HAS_WORKTREE:
        raise ValueError("ワークツリーのないリポジトリでは stash を適用できません")
    if is_merging() or is_rebasing() or is_cherry_picking():
        raise ValueError("マージ・リベース・チェリーピックの途中です。先に完了するか中止してください")
    r = subprocess.run(["git", "-C", REPO, "stash", op, sel], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        unmerged = subprocess.run(["git", "-C", REPO, "diff", "--name-only", "--diff-filter=U"],
                                  capture_output=True, text=True).stdout.strip()
        if unmerged:
            raise ValueError(f"{sel} の適用で競合しました。作業ツリーに競合が残っています (WIP で確認できます)"
                             + ("。stash は残してあります" if op == "pop" else "") + "\n" + unmerged)
        raise ValueError((r.stderr or r.stdout).strip() or "stash の適用に失敗しました")
    return {"ok": True, "message": f"{sel} を" + ("適用しました" if op == "apply" else "適用して削除しました")}


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
        raise ValueError("ディレクトリが見つかりません")
    entries = []
    for item in raw.split("\0"):
        if not item:
            continue
        head, name = item.split("\t", 1)
        mode, kind, obj, size = (head.split() + [""])[:4]
        entries.append({"name": name, "type": kind, "size": None if size == "-" else int(size), "mode": mode})
    return {"hash": h, "path": path, "entries": sorted(entries, key=lambda x: (x["type"] != "tree", x["name"]))}


def api_file(q):
    h = resolve_commit(q.get("hash", [""])[0])
    path = validate_file_path(q.get("path", [""])[0], allow_empty=False)
    spec = h + ":" + path
    kind = git("cat-file", "-t", spec).strip()
    if kind != "blob":
        raise ValueError("ファイルではありません")
    raw = subprocess.check_output(["git", "-C", REPO, "cat-file", "blob", spec])
    truncated = len(raw) > FILE_CONTENT_LIMIT
    data = raw[:FILE_CONTENT_LIMIT]
    return {"hash": h, "path": path, "content": data.decode("utf-8", errors="replace"),
            "binary": b"\0" in data, "truncated": truncated, "size": len(raw)}


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


WIP_TITLES = {"amend": "amend 後のコミットの内容 (直前のコミットの親との差分)", "conflict": "競合", "staged": "ステージ済み", "unstaged": "未ステージの変更", "untracked": "未追跡のファイル"}


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


def wip_files(kind=""):
    """WIP の全ファイルを 競合 → ステージ済み → 未ステージ → 未追跡 の順に、種別(wip)つきで返す。
    kind が amend のときは、amend 後のコミットの内容 (直前のコミットの親との差分) を返す。"""
    if kind == "amend":
        s = api_status({"amend": ["1"]})
        if "amend_files" not in s:
            raise ValueError("amend できる状態ではありません (コミットがない、またはマージ・リベース中です)")
        return s["amend_files"]
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
        files, ctx, ref = wip_files(wip), {}, "WIP"
        info = f'<div class="meta">作業ツリーの変更 (WIP) — {html.escape(WIP_TITLES.get(wip, wip))}</div>'
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
  table.file-log { border-collapse: collapse; width: 100%; margin-top: 8px; }
  table.file-log td { border-bottom: 1px solid #e3e3e3; padding: 5px 6px; vertical-align: top; }
  table.file-log td.hash { width: 1%; font-family: monospace; white-space: nowrap; }
  table.file-log td.date { width: 1%; color: #777; white-space: nowrap; }
  table.file-log a { color: inherit; text-decoration: none; }
  table.file-log tr:hover td { background: #eef; }
"""


FILES_PAGE = r"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8"><title>ファイル表示</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css">
<style>__CSS__</style><script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script></head>
<body><aside id="tree"><h1>ファイル表示</h1><div class="meta" id="commit"></div><div id="list"></div></aside>
<main id="main"><h2 id="title">ファイルを選択してください</h2>
<div id="fileActions" class="file-actions" hidden><button id="fileLogBtn">このファイルのログ</button><button id="fileContentBtn" hidden>ファイル内容</button></div>
<div id="content" class="hint">左の一覧からファイルをクリックしてください。</div></main>
<script>
const qs = new URLSearchParams(location.search), hash = qs.get("hash");
const list = document.getElementById("list"), content = document.getElementById("content"), title = document.getElementById("title");
const fileActions = document.getElementById("fileActions"), fileLogBtn = document.getElementById("fileLogBtn"), fileContentBtn = document.getElementById("fileContentBtn");
let current = "", currentFile = "";
function esc(s) { return String(s).replace(/[&<>\"]/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", "\"":"&quot;"}[c])); }
function api(path, params) { return fetch(path + "?" + new URLSearchParams(params)).then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); })); }
function showError(e) { list.innerHTML = '<p class="hint">エラー: ' + esc(e.message) + '</p>'; }
function loadTree(path, push) {
  current = path;
  fileActions.hidden = true;
  if (push) history.pushState({path: path}, "", "?hash=" + encodeURIComponent(hash) + (path ? "&path=" + encodeURIComponent(path) : ""));
  api("/api/tree", {hash: hash, path: path}).then(r => {
    document.getElementById("commit").textContent = r.hash.slice(0, 7);
    const parts = path ? path.split("/") : [];
    let crumb = '<div class="crumb"><a data-dir="">📁 ルート</a>';
    parts.forEach((p, i) => { const d = parts.slice(0, i + 1).join("/"); crumb += ' / <a data-dir="' + esc(d) + '">' + esc(p) + '</a>'; });
    crumb += '</div>';
    list.innerHTML = crumb + (r.entries.length ? r.entries.map(e => {
      const p = path ? path + "/" + e.name : e.name;
      const icon = e.type === "tree" ? "📁" : "📄";
      const size = e.size === null ? "" : e.size.toLocaleString() + " B";
      return '<div class="entry" data-type="' + e.type + '" data-path="' + esc(p) + '"><span>' + icon + ' ' + esc(e.name) + '</span><span class="size">' + size + '</span></div>';
    }).join("") : '<p class="hint">空のディレクトリです</p>');
    list.querySelectorAll("[data-dir]").forEach(a => a.onclick = () => loadTree(a.dataset.dir, true));
    list.querySelectorAll(".entry").forEach(a => a.onclick = () => a.dataset.type === "tree" ? loadTree(a.dataset.path, true) : loadFile(a.dataset.path));
  }).catch(showError);
}
function loadFile(path) {
  currentFile = path;
  fileActions.hidden = false;
  fileLogBtn.hidden = false;
  fileContentBtn.hidden = true;
  api("/api/file", {hash: hash, path: path}).then(r => {
    const lang = languageFor(r.path);
    title.textContent = r.path + (lang ? " (" + lang + ")" : "");
    content.className = r.binary ? "binary" : "";
    content.innerHTML = r.binary ? "バイナリファイルのため内容を表示できません。" : '<pre class="code"><code>' + highlightCode(r.content, lang) + '</code></pre>' + (r.truncated ? '<p class="hint">大きいため先頭のみ表示しています。</p>' : '');
  }).catch(e => { title.textContent = path; content.className = "hint"; content.textContent = "エラー: " + e.message; });
}
function loadFileLog(path) {
  if (window.opener && !window.opener.closed) {
    window.opener.postMessage({type: "git-graph-file-log", hash: hash, path: path}, location.origin);
    window.close();
    return;
  }
  fileLogBtn.hidden = true;
  fileContentBtn.hidden = false;
  title.textContent = path + " の変更履歴";
  content.className = "hint";
  content.textContent = "読み込み中…";
  api("/api/log", {rev: hash, path: path}).then(r => {
    const rows = r.commits.map(c => '<tr><td class="hash">' + esc(c[1]) +
      '</td><td class="date">' + esc(c[3]) + '</td><td>' + esc(c[4]) + '</td></tr>').join("");
    content.className = "";
    content.innerHTML = '<div class="meta">' + r.total + ' 件</div>' +
      (rows ? '<table class="file-log"><tbody>' + rows + '</tbody></table>' : '<p class="hint">履歴がありません</p>');
  }).catch(e => { content.className = "hint"; content.textContent = "エラー: " + e.message; });
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
window.onpopstate = () => loadTree(new URLSearchParams(location.search).get("path") || "", false);
if (!hash) showError(new Error("コミットが指定されていません")); else loadTree(qs.get("path") || "", false);
</script></body></html>""".replace("__CSS__", FILES_CSS)


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


def run_git_write(*args, literal=False, cwd=None):
    """git を実行し、失敗したら git のエラーメッセージを ValueError にして返す。"""
    env = dict(os.environ, GIT_LITERAL_PATHSPECS="1") if literal else None  # パスをglobとして解釈しない
    r = subprocess.run(["git", "-C", cwd or REPO, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    if r.returncode != 0:
        raise ValueError((r.stderr or r.stdout).strip() or "git コマンドが失敗しました")
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
                           capture_output=True, text=True)
        return r.stdout.strip()
    before = top()
    run_git_write("stash", "push", "--include-untracked", "-m",
                  "git_graph: チェックアウト前の退避 " + time.strftime("%Y-%m-%d %H:%M:%S"))
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
            raise ValueError(f"リポジトリ外のパスは削除できません: {path}")
        if os.path.isdir(full) and not os.path.islink(full):
            raise ValueError(f"ディレクトリは自動削除できません: {path}")
        if os.path.lexists(full):
            os.remove(full)


def checkout_resolving(args, mode):
    """git checkout <args> を実行する。成功なら None を返す。
    編集中のファイル/同名の管理外ファイルで止まったとき、mode が None なら {blocked, modified, untracked} を返し、
    "stash" なら全変更を退避してから、"discard" なら止めているファイルだけ破棄してから再実行する。"""
    if mode not in (None, "stash", "discard"):
        raise ValueError("on_conflict が不正です")
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
            raise ValueError(text or "チェックアウトに失敗しました")
        if mode is None:
            return {"blocked": True, **blockers}
        if mode == "stash":
            raise ValueError("変更を stash に退避しましたが、まだチェックアウトできません:\n" + text)
        discard_blockers(blockers)
    raise ValueError("チェックアウトに失敗しました")


def with_stash_note(res, msg):
    if res and res.get("stashed"):
        msg += f" (それまでの変更は {res['stashed']} に退避しました。戻すときは git stash pop)"
    return msg


def api_branch(body):
    h = resolve_commit(body.get("hash", ""))
    name = str(body.get("name", "")).strip()
    check_ref_name("branch", name)
    if body.get("checkout"):
        res = checkout_resolving(["-b", name, h], body.get("on_conflict"))
        if res and res.get("blocked"):
            return res
        return {"ok": True, "message": with_stash_note(res, f"ブランチ {name} を作成してチェックアウトしました")}
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


def api_delete_ref(body):
    kind = body.get("kind")
    if kind not in ("branch", "tag"):
        raise ValueError("削除できるのはローカルブランチまたはタグだけです")
    name = str(body.get("name", "")).strip()
    check_ref_name(kind, name)
    ref = ("refs/heads/" if kind == "branch" else "refs/tags/") + name
    exists = subprocess.run(["git", "-C", REPO, "show-ref", "--verify", "--quiet", ref])
    if exists.returncode != 0:
        raise ValueError(f"{name!r} は存在しません")
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
                raise ValueError((r.stderr or r.stdout).strip() or "ブランチを削除できません")
        return {"ok": True, "message": f"ブランチ {name} を削除しました"}
    run_git_write("tag", "-d", "--", name)
    return {"ok": True, "message": f"タグ {name} を削除しました"}


def api_checkout(body):
    branch = body.get("branch")
    if branch:
        branch = str(branch)
        check_ref_name("branch", branch)
        res = checkout_resolving([branch, "--"], body.get("on_conflict"))
        if res and res.get("blocked"):
            return res
        return {"ok": True, "message": with_stash_note(res, f"ブランチ {branch} をチェックアウトしました")}
    h = resolve_commit(body.get("hash", ""))
    res = checkout_resolving(["--detach", h], body.get("on_conflict"))
    if res and res.get("blocked"):
        return res
    return {"ok": True, "message": with_stash_note(res, f"{h[:7]} をチェックアウトしました (detached HEAD)")}


def api_reset(body):
    """現在の HEAD を選択したコミットへ移動する。"""
    mode = body.get("mode", "mixed")
    if mode not in ("soft", "mixed", "hard"):
        raise ValueError("リセット方式が不正です")
    if is_merging() or is_rebasing() or is_cherry_picking():
        raise ValueError("マージ、リベース、またはチェリーピックの途中はリセットできません")
    h = resolve_commit(body.get("hash", ""))
    before = git("rev-parse", "HEAD").strip() if has_head() else None
    run_git_write("reset", "--" + mode, h)
    if before == h:
        return {"ok": True, "message": f"HEAD は既に {h[:7]} です (変更なし)"}
    return {"ok": True, "message": f"HEAD を {h[:7]} へ {mode} リセットしました"}


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
        raise ValueError("作業ツリーの変更の表示は無効です")
    amend = body.get("amend") is True
    if amend:
        if not has_head():
            raise ValueError("amend するコミットがありません")
        if is_merging() or is_rebasing():
            raise ValueError("マージまたはリベース中は amend できません")
    msg = str(body.get("message", "")).strip()
    if not msg:
        raise ValueError("コミットメッセージを入力してください")
    paths = body.get("paths")
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths) or (not paths and not amend):
        raise ValueError("コミットするファイルを選んでください")  # amend はメッセージだけの変更 (paths が空) もできる
    checked = set(paths)
    s = parse_status(read_status_raw(), limit=10 ** 9)
    known = {f["path"] for k in ("staged", "unstaged", "untracked", "conflicts") for f in s[k]}
    head_changes = amend_head_changes(amend_base()) if amend else {}  # 直前のコミットが変更したファイル (チェックを外すとコミットから外す)
    if amend and head_changes and not checked:  # index を触る前に確認する (空のコミットは git が拒否する)
        raise ValueError("直前のコミットのファイルをすべて外すと、コミットが空になります (コミットごと取り消すなら git reset HEAD^)")
    bad = checked - known - set(head_changes)  # 任意のパスを add させないよう、いま変更ありと報告されているものだけを対象にする
    if bad:
        raise ValueError("変更のないファイルが含まれています (更新してやり直してください): " + ", ".join(sorted(bad)[:3]))
    unstage = []
    for f in s["staged"]:
        if f["path"] not in checked:
            unstage.append(f["path"])
            if f.get("old_path"):  # リネームは元のパスも一緒に戻す
                unstage.append(f["old_path"])
    if is_merging() and (unstage or any(f["path"] not in checked for f in s["conflicts"])):
        raise ValueError("マージ中は、ステージ済み・競合のファイルをすべて含めてコミットする必要があります")
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
    todo = sorted(checked)
    for i in range(0, len(todo), step):
        run_git_write("add", "-A", "--", *todo[i:i + step], literal=True)
    run_git_write("commit", *(["--amend"] if amend else []), "-m", msg)
    h = git("rev-parse", "HEAD").strip()
    verb = "amend" if amend else "コミット"
    tail = f" ({len(checked)} 件)" if checked else " (メッセージのみ)"
    if drop:
        tail += f"、{len([d for d in head_changes if d not in checked])} 件をコミットから外しました"
    return {"ok": True, "message": f"{h[:7]} を {verb} しました{tail}"}


def is_rebasing():
    for d in ("rebase-merge", "rebase-apply"):
        r = subprocess.run(["git", "-C", REPO, "rev-parse", "--git-path", d],
                           capture_output=True, text=True)
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
        raise ValueError("ワークツリーのないリポジトリではマージできません")
    h = resolve_commit(body.get("hash", ""))
    if is_merging():
        raise ValueError("マージの途中です。先に競合を解決してコミットするか、git merge --abort してください")
    if not has_head():
        raise ValueError("最初のコミットがないためマージできません")
    args = ["merge", "--no-edit"]
    if body.get("no_ff"):
        args.append("--no-ff")
    r = subprocess.run(["git", "-C", REPO, *args, h], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        if is_merging():  # 競合 (自動マージできなかった)
            return {"ok": True, "conflict": True,
                    "message": f"{h[:7]} のマージで競合しました。WIP の競合ファイルを解決してコミットしてください"}
        raise ValueError(out or "マージに失敗しました")
    if "Already up to date" in out:
        return {"ok": True, "message": f"{h[:7]} は既にマージ済みです (変更なし)"}
    return {"ok": True, "message": f"{h[:7]} をマージしました"}


def api_rebase(body):
    """現在のブランチを、選んだコミットの上にリベースする。競合したら自動で中止して元に戻す。"""
    if not HAS_WORKTREE:
        raise ValueError("ワークツリーのないリポジトリではリベースできません")
    h = resolve_commit(body.get("hash", ""))
    if is_merging() or is_rebasing():
        raise ValueError("マージまたはリベースの途中です。先に完了するか中止してください")
    head = api_head()
    if not head["branch"]:
        raise ValueError("ブランチをチェックアウトしていません (detached HEAD ではリベースできません)")
    before = git("rev-parse", "HEAD").strip()
    r = subprocess.run(["git", "-C", REPO, "rebase", h], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        if is_rebasing():  # 競合 → 元に戻す
            subprocess.run(["git", "-C", REPO, "rebase", "--abort"], capture_output=True)
            raise ValueError(f"{h[:7]} の上へのリベースで競合したため中止し、元に戻しました。"
                             "競合を解決したい場合はコマンドラインで git rebase を実行してください")
        raise ValueError(out or "リベースに失敗しました")
    if git("rev-parse", "HEAD").strip() == before:
        return {"ok": True, "message": f"{head['branch']} は既に {h[:7]} の上にあります (変更なし)"}
    return {"ok": True, "message": f"{head['branch']} を {h[:7]} の上にリベースしました"}


def is_cherry_picking():
    return subprocess.run(["git", "-C", REPO, "rev-parse", "-q", "--verify", "CHERRY_PICK_HEAD"],
                          capture_output=True).returncode == 0


def api_cherry_pick(body):
    """選んだコミットを、いまチェックアウトしている HEAD にチェリーピックする。
    競合したときと、変更が空になるとき (既に取り込み済み) は、中止して元に戻す。"""
    if not HAS_WORKTREE:
        raise ValueError("ワークツリーのないリポジトリではチェリーピックできません")
    h = resolve_commit(body.get("hash", ""))
    if is_merging() or is_rebasing() or is_cherry_picking():
        raise ValueError("マージ・リベース・チェリーピックの途中です。先に完了するか中止してください")
    if not has_head():
        raise ValueError("最初のコミットがないためチェリーピックできません")
    if len(git("rev-list", "--parents", "-n", "1", h).split()) > 2:
        raise ValueError("マージコミットはチェリーピックできません (どちらの親側の変更を取るか決められないため)")
    before = git("rev-parse", "HEAD").strip()
    r = subprocess.run(["git", "-C", REPO, "cherry-pick", h], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        if is_cherry_picking():
            unmerged = subprocess.run(["git", "-C", REPO, "diff", "--name-only", "--diff-filter=U"],
                                      capture_output=True, text=True).stdout.strip()
            subprocess.run(["git", "-C", REPO, "cherry-pick", "--abort"], capture_output=True)
            if not unmerged:  # 競合ではなく、変更が空になった
                raise ValueError(f"{h[:7]} の変更は既に HEAD に含まれているため、チェリーピックするものがありません")
            raise ValueError(f"{h[:7]} のチェリーピックで競合したため中止し、元に戻しました。"
                             "競合を解決したい場合はコマンドラインで git cherry-pick を実行してください")
        raise ValueError(out or "チェリーピックに失敗しました")
    if git("rev-parse", "HEAD").strip() == before:
        return {"ok": True, "message": f"{h[:7]} は変更なしでした"}
    return {"ok": True, "message": f"{h[:7]} をチェリーピックしました"}


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
                       capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip() not in ("", "0"):
        raise ValueError("マージコミットを含む範囲は対話的リベースできません (マージが失われてしまうため)")
    hashes = rebase_todo_hashes(base)
    if not hashes:
        raise ValueError(f"{base[:7]} の上に載せ直すコミットがありません")
    if len(hashes) > REBASE_I_MAX:
        raise ValueError(f"対象のコミットが {len(hashes)} 件あり多すぎます (上限 {REBASE_I_MAX} 件)。"
                         "もっと HEAD に近いコミットを選んでください")
    return hashes


def api_rebase_todo(q):
    """対話的リベースの対象コミット一覧 (画面の編集ダイアログに出す)。"""
    if not HAS_WORKTREE:
        raise ValueError("ワークツリーのないリポジトリではリベースできません")
    base = resolve_commit((q.get("hash") or [""])[0])
    head = api_head()
    if not head["branch"]:
        raise ValueError("ブランチをチェックアウトしていません (detached HEAD ではリベースできません)")
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
        raise ValueError("ワークツリーのないリポジトリではリベースできません")
    base = resolve_commit(body.get("hash", ""))
    if is_merging() or is_rebasing():
        raise ValueError("マージまたはリベースの途中です。先に完了するか中止してください")
    head = api_head()
    if not head["branch"]:
        raise ValueError("ブランチをチェックアウトしていません (detached HEAD ではリベースできません)")
    if not has_head():
        raise ValueError("最初のコミットがないためリベースできません")
    items = body.get("todo")
    if not isinstance(items, list) or not items or not all(isinstance(i, dict) for i in items):
        raise ValueError("todo が不正です")
    todo = []
    for it in items:
        act, h = it.get("action"), str(it.get("hash", ""))
        if act not in REBASE_I_ACTIONS or not re.fullmatch(r"[0-9a-f]{40}", h):
            raise ValueError("todo が不正です")
        msg = str(it.get("message") or "").strip()
        if act == "reword" and not msg:
            raise ValueError(f"{h[:7]} の新しいコミットメッセージを入力してください")
        todo.append((act, h, msg))
    expected = check_rebase_i_range(base)
    if sorted(h for _, h, _ in todo) != sorted(expected):
        raise ValueError("対象のコミットが変わっています。画面を更新して、もう一度やり直してください")
    kept = [t for t in todo if t[0] != "drop"]
    if kept and kept[0][0] in ("squash", "fixup"):
        raise ValueError("先頭のコミットは squash / fixup にできません (まとめ先のコミットが必要です)")

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
                raise ValueError("対話的リベースの途中で競合またはエラーが起きたため、中止して元に戻しました。\n" + tail +
                                 "\n競合を解決したい場合はコマンドラインで git rebase -i を実行してください")
            raise ValueError(out or "リベースに失敗しました")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if git("rev-parse", "HEAD").strip() == before:
        return {"ok": True, "message": f"{head['branch']} は変更なしでした"}
    n = Counter(a for a, _, _ in todo)
    parts = [f"{label} {n[k]}" for k, label in (("reword", "メッセージ変更"), ("squash", "squash"),
                                               ("fixup", "fixup"), ("drop", "削除")) if n[k]]
    return {"ok": True, "message": f"{head['branch']} を {base[:7]} の上に対話的リベースしました" +
            (" (" + " / ".join(parts) + ")" if parts else "")}


# --------------------------------------------------------------------------
# リモートとの通信 (fetch / pull / push)
# --------------------------------------------------------------------------
NET_TIMEOUT = 300  # 秒。認証待ちなどで固まったままロックを握り続けないようにする


def run_git_net(*args, timeout=None):
    """リモートと通信する git を実行する。端末への入力待ち (パスワードなど) で固まらないようにする。"""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    timeout = timeout or NET_TIMEOUT
    try:
        return subprocess.run(["git", "-C", REPO, *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env, timeout=timeout,
                              stdin=subprocess.DEVNULL, start_new_session=True)  # 制御端末を切り離し ssh が /dev/tty に尋ねないように
    except subprocess.TimeoutExpired:
        raise ValueError(f"git {args[0]} が {timeout} 秒でタイムアウトしました。認証の入力待ちなどで止まった可能性があります"
                         " (認証情報を credential helper や ssh-agent に登録してください)")


def net_error(r, fallback):
    text = (r.stderr or r.stdout).strip()
    text = "\n".join(l for l in text.splitlines() if not l.startswith("hint:")).strip()  # 長い hint は省く
    return text or fallback


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
    args = ["fetch", "--all"]
    if body.get("prune") is True:
        args.append("--prune")
    before = refs_snapshot()
    # リモートは順に取得されるので、複数あるぶんだけタイムアウトを延ばす (最大 4 倍)
    r = run_git_net(*args, timeout=NET_TIMEOUT * min(max(len(git("remote").split()), 1), 4))
    n = count_changed_refs(before, refs_snapshot())
    if r.returncode != 0:  # 一部のリモートだけ失敗しても、git は残りのリモートを取得している
        raise ValueError(net_error(r, "fetch に失敗しました") +
                         (f"\n(失敗しなかったリモートの分は取得済みです: {n} 件のリモート追跡ブランチ/タグが更新されました)" if n else ""))
    return {"ok": True, "message": f"fetch しました ({n} 件のリモート追跡ブランチ/タグが更新されました)" if n
            else "fetch しました (更新はありません)"}


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
        raise ValueError("リモート名が不正です")
    r = subprocess.run(["git", "-C", REPO, "check-ref-format", "refs/remotes/" + name + "/HEAD"], capture_output=True)
    if r.returncode != 0:
        raise ValueError(f"{name!r} はリモート名として使えません")


def api_remote_add(body):
    """リモートを追加する。fetch:true なら続けて fetch する (fetch に失敗してもリモートは追加済み)。"""
    name, url = body.get("name"), body.get("url")
    check_remote_name(name)
    if not isinstance(url, str) or not url.strip() or "\0" in url or "\n" in url:
        raise ValueError("URL が不正です")
    url = url.strip()
    if url.startswith("-") or re.match(r"(ext|fd)::", url):  # オプション注入と、任意コマンドを実行できる転送方式を避ける
        raise ValueError("この URL は使えません")
    if name in {r["name"] for r in list_remotes()}:
        raise ValueError(f"リモート {name} は既に登録されています")
    run_git_write("remote", "add", "--", name, url)
    msg = f"リモート {name} を追加しました"
    if body.get("fetch") is True:
        r = run_git_net("fetch", name)
        if r.returncode != 0:
            return {"ok": True, "fetch_failed": True,
                    "message": msg + "が、fetch に失敗しました: " + net_error(r, "fetch に失敗しました")}
        msg += " (fetch しました)"
    return {"ok": True, "message": msg}


def api_remote_remove(body):
    """リモートを削除する (リモート追跡ブランチと設定も消える。リモート側は変わらない)。"""
    name = body.get("name")
    if not isinstance(name, str) or name not in {r["name"] for r in list_remotes()}:  # 登録済みの名前だけ許可
        raise ValueError("リモートが見つかりません")
    run_git_write("remote", "remove", "--", name)
    return {"ok": True, "message": f"リモート {name} を削除しました (リモート追跡ブランチも削除しました)"}


def api_pull(body):
    """現在のブランチに upstream の変更を取り込む。
    merge で競合したときはマージ途中で止める (WIP で解決)。rebase で競合したときは中止して元に戻す。"""
    if not HAS_WORKTREE:
        raise ValueError("ワークツリーのないリポジトリでは pull できません")
    mode = body.get("mode", "ff-only")
    flags = {"ff-only": ["--ff-only"], "merge": ["--no-rebase", "--no-edit"], "rebase": ["--rebase"]}.get(str(mode))
    if flags is None:
        raise ValueError("pull の方法が不正です")
    if is_merging() or is_rebasing():
        raise ValueError("マージまたはリベースの途中です。先に完了するか中止してください")
    if not has_head():
        raise ValueError("最初のコミットがないため pull できません")
    branch = api_head()["branch"]
    if not branch:
        raise ValueError("ブランチをチェックアウトしていません (detached HEAD では pull できません)")
    up = upstream_of_head()
    if not up:
        raise ValueError(f"ブランチ {branch} には upstream が設定されていません (git branch -u <リモート>/<ブランチ> で設定するか、push で設定してください)")
    before = git("rev-parse", "HEAD").strip()
    r = run_git_net("pull", *flags)
    if r.returncode != 0:
        if is_merging():  # 競合 (自動マージできなかった)
            return {"ok": True, "conflict": True,
                    "message": f"{up} のマージで競合しました。WIP の競合ファイルを解決してコミットしてください"}
        if is_rebasing():  # 競合 → 元に戻す
            subprocess.run(["git", "-C", REPO, "rebase", "--abort"], capture_output=True)
            raise ValueError(f"{up} の上へのリベースで競合したため中止し、元に戻しました。"
                             "競合を解決したい場合はコマンドラインで git pull --rebase を実行してください")
        raise ValueError(net_error(r, "pull に失敗しました") +
                         ("\n(履歴が分岐しています。方法を「マージ」か「リベース」にして再度 pull してください)" if mode == "ff-only" else ""))
    if git("rev-parse", "HEAD").strip() == before:
        return {"ok": True, "message": f"{branch} は既に最新です (変更なし)"}
    return {"ok": True, "message": f"{branch} に {up} の変更を取り込みました"}


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
    なければ現在のブランチ。upstream があればその先へ、なければ set_upstream 確認のうえ -u で push する。"""
    if not has_head():
        raise ValueError("最初のコミットがないため push できません")
    branch = body.get("branch")
    if branch:
        if not isinstance(branch, str) or branch.startswith("-"):
            raise ValueError("ブランチ名が不正です")
        if subprocess.run(["git", "-C", REPO, "rev-parse", "-q", "--verify", f"refs/heads/{branch}"],
                          capture_output=True).returncode != 0:
            raise ValueError(f"ローカルブランチ {branch} が見つかりません")
    else:
        branch = api_head()["branch"]
        if not branch:
            raise ValueError("ブランチをチェックアウトしていません (detached HEAD では push できません)")
    args = ["push"]
    if body.get("force") is True:
        args.append("--force-with-lease")
    remotes = git("remote").split()
    if not remotes:
        raise ValueError("リモートが登録されていません")
    upstream_remote, merge = branch_config(branch, "remote"), branch_config(branch, "merge")
    has_upstream = bool(upstream_remote and upstream_remote != "." and merge)
    # push 先: branch.<名前>.pushRemote > remote.pushDefault (git の優先順位と同じ)
    push_remote = branch_config(branch, "pushRemote") or git_config("remote.pushDefault")
    if push_remote:
        if push_remote not in remotes:
            raise ValueError(f"push 先のリモート {push_remote!r} (pushRemote / remote.pushDefault) は登録されていません")
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
            raise ValueError(f"リモート {chosen!r} は登録されていません")
        remote = chosen
        args += ["-u", remote, f"refs/heads/{branch}:refs/heads/{branch}"]
    before = refs_snapshot()
    r = run_git_net(*args)
    if r.returncode != 0:
        raise ValueError(net_error(r, "push に失敗しました"))
    changed = count_changed_refs(before, refs_snapshot()) > 0
    where = f" ({remote})" if len(remotes) > 1 else ""
    return {"ok": True, "message": f"{branch} を push しました{where}" if changed else f"{branch} を push しました{where} (更新するものはありませんでした)"}


POST_ROUTES = {"/api/stash": api_stash, "/api/rebase": api_rebase, "/api/rebase-i": api_rebase_i, "/api/cherry-pick": api_cherry_pick, "/api/merge": api_merge, "/api/branch": api_branch, "/api/tag": api_tag, "/api/ref-delete": api_delete_ref,
               "/api/checkout": api_checkout, "/api/reset": api_reset, "/api/wip-commit": api_wip_commit,
               "/api/fetch": api_fetch, "/api/pull": api_pull, "/api/push": api_push,
               "/api/remote-add": api_remote_add, "/api/remote-remove": api_remote_remove}


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
            elif u.path == "/api/stash-list":
                self._send(200, "application/json", json.dumps(api_stash_list(q), ensure_ascii=False))
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
            elif u.path == "/api/commit":
                self._send(200, "application/json", json.dumps(api_commit(q), ensure_ascii=False))
            elif u.path == "/api/tree":
                self._send(200, "application/json", json.dumps(api_tree(q), ensure_ascii=False))
            elif u.path == "/api/file":
                self._send(200, "application/json", json.dumps(api_file(q), ensure_ascii=False))
            elif u.path == "/files":
                h = resolve_commit(q.get("hash", [""])[0])
                self._send(200, "text/html", FILES_PAGE.replace("__HASH__", html.escape(h)))
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
    ap.add_argument("--full-layout", action="store_true",
                    help="ノードが多くても dot のレイアウト最適化を省略しない (遅いが交差が少し減る)")
    ap.add_argument("--no-count", action="store_true", help="省略数 (+N) の計算をしない")
    ap.add_argument("--rankdir", default="BT", help="BT(下から上) / TB / LR (既定: BT)")
    ap.add_argument("--dot", action="store_true", help="dot を標準出力に出して終了")
    ap.add_argument("--no-wip", action="store_true", help="未コミットの変更 (WIP ノード) を表示しない")
    ap.add_argument("--read-only", action="store_true", help="書き込み操作 (ブランチ/タグ作成・チェックアウト) を無効にする")
    ap.add_argument("--allow-write", action="store_true",
                    help="127.0.0.1 以外で待ち受けるときも書き込み操作を許可する (既定では無効)")
    ap.add_argument("--lan", action="store_true",
                    help="--host 0.0.0.0 --allow-write の省略形 (他の端末からも操作可。認証はないので信頼できるネットワークで)")
    OPTS = ap.parse_args()
    if OPTS.lan:
        if OPTS.host == "127.0.0.1":  # --host を明示しているときはそちらを優先する
            OPTS.host = "0.0.0.0"
        OPTS.allow_write = True
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
    url = f"http://{'localhost' if OPTS.host == '0.0.0.0' else OPTS.host}:{OPTS.port}/"  # 0.0.0.0 はブラウザで開く宛先にならない
    print(f"serving {url}  (Ctrl+C で終了)  書き込み操作: {'有効' if WRITABLE else '無効'}")
    if OPTS.host == "0.0.0.0":
        print(f"  待ち受け: 0.0.0.0:{OPTS.port} (他の端末からは http://<このPCのIP>:{OPTS.port}/ )")
        if WRITABLE:
            print("  注意: 認証はありません。同じネットワークの誰でもブランチ作成・コミット・push などができます")
    if OPTS.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
