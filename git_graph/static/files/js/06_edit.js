/* ---- files/06_edit.js ---- */
// ---- 編集 (作業ツリーのみ) ----
// 編集欄では改行が LF になるので、保存時にサーバー側で元の改行コード・文字コード・BOM に戻す
let fileData = null, editing = false, editDirty = false, textEditor = null;
const VIM_KEY = "gitGraphEditorVim";
const WRAP_KEY = "gitGraphEditorWrap";
let vimOn = false;  // Vim モード。ワークツリー設定 > localStorage > 既定
let wrapOn = true;  // 折り返し。ワークツリー設定 > localStorage > 既定
(function initEditorPrefs() {
  if (window.WorktreePrefs && WorktreePrefs.has("editor_vim")) vimOn = WorktreePrefs.getBool("editor_vim", false);
  else try { vimOn = localStorage.getItem(VIM_KEY) === "1"; } catch (e) { vimOn = false; }
  if (window.WorktreePrefs && WorktreePrefs.has("editor_wrap")) wrapOn = WorktreePrefs.getBool("editor_wrap", true);
  else try {
    const w = localStorage.getItem(WRAP_KEY);
    if (w === "0") wrapOn = false; else if (w === "1") wrapOn = true;
  } catch (e) { wrapOn = true; }
})();
function setVimPref(on) {  // Vim モードの設定 (表示・編集・差分で共通)。ワークツリーの .git/git-graph.json にも残す
  vimOn = on;
  try { localStorage.setItem(VIM_KEY, vimOn ? "1" : "0"); } catch (e) { /* 保存できなくても切り替わる */ }
  if (window.WorktreePrefs) WorktreePrefs.setBool("editor_vim", vimOn);
}
function setWrapPref(on) {  // 折り返しの設定 (表示・編集・差分で共通)
  wrapOn = on;
  try { localStorage.setItem(WRAP_KEY, wrapOn ? "1" : "0"); } catch (e) { /* 保存できなくても切り替わる */ }
  if (window.WorktreePrefs) WorktreePrefs.setBool("editor_wrap", wrapOn);
}
const EOL_LABEL = {crlf: "CRLF", lf: "LF", cr: "CR"};
let diffView = null;  // 右ペインの差分エディタ (view=diff)
function closeEditor() {
  saveFileScroll();
  if (mdScrollCleanup) { mdScrollCleanup(); mdScrollCleanup = null; }
  if (textEditor) { textEditor.destroy(); textEditor = null; }
  if (diffView) { diffView.close(); diffView = null; }
}
function fileTitle(r, lang) { return r.path + (lang ? " (" + lang + ")" : "") + (r.encoding ? " [" + r.encoding + "]" : ""); }
function fitEditor(host) {  // エディタの高さを本文の領域に合わせる (ページではなくエディタの中でスクロールさせる)
  const main = document.getElementById("main");
  const top = host.getBoundingClientRect().top - main.getBoundingClientRect().top + main.scrollTop;
  host.style.height = Math.max(200, main.clientHeight - top - 14 - 2) + "px";  // 14: #main の下の余白、2: 枠線
}
window.addEventListener("resize", () => { if (document.getElementById("mdEditPreview") || document.getElementById("htmlEditPreview")) fitMdEditSplit(); else if (document.querySelector("iframe.html-preview-frame, iframe.pdf-preview-frame")) fitHtmlPreviewFrame(document.querySelector("iframe.html-preview-frame, iframe.pdf-preview-frame")); else { const host = document.querySelector(".cm-host"); if (host) fitEditor(host); } });
let keepViewerScroll = null;  // 更新ボタンで読み直すとき、表示のスクロール位置を引き継ぐ ({path, top})
// ファイルごとに内容表示・編集のスクロール位置を覚える (開き直したとき 1 行目に戻さない)
const fileScrollPos = Object.create(null);  // path -> number (CM scrollTop), path+":edit" -> number, path+":main" -> main.scrollTop
function scrollKey(path, kind) {
  if (!path) return "";
  if (kind === "edit") return path + ":edit";
  if (kind === "main") return path + ":main";
  return path;
}
function saveFileScroll() {
  if (!currentFile) return;
  try {
    if (textEditor) {
      const top = textEditor.getScrollTop();
      fileScrollPos[scrollKey(currentFile, editing ? "edit" : "")] = top;
    } else {
      const main = document.getElementById("main");
      if (main) fileScrollPos[scrollKey(currentFile, "main")] = main.scrollTop;
    }
  } catch (e) { /* ignore */ }
}
function restoreCmScroll(path, isEdit) {
  // keepViewerScroll (更新) を優先し、なければファイルごとの記憶。#L は jumpToHashLine 側
  if (keepViewerScroll && keepViewerScroll.path === path) {
    if (textEditor) textEditor.setScrollTop(keepViewerScroll.top);
    keepViewerScroll = null;
    return true;
  }
  const key = scrollKey(path, isEdit ? "edit" : "");
  if (key in fileScrollPos && textEditor) {
    textEditor.setScrollTop(fileScrollPos[key]);
    return true;
  }
  return false;
}
function restoreMainScroll(path) {
  const key = scrollKey(path, "main");
  if (!(key in fileScrollPos)) return;
  const main = document.getElementById("main");
  if (main) main.scrollTop = fileScrollPos[key];
}
function jumpToHashLine() {  // URL の #L12 で、その行を出す
  const m = /^#L(\d+)$/.exec(location.hash);
  if (m && textEditor && !editing) textEditor.scrollToLine(parseInt(m[1], 10));
}
window.addEventListener("hashchange", jumpToHashLine);
function showCode(r, previewKind) {  // ファイルの内容を、読み取り専用のエディタで出す (編集画面と同じ CodeMirror。行番号・構文ハイライト・検索 Ctrl+F)
  // previewKind: "md" / "html" のとき、テキスト側でもプレビュー切替を出す
  content.className = "";
  const bar = previewKind === "md" ? mdViewBarHtml() : (previewKind === "html" ? htmlViewBarHtml() : (previewKind ? mdViewBarHtml() : ""));
  content.innerHTML = (r.truncated ? '<p class="hint">' + esc(t("large_file_head")) + '</p>' : "") + bar +
    '<div class="view-bar"><label class="edit-vim" title="' + esc(t("edit_wrap_tip")) + '"><input type="checkbox" id="viewWrap"' + (wrapOn ? " checked" : "") +
    '> ' + esc(t("edit_wrap")) + '</label><label class="edit-vim" title="' + esc(t("vim_readonly_tip")) + '"><input type="checkbox" id="viewVim"' + (vimOn ? " checked" : "") +
    '> ' + esc(t("edit_vim")) + '</label></div><div class="cm-host" id="viewer"></div>';
  document.getElementById("main").scrollTop = 0;
  const host = document.getElementById("viewer");
  if (previewKind === "md" || previewKind === true) bindMdViewBar(r);
  if (previewKind === "html") bindHtmlViewBar(r);
  document.getElementById("viewWrap").onchange = ev => {
    setWrapPref(ev.target.checked);
    if (textEditor) { textEditor.setWrap(wrapOn); textEditor.focus(); }
  };
  document.getElementById("viewVim").onchange = ev => {  // Vim モード (移動・検索・コピーのキー)。編集画面と同じ設定
    setVimPref(ev.target.checked);
    if (textEditor) { textEditor.setVim(vimOn); textEditor.focus(); }
  };
  fitEditor(host);
  loadEditorScript().then(() => {
    if (fileData !== r || currentView !== "" || !host.isConnected) return;  // 読み込んでいる間に、別の表示に移った
    closeEditor();
    textEditor = GGText.createTextEditor({parent: host, text: r.content, path: r.path, readOnly: true, vim: vimOn, wrap: wrapOn, phrases: editorPhrases()});
    title.textContent = fileTitle(r, textEditor.language);
    if (!restoreCmScroll(r.path || currentFile, false)) jumpToHashLine();
    else if (/^#L\d+$/.test(location.hash)) jumpToHashLine();  // #L 指定があれば記憶より優先
  }).catch(e => {  // エディタを読み込めなくても、内容は読めるようにする
    if (fileData === r && host.isConnected) content.innerHTML = '<p class="hint">' + esc(e.message) + '</p><pre>' + esc(r.content) + '</pre>';
  });
}
function leaveEdit() {  // 編集中なら、変更を捨ててよいか確認して編集をやめる
  if (editing && editDirty && !confirm(t("edit_discard_confirm"))) return false;
  const keepDiff = editing && currentView === "diff" && diffView;  // 差分エディタ上の編集は、差分表示を残す
  editing = editDirty = false;
  const bar = content.querySelector(".edit-bar-diff");
  if (bar) bar.remove();
  if (keepDiff) {
    if (diffView.isEditable && diffView.isEditable()) diffView.setEditable(false);
    // 差分表示のボタン配置に戻す
    fileDiffBtn.hidden = true;
    fileContentBtn.hidden = false;
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
    fileCopyBtn.disabled = !CAN_COPY || isWip;
    updateDiffBtn();
    updateRmBtns();
    if (fileData) title.textContent = t("file_diff_title", {path: currentFile});
    return true;
  }
  closeEditor();
  return true;
}
let editorScript = null;
function loadEditorScript() {  // エディタ (CodeMirror 6 + 言語 + Vim。1MB ほど) は、最初にファイルの内容を出すときに読み込む (ディレクトリの一覧だけなら読み込まない)
  if (window.GGText) return Promise.resolve();
  if (!editorScript) {
    editorScript = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "/static/vendor/text_editor.js";
      script.onload = resolve;
      script.onerror = () => { editorScript = null; reject(new Error(t("editor_load_failed"))); };  // 次の機会に読み直せるようにする
      document.head.appendChild(script);
    });
  }
  return editorScript;
}
function editorPhrases() {  // 検索パネルなどの文言 (CodeMirror 側の英語の文言 → 翻訳)
  return {"Find": t("editor_find"), "Replace": t("editor_replace"), "next": t("editor_next"), "previous": t("editor_previous"),
          "all": t("editor_all"), "match case": t("editor_match_case"), "regexp": t("editor_regexp"), "by word": t("editor_by_word"),
          "replace": t("editor_replace_one"), "replace all": t("editor_replace_all"), "close": t("editor_close"),
          "Go to line": t("editor_goto_line"), "go": t("editor_go")};
}
window.addEventListener("beforeunload", ev => { if (editing && editDirty) { ev.preventDefault(); ev.returnValue = ""; } });
let mdScrollCleanup = null;  // Markdown 編集の左右スクロール同期を外す関数
let mdScrollSyncing = false;
function insertMdTable() {  // 「表」ボタン。カーソルが表の中なら、その表を直接編集するオーバーレイを出す (閉じるときにソースへ書き戻す)。外なら、カーソルの行の下に 2 列の表を入れて、最後の行の 1 つ目の空セルへカーソルを置く
  if (!editing || !textEditor || !textEditor.view) return;
  const view = textEditor.view;
  if (window.GGText && window.GGText.mdTableOverlay &&
      window.GGText.mdTableOverlay(view, {addRowAbove: t("md_table_ov_add_row_above"), addRow: t("md_table_ov_add_row"),
                                          delRow: t("md_table_ov_del_row"), addColLeft: t("md_table_ov_add_col_left"),
                                          addCol: t("md_table_ov_add_col"), delCol: t("md_table_ov_del_col")})) return;  // オーバーレイにフォーカスがあるので、エディタは聚焦らない
  const line = view.state.doc.lineAt(view.state.selection.main.head);
  const rows = ["| " + t("md_table_col", {n: 1}) + " | " + t("md_table_col", {n: 2}) + " |", "| --- | --- |", "|   |   |"];
  const atLineEnd = line.text !== "";  // 行の途中なら、いったん改行してから表を出す (表は専用の行に)
  const from = line.to;
  const insert = (atLineEnd ? "\n" : "") + rows.join("\n");
  const anchor = from + (atLineEnd ? 1 : 0) + rows[0].length + 1 + rows[1].length + 1 + 2;  // 最後の行の 1 つ目の空セルの中
  view.dispatch({changes: {from, to: from, insert: insert}, selection: {anchor: anchor}, scrollIntoView: true});
  textEditor.focus();
}
function mdEditScroller() {  // 左エディタのスクロール要素 (CodeMirror の .cm-scroller)
  const host = document.getElementById("editor");
  return host && host.querySelector(".cm-scroller");
}
function syncScrollRatio(from, to) {  // スクロール位置を割合で合わせる (行高が違うため)
  if (!from || !to) return;
  const fMax = from.scrollHeight - from.clientHeight;
  const tMax = to.scrollHeight - to.clientHeight;
  if (fMax <= 0 || tMax <= 0) { to.scrollTop = 0; return; }
  to.scrollTop = (from.scrollTop / fMax) * tMax;
}
function bindMdEditScrollSync() {
  if (mdScrollCleanup) { mdScrollCleanup(); mdScrollCleanup = null; }
  const scroller = mdEditScroller();
  const prev = document.getElementById("mdEditPreview");
  if (!scroller || !prev) return;
  const onLeft = () => {
    if (mdScrollSyncing) return;
    mdScrollSyncing = true;
    syncScrollRatio(scroller, prev);
    requestAnimationFrame(() => { mdScrollSyncing = false; });
  };
  const onRight = () => {
    if (mdScrollSyncing) return;
    mdScrollSyncing = true;
    syncScrollRatio(prev, scroller);
    requestAnimationFrame(() => { mdScrollSyncing = false; });
  };
  scroller.addEventListener("scroll", onLeft, {passive: true});
  prev.addEventListener("scroll", onRight, {passive: true});
  mdScrollCleanup = () => {
    scroller.removeEventListener("scroll", onLeft);
    prev.removeEventListener("scroll", onRight);
  };
}
function updateMdEditPreview() {
  const pane = document.getElementById("mdEditPreview");
  if (!pane || !textEditor) return;
  const scroller = mdEditScroller();
  const ratio = scroller && (scroller.scrollHeight - scroller.clientHeight) > 0
    ? scroller.scrollTop / (scroller.scrollHeight - scroller.clientHeight) : 0;
  pane.innerHTML = renderMarkdownHtml(textEditor.getText());
  bindMarkdownRepoLinks(pane, currentFile);
  // プレビュー再描画後も、左のスクロール割合に合わせる
  const tMax = pane.scrollHeight - pane.clientHeight;
  if (tMax > 0) pane.scrollTop = ratio * tMax;
  else pane.scrollTop = 0;
}
function fitMdEditSplit() {
  const left = document.getElementById("editor");
  const right = document.getElementById("mdEditPreview") || document.getElementById("htmlEditPreview");
  if (left) fitEditor(left);
  if (right && left) right.style.height = left.style.height;
}
function updateHtmlEditPreview() {
  const frame = document.getElementById("htmlEditPreview");
  if (!frame || !textEditor) return;
  const scroller = mdEditScroller();
  const ratio = scroller && (scroller.scrollHeight - scroller.clientHeight) > 0
    ? scroller.scrollTop / (scroller.scrollHeight - scroller.clientHeight) : 0;
  frame.setAttribute("sandbox", typeof htmlSandboxAttr === "function" ? htmlSandboxAttr() : "allow-same-origin");
  frame.onload = () => {
    try {
      const doc = frame.contentDocument;
      if (doc && doc.body) bindMarkdownRepoLinks(doc.body, currentFile);
      const prev = doc && (doc.scrollingElement || doc.documentElement);
      if (prev) {
        const tMax = prev.scrollHeight - prev.clientHeight;
        prev.scrollTop = tMax > 0 ? ratio * tMax : 0;
      }
      bindHtmlEditScrollSync();
    } catch (e) { /* sandbox */ }
  };
  frame.srcdoc = textEditor.getText();
}
function bindHtmlEditScrollSync() {
  if (mdScrollCleanup) { mdScrollCleanup(); mdScrollCleanup = null; }
  const scroller = mdEditScroller();
  const frame = document.getElementById("htmlEditPreview");
  const doc = frame && frame.contentDocument;
  const prev = doc && (doc.scrollingElement || doc.documentElement);
  if (!scroller || !prev) return;
  const onLeft = () => {
    if (mdScrollSyncing) return;
    mdScrollSyncing = true;
    syncScrollRatio(scroller, prev);
    requestAnimationFrame(() => { mdScrollSyncing = false; });
  };
  const onRight = () => {
    if (mdScrollSyncing) return;
    mdScrollSyncing = true;
    syncScrollRatio(prev, scroller);
    requestAnimationFrame(() => { mdScrollSyncing = false; });
  };
  scroller.addEventListener("scroll", onLeft, {passive: true});
  prev.addEventListener("scroll", onRight, {passive: true});
  mdScrollCleanup = () => {
    scroller.removeEventListener("scroll", onLeft);
    prev.removeEventListener("scroll", onRight);
  };
}
function startEdit() {
  if (!fileData || !fileData.editable || !currentFile) return;
  closeEditor();  // 表示のエディタ
  editing = true; editDirty = false;
  setFileMsg("", true);
  [fileCommitLogBtn, fileLogBtn, fileBlameBtn, fileDiffBtn, fileExtDiffBtn, fileMergeBtn, fileEditBtn, fileContentBtn, fileGraphLogBtn, fileCopyBtn, fileDownloadBtn, fileUploadBtn, fileExtEditBtn, fileRmBtn, fileRmCachedBtn].forEach(b => { b.hidden = true; });
  const eol = EOL_LABEL[fileData.eol] || t("edit_eol_none");
  title.textContent = t("file_edit_title", {path: currentFile});
  content.className = "";
  const mdEdit = typeof isMarkdownPath === "function" && isMarkdownPath(currentFile);
  const htmlEdit = !mdEdit && typeof isHtmlPath === "function" && isHtmlPath(currentFile);
  const splitEdit = mdEdit || htmlEdit;
  const breaksLabel = mdEdit
    ? '<label class="edit-vim" title="' + esc(t("md_breaks_tip")) + '"><input type="checkbox" id="editMdBreaks"' +
      (mdBreaksOn ? " checked" : "") + '> ' + esc(t("md_breaks")) + '</label>'
    : "";
  const editorBlock = mdEdit
    ? '<div class="md-edit-split"><div class="md-edit-left"><div id="editor" class="cm-host"></div></div>' +
      '<div class="md-edit-right"><div id="mdEditPreview" class="md-preview md-edit-preview"></div></div></div>'
    : htmlEdit
      ? '<div class="md-edit-split"><div class="md-edit-left"><div id="editor" class="cm-host"></div></div>' +
        '<div class="md-edit-right"><iframe id="htmlEditPreview" class="html-preview-frame md-edit-preview" sandbox="allow-same-origin" title="HTML preview"></iframe></div></div>'
      : '<div id="editor" class="cm-host"></div>';
  content.innerHTML = '<div class="edit-info">' + esc(t("edit_format", {encoding: fileData.encoding, eol: eol})) + '</div>' +
    (fileData.eol_mixed ? '<div class="edit-info warn">' + esc(t("edit_eol_mixed", {eol: eol})) + '</div>' : "") +
    '<div class="edit-bar"><button class="primary" id="editSave">' + esc(t("edit_save")) + '</button><button id="editCancel">' +
    esc(t("edit_cancel")) + '</button><label class="edit-vim" title="' + esc(t("edit_wrap_tip")) + '"><input type="checkbox" id="editWrap"' + (wrapOn ? " checked" : "") +
    '> ' + esc(t("edit_wrap")) + '</label><label class="edit-vim" title="' + esc(t("edit_vim_tip")) + '"><input type="checkbox" id="editVim"' + (vimOn ? " checked" : "") +
    '> ' + esc(t("edit_vim")) + '</label>' +
    (mdEdit ? '<button id="mdTblBtn" title="' + esc(t("md_table_insert_tip")) + '">' + esc(t("md_table_insert")) + '</button>' : "") +
    breaksLabel + '</div>' + editorBlock;
  document.getElementById("main").scrollTop = 0;
  if (splitEdit) fitMdEditSplit();
  else fitEditor(document.getElementById("editor"));
  const loaded = fileData;
  let mdPreviewTimer = null;
  loadEditorScript().then(() => {
    const host = document.getElementById("editor");
    if (!editing || fileData !== loaded || !host) return;  // 読み込んでいる間に、別の表示に移った
    textEditor = GGText.createTextEditor({
      parent: host, text: loaded.content, path: currentFile, vim: vimOn, wrap: wrapOn, phrases: editorPhrases(),
      onChange: () => {
        editDirty = true;
        if (!splitEdit) return;
        if (mdPreviewTimer) clearTimeout(mdPreviewTimer);
        mdPreviewTimer = setTimeout(mdEdit ? updateMdEditPreview : updateHtmlEditPreview, 120);
      }, onSave: saveEdit,
      onQuit: force => { if (force) editDirty = false; if (leaveEdit()) backToContent(); },  // Vim の :q / :q!
    });
    restoreCmScroll(currentFile, true);
    if (mdEdit) {
      MdAttach.bind(host, {  // 画像のドロップ / 図のペースト → attachments へアップロードして、リンクを貼る
        getView: () => textEditor && textEditor.view, mdPath: () => currentFile, t: t, status: setStatus,
        refresh: () => loadTree(current, false),
      });
      updateMdEditPreview();
      fitMdEditSplit();
      bindMdEditScrollSync();
    } else if (htmlEdit) {
      fitMdEditSplit();
      updateHtmlEditPreview();
    }
    textEditor.focus();
  }).catch(e => {
    if (!leaveEdit()) return;
    backToContent(() => setFileMsg(e.message, false));
  });
  document.getElementById("editWrap").onchange = ev => {
    setWrapPref(ev.target.checked);
    if (textEditor) { textEditor.setWrap(wrapOn); textEditor.focus(); }
  };
  document.getElementById("editVim").onchange = ev => {
    setVimPref(ev.target.checked);
    if (textEditor) { textEditor.setVim(vimOn); textEditor.focus(); }
  };
  const brk = document.getElementById("editMdBreaks");
  if (brk) brk.onchange = ev => {
    setMdBreaksPref(ev.target.checked);
    updateMdEditPreview();
    if (textEditor) textEditor.focus();
  };
  const tblBtn = document.getElementById("mdTblBtn");
  if (tblBtn) tblBtn.onclick = insertMdTable;
  document.getElementById("editSave").onclick = saveEdit;
  document.getElementById("editCancel").onclick = () => { if (leaveEdit()) backToContent(); };
}
function saveEdit() {
  const btn = document.getElementById("editSave");
  if (!editing || !textEditor || btn.disabled) return;
  btn.disabled = true;
  const path = currentFile;
  fetch("/api/file-save", {method: "POST", headers: {"Content-Type": "application/json"},
                           body: JSON.stringify({path: path, content: textEditor.getText(), sha: fileData.sha})})
    .then(r => r.ok ? r.json() : r.text().then(m => { throw new Error(m || r.status); }))
    .then(r => {
      editing = editDirty = false;
      closeEditor();
      fileData = null;  // 保存後の内容は読み込み直す
      backToContent(() => loadTree(current, false)  // 変更の印と差分ボタンを更新する
        .then(() => { updateDiffBtn(); setFileMsg(r.message, true); }));
    })
    .catch(e => { btn.disabled = false; setFileMsg(e.message, false); fileActions.hidden = false; });
}
fileEditBtn.onclick = () => {
  // 差分表示中は、変更後側を差分エディタのまま編集する (内容表示の単独編集へは移らない)
  if (currentView === "diff" && isWip && CAN_EDIT) startDiffEdit();
  else openView("edit");
};
function startDiffEdit() {
  if (!currentFile || currentView !== "diff" || !diffView) return openView("edit");
  const begin = () => {
    if (!fileData) return;
    if (fileData.binary || !fileData.editable) {
      setFileMsg(t(fileData.edit_reason || "edit_bad_encoding"), false);
      return;
    }
    editing = true; editDirty = false;
    setFileMsg("", true);
    [fileCommitLogBtn, fileLogBtn, fileBlameBtn, fileDiffBtn, fileExtDiffBtn, fileMergeBtn, fileEditBtn, fileContentBtn, fileGraphLogBtn, fileCopyBtn, fileDownloadBtn, fileUploadBtn, fileExtEditBtn, fileRmBtn, fileRmCachedBtn].forEach(b => { b.hidden = true; });
    title.textContent = t("file_edit_title", {path: currentFile});
    let bar = content.querySelector(".edit-bar-diff");
    if (!bar) {
      bar = document.createElement("div");
      bar.className = "edit-bar edit-bar-diff";
      bar.innerHTML = '<button class="primary" id="diffEditSave">' + esc(t("edit_save")) + '</button>' +
        '<button id="diffEditCancel">' + esc(t("edit_cancel")) + '</button>';
      content.insertBefore(bar, content.firstChild);
      document.getElementById("diffEditSave").onclick = () => saveDiffEdit();
      document.getElementById("diffEditCancel").onclick = () => { leaveEdit(); };
    }
    diffView.setEditable(true, {
      onChange: () => { editDirty = true; },
      onSave: () => saveDiffEdit(),
    });
  };
  if (fileData) begin();
  else api("/api/file", {hash: hash, path: currentFile}).then(r => {
    fileData = r;
    begin();
  }).catch(e => setFileMsg(e.message, false));
}
function saveDiffEdit() {
  if (!editing || !diffView || !fileData) return;
  const text = diffView.getText();
  if (text === null) return;
  const btn = document.getElementById("diffEditSave");
  if (btn) btn.disabled = true;
  const path = currentFile;
  fetch("/api/file-save", {method: "POST", headers: {"Content-Type": "application/json"},
                           body: JSON.stringify({path: path, content: text, sha: fileData.sha})})
    .then(r => r.ok ? r.json() : r.text().then(m => { throw new Error(m || r.status); }))
    .then(r => {
      editing = editDirty = false;
      fileData = null;
      const bar = content.querySelector(".edit-bar-diff");
      if (bar) bar.remove();
      if (diffView) diffView.close();
      diffView = null;
      setFileMsg(r.message, true);
      // 保存後は差分を読み直す
      loadTree(current, false).then(() => {
        updateDiffBtn();
        loadFileDiff(path);
      });
    })
    .catch(e => {
      if (btn) btn.disabled = false;
      setFileMsg(e.message, false);
    });
}

function loadFileBlame(path) {
  closeEditor();  // 表示のエディタ
  fileDiffBtn.hidden = true;
  fileContentBtn.hidden = false;
  fileCommitLogBtn.hidden = !logHash();
  fileLogBtn.hidden = true;
  fileBlameBtn.hidden = true;
  fileGraphLogBtn.hidden = true;
  setDocumentTitle(t("file_blame_title", {path: path}));
  title.textContent = t("file_blame_title", {path: path});
  content.className = "";
  content.textContent = t("loading");
  Promise.all([api("/api/blame", {hash: hash, path: path}), refsReady]).then(([r]) => {
    content.innerHTML = '<div class="meta">' + esc(t("n_lines", {n: r.lines.length})) + '</div><div class="blame">' +
      r.lines.map(line => {
        const nc = /^0+$/.test(line.hash);
        const meta = nc
          ? '<span class="blame-hash"></span><span class="blame-author">' + esc(t("not_committed_yet")) + '</span><span class="blame-date"></span><span class="blame-subject"></span>'
          : '<span class="blame-hash"><a class="blame-commit commit-graph" href="/?commit_hash=' + encodeURIComponent(line.hash) + '">' +
            esc(line.hash.slice(0, 7)) + '</a></span><span class="blame-author">' + esc(line.author) +
            '</span><span class="blame-date">' + esc(line.date) + '</span><span class="blame-subject">' + refChips(line.hash) + '<span title="' + esc(line.subject || "") + '">' + esc(line.subject || "") + '</span></span>';
        return '<div class="blame-line" data-hash="' + (nc ? "" : esc(line.hash)) + '"><span class="blame-no">' + line.line +
          '</span>' + meta + '<span class="blame-code">' + esc(line.content) + '</span></div>';
      }).join("") + '</div>';
  }).catch(e => { content.className = "hint"; content.textContent = t("error") + e.message; });
}
content.addEventListener("click", ev => {
  const link = ev.target.closest("a.commit-graph");
  if (link) {
    if (window.opener && !window.opener.closed) {
      ev.preventDefault();
      window.opener.postMessage({type: "git-graph-commit", hash: new URL(link.href).searchParams.get("commit_hash")}, location.origin);
    }
    return;
  }
  const dirRow = ev.target.closest("tr.dir-log-row");
  if (dirRow && !currentFile) {  // ディレクトリの一覧の項目: ディレクトリなら開き、ファイルなら表示する
    openEntry(dirRow.dataset.path, dirRow.dataset.type);
    return;
  }
  const row = ev.target.closest("tr.clickable, .blame-line"), rowHash = row && (row.dataset.h || row.dataset.hash);  // ログの行は data-h (グラフ画面と同じ)、blame の行は data-hash
  if (!rowHash) return;
  if (window.mbSetTarget) window.mbSetTarget(rowHash);  // ログ / blame の行をクリックしたコミットを、メニューの「対象」にする
  if (ev.target.closest("td.graph")) return;  // マージグラフの列は、対象にするだけ (そのコミットの表示へは移らない。線の強調は対象の変更で付く)
  // ログから別コミットのファイル画面へ遷移する前に、現在のログ表示を履歴に確実に残す。
  // 初期表示で開いたログ (viaPush なし) だと、location.href 後の戻るでログに戻れないことがあるため。
  if (!history.state || !history.state.viaPush) {
    const st = currentFile
      ? {path: current, file: currentFile, view: currentView || "log", viaPush: true}
      : {path: current, dirlog: true, viaPush: true};
    history.pushState(st, "", location.href);
  }
  if (!currentFile) {  // ディレクトリのログの項目: そのコミットでのディレクトリを開く
    if (!dirLogOn) return;
    const dirParams = new URLSearchParams({hash: rowHash});
    if (current) dirParams.set("path", current);
    if (fileViewTab) dirParams.set("file_view", "1");
    location.href = "/files?" + dirParams;
    return;
  }
  const params = new URLSearchParams({hash: rowHash, file: currentFile});
  const slash = currentFile.lastIndexOf("/");
  const dir = slash < 0 ? "" : currentFile.slice(0, slash);
  if (dir) params.set("path", dir);
  if (fileViewTab) params.set("file_view", "1");
  location.href = "/files?" + params;
});
