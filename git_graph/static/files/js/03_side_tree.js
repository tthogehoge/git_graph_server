/* ---- files/03_side_tree.js ---- */
// ---- 左ペインのツリー: フォルダはクリックで展開・折りたたみ (右ペインは動かさない)。フォルダの行の「右ペインで開く」アイコンで、右ペインへ開く ----
// 右ペインのディレクトリ (current / dirEntries / loadTree) とは別に持つ。左は、展開したディレクトリごとに /api/tree で読む。
// 左と右で別のフォルダを開いておき、行をドラッグして移せる (03_tree_dnd.js)。
const sideData = new Map();      // ディレクトリのパス → 項目の配列 (読み込み済みのもの)。"" はルート
const sideExpanded = new Set();  // 展開しているディレクトリ (ルートは、いつも展開している)
let sideMulti = new Set();       // Ctrl / Shift + クリックで選んだ行 (パス)。まとめて移すときの対象。.selected (キーボードの行) とは別
let sideSeq = 0;                 // 全体の読み直しの、古い応答を捨てるための通し番号
const SIDE_INDENT = 14, SIDE_PAD = 6;

function moveEnabled() { return isWip && CAN_EDIT; }  // 作業ツリーを編集できるときだけ、移せる
function sideRow(path) { return [...list.querySelectorAll(".entry")].find(e => e.dataset.path === path) || null; }
function sideAncestors(dir) {  // "a/b" → ["a", "a/b"] (ルートは含めない)
  const parts = dir ? dir.split("/") : [];
  return parts.map((p, i) => parts.slice(0, i + 1).join("/"));
}
function sideReveal(dir) { sideAncestors(dir).forEach(p => sideExpanded.add(p)); }  // そのディレクトリまで展開する (読み込みは、読み直しで)
function sideVisibleDirs() {  // 読むディレクトリ: ルートと、祖先がすべて展開している、展開中のもの
  return [""].concat([...sideExpanded].filter(p => sideAncestors(parentDir(p)).every(a => sideExpanded.has(a))).sort());
}

function sideRowHtml(e, p, depth, open) {
  const isDir = e.type === "tree";
  const size = e.size === null ? "" : e.size.toLocaleString() + " B";
  const state = treeCheckState(p);
  const ck = wipSelectionEnabled() ? '<input type="checkbox" class="ck" data-kind="tree" data-path="' + esc(p) + '" aria-label="' + esc(p) + '"' + (state.checked ? " checked" : "") + '>' : "";
  const twisty = '<span class="twisty" aria-hidden="true">' + (isDir ? (open ? "▾" : "▸") : "") + '</span>';
  const label = t("open_in_right_pane");
  const openBtn = isDir ? '<button type="button" class="open-right" title="' + esc(label) + '" aria-label="' + esc(label) + '">↗</button>' : "";
  return '<div class="entry' + (sideMulti.has(p) ? " multi" : "") + '" data-type="' + e.type + '" data-path="' + esc(p) + '" data-depth="' + depth + '" style="padding-left:' +
    (SIDE_PAD + depth * SIDE_INDENT) + 'px"' + (moveEnabled() ? ' draggable="true"' : "") + entryTip(e) + '>' + twisty + ck + '<span>' + entryIcon(e) + ' <span class="bd">' +
    statusBadge(e, p) + '</span>' + esc(e.name) + submoduleBadge(e) + '</span>' + openBtn + '<span class="size">' + size + '</span></div>';
}
function sideNoteHtml(depth, text) {  // 「読み込み中」「空」の行 (キーボードの対象にはしない)
  return '<div class="side-note" data-depth="' + depth + '" style="padding-left:' + (SIDE_PAD + depth * SIDE_INDENT + 20) + 'px">' + esc(text) + '</div>';
}
function sideRowsHtml(dir, depth) {
  return (sideData.get(dir) || []).map(e => {
    const p = dir ? dir + "/" + e.name : e.name;
    entryInfo[p] = e;
    const open = e.type === "tree" && sideExpanded.has(p);
    let children = "";
    if (open) {
      if (!sideData.has(p)) children = sideNoteHtml(depth + 1, t("loading"));
      else children = sideData.get(p).length ? sideRowsHtml(p, depth + 1) : sideNoteHtml(depth + 1, t("empty_directory"));
    }
    return sideRowHtml(e, p, depth, open) + children;
  }).join("");
}
function sideRootHtml() {
  const label = t("open_in_right_pane");
  return '<div class="entry root" data-type="tree" data-path="" data-depth="0" style="padding-left:' + SIDE_PAD + 'px"><span class="twisty" aria-hidden="true"></span><span>📁 ' + esc(t("root")) +
    '</span><button type="button" class="open-right" title="' + esc(label) + '" aria-label="' + esc(label) + '">↗</button></div>';
}

function pruneSideMulti() {  // 見えている行だけ、選択に残す (折りたたんだフォルダの中や、消えたものは外す)
  const visible = new Set([...list.querySelectorAll(".entry:not(.root)")].map(e => e.dataset.path));
  sideMulti = new Set([...sideMulti].filter(p => visible.has(p)));
}
function applySideMulti() {
  list.querySelectorAll(".entry").forEach(e => e.classList.toggle("multi", !e.classList.contains("root") && sideMulti.has(e.dataset.path)));
  updateSelBar();
}
function markSideTree(force) {  // 右ペインで開いているフォルダ (.opened) と、開いているファイルの行 (.selected) の印
  // force: ファイルを開いたとき。左で別の行を選んでいても、そのファイルの行を選ぶ (ふだんは、選んでいる行を動かさない)
  list.querySelectorAll(".entry.opened").forEach(e => e.classList.remove("opened"));
  if (!currentFile) { const row = sideRow(current); if (row) row.classList.add("opened"); }
  if (sideMode === "tree" && currentFile) {
    const inList = !!selectedEntry && selectedEntry.isConnected && list.contains(selectedEntry);
    const cur = force || !inList ? sideRow(currentFile) : null;
    if (cur) selectEntry(cur, true);
  }
}
function afterSideRender(keepPath) {
  bindSelectionChecks(list);
  syncTreeCheckBoxes();
  pruneSideMulti();
  const keep = keepPath ? sideRow(keepPath) : null;
  if (keep) selectEntry(keep, true);
  markSideTree();
  updateSelBar();
}
function renderSideTree() {  // 全体を作り直す (スクロールと、キーボードの選択は、そのまま)
  const root = sideData.get("");
  if (!root) return;
  const scroller = list.parentElement, top = scroller.scrollTop;
  const wasInList = !!selectedEntry && list.contains(selectedEntry);  // 変更ファイルの一覧 (changes) の選択は、触らない
  const keepPath = wasInList ? selectedEntry.dataset.path : null;
  list.innerHTML = sideRootHtml() + (root.length ? sideRowsHtml("", 1) : '<p class="hint">' + esc(t("empty_directory")) + '</p>');
  if (wasInList) selectedEntry = null;  // 作り直す前の行は、もう無い
  afterSideRender(keepPath);
  scroller.scrollTop = top;
}
function patchSideDir(path) {  // 1 つのフォルダの子の行だけを入れ替える (ほかの行の要素は、そのまま。ドラッグ中の展開で、ドラッグ元を壊さないため)
  const row = sideRow(path);
  if (!row) return;
  const depth = +row.dataset.depth;
  while (row.nextElementSibling && +row.nextElementSibling.dataset.depth > depth) row.nextElementSibling.remove();
  const open = sideExpanded.has(path);
  row.querySelector(".twisty").textContent = open ? "▾" : "▸";
  if (open) {
    const rows = sideData.has(path) ? (sideData.get(path).length ? sideRowsHtml(path, depth + 1) : sideNoteHtml(depth + 1, t("empty_directory"))) : sideNoteHtml(depth + 1, t("loading"));
    row.insertAdjacentHTML("afterend", rows);
  }
  bindSelectionChecks(list);
  syncTreeCheckBoxes();
  pruneSideMulti();
  markSideTree();
  updateSelBar();
}

// 展開しているディレクトリを全部読み直して、作り直す (変更の印・チェックの状態を、いまの作業ツリーに合わせる)
function refreshSideTree() {
  const seq = ++sideSeq;
  return Promise.all(sideVisibleDirs().map(d =>
    api("/api/tree", {hash: hash, path: d}).then(r => [d, r.entries, null], e => [d, null, e]))).then(results => {
    if (seq !== sideSeq) return;  // もっと新しい読み直しが始まっている
    results.forEach(([d, entries, err]) => {
      if (entries) sideData.set(d, entries);
      else if (d) { sideExpanded.delete(d); sideData.delete(d); }  // 消えた / 読めないフォルダは、たたむ
    });
    const rootErr = results[0][2];
    if (rootErr) { showError(rootErr); return; }
    renderSideTree();
  });
}
function loadSideDir(path) {  // 展開した 1 つのフォルダを読む (読み直しも兼ねる)
  const had = sideData.has(path), before = had ? JSON.stringify(sideData.get(path)) : "";
  return api("/api/tree", {hash: hash, path: path}).then(r => {
    if (!sideExpanded.has(path)) return;  // もうたたまれた
    sideData.set(path, r.entries);
    if (!had || JSON.stringify(r.entries) !== before) patchSideDir(path);
  }, e => {
    sideExpanded.delete(path); sideData.delete(path); patchSideDir(path);
    setStatus(t("error") + e.message, "error", true);
  });
}
function toggleSideDir(path) {  // フォルダの展開 / 折りたたみ。右ペインは動かさない
  if (!path) return Promise.resolve();
  if (sideExpanded.has(path)) { sideExpanded.delete(path); patchSideDir(path); return Promise.resolve(); }
  sideExpanded.add(path);
  patchSideDir(path);  // 読み込み済みなら、すぐ出す (あとで読み直す)。まだなら「読み込み中」
  return loadSideDir(path);
}

// ---- クリック: フォルダは展開 / 折りたたみ、ファイルは右ペインに開く。アイコン: 右ペインで開く。Ctrl / ⌘ + クリック: 選択に加える / 外す。Shift + クリック: 範囲を選ぶ ----
list.addEventListener("click", ev => {
  const row = ev.target.closest(".entry");
  if (!row || ev.target.closest("input.ck")) return;
  const path = row.dataset.path, type = row.dataset.type;
  if (ev.target.closest("button.open-right")) {
    ev.stopPropagation();
    selectEntry(row);
    openDir(path);
    return;
  }
  const isRoot = row.classList.contains("root");
  if (!isRoot && moveEnabled() && (ev.ctrlKey || ev.metaKey)) {  // 選択に加える / 外す (最初は、いま選んでいる行も含める)
    if (!sideMulti.size && selectedEntry && selectedEntry.isConnected && list.contains(selectedEntry) && !selectedEntry.classList.contains("root")) sideMulti.add(selectedEntry.dataset.path);
    if (sideMulti.has(path)) sideMulti.delete(path); else sideMulti.add(path);
    selectEntry(row, true);
    applySideMulti();
    return;
  }
  if (!isRoot && moveEnabled() && ev.shiftKey && selectedEntry && selectedEntry.isConnected && list.contains(selectedEntry)) {  // 範囲
    const rows = [...list.querySelectorAll(".entry:not(.root)")], a = rows.indexOf(selectedEntry), b = rows.indexOf(row);
    if (a >= 0 && b >= 0) {
      sideMulti = new Set(rows.slice(Math.min(a, b), Math.max(a, b) + 1).map(e => e.dataset.path));
      applySideMulti();
      return;
    }
  }
  if (sideMulti.size) { sideMulti.clear(); applySideMulti(); }
  selectEntry(row);
  if (isRoot) { openDir(""); return; }
  if (type === "tree") toggleSideDir(path);
  else openEntry(path, type);
});
document.addEventListener("keydown", ev => {  // 左ペインのどこにフォーカスがあっても、Esc で選択を解く
  if (ev.key === "Escape" && sideMulti.size && ev.target.closest && ev.target.closest("#tree")) { sideMulti.clear(); applySideMulti(); }
});
