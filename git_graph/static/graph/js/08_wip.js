// ---- 作業ツリーの変更 (WIP) ----
// 一覧とコミットフォーム (チェックしたファイルだけを add してコミット、amend、破棄、rm、stash、競合の解決など) は、
// ファイル表示・差分と共通の wip_panel.js。ここは、グラフ画面のヘッダー (戻る / 更新 / HEAD / パッチ) と、操作のあとの再描画だけ。
const wipPanel = WipPanel.create({
  t: t,
  get: api,
  post: post,
  dialog: openActionDialog,
  canWrite: () => !!DATA.writable,  // 書き込みが無効 (--read-only など) のときはチェック欄を出さない
  autoPoll: false,  // グラフ画面の監視は WIP パネルが閉じているときも含め、下の共通ポーラーで行う
  say: (text, ok) => setGraphStatus(text, ok ? "success" : "error"),
  afterChange: (message, opts) => {
    showWip(true, message);
    if (opts && opts.stash) loadStashes();
  },
  onFiles: (files, status) => {
    curCtx = {}; curFiles = files; updatePageNav("WIP");
    if (!status) return;
    observeGraphWipStatus(status);
  },
});
let lastWipHead = null, lastWipBranch = "";
function observeGraphWipStatus(status) {
  const paths = new Set();
  ["conflicts", "staged", "unstaged", "untracked"].forEach(kind => (status[kind] || []).forEach(f => paths.add(f.path)));
  updateWipGraphCount(paths.size);
  const head = status.head || "", branch = status.branch || "";
  if (lastWipHead !== null && (head !== lastWipHead || branch !== lastWipBranch)) refreshGraph().catch(() => {});
  lastWipHead = head; lastWipBranch = branch;
}
function updateWipGraphCount(value) {
  const node = [...document.querySelectorAll("#graph g.node")].find(g => g.querySelector("title")?.textContent === "WIP");
  const count = node && node.querySelectorAll("text")[1];
  if (count && Number.isInteger(value)) count.textContent = value ? t("n_changes", {n: value}) : t("wip_clean");
}
const wipSay = (text, ok) => wipPanel.say(text, ok);
function pollGraphWip() {
  if (document.hidden) return;
  if (detailView.querySelector(".wip-panel")) {
    wipPanel.refreshIfChanged().catch(() => {});
    return;
  }
  api("/api/status", {}).then(observeGraphWipStatus).catch(() => {});
}
if (window.GIT_GRAPH_AUTO_WIP_SYNC === true) {
  pollGraphWip();
  setInterval(pollGraphWip, 2000);
  window.addEventListener("git-graph-repository-changed", () => {
    if (!detailView.querySelector(".wip-panel")) pollGraphWip();
  });
}
const wipCommitMenuBtn = document.getElementById("wipCommitMenuBtn");
wipCommitMenuBtn.disabled = !DATA.writable || !WIP_ENABLED;
wipCommitMenuBtn.addEventListener("click", () => {
  if (!detailView.querySelector(".wip-panel")) showWip(false);
});

// 「その他」メニューのパッチ作成 / 適用 (apply・am)。ファイル表示と共通の patch_ops.js が動かす
PatchOps.bind({
  t: t,
  post: post,
  say: (text, state) => setGraphStatus(text, state === "progress" ? "progress" : state ? "success" : "error"),
  applied: r => {  // 作業ツリーやブランチが変わるので、グラフと、開いている WIP の表示を読み直す
    refreshGraph().catch(() => {});
    if (detailView.querySelector(".wip-panel")) { wipPanel.reset(false); showWip(true, r.message); }
  },
});
// 作成は読み取りだけなので書き込みが無効でも使える。適用は書き込めるときだけ。DATA.writable はグラフを読み込んで初めて入るので、setGraphData からも呼ぶ
function updatePatchMenu() {
  document.getElementById("wipPatchCreateBtn").disabled = !WIP_ENABLED;
  document.getElementById("wipPatchApplyBtn").disabled = document.getElementById("wipPatchAmBtn").disabled = !DATA.writable || !WIP_ENABLED;
}
updatePatchMenu();
function showWip(fromHistory, okMsg) {
  setLocationMenu("WIP");
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
      (files.length ? ' <a class="detail-files" href="/files?hash=WIP&side=changes" title="' + esc(t("file_diff")) + '">' + esc(t("file_diff")) + "</a>" : "") + "</h2>",
    mid: s => '<div class="meta">HEAD: ' + (s.head ? chips(s.head) + esc(s.head.slice(0, 7)) : t("no_commits_yet")) + "</div>",
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
