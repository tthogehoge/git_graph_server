// ---- グラフの読み込み (サーバー側の作成に時間がかかるので、読み込み中画面を出す) ----
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
    restoreScreen(history.state || {screen: "home"});
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
    }
  })
  .catch(e => {
    clearInterval(timer);
    document.getElementById("graph").innerHTML = '<div id="loading"><div>' + esc(t("graph_build_failed")) + '</div><pre>' +
      esc(e.message) + '</pre><button class="sbtn" onclick="location.reload()">' + esc(t("reload_page")) + '</button></div>';
  });

document.getElementById("backBtn").addEventListener("click", () => history.back());  // pywebview にはブラウザの戻るボタンがない
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
  b.disabled = true; b.textContent = t("reload_busy");
  refreshGraph().catch(() => {}).finally(() => { b.disabled = false; b.textContent = t("reload"); });
});
