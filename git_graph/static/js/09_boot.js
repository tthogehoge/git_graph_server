// ---- グラフの読み込み (サーバー側の作成に時間がかかるので、読み込み中画面を出す) ----
function scrollToHead() {  // HEAD のノードをグラフの真ん中に出す
  const head = Object.values(DATA.nodes).find(n => n.refs.some(ref => ref.kind === "head"));
  if (head) revealHash(head.hash);
}
const t0 = Date.now();
const timer = setInterval(() => {
  const el = document.getElementById("lsub");
  if (el) el.textContent = t("elapsed_hint", {sec: Math.round((Date.now() - t0) / 1000)});
}, 1000);
document.getElementById("showMerges").checked = showMergesOn();
fetch(graphUrl())
  .then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }))
  .then(r => {
    clearInterval(timer);
    setGraphData(r);
    if (r.notice) setGraphStatus(r.notice, "error");  // 大きすぎて表示を減らしたとき
    document.getElementById("graph").innerHTML = r.svg;
    applyGraphZoom();
    init();
    updateCmpClasses();
    document.getElementById("reload").disabled = false;
    document.getElementById("showMerges").disabled = false;
    loadStashes();
    updateCurrentBranch().catch(e => setGraphStatus(t("current_branch_failed", {error: e.message}), "error"));
    const screen = history.state || {screen: "home"};
    restoreScreen(screen);
    const fileLogHash = new URLSearchParams(location.search).get("file_log_hash");
    const fileLogPath = new URLSearchParams(location.search).get("file_log_path");
    const commitHash = new URLSearchParams(location.search).get("commit_hash");
    if (fileLogHash && fileLogPath) {
      // 一度だけ消費し、以後の履歴には一時的なクエリを残さない。
      history.replaceState({screen: "home"}, "", "/");
      showFileLogInGraph(fileLogHash, fileLogPath);
    } else if (commitHash) {
      history.replaceState({screen: "home"}, "", "/");
      showContainingBranchLog(commitHash);
    } else if (screen.screen === "home") {
      scrollToHead();
    }
  })
  .catch(e => {
    clearInterval(timer);
    document.getElementById("graph").innerHTML = '<div id="loading"><div>' + esc(t("graph_build_failed")) + '</div><pre>' +
      esc(e.message) + '</pre><button class="sbtn" onclick="location.reload()">' + esc(t("reload_page")) + '</button></div>';
  });

document.getElementById("backBtn").addEventListener("click", () => history.back());  // pywebview にはブラウザの戻るボタンがない
document.getElementById("undoBtn").addEventListener("click", () => doUndoRedo("undo"));
document.getElementById("redoBtn").addEventListener("click", () => doUndoRedo("redo"));
updateUndoButtons();
document.addEventListener("keydown", ev => {  // Ctrl+Z / Ctrl+Y (Ctrl+Shift+Z) で元に戻す / やり直し。入力欄やダイアログの操作中は、ブラウザ標準の動作に任せる
  if (!(ev.ctrlKey || ev.metaKey) || ev.altKey || ev.repeat) return;
  const key = ev.key.toLowerCase(), undo = key === "z" && !ev.shiftKey, redo = key === "y" || (key === "z" && ev.shiftKey);
  if (!undo && !redo) return;
  if (ev.target.closest && ev.target.closest("input, textarea, select, [contenteditable]")) return;
  if (document.querySelector("#quitModal:not([hidden]), #coModal:not([hidden]), #actionModal:not([hidden]), #confirmModal:not([hidden])")) return;
  ev.preventDefault();
  if (!document.getElementById(undo ? "undoBtn" : "redoBtn").disabled) doUndoRedo(undo ? "undo" : "redo", true);
});
document.getElementById("quitBtn").addEventListener("click", () => {  // サーバーを終了または再起動する
  document.getElementById("quitModal").hidden = false;
  document.getElementById("quitModalQuit").focus();
});
document.getElementById("quitModalCancel").addEventListener("click", () => {
  document.getElementById("quitModal").hidden = true;
});
function quitModalAction(action) {
  document.getElementById("quitModal").hidden = true;
  const endpoint = action === "restart" ? "/api/restart" : "/api/shutdown";
  post(endpoint, {})
    .then(() => {
      if (action === "restart") {
        document.body.innerHTML = '<div style="padding:24px;font-size:14px">' + esc(t("server_restarting")) + "</div>";
        // サーバーが再起動するまで待ってからリロード
        const tryReload = () => fetch("/").then(() => location.reload()).catch(() => setTimeout(tryReload, 500));
        setTimeout(tryReload, 1000);
      } else {
        document.body.innerHTML = '<div style="padding:24px;font-size:14px">' + esc(t("server_stopped")) + "</div>";
      }
    })
    .catch(e => setGraphStatus(t("quit_failed", {error: e.message}), "error"));
}
document.getElementById("quitModalQuit").addEventListener("click", () => quitModalAction("quit"));
document.getElementById("quitModalRestart").addEventListener("click", () => quitModalAction("restart"));
document.getElementById("reload").addEventListener("click", ev => {  // グラフを最新の状態に更新
  const b = ev.currentTarget;
  b.disabled = true; b.classList.add("busy"); b.title = t("reload_busy");
  refreshGraph().catch(() => {}).finally(() => { b.disabled = false; b.classList.remove("busy"); b.title = t("reload_title"); });
});
document.getElementById("topBtn").addEventListener("click", scrollToHead);
document.getElementById("wipBtn").hidden = !WIP_ENABLED;
document.getElementById("wipBtn").addEventListener("click", () => { clearCmp(); showWip(); });  // グラフの WIP ノードをクリックしたときと同じ
