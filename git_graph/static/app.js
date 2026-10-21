/*__I18N__*/
function t(key, values = {}) {  // {name} を values[name] に置き換える (値の中の $ は特別扱いしない)
  let text = key in I18N ? I18N[key] : key;
  Object.entries(values).forEach(([name, value]) => { text = text.replaceAll("{" + name + "}", () => String(value)); });
  return text;
}

let DATA = {nodes: {}, edges: {}};
const COLORS = {branch: "#b7e4a7", current: "#b7e4a7", remote: "#a7c8f0", tag: "#ffe9a0", head: "#f4a7a7"};
const CHUNK = 500;
const listView = document.getElementById("listView"), detailView = document.getElementById("detailView");
const views = {list: listView, detail: detailView}, savedScroll = {list: 0, detail: 0};
let curView = "list", selected = null, selectedKey = null, token = 0, observer = null, curRow = null;
let curCtx = null, curFiles = [];
let compareAHash = null, compareBHash = null;
let actionHash = null;  // マージ / リベースなどの操作対象
let currentBranch = null;
let currentBranchLoaded = false;
const nodeEl = {};  // ハッシュ -> グラフ上のノード要素
let graphZoom = 1;
const homeHtml = listView.innerHTML;
if (!history.state || !history.state.screen) history.replaceState({screen: "home"}, "");

function applyGraphZoom() {
  const svg = document.querySelector("#graph > svg");
  if (!svg) return;
  svg.style.transformOrigin = "0 0";
  svg.style.transform = "scale(" + graphZoom + ")";
  const reset = document.getElementById("graphZoomReset");
  if (reset) reset.textContent = Math.round(graphZoom * 100) + "%";
}
function resetGraphZoom() {
  const graph = document.getElementById("graph");
  const oldZoom = graphZoom;
  if (oldZoom === 1) return;
  const x = graph.scrollLeft + graph.clientWidth / 2;
  const y = graph.scrollTop + graph.clientHeight / 2;
  graphZoom = 1;
  applyGraphZoom();
  graph.scrollLeft = x / oldZoom - graph.clientWidth / 2;
  graph.scrollTop = y / oldZoom - graph.clientHeight / 2;
}
document.getElementById("graph").addEventListener("wheel", ev => {
  if (!ev.ctrlKey && !ev.metaKey) return;
  ev.preventDefault();  // Ctrl+ホイールによるブラウザ全体のズームをグラフのズームに置き換える
  const graph = ev.currentTarget;
  const rect = graph.getBoundingClientRect();
  const x = graph.scrollLeft + ev.clientX - rect.left;
  const y = graph.scrollTop + ev.clientY - rect.top;
  const oldZoom = graphZoom;
  graphZoom = Math.max(0.25, Math.min(4, graphZoom * Math.pow(1.1, -ev.deltaY / 100)));
  if (graphZoom === oldZoom) return;
  applyGraphZoom();
  graph.scrollLeft = x * graphZoom / oldZoom - (ev.clientX - rect.left);
  graph.scrollTop = y * graphZoom / oldZoom - (ev.clientY - rect.top);
}, {passive: false});
document.getElementById("graphZoomReset").addEventListener("click", resetGraphZoom);
document.addEventListener("keydown", ev => {
  if ((ev.ctrlKey || ev.metaKey) && ev.key === "0") {
    ev.preventDefault();
    resetGraphZoom();
  }
});

const splitter = document.getElementById("splitter");
let resizing = false;
const PANEL_WIDTH_KEY = "git-graph-panel-width";
const COMPARE_PANEL_WIDTH_KEY = "git-graph-compare-panel-width";
function applyPanelWidth(width, compareLogs = document.body.classList.contains("compare-logs-mode")) {
  const property = compareLogs ? "--compare-panel-width" : "--panel-width";
  document.documentElement.style.setProperty(property, Math.max(240, Math.min(window.innerWidth - 166, width)) + "px");
}
try {  // 別ページ (差分など) から戻ったときも、右ペインの幅を保つ
  const saved = parseInt(localStorage.getItem(PANEL_WIDTH_KEY), 10);
  if (saved > 0) applyPanelWidth(saved);
  const compareSaved = parseInt(localStorage.getItem(COMPARE_PANEL_WIDTH_KEY), 10);
  if (compareSaved > 0) applyPanelWidth(compareSaved, true);
} catch (e) {}
splitter.addEventListener("pointerdown", ev => {
  resizing = true;
  splitter.setPointerCapture(ev.pointerId);
  document.body.classList.add("resizing");
  ev.preventDefault();
});
splitter.addEventListener("pointermove", ev => {
  if (!resizing) return;
  applyPanelWidth(window.innerWidth - ev.clientX);
});
function stopResizing() {
  if (resizing) {
    try {
      const compareLogs = document.body.classList.contains("compare-logs-mode");
      localStorage.setItem(compareLogs ? COMPARE_PANEL_WIDTH_KEY : PANEL_WIDTH_KEY,
                           String(parseInt(getComputedStyle(document.getElementById("panel")).width, 10)));
    } catch (e) {}
  }
  resizing = false;
  document.body.classList.remove("resizing");
}
splitter.addEventListener("pointerup", stopResizing);
splitter.addEventListener("pointercancel", stopResizing);

function esc(s) {
  return String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
}
const DEFAULT_DOCUMENT_TITLE = document.title;
const PROJECT_DOCUMENT_TITLE = DEFAULT_DOCUMENT_TITLE.replace(/ - git graph$/, "");
function setDocumentTitle(label) {
  document.title = label ? label + " - " + PROJECT_DOCUMENT_TITLE : DEFAULT_DOCUMENT_TITLE;
}
function setDocumentTitleFromHtml(titleHtml) {
  const el = document.createElement("span");
  el.innerHTML = titleHtml;
  setDocumentTitle(el.textContent || el.innerText || "");
}
function chips(h) {
  const n = DATA.nodes[h];
  if (!n) return "";
  return n.refs.map(r => {
    const current = r.kind === "current" || (r.kind === "branch" && r.name === currentBranch);
    return '<span class="chip" style="background:' + COLORS[r.kind] + '"' +
      (current ? ' title="' + esc(t("current_branch")) + '"' : "") + '>' + (current ? "★ " : "") + esc(r.name) + "</span>";
  }).join("");
}
function detailChips(h) {
  const n = DATA.nodes[h];
  if (!n) return "";
  return n.refs.map(r => {
    const current = r.kind === "current" || (r.kind === "branch" && r.name === currentBranch);
    return '<span class="chip" style="background:' + COLORS[r.kind] + '"' +
      (current ? ' title="' + esc(t("current_branch")) + '"' : "") + '>' + (current ? "★ " : "") + esc(r.name) + "</span>";
  }).join("");
}

function fallbackCopy(text) {
  const ta = document.createElement("textarea");
  ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
  document.body.appendChild(ta); ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (e) {}
  document.body.removeChild(ta);
  return ok;
}
function updatePushActions() {
  document.getElementById("pushBtn").disabled = !currentBranchLoaded || !DATA.writable;
}
function updateCurrentBranch() {
  return fetch("/api/head").then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }))
    .then(head => {
      currentBranch = head.branch; currentBranchLoaded = true;
      Object.keys(DATA.nodes).forEach(h => {
        document.querySelectorAll('tr[data-h="' + h + '"] .rc').forEach(el => { el.innerHTML = chips(h); });
      });
      const c = detailView.querySelector("#dChips");
      if (c && curDetailHash) c.innerHTML = detailChips(curDetailHash);
      updatePushActions();
    });
}
function copyText(text) {  // localhost / https なら Clipboard API、それ以外 (LAN の http など) は execCommand で代替
  if (navigator.clipboard && window.isSecureContext)
    return navigator.clipboard.writeText(text).then(() => true, () => fallbackCopy(text));
  return Promise.resolve(fallbackCopy(text));
}
// .copy リンクは data-copy の値をコピーする。行やリンク本来のクリック処理には渡さない
document.addEventListener("click", ev => {
  const a = ev.target.closest(".copy");
  if (!a) return;
  ev.preventDefault(); ev.stopPropagation();
  const label = a.dataset.label || (a.dataset.label = a.textContent);
  copyText(a.dataset.copy).then(ok => {
    const cls = ok ? "ok" : "ng";
    a.textContent = ok ? "✅" : "⚠️"; a.classList.add(cls);
    setTimeout(() => { a.textContent = label; a.classList.remove(cls); }, 1500);
  });
}, true);

function copyLink(h) {  // フルハッシュをコピーするリンク
  return '<a class="copy" data-copy="' + h + '" title="' + esc(t("copy_hash_title")) + '">📋</a>';
}
function api(path, params) {
  return fetch(path + "?" + new URLSearchParams(params)).then(r =>
    r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }));
}
function showView(name, keepScroll) {  // keepScroll: 戻ってきたときに以前のスクロール位置を復元
  savedScroll[curView] = views[curView].scrollTop;
  for (const k in views) views[k].style.display = (k === name) ? "block" : "none";
  curView = name;
  views[name].scrollTop = keepScroll ? savedScroll[name] : 0;
}
function pushScreen(state) {
  const uiState = {compareA: compareAHash, compareB: compareBHash, actionHash: actionHash, cmpActOpen: cmpActOpen};
  history.replaceState(Object.assign({}, history.state, uiState, {scroll: views[curView].scrollTop}), "");
  history.pushState(Object.assign({}, state, uiState), "");
}
function saveUiState() {
  if (!history.state) return;
  history.replaceState(Object.assign({}, history.state, {
    compareA: compareAHash, compareB: compareBHash, actionHash: actionHash, cmpActOpen: cmpActOpen
  }), "");
}
function rowHtml(c, extraClass) {  // c = [hash, short, author, date, subject]
  return '<tr class="clickable' + (extraClass ? ' ' + extraClass : '') + '" data-h="' + c[0] + '"><td class="h">' + esc(c[1]) + copyLink(c[0]) +
    '<div class="cmp-pick"><button type="button" class="sbtn" data-cmp="A" title="' + esc(t("set_compare", {slot: "A"})) + '">A</button>' +
    '<button type="button" class="sbtn" data-cmp="B" title="' + esc(t("set_compare", {slot: "B"})) + '">B</button></div></td><td>' +
    '<span class="rc">' + chips(c[0]) + "</span>" + esc(c[4]) + '<div class="meta">' + esc(c[2]) + " / " + esc(c[3]) + "</div></td></tr>";
}

// git log を CHUNK 件ずつ取得して、末尾が見えたら続きを読み込む (全件読むまで続く)
// src を渡すと /api/log の代わりに src.path (src.params を付けて) から同じ形式の一覧を取る (stash 一覧 / reflog)
function showLog(titleHtml, rev, topHtml, fromHistory, restoreScroll, src, targetHash) {
  document.body.classList.remove("compare-logs-mode");
  if (!fromHistory) pushScreen({screen: "log", titleHtml: titleHtml, rev: rev, topHtml: topHtml, src: src || null, targetHash: targetHash || ""});
  setDocumentTitleFromHtml(titleHtml);
  const my = ++token;
  if (observer) { observer.disconnect(); observer = null; }
  curRow = null;
  listView.innerHTML = "<h2>" + titleHtml + "</h2>" + topHtml +
    '<div class="meta" id="count">' + esc(t("loading")) + '</div>' +
    '<table class="log"><tbody id="rows"></tbody></table><div id="sentinel" class="hint"></div>';
  showView("list");
  listView.dataset.src = src ? src.path : "";
  const rows = listView.querySelector("#rows"), count = listView.querySelector("#count"),
        sentinel = listView.querySelector("#sentinel");
  let skip = 0, done = false, loading = false, total = null;

  async function more() {
    if (loading || done || my !== token) return;
    loading = true;
    try {
      const r = src ? await api(src.path, Object.assign({}, src.params, {skip: skip, limit: CHUNK}))
                    : await api("/api/log", {rev: rev, skip: skip, limit: CHUNK});
      if (my !== token) return;
      rows.insertAdjacentHTML("beforeend", r.commits.map(rowHtml).join(""));
      updateCmpClasses();
      skip += r.commits.length;
      if (fromHistory && restoreScroll) {
        listView.scrollTop = Math.max(0, Math.min(restoreScroll, listView.scrollHeight - listView.clientHeight));
      }
      done = !r.has_more;
      if (r.total !== undefined) total = r.total;
      count.textContent = total === null ? t("n_items", {n: skip})
        : (done ? t("n_changes", {n: total}) : t("n_loaded", {skip: skip, total: total}));
      sentinel.textContent = done ? (skip === 0 ? t("none") : "") : t("loading");
    } catch (e) {
      sentinel.textContent = t("error") + e.message; done = true;
    } finally {
      loading = false;
    }
  }

  if (targetHash) {
    (async () => {
      while (!done && my === token) {
        await more();
        if (my !== token) return;
        const target = listView.querySelector('tr[data-h="' + targetHash + '"]');
        if (target) {
          target.classList.add("cur");
          target.scrollIntoView({block: "center"});
          break;
        }
      }
    })();
  } else if ("IntersectionObserver" in window) {
    const recheck = () => {
      if (!done && observer && my === token) { observer.unobserve(sentinel); observer.observe(sentinel); }
    };
    observer = new IntersectionObserver(es => { if (es[0].isIntersecting) more().then(recheck); },
                                        {root: listView, rootMargin: "600px"});
    observer.observe(sentinel);
  } else {
    (async () => { while (!done && my === token) await more(); })();
  }

}

async function showContainingBranchLog(hash, selectedRef, replaceHistory = false) {
  try {
    const result = await api("/api/branches-containing", {hash: hash});
    if (!result.branches.length) {
      setGraphStatus(t("branches_containing_none", {hash: hash.slice(0, 7)}), "error");
      return;
    }
    const branches = result.branches;
    const preferred = "refs/heads/" + currentBranch;
    const branch = branches.find(b => b.ref === selectedRef) ||
      branches.find(b => b.ref === preferred) || branches[0];
    const options = branches.map(b => '<option value="' + esc(b.ref) + '"' +
      (b.ref === branch.ref ? " selected" : "") + ">" + esc(b.name) + "</option>").join("");
    const top = '<button class="back-btn">' + t("back_history") + '</button><div class="meta"><label>' + esc(t("target_label")) +
      ': <select id="containingBranchSelect" data-h="' + esc(hash) + '">' + options + "</select></label></div>";
    showLog(esc(t("branch_log_title", {name: branch.name})), branch.tip, top, replaceHistory, 0, null, hash);
  } catch (e) {
    setGraphStatus(t("error") + e.message, "error");
  }
}

function showCompareLogs(a, b, fromHistory, restoreScroll) {
  document.body.classList.add("compare-logs-mode");
  if (!fromHistory) pushScreen({screen: "compare-logs", a: a, b: b});
  setDocumentTitle(t("compare_logs_heading", {from: a.slice(0, 7), to: b.slice(0, 7)}));
  const my = ++token;
  if (observer) { observer.disconnect(); observer = null; }
  curRow = null;
  listView.innerHTML = '<h2>' + esc(t("compare_logs_heading", {from: a.slice(0, 7), to: b.slice(0, 7)})) + '</h2>' +
    '<div class="compare-logs"><section class="compare-log-col"><h3>A: ' + esc(a.slice(0, 7)) + '</h3>' +
    '<table class="log"><tbody id="compareRowsA"></tbody></table></section>' +
    '<section class="compare-log-col"><h3>B: ' + esc(b.slice(0, 7)) + '</h3>' +
    '<table class="log"><tbody id="compareRowsB"></tbody></table></section></div>' +
    '<div id="compareLogSentinel" class="hint">' + esc(t("loading")) + '</div>';
  showView("list");
  listView.dataset.src = "";
  const rowsA = listView.querySelector("#compareRowsA"), rowsB = listView.querySelector("#compareRowsB"),
        sentinel = listView.querySelector("#compareLogSentinel");
  const gapRow = '<tr class="log-gap"><td colspan="2"></td></tr>';
  let skipA = 0, skipB = 0, doneA = false, doneB = false;
  let initial = true, loading = false, base = "";

  function hasCommit(commits, hash) {
    return commits.some(c => c[0] === hash);
  }

  function alignedRows(commitsA, commitsB) {
    const ia = base ? commitsA.findIndex(c => c[0] === base) : -1;
    const ib = base ? commitsB.findIndex(c => c[0] === base) : -1;
    if (ia < 0 || ib < 0) {
      return [commitsA.map(rowHtml).join(""), commitsB.map(rowHtml).join("")];
    }
    const target = Math.max(ia, ib);
    const side = (commits, index) => {
      const html = [];
      for (let i = index; i < target; i++) html.push(gapRow);
      html.push(...commits.slice(0, index).map(rowHtml));
      html.push(...commits.slice(index).map((c, i) => rowHtml(c, i === 0 ? "log-common log-common-start" : "log-common")));
      return html.join("");
    };
    return [side(commitsA, ia), side(commitsB, ib)];
  }

  function syncCompareRows() {
    const listA = Array.from(rowsA.querySelectorAll("tr"));
    const listB = Array.from(rowsB.querySelectorAll("tr"));
    const commonA = listA.findIndex(row => row.classList.contains("log-common"));
    const commonB = listB.findIndex(row => row.classList.contains("log-common"));
    if (commonA < 0 || commonA !== commonB) return;
    for (let i = 0; i <= commonA; i++) {
      listA[i].style.height = "";
      listB[i].style.height = "";
      const height = Math.max(listA[i].getBoundingClientRect().height, listB[i].getBoundingClientRect().height);
      listA[i].style.height = height + "px";
      listB[i].style.height = height + "px";
    }
  }

  async function more() {
    if (loading || (doneA && doneB) || my !== token) return;
    loading = true;
    try {
      if (initial) {
        const [mb, firstA, firstB] = await Promise.all([
          api("/api/merge-base", {from: a, to: b}),
          api("/api/log", {rev: a, skip: skipA, limit: CHUNK}),
          api("/api/log", {rev: b, skip: skipB, limit: CHUNK})
        ]);
        if (my !== token) return;
        base = mb.hash || "";
        const commitsA = firstA.commits.slice(), commitsB = firstB.commits.slice();
        skipA += firstA.commits.length; skipB += firstB.commits.length;
        doneA = !firstA.has_more; doneB = !firstB.has_more;
        if (base) {
          while ((!hasCommit(commitsA, base) && !doneA) || (!hasCommit(commitsB, base) && !doneB)) {
            const needA = !hasCommit(commitsA, base) && !doneA;
            const needB = !hasCommit(commitsB, base) && !doneB;
            const [ra, rb] = await Promise.all([
              needA ? api("/api/log", {rev: a, skip: skipA, limit: CHUNK}) : Promise.resolve({commits: [], has_more: false}),
              needB ? api("/api/log", {rev: b, skip: skipB, limit: CHUNK}) : Promise.resolve({commits: [], has_more: false})
            ]);
            if (my !== token) return;
            commitsA.push(...ra.commits); commitsB.push(...rb.commits);
            skipA += ra.commits.length; skipB += rb.commits.length;
            if (needA) doneA = !ra.has_more;
            if (needB) doneB = !rb.has_more;
          }
        }
        const aligned = alignedRows(commitsA, commitsB);
        rowsA.innerHTML = aligned[0]; rowsB.innerHTML = aligned[1];
        initial = false;
        requestAnimationFrame(syncCompareRows);
      } else {
        const [ra, rb] = await Promise.all([
          doneA ? Promise.resolve({commits: [], has_more: false}) : api("/api/log", {rev: a, skip: skipA, limit: CHUNK}),
          doneB ? Promise.resolve({commits: [], has_more: false}) : api("/api/log", {rev: b, skip: skipB, limit: CHUNK})
        ]);
        if (my !== token) return;
        rowsA.insertAdjacentHTML("beforeend", ra.commits.map(rowHtml).join(""));
        rowsB.insertAdjacentHTML("beforeend", rb.commits.map(rowHtml).join(""));
        skipA += ra.commits.length; skipB += rb.commits.length;
        doneA = doneA || !ra.has_more; doneB = doneB || !rb.has_more;
      }
      updateCmpClasses();
      const done = doneA && doneB;
      sentinel.textContent = done ? "" : t("loading");
      if (fromHistory && restoreScroll) {
        listView.scrollTop = Math.max(0, Math.min(restoreScroll, listView.scrollHeight - listView.clientHeight));
      }
    } catch (e) {
      sentinel.textContent = t("error") + e.message;
      doneA = true; doneB = true;
    } finally {
      loading = false;
    }
  }
  if ("IntersectionObserver" in window) {
    const recheck = () => {
      if (!(doneA && doneB) && observer && my === token) { observer.unobserve(sentinel); observer.observe(sentinel); }
    };
    observer = new IntersectionObserver(es => { if (es[0].isIntersecting) more().then(recheck); },
                                        {root: listView, rootMargin: "600px"});
    observer.observe(sentinel);
  } else {
    (async () => { while (!(doneA && doneB) && my === token) await more(); })();
  }
}

// 差分は別ページ (/diff) で開く。ctx は {hash} (1コミット) または {from, to} (2コミット間の比較)
function diffUrl(ctx, f) {
  const p = Object.assign({}, ctx, {path: f.path});
  if (f.old_path) p.old = f.old_path;
  if (f.wip) p.wip = f.wip;  // 作業ツリーの変更 (staged / unstaged / untracked / conflict)
  return "/diff?" + new URLSearchParams(p);
}

const ST_CLASS = {M: "stM", T: "stM", A: "stA", D: "stD", R: "stR", C: "stR", U: "stU", "?": "stN"};
function badge(f) {  // 変更の種類 (M/A/D/R...) のバッジ。WIP の一覧で使う
  return f.st ? '<span class="st ' + (ST_CLASS[f.st] || "stM") + '">' + esc(f.st) + "</span>" : "";
}
const WIP_DIR_COLLAPSE = 10;  // 1つのディレクトリの変更がこの件数以上なら、既定で畳む
const WIP_OPEN_KEY = "git-graph-wip-open-dirs";  // /diff ページと共有する
let wipOpenDirs = new Set();  // 開いているディレクトリ ("種別|パス")
try { wipOpenDirs = new Set(JSON.parse(sessionStorage.getItem(WIP_OPEN_KEY) || "[]")); } catch (e) {}
function saveWipOpenDirs() { try { sessionStorage.setItem(WIP_OPEN_KEY, JSON.stringify([...wipOpenDirs])); } catch (e) {} }
function dirOf(path) { const i = path.lastIndexOf("/"); return i < 0 ? "" : path.slice(0, i); }
function filesHtml(ctx, files, offset, withCheck, collapse) {
  offset = offset || 0;
  if (!files.length) return '<p class="hint">' + esc(t("no_changed_files")) + '</p>';
  let add = 0, del = 0, max = 0;
  files.forEach(f => { add += f.added || 0; del += f.deleted || 0; max = Math.max(max, (f.added || 0) + (f.deleted || 0)); });
  const scale = max > 40 ? 40 / max : 1;
  const dirCount = {}, dirStat = {}, dirSeen = {};
  if (collapse) files.forEach(f => {
    const dir = dirOf(f.path);
    dirCount[dir] = (dirCount[dir] || 0) + 1;
    dirStat[dir] = dirStat[dir] || [0, 0];
    dirStat[dir][0] += f.added || 0; dirStat[dir][1] += f.deleted || 0;
  });
  const rows = files.map((f, k) => {
    const a = f.added || 0, d = f.deleted || 0;
    const dir = dirOf(f.path), folded = collapse && dirCount[dir] >= WIP_DIR_COLLAPSE;
    const open = folded && wipOpenDirs.has(f.wip + "|" + dir);
    let head = "";
    if (folded && !dirSeen[dir]) {
      dirSeen[dir] = true;
      head = '<tr class="drow" data-dir="' + esc(dir) + '" data-kind="' + esc(f.wip) + '" title="' + esc(t("dir_toggle")) + '">' +
        (withCheck ? '<td class="fk"><input type="checkbox" class="wipdir" data-kind="' + esc(f.wip) + '" data-dir="' + esc(dir) + '"></td>' : "") +
        '<td class="fp" colspan="3"><span class="dtog">' + (open ? "▼" : "▶") + '</span> ' + esc(dir ? dir + "/" : t("dir_root")) +
        ' <span class="hint">(' + esc(t("n_items", {n: dirCount[dir]})) + ') <span class="plus">+' + dirStat[dir][0] +
        '</span> <span class="minus">-' + dirStat[dir][1] + '</span></span></td></tr>';
    }
    const pa = a ? Math.max(1, Math.round(a * scale)) : 0, pd = d ? Math.max(1, Math.round(d * scale)) : 0;
    const name = f.old_path ? f.old_path + " => " + f.path : f.path;
    return head + '<tr class="frow' + (folded && !open ? " dhide" : "") + '" data-k="' + (offset + k) + '" data-dir="' + esc(dir) + '">' + (withCheck ? wipCheckCell(f) : "") + '<td class="fp">' + badge(f) + (f.tag ? '<span class="hint">[' + esc(f.tag) + '] </span>' : "") + '<a href="' + esc(diffUrl(ctx, f)) +
      '">' + esc(name) + '</a></td><td class="fc">' +
      (f.label ? esc(t(f.label)) : f.added === null ? "Bin" : a + d) + '</td><td class="fb"><span class="plus">' + "+".repeat(pa) +
      '</span><span class="minus">' + "-".repeat(pd) + "</span></td></tr>";
  }).join("");
  return '<div class="meta">' + files.length + (files.some(f => f.added !== undefined)
      ? " files changed, " + add + " insertions(+), " + del + " deletions(-)" : " files") + "</div>" +
    '<table class="files">' + rows + "</table>";
}

let curDetailHash = null;

function localBranches(h) {
  const n = DATA.nodes[h];
  return n ? n.refs.filter(r => r.kind === "branch" || r.kind === "current").map(r => r.name) : [];
}
function remoteBranches(h) {
  const n = DATA.nodes[h];
  return n ? n.refs.filter(r => r.kind === "remote").map(r => r.name) : [];
}
function defaultNewBranchName(h) {
  const remote = remoteBranches(h).sort()[0] || "";
  const slash = remote.indexOf("/");
  return slash >= 0 ? remote.slice(slash + 1) : remote;
}
function localTags(h) {
  const n = DATA.nodes[h];
  return n ? n.refs.filter(r => r.kind === "tag").map(r => r.name) : [];
}
function showRefList(kind) {  // kind: "branches" | "tags"
  showLog(t(kind === "tags" ? "tag_list_title" : "branch_list_title"), "",
          '<div class="meta">' + esc(t(kind === "tags" ? "tag_list_help" : "branch_list_help")) + '</div>',
          false, 0, {path: "/api/" + kind, params: {}});
}
function showStashList(fromHistory) {
  loadStashes();
  showLog(t("stash_list_title"), "", '<div class="meta">' + esc(t("stash_list_help")) + '</div>',
          !!fromHistory, 0, {path: "/api/stash-list", params: {}});
}
function doStash(op) {
  if (!actionHash) return;
  const h = actionHash, sel = stashByHash[h];
  if (!sel) return;
  const label = {apply: t("op_apply"), pop: t("op_pop"), drop: t("op_drop")}[op];
  const msg = {
    apply: t("stash_confirm_apply", {sel: sel}),
    pop: t("stash_confirm_pop", {sel: sel}),
    drop: t("stash_confirm_drop", {sel: sel, hash: h.slice(0, 7)}),
  }[op];
  if (!confirm(msg)) return;
  document.querySelectorAll("#stashActions button").forEach(b => { b.disabled = true; });
  setGraphStatus(t("stash_busy"), "progress");
  const reload = () => Promise.all([loadStashes(), refreshGraph().catch(() => {})]).then(() => {
    if (curView === "list" && listView.dataset.src === "/api/stash-list") showStashList(true);
  });
  post("/api/stash", {hash: h, op: op})
    .then(r => { clearCmp(); setGraphStatus(r.message, "success"); return reload(); })
    .catch(e => { setGraphStatus(t("stash_op_failed", {op: label, error: e.message}), "error"); return reload(); })
    .finally(() => updateCmpClasses());
}
function showReflog(branch) {  // branch が空なら HEAD の reflog
  const names = [];
  Object.values(DATA.nodes).forEach(n => n.refs.forEach(r => {
    if ((r.kind === "branch" || r.kind === "current") && !names.includes(r.name)) names.push(r.name);
  }));
  names.sort();
  if (branch && !names.includes(branch)) names.push(branch);
  const opts = ['<option value=""' + (branch ? "" : " selected") + ">HEAD</option>"].concat(names.map(n =>
    '<option value="' + esc(n) + '"' + (n === branch ? " selected" : "") + ">" + esc(n) + "</option>")).join("");
  const top = '<div class="meta"><label>' + esc(t("target_label")) + ': <select class="reflog-ref">' + opts + "</select></label></div>" +
    '<div class="meta">' + esc(t("reflog_help")) + '</div>';
  showLog("reflog " + esc(branch || "HEAD"), "", top, false, 0,
          {path: "/api/reflog", params: branch ? {branch: branch} : {}});
}
listView.addEventListener("change", ev => {
  const sel = ev.target.closest(".reflog-ref");
  if (sel) showReflog(sel.value);
  const branchSel = ev.target.closest("#containingBranchSelect");
  if (branchSel) showContainingBranchLog(branchSel.dataset.h, branchSel.value);
});
function showDetail(h, fromHistory) {
  document.body.classList.remove("compare-logs-mode");
  if (!fromHistory) pushScreen({screen: "detail", hash: h});
  setDocumentTitle(t("commit_label") + " " + h.slice(0, 7));
  const back = '<button class="back-btn">' + t("back_history") + '</button>';
  detailView.innerHTML = back + '<p class="hint">' + esc(t("loading")) + '</p>';
  showView("detail");
  api("/api/commit", {hash: h}).then(c => {
    curCtx = {hash: c.hash}; curFiles = c.files; curDetailHash = c.hash;
    const parents = c.parents.map(p => esc(p.slice(0, 7))).join(", ") || t("none");
    detailView.innerHTML = back + "<h2>" + t("commit_label") + " " + esc(c.hash.slice(0, 7)) +
      ' <a class="detail-files" href="/files?hash=' + encodeURIComponent(c.hash) + '" title="' + esc(t("tip_files")) + '">' + esc(t("files")) + "</a></h2>" +
      '<span id="dChips">' + detailChips(c.hash) + "</span>" +
      '<div class="meta">' + esc(c.hash) + copyLink(c.hash) + "</div>" +
      '<div class="meta">Author: ' + esc(c.author) + "</div>" +
      '<div class="meta">Date: ' + esc(c.date) + "</div>" +
      '<div class="meta">Parent: ' + parents + "</div>" +
      "<pre>" + esc(c.message) + "</pre>" + filesHtml(curCtx, c.files) +
      '<div class="meta"><code>git show ' + esc(c.hash.slice(0, 7)) + "</code></div>";
  }).catch(e => {
    detailView.innerHTML = back + '<p class="hint">' + esc(t("error")) + esc(e.message) + "</p>";
  });
}

function post(path, body) {
  return fetch(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)})
    .then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }));
}
function doAction(btn) {  // ブランチ/タグのチップの × (削除)
  if (btn.dataset.act !== "delete-ref") return;
  const kind = btn.dataset.kind, name = btn.dataset.name;
  const url = "/api/ref-delete", body = {kind: kind, name: name};
  if (!confirm(t(kind === "branch" ? "confirm_delete_branch" : "confirm_delete_tag", {name: name}))) return;
  btn.disabled = true; setGraphStatus(t("deleting"), "progress");
  post(url, body)
    .then(r => {
      if (!r.needs_force) return r;
      if (!confirm(t("confirm_force_delete", {name: name}))) return null;
      return post(url, Object.assign({}, body, {force: true}));
    })
    .then(r => {
      if (r === null) { setGraphStatus(t("force_delete_cancelled")); return; }
      setGraphStatus(r.message, "success");
      return refreshGraph().catch(() => {});
    })
    .catch(e => setGraphStatus(t("delete_failed", {error: e.message}), "error", true))
    .finally(() => { btn.disabled = false; });
}

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
let cmpActOpen = false;
let abActOpen = false;
function restoreMenuPosition(menu) {
  menu.classList.remove("near-pointer");
  menu.style.removeProperty("--menu-left");
  menu.style.removeProperty("--menu-top");
}
function positionMenuNearPointer(menu, ev) {
  if (!ev || (ev.detail === 0 && ev.type !== "contextmenu")) return;
  const gap = 12, edge = 8;
  menu.classList.add("near-pointer");
  menu.style.setProperty("--menu-left", (ev.clientX + gap) + "px");
  menu.style.setProperty("--menu-top", (ev.clientY + gap) + "px");
  const rect = menu.getBoundingClientRect();
  let left = ev.clientX + gap, top = ev.clientY + gap;
  if (left + rect.width > window.innerWidth - edge) left = ev.clientX - rect.width - gap;
  if (top + rect.height > window.innerHeight - edge) top = ev.clientY - rect.height - gap;
  left = Math.max(edge, Math.min(left, window.innerWidth - rect.width - edge));
  top = Math.max(edge, Math.min(top, window.innerHeight - rect.height - edge));
  menu.style.setProperty("--menu-left", left + "px");
  menu.style.setProperty("--menu-top", top + "px");
}
function setOtherMenu(open) {  // 「その他」のメニュー
  const btn = document.getElementById("otherMenuBtn");
  document.getElementById("otherActions").hidden = !open;
  btn.setAttribute("aria-expanded", open ? "true" : "false");
  btn.textContent = open ? t("other").replace("▾", "▴") : t("other");
}
function closeMenus() {  // 開いているメニュー (操作 / AB操作 / その他) をすべて閉じる
  const changed = cmpActOpen || abActOpen || !document.getElementById("otherActions").hidden;
  cmpActOpen = false;
  abActOpen = false;
  setOtherMenu(false);
  if (changed) updateCmpClasses();
}
let stashByHash = {};  // stash のコミットのハッシュ -> stash@{n}
function loadStashes() {
  return fetch("/api/stash-list").then(r => r.ok ? r.json() : null).then(r => {
    if (!r) return;
    stashByHash = {};
    r.commits.forEach(c => { stashByHash[c[0]] = c[2]; });
    updateCmpClasses();
  }).catch(() => {});
}
function askCheckoutConflict(r) {  // チェックアウトを止めているファイルを見せて、stash / 削除 / キャンセルを選んでもらう
  return new Promise(resolve => {
    const m = document.getElementById("coModal");
    const list = fs => "<ul>" + fs.map(f => "<li>" + esc(f) + "</li>").join("") + "</ul>";
    let html = "";
    if (r.modified.length) html += "<h3>" + esc(t("co_modified")) + "</h3>" + list(r.modified);
    if (r.untracked.length) html += "<h3>" + esc(t("co_untracked")) + "</h3>" + list(r.untracked);
    document.getElementById("coFiles").innerHTML = html;
    const done = v => { m.hidden = true; m.onclick = null; resolve(v); };
    m.hidden = false;
    m.onclick = ev => {
      const b = ev.target.closest("[data-co]");
      if (b) done(b.dataset.co === "cancel" ? null : b.dataset.co);
      else if (ev.target === m) done(null);
    };
  });
}
function openActionDialog(title, fields, submitLabel, opts) {  // opts: {wide, validate(values) -> エラー文 or null}
  opts = opts || {};
  return new Promise(resolve => {
    const modal = document.getElementById("actionModal"), form = document.getElementById("actionForm");
    form.className = "action-dialog" + (opts.wide ? " wide" : "");
    document.getElementById("actionError").textContent = "";
    let cleanup = null;
    const finish = value => {
      if (cleanup) cleanup();
      modal.hidden = true;
      form.onsubmit = null;
      modal.onclick = null;
      window.removeEventListener("keydown", onKeyDown);
      resolve(value);
    };
    const onKeyDown = ev => { if (ev.key === "Escape") finish(null); };
    document.getElementById("actionTitle").textContent = title;
    document.getElementById("actionFields").innerHTML = fields;
    document.getElementById("actionSubmit").textContent = submitLabel;
    modal.hidden = false;
    if (opts.onOpen) cleanup = opts.onOpen(form) || null;
    form.onsubmit = ev => {
      ev.preventDefault();
      const values = Object.fromEntries(new FormData(form).entries());
      form.querySelectorAll('input[type="checkbox"][name]').forEach(input => { values[input.name] = input.checked; });
      if (opts.validate) {
        const err = opts.validate(values);
        document.getElementById("actionError").textContent = err || "";
        if (err) return;
      }
      finish(values);
    };
    modal.onclick = ev => {
      if (ev.target === modal || ev.target.closest("[data-dialog-cancel]")) finish(null);
    };
    window.addEventListener("keydown", onKeyDown);
    const first = form.querySelector("input, select");
    if (first) first.focus();
  });
}
function setCmpMsg(text, state) {  // state: true = 成功 / false = 失敗 / "progress" = 実行中
  setGraphStatus(text, state === "progress" ? "progress" : state ? "success" : "error", state === false);
}
function setRemoteMsg(text, state) {
  setCmpMsg(text, state);
}
async function doCmpAction(btn) {  // 選択対象に対するブランチ作成 / タグ作成 / チェックアウト
  if (!actionHash) return;
  const h = actionHash, act = btn.dataset.cact;
  let values, url, body, ask = null;
  if (act === "branch") {
    values = await openActionDialog(t("create_branch"), '<label>' + esc(t("branch_name")) + '<input type="text" name="name" required autofocus></label>' +
      '<label class="check"><input type="checkbox" name="checkout"> ' + esc(t("checkout_after_create")) + '</label>', t("create"));
    if (!values) return;
    url = "/api/branch"; body = {hash: h, name: values.name.trim(), checkout: values.checkout};
    if (!body.name) { setCmpMsg(t("branch_name_required"), false); return; }
    if (body.checkout) ask = t("confirm_create_checkout", {name: body.name});
  } else if (act === "tag") {
    values = await openActionDialog(t("create_tag"), '<label>' + esc(t("tag_name")) + '<input type="text" name="name" required autofocus></label>' +
      '<label>' + esc(t("message_optional")) + '<input type="text" name="message"></label>', t("create"));
    if (!values) return;
    url = "/api/tag"; body = {hash: h, name: values.name.trim(), message: values.message.trim()};
    if (!body.name) { setCmpMsg(t("tag_name_required"), false); return; }
  } else if (act === "checkout") {
    const branches = localBranches(h);
    const remoteRefs = remoteBranches(h).sort();
    const defaultRemoteRef = remoteRefs[0] || "";
    const defaultNewBranch = defaultNewBranchName(h);
    values = await openActionDialog(t("checkout"), '<label>' + esc(t("switch_to")) + '<select name="branch">' +
      branches.map(branch => '<option value="' + esc(branch) + '">' + esc(branch) + '</option>').join("") +
      '<option value="">' + esc(t("checkout_detached_option")) + '</option>' +
      '<option value="__new_branch__">' + esc(t("checkout_new_branch_option")) + '</option>' +
      '</select></label>' +
      '<label id="newBranchField" hidden>' + esc(t("new_branch_name")) + '<input type="text" name="newBranch" value="' + esc(defaultNewBranch) + '" placeholder="' + esc(t("new_branch_placeholder")) + '"></label>' +
      '<label id="trackUpstreamField" class="check" hidden><input type="checkbox" name="trackUpstream"' + (defaultRemoteRef ? ' checked' : '') + '> ' + esc(t("track_upstream")) + (defaultRemoteRef ? ' (' + esc(defaultRemoteRef) + ')' : '') + '</label>', t("checkout"), {
        onOpen: form => {
          const select = form.elements.branch, field = form.querySelector("#newBranchField"), track = form.querySelector("#trackUpstreamField");
          const update = () => { field.hidden = select.value !== "__new_branch__"; track.hidden = select.value !== "__new_branch__" || !defaultRemoteRef; };
          select.addEventListener("change", update);
          update();
          return () => select.removeEventListener("change", update);
        },
        validate: values => values.branch === "__new_branch__" && !values.newBranch.trim() ?
          t("new_branch_name_required") : null
      });
    if (!values) return;
    if (values.branch === "__new_branch__") {
      const name = values.newBranch.trim();
      url = "/api/branch"; body = {hash: h, name: name, checkout: true};
      if (values.trackUpstream && defaultRemoteRef) body.upstream = defaultRemoteRef;
      ask = t("confirm_create_checkout", {name: name});
    } else if (values.branch) {
      url = "/api/checkout"; body = {branch: values.branch};
      ask = t("confirm_checkout_branch", {branch: values.branch});
    } else {
      url = "/api/checkout"; body = {hash: h};
      ask = t("confirm_checkout_detached", {hash: h.slice(0, 7)});
    }
  } else return;
  if (ask && !confirm(ask)) return;
  btn.disabled = true; setCmpMsg(t("running"), "progress");
  const run = extra => post(url, Object.assign({}, body, extra || {}));
  run()
    .then(r => {
      if (!r.blocked) return r;
      setCmpMsg("", true);
      return askCheckoutConflict(r).then(mode => {
        if (!mode) return {cancelled: true, message: t("checkout_cancelled")};
        setCmpMsg(t("running"), "progress");
        return run({on_conflict: mode});
      });
    })
    .then(r => {
      if (r.cancelled) { setCmpMsg(r.message, false); return; }
      setCmpMsg(r.message, true);
      return refreshGraph().catch(() => {});
    })
    .catch(e => setCmpMsg(e.message, false))
    .finally(() => { btn.disabled = false; });
}
async function doDeleteBranch(btn) {
  const branches = localBranches(actionHash).filter(name => name !== currentBranch).sort();
  if (!branches.length) return;
  const values = await openActionDialog(t("delete_branch"),
    '<label>' + esc(t("branch_to_delete")) + '<select name="name">' + branches.map(branch =>
      '<option value="' + esc(branch) + '">' + esc(branch) + "</option>").join("") +
    "</select></label>", t("delete"));
  if (!values) return;
  const name = values.name;
  if (!confirm(t("confirm_delete_branch", {name: name}))) return;
  const body = {kind: "branch", name: name};
  btn.disabled = true; setCmpMsg(t("deleting"), "progress");
  post("/api/ref-delete", body)
    .then(r => {
      if (!r.needs_force) return r;
      if (!confirm(t("confirm_force_delete", {name: name}))) return null;
      return post("/api/ref-delete", Object.assign({}, body, {force: true}));
    })
    .then(r => {
      if (r === null) { setCmpMsg(t("force_delete_cancelled"), false); return; }
      setCmpMsg(r.message, true);
      return refreshGraph().catch(() => {});
    })
    .catch(e => setCmpMsg(t("delete_failed", {error: e.message}), false))
    .finally(() => { btn.disabled = false; });
}
async function doDeleteTag(btn) {
  const tags = localTags(actionHash).sort();
  if (!tags.length) return;
  const values = await openActionDialog(t("delete_tag"),
    '<label>' + esc(t("tag_to_delete")) + '<select name="name">' + tags.map(tag =>
      '<option value="' + esc(tag) + '">' + esc(tag) + "</option>").join("") +
    "</select></label>", t("delete"));
  if (!values) return;
  const name = values.name;
  if (!confirm(t("confirm_delete_tag", {name: name}))) return;
  btn.disabled = true; setCmpMsg(t("deleting"), "progress");
  post("/api/ref-delete", {kind: "tag", name: name})
    .then(r => { setCmpMsg(r.message, true); return refreshGraph().catch(() => {}); })
    .catch(e => setCmpMsg(t("delete_failed", {error: e.message}), false))
    .finally(() => { btn.disabled = false; });
}
async function doDeleteRemoteBranch(btn) {
  const branches = remoteBranches(actionHash).sort();
  if (!branches.length) return;
  const values = await openActionDialog(t("delete_remote_branch"),
    '<label>' + esc(t("remote_branch_to_delete")) + '<select name="name">' + branches.map(branch =>
      '<option value="' + esc(branch) + '">' + esc(branch) + "</option>").join("") +
    "</select></label>", t("delete"));
  if (!values) return;
  const name = values.name;
  if (!confirm(t("confirm_delete_remote_branch", {name: name}))) return;
  const body = {kind: "remote-branch", name: name};
  btn.disabled = true; setCmpMsg(t("deleting"), "progress");
  const run = extra => post("/api/ref-delete", Object.assign({}, body, extra || {}));
  try {
    let r = await run();
    if (r.auth_required) {
      const auth = await openActionDialog(t("http_auth"),
        '<label>' + esc(t("auth_username")) + '<input name="username" autocomplete="username" autofocus></label>' +
        '<label>' + esc(t("auth_password")) + '<input type="password" name="password" autocomplete="current-password"></label>',
        t("authenticate"));
      if (!auth) { setCmpMsg(t("push_cancelled"), false); return; }
      r = await run({auth: auth, auth_remote: r.auth_remote, auth_retry: true});
    }
    setCmpMsg(r.message, true);
    await refreshGraph().catch(() => {});
  } catch (e) {
    setCmpMsg(e.message, false);
  } finally {
    btn.disabled = false;
  }
}
async function doRemoteAction(btn) {  // fetch / pull / push
  const act = btn.dataset.cact;
  const url = "/api/" + act;
  let body = {}, ask = null;
  if (act === "fetch") {
    const values = await openActionDialog("fetch", '<label class="check"><input type="checkbox" name="prune"> ' + esc(t("prune_label")) + '</label>', "fetch");
    if (!values) return;
    body = {prune: values.prune};
  } else if (act === "pull") {
    const values = await openActionDialog("pull", '<label>' + esc(t("pull_mode")) + '<select name="mode">' +
      '<option value="ff-only">' + esc(t("pull_opt_ff")) + '</option>' +
      '<option value="merge">' + esc(t("pull_opt_merge")) + '</option>' +
      '<option value="rebase">' + esc(t("pull_opt_rebase")) + '</option></select></label>', "pull");
    if (!values) return;
    const mode = values.mode;
    body = {mode: mode};
    ask = t("confirm_pull", {mode: {"ff-only": t("pull_mode_ff"), "merge": t("pull_mode_merge"), "rebase": t("pull_mode_rebase")}[mode]});
  } else if (act === "push") {
    const branches = [...new Set([currentBranch, ...localBranches(actionHash)])]
      .filter(Boolean).sort((a, b) => a.localeCompare(b));
    if (!branches.length) return;
    // 操作メニューの push は選択中のコミットのブランチ、メニューバーの push は HEAD (現在のブランチ) を既定にする
    const selected = btn.dataset.pushSel ? localBranches(actionHash).sort((a, b) => a.localeCompare(b)) : [];
    const defaultBranch = selected.length ? (selected.includes(currentBranch) ? currentBranch : selected[0]) : (currentBranch || branches[0]);
    const remoteInfo = await api("/api/remotes", {});
    const remotes = remoteInfo.remotes || [];
    if (!remotes.length) { setRemoteMsg(t("no_remotes"), false); return; }
    const defaultRemote = remotes.some(r => r.name === "origin") ? "origin" : remotes[0].name;
    const values = await openActionDialog("push",
      '<label>' + esc(t("push_branch_select")) + '<select name="branch">' + branches.map(branch =>
        '<option value="' + esc(branch) + '"' + (branch === defaultBranch ? " selected" : "") + '>' + esc(branch) + (branch === currentBranch ? " (HEAD)" : "") + "</option>").join("") +
      '</select></label>' +
      '<label>' + esc(t("push_remote_select")) + '<select name="remote">' + remotes.map(r =>
        '<option value="' + esc(r.name) + '"' + (r.name === defaultRemote ? " selected" : "") + '>' + esc(r.name) + "</option>").join("") +
      '</select></label>' +
      '<label>' + esc(t("push_remote_branch")) + '<input name="remoteBranch" value="' + esc(defaultBranch) + '" required></label>' +
      '<label class="check"><input type="checkbox" name="force"> ' + esc(t("force_label")) + '</label>', "push", {
        onOpen: form => {
          const branch = form.elements.branch, remoteBranch = form.elements.remoteBranch;
          const update = () => { remoteBranch.value = branch.value; };
          branch.addEventListener("change", update);
          return () => branch.removeEventListener("change", update);
        }
      });
    if (!values) return;
    body = {branch: values.branch, remote: values.remote, remote_branch: values.remoteBranch.trim(), force: values.force};
    const what = t("what_branch", {name: values.branch}) + " → " + values.remote;
    ask = body.force
      ? t("confirm_push_force", {what: what})
      : t("confirm_push", {what: what});
  } else if (act === "push-tag") {
    body = {tag: btn.dataset.tag};
    ask = t("confirm_push_tag", {tag: body.tag});
  } else return;
  if (ask && !confirm(ask)) return;
  btn.disabled = true; setRemoteMsg(t("running_remote"), "progress");
  let p = post(url, body).then(async r => {
    if (!r.auth_required) return r;
    const auth = await openActionDialog(t("http_auth"),
      '<label>' + esc(t("auth_username")) + '<input name="username" autocomplete="username" autofocus></label>' +
      '<label>' + esc(t("auth_password")) + '<input type="password" name="password" autocomplete="current-password"></label>',
      t("authenticate"));
    if (!auth) return {cancelled: true, message: t("push_cancelled")};
    setRemoteMsg(t("running_remote"), "progress");
    return post(url, Object.assign({}, body, {auth: auth, auth_remote: r.auth_remote, auth_retry: true}));
  });
  if (act === "push-tag") {
    p = p.then(r => {
      if (!r.needs_remote) return r;
      const input = prompt(t("prompt_push_tag_remote", {tag: r.tag, remotes: r.remotes.join(" / ")}), r.remote);
      if (input === null) return {cancelled: true, message: t("push_cancelled")};
      const remote = input.trim();
      if (r.remotes.indexOf(remote) < 0) throw new Error(t("remote_unregistered", {remote: remote}));
      setRemoteMsg(t("running_remote"), "progress");
      return post(url, Object.assign({}, body, {remote: remote}));
    });
  }
  if (act === "push") {
    p = p.then(r => {
      if (!r.needs_upstream) return r;
      const cancelled = {cancelled: true, message: t("push_cancelled")};
      let remote = r.remote;
      if (r.remotes && r.remotes.length > 1) {  // リモートが複数: push 先を選ばせる
        const input = prompt(t("prompt_push_remote", {branch: r.branch, remotes: r.remotes.join(" / ")}), r.remote);
        if (input === null) return cancelled;
        remote = input.trim();
        if (r.remotes.indexOf(remote) < 0) throw new Error(t("remote_unregistered", {remote: remote}));
      } else if (!confirm(t("confirm_set_upstream", {branch: r.branch, remote: r.remote}))) return cancelled;
      setRemoteMsg(t("running_remote"), "progress");
      return post(url, Object.assign({}, body, {set_upstream: true, remote: remote}));
    });
  }
  p.then(r => {
    if (r.cancelled) { setRemoteMsg(r.message, false); return; }
    if (act === "push") {
      // git push 自体の完了を先に通知し、グラフ更新は別の進捗表示で行う。
      setRemoteMsg(r.message, true);
      return refreshGraph().catch(() => {});
    }
    setRemoteMsg(r.message, r.ok !== false && !r.fetch_failed);
    return refreshGraph().catch(() => {}).then(() => {
      if (r.conflict) { clearCmp(); showWip(); }
    });
  })
    .catch(e => setRemoteMsg(e.message, false))
    .finally(() => { btn.disabled = false; });
}
async function doRemoteConfig(btn) {  // remote 追加 / 削除
  let url, body, ask = null;
  if (btn.dataset.cact === "remote-add") {
    const values = await openActionDialog(t("remote_add"),
      '<label>' + esc(t("name")) + '<input type="text" name="name" placeholder="origin" required autofocus></label>' +
      '<label>URL<input type="text" name="url" required></label>' +
      '<label class="check"><input type="checkbox" name="fetch" checked> ' + esc(t("fetch_after_add")) + '</label>', t("add"));
    if (!values) return;
    body = {name: values.name.trim(), url: values.url.trim(), fetch: !!values.fetch};
    if (!body.name || !body.url) { setRemoteMsg(t("name_url_required"), false); return; }
    url = "/api/remote-add";
  } else {
    let list;
    try { list = (await api("/api/remotes", {})).remotes; } catch (e) { setRemoteMsg(e.message, false); return; }
    if (!list.length) { setRemoteMsg(t("no_remotes"), false); return; }
    const values = await openActionDialog(t("remote_remove"),
      '<label>' + esc(t("remote_to_remove")) + '<select name="name">' + list.map(r =>
        '<option value="' + esc(r.name) + '">' + esc(r.name) + (r.url ? " — " + esc(r.url) : "") + "</option>").join("") +
      "</select></label>", t("delete"));
    if (!values) return;
    body = {name: values.name};
    url = "/api/remote-remove";
    ask = t("confirm_remove_remote", {name: body.name});
  }
  if (ask && !confirm(ask)) return;
  btn.disabled = true;
  setRemoteMsg(body.fetch ? t("running_fetch") : t("running"), "progress");
  post(url, body)
    .then(r => { setRemoteMsg(r.message, r.ok !== false && !r.fetch_failed); return refreshGraph().catch(() => {}); })
    .catch(e => setRemoteMsg(e.message, false))
    .finally(() => { btn.disabled = false; });
}
function clearCmp() {
  compareAHash = null; compareBHash = null; actionHash = null;
  updateCmpClasses();
  saveUiState();
}
function selectActionTarget(hash) { actionHash = hash; updateCmpClasses(); saveUiState(); }
function doRebase() {
  if (!actionHash) return;
  const h = actionHash, btn = document.getElementById("rebaseBtn");
  btn.disabled = true;
  fetch("/api/head").then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }))
    .then(hd => {
      if (hd.merging) throw new Error(t("op_in_progress_finish"));
      if (!hd.branch) throw new Error(t("detached_rebase"));
      if (!confirm(t("confirm_rebase", {branch: hd.branch, hash: h.slice(0, 7)}))) return null;
      setGraphStatus(t("rebasing"), "progress");
      return post("/api/rebase", {hash: h});
    })
    .then(r => {
      if (r === null) { setGraphStatus(""); return; }
      clearCmp();
      setGraphStatus(r.message, "success");
      return refreshGraph().catch(() => {});
    })
    .catch(e => { setGraphStatus(t("rebase_error", {error: e.message}), "error"); })
    .finally(() => { btn.disabled = false; });
}
function doRebaseRange() {
  if (!compareAHash || !compareBHash) return;
  const from = compareAHash, to = compareBHash, btn = document.getElementById("rebaseRangeBtn");
  btn.disabled = true;
  fetch("/api/head").then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }))
    .then(hd => {
      if (hd.merging) throw new Error(t("op_in_progress_finish"));
      if (!hd.branch) throw new Error(t("detached_rebase"));
      if (!confirm(t("confirm_rebase_range", {branch: hd.branch, from: from.slice(0, 7), to: to.slice(0, 7)}))) return null;
      setGraphStatus(t("rebasing"), "progress");
      return post("/api/rebase-range", {from: from, to: to});
    })
    .then(r => {
      if (r === null) { setGraphStatus(""); return; }
      clearCmp();
      setGraphStatus(r.message, "success");
      return refreshGraph().catch(() => {});
    })
    .catch(e => { setGraphStatus(t("rebase_error", {error: e.message}), "error"); })
    .finally(() => { btn.disabled = false; });
}
function doRebaseDescendants() {
  if (!compareAHash || !compareBHash) return;
  const from = compareAHash, to = compareBHash, btn = document.getElementById("rebaseDescendantsBtn");
  btn.disabled = true;
  fetch("/api/head").then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }))
    .then(hd => {
      if (hd.merging) throw new Error(t("op_in_progress_finish"));
      if (!hd.branch) throw new Error(t("detached_rebase"));
      if (!confirm(t("confirm_rebase_descendants", {from: from.slice(0, 7), to: to.slice(0, 7)}))) return null;
      setGraphStatus(t("rebasing"), "progress");
      return post("/api/rebase-descendants", {from: from, to: to});
    })
    .then(r => {
      if (r === null) { setGraphStatus(""); return; }
      clearCmp();
      setGraphStatus(r.message, "success");
      return refreshGraph().catch(() => {});
    })
    .catch(e => { setGraphStatus(t("rebase_error", {error: e.message}), "error"); })
    .finally(() => { btn.disabled = false; });
}
function doRebaseEquivalent() {
  if (!compareAHash || !compareBHash) return;
  const from = compareAHash, to = compareBHash, btn = document.getElementById("rebaseEquivalentBtn");
  btn.disabled = true;
  fetch("/api/head").then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }))
    .then(hd => {
      if (hd.merging) throw new Error(t("op_in_progress_finish"));
      if (!hd.branch) throw new Error(t("detached_rebase"));
      if (!confirm(t("confirm_rebase_equivalent", {from: from.slice(0, 7), to: to.slice(0, 7)}))) return null;
      setGraphStatus(t("rebasing"), "progress");
      return post("/api/rebase-equivalent", {from: from, to: to});
    })
    .then(r => {
      if (r === null) { setGraphStatus(""); return; }
      clearCmp();
      setGraphStatus(r.message, "success");
      return refreshGraph().catch(() => {});
    })
    .catch(e => { setGraphStatus(t("rebase_error", {error: e.message}), "error"); })
    .finally(() => { btn.disabled = false; });
}
function doFindEquivalentBranch() {
  if (!compareAHash || !compareBHash) return;
  const from = compareAHash, to = compareBHash, btn = document.getElementById("findEquivalentBranchBtn");
  btn.disabled = true;
  setCmpMsg(t("find_equivalent_branch") + "…", "progress");
  post("/api/find-equivalent-branch", {from: from, to: to})
    .then(r => {
      compareBHash = r.to;
      updateCmpClasses();
      saveUiState();
      setCmpMsg("", true);
      setCmpMsg(r.message, true);
    })
    .catch(e => setCmpMsg(e.message, false))
    .finally(() => { btn.disabled = false; });
}
function doFindEquivalentParents() {
  if (!compareAHash || !compareBHash) return;
  const from = compareAHash, to = compareBHash, btn = document.getElementById("findEquivalentParentsBtn");
  btn.disabled = true;
  setCmpMsg(t("find_equivalent_parents") + "…", "progress");
  post("/api/find-equivalent-parents", {from: from, to: to})
    .then(r => {
      if (!r.from || !r.to) { setCmpMsg(r.message, true); return; }
      compareAHash = r.from;
      compareBHash = r.to;
      updateCmpClasses();
      saveUiState();
      setCmpMsg("", true);
      setCmpMsg(r.message, true);
    })
    .catch(e => setCmpMsg(e.message, false))
    .finally(() => { btn.disabled = false; });
}
function doCherryPick() {
  if (!actionHash) return;
  const h = actionHash, btn = document.getElementById("cherryBtn");
  btn.disabled = true;
  fetch("/api/head").then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }))
    .then(hd => {
      if (hd.merging) throw new Error(t("op_in_progress_finish"));
      if (!confirm(t("confirm_cherry_pick", {hash: h.slice(0, 7), target: hd.branch ? t("what_branch", {name: hd.branch}) : t("target_detached_head")}))) return null;
      setGraphStatus(t("cherry_picking"), "progress");
      return post("/api/cherry-pick", {hash: h});
    })
    .then(r => {
      if (r === null) { setGraphStatus(""); return; }
      clearCmp();
      setGraphStatus(r.message, "success");
      return refreshGraph().catch(() => {});
    })
    .catch(e => { setGraphStatus(t("cherry_pick_error", {error: e.message}), "error"); })
    .finally(() => { btn.disabled = false; });
}
function doRevert() {
  if (!actionHash) return;
  const h = actionHash, btn = document.getElementById("revertBtn");
  btn.disabled = true;
  fetch("/api/head").then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }))
    .then(hd => {
      if (hd.merging) throw new Error(t("op_in_progress_finish"));
      if (!confirm(t("confirm_revert", {hash: h.slice(0, 7), target: hd.branch ? t("what_branch", {name: hd.branch}) : t("target_detached_head")}))) return null;
      setGraphStatus(t("reverting"), "progress");
      return post("/api/revert", {hash: h});
    })
    .then(r => {
      if (r === null) { setGraphStatus(""); return; }
      clearCmp();
      setGraphStatus(r.message, "success");
      return refreshGraph().catch(() => {});
    })
    .catch(e => { setGraphStatus(t("revert_error", {error: e.message}), "error"); })
    .finally(() => { btn.disabled = false; });
}
async function doRenameBranch(btn) {
  const branches = localBranches(actionHash).sort();
  if (!branches.length) return;
  const values = await openActionDialog(t("rename_branch"),
    '<label>' + esc(t("branch_to_rename")) + '<select name="old">' + branches.map(branch =>
      '<option value="' + esc(branch) + '">' + esc(branch) + "</option>").join("") + "</select></label>" +
    '<label>' + esc(t("new_branch_name")) + '<input type="text" name="new_name" value="' + esc(branches[0]) + '" required autofocus></label>',
    t("rename"),
    {onOpen: form => {  // 選んだブランチ名を、新しい名前の初期値にする
      const sel = form.elements["old"], inp = form.elements["new_name"];
      sel.addEventListener("change", () => { inp.value = sel.value; });
    }});
  if (!values) return;
  const oldName = values.old, newName = values.new_name.trim();
  if (!newName || newName === oldName) { setCmpMsg(t("branch_rename_same"), false); return; }
  btn.disabled = true; setCmpMsg(t("renaming"), "progress");
  post("/api/branch-rename", {old: oldName, new: newName})
    .then(r => { setCmpMsg(r.message, true); return refreshGraph().catch(() => {}); })
    .catch(e => setCmpMsg(t("rename_failed", {error: e.message}), false))
    .finally(() => { btn.disabled = false; });
}
async function doReset() {
  if (!actionHash) return;
  const h = actionHash, btn = document.getElementById("resetBtn");
  const options = await openActionDialog(t("reset"),
    '<label>' + esc(t("reset_mode")) + '<select name="mode">' +
    '<option value="mixed" selected>' + esc(t("reset_opt_mixed")) + '</option>' +
    '<option value="soft">' + esc(t("reset_opt_soft")) + '</option>' +
    '<option value="hard">' + esc(t("reset_opt_hard")) + '</option>' +
    '</select></label>', t("reset_submit"));
  if (!options) return;
  const mode = options.mode;
  if (!confirm(t({hard: "confirm_reset_hard", soft: "confirm_reset_soft", mixed: "confirm_reset_mixed"}[mode], {hash: h.slice(0, 7)}))) return;
  btn.disabled = true;
  setGraphStatus(t("resetting"), "progress");
  try {
    const r = await post("/api/reset", {hash: h, mode: mode});
    clearCmp();
    setGraphStatus(r.message, "success");
    await refreshGraph().catch(() => {});
  } catch (e) {
    setGraphStatus(t("reset_error", {error: e.message}), "error");
  } finally {
    btn.disabled = false;
  }
}
async function doRebaseI() {  // 対話的リベース (git rebase -i 相当)
  if (!actionHash) return;
  const h = actionHash, btn = document.getElementById("irebaseBtn");
  btn.disabled = true;
  try {
    const hd = await fetch("/api/head").then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }));
    if (hd.merging) throw new Error(t("op_in_progress_finish"));
    setGraphStatus(t("loading_rebase_commits"), "progress");
    const info = await fetch("/api/rebase-todo?hash=" + encodeURIComponent(h))
      .then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }));
    setGraphStatus("");
    const msgs = {};
    const opt = (v, label) => '<option value="' + v + '">' + label + "</option>";
    const rows = info.commits.map(c => {
      msgs[c.hash] = c.message;
      return '<div class="ri-row pick" data-h="' + c.hash + '"><div class="ri-main">' +
        '<select name="act_' + c.hash + '">' + opt("pick", "pick") + opt("reword", "reword") + opt("squash", "squash") +
        opt("fixup", "fixup") + opt("drop", "drop") + "</select>" +
        "<code>" + esc(c.short) + '</code><span class="ri-subj" title="' + esc(c.subject) + '">' + esc(c.subject) + "</span>" +
        '<span class="ri-author">' + esc(c.author) + " " + esc(c.date) + "</span>" +
        '<button type="button" data-ri="up" title="' + esc(t("ri_up")) + '">▲</button>' +
        '<button type="button" data-ri="down" title="' + esc(t("ri_down")) + '">▼</button></div>' +
        '<textarea name="msg_' + c.hash + '" rows="3" hidden>' + esc(c.message) + "</textarea></div>";
    }).join("");
    const fields = '<div class="ri-note">' + t("ri_note", {branch: esc(info.branch), hash: esc(h.slice(0, 7))}) + '</div>' +
      '<div class="ri-list">' + rows + "</div>";
    const fieldsEl = document.getElementById("actionFields");
    const validate = values => {
      const acts = Object.keys(values).filter(k => k.startsWith("act_"));
      const kept = acts.filter(k => values[k] !== "drop");
      if (kept.length && ["squash", "fixup"].includes(values[kept[0]]))
        return t("ri_first_squash");
      const bad = acts.find(k => values[k] === "reword" && !(values["msg_" + k.slice(4)] || "").trim());
      if (bad) return t("ri_reword_required");
      return null;
    };
    const p = openActionDialog(t("interactive_rebase"), fields, t("run_rebase"), {wide: true, validate: validate});
    fieldsEl.onclick = ev => {  // 並べ替え
      const b = ev.target.closest("[data-ri]");
      if (!b) return;
      const row = b.closest(".ri-row");
      if (b.dataset.ri === "up" && row.previousElementSibling) row.parentNode.insertBefore(row, row.previousElementSibling);
      if (b.dataset.ri === "down" && row.nextElementSibling) row.parentNode.insertBefore(row.nextElementSibling, row);
    };
    fieldsEl.onchange = ev => {  // 操作の種類に合わせて表示を切り替える
      const sel = ev.target.closest("select");
      if (!sel) return;
      const row = sel.closest(".ri-row");
      row.className = "ri-row " + sel.value;
      row.querySelector("textarea").hidden = sel.value !== "reword";
    };
    const values = await p;
    fieldsEl.onclick = null; fieldsEl.onchange = null;
    if (!values) return;
    const todo = Object.keys(values).filter(k => k.startsWith("act_"))
      .map(k => ({hash: k.slice(4), action: values[k], message: values["msg_" + k.slice(4)] || ""}));
    const n = a => todo.filter(t => t.action === a).length;
    const note = [n("reword") && t("ri_part_reword", {n: n("reword")}), n("squash") && t("ri_part_squash", {n: n("squash")}),
                  n("fixup") && t("ri_part_fixup", {n: n("fixup")}), n("drop") && t("ri_part_drop", {n: n("drop")})].filter(Boolean).join(" / ");
    if (!confirm(t("confirm_rebase_i", {branch: info.branch, count: todo.length, hash: h.slice(0, 7), note: note ? " (" + note + ")" : ""}))) return;
    setGraphStatus(t("rebasing"), "progress");
    const r = await post("/api/rebase-i", {hash: h, todo: todo});
    clearCmp();
    setGraphStatus(r.message, "success");
    await refreshGraph().catch(() => {});
  } catch (e) {
    setGraphStatus(t("rebase_i_error", {error: e.message}), "error");
  } finally {
    btn.disabled = false;
  }
}
async function doMerge() {
  if (!actionHash) return;
  const options = await openActionDialog(t("merge"), '<label class="check" title="' + esc(t("tip_no_ff")) + '">' +
    '<input type="checkbox" name="noFf"> --no-ff</label>', t("merge"));
  if (!options) return;
  const h = actionHash, noFf = options.noFf;
  const btn = document.getElementById("mergeBtn");
  btn.disabled = true;
  fetch("/api/head").then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }))
    .then(hd => {
      if (hd.merging) throw new Error(t("merge_in_progress_resolve"));
      const into = hd.branch ? t("what_branch", {name: hd.branch}) : "detached HEAD";
      if (!confirm(t("confirm_merge", {hash: h.slice(0, 7), into: into, noff: noFf ? " (--no-ff)" : ""}))) return null;
      setGraphStatus(t("merging"), "progress");
      return post("/api/merge", {hash: h, no_ff: noFf});
    })
    .then(r => {
      if (r === null) { setGraphStatus(""); return; }
      clearCmp();
      setGraphStatus(r.message, r.conflict ? "error" : "success");
      return refreshGraph().catch(() => {}).then(() => {
        if (r.conflict) showWip();
      });
    })
    .catch(e => { setGraphStatus(t("merge_error", {error: e.message}), "error"); })
    .finally(() => { btn.disabled = false; });
}
function selectCmpA(hash) {
  compareAHash = hash;
  if (compareBHash === hash) compareBHash = null;
  if (compareAHash && compareBHash) { abActOpen = true; cmpActOpen = false; setOtherMenu(false); }
  updateCmpClasses();
  saveUiState();
}
function selectCompareHash(slot, hash) {
  if (slot === "A") selectCmpA(hash);
  else selectCmpB(hash);
}
function selectCmpB(hash) {
  compareBHash = hash;
  if (compareAHash === hash) compareAHash = null;
  if (compareAHash && compareBHash) { abActOpen = true; cmpActOpen = false; setOtherMenu(false); }
  updateCmpClasses();
  saveUiState();
}
function restoreScreen(state) {
  if (Object.prototype.hasOwnProperty.call(state, "compareA")) compareAHash = state.compareA;
  if (Object.prototype.hasOwnProperty.call(state, "compareB")) compareBHash = state.compareB;
  if (Object.prototype.hasOwnProperty.call(state, "actionHash")) actionHash = state.actionHash;
  cmpActOpen = false;  // メニューは画面の復元では開かない (グラフを隠さない)
  if (state.screen === "log") showLog(state.titleHtml, state.rev, state.topHtml, true, state.scroll || 0, state.src, state.targetHash);
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
  const cmpButton = ev.target.closest("[data-cmp]");
  if (cmpButton) {
    ev.preventDefault();
    ev.stopPropagation();
    selectCompareHash(cmpButton.dataset.cmp, tr.dataset.h);
    return;
  }
  if (ev.target.closest(".copy")) return;
  selectActionTarget(tr.dataset.h);
  if (curRow) curRow.classList.remove("cur");
  curRow = tr; tr.classList.add("cur");
  restoreMenuPosition(document.getElementById("cmpActions"));
  cmpActOpen = true; abActOpen = false; setOtherMenu(false);
  updateCmpClasses();
  showDetail(tr.dataset.h);
});
detailView.addEventListener("click", ev => {
  if (ev.target.closest("#wipReload")) { showWip(true); refreshGraph().catch(() => {}); return; }
  if (ev.target.closest("#wipCommit")) { doWipCommit(); return; }
  if (ev.target.closest("#wipDiscard")) { doWipDiscard(); return; }
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
  if (ev.target.closest(".dropdown .abtn")) closeMenus();  // 無効なボタンはクリック自体が発火しない
});
document.addEventListener("click", ev => {  // メニューの外をクリックしたら閉じる
  if (ev.target.closest(".menu-wrap") || ev.target.closest("svg g.node") || ev.target.closest("#panel")) return;  // 右ペインのクリックでは閉じない  // ノードのクリックは onNodeClick が扱う
  closeMenus();
});
document.addEventListener("keydown", ev => { if (ev.key === "Escape") closeMenus(); });
function select(g) {
  if (selected) selected.classList.remove("sel");
  selected = g; g.classList.add("sel");
  const t = g.querySelector("title");
  selectedKey = t ? t.textContent : null;  // グラフを作り直したあとで選択を復元するため
}

function onNodeClick(g, n, ev) {
  const openMenu = ev && ev.type === "contextmenu";
  if (openMenu && (n.hash === compareAHash || n.hash === compareBHash)) {
    cmpActOpen = false; abActOpen = true; setOtherMenu(false);
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
  selectActionTarget(n.hash);
  select(g);
  if (openMenu) positionMenuNearPointer(document.getElementById("cmpActions"), ev);
  showLog("git log --oneline " + esc(n.hash.slice(0, 7)), n.hash,
          chips(n.hash) + '<div class="meta">' + t("all_log_to_root") + '</div>');
}

function init() {
  document.querySelectorAll("svg g.node").forEach(g => {
    const t = g.querySelector("title");
    const n = t && DATA.nodes[t.textContent];
    if (n) {
      nodeEl[n.hash] = g;
      const box = g.getBBox(), hit = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      hit.setAttribute("x", box.x); hit.setAttribute("y", box.y);
      hit.setAttribute("width", box.width); hit.setAttribute("height", box.height);
      hit.setAttribute("fill", "transparent"); hit.setAttribute("pointer-events", "all");
      g.insertBefore(hit, g.children[1] || null);
      g.addEventListener("click", ev => { if (ev.detail <= 1) onNodeClick(g, n, ev); });
      g.addEventListener("contextmenu", ev => { ev.preventDefault(); onNodeClick(g, n, ev); });
    }
  });
  document.querySelectorAll("svg g.edge").forEach(g => {
    const t = g.querySelector("title");
    const e = t && DATA.edges[t.textContent];
    if (!e) return;
    const path = g.querySelector("path");
    if (path) { const hit = path.cloneNode(); hit.setAttribute("class", "hit"); g.appendChild(hit); }
    g.addEventListener("click", () => {
      clearCmp(); select(g);
      const range = e.from.slice(0, 7) + ".." + e.to.slice(0, 7);
      showLog(esc(e.from.slice(0, 7)) + " → " + esc(e.to.slice(0, 7)), e.from + ".." + e.to,
              '<div class="meta"><code>git log ' + esc(range) + "</code></div>");
    });
  });
}

// ---- 作業ツリーの変更 (WIP) ----
// チェックしたファイルだけを add してコミットする (TortoiseGit のコミットダイアログと同じ考え方)。
// 既定ではステージ済みのファイルがチェック済み。チェックを外したステージ済みファイルはステージから外れる。
let wipSel = {}, wipStaged = new Set(), wipMerging = false, wipMsg = "";  // wipSel: パス → ユーザーが付け外ししたチェック状態
// amend モード: チェック中は「直前のコミットの親」と作業ツリーの差分を出し、メッセージも直前のものを入れる
let wipAmendOn = false, wipMsgNormal = "", wipHeadMsg = "";  // wipMsgNormal: amend にする前に書いていたメッセージ (解除で戻す)
function wipIsChecked(f) {  // amend: 直前のコミットのファイルは、外さない限りコミットに残る (チェック済みが既定)
  return f.path in wipSel ? wipSel[f.path] : (wipMerging || !!f.in_head || wipStaged.has(f.path));
}
function wipCheckCell(f) {
  return '<td class="fk"><input type="checkbox" class="wipck" data-kind="' + esc(f.wip) + '" data-dir="' + esc(dirOf(f.path)) + '" data-path="' + esc(f.path) + '"' +
    (wipIsChecked(f) ? " checked" : "") + (f.in_head ? ' title="' + t("wip_file_remove_title") + '"' : "") + "></td>";
}
function wipCks(kind) {  // 操作できる (disabled でない) チェックボックス
  return [...detailView.querySelectorAll(".wipck" + (kind ? '[data-kind="' + kind + '"]' : ""))].filter(x => !x.disabled);
}
function wipPaths() {  // チェックされたパス (同じファイルが ステージ済み と 未ステージ の両方に出ても1件)
  const s = new Set();
  wipCks().forEach(el => { if (el.checked) s.add(el.dataset.path); });
  return [...s];
}
function setWipPath(p, v) {  // 同じパスのチェックボックスをまとめて切り替える
  wipSel[p] = v;
  wipCks().forEach(x => { if (x.dataset.path === p) x.checked = v; });
}
function wipSync() {  // コミットボタンの件数と、グループの全選択チェックを更新
  const n = wipPaths().length, b = detailView.querySelector("#wipCommit");
  const dis = detailView.querySelector("#wipDiscard");
  if (dis) { dis.disabled = n === 0; dis.textContent = t("wip_discard") + " (" + n + ")"; }
  if (b) {
    b.disabled = n === 0;
    b.textContent = t("wip_commit") + " (" + n + ")" + (wipAmendOn ? " amend" : "");
  }
  detailView.querySelectorAll(".wipdir").forEach(dc => {
    const cks = wipCks(dc.dataset.kind).filter(x => x.dataset.dir === dc.dataset.dir), c = cks.filter(x => x.checked).length;
    dc.disabled = cks.length === 0;
    dc.checked = cks.length > 0 && c === cks.length;
    dc.indeterminate = c > 0 && c < cks.length;
  });
  detailView.querySelectorAll(".wipall").forEach(all => {
    const cks = wipCks(all.dataset.kind), c = cks.filter(x => x.checked).length;
    all.disabled = cks.length === 0;
    all.checked = cks.length > 0 && c === cks.length;
    all.indeterminate = c > 0 && c < cks.length;
  });
}
function wipSay(text, ok) {
  const el = detailView.querySelector("#wipMsgOut");
  if (el) { el.textContent = text; el.className = "amsg " + (ok ? "ok" : "ng"); }
  else setGraphStatus(text, ok ? "success" : "error");
}
function setWipAmend(on) {  // amend のチェックを切り替える: 差分の一覧とメッセージを入れ替えて再表示
  if (on === wipAmendOn) return;
  wipSel = {};  // 一覧が変わるので、付け外ししたチェックはリセット
  if (on) { wipMsgNormal = wipMsg; wipMsg = wipHeadMsg; }  // 直前のコミットのメッセージを入れておく
  else { wipMsg = wipMsgNormal; wipMsgNormal = ""; }
  wipAmendOn = on;
  showWip(true);
}
function doWipCommit() {
  const amend = wipAmendOn, paths = wipPaths(), ta = detailView.querySelector("#wipMsg"), msg = ta ? ta.value : "";
  const branchInput = detailView.querySelector("#wipBranch");
  const branch = branchInput ? branchInput.value.trim() : "";
  if (!msg.trim()) { wipSay(t("message_required"), false); return; }
  if (!amend && !paths.length) { wipSay(t("select_files"), false); return; }
  if (amend) {
    const chk = new Set(paths), drops = curFiles.filter(f => f.in_head && !chk.has(f.path)).length;
    const dropText = drops ? "\n" + t("amend_drop", {n: drops}) : "";
    if (!confirm(t("amend_confirm", {drop: dropText}))) return;
  }
  const btn = detailView.querySelector("#wipCommit");
  if (btn) btn.disabled = true;
  wipSay(t("wip_execute"), true);
  post("/api/wip-commit", {paths: paths, message: msg, amend: amend, branch: branch})
    .then(r => {
      wipMsg = ""; wipMsgNormal = ""; wipSel = {}; wipAmendOn = false;
      showWip(true, r.message);
      refreshGraph().catch(() => {});
    })
    .catch(e => { wipSay(e.message, false); wipSync(); });
}
detailView.addEventListener("change", ev => {
  if (ev.target.id === "wipAmendCk") { setWipAmend(ev.target.checked); return; }
  const ck = ev.target.closest(".wipck"), all = ev.target.closest(".wipall"), dc = ev.target.closest(".wipdir");
  if (ck) setWipPath(ck.dataset.path, ck.checked);
  else if (dc) wipCks(dc.dataset.kind).filter(x => x.dataset.dir === dc.dataset.dir).forEach(x => setWipPath(x.dataset.path, dc.checked));
  else if (all) wipCks(all.dataset.kind).forEach(x => setWipPath(x.dataset.path, all.checked));
  else return;
  wipSync();
});
detailView.addEventListener("input", ev => { if (ev.target.id === "wipMsg") wipMsg = ev.target.value; });

function resolveBarHtml() {  // 競合ファイルを ours / theirs で解決するボタン (チェックしたファイルが対象)
  return '<div class="wipres"><button class="abtn" data-resolve="ours">' + esc(t("resolve_ours")) + '</button>' +
    '<button class="abtn" data-resolve="theirs">' + esc(t("resolve_theirs")) + '</button>' +
    '<span class="hint">' + esc(t("resolve_hint")) + '</span></div>';
}
function opBannerHtml(cmd, canWrite) {  // マージなどの途中であることと、中断ボタン
  if (!cmd) return "";
  return '<div class="wipop"><span>' + esc(t("op_banner", {cmd: cmd})) + '</span>' +
    (canWrite ? '<button class="abtn danger" id="wipAbort" data-cmd="' + esc(cmd) + '">' + esc(t("abort_button", {cmd: cmd})) + '</button>' : "") + '</div>';
}
function doWipResolve(side) {
  const paths = wipCks("conflict").filter(x => x.checked).map(x => x.dataset.path);
  if (!paths.length) { wipSay(t("select_files"), false); return; }
  if (!confirm(t("confirm_resolve", {n: paths.length, side: side}))) return;
  wipSay(t("wip_execute"), true);
  post("/api/resolve", {paths: paths, side: side})
    .then(r => { wipSel = {}; showWip(true, r.message); refreshGraph().catch(() => {}); })
    .catch(e => wipSay(e.message, false));
}
function doWipDiscard() {
  const paths = wipPaths();
  if (!paths.length) return;
  const confl = new Set(curFiles.filter(f => f.wip === "conflict").map(f => f.path)), hit = paths.filter(p => confl.has(p));
  if (hit.length) { wipSay(t("discard_conflict", {files: hit.slice(0, 3).join(", ")}), false); return; }
  if (!confirm(t("confirm_discard", {n: paths.length}))) return;
  const btn = detailView.querySelector("#wipDiscard");
  if (btn) btn.disabled = true;
  wipSay(t("wip_execute"), true);
  post("/api/discard", {paths: paths})
    .then(r => { wipSel = {}; showWip(true, r.message); refreshGraph().catch(() => {}); })
    .catch(e => { wipSay(e.message, false); wipSync(); });
}
function doWipAbort() {
  const b = detailView.querySelector("#wipAbort");
  if (!b) return;
  if (!confirm(t("confirm_abort", {cmd: b.dataset.cmd}))) return;
  b.disabled = true;
  post("/api/abort", {})
    .then(r => { wipSel = {}; wipMsg = ""; wipMsgNormal = ""; wipAmendOn = false; showWip(true, r.message); return refreshGraph().catch(() => {}); })
    .catch(e => { b.disabled = false; wipSay(e.message, false); });
}

function showWip(fromHistory, okMsg) {
  document.body.classList.remove("compare-logs-mode");
  if (!fromHistory) pushScreen({screen: "wip"});
  setDocumentTitle(t("wip_title"));
  const btns = '<button class="back-btn">' + t("back_history") + '</button><button class="sbtn" id="wipReload">' + t("reload") + '</button>';
  detailView.innerHTML = btns + '<p class="hint">' + t("wip_loading") + '</p>';
  showView("detail"); curDetailHash = null;
  api("/api/status", wipAmendOn ? {amend: 1} : {}).then(s => {
    wipStaged = new Set(s.staged.map(f => f.path)); wipMerging = !!s.merging; wipHeadMsg = s.head_message || "";
    const canAmend = !!(s.head && !s.merging && !s.rebasing);
    if (wipAmendOn && !s.amend_files) {  // マージ中になったなどで amend できなくなった: 通常のコミットに戻す
      wipMsg = wipMsgNormal; wipMsgNormal = ""; wipAmendOn = false;
    }
    const canCommit = !!DATA.writable;  // 書き込みが無効 (--read-only など) のときはチェック欄を出さない
    const opCmd = s.merging ? "merge" : s.rebasing ? "rebase" : s.cherry_picking ? "cherry-pick" : s.reverting ? "revert" : null;
    const sections = wipAmendOn
      ? [["amend", t("amend_content"), s.amend_files]]
      : [["conflict", t("conflict"), s.conflicts], ["staged", t("staged"), s.staged],
         ["unstaged", t("unstaged"), s.unstaged], ["untracked", t("untracked"), s.untracked]];
    let files = [], body = "";
    sections.forEach(([kind, title, items]) => {
      if (!items.length) return;
      const list = items.map(f => Object.assign({wip: kind}, f));
      body += "<h3>" + (canCommit ? '<input type="checkbox" class="wipall" data-kind="' + kind + '" title="' + t("select_all") + '">' : "") +
        title + " (" + list.length + ")</h3>" + (kind === "conflict" && canCommit ? resolveBarHtml() : "") + filesHtml({}, list, files.length, canCommit, true);
      files = files.concat(list);
    });
    curCtx = {}; curFiles = files;
    const hint = wipAmendOn
      ? t("amend_hint")
      : t("wip_hint");
    const box = canCommit && (files.length || wipAmendOn)
      ? '<div class="wipbox">' +
        '<textarea id="wipMsg" placeholder="' + t("wip_commit_message") + '"></textarea>' +
        (!wipAmendOn ? '<input type="text" id="wipBranch" placeholder="' + esc(t("wip_commit_branch_placeholder")) + '" aria-label="' + esc(t("wip_commit_branch")) + '">' : "") +
        (canAmend ? '<label class="amendrow"><input type="checkbox" id="wipAmendCk"' + (wipAmendOn ? " checked" : "") +
          '>' + t("amend_checkbox") + '</label>' : "") +
        '<div class="arow"><button class="wbtn" id="wipCommit">' + t("wip_commit") + '</button>' +
        (!wipAmendOn && !opCmd ? '<button class="wbtn danger" id="wipDiscard">' + t("wip_discard") + '</button>' : "") +
        '<span class="hint">' + hint + '</span></div>' +
        '<div class="amsg" id="wipMsgOut"></div></div>' : "";
    detailView.innerHTML = btns + "<h2>" + t("wip_title") + "</h2>" + opBannerHtml(opCmd, canCommit) +
      '<div class="meta">HEAD: ' + (s.head ? chips(s.head) + esc(s.head.slice(0, 7)) : t("no_commits_yet")) + "</div>" +
      (body || '<p class="hint">' + t("no_changes") + '</p>') + box +
      (s.truncated ? '<p class="hint">' + t("truncated_files") + '</p>' : "");
    const ta = detailView.querySelector("#wipMsg");
    if (ta) ta.value = wipMsg;
    if (okMsg) wipSay(okMsg, true);
    wipSync();
  }).catch(e => {
    detailView.innerHTML = btns + '<p class="hint">' + esc(t("error")) + esc(e.message) + "</p>";
  });
}

document.getElementById("quitBtn").addEventListener("click", () => {  // サーバーを終了する
  if (!confirm(t("confirm_quit"))) return;
  post("/api/shutdown", {})
    .then(() => {
      document.body.innerHTML = '<div style="padding:24px;font-size:14px">' + esc(t("server_stopped")) + "</div>";
    })
    .catch(e => setGraphStatus(t("quit_failed", {error: e.message}), "error"));
});
document.getElementById("reload").addEventListener("click", ev => {  // グラフを最新の状態に更新
  const b = ev.currentTarget;
  b.disabled = true; b.textContent = t("reload_busy");
  refreshGraph().catch(() => {}).finally(() => { b.disabled = false; b.textContent = t("reload"); });
});

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