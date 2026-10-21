/*__I18N__*/
function t(key, values = {}) {  // {name} を values[name] に置き換える (値の中の $ は特別扱いしない)
  let text = key in I18N ? I18N[key] : key;
  Object.entries(values).forEach(([name, value]) => { text = text.replaceAll("{" + name + "}", () => String(value)); });
  return text;
}

let DATA = {nodes: {}, edges: {}};
const COLORS = {branch: "#b7e4a7", current: "#b7e4a7", remote: "#a7c8f0", tag: "#ffe9a0", head: "#f4a7a7", stash: "#d9c7f2"};
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

