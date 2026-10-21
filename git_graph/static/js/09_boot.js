// ---- グラフの読み込み (サーバー側の作成に時間がかかるので、読み込み中画面を出す) ----
const t0 = Date.now();
const timer = setInterval(() => {
  const el = document.getElementById("lsub");
  if (el) el.textContent = t("elapsed_hint", {sec: Math.round((Date.now() - t0) / 1000)});
}, 1000);
fetch("/api/graph")
  .then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }))
  .then(r => {
    clearInterval(timer);
    DATA = {nodes: r.nodes, edges: r.edges, writable: r.writable, merge: r.merge};
    document.getElementById("graph").innerHTML = r.svg;
    applyGraphZoom();
    init();
    updateCmpClasses();
    document.getElementById("reload").disabled = false;
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