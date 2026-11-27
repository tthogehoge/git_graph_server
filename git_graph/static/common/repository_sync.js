// 成功した API 書き込みを画面間で共有する。個々の操作 UI に更新呼び出しを足し忘れないための共通通知。
(function () {
  // ファイルシステム監視の方式を見直すまで自動同期を停止する。
  window.GIT_GRAPH_AUTO_WIP_SYNC = false;
  if (window.GIT_GRAPH_AUTO_WIP_SYNC !== true) return;
  const KEY = "git_graph_repository_changed";
  const notify = detail => window.dispatchEvent(new CustomEvent("git-graph-repository-changed", {detail: detail || {}}));
  const originalFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    const method = String((init && init.method) || (input && input.method) || "GET").toUpperCase();
    let url;
    try { url = new URL(typeof input === "string" ? input : input.url, location.href); } catch (e) { return originalFetch(input, init); }
    const isApiWrite = method === "POST" && url.origin === location.origin && url.pathname.startsWith("/api/");
    const request = originalFetch(input, init);
    if (isApiWrite) request.then(response => {
      if (!response.ok) return;
      const detail = {path: url.pathname, time: Date.now()};
      notify(detail);
      try { localStorage.setItem(KEY, JSON.stringify(detail)); } catch (e) { /* 通知できなくても書き込み結果には影響しない */ }
    }).catch(() => {});
    return request;
  };
  window.addEventListener("storage", ev => {
    if (ev.key !== KEY || !ev.newValue) return;
    try { notify(JSON.parse(ev.newValue)); } catch (e) { notify({}); }
  });
})();
