
let pageNavFetch = 0;  // 選択のたびに増やす。古い /api/commit の応答は無視する
function pageNavIsStash(hash) {
  return !!hash && (!!stashByHash[hash] || (typeof stashNameOf === "function" && !!stashNameOf(hash)));
}
function updatePageNav(hash, files) {
  // 左上のファイル / 差分アイコンを、選択中コミットに合わせて有効化する
  const filesBtn = document.getElementById("navFilesBtn");
  const diffBtn = document.getElementById("navDiffBtn");
  if (!filesBtn || !diffBtn) return;
  if (!hash || pageNavIsStash(hash)) {
    filesBtn.disabled = true;
    filesBtn.onclick = null;
    diffBtn.disabled = true;
    diffBtn.onclick = null;
    return;
  }
  filesBtn.disabled = false;
  filesBtn.onclick = () => { location.href = "/files?hash=" + encodeURIComponent(hash); };
  if (files && files.length) {
    diffBtn.disabled = false;
    const f = files[0];
    const p = {hash: hash, path: f.path};
    if (f.old_path) p.old = f.old_path;
    if (f.wip) p.wip = f.wip;
    diffBtn.onclick = () => { location.href = "/diff?" + new URLSearchParams(p); };
  } else {
    diffBtn.disabled = true;
    diffBtn.onclick = null;
  }
}
function updatePageNavForSelection(hash) {
  // 1回目のクリック (詳細を開く前) でもファイルを有効にし、差分は変更一覧を取ってから有効にする
  updatePageNav(hash, null);
  if (!hash || pageNavIsStash(hash)) return;
  const seq = ++pageNavFetch;
  api("/api/commit", {hash: hash}).then(c => {
    if (seq !== pageNavFetch || actionHash !== hash) return;
    updatePageNav(hash, c.files || []);
  }).catch(() => {});
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
const WIP_DIR_COLLAPSE = 10;  // 1つのディレクトリの変更がこの件数以上なら、既定で畳む
const WIP_OPEN_KEY = "git-graph-wip-open-dirs";  // /diff ページと共有する
let wipOpenDirs = new Set();  // 開いているディレクトリ ("種別|パス")
try { wipOpenDirs = new Set(JSON.parse(sessionStorage.getItem(WIP_OPEN_KEY) || "[]")); } catch (e) {}
function saveWipOpenDirs() { try { sessionStorage.setItem(WIP_OPEN_KEY, JSON.stringify([...wipOpenDirs])); } catch (e) {} }
function dirOf(path) { const i = path.lastIndexOf("/"); return i < 0 ? "" : path.slice(0, i); }
function filesHtml(ctx, files, offset, withCheck, collapse) {
  offset = offset || 0;
  if (!files.length) return '<p class="hint">' + esc(t("no_changed_files")) + '</p>';
  let add = 0, del = 0, max = 0;
  files.forEach(f => { add += f.added || 0; del += f.deleted || 0; max = Math.max(max, (f.added || 0) + (f.deleted || 0)); });
  const scale = max > 12 ? 12 / max : 1;
  const dirCount = {}, dirStat = {}, dirSeen = {};
  if (collapse) files.forEach(f => {
    const dir = dirOf(f.path);
    dirCount[dir] = (dirCount[dir] || 0) + 1;
    dirStat[dir] = dirStat[dir] || [0, 0];
    dirStat[dir][0] += f.added || 0; dirStat[dir][1] += f.deleted || 0;
  });
  const rows = files.map((f, k) => {
    const a = f.added || 0, d = f.deleted || 0;
    const dir = dirOf(f.path), folded = collapse && dirCount[dir] >= WIP_DIR_COLLAPSE;
    const open = folded && wipOpenDirs.has(f.wip + "|" + dir);
    let head = "";
    if (folded && !dirSeen[dir]) {
      dirSeen[dir] = true;
      head = '<tr class="drow" data-dir="' + esc(dir) + '" data-kind="' + esc(f.wip) + '" title="' + esc(t("dir_toggle")) + '">' +
        (withCheck ? '<td class="fk"><input type="checkbox" class="wipdir" data-kind="' + esc(f.wip) + '" data-dir="' + esc(dir) + '"></td>' : "") +
        '<td class="fp" colspan="3"><span class="dtog">' + (open ? "▼" : "▶") + '</span> ' + esc(dir ? dir + "/" : t("dir_root")) +
        ' <span class="hint">(' + esc(t("n_items", {n: dirCount[dir]})) + ') <span class="plus">+' + dirStat[dir][0] +
        '</span> <span class="minus">-' + dirStat[dir][1] + '</span></span></td></tr>';
    }
    const pa = a ? Math.max(1, Math.round(a * scale)) : 0, pd = d ? Math.max(1, Math.round(d * scale)) : 0;
    const name = f.old_path ? f.old_path + " => " + f.path : f.path;
    return head + '<tr class="frow' + (folded && !open ? " dhide" : "") + '" data-k="' + (offset + k) + '" data-dir="' + esc(dir) + '">' + (withCheck ? wipCheckCell(f) : "") + '<td class="fp">' + badge(f) + (f.tag ? '<span class="hint">[' + esc(f.tag) + '] </span>' : "") + '<a href="' + esc(diffUrl(ctx, f)) +
      '">' + esc(name) + '</a></td><td class="fc">' +
      (f.label ? esc(t(f.label)) : f.added === null ? "Bin" : a + d) + '</td><td class="fb"><span class="plus">' + "+".repeat(pa) +
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
function remoteBranches(h) {
  const n = DATA.nodes[h];
  return n ? n.refs.filter(r => r.kind === "remote").map(r => r.name) : [];
}
function defaultNewBranchName(h) {
  const remote = remoteBranches(h).sort()[0] || "";
  const slash = remote.indexOf("/");
  return slash >= 0 ? remote.slice(slash + 1) : remote;
}
function stashNameOf(h) {  // グラフ上の stash ノードの名前 (stash@{n})
  const n = DATA.nodes[h], r = n && n.refs.find(x => x.kind === "stash");
  return r ? r.name : null;
}
function localTags(h) {
  const n = DATA.nodes[h];
  return n ? n.refs.filter(r => r.kind === "tag").map(r => r.name) : [];
}
function showRefList(kind) {  // kind: "branches" | "tags"
  showLog(t(kind === "tags" ? "tag_list_title" : "branch_list_title"), "",
          '<div class="meta">' + esc(t(kind === "tags" ? "tag_list_help" : "branch_list_help")) + '</div>',
          false, 0, {path: "/api/" + kind, params: {}});
}
function showStashList(fromHistory) {
  loadStashes();
  showLog(t("stash_list_title"), "", '<div class="meta">' + esc(t("stash_list_help")) + '</div>',
          !!fromHistory, 0, {path: "/api/stash-list", params: {}});
}
function submoduleWritable() { return !!(typeof DATA !== "undefined" && DATA && DATA.writable); }
function showSubmoduleList() {
  const bar = submoduleWritable()
    ? '<div class="submodule-bar"><label><input type="checkbox" id="submoduleAll"> ' + esc(t("submodule_select_all")) + "</label> " +
      '<button class="sbtn" id="submoduleUpdateBtn" disabled title="' + esc(t("submodule_update_tip")) + '">' + esc(t("submodule_update")) + "</button> " +
      '<button class="sbtn" id="submoduleUpdateRemoteBtn" disabled title="' + esc(t("submodule_update_remote_tip")) + '">' + esc(t("submodule_update_remote")) + "</button></div>"
    : "";
  showLog(t("submodule_list_title"), "",
          '<div class="meta">' + esc(t("submodule_list_help")) + "</div>" + bar + '<div class="meta" id="submoduleMsg"></div>',
          false, 0, {path: "/api/submodules", params: {}});
}
// submodule は、この画面で選択 (コミットとして) するのではなく、別のプロセス (別のサーバー) で開く。
// submodule のコミットは、このリポジトリのグラフには無いので、行はコミットの行 (tr.clickable) にしない
function submoduleRowHtml(c, sm) {  // c = [hash, short, status, describe, subject]; sm = {path, url, initialized}
  const sel = submoduleWritable() ? '<td class="sel"><input type="checkbox" class="submodule-sel" data-path="' + esc(sm.path) + '"></td>' : "";
  return '<tr class="submodule-row' + (sm.initialized ? "" : " uninit") + '" data-path="' + esc(sm.path) + '" data-init="' + (sm.initialized ? "1" : "") +
    '" title="' + esc(t(sm.initialized ? "submodule_open_tip" : "submodule_uninit_tip")) + '">' + sel + '<td class="h">' + esc(c[1]) +
    '</td><td class="log-cell"><div class="log-main"><div class="log-subj">' + esc(c[4]) +
    '</div><div class="meta">' + esc(c[2]) + (c[3] ? " / " + esc(c[3]) : "") + "</div></div></td></tr>";
}
function submoduleMessage(html) {
  const el = document.getElementById("submoduleMsg");
  if (el) el.innerHTML = html;
}
let submoduleOpening = false;
function openSubmodule(path) {
  if (submoduleOpening) return;
  submoduleOpening = true;
  submoduleMessage(esc(t("submodule_opening", {path: path})));
  post("/api/submodule-open", {path: path}).then(r => {
    // ブラウザーでは、子のサーバーが待ち受けているポートを新しいタブで開く (pywebview では子が自分のウィンドウを開く)。
    // ポップアップとしてブロックされることがあるので、リンクも残す
    let link = "";
    if (r.port) {
      const url = location.protocol + "//" + location.hostname + ":" + r.port + "/";
      window.open(url, "_blank");
      link = ' <a href="' + esc(url) + '" target="_blank" rel="noopener">' + esc(url) + "</a>";
    }
    submoduleMessage(esc(r.message) + link);
  }).catch(e => submoduleMessage('<span style="color:#b00">' + esc(t("error") + e.message) + "</span>"))
    .finally(() => { submoduleOpening = false; });
}
function selectedSubmodules() {
  return Array.from(listView.querySelectorAll("input.submodule-sel:checked")).map(el => el.dataset.path);
}
function syncSubmoduleSel() {  // 選択数に合わせて、更新ボタンと全選択の状態を更新する
  const all = Array.from(listView.querySelectorAll("input.submodule-sel")), n = all.filter(el => el.checked).length;
  for (const id of ["submoduleUpdateBtn", "submoduleUpdateRemoteBtn"]) {
    const b = document.getElementById(id);
    if (b) b.disabled = n === 0 || submoduleBusy;
  }
  const top = document.getElementById("submoduleAll");
  if (top) { top.checked = all.length > 0 && n === all.length; top.indeterminate = n > 0 && n < all.length; }
}
async function reloadSubmoduleRows() {  // 履歴を増やさずに、一覧の行だけ読み直す
  const rows = listView.querySelector("#rows");
  if (!rows || listView.dataset.src !== "/api/submodules") return;
  const r = await api("/api/submodules", {});
  rows.innerHTML = r.commits.map((c, i) => submoduleRowHtml(c, r.submodules[i])).join("");
  const count = listView.querySelector("#count");
  if (count) count.textContent = t("n_changes", {n: r.total});
  syncSubmoduleSel();
}
let submoduleBusy = false;
function updateSubmodules(remote) {
  const paths = selectedSubmodules();
  if (!paths.length || submoduleBusy) return;
  if (!confirm(t(remote ? "submodule_confirm_update_remote" : "submodule_confirm_update", {n: paths.length}))) return;
  submoduleBusy = true; syncSubmoduleSel();
  submoduleMessage(esc(t("submodule_updating", {n: paths.length})));
  post("/api/submodule-update", {paths: paths, remote: remote}).then(async r => {
    const failed = r.results.filter(x => !x.ok).map(x => "<div>" + esc(x.path) + ": " + esc(x.error) + "</div>").join("");
    submoduleBusy = false;
    await reloadSubmoduleRows().catch(() => {});
    submoduleMessage('<span style="color:' + (r.ok ? "#080" : "#b00") + '">' + esc(r.message) + "</span>" + failed);
  }).catch(e => submoduleMessage('<span style="color:#b00">' + esc(t("error") + e.message) + "</span>"))
    .finally(() => { submoduleBusy = false; syncSubmoduleSel(); });
}
listView.addEventListener("change", ev => {
  if (ev.target.id === "submoduleAll") {
    for (const el of listView.querySelectorAll("input.submodule-sel")) el.checked = ev.target.checked;
    syncSubmoduleSel();
  } else if (ev.target.classList && ev.target.classList.contains("submodule-sel")) {
    syncSubmoduleSel();
  }
});
listView.addEventListener("click", ev => {
  if (ev.target.closest("#submoduleUpdateBtn")) { updateSubmodules(false); return; }
  if (ev.target.closest("#submoduleUpdateRemoteBtn")) { updateSubmodules(true); return; }
  const tr = ev.target.closest("tr.submodule-row");
  if (!tr || ev.detail > 1 || ev.target.closest("td.sel")) return;  // チェック欄のクリックでは開かない
  if (!tr.dataset.init) { submoduleMessage(esc(t("submodule_uninit_tip"))); return; }
  openSubmodule(tr.dataset.path);
});
function doStash(op) {
  if (!actionHash) return;
  const h = actionHash, sel = stashByHash[h] || stashNameOf(h);
  if (!sel) return;
  const label = {apply: t("op_apply"), pop: t("op_pop"), drop: t("op_drop")}[op];
  const msg = {
    apply: t("stash_confirm_apply", {sel: sel}),
    pop: t("stash_confirm_pop", {sel: sel}),
    drop: t("stash_confirm_drop", {sel: sel, hash: h.slice(0, 7)}),
  }[op];
  if (!confirm(msg)) return;
  document.querySelectorAll("#stashActions button").forEach(b => { b.disabled = true; });
  setGraphStatus(t("stash_busy"), "progress");
  const reload = () => Promise.all([loadStashes(), refreshGraph().catch(() => {})]).then(() => {
    if (curView === "list" && listView.dataset.src === "/api/stash-list") showStashList(true);
  });
  post("/api/stash", {hash: h, op: op})
    .then(r => {  // 編集中のファイルや管理外ファイルに邪魔されたら、破棄して上書きするか選ばせる
      if (!r.blocked) return r;
      setGraphStatus("");
      return askCheckoutConflict(r, "stash").then(mode => {
        if (!mode) return {cancelled: true, message: t("operation_cancelled")};
        setGraphStatus(t("stash_busy"), "progress");
        return post("/api/stash", {hash: h, op: op, on_conflict: mode});
      });
    })
    .then(r => {
      if (r.cancelled) { setGraphStatus(r.message, "error"); return reload(); }
      clearCmp(); setGraphStatus(r.message, "success"); return reload();
    })
    .catch(e => { setGraphStatus(t("stash_op_failed", {op: label, error: e.message}), "error"); return reload(); })
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
  const top = '<div class="meta"><label>' + esc(t("target_label")) + ': <select class="reflog-ref">' + opts + "</select></label></div>" +
    '<div class="meta">' + esc(t("reflog_help")) + '</div>';
  showLog("reflog " + esc(branch || "HEAD"), "", top, false, 0,
          {path: "/api/reflog", params: branch ? {branch: branch} : {}});
}
listView.addEventListener("change", ev => {
  const sel = ev.target.closest(".reflog-ref");
  if (sel) showReflog(sel.value);
});

// 詳細ペインの prev/next: まず表示中ログの行順、無ければ親/子
function detailNavHashes(h, parents) {
  const hashes = [];
  listView.querySelectorAll("tr.clickable[data-h]").forEach(tr => {
    if (tr.dataset.h) hashes.push(tr.dataset.h);
  });
  const i = hashes.indexOf(h);
  if (i >= 0) {
    return {
      newer: i > 0 ? hashes[i - 1] : null,
      older: i + 1 < hashes.length ? hashes[i + 1] : null,
    };
  }
  let older = (parents && parents[0]) || null;
  let newer = null;
  for (const e of Object.values(DATA.edges || {})) {
    if (e.to === h && e.from && DATA.nodes[e.from] && !DATA.nodes[e.from].wip) {
      newer = e.from;
      break;
    }
  }
  return { newer: newer, older: older };
}

let detailNavCursor = null;  // ナビゲーション位置
let detailFetchSeq = 0;
let detailNavBusy = false;
const DETAIL_LOADING_DELAY_MS = 150;  // これより速く返れば「読み込み中」を出さない

function stepDetailNav(dir) {
  if (detailNavBusy || curView !== "detail") return;
  // dir: "newer" | "older"。DOM の data-hash ではなく論理カーソルから次を決める
  const from = detailNavCursor || curDetailHash;
  if (!from) return;
  const n = detailNavHashes(from, null);
  const h = dir === "newer" ? n.newer : n.older;
  if (!h) return;
  detailNavCursor = h;
  selectActionTarget(h, true);
  if (nodeEl[h]) select(nodeEl[h]);
  if (history.state && history.state.screen === "detail") {
    history.replaceState({screen: "detail", hash: h}, "");
  }
  showDetail(h, true);
}

function detailNavButtonsHtml(h, parents) {
  // 戻る / 新しい / 古い を同じ iconbtn 仕様で並べる
  const n = detailNavHashes(h, parents);
  const backTitle = esc(t("back_history"));
  const newerTitle = esc(t("tip_commit_newer"));
  const olderTitle = esc(t("tip_commit_older"));
  const newerDis = n.newer ? "" : " disabled";
  const olderDis = n.older ? "" : " disabled";
  const backIcon = '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false">' +
    '<path d="M13 8H3M7.5 3.5 3 8l4.5 4.5" fill="none" stroke="currentColor" stroke-width="1.8" ' +
    'stroke-linecap="round" stroke-linejoin="round"/></svg>';
  return '<span class="detail-nav-bar">' +
    '<button type="button" class="sbtn iconbtn back-btn" title="' + backTitle +
    '" aria-label="' + backTitle + '">' + backIcon + '</button>' +
    '<button type="button" class="sbtn iconbtn" id="detailNewerBtn" title="' + newerTitle +
    '" aria-label="' + esc(t("commit_newer")) + '"' + newerDis + ">▲</button>" +
    '<button type="button" class="sbtn iconbtn" id="detailOlderBtn" title="' + olderTitle +
    '" aria-label="' + esc(t("commit_older")) + '"' + olderDis + ">▼</button>" +
    "</span>";
}

function bindDetailNavButtons() {
  const newer = document.getElementById("detailNewerBtn");
  const older = document.getElementById("detailOlderBtn");
  if (newer) newer.onclick = () => stepDetailNav("newer");
  if (older) older.onclick = () => stepDetailNav("older");
}

function detailActionLinksHtml(c) {
  // ファイル表示 / 差分 / パッチ作成はタイトルの次の行
  let links = '<a class="detail-files" href="/files?hash=' + encodeURIComponent(c.hash) +
    '" title="' + esc(t("tip_files")) + '">' + esc(t("files")) + "</a>";
  if (c.files && c.files.length) {
    links += '<a class="detail-files" href="' + esc(diffUrl({hash: c.hash}, c.files[0])) + '">' +
      esc(t("file_diff")) + "</a>";
  }
  links += '<a class="detail-patch" href="#" data-hash="' + esc(c.hash) +
    '" data-name="' + esc(c.hash.slice(0, 7)) + '.patch" title="' +
    esc(t("tip_patch_create_commit")) + '">' + esc(t("patch_create")) + "</a>";
  return '<div class="detail-actions">' + links + "</div>";
}

function showDetail(h, fromHistory) {
  document.body.classList.remove("compare-logs-mode");
  if (!fromHistory) { pushScreen({screen: "detail", hash: h}); revealHash(h); }
  detailNavCursor = h;
  detailNavBusy = true;
  setDocumentTitle(t("commit_label") + " " + h.slice(0, 7));
  const seq = ++detailFetchSeq;
  const stay = curView === "detail";  // すでに詳細表示中 (上下キー・prev/next)
  const nav = detailNavButtonsHtml(h, null);
  let loadingTimer = null;
  const showLoading = () => {
    detailView.innerHTML = nav + '<p class="hint">' + esc(t("loading")) + '</p>';
    bindDetailNavButtons();
  };
  if (!stay) {
    showLoading();
    showView("detail");
  } else {
    // すぐ返るときは今の内容のまま。遅いときだけ読み込み中に切り替える
    loadingTimer = setTimeout(() => {
      if (seq !== detailFetchSeq) return;
      showLoading();
    }, DETAIL_LOADING_DELAY_MS);
  }
  api("/api/commit", {hash: h}).then(c => {
    if (seq !== detailFetchSeq) return;
    if (loadingTimer) clearTimeout(loadingTimer);
    curCtx = {hash: c.hash}; curFiles = c.files; curDetailHash = c.hash;
    if (!detailNavCursor) detailNavCursor = c.hash;
    updatePageNav(c.hash, c.files);
    const parents = c.parents.map(p => esc(p.slice(0, 7))).join(", ") || t("none");
    detailView.innerHTML = detailNavButtonsHtml(c.hash, c.parents) +
      "<h2>" + t("commit_label") + " " + esc(c.hash.slice(0, 7)) + "</h2>" +
      detailActionLinksHtml(c) +
      '<span id="dChips">' + detailChips(c.hash) + "</span>" +
      '<div class="meta">' + esc(c.hash) + copyLink(c.hash) + "</div>" +
      '<div class="meta">Author: ' + esc(c.author) + "</div>" +
      '<div class="meta">Date: ' + esc(c.date) + "</div>" +
      '<div class="meta">Parent: ' + parents + "</div>" +
      "<pre>" + esc(c.message) + "</pre>" + filesHtml(curCtx, c.files) +
      '<div class="meta"><code>git show ' + esc(c.hash.slice(0, 7)) + "</code></div>";
    bindDetailNavButtons();
    detailNavBusy = false;
  }).catch(e => {
    if (seq !== detailFetchSeq) return;
    if (loadingTimer) clearTimeout(loadingTimer);
    detailView.innerHTML = nav + '<p class="hint">' + esc(t("error")) + esc(e.message) + "</p>";
    bindDetailNavButtons();
    detailNavBusy = false;
  });
}

// 詳細表示中は ↑/↓ で同じ移動 (論理カーソルを先に進める)
document.addEventListener("keydown", ev => {
  if (curView !== "detail" || !(detailNavCursor || curDetailHash)) return;
  const tag = ev.target && ev.target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
  if (document.querySelector('[id$="Modal"]:not([hidden])')) return;
  if (ev.key !== "ArrowUp" && ev.key !== "ArrowDown") return;
  ev.preventDefault();
  stepDetailNav(ev.key === "ArrowUp" ? "newer" : "older");
});

function post(path, body) {
  return fetch(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)})
    .then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }))
    .then(r => { updateUndoButtons(); return r; });  // 書き込み操作のたびに、元に戻す/やり直しの有効・無効を更新する
}
// ---- パッチの作成 (ダウンロード) と適用 (アップロード) ----
// say(text, ok) は結果の出し先。右ペインの WIP とコミット詳細・比較で共通に使う
function downloadPatch(params, name, say) {
  return fetch("/patch?" + new URLSearchParams(params))
    .then(r => r.ok ? r.blob() : r.text().then(m => { throw new Error(m || r.status); }))
    .then(blob => {  // 失敗 (変更なしなど) のときにページを移動しないよう、fetch で取得して保存する
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = name;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 10000);
    })
    .catch(e => say(e.message, false));
}
function bytesToBase64(bytes) {
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(s);
}
let patchPicker = null;
function pickPatchFile(mode, say, done) {  // mode: "apply" (git apply) | "am" (git am)。done(結果) は適用に成功したあとに呼ぶ
  if (!patchPicker) {
    patchPicker = document.createElement("input");
    patchPicker.type = "file"; patchPicker.hidden = true;
    document.body.appendChild(patchPicker);
  }
  const input = patchPicker;
  input.value = "";
  input.onchange = () => {
    const f = input.files[0];
    if (!f) return;
    const size = f.size < 1024 ? f.size + " B" : Math.ceil(f.size / 1024) + " KB";
    if (!confirm(t(mode === "am" ? "confirm_patch_am" : "confirm_patch_apply", {name: f.name, size: size}))) return;
    say(t("wip_execute"), true);
    f.arrayBuffer()
      .then(buf => post(mode === "am" ? "/api/patch-am" : "/api/patch-apply", {data: bytesToBase64(new Uint8Array(buf))}))
      .then(done)
      .catch(e => say(e.message, false));
  };
  input.click();
}
document.addEventListener("click", ev => {  // コミット詳細・比較の「パッチ作成」
  const a = ev.target.closest("a.detail-patch");
  if (!a) return;
  ev.preventDefault();
  const params = {};
  ["hash", "from", "to"].forEach(k => { if (a.dataset[k]) params[k] = a.dataset[k]; });
  downloadPatch(params, a.dataset.name, (text, ok) => setGraphStatus(text, ok ? "success" : "error"));
});

function doAction(btn) {  // ブランチ/タグのチップの × (削除)
  if (btn.dataset.act !== "delete-ref") return;
  const kind = btn.dataset.kind, name = btn.dataset.name;
  const url = "/api/ref-delete", body = {kind: kind, name: name};
  if (!confirm(t(kind === "branch" ? "confirm_delete_branch" : "confirm_delete_tag", {name: name}))) return;
  btn.disabled = true; setGraphStatus(t("deleting"), "progress");
  post(url, body)
    .then(r => {
      if (!r.needs_force) return r;
      if (!confirm(t("confirm_force_delete", {name: name}))) return null;
      return post(url, Object.assign({}, body, {force: true}));
    })
    .then(r => {
      if (r === null) { setGraphStatus(t("force_delete_cancelled")); return; }
      setGraphStatus(r.message, "success");
      return refreshGraph().catch(() => {});
    })
    .catch(e => setGraphStatus(t("delete_failed", {error: e.message}), "error", true))
    .finally(() => { btn.disabled = false; });
}
