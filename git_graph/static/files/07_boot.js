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
