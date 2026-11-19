// ブラウザの「このページでこれ以上ダイアログを表示しない」対策。
// 抑止されると confirm() が即座に false、prompt() が null を返し、確認が必要な操作が何もできなくなる。
// 人間には不可能な速さで戻ってきたら抑止とみなし、ページ内のモーダルで確認して、承認された操作をもう一度実行する
// (承認は短時間だけ覚えておくので、再実行された操作は同じ確認をそのまま通る)。
const nativeConfirm = window.confirm.bind(window), nativePrompt = window.prompt.bind(window);
const dialogGrants = new Map();  // 種類+メッセージ -> {value, until}
let lastClickTarget = null, replayingClick = false, dialogPending = null;
document.addEventListener("click", ev => { if (!replayingClick) lastClickTarget = ev.target; }, true);
function guardedDialog(kind, msg, def, nativeFn) {
  const grant = dialogGrants.get(kind + msg);
  if (grant && grant.until > Date.now()) return grant.value;
  const t0 = performance.now(), r = nativeFn();
  if ((kind === "confirm" ? r !== false : r !== null) || performance.now() - t0 > 30) return r;
  const modal = document.getElementById("confirmModal"), input = document.getElementById("confirmModalInput");
  document.getElementById("confirmModalMsg").textContent = msg;
  input.hidden = kind !== "prompt";
  input.value = def || "";
  dialogPending = {kind: kind, msg: msg, target: lastClickTarget};
  modal.hidden = false;
  (kind === "prompt" ? input : document.getElementById("confirmModalOk")).focus();
  return kind === "confirm" ? false : null;
}
window.confirm = msg => guardedDialog("confirm", String(msg), null, () => nativeConfirm(msg));
window.prompt = (msg, def) => guardedDialog("prompt", String(msg), def, () => nativePrompt(msg, def));
function closeConfirmModal(ok) {
  const p = dialogPending, input = document.getElementById("confirmModalInput");
  dialogPending = null;
  document.getElementById("confirmModal").hidden = true;
  if (!ok || !p || !p.target || !p.target.isConnected) return;
  dialogGrants.set(p.kind + p.msg, {value: p.kind === "confirm" ? true : input.value, until: Date.now() + 10000});
  replayingClick = true;
  try { p.target.click(); } finally { replayingClick = false; }
}
document.getElementById("confirmModalOk").addEventListener("click", () => closeConfirmModal(true));
document.getElementById("confirmModalCancel").addEventListener("click", () => closeConfirmModal(false));
document.getElementById("confirmModal").addEventListener("keydown", ev => {
  if (ev.key === "Escape") { ev.preventDefault(); closeConfirmModal(false); }
  else if (ev.key === "Enter") { ev.preventDefault(); closeConfirmModal(true); }
});

/*__I18N__*/
function t(key, values = {}) {  // {name} を values[name] に置き換える (値の中の $ は特別扱いしない)
  let text = key in I18N ? I18N[key] : key;
  Object.entries(values).forEach(([name, value]) => { text = text.replaceAll("{" + name + "}", () => String(value)); });
  return text;
}

let DATA = {nodes: {}, edges: {}};
const SHOW_MERGES_KEY = "gitGraphShowMerges";
let showMergesState = null;  // 「マージ表示」のチェック状態。保存先 (localStorage) が使えない pywebview などでも切り替わるよう、メモリ上の値を正とする
function showMergesOn() {
  if (showMergesState === null) {
    try { showMergesState = localStorage.getItem(SHOW_MERGES_KEY) === "1"; } catch (e) { showMergesState = false; }
  }
  return showMergesState;
}
function setShowMerges(on) {
  showMergesState = !!on;
  try { localStorage.setItem(SHOW_MERGES_KEY, on ? "1" : "0"); } catch (e) { /* 保存できなくても表示は切り替わる (次回起動時には引き継がれない) */ }
}
let openClusters = [];  // クラスター表示で開いているクラスター (古い順)。サーバーが数を絞って返す
function graphUrl() {
  const params = [];
  if (showMergesOn()) params.push("merges=1");
  if (openClusters.length) params.push("open=" + encodeURIComponent(openClusters.join(",")));
  return "/api/graph" + (params.length ? "?" + params.join("&") : "");
}
const COLORS = CommitLog.COLORS;  // ブランチ / タグの色 (ファイル表示のログと共通。commit_log.js)
const CHUNK = CommitLog.CHUNK;
const listView = document.getElementById("listView"), detailView = document.getElementById("detailView");
const views = {list: listView, detail: detailView}, savedScroll = {list: 0, detail: 0};
let curView = "list", selected = null, selectedKey = null, token = 0, observer = null, curRow = null;
let curCtx = null, curFiles = [];
let compareAHash = null, compareBHash = null;
let actionHash = null;  // マージ / リベースなどの操作対象
// 選択の保存 (ファイル/差分ページから戻ったときに選択を復元する)。保存形式は log_select.js (ファイル表示のログの強調と共通)
function persistUiSel() { LogSel.save({compareA: compareAHash, compareB: compareBHash, actionHash: actionHash}); }
function loadPersistedUiSel() { return LogSel.load(); }
function clearPersistedUiSel() { LogSel.clear(); }
let currentBranch = null;
let currentBranchLoaded = false;
const nodeEl = {};  // ハッシュ -> グラフ上のノード要素
let graphZoom = 1;
const homeHtml = listView.innerHTML;
if (!history.state || !history.state.screen) history.replaceState({screen: "home"}, "");

function svgAttrPx(v) {  // <svg width="123pt"> の値を px に直す (dot の SVG は pt で出る)
  const m = /^([\d.]+)\s*(pt|px)?$/.exec(v || "");
  return m ? parseFloat(m[1]) * (m[2] === "pt" ? 96 / 72 : 1) : 0;
}
function applyGraphZoom() {
  const svg = document.querySelector("#graph > svg");
  if (!svg) return;
  // transform: scale() は見た目だけを変えて、レイアウト上の大きさは 100% のままなので、縮小するとスクロール範囲が図より広くなる。
  // SVG 自体の大きさを変えれば (viewBox があるので中身も拡縮する)、スクロール範囲も図に合う。
  const w = svgAttrPx(svg.getAttribute("width")), h = svgAttrPx(svg.getAttribute("height"));
  if (w && h) {
    svg.style.transform = "";
    svg.style.width = w * graphZoom + "px";
    svg.style.height = h * graphZoom + "px";
  } else {  // 大きさが分からない SVG は、従来どおり見た目だけ拡縮する
    svg.style.transformOrigin = "0 0";
    svg.style.transform = "scale(" + graphZoom + ")";
  }
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
const MIN_GRAPH_WIDTH = 260;  // 右ペインを最大まで広げても、メニューバーとメニューが収まる左側の幅を残す
function applyPanelWidth(width, compareLogs = document.body.classList.contains("compare-logs-mode")) {
  const property = compareLogs ? "--compare-panel-width" : "--panel-width";
  document.documentElement.style.setProperty(property, Math.max(240, Math.min(window.innerWidth - MIN_GRAPH_WIDTH, width)) + "px");
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
const PROJECT_DOCUMENT_TITLE = DEFAULT_DOCUMENT_TITLE.replace(/ - git graph$/, "");  // ウィンドウタイトルは「プロジェクト名 - 表示対象」
function setDocumentTitle(label) {
  document.title = label ? PROJECT_DOCUMENT_TITLE + " - " + label : DEFAULT_DOCUMENT_TITLE;
}
function setDocumentTitleFromHtml(titleHtml) {
  const el = document.createElement("span");
  el.innerHTML = titleHtml;
  setDocumentTitle(el.textContent || el.innerText || "");
}
function chips(h) {  // コミットのブランチ / タグの札 (ファイル表示のログと共通の描画。commit_log.js)
  const n = DATA.nodes[h];
  return n ? CommitLog.chipsHtml(n.refs, currentBranch) : "";
}
function detailChips(h) { return chips(h); }

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

function copyLink(h) { return CommitLog.copyLink(h); }  // フルハッシュをコピーするリンク
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
  persistUiSel();  // 別ページ (/files /diff) から戻っても選択を復元できるようにする
}
function rowHtml(c, extraClass, graphTd) {  // c = [hash, short, author, date, subject, parents?]; graphTd = マージグラフの列。行の描画はファイル表示のログと共通 (commit_log.js)
  return CommitLog.rowHtml(c, {extraClass: extraClass, graphTd: graphTd, chips: chips(c[0])});
}


// 右ボタンドラッグ左右で戻る / 進む。
// ドラッグ（有意な移動）だった場合は contextmenu を止め、戻る/進む以外の右クリック機能を出さない。
// 一度有意な横方向に動いたあと反対方向へ動いたらジェスチャを無効にする（右→左→右で進むになるのを防ぐ）。
(function () {
  if (window.__ggRightDragNav) return;
  window.__ggRightDragNav = true;
  const MOVE_THRESH = 6;   // px: これ以上動いたらドラッグ扱い（メニュー抑制・ナビ対象）
  const DIR_THRESH = 40;   // px: 方向を確定する横移動量
  let startX = 0, startY = 0, active = false, moved = false, dir = 0, invalidated = false;
  let lastStartX = 0, lastStartY = 0;  // contextmenu 時にも距離判定できるように保持
  document.addEventListener("pointerdown", ev => {
    if (ev.button !== 2 || ev.pointerType === "touch") return;
    startX = lastStartX = ev.clientX;
    startY = lastStartY = ev.clientY;
    active = true;
    moved = false;
    dir = 0;
    invalidated = false;
  }, true);
  document.addEventListener("pointermove", ev => {
    if (!active) return;
    const dx = ev.clientX - startX, dy = ev.clientY - startY;
    if (Math.hypot(dx, dy) >= MOVE_THRESH) moved = true;
    if (Math.abs(dx) >= DIR_THRESH) {
      const d = dx > 0 ? 1 : -1;  // +右=進む, -左=戻る
      if (dir === 0) dir = d;
      else if (d !== dir) invalidated = true;
    }
  }, true);
  function endRightDrag() {
    if (!active) return;
    active = false;
    if (invalidated || !moved || dir === 0) return;
    if (dir < 0) history.back();
    else history.forward();
  }
  document.addEventListener("pointerup", endRightDrag, true);
  document.addEventListener("pointercancel", () => { active = false; }, true);
  // ドラッグだったときは contextmenu を止め、他の右クリックメニュー等を出さない。
  // 開始直後や pointermove が来ない環境でも、解放位置との距離で判定する。
  document.addEventListener("contextmenu", ev => {
    const dx = ev.clientX - lastStartX, dy = ev.clientY - lastStartY;
    const dist = Math.hypot(dx, dy);
    const wasDrag = moved || dist >= MOVE_THRESH || active;
    if (wasDrag) {
      ev.preventDefault();
      ev.stopPropagation();
      // active のまま来た場合もナビしないようクリア
      if (active) active = false;
    }
  }, true);
})();
