/* ---- files/05_dir_file.js ---- */
function setFileMsg(text, ok) { setStatus(text || "", ok === false ? "error" : (ok === true ? "success" : "info"), ok === false); }
function defaultCopyName(path) {  // 例: src/a.py → src/a_1234567.py
  const i = path.lastIndexOf("/"), dir = path.slice(0, i + 1), name = path.slice(i + 1), dot = name.lastIndexOf(".");
  const suffix = "_" + hash.slice(0, 7);
  return dir + (dot > 0 ? name.slice(0, dot) + suffix + name.slice(dot) : name + suffix);
}
fileCopyBtn.onclick = () => {
  if (!currentFile) return;
  const dest = prompt(t("prompt_copy_dest"), defaultCopyName(currentFile));
  if (dest === null || !dest.trim()) return;
  fetch("/api/file-copy", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({hash: hash, path: currentFile, dest: dest.trim()})})
    .then(r => r.ok ? r.json() : r.text().then(m => { throw new Error(m || r.status); }))
    .then(r => setFileMsg(r.message, true))
    .catch(e => setFileMsg(e.message, false));
};
function clearFile() {  // ファイルを選ぶ前の表示に戻す
  closeEditor();
  currentFile = ""; currentView = "";
  setDocumentTitle("");
  fileActions.hidden = true;
  title.textContent = t("select_file");
  content.className = "hint";
  content.textContent = t("select_file_hint");
}
function fileLogRow(c, graphTd) {  // ログの 1 行。グラフ画面のログと同じ行 (commit_log.js): (マージグラフ +) ハッシュ (グラフ画面で開くリンク)・(ブランチ / タグ +) 件名・作者 / 日付。操作の対象 / A / B の行は強調する (log_select.js)
  return CommitLog.rowHtml(c, {extraClass: LogSel.kinds(c[0], LogSel.load()), chips: refChips(c[0]), graphLink: true, graphTd: graphTd});
}
LogSel.onChange(sel => {  // メニューで対象 / A / B を変えたら、表示中のログの強調も付け直す。対象がマージコミットなら、マージグラフでその線を全部描く
  LogSel.markRows(content, sel);
  if (logGraphCur) logGraphCur.sync();
});
function dirLogCells(c) {  // 最終コミットのセル (ハッシュ・件名・日付)。c が無ければ空 (まだ読み込み中 / 履歴に見つからない)
  if (!c) return '<td class="hash"></td><td class="dir-msg"></td><td class="date"></td>';
  return '<td class="hash"><a class="commit-graph" href="/?commit_hash=' + encodeURIComponent(c[0]) + '">' + esc(c[1]) + '</a></td><td class="dir-msg" title="' + esc(c[4]) + '">' +
    esc(c[4]) + '</td><td class="date">' + esc(c[3]) + '</td>';
}
function dirNameCell(e, path) {
  return '<td class="dir-name"' + entryTip(e) + '>' + entryIcon(e) + ' <span class="bd">' + statusBadge(e, path) + '</span>' + esc(e.name) + submoduleBadge(e) + '</td>';
}
function setDirActions() {  // ディレクトリを表示しているときの操作ボタン (項目の一覧 ↔ このディレクトリのログ)
  dirActions.hidden = !!currentFile;
  const canLog = !!logHash();
  dirCommitLogBtn.hidden = commitLogOn || !canLog;  // 通常のコミットログ (パスで絞らない)。ディレクトリのログからも切り替えられる
  dirLogBtn.hidden = (dirLogOn && !commitLogOn) || !canLog;
  dirListBtn.hidden = !dirLogOn;
  dirGraphLogBtn.hidden = !dirLogOn || commitLogOn || !canLog || !current;  // ルートは、グラフ画面のファイルログの対象にならない
}

function pathCrumbHtml(path) {
  const parts = path ? path.split("/") : [];
  let html = '<span class="crumb"><a data-dir="">📁 ' + t("root") + '</a>';
  parts.forEach((p, i) => {
    const d = parts.slice(0, i + 1).join("/");
    html += ' / <a data-dir="' + esc(d) + '">' + esc(p) + '</a>';
  });
  return html + '</span>';
}
function bindPathCrumb(el) {
  el.querySelectorAll("a[data-dir]").forEach(a => {
    a.onclick = (ev) => { ev.preventDefault(); openDir(a.dataset.dir); };
  });
}

function showDir() {  // ファイルを開いていないとき、ディレクトリ内の一覧を各項目の最終コミットつきで本文に出す
  showFilePane();
  closeEditor();
  if (dirEntries === null || currentFile) return;
  dirLogOn = commitLogOn = false;
  setDirActions();
  const seq = ++dirLogSeq, path = current, entries = dirEntries;
  if (treeLogAbort) treeLogAbort.abort();
  setDocumentTitle(path || t("root"));
  title.innerHTML = pathCrumbHtml(path);
  bindPathCrumb(title);
  if (!entries.length) { content.className = "hint"; content.textContent = t("empty_directory"); return; }
  content.className = "";
  content.innerHTML = '<table class="dir-log"><thead><tr><th class="dir-name">' + esc(t("dir_log_name")) + '</th><th class="hash">' + esc(t("dir_log_commit")) +
    '</th><th class="dir-msg">' + esc(t("dir_log_message")) + '</th><th class="date">' + esc(t("dir_log_date")) + '</th></tr></thead><tbody>' +
    entries.map(e => '<tr class="dir-log-row" data-type="' + e.type + '" data-name="' + esc(e.name) + '" data-path="' +
      esc(path ? path + "/" + e.name : e.name) + '">' + dirNameCell(e, path ? path + "/" + e.name : e.name) + dirLogCells(null) + '</tr>').join("") + '</tbody></table><div id="dirLogNote"></div>';
  // 各項目の最終コミットは、見つかった行から順に埋める (古い項目を探すのに時間がかかっても、新しい項目はすぐ出る)
  const ctl = treeLogAbort = new AbortController();
  const byName = {};
  entries.forEach(e => { byName[e.name] = e; });
  const rowOf = {};
  content.querySelectorAll("tr.dir-log-row").forEach(row => { rowOf[row.dataset.name] = row; });
  const fill = (name, c) => {
    const row = rowOf[name];
    if (row) row.innerHTML = dirNameCell(byName[name], row.dataset.path) + dirLogCells(c);
  };
  apiStream("/api/tree-log", {hash: hash, path: path}, ev => {
    if (seq !== dirLogSeq || currentFile) { ctl.abort(); return; }  // 別のディレクトリ / ファイルに移った
    Object.keys(ev.commits || {}).forEach(name => fill(name, ev.commits[name]));
    if (ev.complete === false) document.getElementById("dirLogNote").innerHTML = '<p class="hint">' + esc(t("dir_log_incomplete")) + '</p>';
  }, ctl.signal).catch(e => {
    if (ctl.signal.aborted) return;
    if (seq === dirLogSeq && !currentFile) { content.className = "hint"; content.textContent = t("error") + e.message; }
  });
}
function showDirLog(all) {  // ファイルを開いていないとき、このディレクトリの git log を本文に出す (all: パスで絞らない通常のコミットログ)
  closeEditor();
  const rev = logHash();
  if (currentFile || !rev) { showDir(); return; }  // 作業ツリーに HEAD が無いときは、ログが無い
  const seq = ++dirLogSeq, path = current, label = path || t("root");
  if (treeLogAbort) treeLogAbort.abort();
  dirLogOn = true; commitLogOn = !!all;
  setDirActions();
  if (all) {
    setDocumentTitle(t("commit_log_title", {rev: rev.slice(0, 7)}));
    title.textContent = t("commit_log_title", {rev: rev.slice(0, 7)});
    showLogList("", () => seq === dirLogSeq && !currentFile && dirLogOn && commitLogOn);
    return;
  }
  setDocumentTitle(t("file_history_title", {path: label}));
  const histSuffix = t("file_history_title", {path: ""}).replace("{path}", "").trim();
  title.innerHTML = pathCrumbHtml(path) + (histSuffix ? " " + esc(histSuffix) : "");
  bindPathCrumb(title);
  showLogList(path, () => seq === dirLogSeq && !currentFile && dirLogOn && !commitLogOn);
}
function openDirLogView(all) {  // 「このディレクトリのログ」「ログ」: 履歴に積んで出す (ブラウザーの戻るで一覧に戻れる)。ログ同士の切り替えは履歴を積み増さない
  if (currentFile || !logHash() || (dirLogOn && !!all === commitLogOn)) return;
  const state = {path: current, dirlog: true, viaPush: true}, url = pageUrl(current, "", "", all ? "clog" : true);
  if (dirLogOn) history.replaceState(state, "", url); else history.pushState(state, "", url);
  showDirLog(all);
}
function openDirLog() { openDirLogView(false); }
function openCommitLogView() { openDirLogView(true); }
function backToDirList() {  // ディレクトリの一覧に戻る。前の履歴が同じディレクトリのログ表示なら、そこへ戻る
  if (history.state && history.state.viaPush && history.state.dirlog) { history.back(); return; }
  history.replaceState({path: current}, "", pageUrl(current, ""));
  showDir();
}
dirLogBtn.onclick = openDirLog;
dirListBtn.onclick = backToDirList;
dirGraphLogBtn.onclick = () => {
  const rev = logHash();
  if (rev && current) location.href = "/?file_log_hash=" + encodeURIComponent(rev) + "&file_log_path=" + encodeURIComponent(current);
};
function openDir(path) {  // ディレクトリを選ぶ: 開いているファイルは閉じて、ディレクトリの一覧を本文に出す
  if (!leaveEdit()) return;
  showFilePane();
  if (currentFile) clearFile();
  loadTree(path, true).then(showDir);
}
function showImage(r) {  // 画像ファイルは <img> でプレビュー (中身は /raw から)
  closeEditor();
  const params = new URLSearchParams({hash: r.hash || hash, path: r.path || currentFile});
  content.className = "image-preview";
  content.innerHTML = '<img src="/raw?' + params.toString() + '" alt="' + esc(r.path || currentFile) + '">';
}

function loadFile(path, push) {
  if (!leaveEdit()) return;
  showFilePane();
  if (push && (path !== currentFile || currentView)) history.pushState({path: current, file: path}, "", pageUrl(current, path));
  currentFile = path; currentView = "";
  if (diffSel && diffSel.path !== path) diffSel = null;
  syncChangesSelection();
  markWipCurrent();
  dirLogOn = commitLogOn = false; dirActions.hidden = true;
  setDocumentTitle(path);
  setFileMsg("", true);
  fileActions.hidden = false;
  fileDiffBtn.hidden = false;  // 内容表示中は差分ボタン (内容ボタンは出さない)
  fileContentBtn.hidden = true;
  fileEditBtn.hidden = false;
  fileEditBtn.disabled = true;  // 内容取得後に更新
  fileExtEditBtn.hidden = false;
  fileExtEditBtn.disabled = !(DESKTOP && isWip);  // pywebview などでは、作業ツリーのファイルを外部エディタで開ける
  fileLogBtn.hidden = !logHash();
  fileCommitLogBtn.hidden = !logHash();
  fileLogBtn.disabled = !logHash();
  fileBlameBtn.hidden = false;
  fileBlameBtn.disabled = isWip && !wipHead;
  fileGraphLogBtn.hidden = true;
  fileDownloadBtn.hidden = false;
  fileDownloadBtn.disabled = false;
  fileCopyBtn.disabled = !CAN_COPY || isWip;
  updateDiffBtn();
  fileData = null;
  return api("/api/file", {hash: hash, path: path}).then(r => {
    if (path !== currentFile) return;
    fileData = r;
    fileEditBtn.hidden = false;
    fileEditBtn.disabled = !CAN_EDIT || r.binary || !r.editable;
    fileEditBtn.title = (!CAN_EDIT || r.binary) ? "" : (r.editable ? "" : t(r.edit_reason || "edit_bad_encoding"));
    title.textContent = fileTitle(r, "");
    if (r.image) showImage(r);
    else if (r.binary) { content.className = "binary"; content.textContent = t("binary_file"); }
    else showCode(r);
    fileBlameBtn.hidden = false;
    fileBlameBtn.disabled = r.binary || (isWip && !wipHead);
  }).catch(e => { title.textContent = path; content.className = "hint"; content.textContent = t("error") + e.message; });
}
// ---- ファイルごとの表示 (内容 / 編集 / blame / ログ) ----
// 表示を切り替えるたびに履歴に積む。viaPush: 同じファイルの前の表示から来た (「ファイル内容」などは履歴を戻る)
let currentView = "", afterRestore = null;
function renderView(view) {
  currentView = view;
  updateDiffBtn();
  if (view === "edit") {
    if (!CAN_EDIT) setFileMsg(t("write_disabled"), false);
    else if (fileData && fileData.editable) startEdit();
    else if (fileData) setFileMsg(t(fileData.edit_reason || "edit_bad_encoding"), false);
  } else if (view === "blame") loadFileBlame(currentFile);
  else if (view === "log") loadFileLog(currentFile);
  else if (view === "clog") loadFileCommitLog();
  else if (view === "diff") loadFileDiff(currentFile);
  else showFileContentView();  // ファイル内容 (履歴に積んで戻れる)
  markWipCurrent();
}
function showFileContentView() {  // 差分 / ログ / blame から内容表示へ (API 再取得なし)
  closeEditor();
  fileDiffBtn.hidden = false;
  fileContentBtn.hidden = true;
  fileEditBtn.hidden = false;
  fileExtEditBtn.hidden = false;
  fileExtEditBtn.disabled = !(DESKTOP && isWip);
  fileLogBtn.hidden = !logHash();
  fileCommitLogBtn.hidden = !logHash();
  fileLogBtn.disabled = !logHash();
  fileBlameBtn.hidden = false;
  fileBlameBtn.disabled = (fileData && fileData.binary) || (isWip && !wipHead);
  fileGraphLogBtn.hidden = true;
  fileDownloadBtn.hidden = false;
  fileDownloadBtn.disabled = false;
  fileCopyBtn.disabled = !CAN_COPY || isWip;
  setDocumentTitle(currentFile || "");
  if (fileData) {
    fileEditBtn.disabled = !CAN_EDIT || fileData.binary || !fileData.editable;
    fileEditBtn.title = (!CAN_EDIT || fileData.binary) ? "" : (fileData.editable ? "" : t(fileData.edit_reason || "edit_bad_encoding"));
    title.textContent = fileTitle(fileData, "");
    if (fileData.image) showImage(fileData);
    else if (fileData.binary) { content.className = "binary"; content.textContent = t("binary_file"); }
    else showCode(fileData);
  } else if (currentFile) {
    loadFile(currentFile, false);
    return;
  }
  updateDiffBtn();
  updateRmBtns();
}
function openView(view) {
  if (!currentFile || view === currentView) return;
  if (!leaveEdit()) return;
  history.pushState({path: current, file: currentFile, view: view, viaPush: true}, "", pageUrl(current, currentFile, view));
  if (currentView === "edit" || !fileData) loadFile(currentFile, false).then(() => renderView(view));  // 編集欄を閉じて内容を出し直す
  else renderView(view);
}
function backToContent(after) {  // ファイル内容の表示に戻る。前の履歴が同じファイルならそこへ戻る
  if (history.state && history.state.viaPush) { afterRestore = after || null; history.back(); return; }
  history.replaceState({path: current, file: currentFile}, "", pageUrl(current, currentFile));
  const done = loadFile(currentFile, false);
  if (after && done) done.then(after);
}
