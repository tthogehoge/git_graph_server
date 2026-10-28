const graphStatusTimers = new Map();
function setGraphStatus(message, type, clearProgress) {
  const container = document.getElementById("graphStatus");
  const progress = type === "progress";  // 進捗かどうかは表示文言ではなく type で明示する (文言は翻訳で変わる)
  if (clearProgress || !progress) {
    container.querySelectorAll(".graph-toast.progress").forEach(toast => {
      clearTimeout(graphStatusTimers.get(toast));
      graphStatusTimers.delete(toast);
      toast.remove();
    });
  }
  if (!message) return;
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
  return fetch(graphUrl())
    .then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }))
    .then(r => {
      const keys = new Set(Object.keys(DATA.nodes).concat(Object.keys(r.nodes)));
      const gc = document.getElementById("graph"), top0 = gc.scrollTop, left0 = gc.scrollLeft;
      const anchor = captureAnchor(gc);  // 選択中の要素が画面のどこにあるか (作り直したあとも同じ位置に置く)
      setGraphData(r);
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
      if (anchor) restoreAnchor(gc, anchor);
      updateCmpClasses();
      loadStashes();
      updateCurrentBranch().catch(e => setGraphStatus(t("current_branch_failed", {error: e.message}), "error"));
      keys.forEach(h => listView.querySelectorAll('tr[data-h="' + h + '"] .rc').forEach(el => { el.innerHTML = chips(h); }));
      const c = detailView.querySelector("#dChips");
      if (c && curDetailHash) c.innerHTML = detailChips(curDetailHash);
      setGraphStatus(r.notice || "", r.notice ? "error" : undefined);  // 大きすぎて表示を減らしたときは通知を出す
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
  const toLabel = b === "WIP" ? t("wip_title") : b.slice(0, 7);
  setDocumentTitle(t("compare_heading", {from: a.slice(0, 7), to: toLabel}));
  const btns = '<button class="back-btn">' + t("back_history") + '</button>';
  detailView.innerHTML = btns + '<p class="hint">' + esc(t("loading")) + '</p>';
  showView("detail");
  api("/api/compare", {from: a, to: b}).then(r => {
    const isWip = r.to.hash === "WIP";
    curCtx = isWip ? {from: r.from.hash, wip: "compare"} : {from: r.from.hash, to: r.to.hash};
    curFiles = r.files;
    const heading = t("compare_heading", {
      from: r.from.hash.slice(0, 7),
      to: isWip ? t("wip_title") : r.to.hash.slice(0, 7),
    });
    const toLine = isWip
      ? '<div class="meta"><span class="tag tagB">B</span>' + esc(t("wip_title")) + "</div>"
      : commitLine("B", r.to);
    const command = "git diff " + r.from.hash.slice(0, 7) + (isWip ? "" : " " + r.to.hash.slice(0, 7));
    const patchName = r.from.hash.slice(0, 7) + ".." + (isWip ? "wip" : r.to.hash.slice(0, 7)) + ".patch";
    detailView.innerHTML = btns + "<h2>" + esc(heading) +
      ' <a class="detail-patch" href="#" data-from="' + esc(r.from.hash) + '" data-to="' + esc(r.to.hash) + '" data-name="' + esc(patchName) +
      '" title="' + esc(t(isWip ? "tip_patch_create_compare_wip" : "tip_patch_create_compare")) + '">' + esc(t("patch_create")) + "</a></h2>" +
      commitLine("A", r.from) + toLine + filesHtml(curCtx, r.files) +
      '<div class="meta"><code>' + esc(command) + "</code></div>";
  }).catch(e => {
    detailView.innerHTML = btns + '<p class="hint">' + esc(t("error")) + esc(e.message) + "</p>";
  });
}
function updateCmpClasses() {  // 選択中のコミットを、グラフのノードとログの行の両方でハイライトする
  if (logGraphCur) logGraphCur.sync();  // アクション対象がマージコミットなら、ログのマージグラフでその線を全部描く
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
  const isStash = !!actionHash && (!!stashByHash[actionHash] || !!stashNameOf(actionHash));
  const compareWipBtn = document.getElementById("compareWipBtn");
  compareWipBtn.hidden = !actionHash || isStash || !WIP_ENABLED;
  compareWipBtn.disabled = !actionHash || isStash || !WIP_ENABLED;
  document.getElementById("aActions").hidden = !actionHash || isStash;
  document.getElementById("stashActions").hidden = !isStash;
  document.getElementById("actionsEmpty").hidden = !!actionHash || (!!compareAHash && !!compareBHash);
  const canMerge = !!actionHash && !isStash && !!DATA.writable && !!DATA.merge;
  document.getElementById("mergeBtn").disabled = !canMerge;
  document.getElementById("rebaseBtn").disabled = !canMerge;
  document.getElementById("irebaseBtn").disabled = !canMerge;
  // AB操作は A と B だけで決まる。操作対象 (actionHash) が選ばれていなくても使える (ドラッグ&ドロップでセットしたときなど)
  const canMergeAB = !!DATA.writable && !!DATA.merge;
  document.getElementById("rebaseRangeBtn").disabled = !compareAHash || !compareBHash || !canMergeAB;
  document.getElementById("rebaseDescendantsBtn").disabled = !compareAHash || !compareBHash || !canMergeAB;
  document.getElementById("rebaseEquivalentBtn").disabled = !compareAHash || !compareBHash || !canMergeAB;
  document.getElementById("findEquivalentParentsBtn").disabled = !compareAHash || !compareBHash;
  document.getElementById("findEquivalentBranchBtn").disabled = !compareAHash || !compareBHash;
  document.getElementById("cherryBtn").disabled = !canMerge;
  document.getElementById("revertBtn").disabled = !canMerge;
  document.getElementById("resetBtn").disabled = !canMerge;
  const canWrite = !!DATA.writable;
  document.querySelectorAll("#aActions button")
    .forEach(el => {
      if (el.id === "compareWipBtn" || el.dataset.setCmp) return;
      el.disabled = !canWrite || (["mergeBtn", "rebaseBtn", "irebaseBtn", "cherryBtn", "revertBtn", "resetBtn"].includes(el.id) && !DATA.merge);
    });
  // すでにそのスロットにセットされているコミットなら、ボタンは「セット解除」になる
  const setA = document.getElementById("setABtn"), setB = document.getElementById("setBBtn");
  setA.disabled = setB.disabled = !actionHash;
  setA.textContent = t(actionHash && actionHash === compareAHash ? "unset_a" : "set_as_a");
  setB.textContent = t(actionHash && actionHash === compareBHash ? "unset_b" : "set_as_b");
  // A/B のコミットを右クリックして開いたときは、そのコミットの側の解除だけ出す (A なら A、B なら B)
  const both = !!compareAHash && !!compareBHash;
  document.getElementById("unsetABtn").hidden = !both || (!!abMenuHash && abMenuHash !== compareAHash);
  document.getElementById("unsetBBtn").hidden = !both || (!!abMenuHash && abMenuHash !== compareBHash);
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

// ---- クラスター表示 (ノードが多いとき、サーバーが時期ごとにまとめて返す。クリックで展開する) ----
function setGraphData(r) {
  DATA = {nodes: r.nodes, edges: r.edges, writable: r.writable, merge: r.merge,
          clusters: r.clusters, clusterOf: r.clusterOf, open: r.open};
  openClusters = r.open || [];  // サーバーが数を絞った後の、開いているクラスター
  locateCache.clear();
}
let clusterBusy = false;
function expandCluster(ids) {  // 開いたものを新しい扱いにして描き直す。多くなったら、サーバーが古い方から閉じる
  const list = (Array.isArray(ids) ? ids : [ids]).filter(Boolean);
  if (!list.length || clusterBusy) return Promise.resolve();
  clusterBusy = true;
  const before = openClusters;
  let next = openClusters;
  for (const id of list) next = next.filter(x => x !== id).concat([id]);
  openClusters = next;
  const lastId = list[list.length - 1];
  return refreshGraph()
    .then(() => revealCluster(lastId))
    .catch(() => { openClusters = before; })
    .finally(() => { clusterBusy = false; });
}
function revealCluster(id) {
  const g = [...document.querySelectorAll("#graph g.cluster")].find(x => {
    const title = x.querySelector("title"); return title && title.textContent === "cluster_" + id;
  });
  if (g) ensureGraphVisible(g, true);
}
const locateCache = new Map();  // コミット -> グラフのどこにあるか (グラフを作り直したら捨てる)
function locateCommit(h) {
  const key = h + (showMergesOn() ? "|m" : "");
  if (!locateCache.has(key)) {
    locateCache.set(key, fetch("/api/graph_focus?hash=" + encodeURIComponent(h) + (showMergesOn() ? "&merges=1" : ""))
      .then(r => r.ok ? r.json() : null).catch(() => null));
  }
  return locateCache.get(key);
}
function graphElementFor(r) {  // 描いてあるものを、線 -> 両端のノード -> クラスターの順に探す
  const gc = document.getElementById("graph");
  const titleOf = g => { const tt = g.querySelector("title"); return tt ? tt.textContent : ""; };
  if (r.edge) {
    const e = [...gc.querySelectorAll("g.edge")].find(g => g.id === r.edge || titleOf(g) === r.edge);
    if (e) return e;
    // 閉じたクラスターに繋がるまとめ線 (クラスター同士 / ノードとクラスター) を探す
    if (r.from && r.to) {
      const rep = k => nodeEl[k] ? k : ((DATA.clusterOf && DATA.clusterOf[k]) || k);
      const rd = rep(r.from), rc = rep(r.to);
      if (rd !== r.from || rc !== r.to) {
        const ce = [...gc.querySelectorAll("g.edge")].find(g => titleOf(g) === rd + "->" + rc);
        if (ce) return ce;
      }
    }
  }
  for (const k of [r.from, r.to]) if (k && nodeEl[k]) return nodeEl[k];
  if (r.cluster) {
    return [...gc.querySelectorAll("g.node, g.cluster")].find(g => titleOf(g) === r.cluster || titleOf(g) === "cluster_" + r.cluster) || null;
  }
  return null;
}
let revealTarget = "";
function revealHash(h) {
  // 選んだコミットをグラフの真ん中に表示する: ノードならそのノード、ノードでないコミットはそれが省略されている線
  // (描いていなければ両端のノード / 含むクラスター)。含むクラスターが閉じていたら先に開く
  if (!h) return;
  revealTarget = h;
  clearLocate();
  if (nodeEl[h]) { ensureGraphVisible(nodeEl[h], true); return; }
  locateCommit(h).then(r => {
    if (!r || revealTarget !== h) return;
    const show = () => {
      const el = graphElementFor(r);
      if (!el || revealTarget !== h) return;
      ensureGraphVisible(el, true);
      markLocated(el, h);
    };
    if (DATA.clusters) {
      const want = [];
      const cFrom = DATA.clusterOf && DATA.clusterOf[r.from];
      const cTo = DATA.clusterOf && DATA.clusterOf[r.to];
      for (const c of [cFrom, cTo]) {
        if (c && c !== r.cluster && !want.includes(c)) want.push(c);
      }
      if (r.cluster && !want.includes(r.cluster)) want.push(r.cluster);
      const need = want.filter(c => !(DATA.open || []).includes(c));
      if (need.length) {
        expandCluster(need).then(show);
        return;
      }
    }
    show();
  });
}

// ---- ノードでないコミットを選んだとき、グラフ上のどの線かを示す ----
function clearLocate() {
  document.querySelectorAll("#graph .locate, #graph g.locate-mark").forEach(e => {
    if (e.classList.contains("locate-mark")) e.remove(); else e.classList.remove("locate");
  });
}
function markLocated(el, h) {  // 線を目立たせ、そのコミットがある位置 (線の真ん中) に印とハッシュを出す
  clearLocate();
  el.classList.add("locate");
  const path = el.classList.contains("edge") ? el.querySelector("path") : null;
  if (!path || !path.getTotalLength) return;
  const pt = path.getPointAtLength(path.getTotalLength() / 2), NS = "http://www.w3.org/2000/svg";
  const mark = document.createElementNS(NS, "g");
  mark.setAttribute("class", "locate-mark");
  const dot = document.createElementNS(NS, "circle");
  dot.setAttribute("cx", pt.x); dot.setAttribute("cy", pt.y); dot.setAttribute("r", 5);
  const label = document.createElementNS(NS, "text");
  label.setAttribute("x", pt.x + 9); label.setAttribute("y", pt.y + 4);
  label.textContent = h.slice(0, 7);
  mark.append(dot, label);
  el.parentNode.appendChild(mark);  // 線と同じ座標系 (グラフの <g>) に置く
}

// ---- グラフのスクロール ----
function ensureGraphVisible(el, center) {  // el が見える位置までグラフをスクロールする (見えているなら動かさない)
  const gc = document.getElementById("graph");
  const r = el.getBoundingClientRect(), c = gc.getBoundingClientRect(), m = 24;
  const inside = r.left >= c.left + m && r.right <= c.right - m && r.top >= c.top + m && r.bottom <= c.bottom - m;
  if (inside && !center) return;
  gc.scrollLeft += (r.left + r.right) / 2 - (c.left + c.right) / 2;
  gc.scrollTop += (r.top + r.bottom) / 2 - (c.top + c.bottom) / 2;
}
function findGraphEl(gc, key) {  // key (ノードのハッシュ / 線 / クラスター) の要素。無ければ、それを含むクラスターの箱
  const all = [...gc.querySelectorAll("g.node, g.edge")];
  const byTitle = k => all.find(x => { const t = x.querySelector("title"); return t && t.textContent === k; });
  return byTitle(key) || (DATA.clusterOf && DATA.clusterOf[key] ? byTitle(DATA.clusterOf[key]) : null);
}
function captureAnchor(gc) {  // 選択中の要素の、グラフ表示領域の中での位置
  if (!selectedKey) return null;
  const el = findGraphEl(gc, selectedKey);
  if (!el) return null;
  const r = el.getBoundingClientRect(), c = gc.getBoundingClientRect();
  return {key: selectedKey, dx: r.left - c.left, dy: r.top - c.top};
}
function restoreAnchor(gc, a) {  // グラフを作り直して配置が変わっても、選択中の要素を同じ位置に戻す
  const el = findGraphEl(gc, a.key);
  if (!el) return;
  const r = el.getBoundingClientRect(), c = gc.getBoundingClientRect();
  gc.scrollLeft += (r.left - c.left) - a.dx;
  gc.scrollTop += (r.top - c.top) - a.dy;
}
