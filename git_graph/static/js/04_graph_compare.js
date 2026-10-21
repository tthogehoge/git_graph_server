const graphStatusTimers = new Map();
function setGraphStatus(message, type, clearProgress) {
  const container = document.getElementById("graphStatus");
  if (clearProgress) {
    container.querySelectorAll(".graph-toast.progress").forEach(toast => {
      clearTimeout(graphStatusTimers.get(toast));
      graphStatusTimers.delete(toast);
      toast.remove();
    });
  }
  if (!message) {
    container.querySelectorAll(".graph-toast.progress").forEach(toast => {
      clearTimeout(graphStatusTimers.get(toast));
      graphStatusTimers.delete(toast);
      toast.remove();
    });
    return;
  }
  const progress = type === "progress";  // 進捗かどうかは表示文言ではなく type で明示する (文言は翻訳で変わる)
  const toast = [...container.children].find(el =>
    el.dataset.message === message && el.classList.contains(progress ? "progress" : (type || "info")));
  if (toast) {
    clearTimeout(graphStatusTimers.get(toast));
    graphStatusTimers.delete(toast);
  }
  const item = toast || document.createElement("div");
  item.className = "graph-toast " + (type || "info");
  item.setAttribute("role", "status");
  item.setAttribute("aria-live", "polite");
  item.dataset.message = message;
  item.innerHTML = '<span class="graph-toast-message"></span><button type="button" class="graph-toast-close" aria-label="' + esc(t("close_notification")) + '" title="' + esc(t("close_notification")) + '">×</button>';
  item.querySelector(".graph-toast-message").textContent = message;
  item.querySelector(".graph-toast-close").addEventListener("click", () => {
    clearTimeout(graphStatusTimers.get(item));
    graphStatusTimers.delete(item);
    item.remove();
  });
  if (!toast) container.appendChild(item);
  if (!progress) {
    graphStatusTimers.set(item, setTimeout(() => {
      item.remove();
      graphStatusTimers.delete(item);
    }, 15000));
  }
}

// 操作のあと、グラフを(表示したまま)作り直して新しいブランチ/タグ/HEAD を反映する
function refreshGraph() {
  setGraphStatus(t("graph_updating"), "progress");
  return fetch("/api/graph")
    .then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }))
    .then(r => {
      const keys = new Set(Object.keys(DATA.nodes).concat(Object.keys(r.nodes)));
      const gc = document.getElementById("graph"), top0 = gc.scrollTop, left0 = gc.scrollLeft;
      DATA = {nodes: r.nodes, edges: r.edges, writable: r.writable, merge: r.merge};
      gc.innerHTML = r.svg;
      applyGraphZoom();
      gc.scrollTop = top0; gc.scrollLeft = left0;
      selected = null;
      for (const k in nodeEl) delete nodeEl[k];
      init();
      if (selectedKey) {
        const g = [...gc.querySelectorAll("g.node, g.edge")].find(x => {
          const t = x.querySelector("title"); return t && t.textContent === selectedKey;
        });
        if (g) { g.classList.add("sel"); selected = g; }
      }
      updateCmpClasses();
      loadStashes();
      updateCurrentBranch().catch(e => setGraphStatus(t("current_branch_failed", {error: e.message}), "error"));
      keys.forEach(h => listView.querySelectorAll('tr[data-h="' + h + '"] .rc').forEach(el => { el.innerHTML = chips(h); }));
      const c = detailView.querySelector("#dChips");
      if (c && curDetailHash) c.innerHTML = detailChips(curDetailHash);
      setGraphStatus("");
    })
    .catch(e => {
      setGraphStatus(t("graph_update_failed", {error: e.message}), "error");
      throw e;
    });
}

// ---- コミット比較 ----
function commitLine(label, c) {
  return '<div class="meta"><span class="tag tag' + label + '">' + label + "</span>" + chips(c.hash) +
    esc(c.hash.slice(0, 7)) + copyLink(c.hash) + " — " + esc(c.message.split("\n")[0]) +
    '<div class="hint">' + esc(c.author) + " / " + esc(c.date) + "</div></div>";
}
function showCompare(a, b, fromHistory) {  // a, b はコミットのハッシュ
  document.body.classList.remove("compare-logs-mode");
  if (!fromHistory) pushScreen({screen: "compare", a: a, b: b});
  setDocumentTitle(t("compare_heading", {from: a.slice(0, 7), to: b.slice(0, 7)}));
  const btns = '<button class="back-btn">' + t("back_history") + '</button>';
  detailView.innerHTML = btns + '<p class="hint">' + esc(t("loading")) + '</p>';
  showView("detail");
  api("/api/compare", {from: a, to: b}).then(r => {
    curCtx = {from: r.from.hash, to: r.to.hash}; curFiles = r.files;
    detailView.innerHTML = btns + "<h2>" + esc(t("compare_heading", {from: r.from.hash.slice(0, 7), to: r.to.hash.slice(0, 7)})) + "</h2>" +
      commitLine("A", r.from) + commitLine("B", r.to) + filesHtml(curCtx, r.files) +
      '<div class="meta"><code>git diff ' + esc(r.from.hash.slice(0, 7)) + " " + esc(r.to.hash.slice(0, 7)) + "</code></div>";
  }).catch(e => {
    detailView.innerHTML = btns + '<p class="hint">' + esc(t("error")) + esc(e.message) + "</p>";
  });
}
function updateCmpClasses() {  // 選択中のコミットを、グラフのノードとログの行の両方でハイライトする
  document.querySelectorAll(".cmpA, .cmpB, .actionTarget").forEach(el => el.classList.remove("cmpA", "cmpB", "actionTarget"));
  const compareRowsA = listView.querySelector("#compareRowsA");
  const compareRowsB = listView.querySelector("#compareRowsB");
  const rowsFor = (hash, side) => {
    const root = compareRowsA && compareRowsB ? (side === "A" ? compareRowsA : compareRowsB) : listView;
    return root.querySelectorAll('tr[data-h="' + hash + '"]');
  };
  if (compareAHash) {
    if (nodeEl[compareAHash]) nodeEl[compareAHash].classList.add("cmpA");
    rowsFor(compareAHash, "A").forEach(tr => tr.classList.add("cmpA"));
  }
  if (compareBHash) {
    if (nodeEl[compareBHash]) nodeEl[compareBHash].classList.add("cmpB");
    rowsFor(compareBHash, "B").forEach(tr => tr.classList.add("cmpB"));
  }
  if (actionHash) {
    if (nodeEl[actionHash]) nodeEl[actionHash].classList.add("actionTarget");
    listView.querySelectorAll('tr[data-h="' + actionHash + '"]').forEach(tr => tr.classList.add("actionTarget"));
  }
  const actionTarget = document.getElementById("actionTarget");
  actionTarget.textContent = actionHash ? t("target_label") + ": " + actionHash.slice(0, 7) : "";
  actionTarget.hidden = !actionHash;
  actionTarget.title = t("branch_log");
  const cmpA = document.getElementById("cmpA"), cmpB = document.getElementById("cmpB");
  cmpA.textContent = compareAHash ? "A: " + compareAHash.slice(0, 7) : "";
  cmpB.textContent = compareBHash ? "B: " + compareBHash.slice(0, 7) : "";
  cmpA.hidden = !compareAHash; cmpB.hidden = !compareBHash;
  cmpA.title = cmpB.title = t("branch_log");
  if (!actionHash) cmpActOpen = false;
  if (!compareAHash || !compareBHash) abActOpen = false;
  document.getElementById("cmpActToggle").disabled = !actionHash;
  document.getElementById("abActToggle").disabled = !compareAHash || !compareBHash;
  document.getElementById("swapAbBtn").hidden = !compareAHash || !compareBHash;
  document.getElementById("cmpActToggle").setAttribute("aria-expanded", cmpActOpen ? "true" : "false");
  document.getElementById("cmpActToggle").textContent = cmpActOpen ? t("actions").replace("▾", "▴") : t("actions");
  document.getElementById("abActToggle").setAttribute("aria-expanded", abActOpen ? "true" : "false");
  document.getElementById("abActToggle").textContent = abActOpen ? t("ab_actions").replace("▾", "▴") : t("ab_actions");
  document.getElementById("clearCmpBtn").disabled = !compareAHash && !compareBHash && !actionHash;
  document.getElementById("cmpActions").hidden = !cmpActOpen;
  document.getElementById("abActions").hidden = !abActOpen;
  if (!cmpActOpen) restoreMenuPosition(document.getElementById("cmpActions"));
  if (!abActOpen) restoreMenuPosition(document.getElementById("abActions"));
  document.getElementById("compareBtn").hidden = !compareAHash || !compareBHash;
  document.getElementById("compareLogsBtn").hidden = !compareAHash || !compareBHash;
  document.getElementById("rebaseRangeBtn").hidden = !compareAHash || !compareBHash;
  document.getElementById("rebaseDescendantsBtn").hidden = !compareAHash || !compareBHash;
  document.getElementById("rebaseEquivalentBtn").hidden = !compareAHash || !compareBHash;
  document.getElementById("findEquivalentParentsBtn").hidden = !compareAHash || !compareBHash;
  document.getElementById("findEquivalentBranchBtn").hidden = !compareAHash || !compareBHash;
  const isStash = !!actionHash && !!stashByHash[actionHash];
  document.getElementById("aActions").hidden = !actionHash || isStash;
  document.getElementById("stashActions").hidden = !isStash;
  document.getElementById("actionsEmpty").hidden = !!actionHash || (!!compareAHash && !!compareBHash);
  const canMerge = !!actionHash && !isStash && !!DATA.writable && !!DATA.merge;
  document.getElementById("mergeBtn").disabled = !canMerge;
  document.getElementById("rebaseBtn").disabled = !canMerge;
  document.getElementById("irebaseBtn").disabled = !canMerge;
  document.getElementById("rebaseRangeBtn").disabled = !compareAHash || !compareBHash || !canMerge;
  document.getElementById("rebaseDescendantsBtn").disabled = !compareAHash || !compareBHash || !canMerge;
  document.getElementById("rebaseEquivalentBtn").disabled = !compareAHash || !compareBHash || !DATA.writable || !DATA.merge;
  document.getElementById("findEquivalentParentsBtn").disabled = !compareAHash || !compareBHash;
  document.getElementById("findEquivalentBranchBtn").disabled = !compareAHash || !compareBHash;
  document.getElementById("cherryBtn").disabled = !canMerge;
  document.getElementById("revertBtn").disabled = !canMerge;
  document.getElementById("resetBtn").disabled = !canMerge;
  const canWrite = !!DATA.writable;
  document.querySelectorAll("#aActions button")
    .forEach(el => { el.disabled = !canWrite || (["mergeBtn", "rebaseBtn", "irebaseBtn", "cherryBtn", "revertBtn", "resetBtn"].includes(el.id) && !DATA.merge); });
  document.querySelectorAll("#remoteActions button")
    .forEach(el => { el.disabled = !canWrite || (el.dataset.cact === "pull" && !DATA.merge); });
  document.querySelectorAll("#remoteConfigActions button").forEach(el => { el.disabled = !canWrite; });
  updatePushActions();
  document.querySelector('#remoteActions [data-cact="pull"]').title =
    DATA.merge ? "" : t("pull_needs_worktree");
  document.getElementById("stashApplyBtn").disabled = !DATA.merge;  // apply / pop はワークツリーが必要
  document.getElementById("stashPopBtn").disabled = !DATA.merge;
  document.getElementById("stashDropBtn").disabled = !canWrite;
  document.getElementById("deleteBranchBtn").disabled = !canWrite || !actionHash || isStash ||
    !localBranches(actionHash).some(name => name !== currentBranch);
  document.getElementById("deleteRemoteBranchBtn").disabled = !canWrite || !actionHash || isStash || !remoteBranches(actionHash).length;
  document.getElementById("deleteTagBtn").disabled = !canWrite || !actionHash || isStash || !localTags(actionHash).length;
  document.getElementById("pushSelBtn").disabled = !canWrite || !actionHash || isStash || !localBranches(actionHash).length;
  document.getElementById("renameBranchBtn").disabled = !canWrite || !actionHash || isStash || !localBranches(actionHash).length;
}
