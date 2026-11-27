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
function updateUploadBtn() {  // 「アップロードして上書き」は、作業ツリーのファイルの内容表示だけに出す
  fileUploadBtn.hidden = !CAN_EDIT || !currentFile || (currentView !== "" && currentView !== "content");
  fileMoveBtn.hidden = !CAN_EDIT || !currentFile || editing;  // 名前の変更・移動は、作業ツリーのファイルなら、どの表示でも (編集中は出さない)
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
  // 新規作成は作業ツリーを編集できるときだけ (ログ表示中も、いまのディレクトリに対して作れる)
  const canCreate = isWip && CAN_EDIT && !dirLogOn;
  if (dirNewFileBtn) dirNewFileBtn.hidden = !canCreate;
  if (dirNewDirBtn) dirNewDirBtn.hidden = !canCreate;
  if (dirMoveBtn) dirMoveBtn.hidden = !canCreate || !current;  // ルートは動かせない
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

const MD_EXT = /\.(md|markdown|mdown|mkd|mkdn)$/i;
const HTML_EXT = /\.(html?)$/i;
const MD_PREVIEW_KEY = "gg-md-preview", MD_BREAKS_KEY = "gg-md-breaks", HTML_PREVIEW_KEY = "gg-html-preview", HTML_JS_KEY = "gg-html-js";
let mdPreviewOn = true;  // Markdown 整形表示。ワークツリー設定 > localStorage > オン
let mdBreaksOn = false;
let htmlPreviewOn = true;
let htmlJsOn = false;
(function initPreviewPrefs() {
  function fromLs(key, onVal, offVal, fallback) {
    try {
      const v = localStorage.getItem(key);
      if (v === onVal) return true;
      if (v === offVal) return false;
    } catch (e) { /* ignore */ }
    return fallback;
  }
  const WP = window.WorktreePrefs;
  mdPreviewOn = (WP && WP.has("md_preview")) ? WP.getBool("md_preview", true) : fromLs(MD_PREVIEW_KEY, "1", "0", true);
  mdBreaksOn = (WP && WP.has("md_breaks")) ? WP.getBool("md_breaks", false) : fromLs(MD_BREAKS_KEY, "1", "0", false);
  htmlPreviewOn = (WP && WP.has("html_preview")) ? WP.getBool("html_preview", true) : fromLs(HTML_PREVIEW_KEY, "1", "0", true);
  htmlJsOn = (WP && WP.has("html_js")) ? WP.getBool("html_js", false) : fromLs(HTML_JS_KEY, "1", "0", false);
})();
function setMdPreviewPref(on) {
  mdPreviewOn = on;
  try { localStorage.setItem(MD_PREVIEW_KEY, mdPreviewOn ? "1" : "0"); } catch (e) { /* ignore */ }
  if (window.WorktreePrefs) WorktreePrefs.setBool("md_preview", mdPreviewOn);
}
function setMdBreaksPref(on) {
  mdBreaksOn = on;
  try { localStorage.setItem(MD_BREAKS_KEY, mdBreaksOn ? "1" : "0"); } catch (e) { /* ignore */ }
  if (window.WorktreePrefs) WorktreePrefs.setBool("md_breaks", mdBreaksOn);
}
function setHtmlPreviewPref(on) {
  htmlPreviewOn = on;
  try { localStorage.setItem(HTML_PREVIEW_KEY, htmlPreviewOn ? "1" : "0"); } catch (e) { /* ignore */ }
  if (window.WorktreePrefs) WorktreePrefs.setBool("html_preview", htmlPreviewOn);
}
function setHtmlJsPref(on) {
  htmlJsOn = on;
  try { localStorage.setItem(HTML_JS_KEY, htmlJsOn ? "1" : "0"); } catch (e) { /* ignore */ }
  if (window.WorktreePrefs) WorktreePrefs.setBool("html_js", htmlJsOn);
}
function htmlSandboxAttr() {  // sandbox 属性の値。script を動かすときは allow-same-origin を外す
  // (allow-scripts + allow-same-origin の組合せは、script が自分で sandbox を外せるので親ページに触れてしまう)
  return htmlJsOn ? "allow-scripts" : "allow-same-origin";
}
function isMarkdownPath(path) {
  return MD_EXT.test(path || "");
}
function isHtmlPath(path) {
  return HTML_EXT.test(path || "");
}
let markedReady = false;
function ensureMarked() {
  if (markedReady || !window.marked || typeof marked.use !== "function") return;
  // 生の HTML タグは出さない (リポジトリ内の .md でも XSS を抑える)
  marked.use({ renderer: { html() { return ""; } } });
  markedReady = true;
}
function mdViewBarHtml() {
  return '<div class="view-bar">' +
    '<label class="edit-vim" title="' + esc(t("md_preview_tip")) + '"><input type="checkbox" id="viewMdPreview"' +
    (mdPreviewOn ? " checked" : "") + '> ' + esc(t("md_preview")) + '</label>' +
    '<label class="edit-vim" title="' + esc(t("md_breaks_tip")) + '"' + (mdPreviewOn ? "" : " hidden") +
    ' id="viewMdBreaksLabel"><input type="checkbox" id="viewMdBreaks"' +
    (mdBreaksOn ? " checked" : "") + '> ' + esc(t("md_breaks")) + '</label></div>';
}
function bindMdViewBar(r) {
  const prev = document.getElementById("viewMdPreview");
  const brk = document.getElementById("viewMdBreaks");
  const brkLab = document.getElementById("viewMdBreaksLabel");
  if (prev) prev.onchange = ev => {
    setMdPreviewPref(ev.target.checked);
    showFileBody(r);  // プレビュー / テキストを切り替える
  };
  if (brk) brk.onchange = ev => {
    setMdBreaksPref(ev.target.checked);
    if (mdPreviewOn) showMarkdown(r);
  };
  if (brkLab) brkLab.hidden = !mdPreviewOn;
}
function renderMarkdownHtml(src) {  // Markdown 文字列 → HTML (失敗時はエスケープした pre)
  src = src == null ? "" : String(src);
  try {
    ensureMarked();
    if (window.marked && typeof marked.parse === "function") {
      return marked.parse(src, { async: false, breaks: !!mdBreaksOn });
    }
  } catch (e) { /* fall through */ }
  return "<pre>" + esc(src) + "</pre>";
}
function resolveMdRepoPath(fromFile, href) {
  // Markdown の相対リンクをリポジトリ内パスにする。外部 URL や # だけなら null
  if (href == null) return null;
  href = String(href).trim();
  if (!href || /^(https?:|mailto:|data:|javascript:)/i.test(href)) return null;
  if (href.charAt(0) === "#") return null;
  let path = href.split("#")[0].split("?")[0];
  if (!path) return null;
  try { path = decodeURIComponent(path); } catch (e) { /* keep raw */ }
  if (path.charAt(0) === "/") path = path.slice(1);
  const base = (fromFile && fromFile.indexOf("/") >= 0) ? fromFile.slice(0, fromFile.lastIndexOf("/") + 1) : "";
  const parts = (base + path).split("/");
  const out = [];
  for (let i = 0; i < parts.length; i++) {
    const p = parts[i];
    if (!p || p === ".") continue;
    if (p === "..") {
      if (!out.length) return null;  // リポジトリの外へは出さない
      out.pop();
      continue;
    }
    if (p.indexOf("\\") >= 0 || p.indexOf("\0") >= 0) return null;
    out.push(p);
  }
  return out.join("/");
}
function mdFilePageUrl(repoPath) {
  const dir = parentDir(repoPath);
  return pageUrl(dir, repoPath);
}
function bindMarkdownRepoLinks(root, fromFile) {
  // 相対リンクを /files?...&file=... にし、クリックで同じ画面内で開く
  if (!root) return;
  root.querySelectorAll("a[href]").forEach(a => {
    const raw = a.getAttribute("href");
    const repoPath = resolveMdRepoPath(fromFile || currentFile, raw);
    if (!repoPath) {
      if (raw && /^(https?:)/i.test(raw)) a.setAttribute("target", "_blank");
      return;
    }
    const frag = (raw.indexOf("#") >= 0) ? raw.slice(raw.indexOf("#")) : "";
    a.setAttribute("href", mdFilePageUrl(repoPath) + frag);
    a.setAttribute("data-md-file", repoPath);
    a.onclick = ev => {
      if (ev.defaultPrevented || ev.button || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
      ev.preventDefault();
      if (!leaveEdit()) return;
      const dir = parentDir(repoPath);
      const open = () => loadFile(repoPath, true);
      if (dir !== current) loadTree(dir, false).then(open);
      else open();
    };
  });
  // 相対パスの画像は /raw で出す
  root.querySelectorAll("img[src]").forEach(img => {
    const repoPath = resolveMdRepoPath(fromFile || currentFile, img.getAttribute("src"));
    if (!repoPath) return;
    img.setAttribute("src", "/raw?" + new URLSearchParams({hash: hash, path: repoPath}).toString());
  });
}
function htmlViewBarHtml() {
  return '<div class="view-bar">' +
    '<label class="edit-vim" title="' + esc(t("html_preview_tip")) + '"><input type="checkbox" id="viewHtmlPreview"' +
    (htmlPreviewOn ? " checked" : "") + '> ' + esc(t("html_preview")) + '</label>' +
    '<label class="edit-vim" title="' + esc(t("html_preview_js_tip")) + '"' + (htmlPreviewOn ? "" : " hidden") +
    ' id="viewHtmlJsLabel"><input type="checkbox" id="viewHtmlJs"' +
    (htmlJsOn ? " checked" : "") + '> ' + esc(t("html_preview_js")) + '</label></div>';
}
function bindHtmlViewBar(r) {
  const prev = document.getElementById("viewHtmlPreview");
  const js = document.getElementById("viewHtmlJs");
  const jsLab = document.getElementById("viewHtmlJsLabel");
  if (prev) prev.onchange = ev => {
    setHtmlPreviewPref(ev.target.checked);
    showFileBody(r);
  };
  if (js) js.onchange = ev => {
    setHtmlJsPref(ev.target.checked);
    if (htmlPreviewOn) showHtml(r);  // sandbox を付け替えてプレビューを作り直す
  };
  if (jsLab) jsLab.hidden = !htmlPreviewOn;
}
function fitHtmlPreviewFrame(frame) {
  if (!frame) return;
  const main = document.getElementById("main");
  const top = frame.getBoundingClientRect().top - main.getBoundingClientRect().top + main.scrollTop;
  frame.style.height = Math.max(200, main.clientHeight - top - 14 - 2) + "px";
}
function showHtml(r) {  // HTML は sandbox iframe でプレビュー (script はオプションで有効にする)
  closeEditor();
  content.className = "html-preview";
  content.innerHTML = htmlViewBarHtml() +
    '<div class="html-body"><iframe class="html-preview-frame" sandbox="' + htmlSandboxAttr() + '" title="HTML preview"></iframe></div>';
  bindHtmlViewBar(r);
  const frame = content.querySelector("iframe.html-preview-frame");
  fitHtmlPreviewFrame(frame);
  frame.srcdoc = (r && r.content != null) ? r.content : "";
  frame.onload = () => {
    try {
      const doc = frame.contentDocument;
      if (doc && doc.body) bindMarkdownRepoLinks(doc.body, r.path || currentFile);
    } catch (e) { /* sandbox */ }
  };
  restoreMainScroll(r.path || currentFile);
}
function showMarkdown(r) {  // Markdown は marked.js で HTML 化してプレビュー (編集は元テキスト)
  closeEditor();
  const src = (r && r.content != null) ? r.content : "";
  content.className = "md-preview";
  content.innerHTML = mdViewBarHtml() + '<div class="md-body">' + renderMarkdownHtml(src) + "</div>";
  bindMdViewBar(r);
  bindMarkdownRepoLinks(content.querySelector(".md-body"), r.path || currentFile);
  restoreMainScroll(r.path || currentFile);
}
function showFileBody(r) {  // 内容表示: Markdown / HTML / PDF / 画像ならプレビュー
  if (r.pdf) return showPdf(r);
  if (r.image) return showImage(r);
  if (r.binary) { content.className = "binary"; content.textContent = t("binary_file"); return; }
  const path = r.path || currentFile;
  if (isMarkdownPath(path) && mdPreviewOn) return showMarkdown(r);
  if (isHtmlPath(path) && htmlPreviewOn) return showHtml(r);
  if (isMarkdownPath(path) || isHtmlPath(path)) {
    // テキスト表示でもプレビュー切替バーを出す
    showCode(r, isMarkdownPath(path) ? "md" : "html");
    return;
  }
  showCode(r, false);
}

function showPdf(r) {  // PDF はブラウザのビューアでプレビュー (中身は /raw。Range 対応)
  closeEditor();
  const params = new URLSearchParams({hash: r.hash || hash, path: r.path || currentFile});
  content.className = "pdf-preview";
  content.innerHTML = '<iframe class="pdf-preview-frame" src="/raw?' + params.toString() +
    '" title="' + esc(r.path || currentFile) + '"></iframe>';
  const frame = content.querySelector("iframe.pdf-preview-frame");
  if (typeof fitHtmlPreviewFrame === "function") fitHtmlPreviewFrame(frame);
}
function showImage(r) {  // 画像ファイルは <img> でプレビュー (中身は /raw から)
  closeEditor();
  const params = new URLSearchParams({hash: r.hash || hash, path: r.path || currentFile});
  content.className = "image-preview";
  content.innerHTML = '<img src="/raw?' + params.toString() + '" alt="' + esc(r.path || currentFile) + '">';
}

function loadFile(path, push) {
  if (!leaveEdit()) return;
  if (path !== currentFile) {
    saveFileScroll();  // 別ファイルへ移る前に、今見ていた位置を覚える
    // currentFile を変える前にエディタを閉じる (closeEditor が新しい path に誤って保存しないように)
    if (typeof closeEditor === "function") closeEditor();
  }
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
  updateUploadBtn();
  updateDiffBtn();
  fileData = null;
  return api("/api/file", {hash: hash, path: path}).then(r => {
    if (path !== currentFile) return;
    fileData = r;
    fileEditBtn.hidden = false;
    fileEditBtn.disabled = !CAN_EDIT || r.binary || !r.editable;
    fileEditBtn.title = (!CAN_EDIT || r.binary) ? "" : (r.editable ? "" : t(r.edit_reason || "edit_bad_encoding"));
    title.textContent = fileTitle(r, "");
    showFileBody(r);
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
  updateUploadBtn();
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
    showFileBody(fileData);
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
