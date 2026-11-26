// 「表」ボタン (06_edit.js) から開く、表の直接編集 (オーバーレイ)。カーソルのある表の上に
// contenteditable な HTML の表を浮かせて、セルの中をブラウザーと同じ操作で編集できるようにする。
// 大きさは常に同じ (開いた位置や表の大きさによらず、画面の真ん中に固定で出る)。表をスクロールしても、
// 上のボタンの行は見えている。Ctrl+Z / Ctrl+Y (Shift+Ctrl+Z) で、セルの入力と行・列の追加・削除・
// 貼り付けを元に戻す / やり直せる。
// ボタン (行・列の追加 / 削除。上 / 下、左 / 右) と、Alt+Backspace / Alt+Shift+Backspace でも
// 行・列を消せる。Ctrl/Cmd+Enter で行を下に足す。Alt+Enter はセルの中の改行 (書き戻すときに <br> にする)。
// 矢印キーは、セルの中ではふうに動き、セルの端では隣のセルへ動く。上下は、折り返しで複数行の
// セルでは、折り返しの行で判断する (見た目の行の位置を、矩形から取る。取れないときはセルの中の改行で代用)。
// 表計算からのタブ区切りのテキストの貼り付けは、カーソルのセルから、貼った内容で上書きする
// (行・列が足りなければ、足してから上書きする)。複数のセルにまたがる選択は、タブ区切りのテキストとして
// コピーできる (表計算に貼り付けられる)。
// 閉じるとき (Esc、外のクリック、エディタのスクロール、リサイズ) に、編集した内容を
// Markdown の表の行に変換してソースに書き戻す。表の解析と整形は src/md_table.js の純関数を使う。
// 見出し (最初の行) は特別扱いしない: 上に行を足すと、その行が新しい見出しになる。見出しの行を
// 消すと、次の行が見出しになる (区切りの行「| --- |」は、オーバーレイでは出さず、書き戻すときに作る)。
import {blockOf, cellPos, formatMarkdownTable, rowLine} from "./md_table.js";

const BLANK = "\u200b";  // 空のセルの高さを保つための幅 0 の空白 (読むときに消す)
const SELECT_BG = "#cfe3ff";  // セルを選んでいるときの背景 (文字列の選択と区別がつくように)

let open = null;  // 開いているオーバーレイの情報

function cellFromDom(el) {  // セルの DOM の内容 → セルのテキスト (セルの中の改行は <br> に。幅 0 の空白は消す)
  return rawCell(el).replace(/\r/g, "").replace(/^ +/, "").replace(/ +$/, "").replace(/\n/g, "<br>");
}
function rawCell(el) {  // セルの見えているテキスト (コピー用。改行はそのまま)
  return (el.textContent || "").split(BLANK).join("").replace(/\u00a0/g, " ");
}
function escapeCell(s) {  // セルの中の生の「|」は \ で逃がす (元から \| だったものは、逃がしてから戻すので変わらない)
  return s.replace(/\\\|/g, "\u0000").replace(/\|/g, "\\|").replace(/\u0000/g, "\\|");
}
function sepText(a) {  // 区切りの行のセル (元の : の位置を残す)
  const [l, r] = a;
  if (l && r) return ":---:";
  if (l) return ":---";
  if (r) return "---:";
  return "---";
}
function mkCell(doc, text) {  // セル (td) を 1 つ作る。data-r / data-c は renumber で振り直す
  const el = doc.createElement("td");
  el.textContent = text === "" ? BLANK : text;  // 空のセルは、高さを保つために幅 0 の空白を入れておく
  Object.assign(el.style, {border: "1px solid #aaa", padding: "2px 8px", outline: "none",
                           whiteSpace: "pre-wrap", wordBreak: "break-word",
                           font: "inherit", textAlign: "left", verticalAlign: "top"});
  el.setAttribute("contenteditable", "true");
  return el;
}
function renumber(o) {  // セルの data-r (0 = 見出し) と data-c を、DOM の並びから振り直して、見出しの行の見た目も直す
  [...o.root.querySelectorAll("tr")].forEach((tr, r) => [...tr.children].forEach((el, c) => {
    el.dataset.r = String(r);
    el.dataset.c = String(c);
    if (r === 0) { el.style.background = "#f6f8fa"; el.style.fontWeight = "600"; }
    else { el.style.background = ""; el.style.fontWeight = ""; }
  }));
}
function focusCell(doc, el, atEnd) {  // セルへカーソル (キャレット) を置く
  el.focus();
  const sel = doc.defaultView.getSelection();
  if (!sel) return;
  const range = doc.createRange();
  range.selectNodeContents(el);
  range.collapse(!atEnd);
  sel.removeAllRanges();
  sel.addRange(range);
}
function goCell(o, doc, el) {  // セルを覚えて、そこへ移る
  o.lastCell = {row: +el.dataset.r, col: +el.dataset.c};
  o.selState = null;  // セルの選択は、移ったら消す
  focusCell(doc, el, false);
}
function cellOf(o, row, col) {  // (data-r, data-c) のセル。なければ null
  return [...o.root.querySelectorAll("td")].find(el => +el.dataset.r === row && +el.dataset.c === col) || null;
}
function goRC(o, doc, row, col) {  // (row, col) のセルへ移る (なければ何もしない)
  const el = cellOf(o, row, col);
  if (el) goCell(o, doc, el);
}
function cellRange(doc, td) {  // セルの中身全体の範囲 (ドラッグでの選択に使う)
  const r = doc.createRange();
  r.selectNodeContents(td);
  return r;
}
function caretAtPoint(doc, x, y) {  // (x, y) の場所のキャレット。取れない環境 (テストなど) では null
  if (typeof doc.caretRangeFromPoint === "function") return doc.caretRangeFromPoint(x, y);
  if (typeof doc.caretPositionFromPoint === "function") {
    const p = doc.caretPositionFromPoint(x, y);
    if (!p) return null;
    const r = doc.createRange();
    r.setStart(p.offsetNode, p.offset);
    return r;
  }
  return null;
}
function setSel(doc, r) {  // 選択を、範囲 r にする
  const sel = doc.defaultView.getSelection();
  if (!sel) return;
  sel.removeAllRanges();
  sel.addRange(r);
}
function selectedCells(o, doc) {  // 選択範囲にかかっているセル (行優先の順)。つぶれていない範囲がなければ空
  const sel = doc.defaultView.getSelection();
  if (!sel || !sel.rangeCount) return [];
  const r = sel.getRangeAt(0);
  if (r.collapsed) return [];
  // Range の定数 (START_TO_END = 1, END_TO_START = 3)。START_TO_END はこちらの終わりと相手の始まり、
  // END_TO_START はこちらの始まりと相手の終わりを比べる。環境によらないように、数で書く
  return [...o.root.querySelectorAll("td")].filter(td => {
    const cr = doc.createRange();
    cr.selectNodeContents(td);
    return r.compareBoundaryPoints(1, cr) > 0 && r.compareBoundaryPoints(3, cr) < 0;  // 範囲と重なるセル
  });
}
function paintSelected(o, doc) {  // セルの選択 (selState。ドラッグで作る) があれば、その範囲のセルの背景を変える
  renumber(o);  // いったん、見出しかどうかのふつうの色に戻してから
  const s = o.selState;
  if (!s) return;
  for (let r = s.r0; r <= s.r1; r++) for (let c = s.c0; c <= s.c1; c++) {
    const td = cellOf(o, r, c);
    if (td) td.style.background = SELECT_BG;  // 文字列の選択と区別がつくように
  }
}
function moveCell(o, doc, dir) {  // オーバーレイの中の Tab / Shift+Tab。セルの間を動く (端では止まる)
  const cells = [...o.root.querySelectorAll("td")];
  const i = cells.findIndex(td => +td.dataset.r === o.lastCell.row && +td.dataset.c === o.lastCell.col);
  const next = cells[i + dir];
  if (next) goCell(o, doc, next);
}
function moveDown(o, doc) {  // オーバーレイの中の Enter。下のセルへ
  const next = cellOf(o, o.lastCell.row + 1, o.lastCell.col);
  if (next) goCell(o, doc, next);
}
function insertBreak(doc) {  // Alt+Enter。セルの中で改行する (書き戻すときに <br> になる)
  const sel = doc.defaultView.getSelection();
  if (!sel || !sel.rangeCount) return;
  const r = sel.getRangeAt(0);
  r.deleteContents();
  r.insertNode(doc.createTextNode("\n"));
  r.collapse(false);
  sel.removeAllRanges();
  sel.addRange(r);
}
function currentTd(o, doc) {  // カーソルのあるセル (フォーカスがセルになければ null)
  const el = doc.activeElement;
  return el && el.closest && o.root.contains(el) ? el.closest("td") : null;
}
function sideText(doc, cell, before) {  // セルの中の、キャレットより前 / 後ろのテキスト
  const sel = doc.defaultView.getSelection();
  if (!sel || !sel.rangeCount) return null;
  const r = sel.getRangeAt(0);
  if (!r.collapsed) return null;  // 選んでいるときは動かさない (選択を伸ばすのはブラウザーに任せる)
  const probe = doc.createRange();
  probe.selectNodeContents(cell);
  if (before) probe.setEnd(r.startContainer, r.startOffset);
  else probe.setStart(r.endContainer, r.endOffset);
  return probe.toString();
}
function visualLines(r) {  // 範囲が、見た目で何行に渡るか (折り返しの行も数える)。レイアウトが取れないときは null
  const rects = r.getClientRects ? r.getClientRects() : [];
  if (!rects.length) return r.collapsed ? 1 : null;  // 範囲がつぶれているときは 1 行。矩形が取れないときは分からない
  const tops = [];
  for (const rect of rects) {
    if (rect.width === 0 && rect.height === 0) continue;  // 幅も高さもない断片は、数えない
    if (!tops.some(t => Math.abs(t - rect.top) <= 1)) tops.push(rect.top);  // 同じ行の断片 (折り返しの割れる場所) は 1 行
  }
  return Math.max(1, tops.length);
}
function atVerticalEdge(doc, cell, up) {  // カーソルが、セルの一番上 / 下の「見た目の行」にあるか。
  const sel = doc.defaultView.getSelection();  // 折り返しで複数行のセルは、折り返しの行数で判断する
  if (!sel || !sel.rangeCount) return true;
  const r = sel.getRangeAt(0);
  if (!r.collapsed) return false;
  const probe = doc.createRange();
  probe.selectNodeContents(cell);
  if (up) probe.setEnd(r.startContainer, r.startOffset);
  else probe.setStart(r.endContainer, r.endOffset);
  const n = visualLines(probe);
  if (n != null) return n <= 1;  // カーソルより前 / 後ろが 1 行に収まっているなら、端
  return !(sideText(doc, cell, up) || "").includes("\n");  // 見た目が取れない環境 (テストなど) は、セルの中の改行で代用
}

// --- やり直し (Ctrl+Z / Ctrl+Y。セルの入力と、行・列の追加・削除・貼り付けを戻せる) ---

function serialize(o) {  // 表のいまの状態 (セルの中身・列の数・区切りの行の揃え)
  return {cells: [...o.root.querySelectorAll("tr")].map(tr => [...tr.children].map(el => el.textContent)),
          ncol: o.ncol, aligns: o.aligns.map(a => [a[0], a[1]])};
}
function eqState(a, b) { return JSON.stringify(a) === JSON.stringify(b); }
function snapshot(o) {  // いまの状態を undo の積み物に置く (変わっていなければ置かない)。redo の積み物は空にする
  const cur = serialize(o);
  if (o.undo.length && eqState(o.undo[o.undo.length - 1], cur)) return;
  o.undo.push(cur);
  o.redo = [];
}
function restore(o, doc, state) {  // 記録しておいた状態の表を作り直す (カーソルは、なるべく同じセルへ)
  o.ncol = state.ncol;
  o.aligns = state.aligns.map(a => [a[0], a[1]]);
  o.restoring = true;  // 作り直しの間は、履歴を積まない (focusin の記録を止める)
  const tbody = o.root.querySelector("tbody");
  tbody.replaceChildren();
  state.cells.forEach(cells => {
    const tr = doc.createElement("tr");
    for (let c = 0; c < o.ncol; c++) tr.appendChild(mkCell(doc, cells[c] || ""));
    tbody.appendChild(tr);
  });
  renumber(o);
  o.lastCell = {row: Math.min(o.lastCell.row, state.cells.length - 1), col: Math.min(o.lastCell.col, o.ncol - 1)};
  goCell(o, doc, cellOf(o, o.lastCell.row, o.lastCell.col));
  o.restoring = false;
}
function applyHistory(o, doc, from, to) {  // from の積み物から 1 つ戻して作り直す (undo は undo→redo、redo は redo→undo)
  const cur = serialize(o);
  if (from.length && eqState(from[from.length - 1], cur)) from.pop();  // いまの状態が積んであれば捨てて、その前から戻す
  const state = from.pop();
  if (!state) return;
  to.push(cur);
  restore(o, doc, state);
}

// --- 行と列の追加・削除 (ボタンと、オーバーレイの中のキーから。ソースへの書き戻しは閉じるとき) ---

function addRow(o, doc, above) {  // いまの行の上 / 下に、空の行を足す (見出しの上でもよい。その行が新しい見出しになる)
  snapshot(o);  // 元に戻せるように、変える前の状態を覚える
  const tbody = o.root.querySelector("tbody");
  const trs = [...tbody.children];
  const at = Math.max(0, o.lastCell.row + (above ? 0 : 1));  // 挿入位置 (tbody の中の番号)
  if (at > trs.length) return;
  const tr = doc.createElement("tr");
  for (let c = 0; c < o.ncol; c++) tr.appendChild(mkCell(doc, ""));
  tbody.insertBefore(tr, trs[at] || null);
  renumber(o);
  goCell(o, doc, tr.children[Math.min(o.lastCell.col, o.ncol - 1)]);
}
function deleteRow(o, doc) {  // いまの行を消す (見出しの行を消すと、次の行が見出しに繰り上がる)。
  snapshot(o);  // 元に戻せるように、変える前の状態を覚える (消せないときは、状態が変わらないので積まれない)
  const trs = [...o.root.querySelector("tbody").children];  // 消すと見出しとデータが残らない (2 行未満になる) ときは、消さない
  if (trs.length <= 2) return;
  trs[o.lastCell.row].remove();
  renumber(o);
  o.lastCell.row = Math.min(o.lastCell.row, trs.length - 2);
  goCell(o, doc, cellOf(o, o.lastCell.row, Math.min(o.lastCell.col, o.ncol - 1)));
}
function addColumn(o, doc, left) {  // いまの列の左 / 右に、空の列を足す (すべての行)
  snapshot(o);  // 元に戻せるように、変える前の状態を覚える
  const rows = [...o.root.querySelectorAll("tr")];
  const at = left ? o.lastCell.col : o.lastCell.col + 1;
  o.ncol += 1;
  o.aligns.splice(at, 0, [false, false]);  // 区切りの行 (書き戻すときに : を残す) も列に合わせる
  rows.forEach(tr => {
    const el = mkCell(doc, "");
    tr.insertBefore(el, tr.children[at] || null);
  });
  renumber(o);
  goCell(o, doc, cellOf(o, o.lastCell.row, at));
}
function deleteColumn(o, doc) {  // いまの列を消す (列が 2 つしかないときは消さない。消すと表が壊れる)
  snapshot(o);  // 元に戻せるように、変える前の状態を覚える (消せないときは、状態が変わらないので積まれない)
  if (o.ncol <= 2) return;
  const col = o.lastCell.col;
  [...o.root.querySelectorAll("tr")].forEach(tr => tr.children[col].remove());
  o.ncol -= 1;
  o.aligns.splice(col, 1);
  renumber(o);
  o.lastCell.col = Math.min(col, o.ncol - 1);
  goCell(o, doc, cellOf(o, o.lastCell.row, o.lastCell.col));
}

// --- オーバーレイ本体 ---

// カーソルが表の中なら、その表のオーバーレイを出す。表の外なら何もしない (false)。
// labels はボタンの文言 ({addRowAbove, addRow, delRow, addColLeft, addCol, delCol}。06_edit.js が t() で渡す)
export function mdTableOverlay(view, labels = {}) {
  const head = view.state.selection.main.head;
  const block = blockOf(view.state.doc, head);
  if (!block) return false;
  if (open) closeOverlay();  // ほかの表のオーバーレイが開いていたら、書き戻してから開き直す
  const doc = view.dom.ownerDocument, win = doc.defaultView;
  const rows = block.rows, ncol = rows[0].cells.length, indent = rows[0].indent;
  const aligns = rows[1].cells.map(c => {  // 区切りの行の : の位置 (書き戻すときに残す)
    const m = /^(:?)(-+)(:?)$/.exec(c.text);
    return m ? [m[1] !== "", m[3] !== ""] : [false, false];
  });
  while (aligns.length < ncol) aligns.push([false, false]);

  const root = doc.createElement("div");
  root.className = "gg-md-table-overlay";
  Object.assign(root.style, {position: "fixed", zIndex: "1000", background: "#fff", color: "#222",
                             border: "1px solid #888", boxShadow: "0 4px 16px rgba(0,0,0,.3)", padding: "4px",
                             display: "flex", flexDirection: "column", boxSizing: "border-box"});
  open = {view, doc, from: block.from, to: block.to, text: block.text, indent, aligns, ncol, root,
          lastCell: {row: 1, col: 0}, selState: null, undo: [], redo: [], restoring: false};
  const bar = doc.createElement("div");  // 行と列の追加・削除のボタン (表をスクロールしても見えている)
  bar.style.cssText = "display:flex; gap:4px; margin-bottom:4px; flex:none;";
  const fallback = {addRowAbove: "↑ 行", addRow: "↓ 行", delRow: "− 行", addColLeft: "← 列", addCol: "→ 列", delCol: "− 列"};
  [["addRowAbove", () => addRow(open, doc, true)], ["addRow", () => addRow(open, doc, false)], ["delRow", () => deleteRow(open, doc)],
   ["addColLeft", () => addColumn(open, doc, true)], ["addCol", () => addColumn(open, doc, false)],
   ["delCol", () => deleteColumn(open, doc)]].forEach(([key, run]) => {
    const b = doc.createElement("button");
    b.type = "button";
    b.textContent = labels[key] || fallback[key];
    b.style.cssText = "font:inherit; padding:1px 6px; cursor:pointer;";
    b.onclick = run;
    bar.appendChild(b);
  });
  const table = doc.createElement("table");
  table.style.cssText = "border-collapse:collapse; width:100%; table-layout:fixed;";  // 大きさは常に同じなので、列は画面 (オーバーレイの幅) に合わせる。長いセルは中で折り返す
  const tbody = doc.createElement("tbody");
  rows.forEach((r, i) => {  // 見出し (区切りの行は出さない) も、ふつうの行と同じ tbody に入れる (上にも行を足せる)
    if (i === 1) return;  // 区切りの行 (「| --- |」) は、書き戻すときに aligns から作る
    const tr = doc.createElement("tr");
    for (let c = 0; c < ncol; c++) tr.appendChild(mkCell(doc, c < r.cells.length ? r.cells[c].text.replace(/<br\s*\/?\s*>/gi, "\n") : ""));
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  const scroller = doc.createElement("div");  // 表はここでスクロールする (ボタンの行はスクロールしても見えている)
  scroller.style.cssText = "overflow:auto; flex:1 1 auto; min-height:0;";
  scroller.appendChild(table);
  root.appendChild(bar);
  root.appendChild(scroller);
  renumber(open);
  snapshot(open);  // 開いたときの状態を覚えておく (最初の入力も Ctrl+Z で戻せるように)

  // 大きさは常に同じ (画面の 8 割 × 6 割)。置く場所は画面の真ん中で固定 (開いた位置によらず、あっちこっちに動かない)
  const width = Math.min(win.innerWidth - 16, Math.max(360, Math.floor(win.innerWidth * 0.8)));
  const height = Math.min(win.innerHeight - 16, Math.max(200, Math.floor(win.innerHeight * 0.6)));
  root.style.width = width + "px";
  root.style.height = height + "px";
  root.style.left = Math.max(8, Math.floor((win.innerWidth - width) / 2)) + "px";
  root.style.top = Math.max(8, Math.floor((win.innerHeight - height) / 2)) + "px";
  doc.body.appendChild(root);

  // カーソルのあったセル (見出しと区切りの行は、オーバーレイでは見出しの行) を覚えて、そこへ移る
  let srcRow = 0;  // ソースの行 (ブロックの中の番号。0 = 見出し、1 = 区切り)
  for (let i = 0; i < block.lineStarts.length; i++) if (head - block.from >= block.lineStarts[i]) srcRow = i;
  const posInLine = head - block.from - block.lineStarts[srcRow];
  const srcCells = rows[srcRow].cells;
  let col = srcCells.findIndex(s => s.from <= posInLine && posInLine <= s.to);
  if (col < 0) col = srcCells.findIndex(s => s.from > posInLine);  // パイプの上なら、右のセル
  if (col < 0) col = srcCells.length - 1;
  const ovrRow = srcRow <= 1 ? 0 : srcRow - 1;  // オーバーレイの行の番号 (tbody の中。0 = 見出し)
  goCell(open, doc, cellOf(open, ovrRow, Math.min(Math.max(col, 0), ncol - 1)));

  open.onKey = ev => {
    if (ev.key === "Escape") { ev.preventDefault(); closeOverlay(); return; }
    if ((ev.ctrlKey || ev.metaKey) && !ev.altKey && (ev.key === "z" || ev.key === "Z")) {  // やり直し
      ev.preventDefault();
      applyHistory(open, doc, ev.shiftKey ? open.redo : open.undo, ev.shiftKey ? open.undo : open.redo);
      return;
    }
    if ((ev.ctrlKey || ev.metaKey) && !ev.altKey && !ev.shiftKey && (ev.key === "y" || ev.key === "Y")) {
      ev.preventDefault();
      applyHistory(open, doc, open.redo, open.undo);
      return;
    }
    if (ev.key === "Tab") { ev.preventDefault(); moveCell(open, doc, ev.shiftKey ? -1 : 1); return; }
    if (ev.key === "Enter" && ev.altKey) { ev.preventDefault(); insertBreak(doc); return; }  // セルの中の改行
    if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); addRow(open, doc, false); return; }
    if (ev.key === "Backspace" && ev.altKey && ev.shiftKey) { ev.preventDefault(); deleteColumn(open, doc); return; }
    if (ev.key === "Backspace" && ev.altKey) { ev.preventDefault(); deleteRow(open, doc); return; }
    // 矢印キー。セルの端では、隣のセルへ動く (セルの中では、ふつうに動かす)。Shift は選択なので働かない
    if (ev.key.indexOf("Arrow") === 0) open.selState = null;  // 矢印で動いたら、セルの選択は消す
    const td = currentTd(open, doc);
    if (td && !ev.shiftKey && !ev.altKey && !ev.ctrlKey && !ev.metaKey) {
      if (ev.key === "ArrowLeft" && open.lastCell.col > 0 && !(sideText(doc, td, true) || "").length) {
        ev.preventDefault();
        goRC(open, doc, open.lastCell.row, open.lastCell.col - 1);
        return;
      }
      if (ev.key === "ArrowRight" && open.lastCell.col < open.ncol - 1 && !(sideText(doc, td, false) || "").length) {
        ev.preventDefault();
        goRC(open, doc, open.lastCell.row, open.lastCell.col + 1);
        return;
      }
      if (ev.key === "ArrowUp" && atVerticalEdge(doc, td, true)) {
        const next = cellOf(open, open.lastCell.row - 1, open.lastCell.col);
        if (next) { ev.preventDefault(); goCell(open, doc, next); }
        return;
      }
      if (ev.key === "ArrowDown" && atVerticalEdge(doc, td, false)) {
        const next = cellOf(open, open.lastCell.row + 1, open.lastCell.col);
        if (next) { ev.preventDefault(); goCell(open, doc, next); }
        return;
      }
    }
    if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); moveDown(open, doc); }
  };
  root.addEventListener("keydown", open.onKey);
  open.track = () => {  // セルの位置と、打った内容 (やり直し用) を覚える。打鍵ではなく input で取る (undo のキー自体を履歴に入れない)
    if (!open.restoring) snapshot(open);
    const el = doc.activeElement;
    const cell = el && el.closest ? el.closest("td") : null;
    if (cell && root.contains(cell)) open.lastCell = {row: +cell.dataset.r, col: +cell.dataset.c};
    open.selState = null;  // セルを選んでいるときに入力したら、選択は消す
  };
  open.retrack = () => {  // セルの位置だけ覚え直す
    const el = doc.activeElement;
    const cell = el && el.closest ? el.closest("td") : null;
    if (cell && root.contains(cell)) open.lastCell = {row: +cell.dataset.r, col: +cell.dataset.c};
  };
  root.addEventListener("focusin", open.track);
  root.addEventListener("input", open.track);
  root.addEventListener("keyup", open.retrack);
  root.addEventListener("click", open.retrack);
  open.onPaste = ev => {  // 表計算からのタブ区切りのテキスト。カーソルのセルから、貼った内容で上書きする
    const cd = ev.clipboardData;
    if (!cd || typeof cd.getData !== "function") return;
    const text = cd.getData("text/plain");
    if (!text || text.indexOf("\t") < 0) return;  // タブのない普通のテキストは、ブラウザーの貼り付けに任せる
    ev.preventDefault();
    ev.stopPropagation();
    snapshot(open);  // 元に戻せるように、変える前の状態を覚える
    const pasted = text.replace(/\r\n?/g, "\n").replace(/\n+$/, "").split("\n").map(l => l.split("\t"));
    const r0 = Math.max(0, open.lastCell.row), c0 = Math.max(0, open.lastCell.col);
    const needR = r0 + pasted.length, needC = c0 + Math.max(...pasted.map(row => row.length));
    const tbody = open.root.querySelector("tbody");
    while (tbody.children.length < needR) {  // 行が足りなければ、末尾に足す
      const tr = doc.createElement("tr");
      for (let c = 0; c < open.ncol; c++) tr.appendChild(mkCell(doc, ""));
      tbody.appendChild(tr);
    }
    if (needC > open.ncol) {  // 列が足りなければ、全部の行 (見出しも) をその列の数まで広げる (区切りの行の揃えも)
      [...open.root.querySelectorAll("tr")].forEach(tr => { while (tr.children.length < needC) tr.appendChild(mkCell(doc, "")); });
      for (let c = open.ncol; c < needC; c++) open.aligns.push([false, false]);
      open.ncol = needC;
    }
    renumber(open);  // 足したセルにも data-r / data-c を振ってから、上書きする
    pasted.forEach((cells, i) => {
      for (let c = 0; c < cells.length; c++) {
        const td = cellOf(open, r0 + i, c0 + c);
        if (td) td.textContent = cells[c] === "" ? BLANK : cells[c].replace(/\|/g, "\\|");
      }
    });
    renumber(open);
    goCell(open, doc, cellOf(open, r0, c0));  // カーソルは、貼り始めたセルへ
  };
  open.onCopy = ev => {  // セルの選択 (ドラッグ) か、複数のセルにまたがる文字列の選択なら、タブ区切りのテキストとしてコピーする
    const s = open.selState;
    let block = null;  // [r0, r1, c0, c1]
    if (s) block = [s.r0, s.r1, s.c0, s.c1];
    else {
      const cells = selectedCells(open, doc);
      if (cells.length < 2) return;  // 1 つのセルの中の選択は、ブラウザーのコピーに任せる
      const rs = cells.map(td => +td.dataset.r), cs = cells.map(td => +td.dataset.c);
      block = [Math.min(...rs), Math.max(...rs), Math.min(...cs), Math.max(...cs)];  // 選んだ範囲を長方形として切り出す
    }
    const cd = ev.clipboardData;
    if (!cd || typeof cd.setData !== "function") return;
    ev.preventDefault();
    ev.stopPropagation();
    const lines = [];
    for (let r = block[0]; r <= block[1]; r++) {  // 選んでいないセルも、その場所のセルで埋める
      const line = [];
      for (let c = block[2]; c <= block[3]; c++) {
        const td = cellOf(open, r, c);
        line.push(td ? rawCell(td) : "");
      }
      lines.push(line.join("\t"));
    }
    cd.setData("text/plain", lines.join("\n"));
  };
  root.addEventListener("paste", open.onPaste);
  root.addEventListener("copy", open.onCopy);

  // マウスのドラッグでの選択。セルはそれぞれ contenteditable なので、ネイティブの選択は
  // ドラッグを始めたセルの中で閉じて、セルをまたげない。そこで、選択はこちらで作る
  let drag = null;  // ドラッグ中の状態 (始めたセルと、押した場所のキャレット)
  open.onMd = ev => {  // 押したら、いったん自分でキャレットを置いて、ドラッグでの選択を始める
    const td = ev.target && ev.target.closest ? ev.target.closest("td") : null;
    if (!td || !root.contains(td) || ev.button !== 0) return;
    ev.preventDefault();  // ネイティブのキャレット移動・選択は働かせない (自分で作る)
    drag = {td, caret: caretAtPoint(doc, ev.clientX, ev.clientY)};
    open.selState = null;  // 新しく押したので、前のセルの選択は消す
    td.focus();
    const r = drag.caret || cellRange(doc, td);
    r.collapse(true);
    setSel(doc, r);
    paintSelected(open, doc);  // キャレットを置くので、セルの選択の色は消える
    open.lastCell = {row: +td.dataset.r, col: +td.dataset.c};
  };
  root.addEventListener("mousedown", open.onMd);
  open.onMm = ev => {  // 動かした先で、選択を作り直す
    if (!drag) return;
    const td = ev.target && ev.target.closest ? ev.target.closest("td") : null;
    if (!td || !root.contains(td)) return;
    if (td === drag.td) {  // 同じセルの中なら、セルの中の選択 (ネイティブと同じように)
      open.selState = null;
      const c2 = caretAtPoint(doc, ev.clientX, ev.clientY);
      if (!c2) return;
      const r = doc.createRange();
      const a = drag.caret || cellRange(doc, td);
      if (a.compareBoundaryPoints(0, c2) <= 0) { r.setStart(a.startContainer, a.startOffset); r.setEnd(c2.startContainer, c2.startOffset); }
      else { r.setStart(c2.startContainer, c2.startOffset); r.setEnd(a.startContainer, a.startOffset); }
      setSel(doc, r);
      paintSelected(open, doc);
    } else {  // ほかのセルへなら、セルの選択。縦・横・斜めのどちらでも選べるように、セルの番号の長方形として覚える
      // (DOM の選択は、ブラウザーが contenteditable のセルをまたげずに潰すことがあるので、使わない)
      const r0 = Math.min(+drag.td.dataset.r, +td.dataset.r), r1 = Math.max(+drag.td.dataset.r, +td.dataset.r);
      const c0 = Math.min(+drag.td.dataset.c, +td.dataset.c), c1 = Math.max(+drag.td.dataset.c, +td.dataset.c);
      open.selState = {r0, r1, c0, c1};
      paintSelected(open, doc);
    }
  };
  root.addEventListener("mousemove", open.onMm);
  open.onMu = () => { drag = null; };  // 放したら終わり (選択はそのまま残る)
  doc.addEventListener("mouseup", open.onMu);
  open.onSelChange = () => { if (open && open.root.isConnected) paintSelected(open, doc); };  // ほかの選択の変化 (ダブルクリックなど) でも、色を直す
  doc.addEventListener("selectionchange", open.onSelChange);
  open.onDown = ev => { if (!root.contains(ev.target)) closeOverlay(); };  // 外のクリックで閉じる
  doc.addEventListener("mousedown", open.onDown, true);
  open.onScroll = () => closeOverlay();  // 表の位置がずれるので、スクロールでも閉じる
  view.scrollDOM.addEventListener("scroll", open.onScroll);
  open.onResize = () => closeOverlay();
  win.addEventListener("resize", open.onResize);
  return true;
}

// 閉じる (必ず書き戻す)。Esc、外のクリック、エディタのスクロール、リサイズから呼ばれる
export function closeOverlay() {
  const o = open;
  if (!o) return;
  open = null;
  o.root.remove();
  o.root.removeEventListener("keydown", o.onKey);
  o.root.removeEventListener("focusin", o.track);
  o.root.removeEventListener("input", o.track);
  o.root.removeEventListener("keyup", o.retrack);
  o.root.removeEventListener("click", o.retrack);
  o.root.removeEventListener("paste", o.onPaste);
  o.root.removeEventListener("copy", o.onCopy);
  o.root.removeEventListener("mousedown", o.onMd);
  o.root.removeEventListener("mousemove", o.onMm);
  o.doc.removeEventListener("mouseup", o.onMu);
  o.doc.removeEventListener("selectionchange", o.onSelChange);
  const doc = o.view.dom.ownerDocument, win = doc.defaultView;
  doc.removeEventListener("mousedown", o.onDown, true);
  o.view.scrollDOM.removeEventListener("scroll", o.onScroll);
  win.removeEventListener("resize", o.onResize);
  writeBack(o);
  o.view.focus();
}

function writeBack(o) {  // オーバーレイで編集した内容を、Markdown の表の行に変換して書き戻す
  const view = o.view;
  if (view.state.sliceDoc(o.from, o.to) !== o.text) return;  // 開いている間に外から変わったときは、上書きしない
  const cellRows = [...o.root.querySelectorAll("tr")].map(tr => [...tr.children].map(el => escapeCell(cellFromDom(el))));
  const lines = [rowLine(cellRows[0], o.indent), rowLine(o.aligns.map(sepText), o.indent)];
  for (let i = 1; i < cellRows.length; i++) lines.push(rowLine(cellRows[i], o.indent));
  const text = lines.join("\n");
  if (formatMarkdownTable(text, Infinity) == null) return;  // 表として壊れる内容になっていたら、書き戻さない
  const formatted = formatMarkdownTable(text);  // 幅の上限を超える表は、余白をそろえない素の形で戻す
  const out = formatted == null ? text : formatted;
  if (out === o.text) return;
  // カーソルは、編集していたセルの中へ (ソース側の行は、見出し 0、区切り 1、データはオーバーレイの行 + 1)
  const place = {row: o.lastCell.row === 0 ? 0 : o.lastCell.row + 1, col: o.lastCell.col, offset: 0};
  const anchor = cellPos(out, o.from, place);  // カーソルは、編集していたセルの中へ
  view.dispatch({changes: {from: o.from, to: o.to, insert: out},
                 selection: {anchor: anchor, head: anchor}, userEvent: "input"});
}
