// ---- 作業ツリーの変更 (WIP) ----
// 一覧とコミットフォーム (チェックしたファイルだけを add してコミット、amend、破棄、rm、stash、競合の解決など) は、
// ファイル表示・差分と共通の wip_panel.js。ここは、グラフ画面のヘッダー (戻る / 更新 / HEAD / パッチ) と、操作のあとの再描画だけ。
const wipPanel = WipPanel.create({
  t: t,
  get: api,
  post: post,
  dialog: openActionDialog,
  canWrite: () => !!DATA.writable,  // 書き込みが無効 (--read-only など) のときはチェック欄を出さない
  say: (text, ok) => setGraphStatus(text, ok ? "success" : "error"),
  afterChange: (message, opts) => {
    showWip(true, message);
    refreshGraph().catch(() => {});
    if (opts && opts.stash) loadStashes();
  },
  onFiles: files => { curCtx = {}; curFiles = files; updatePageNav("WIP"); },
});
const wipSay = (text, ok) => wipPanel.say(text, ok);
const wipCommitMenuBtn = document.getElementById("wipCommitMenuBtn");
wipCommitMenuBtn.disabled = !DATA.writable || !WIP_ENABLED;
wipCommitMenuBtn.addEventListener("click", () => {
  if (!detailView.querySelector(".wip-panel")) showWip(false);
});

function wipPatchBarHtml(canWrite) {  // パッチ作成 (読み取りだけなので常時) と、書き込みが有効なときの適用 (git apply / git am)
  return '<div class="wippatch"><button class="wbtn" id="wipPatchCreate" title="' + esc(t("tip_patch_create_wip")) + '">' + esc(t("patch_create")) + '</button>' +
    (canWrite ? '<button class="wbtn" id="wipPatchApply" title="' + esc(t("tip_patch_apply")) + '">' + esc(t("patch_apply")) + '</button>' +
      '<button class="wbtn" id="wipPatchAm" title="' + esc(t("tip_patch_am")) + '">' + esc(t("patch_am")) + '</button>' : "") + '</div>';
}
detailView.addEventListener("click", ev => {
  const b = ev.target.closest("#wipPatchCreate, #wipPatchApply, #wipPatchAm");
  if (!b) return;
  if (b.id === "wipPatchCreate") { downloadPatch({hash: "WIP"}, "wip.patch", wipSay); return; }
  pickPatchFile(b.id === "wipPatchAm" ? "am" : "apply", wipSay, r => {
    wipPanel.reset(false);
    showWip(true, r.message);
    refreshGraph().catch(() => {});
  });
});
function showWip(fromHistory, okMsg) {
  document.body.classList.remove("compare-logs-mode");
  if (!fromHistory) pushScreen({screen: "wip"});
  setDocumentTitle(t("wip_title"));
  updatePageNav("WIP");  // 変更ファイルを取る前でも、ファイルボタンは WIP を開く (前に選んでいたコミットのままにしない)
  const backIcon = '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false">' +
    '<path d="M13 8H3M7.5 3.5 3 8l4.5 4.5" fill="none" stroke="currentColor" stroke-width="1.8" ' +
    'stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const reloadIcon = '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false">' +
    '<path d="M13.5 8a5.5 5.5 0 1 1-1.7-4M13 2.5v3.2h-3.2" fill="none" stroke="currentColor" stroke-width="1.8" ' +
    'stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const btns = '<span class="detail-nav-bar">' +
    '<button type="button" class="sbtn iconbtn back-btn" title="' + esc(t("back_history")) +
    '" aria-label="' + esc(t("back_history")) + '">' + backIcon + '</button>' +
    '<button type="button" class="sbtn iconbtn" id="wipReload" title="' + esc(t("reload_page")) +
    '" aria-label="' + esc(t("reload_page")) + '">' + reloadIcon + '</button></span>';
  detailView.innerHTML = btns + '<p class="hint">' + t("wip_loading") + '</p>';
  showView("detail"); curDetailHash = null;
  wipPanel.render(detailView, {
    okMsg: okMsg,
    top: (s, files) => btns + "<h2>" + t("wip_title") +
      ' <a class="detail-files" href="/files?hash=WIP" title="' + esc(t("tip_wip_files")) + '">' + esc(t("files")) + "</a>" +
      (files.length ? ' <a class="detail-files" href="' + esc(diffUrl({}, files[0])) + '">' + esc(t("file_diff")) + "</a>" : "") + "</h2>",
    mid: s => '<div class="meta">HEAD: ' + (s.head ? chips(s.head) + esc(s.head.slice(0, 7)) : t("no_commits_yet")) + "</div>" +
      (wipPanel.amend() ? "" : wipPatchBarHtml(!!DATA.writable)),
    error: e => btns + '<p class="hint">' + esc(t("error")) + esc(e.message) + "</p>",
  });
}

document.getElementById("showMergesBtn").addEventListener("click", () => {  // マージ表示の on / off (他のメニュー項目と同じ button)
  const btn = document.getElementById("showMergesBtn"), cb = document.getElementById("showMerges");
  if (btn.disabled) return;
  cb.checked = !cb.checked;
  setShowMerges(cb.checked);
  openClusters = [];  // マージ表示で残すノードが変わるので、開くクラスターは HEAD の分から始め直す
  btn.disabled = true;
  refreshGraph()
    .catch(() => { cb.checked = !cb.checked; setShowMerges(cb.checked); })  // 作り直せなかったら、表示中のグラフに合わせて戻す
    .finally(() => { btn.disabled = false; });
});
