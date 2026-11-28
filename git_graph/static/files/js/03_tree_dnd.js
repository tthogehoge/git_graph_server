/* ---- files/03_tree_dnd.js ---- */
// ---- 作業ツリーのファイル / フォルダの移動: ドラッグ&ドロップ (左のツリー・右の一覧のどれからどれへも) と、選んだ行をまとめて (「移動…」) ----
// ドラッグできるのは、左のツリーの行 (Ctrl / Shift + クリックで選んだ行は、まとめて) と、右の一覧の行。
// 置けるのは、フォルダの行 (左のルートの行も) / 右ペインの上のパンくずのフォルダ / 右ペインの一覧のあき (いま開いているフォルダ)。
// 閉じたフォルダの上にしばらくいると、そのフォルダが開く。移すのは /api/file-move (追跡中なら git mv。元に戻す・やり直しの履歴に積まれる)。
const MOVE_MIME = "application/x-git-graph-move";  // 内部のドラッグの印 (OS からのファイルのドロップは、アップロード側の処理)
let dragPaths = null;  // ドラッグ中のパス (dragover の間は、データを読めないので、ここに持つ)
let springTimer = null, springPath = null;
let moving = false;

function movedPath(moved, p) {  // 動かしたあとのパス (動かしたフォルダの中のものも、一緒に変わる)
  for (const m of moved) {
    if (p === m.src) return m.dest;
    if (p.startsWith(m.src + "/")) return m.dest + p.slice(m.src.length);
  }
  return p;
}
function canDropInto(paths, dir) {  // 自分自身の中へは置けない。全部がもうそこにあるなら、置く意味がない
  if (!paths.length) return false;
  if (paths.some(p => dir === p || dir.startsWith(p + "/"))) return false;
  return paths.some(p => parentDir(p) !== dir);
}
function dropTargetOf(ev) {
  const el = ev.target;
  if (!el || !el.closest) return null;
  const side = el.closest("#list .entry");
  if (side) return side.dataset.type === "tree" ? {dir: side.dataset.path, el: side} : null;
  const crumb = el.closest("#title a[data-dir]");
  if (crumb) return {dir: crumb.dataset.dir, el: crumb};
  if (!currentFile && !dirLogOn && content.contains(el)) {  // 右ペインの一覧: フォルダの行ならそのフォルダ、それ以外は、いま開いているフォルダ
    const row = el.closest("tr.dir-log-row");
    if (row && row.dataset.type === "tree") return {dir: row.dataset.path, el: row};
    return {dir: current, el: content};
  }
  return null;
}
function clearDropHover() { document.querySelectorAll(".drop-hover").forEach(e => e.classList.remove("drop-hover")); }
function cancelSpring() {
  if (springTimer) clearTimeout(springTimer);
  springTimer = null;
  springPath = null;
}
function scheduleSpring(target) {  // 閉じた (左のツリーの) フォルダの上に 0.7 秒いたら、開く
  const sideDir = target.el.classList.contains("entry") && !target.el.classList.contains("root");
  if (!sideDir || sideExpanded.has(target.dir)) { cancelSpring(); return; }
  if (springPath === target.dir) return;
  cancelSpring();
  springPath = target.dir;
  springTimer = setTimeout(() => {
    springTimer = null;
    const p = springPath;
    springPath = null;
    if (dragPaths && p && !sideExpanded.has(p)) toggleSideDir(p);
  }, 700);
}
function endDrag() {
  dragPaths = null;
  cancelSpring();
  clearDropHover();
  document.querySelectorAll(".dragging").forEach(e => e.classList.remove("dragging"));
}
const isMoveDrag = ev => !!(dragPaths && ev.dataTransfer && [...(ev.dataTransfer.types || [])].indexOf(MOVE_MIME) >= 0);

document.addEventListener("dragstart", ev => {
  const row = ev.target.closest && ev.target.closest("#list .entry:not(.root), #content tr.dir-log-row");
  if (!row || !moveEnabled()) return;
  const path = row.dataset.path, inSide = !!row.closest("#list");
  if (inSide && sideMulti.size > 1 && sideMulti.has(path)) dragPaths = [...sideMulti];  // 選んだ行の 1 つを掴んだら、選んだ全部
  else {
    dragPaths = [path];
    if (inSide && sideMulti.size) { sideMulti.clear(); applySideMulti(); }
  }
  ev.dataTransfer.setData(MOVE_MIME, JSON.stringify(dragPaths));
  ev.dataTransfer.effectAllowed = "move";
  row.classList.add("dragging");
});
document.addEventListener("dragover", ev => {
  if (!isMoveDrag(ev)) return;
  clearDropHover();
  const target = dropTargetOf(ev);
  if (!target || !canDropInto(dragPaths, target.dir)) { cancelSpring(); return; }  // preventDefault しない = 置けない
  ev.preventDefault();
  ev.dataTransfer.dropEffect = "move";
  target.el.classList.add("drop-hover");
  scheduleSpring(target);
});
document.addEventListener("dragleave", ev => { if (!ev.relatedTarget) clearDropHover(); });  // ウインドウの外へ出た
document.addEventListener("drop", ev => {
  if (!isMoveDrag(ev)) return;
  const target = dropTargetOf(ev), paths = dragPaths;
  endDrag();
  if (!target || !canDropInto(paths, target.dir)) return;
  ev.preventDefault();
  moveInto(paths, target.dir);
});
document.addEventListener("dragend", endDrag);

// 動かしたあと: 開いているファイル / フォルダと、左のツリーの展開を、新しいパスに合わせて読み直す。revealDir: 左のツリーで見せるフォルダ
async function afterMove(moved, revealDir) {
  const nf = currentFile ? movedPath(moved, currentFile) : "";
  const nc = nf ? parentDir(nf) : movedPath(moved, current);
  const expanded = [...sideExpanded].map(p => movedPath(moved, p));
  sideExpanded.clear();
  expanded.forEach(p => sideExpanded.add(p));
  if (revealDir !== undefined) sideReveal(revealDir);
  sideMulti.clear();
  entryInfo = {};
  await loadTree(nc, false);  // 右ペインのデータと、左のツリー (展開している分) を読み直す
  if (nf && nf !== currentFile) {  // 開いていたファイルを動かした: 新しい場所で開き直し、左のツリーでも、その行を選ぶ
    await loadFile(nf, true);
    markSideTree();
    return;
  }
  if (!currentFile) showDir();
}
async function moveInto(paths, destDir) {  // paths を、フォルダ destDir ("" はルート) の中へ移す
  if (!moveEnabled() || moving || !paths.length) return;
  if (!leaveEdit()) return;
  moving = true;
  try {
    const r = await postJson("/api/file-move", {paths: paths, dest_dir: destDir});
    setStatus(r.message, "success");
    await afterMove(r.moved, destDir);
  } catch (e) {
    setStatus(e.message || String(e), "error", true);
  } finally {
    moving = false;
  }
}

// 「移動…」: 左のツリーで選んだ行 (選んでいなければ、キーボードで選んでいる行) を、フォルダを入力して、まとめて移す
function moveSelectionPaths() {
  if (sideMulti.size) return [...sideMulti];
  const row = selectedEntry && selectedEntry.isConnected && list.contains(selectedEntry) && !selectedEntry.classList.contains("root") ? selectedEntry : null;
  return row ? [row.dataset.path] : [];
}
async function promptMoveSelection() {
  if (!moveEnabled() || typeof openDialog !== "function") return;
  const paths = moveSelectionPaths();
  if (!paths.length) { setStatus(t("move_many_none"), "error", true); return; }
  if (!leaveEdit()) return;
  const values = await openDialog(t("move_many_title", {n: paths.length}), form => {
    const el = input("text", "dir");
    el.autocomplete = "off";
    el.spellcheck = false;
    el.value = current;  // 既定は、右ペインで開いているフォルダ (左で選んで、右のフォルダへ)
    field(form, t("move_many_dir_label"), el, false);
    const hint = document.createElement("p");
    hint.className = "hint";
    hint.textContent = t("move_many_hint");
    form.appendChild(hint);
  }, t("move_submit"));
  if (!values) return;
  await moveInto(paths, movePathNormalize(values.dir));
}
selMoveBtn.onclick = promptMoveSelection;
