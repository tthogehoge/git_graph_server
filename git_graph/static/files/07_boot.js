/* ---- files/07_boot.js ---- */
// ---- ログの行をドラッグ&ドロップして A / B にセットする (ドラッグ元が A、ドロップ先が B)。ドラッグの動きはグラフ画面と共通 (commit_log.js)。落としたら、メニューバーの AB操作メニューを開く ----
const commitDrag = CommitLog.createDrag({
  hitAt: (x, y) => {
    const at = document.elementFromPoint(x, y), tr = at && at.closest("#content tr.clickable");
    return tr && tr.dataset.h ? {el: tr, hash: tr.dataset.h} : null;
  },
  tip: (from, hit) => "A: " + from.slice(0, 7) + (hit ? "  →  B: " + hit.hash.slice(0, 7) : ""),
  onDrop: (from, hit) => { if (window.mbSetAB) window.mbSetAB(from, hit.hash); },
});
content.addEventListener("pointerdown", ev => {  // ログの行からドラッグを始める (リンク・入力欄・コピー記号の上は除く)
  const tr = ev.target.closest("tr.clickable");
  if (!tr || ev.target.closest("a, input, select, textarea, .copy")) return;
  commitDrag.start(tr, tr.dataset.h, ev);
});
function loadFileDiff(path) {  // 右ペインに、このファイルの差分を差分エディタ (変更前 | 変更後) で出す
  closeEditor();  // 表示のエディタ
  fileDiffBtn.hidden = true;  // 差分表示中は内容ボタンだけ (差分ボタンは出さない)
  fileContentBtn.hidden = false;
  fileLogBtn.hidden = !logHash();  // 差分からも、このファイルのログへ移れる (作業ツリーに HEAD が無いときは、ログが無い)
  fileCommitLogBtn.hidden = !logHash();
  fileLogBtn.disabled = !logHash();
  fileBlameBtn.hidden = false;  // 差分表示中もこのファイルの blame へ移れる
  fileBlameBtn.disabled = (fileData && fileData.binary) || (isWip && !wipHead);
  fileGraphLogBtn.hidden = true;
  setDocumentTitle(t("file_diff_title", {path: path}));
  title.textContent = t("file_diff_title", {path: path});
  // 変更の一覧 (commitChanges) の読み込み前に URL から開かれても出せるよう、コミットは hash と path だけでも (変更前の名前は分からないが) 開く
  const params = diffParams(path) || (isWip ? null : {hash: hash, path: path});
  if (!params) { content.className = "hint"; content.textContent = t("no_diff"); return; }
  content.className = "";
  content.textContent = "";
  const box = document.createElement("div");
  content.appendChild(box);
  diffView = createDiffEditorView({box: box, text: t, phrases: editorPhrases, loadScript: loadEditorScript, vim: {get: () => vimOn, set: setVimPref}});
  diffView.load(params);
}
function loadFileLog(path) {
  closeEditor();  // 表示のエディタ
  const hash = logHash();
  if (!hash) return;
  fileDiffBtn.hidden = true;
  fileContentBtn.hidden = false;
  fileCommitLogBtn.hidden = !logHash();
  fileLogBtn.hidden = true;
  fileBlameBtn.hidden = true;
  fileGraphLogBtn.hidden = false;
  setDocumentTitle(t("file_history_title", {path: path}));
  title.textContent = t("file_history_title", {path: path});
  showLogList(path, () => currentFile === path && currentView === "log");
}
function loadFileCommitLog() {  // 右ペインに、通常のコミットログ (パスで絞らない。この時点までの全コミット) を出す
  closeEditor();  // 表示のエディタ
  const rev = logHash(), path = currentFile;
  if (!rev) return;
  fileDiffBtn.hidden = true;
  fileContentBtn.hidden = false;
  fileCommitLogBtn.hidden = true;  // 表示中のログのボタンは出さない (ほかの表示と同じ)
  fileLogBtn.hidden = false;
  fileLogBtn.disabled = false;
  fileBlameBtn.hidden = false;
  fileBlameBtn.disabled = (fileData && fileData.binary) || (isWip && !wipHead);
  fileGraphLogBtn.hidden = true;
  setDocumentTitle(t("commit_log_title", {rev: rev.slice(0, 7)}));
  title.textContent = t("commit_log_title", {rev: rev.slice(0, 7)});
  showLogList("", () => currentFile === path && currentView === "clog");
}
let logCtx = null, logPager = null;  // logCtx: 表示中のログ (検索し直すときに使う)、logPager: 続きの読み込み
function showLogList(path, alive, search) {  // 右ペインに git log を出す (path: 空ならパスで絞らない通常のコミットログ)。alive: 別の表示に移っていたら false (古い応答を捨てる)
  // 続きの読み込み (無限スクロール) と検索は、グラフ画面のログと共通 (commit_log.js)
  const rev = logHash();
  if (!rev) return;
  if (logPager) logPager.stop();
  search = CommitLog.normSearch(search);
  logCtx = {path: path, alive: alive};
  content.className = "";
  content.innerHTML = '<div class="log-head"><span class="meta" id="logCount">' + esc(t("loading")) + '</span>' +
    '<button class="sbtn iconbtn log-search-btn' + (search ? " on" : "") + '" title="' + esc(t("search_open")) + '">🔍</button></div>' +
    CommitLog.searchHtml(search) + CommitLog.tableHtml("") + '<div id="logSentinel" class="hint"></div>';
  document.getElementById("main").scrollTop = 0;
  const rows = content.querySelector("table.log tbody"), count = content.querySelector("#logCount"), sentinel = content.querySelector("#logSentinel");
  const searchP = CommitLog.searchParams(search);
  if (search) content.querySelector("#logSearch input").focus({preventScroll: true});
  const pg = logPager = CommitLog.createPager({
    root: document.getElementById("main"), sentinel: sentinel,
    alive: () => logPager === pg && alive(),
    load: skip => Promise.all([api("/api/log", Object.assign({rev: rev, path: path, skip: skip, limit: CommitLog.CHUNK}, searchP)), refsReady]).then(([r]) => r),
    onPage: r => {
      rows.insertAdjacentHTML("beforeend", r.commits.map(c => fileLogRow(c)).join(""));
      count.textContent = CommitLog.countText(search, pg.done, pg.skip, pg.total);
      sentinel.textContent = pg.done ? (pg.skip === 0 ? t("none") : "") : t("loading");
    },
    onError: e => { sentinel.textContent = t("error") + e.message; },
  });
  pg.watch();
}
CommitLog.bindSearch(content, {apply: search => { if (logCtx) showLogList(logCtx.path, logCtx.alive, search); }});
fileCommitLogBtn.onclick = () => { if (currentFile) openView("clog"); };  // 通常のコミットログは右ペインに出す (履歴に積むので、戻るで元の表示へ戻れる)
dirCommitLogBtn.onclick = openCommitLogView;
fileLogBtn.onclick = () => { if (currentFile) openView("log"); };
fileBlameBtn.onclick = () => openView("blame");
fileContentBtn.onclick = () => { if (currentFile) openView(""); };  // 履歴に積むので戻るで差分などへ戻れる
fileGraphLogBtn.onclick = () => {
  const rev = logHash();
  if (rev && currentFile) location.href = "/?file_log_hash=" + encodeURIComponent(rev) + "&file_log_path=" + encodeURIComponent(currentFile);
};
function restoreFromUrl(first) {  // URL の path / file / view の状態を画面に反映する
  const p = new URLSearchParams(location.search), dir = p.get("path") || "", file = p.get("file") || "";
  const view = file ? (p.get("view") || (p.get("edit") === "1" ? "edit" : "")) : "";
  if (!first && editing && (file !== currentFile || view !== "edit")) {  // 編集中に戻る/進む: 破棄しないなら編集画面にとどまる
    if (!leaveEdit()) {
      // 戻ろうとした先が同じファイルの別の表示なら、保存/キャンセルでそこへ戻れるようにする
      history.pushState({path: current, file: currentFile, view: "edit", viaPush: file === currentFile},
                        "", pageUrl(current, currentFile, "edit"));
      return;
    }
  }
  const tree = first || dir !== current ? loadTree(dir, false) : Promise.resolve();
  // WIP ではツリーの読み込みで HEAD が分かってから開く (ログ / blame ボタンの表示に使う)
  return tree.then(() => {
    if (!file) {
      if (currentFile) clearFile();
      const v = p.get("view");  // ファイルを開かない view=log は、ディレクトリのログ。view=clog は、通常のコミットログ
      if (v === "log") showDirLog(); else if (v === "clog") showDirLog(true); else showDir();
      return;
    }
    const from = p.get("dfrom"), to = p.get("dto"), dwip = p.get("dwip");
    diffSel = view === "diff" && from && to
      ? {path: file, from: from, to: to, wip: to === "WIP" ? "compare" : "", old: p.get("dold") || ""}
      : view === "diff" && (dwip || p.get("dold"))
        ? {path: file, wip: dwip || "", old: p.get("dold") || ""} : null;
    const reload = first || file !== currentFile || !view || currentView === "edit" || !fileData;
    return (reload ? loadFile(file, false) : Promise.resolve()).then(() => { if (view) renderView(view); });
  }).then(() => {
    if (p.get("wip_commit") === "1") showWipCommitPane(); else showFilePane();
    const f = afterRestore; afterRestore = null; if (f) f();
  });
}
window.onpopstate = () => restoreFromUrl(false);
// 更新ボタン (作業ツリーの表示だけ): 一覧と開いているファイル / 表示を、いまの作業ツリーから読み直す。
// コミットの内容は変わらないので、コミット表示では出さない。編集中の変更は、捨ててよいか確認してから読み直す
const reloadBtn = document.getElementById("reloadBtn");
reloadBtn.disabled = !isWip || !hash;  // 作業ツリーのときだけ使える (それ以外は無効)
reloadBtn.onclick = () => {
  if (reloadBtn.disabled) return;
  const viewerScroll = textEditor && !editing ? {path: currentFile, top: textEditor.getScrollTop()} : null;  // leaveEdit() が閉じる前に控える
  if (!leaveEdit()) return;
  keepViewerScroll = viewerScroll;
  const treeEl = document.getElementById("tree"), mainEl = document.getElementById("main");
  const keep = {tree: treeEl.scrollTop, main: mainEl.scrollTop};
  reloadBtn.disabled = true;
  stageOverrides.clear();
  stageDirty = false;
  entryInfo = {};
  Promise.resolve(restoreFromUrl(true)).then(refreshWipCommitPanel).finally(() => {
    treeEl.scrollTop = keep.tree; mainEl.scrollTop = keep.main;
    reloadBtn.disabled = false;
  });
};
updateDiffBtn();  // 差分アイコンは、ファイルを開く前から押せる
sideTabs.hidden = !hash;
sideChangesBtn.textContent = t("side_changes");
if (hash) changesEl.innerHTML = '<p class="hint">' + esc(t("loading")) + '</p>';
if (hash) applySideMode();
if (!hash) showError(new Error(t("commit_required"))); else { loadCommitChanges(); restoreFromUrl(true); }

// ---- その他メニューの一覧 (ブランチ / タグ / stash / reflog など) を右ペインに表示。グラフ画面の showRefList 等と同じ API・同じ行 (CommitLog) を使う ----
let otherListSeq = 0;
function showOtherList(kind, titleKey, helpKey, apiPath, params, opts) {
  opts = opts || {};
  closeEditor();
  if (logPager) logPager.stop();
  const seq = ++otherListSeq;
  const alive = () => otherListSeq === seq && !currentFile;  // ファイルを開いたら捨てる (簡易)
  // ファイルを開いていない状態に寄せる
  if (currentFile) clearFile();
  dirLogOn = commitLogOn = false;
  dirActions.hidden = true;
  fileActions.hidden = true;
  title.textContent = t(titleKey);
  setDocumentTitle(t(titleKey));
  content.className = "";
  const top = helpKey ? '<div class="meta">' + esc(t(helpKey)) + "</div>" : "";
  content.innerHTML = top + '<div class="log-head"><span class="meta" id="logCount">' + esc(t("loading")) + '</span></div>' +
    CommitLog.tableHtml("") + '<div id="logSentinel" class="hint"></div>';
  document.getElementById("main").scrollTop = 0;
  const rows = content.querySelector("table.log tbody"), count = content.querySelector("#logCount"), sentinel = content.querySelector("#logSentinel");
  const p0 = Object.assign({}, params || {});
  const pg = logPager = CommitLog.createPager({
    root: document.getElementById("main"), sentinel: sentinel,
    alive: () => logPager === pg && alive(),
    load: skip => api(apiPath, Object.assign({}, p0, {skip: skip, limit: CommitLog.CHUNK})),
    onPage: r => {
      const list = r.commits || r.list || [];
      rows.insertAdjacentHTML("beforeend", list.map(c => {
        if (opts.rowHtml) return opts.rowHtml(c, r);
        return fileLogRow(c);  // グラフと同じ行 (ハッシュリンク + 件名)
      }).join(""));
      count.textContent = CommitLog.countText(null, pg.done, pg.skip, r.total !== undefined ? r.total : pg.total);
      sentinel.textContent = pg.done ? (pg.skip === 0 ? t("none") : "") : t("loading");
    },
    onError: e => { sentinel.textContent = t("error") + e.message; },
  });
  pg.watch();
  // 行クリック: そのコミットのファイル画面へ (グラフでは詳細)
  content.onclick = ev => {
    if (otherListSeq !== seq) return;
    const tr = ev.target.closest("tr.clickable");
    if (!tr || !tr.dataset.h || ev.target.closest("a, .copy")) return;
    if (ev.detail > 1) return;
    location.href = "/files?hash=" + encodeURIComponent(tr.dataset.h);
  };
}
window.showOtherList = showOtherList;
window.showOtherBranchList = () => showOtherList("branches", "branch_list_title", "branch_list_help", "/api/branches", {});
window.showOtherTagList = () => showOtherList("tags", "tag_list_title", "tag_list_help", "/api/tags", {});
window.showOtherStashList = () => showOtherList("stash", "stash_list_title", "stash_list_help", "/api/stash-list", {});
window.showOtherReflog = (branch) => showOtherList("reflog", "reflog", "reflog_help", "/api/reflog", branch ? {branch: branch} : {});

// ---- 操作履歴 (その他メニュー)。グラフの showOpHistory と同じ API ----
window.showOtherOpHistory = function () {
  closeEditor();
  if (logPager) logPager.stop();
  const seq = ++otherListSeq;
  if (currentFile) clearFile();
  dirLogOn = commitLogOn = false;
  dirActions.hidden = true;
  fileActions.hidden = true;
  title.textContent = t("op_history_title");
  setDocumentTitle(t("op_history_title"));
  content.className = "";
  content.innerHTML = '<div class="meta">' + esc(t("op_history_help")) + '</div><div class="hint" id="ophMsg">' + esc(t("loading")) + "</div>";
  document.getElementById("main").scrollTop = 0;
  api("/api/undo-history", {}).then(h => {
    if (otherListSeq !== seq) return;
    const rows = (h.entries || []).map(e => {
      const note = e.done ? "" : " (" + t("op_history_undone") + ")";
      const warn = e.undoable ? "" : ' <span class="hint">' + esc(t("op_history_irreversible", {reason: e.problem || ""})) + "</span>";
      return '<tr class="' + (e.done ? "" : "oph-undone") + '"><td class="h">' + esc(new Date(e.time * 1000).toLocaleString()) +
        "</td><td class=\"log-cell\">" + esc(e.label) + esc(note) + warn + "</td></tr>";
    }).join("");
    const body = rows
      ? '<table class="log"><tbody>' + rows + "</tbody></table>"
      : '<div class="hint">' + esc(t("op_history_empty")) + "</div>";
    content.innerHTML = '<div class="meta">' + esc(t("op_history_help")) + "</div>" + body;
  }).catch(e => {
    if (otherListSeq !== seq) return;
    content.innerHTML = '<div class="hint">' + esc(t("error") + e.message) + "</div>";
  });
};

// ---- submodule 一覧 (その他メニュー)。グラフと同じ API。行クリックで子サーバーを開く ----
function submoduleRowHtmlFiles(c, sm, writable) {
  const sel = writable ? '<td class="sel"><input type="checkbox" class="submodule-sel" data-path="' + esc(sm.path) + '"></td>' : "";
  return '<tr class="submodule-row' + (sm.initialized ? "" : " uninit") + '" data-path="' + esc(sm.path) + '" data-init="' + (sm.initialized ? "1" : "") +
    '" title="' + esc(t(sm.initialized ? "submodule_open_tip" : "submodule_uninit_tip")) + '">' + sel +
    '<td class="h">' + esc(c[1]) + '</td><td class="log-cell"><div class="log-main"><div class="log-subj">' + esc(c[4]) +
    '</div><div class="meta">' + esc(c[2]) + (c[3] ? " / " + esc(c[3]) : "") + "</div></div></td></tr>";
}
window.showOtherSubmoduleList = function () {
  closeEditor();
  if (logPager) logPager.stop();
  const seq = ++otherListSeq;
  if (currentFile) clearFile();
  dirLogOn = commitLogOn = false;
  dirActions.hidden = true;
  fileActions.hidden = true;
  title.textContent = t("submodule_list_title");
  setDocumentTitle(t("submodule_list_title"));
  const writable = !!(typeof CAN_COPY !== "undefined" && CAN_COPY);  // ファイル画面の書き込み可能フラグ
  const bar = writable
    ? '<div class="submodule-bar"><label><input type="checkbox" id="submoduleAll"> ' + esc(t("submodule_select_all")) + "</label> " +
      '<button class="sbtn" id="submoduleUpdateBtn" disabled title="' + esc(t("submodule_update_tip")) + '">' + esc(t("submodule_update")) + "</button> " +
      '<button class="sbtn" id="submoduleUpdateRemoteBtn" disabled title="' + esc(t("submodule_update_remote_tip")) + '">' + esc(t("submodule_update_remote")) + "</button> " +
      '<button class="sbtn" id="submoduleFetchBtn" disabled title="' + esc(t("submodule_fetch_tip")) + '">' + esc(t("submodule_fetch")) + "</button> " +
      '<label title="' + esc(t("submodule_recursive_tip")) + '"><input type="checkbox" id="submoduleRecursive"> ' + esc(t("submodule_recursive")) + "</label></div>" +
      '<div class="meta" id="submoduleMsg"></div>'
    : '<div class="meta" id="submoduleMsg"></div>';
  content.className = "";
  content.innerHTML = '<div class="meta">' + esc(t("submodule_list_help")) + "</div>" + bar +
    '<div class="log-head"><span class="meta" id="logCount">' + esc(t("loading")) + "</span></div>" +
    '<table class="log"><tbody id="smRows"></tbody></table>';
  document.getElementById("main").scrollTop = 0;
  let submoduleBusy = false;
  function syncSel() {
    const all = Array.from(content.querySelectorAll("input.submodule-sel"));
    const n = all.filter(el => el.checked).length;
    for (const id of ["submoduleUpdateBtn", "submoduleUpdateRemoteBtn", "submoduleFetchBtn"]) {
      const b = document.getElementById(id);
      if (b) b.disabled = n === 0 || submoduleBusy;
    }
    const top = document.getElementById("submoduleAll");
    if (top) { top.checked = all.length > 0 && n === all.length; top.indeterminate = n > 0 && n < all.length; }
  }
  function selectedPaths() {
    return Array.from(content.querySelectorAll("input.submodule-sel:checked")).map(el => el.dataset.path);
  }
  function smMsg(html) {
    const el = document.getElementById("submoduleMsg");
    if (el) el.innerHTML = html;
  }
  function recursive() {
    const el = document.getElementById("submoduleRecursive");
    return !!(el && el.checked);
  }
  function runBatch(url, extra, confirmKey, workingKey, doneKey) {
    const paths = selectedPaths();
    if (!paths.length || submoduleBusy) return;
    if (confirmKey && !confirm(t(confirmKey, {n: paths.length}))) return;
    submoduleBusy = true; syncSel();
    smMsg(esc(t(workingKey, {n: paths.length})));
    postJson(url, Object.assign({paths: paths, recursive: recursive()}, extra || {})).then(async r => {
      const failed = (r.results || []).filter(x => !x.ok).map(x => "<div>" + esc(x.path) + ": " + esc(x.error) + "</div>").join("");
      await reloadRows().catch(() => {});
      smMsg('<span style="color:' + (r.ok ? "#080" : "#b00") + '">' + esc(r.message || "") + "</span>" + failed);
    }).catch(e => smMsg('<span style="color:#b00">' + esc(t("error") + e.message) + "</span>"))
      .finally(() => { submoduleBusy = false; syncSel(); });
  }
  async function reloadRows() {
    const r = await api("/api/submodules", {});
    if (otherListSeq !== seq) return;
    const tbody = document.getElementById("smRows");
    if (!tbody) return;
    tbody.innerHTML = (r.commits || []).map((c, i) => submoduleRowHtmlFiles(c, r.submodules[i], writable)).join("");
    const count = document.getElementById("logCount");
    if (count) count.textContent = t("n_changes", {n: r.total != null ? r.total : (r.commits || []).length});
    syncSel();
  }
  api("/api/submodules", {}).then(r => {
    if (otherListSeq !== seq) return;
    const tbody = document.getElementById("smRows");
    tbody.innerHTML = (r.commits || []).map((c, i) => submoduleRowHtmlFiles(c, r.submodules[i], writable)).join("");
    const count = document.getElementById("logCount");
    if (count) count.textContent = t("n_changes", {n: r.total != null ? r.total : (r.commits || []).length});
    syncSel();
  }).catch(e => {
    if (otherListSeq !== seq) return;
    content.innerHTML = '<div class="hint">' + esc(t("error") + e.message) + "</div>";
  });
  let opening = false;
  content.onclick = ev => {
    if (otherListSeq !== seq) return;
    if (ev.target.closest("#submoduleUpdateBtn")) { runBatch("/api/submodule-update", {remote: false}, "submodule_confirm_update", "submodule_updating", "submodule_update_done"); return; }
    if (ev.target.closest("#submoduleUpdateRemoteBtn")) { runBatch("/api/submodule-update", {remote: true}, "submodule_confirm_update_remote", "submodule_updating", "submodule_update_done"); return; }
    if (ev.target.closest("#submoduleFetchBtn")) { runBatch("/api/submodule-fetch", {}, "", "submodule_fetching", "submodule_fetch_done"); return; }
    if (ev.target.id === "submoduleAll") {
      for (const el of content.querySelectorAll("input.submodule-sel")) el.checked = ev.target.checked;
      syncSel(); return;
    }
    if (ev.target.classList && ev.target.classList.contains("submodule-sel")) { syncSel(); return; }
    const tr = ev.target.closest("tr.submodule-row");
    if (!tr || ev.target.closest("td.sel")) return;
    if (!tr.dataset.init) { smMsg(esc(t("submodule_uninit_tip"))); return; }
    if (opening) return;
    opening = true;
    const path = tr.dataset.path;
    smMsg(esc(t("submodule_opening", {path: path})));
    postJson("/api/submodule-open", {path: path, start_path: "/files"}).then(r => {
      let link = "";
      if (r.port) {
        const url = location.protocol + "//" + location.hostname + ":" + r.port + (r.start_path || "/files");
        window.open(url, "_blank");
        link = ' <a href="' + esc(url) + '" target="_blank" rel="noopener">' + esc(url) + "</a>";
      }
      smMsg(esc(r.message || "") + link);
    }).catch(e => smMsg('<span style="color:#b00">' + esc(t("error") + e.message) + "</span>"))
      .finally(() => { opening = false; });
  };
};
