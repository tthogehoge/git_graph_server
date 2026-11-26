// 「表」ボタン (06_edit.js) から開く、表の直接編集 (オーバーレイ)。カーソルのある表の上に
// contenteditable な HTML の表を浮かせて、セルの中をブラウザーと同じ操作で編集できるようにする。
// 上のボタン (行・列の追加 / 削除) と、ソース側と同じキー (Alt+Enter で行追加、Ctrl/Cmd+Enter で
// 列追加、Alt+Backspace / Alt+Shift+Backspace で行 / 列の削除) も使える。
// 閉じるとき (Esc、外のクリック、エディタのスクロール、リサイズ) に、編集した内容を
// Markdown の表の行に変換してソースに書き戻す。表の解析と整形は src/md_table.js の純関数を使う。
import {blockOf, cellPos, formatMarkdownTable, rowLine} from "./md_table.js";

const BLANK = "\u200b";  // 空のセルの高さを保つための幅 0 の空白 (読むときに消す)

let open = null;  // 開いているオーバーレイの情報

function cellFromDom(el) {  // セルの DOM の内容 → セルのテキスト (改行は空白 1 つに、幅 0 の空白は消す)
  return (el.textContent || "").split(BLANK).join("").replace(/\u00a0/g, " ").replace(/[\r\n]+/g, " ").trim();
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
function mkCell(doc, tag, text) {  // セル (th / td) を 1 つ作る。data-r / data-c は renumber で振り直す
  const el = doc.createElement(tag);
  el.textContent = text === "" ? BLANK : text;  // 空のセルは、高さを保つために幅 0 の空白を入れておく
  Object.assign(el.style, {border: "1px solid #aaa", padding: "2px 8px", minWidth: "48px",
                           outline: "none", whiteSpace: "pre-wrap", wordBreak: "break-word",
                           font: "inherit", textAlign: "left", verticalAlign: "top"});
  el.setAttribute("contenteditable", "true");  // 見出しのセルも書ける
  return el;
}
function renumber(o) {  // セルの data-r (0 = 見出し、1.. = データの行) と data-c を、DOM の並びから振り直す
  [...o.root.querySelectorAll("thead tr, tbody tr")].forEach((tr, r) =>
    [...tr.children].forEach((el, c) => { el.dataset.r = String(r); el.dataset.c = String(c); }));
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
  focusCell(doc, el, false);
}
function cellOf(o, row, col) {  // (data-r, data-c) のセル。なければ null
  return [...o.root.querySelectorAll("td, th")].find(el => +el.dataset.r === row && +el.dataset.c === col) || null;
}
function moveCell(o, doc, dir) {  // オーバーレイの中の Tab / Shift+Tab。データのセルの間を動く (端では止まる)
  const cells = [...o.root.querySelectorAll("td")];
  const i = cells.findIndex(td => +td.dataset.r === o.lastCell.row && +td.dataset.c === o.lastCell.col);
  const next = cells[i + dir];
  if (next) goCell(o, doc, next);
}
function moveDown(o, doc) {  // オーバーレイの中の Enter。下のセルへ (セルの中の改行は、Markdown のセルに書けないので入れない)
  const next = cellOf(o, o.lastCell.row + 1, o.lastCell.col);
  if (next) goCell(o, doc, next);
}

// --- 行と列の追加・削除 (ボタンと、オーバーレイの中のキーから。ソースへの書き戻しは閉じるとき) ---

function addRowBelow(o, doc) {  // いまの行の下に、空の行を足す
  const tbody = o.root.querySelector("tbody");
  const trs = [...tbody.children];
  if (o.lastCell.row < 1 || o.lastCell.row > trs.length) return;
  const tr = doc.createElement("tr");
  for (let c = 0; c < o.ncol; c++) tr.appendChild(mkCell(doc, "td", ""));
  tbody.insertBefore(tr, trs[o.lastCell.row - 1] ? trs[o.lastCell.row - 1].nextSibling : null);
  renumber(o);
  goCell(o, doc, tr.children[Math.min(o.lastCell.col, o.ncol - 1)]);
}
function deleteRow(o, doc) {  // いまの行を消す (データの行が 1 つしかないときは消さない)
  const trs = [...o.root.querySelector("tbody").children];
  if (trs.length <= 1) return;
  const tr = trs[o.lastCell.row - 1];
  const next = tr.nextSibling || tr.previousSibling;
  tr.remove();
  renumber(o);
  goCell(o, doc, next.children[Math.min(o.lastCell.col, o.ncol - 1)]);
}
function addColumnRight(o, doc) {  // いまの列の右に、空の列を足す (見出しと、すべてのデータの行)
  const rows = [...o.root.querySelectorAll("thead tr, tbody tr")];
  o.ncol += 1;
  o.aligns.splice(o.lastCell.col + 1, 0, [false, false]);  // 区切りの行 (書き戻すときに : を残す) も列に合わせる
  rows.forEach(tr => {
    const el = mkCell(doc, tr.closest("thead") ? "th" : "td", "");
    const ref = tr.children[o.lastCell.col];
    tr.insertBefore(el, ref ? ref.nextSibling : null);
  });
  renumber(o);
  goCell(o, doc, cellOf(o, o.lastCell.row, o.lastCell.col + 1));
}
function deleteColumn(o, doc) {  // いまの列を消す (列が 2 つしかないときは消さない。消すと表が壊れる)
  if (o.ncol <= 2) return;
  const col = o.lastCell.col;
  [...o.root.querySelectorAll("thead tr, tbody tr")].forEach(tr => tr.children[col].remove());
  o.ncol -= 1;
  o.aligns.splice(col, 1);
  renumber(o);
  o.lastCell.col = Math.min(col, o.ncol - 1);
  goCell(o, doc, cellOf(o, o.lastCell.row, o.lastCell.col));
}

// --- オーバーレイ本体 ---

// カーソルが表の中なら、その表のオーバーレイを出す。表の外なら何もしない (false)。
// labels はボタンの文言 ({addRow, delRow, addCol, delCol}。06_edit.js が t() で渡す)
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
                             border: "1px solid #888", boxShadow: "0 4px 16px rgba(0,0,0,.3)",
                             padding: "4px", overflow: "auto"});
  open = {view, from: block.from, to: block.to, text: block.text, indent, aligns, ncol, root, left: 0,
          lastCell: {row: 1, col: 0}};
  const bar = doc.createElement("div");  // 行と列の追加・削除のボタン
  bar.style.cssText = "display:flex; gap:4px; margin-bottom:4px;";
  const fallback = {addRow: "+ 行", delRow: "− 行", addCol: "+ 列", delCol: "− 列"};
  [["addRow", () => addRowBelow(open, doc)], ["delRow", () => deleteRow(open, doc)],
   ["addCol", () => addColumnRight(open, doc)], ["delCol", () => deleteColumn(open, doc)]].forEach(([key, run]) => {
    const b = doc.createElement("button");
    b.type = "button";
    b.textContent = labels[key] || fallback[key];
    b.style.cssText = "font:inherit; padding:1px 6px; cursor:pointer;";
    b.onclick = run;
    bar.appendChild(b);
  });
  root.appendChild(bar);

  const table = doc.createElement("table");
  table.style.borderCollapse = "collapse";
  const trH = doc.createElement("tr");
  for (let c = 0; c < ncol; c++) trH.appendChild(mkCell(doc, "th", c < rows[0].cells.length ? rows[0].cells[c].text : ""));
  const thead = doc.createElement("thead");
  thead.appendChild(trH);
  const tbody = doc.createElement("tbody");
  for (let r = 2; r < rows.length; r++) {  // 区切りの行は見出しの下の線 (HTML の表では出さない)
    const tr = doc.createElement("tr");
    for (let c = 0; c < ncol; c++) tr.appendChild(mkCell(doc, "td", c < rows[r].cells.length ? rows[r].cells[c].text : ""));
    tbody.appendChild(tr);
  }
  table.appendChild(thead);
  table.appendChild(tbody);
  root.appendChild(table);
  renumber(open);

  // 置く場所は、ソースの表の上 (画面の座標。表の始まりが上に切れていたら、画面の上端に合わせる)
  const a = view.coordsAtPos(block.from) || {left: 0, right: 0, top: 0, bottom: 0};
  const b = view.coordsAtPos(block.to) || a;
  open.left = Math.max(0, Math.min(a.left, b.left) - 6);
  const top = Math.max(0, a.top - 6);
  root.style.left = open.left + "px";
  root.style.top = top + "px";
  root.style.maxHeight = Math.max(120, win.innerHeight - top - 12) + "px";
  doc.body.appendChild(root);
  fitWidth(open, win);

  // カーソルのあったセル (見出しと区切りの行は、オーバーレイでは見出しの行) を覚えて、そこへ移る
  let srcRow = 0;  // ソースの行 (ブロックの中の番号。0 = 見出し、1 = 区切り)
  for (let i = 0; i < block.lineStarts.length; i++) if (head - block.from >= block.lineStarts[i]) srcRow = i;
  const posInLine = head - block.from - block.lineStarts[srcRow];
  const srcCells = rows[srcRow].cells;
  let col = srcCells.findIndex(s => s.from <= posInLine && posInLine <= s.to);
  if (col < 0) col = srcCells.findIndex(s => s.from > posInLine);  // パイプの上なら、右のセル
  if (col < 0) col = srcCells.length - 1;
  const ovrRow = srcRow <= 1 ? 1 : srcRow - 1;  // オーバーレイの行の番号 (セルに付けた data-r。1 = 最初のデータの行)
  goCell(open, doc, cellOf(open, ovrRow, Math.min(Math.max(col, 0), ncol - 1)));

  open.onKey = ev => {
    if (ev.key === "Escape") { ev.preventDefault(); closeOverlay(); return; }
    if (ev.key === "Tab") { ev.preventDefault(); moveCell(open, doc, ev.shiftKey ? -1 : 1); return; }
    if (ev.key === "Enter" && ev.altKey) { ev.preventDefault(); addRowBelow(open, doc); return; }
    if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); addColumnRight(open, doc); return; }
    if (ev.key === "Backspace" && ev.altKey && ev.shiftKey) { ev.preventDefault(); deleteColumn(open, doc); return; }
    if (ev.key === "Backspace" && ev.altKey) { ev.preventDefault(); deleteRow(open, doc); return; }
    if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); moveDown(open, doc); }
  };
  root.addEventListener("keydown", open.onKey);
  open.track = () => {  // 編集しているセルを覚える (書き戻したあと、カーソルをそこへ戻す)
    const el = doc.activeElement;
    const cell = el && el.closest ? el.closest("td, th") : null;
    if (cell && root.contains(cell)) open.lastCell = {row: +cell.dataset.r, col: +cell.dataset.c};
  };
  root.addEventListener("keyup", open.track);
  root.addEventListener("focusin", open.track);
  root.addEventListener("click", open.track);
  open.onDown = ev => { if (!root.contains(ev.target)) closeOverlay(); };  // 外のクリックで閉じる
  doc.addEventListener("mousedown", open.onDown, true);
  open.onScroll = () => closeOverlay();  // 表の位置がずれるので、スクロールでも閉じる
  view.scrollDOM.addEventListener("scroll", open.onScroll);
  open.onResize = () => closeOverlay();
  win.addEventListener("resize", open.onResize);
  return true;
}

function fitWidth(o, win) {  // 幅が画面に入りきらない表 (長いセルのある表) は、プレビューと同じく
  const table = o.root.querySelector("table");  // 画面幅までにして、セルの中で折り返させる (細くなったら戻す)
  const cap = win.innerWidth - o.left - 12;
  if (table.offsetWidth > cap) {
    o.root.style.width = Math.max(200, cap) + "px";
    table.style.width = "100%";
    table.style.tableLayout = "fixed";  // 列の幅は均等。中身は折り返すので、はみ出さない
  } else {
    o.root.style.width = "";
    table.style.width = "";
    table.style.tableLayout = "";
  }
}

// 閉じる (必ず書き戻す)。Esc、外のクリック、エディタのスクロール、リサイズから呼ばれる
export function closeOverlay() {
  const o = open;
  if (!o) return;
  open = null;
  o.root.remove();
  o.root.removeEventListener("keydown", o.onKey);
  o.root.removeEventListener("keyup", o.track);
  o.root.removeEventListener("focusin", o.track);
  o.root.removeEventListener("click", o.track);
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
  const lines = [rowLine(cellRows[0], o.indent), rowLine(o.aligns.slice(0, o.ncol).map(sepText), o.indent)];
  for (let i = 1; i < cellRows.length; i++) lines.push(rowLine(cellRows[i], o.indent));
  const text = lines.join("\n");
  if (formatMarkdownTable(text, Infinity) == null) return;  // 表として壊れる内容になっていたら、書き戻さない
  const formatted = formatMarkdownTable(text);  // 幅の上限を超える表は、余白をそろえない素の形で戻す
  const out = formatted == null ? text : formatted;
  if (out === o.text) return;
  const place = {row: o.lastCell.row === 0 ? 0 : o.lastCell.row + 1, col: o.lastCell.col, offset: 0};
  const anchor = cellPos(out, o.from, place);  // カーソルは、編集していたセルの中へ
  view.dispatch({changes: {from: o.from, to: o.to, insert: out},
                 selection: {anchor: anchor, head: anchor}, userEvent: "input"});
}
