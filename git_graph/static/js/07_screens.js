function restoreScreen(state) {
  if (Object.prototype.hasOwnProperty.call(state, "compareA")) compareAHash = state.compareA;
  if (Object.prototype.hasOwnProperty.call(state, "compareB")) compareBHash = state.compareB;
  if (Object.prototype.hasOwnProperty.call(state, "actionHash")) actionHash = state.actionHash;
  cmpActOpen = false;  // メニューは画面の復元では開かない (グラフを隠さない)
  if (state.screen === "log") {
    const kept = listView.querySelector("#rows");  // 詳細などから戻ったとき、同じログがそのまま残っていれば読み直さない
    if (kept && kept.dataset.key === logKey(state.titleHtml, state.rev, state.topHtml, state.src, state.targetHash)) {
      document.body.classList.remove("compare-logs-mode");
      setDocumentTitleFromHtml(state.titleHtml);
      showView("list", true);
    } else showLog(state.titleHtml, state.rev, state.topHtml, true, state.scroll || 0, state.src, state.targetHash);
  }
  else if (state.screen === "detail") showDetail(state.hash, true);
  else if (state.screen === "compare") {
    compareAHash = state.a; compareBHash = state.b;
    showCompare(state.a, state.b, true);
  }
  else if (state.screen === "compare-logs") {
    compareAHash = state.a; compareBHash = state.b;
    showCompareLogs(state.a, state.b, true, state.scroll || 0);
  }
  else if (state.screen === "wip") showWip(true);
  else {
    if (observer) { observer.disconnect(); observer = null; }
    ++token;
    document.body.classList.remove("compare-logs-mode");
    listView.innerHTML = homeHtml;
    showView("list");
    setDocumentTitle("");
  }
  updateCmpClasses();
}
window.addEventListener("popstate", ev => restoreScreen(ev.state || {screen: "home"}));
function showFileLogInGraph(hash, path) {
  showLog(t("file_log_heading", {path: esc(path)}), hash,
          '<div class="meta"><code>git log ' + esc(hash.slice(0, 7)) + " -- " + esc(path) + "</code></div>",
          false, 0, {path: "/api/log", params: {rev: hash, path: path}});
}
window.addEventListener("message", ev => {
  if (ev.origin !== location.origin || !ev.data) return;
  if (ev.data.type === "git-graph-commit") {
    if (typeof ev.data.hash === "string" && ev.data.hash) showContainingBranchLog(ev.data.hash);
    return;
  }
  if (ev.data.type !== "git-graph-file-log") return;
  const hash = ev.data.hash, path = ev.data.path;
  if (typeof hash !== "string" || typeof path !== "string" || !hash || !path) return;
  showFileLogInGraph(hash, path);
});

listView.addEventListener("click", ev => {
  if (ev.target.closest(".back-btn")) { history.back(); return; }
  if (ev.detail > 1) return;
  const tr = ev.target.closest("tr.clickable");
  if (!tr) return;
  if (ev.target.closest(".copy")) return;
  // ctrl+クリック: グラフのノードと同じく、選択中のコミットを A、クリックしたコミットを B にして比較できるようにする
  if ((ev.ctrlKey || ev.metaKey) && actionHash && actionHash !== tr.dataset.h) {
    const h = tr.dataset.h;
    compareAHash = actionHash;
    compareBHash = h;
    cmpActOpen = false; abActOpen = false; setOtherMenu(false);
    if (curRow) curRow.classList.remove("cur");
    curRow = tr; tr.classList.add("cur");
    if (nodeEl[h]) select(nodeEl[h]);  // グラフにもあれば、そのノードを選択状態にする
    updateCmpClasses();
    saveUiState();
    revealHash(h);
    return;
  }
  if (ev.ctrlKey || ev.metaKey) return;  // 選択中のコミット自身への ctrl+クリックでは、何もしない (詳細を開かない)
  // 1 回目のクリックは選択だけ。選択済みのコミットをもう一度クリックしたらログ詳細を開く
  const already = tr.dataset.h === actionHash;
  selectActionTarget(tr.dataset.h);
  if (curRow) curRow.classList.remove("cur");
  curRow = tr; tr.classList.add("cur");
  // Prevent opening the operation menu on click; just select the commit.
  cmpActOpen = false; abActOpen = false;
  closeMenus();
  updateCmpClasses();
  if (already) showDetail(tr.dataset.h);
});
listView.addEventListener("contextmenu", ev => {  // ログ一覧の右クリック (比較ログの行も対象): 右ペインは動かさず、その行を操作対象にしてメニューを開く
  const tr = ev.target.closest("tr.clickable");
  if (!tr || ev.target.closest("a, input, select, textarea")) return;
  ev.preventDefault();
  const h = tr.dataset.h;
  if (compareAHash && compareBHash && (h === compareAHash || h === compareBHash)) {  // A/B の行は AB操作メニュー (グラフのノードと同じ)
    cmpActOpen = false; abActOpen = true; setOtherMenu(false); abMenuHash = h;
    updateCmpClasses();
    positionMenuNearPointer(document.getElementById("abActions"), ev);
  } else {
    selectActionTarget(h);
    cmpActOpen = true; abActOpen = false; setOtherMenu(false);
    updateCmpClasses();
    positionMenuNearPointer(document.getElementById("cmpActions"), ev);
  }
  saveUiState();
});
detailView.addEventListener("click", ev => {
  if (ev.target.closest("#wipReload")) { showWip(true); refreshGraph().catch(() => {}); return; }
  if (ev.target.closest("#wipCommit")) { doWipCommit(); return; }
  if (ev.target.closest("#wipStash")) { doWipStash(); return; }
  if (ev.target.closest("#wipDiscard")) { doWipDiscard(); return; }
  if (ev.target.closest("#wipRm")) { doWipRm(); return; }
  if (ev.target.closest("#wipAbort")) { doWipAbort(); return; }
  const rb = ev.target.closest("[data-resolve]");
  if (rb) { doWipResolve(rb.dataset.resolve); return; }
  const ab = ev.target.closest(".abtn");
  if (ab) { doAction(ab); return; }
  if (ev.target.closest(".back-btn")) { history.back(); return; }
  const dr = ev.target.closest("tr.drow");
  if (dr) {  // ディレクトリ行: 配下のファイルを表示 / 非表示
    if (ev.target.closest("input")) return;
    const key = dr.dataset.kind + "|" + dr.dataset.dir, open = !wipOpenDirs.has(key);
    if (open) wipOpenDirs.add(key); else wipOpenDirs.delete(key);
    saveWipOpenDirs();
    dr.querySelector(".dtog").textContent = open ? "▼" : "▶";
    dr.closest("table").querySelectorAll("tr.frow").forEach(r => {
      if (r.dataset.dir === dr.dataset.dir) r.classList.toggle("dhide", !open);
    });
    return;
  }
  const tr = ev.target.closest("tr.frow");
  if (tr && !ev.target.closest("a, input, td.fk") && curCtx)  // 行のどこをクリックしても差分を開く
    location.href = diffUrl(curCtx, curFiles[+tr.dataset.k]);
});
// 中クリック/Ctrl・Cmdクリックは、ブラウザの opener 設定に関係なく
// ファイル画面を別タブで開くことを明示する。
function openFilesInNewTab(ev) {
  const link = ev.target.closest("a.detail-files");
  if (!link) return;
  const newTabClick = ev.type === "auxclick" ? ev.button === 1 : (ev.ctrlKey || ev.metaKey);
  if (!newTabClick) return;
  ev.preventDefault();
  const url = new URL(link.href, location.href);
  url.searchParams.set("file_view", "1");
  window.open(url.href, "_blank", "noopener,noreferrer");
}
document.addEventListener("auxclick", openFilesInNewTab);
document.addEventListener("click", openFilesInNewTab);
document.getElementById("compareToolbar").addEventListener("click", ev => {  // メニューバー (操作 / push / pull / fetch / その他)
  const unsetBtn = ev.target.closest("[data-unset-cmp]");
  if (unsetBtn) {  // AB操作メニューの「Aのセット解除 / Bのセット解除」
    if (unsetBtn.dataset.unsetCmp === "A") compareAHash = null; else compareBHash = null;
    closeMenus(); updateCmpClasses(); saveUiState();
    return;
  }
  const setBtn = ev.target.closest("[data-set-cmp]");
  if (setBtn && actionHash) {  // 操作メニューの「Aにセット / Bにセット」(セット済みなら解除)
    const slot = setBtn.dataset.setCmp;
    if (actionHash === (slot === "A" ? compareAHash : compareBHash)) {
      if (slot === "A") compareAHash = null; else compareBHash = null;
      closeMenus(); updateCmpClasses(); saveUiState();
      return;
    }
    const near = document.getElementById("cmpActions").classList.contains("near-pointer");  // 右クリックで開いたメニューか
    selectCompareHash(setBtn.dataset.setCmp, actionHash);  // A/B が揃うと操作メニューは閉じて AB操作メニューが開く
    if (abActOpen && near) { abMenuHash = actionHash; updateCmpClasses(); positionMenuNearPointer(document.getElementById("abActions"), ev); }  // いまセットしたコミットの解除だけ出す
    else if (!abActOpen) closeMenus();
    return;
  }
  if (ev.target.closest("#actionTarget") && actionHash) {
    showContainingBranchLog(actionHash);
    return;
  }
  if (ev.target.closest("#cmpA") && compareAHash) {
    showContainingBranchLog(compareAHash);
    return;
  }
  if (ev.target.closest("#cmpB") && compareBHash) {
    showContainingBranchLog(compareBHash);
    return;
  }
  if (ev.target.closest("#swapAbBtn")) {
    [compareAHash, compareBHash] = [compareBHash, compareAHash];
    updateCmpClasses();
    saveUiState();
    return;
  }
  if (ev.target.closest("#abActToggle")) {
    abMenuHash = null;  // ツールバーから開いたときは、A / B どちらの解除も出す
    abActOpen = !abActOpen;
    if (abActOpen) { cmpActOpen = false; setOtherMenu(false); }
    updateCmpClasses();
    saveUiState();
    return;
  }
  if (ev.target.closest("#otherMenuBtn")) {
    const open = document.getElementById("otherActions").hidden;
    if (open && (cmpActOpen || abActOpen)) { cmpActOpen = false; abActOpen = false; updateCmpClasses(); }  // 同時に開くのは1つだけ
    setOtherMenu(open);
    return;
  }
  if (ev.target.closest("#compareBtn") && compareAHash && compareBHash) {
    showCompare(compareAHash, compareBHash);
    return;
  }
  if (ev.target.closest("#compareWipBtn") && actionHash) {
    showCompare(actionHash, "WIP");
    return;
  }
  if (ev.target.closest("#compareLogsBtn") && compareAHash && compareBHash) {
    showCompareLogs(compareAHash, compareBHash);
    return;
  }
  if (ev.target.closest("#rebaseRangeBtn")) { doRebaseRange(); return; }
  if (ev.target.closest("#rebaseDescendantsBtn")) { doRebaseDescendants(); return; }
  if (ev.target.closest("#rebaseEquivalentBtn")) { doRebaseEquivalent(); return; }
  if (ev.target.closest("#findEquivalentParentsBtn")) { doFindEquivalentParents(); return; }
  if (ev.target.closest("#findEquivalentBranchBtn")) { doFindEquivalentBranch(); return; }
  if (ev.target.closest("#branchListBtn")) { showRefList("branches"); return; }
  if (ev.target.closest("#tagListBtn")) { showRefList("tags"); return; }
  if (ev.target.closest("#stashListBtn")) { showStashList(); return; }
  if (ev.target.closest("#stashApplyBtn")) { doStash("apply"); return; }
  if (ev.target.closest("#stashPopBtn")) { doStash("pop"); return; }
  if (ev.target.closest("#stashDropBtn")) { doStash("drop"); return; }
  if (ev.target.closest("#reflogBtn")) { showReflog(""); return; }
  if (ev.target.closest("#opHistoryBtn")) { showOpHistory(); return; }
  if (ev.target.closest("#deleteBranchBtn")) { doDeleteBranch(ev.target.closest("#deleteBranchBtn")); return; }
  if (ev.target.closest("#deleteRemoteBranchBtn")) { doDeleteRemoteBranch(ev.target.closest("#deleteRemoteBranchBtn")); return; }
  if (ev.target.closest("#deleteTagBtn")) { doDeleteTag(ev.target.closest("#deleteTagBtn")); return; }
  if (ev.target.closest("#mergeBtn")) { doMerge(); return; }
  if (ev.target.closest("#rebaseBtn")) { doRebase(); return; }
  if (ev.target.closest("#irebaseBtn")) { doRebaseI(); return; }
  if (ev.target.closest("#cherryBtn")) { doCherryPick(); return; }
  if (ev.target.closest("#revertBtn")) { doRevert(); return; }
  if (ev.target.closest("#renameBranchBtn")) { doRenameBranch(ev.target.closest("#renameBranchBtn")); return; }
  if (ev.target.closest("#resetBtn")) { doReset(); return; }
  const cab = ev.target.closest(".abtn[data-cact]");
  if (cab) {
    const act = cab.dataset.cact;
    if (act === "delete-branch") doDeleteBranch(cab);
    else if (act === "delete-tag") doDeleteTag(cab);
    else (["fetch", "pull", "push", "push-tag"].includes(act) ? doRemoteAction : act.startsWith("remote-") ? doRemoteConfig : doCmpAction)(cab);
    return;
  }
});
document.getElementById("compareToolbar").addEventListener("click", ev => {
  if (ev.target.closest("#cmpActToggle")) {
    cmpActOpen = !cmpActOpen;
    if (cmpActOpen) { abActOpen = false; setOtherMenu(false); }  // 同時に開くのは1つだけ
    updateCmpClasses();
    saveUiState();
  } else if (ev.target.closest("#clearCmpBtn")) {
    clearCmp();
  }
});
document.getElementById("compareToolbar").addEventListener("click", ev => {  // メニューの項目を選んだら閉じる
  if (ev.target.closest(".dropdown .abtn:not([data-set-cmp])")) closeMenus();  // 無効なボタンはクリック自体が発火しない
});
document.addEventListener("click", ev => {  // メニューの外をクリックしたら閉じる
  if (ev.target.closest(".menu-wrap") || ev.target.closest("svg g.node") || ev.target.closest("#panel")) return;  // 右ペインのクリックでは閉じない  // ノードのクリックは onNodeClick が扱う
  if (ev.target.closest("#graph") && !ev.target.closest("button, a, input, select, label")) { clearSelection(); return; }  // グラフの何もないところをクリック: 選択を解除する
  closeMenus();
});
document.addEventListener("keydown", ev => {
  if (ev.key !== "Escape" || ev.defaultPrevented) return;  // ダイアログが Escape を処理したときは何もしない
  closeMenus();
  const tag = ev.target && ev.target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;  // 入力中の Escape では選択を解除しない
  if (document.querySelector('[id$="Modal"]:not([hidden])')) return;  // ダイアログを開いているあいだも解除しない
  clearSelection();
});
function select(g) {
  if (selected) selected.classList.remove("sel");
  selected = g; g.classList.add("sel");
  const t = g.querySelector("title");
  selectedKey = t ? t.textContent : null;  // グラフを作り直したあとで選択を復元するため
}

function onNodeClick(g, n, ev) {
  const openMenu = ev && ev.type === "contextmenu";
  if (openMenu && compareAHash && compareBHash && (n.hash === compareAHash || n.hash === compareBHash)) {
    cmpActOpen = false; abActOpen = true; setOtherMenu(false); abMenuHash = n.hash;
    select(g);
    updateCmpClasses();
    positionMenuNearPointer(document.getElementById("abActions"), ev);
    saveUiState();
    return;
  }
  if (ev && ev.type !== "contextmenu" && (ev.ctrlKey || ev.metaKey) && actionHash && actionHash !== n.hash && !n.wip) {
    compareAHash = actionHash;
    compareBHash = n.hash;
    cmpActOpen = false; abActOpen = openMenu; setOtherMenu(false);
    select(g);
    updateCmpClasses();
    if (openMenu) positionMenuNearPointer(document.getElementById("abActions"), ev);
    saveUiState();
    return;
  }
  if (!n.wip) { cmpActOpen = !!openMenu; abActOpen = false; setOtherMenu(false); }
  if (n.wip) {  // 作業ツリーの変更 (WIP) ノード: ログではなく変更ファイルの一覧を出す
    clearCmp(); select(g); showWip();
    return;
  }
  selectActionTarget(n.hash, false);  // グラフ上のクリックなので、グラフは動かさない
  select(g);
  if (openMenu) positionMenuNearPointer(document.getElementById("cmpActions"), ev);
  showLog("git log --oneline " + esc(n.hash.slice(0, 7)), n.hash,
          chips(n.hash) + '<div class="meta">' + t("all_log_to_root") + '</div>');
}

// ---- コミットをドラッグ&ドロップして A / B にセットする (ドラッグ元が A、ドロップ先が B) ----
// グラフのノードとログ一覧の行のどちらも、ドラッグ元にもドロップ先にもなれる (図→図 / 行→図 / 図→行 / 行→行)。
// ドロップするとAB操作メニューが開く。ほとんど動かさずに離したときは、これまでどおりのクリックとして扱う
let nodeDrag = null, dragClickUntil = 0;
const DRAG_THRESHOLD = 6;  // px
function startNodeDrag(el, hash, ev) {  // el: ノード (g.node) またはログの行 (tr)
  if (ev.button !== 0 || ev.pointerType === "touch" || !hash) return;  // マウス / ペンの左ボタンだけ
  nodeDrag = {el: el, hash: hash, x: ev.clientX, y: ev.clientY, active: false, over: null, tip: null};
}
function dropTargetAt(x, y) {  // その位置にあるコミット (グラフのノードまたはログの行。WIP は除く)
  const at = document.elementFromPoint(x, y);
  if (!at) return null;
  const g = at.closest("svg g.node");
  if (g) {
    const t = g.querySelector("title"), n = t && DATA.nodes[t.textContent];
    return n && !n.wip ? {el: g, hash: n.hash, node: g} : null;
  }
  const tr = at.closest("tr.clickable");
  return tr && tr.dataset.h ? {el: tr, hash: tr.dataset.h, node: null} : null;
}
function endNodeDrag(ev, cancel) {
  const d = nodeDrag;
  nodeDrag = null;
  if (!d || !d.active) return;
  document.body.classList.remove("node-dragging");
  d.el.classList.remove("drag-src");
  if (d.over) d.over.classList.remove("drop-target");
  if (d.tip) d.tip.remove();
  dragClickUntil = Date.now() + 400;  // ドラッグの終わりに出る click は無かったことにする (背景のクリックで選択が外れないように)
  if (cancel) return;
  const hit = dropTargetAt(ev.clientX, ev.clientY);
  if (!hit || hit.hash === d.hash) return;  // 何もないところや自分自身に落としたら、何もしない
  compareAHash = d.hash; compareBHash = hit.hash;
  cmpActOpen = false; abActOpen = true; abMenuHash = null; setOtherMenu(false);
  if (hit.node) select(hit.node);
  else {  // ログの行に落としたときは、その行を選択状態にして、グラフにもあればそのノードも選択する (ctrl+クリックと同じ)
    if (curRow) curRow.classList.remove("cur");
    curRow = hit.el; hit.el.classList.add("cur");
    if (nodeEl[hit.hash]) select(nodeEl[hit.hash]);
  }
  updateCmpClasses();
  saveUiState();
  positionMenuNearPointer(document.getElementById("abActions"), {type: "contextmenu", clientX: ev.clientX, clientY: ev.clientY});
}
document.addEventListener("pointermove", ev => {
  const d = nodeDrag;
  if (!d) return;
  if (!d.active) {
    if (Math.hypot(ev.clientX - d.x, ev.clientY - d.y) < DRAG_THRESHOLD) return;
    d.active = true;
    closeMenus();
    if (window.getSelection) window.getSelection().removeAllRanges();
    document.body.classList.add("node-dragging");
    d.el.classList.add("drag-src");
    d.tip = document.createElement("div");
    d.tip.id = "dragTip";
    document.body.appendChild(d.tip);
  }
  const hit = dropTargetAt(ev.clientX, ev.clientY), over = hit && hit.hash !== d.hash ? hit.el : null;
  if (over !== d.over) {
    if (d.over) d.over.classList.remove("drop-target");
    if (over) over.classList.add("drop-target");
    d.over = over;
  }
  d.tip.textContent = "A: " + d.hash.slice(0, 7) + (over ? "  →  B: " + hit.hash.slice(0, 7) : "");
  d.tip.style.left = (ev.clientX + 14) + "px";
  d.tip.style.top = (ev.clientY + 14) + "px";
});
document.addEventListener("pointerup", ev => endNodeDrag(ev, false));
document.addEventListener("pointercancel", ev => endNodeDrag(ev, true));
document.addEventListener("keydown", ev => {  // ドラッグ中の Escape はドラッグだけを取り消す
  if (ev.key === "Escape" && nodeDrag && nodeDrag.active) { ev.stopPropagation(); endNodeDrag(ev, true); }
}, true);
document.addEventListener("click", ev => {
  if (Date.now() < dragClickUntil) { ev.stopPropagation(); ev.preventDefault(); }
}, true);
listView.addEventListener("pointerdown", ev => {  // ログ一覧の行からドラッグを始める (リンク・入力欄・コピー記号の上は除く)
  const tr = ev.target.closest("tr.clickable");
  if (!tr || ev.target.closest("a, input, select, textarea, .copy")) return;
  startNodeDrag(tr, tr.dataset.h, ev);
});

function init() {
  document.querySelectorAll("svg g.node").forEach(g => {
    const t = g.querySelector("title");
    const n = t && DATA.nodes[t.textContent];
    const cl = !n && t && DATA.clusters && DATA.clusters[t.textContent];
    if (cl) {  // 閉じたクラスター: クリックで開く
      g.classList.add("cluster-node");
      g.addEventListener("click", ev => { if (ev.detail <= 1) expandCluster(cl.id); });
      return;
    }
    if (n) {
      nodeEl[n.hash] = g;
      const box = g.getBBox(), hit = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      const padX = Math.max(0, (16 - box.width) / 2), padY = Math.max(0, (16 - box.height) / 2);  // 小さな点でもクリックしやすくする
      hit.setAttribute("x", box.x - padX); hit.setAttribute("y", box.y - padY);
      hit.setAttribute("width", box.width + padX * 2); hit.setAttribute("height", box.height + padY * 2);
      hit.setAttribute("fill", "transparent"); hit.setAttribute("pointer-events", "all");
      g.insertBefore(hit, g.children[1] || null);
      g.addEventListener("pointerdown", ev => { if (!n.wip) startNodeDrag(g, n.hash, ev); });  // WIP ノードは比較に使えない
      g.addEventListener("click", ev => { if (ev.detail <= 1) onNodeClick(g, n, ev); });
      g.addEventListener("contextmenu", ev => { ev.preventDefault(); onNodeClick(g, n, ev); });
    }
  });
  document.querySelectorAll("svg g.edge").forEach(g => {
    const t = g.querySelector("title");
    const e = DATA.edges[g.id] || (t && DATA.edges[t.textContent]);  // マージ表示の線は id で区別する
    if (!e) return;
    const path = g.querySelector("path");
    if (path) { const hit = path.cloneNode(); hit.setAttribute("class", "hit"); g.appendChild(hit); }
    g.addEventListener("click", () => {
      clearCmp(); select(g);
      // 線の両端は含めない。マージの線はその親の側 (from..親)、それ以外は from..to^@ (to の親たち)
      const rev = e.via ? e.from + ".." + e.via : e.from + ".." + e.to + "^@";
      const range = e.via ? e.from.slice(0, 7) + ".." + e.via.slice(0, 7) : "^" + e.from.slice(0, 7) + " " + e.to.slice(0, 7) + "^@";
      showLog(esc(e.from.slice(0, 7)) + " → " + esc(e.to.slice(0, 7)), rev,
              '<div class="meta"><code>git log ' + esc(range) + "</code></div>");
    });
  });
}
