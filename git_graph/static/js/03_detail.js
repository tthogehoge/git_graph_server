
function updatePageNav(hash) {
  // 左上のファイルアイコンは、選択中のコミットを開く。何も選んでいないときは HEAD (まだコミットが無ければ作業ツリー) を開く。
  const filesBtn = document.getElementById("navFilesBtn");
  if (!filesBtn) return;
  const open = build => {
    if (hash) { location.href = build(hash); return; }
    fetch("/api/head").then(r => r.ok ? r.json() : r.text().then(m => { throw new Error(m || r.status); }))
      .then(r => { location.href = build(r.head || "WIP"); })
      .catch(e => setGraphStatus(e.message, "error"));
  };
  filesBtn.disabled = false;
  filesBtn.onclick = () => open(h => "/files?hash=" + encodeURIComponent(h));
}
function updatePageNavForSelection(hash) {
  updatePageNav(hash);
}

// 差分の URL・変更の種類のバッジ・ファイルの一覧は、WIP の一覧と同じ 3 画面共通の部品 (wip_panel.js)。
// 差分はファイル表示の右ペインで開く。ctx は {hash} (1コミット) または {from, to} (比較)
const diffUrl = WipPanel.diffUrl, badge = WipPanel.badge, dirOf = WipPanel.dirOf;
function filesHtml(ctx, files, offset) { return WipPanel.filesTable(ctx, files, offset, {t: t}); }

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
      '<button class="sbtn" id="submoduleUpdateRemoteBtn" disabled title="' + esc(t("submodule_update_remote_tip")) + '">' + esc(t("submodule_update_remote")) + "</button> " +
      '<button class="sbtn" id="submoduleFetchBtn" disabled title="' + esc(t("submodule_fetch_tip")) + '">' + esc(t("submodule_fetch")) + "</button> " +
      '<label title="' + esc(t("submodule_recursive_tip")) + '"><input type="checkbox" id="submoduleRecursive"> ' + esc(t("submodule_recursive")) + "</label> " +
      '<button class="sbtn" id="submoduleAddToggle" title="' + esc(t("submodule_add_tip")) + '">' + esc(t("submodule_add_toggle")) + "</button></div>" +
      '<div class="submodule-bar" id="submoduleAddForm" hidden>' +
      '<input id="smAddUrl" size="42" placeholder="' + esc(t("submodule_add_url")) + '"> ' +
      '<input id="smAddPath" size="22" placeholder="' + esc(t("submodule_add_path")) + '"> ' +
      '<input id="smAddBranch" size="16" placeholder="' + esc(t("submodule_add_branch")) + '"> ' +
      '<button class="sbtn" id="submoduleAddGo">' + esc(t("submodule_add_go")) + "</button></div>"
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
    // 一覧からは start_path が空 = グラフ。ファイル表示から開くときと同じく start_path を付ける。
    // ポップアップとしてブロックされることがあるので、リンクも残す
    let link = "";
    if (r.port) {
      const url = location.protocol + "//" + location.hostname + ":" + r.port + (r.start_path || "/");
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
  for (const id of ["submoduleUpdateBtn", "submoduleUpdateRemoteBtn", "submoduleFetchBtn"]) {
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
function submoduleRecursive() {
  const el = document.getElementById("submoduleRecursive");
  return !!(el && el.checked);
}
// HTTP 認証が必要だと返されたら、origin (接続先) ごとに認証情報を尋ね、そのパスだけをやり直す。
// 入力した認証情報は、サーバーが、その origin の質問にだけ使う。更新・fetch は結果を、パスごとに最後のものへ入れ替えて返す。
async function postSubmoduleWithAuth(url, body, doneKey) {
  let r = await post(url, body);
  if (!r.auth_required) return r;
  const merged = new Map((r.results || []).map(x => [x.path, x]));
  const asked = new Set(), pending = (r.auth_requests || []).slice();
  while (pending.length) {
    const req = pending.shift();
    if (asked.has(req.origin)) continue;  // 同じ接続先を 2 度は尋ねない (認証に失敗した結果は、そのまま失敗として出す)
    asked.add(req.origin);
    const auth = await openActionDialog(t("http_auth") + " — " + req.origin,
      '<label>' + esc(t("auth_username")) + '<input name="username" autocomplete="username" autofocus></label>' +
      '<label>' + esc(t("auth_password")) + '<input type="password" name="password" autocomplete="current-password"></label>',
      t("authenticate"));
    if (!auth) {
      for (const p of (req.paths || [])) merged.set(p, {path: p, ok: false, error: t("submodule_auth_cancelled")});
      if (!req.paths) throw new Error(t("submodule_auth_cancelled"));  // 追加: そのまま取りやめる
      continue;
    }
    r = await post(url, Object.assign({}, body, req.paths ? {paths: req.paths} : {}, {auth: auth, auth_origin: req.origin}));
    for (const x of (r.results || [])) merged.set(x.path, x);
    for (const q of (r.auth_requests || [])) pending.push(q);  // 入れ子の submodule が別のホストを求めた
    if (!r.results) return r;  // 追加: 結果はそのまま
  }
  const results = Array.from(merged.values()), done = results.filter(x => x.ok).length;
  return {ok: done === results.length, results: results, message: t(doneKey, {done: done, total: results.length})};
}
// 選んだ submodule に対して、url の API を 1 回呼ぶ (更新・fetch)。結果は一覧の上に出し、一覧の行は読み直す
function runSubmoduleBatch(url, extra, confirmKey, workingKey, doneKey) {
  const paths = selectedSubmodules();
  if (!paths.length || submoduleBusy) return;
  if (confirmKey && !confirm(t(confirmKey, {n: paths.length}))) return;
  submoduleBusy = true; syncSubmoduleSel();
  submoduleMessage(esc(t(workingKey, {n: paths.length})));
  postSubmoduleWithAuth(url, Object.assign({paths: paths, recursive: submoduleRecursive()}, extra), doneKey).then(async r => {
    const failed = r.results.filter(x => !x.ok).map(x => "<div>" + esc(x.path) + ": " + esc(x.error) + "</div>").join("");
    submoduleBusy = false;
    await reloadSubmoduleRows().catch(() => {});
    submoduleMessage('<span style="color:' + (r.ok ? "#080" : "#b00") + '">' + esc(r.message) + "</span>" + failed);
  }).catch(e => submoduleMessage('<span style="color:#b00">' + esc(t("error") + e.message) + "</span>"))
    .finally(() => { submoduleBusy = false; syncSubmoduleSel(); });
}
function updateSubmodules(remote) {
  runSubmoduleBatch("/api/submodule-update", {remote: remote},
                    remote ? "submodule_confirm_update_remote" : "submodule_confirm_update", "submodule_updating", "submodule_update_done");
}
function fetchSubmodules() {
  runSubmoduleBatch("/api/submodule-fetch", {}, "", "submodule_fetching", "submodule_fetch_done");
}
function addSubmodule() {
  const val = id => (document.getElementById(id) || {}).value || "";
  const url = val("smAddUrl").trim(), path = val("smAddPath").trim(), branch = val("smAddBranch").trim();
  if (!url || !path) { submoduleMessage(esc(t("submodule_url_and_path_required"))); return; }
  if (submoduleBusy) return;
  submoduleBusy = true; syncSubmoduleSel();
  const go = document.getElementById("submoduleAddGo");
  if (go) go.disabled = true;
  submoduleMessage(esc(t("submodule_adding", {path: path})));
  postSubmoduleWithAuth("/api/submodule-add", {url: url, path: path, branch: branch, recursive: submoduleRecursive()}, "").then(async r => {
    for (const id of ["smAddUrl", "smAddPath", "smAddBranch"]) { const el = document.getElementById(id); if (el) { el.value = ""; delete el.dataset.touched; } }
    await reloadSubmoduleRows().catch(() => {});
    refreshGraph().catch(() => {});  // WIP の変更 (.gitmodules など) を反映する
    submoduleMessage('<span style="color:#080">' + esc(r.message).replace(/\n/g, "<br>") + "</span>");
  }).catch(e => submoduleMessage('<span style="color:#b00">' + esc(t("error") + e.message) + "</span>"))
    .finally(() => { submoduleBusy = false; syncSubmoduleSel(); if (go) go.disabled = false; });
}
// URL から、追加先のパスの案 (最後の要素から .git を除いたもの) を入れる。パスを自分で入力したあとは触らない
listView.addEventListener("input", ev => {
  const el = ev.target;
  if (el.id === "smAddPath") { el.dataset.touched = "1"; return; }
  if (el.id !== "smAddUrl") return;
  const pathEl = document.getElementById("smAddPath");
  if (!pathEl || pathEl.dataset.touched) return;
  const last = el.value.trim().replace(/[\/:]+$/, "").split(/[\/:]/).pop() || "";
  pathEl.value = last.replace(/\.git$/, "");
});
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
  if (ev.target.closest("#submoduleFetchBtn")) { fetchSubmodules(); return; }
  if (ev.target.closest("#submoduleAddToggle")) {
    const f = document.getElementById("submoduleAddForm");
    if (f) { f.hidden = !f.hidden; if (!f.hidden) document.getElementById("smAddUrl").focus(); }
    return;
  }
  if (ev.target.closest("#submoduleAddGo")) { addSubmodule(); return; }
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
    updatePageNav(c.hash);
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
    // キーボード操作の続きのため、ファイル表示リンクへフォーカスを移す
    const filesLink = detailView.querySelector("a.detail-files");
    if (filesLink) filesLink.focus({preventScroll: true});
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
  if (!ev.target.closest("#detailView")) return;
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
