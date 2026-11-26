// 「表」ボタン (06_edit.js) から開く、表の直接編集 (オーバーレイ)。カーソルのある表の上に
// contenteditable な HTML の表を浮かせて、セルの中をブラウザーと同じ操作で編集できるようにする。
// 大きさは常に同じ (開いた位置や表の大きさによらない)。表をスクロールしても、上のボタンの行は見えている。
// ボタン (行・列の追加 / 削除。上 / 下、左 / 右) と、Alt+Backspace / Alt+Shift+Backspace でも
// 行・列を消せる。Ctrl/Cmd+Enter で行を下に足す。Alt+Enter はセルの中の改行 (書き戻すときに <br> にする)。
// 表計算からのタブ区切りのテキストの貼り付けも、表の行に変換して受ける。
// 閉じるとき (Esc、外のクリック、エディタのスクロール、リサイズ) に、編集した内容を
// Markdown の表の行に変換してソースに書き戻す。表の解析と整形は src/md_table.js の純関数を使う。
// 見出し (最初の行) は特別扱いしない: 上に行を足すと、その行が新しい見出しになる。見出しの行を
// 消すと、次の行が見出しになる (区切りの行「| --- |」は、オーバーレイでは出さず、書き戻すときに作る)。
import {blockOf, cellPos, formatMarkdownTable, rowLine} from "./md_table.js";

const BLANK = "\u200b";  // 空のセルの高さを保つための幅 0 の空白 (読むときに消す)

let open = null;  // 開いているオーバーレイの情報

function cellFromDom(el) {  // セルの DOM の内容 → セルのテキスト (セルの中の改行は <br> に。幅 0 の空白は消す)
  return (el.textContent || "").split(BLANK).join("").replace(/\u00a0/g, " ").replace(/\r/g, "")
    .replace(/^ +/, "").replace(/ +$/, "").replace(/\n/g, "<br>");
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
  focusCell(doc, el, false);
}
function cellOf(o, row, col) {  // (data-r, data-c) のセル。なければ null
  return [...o.root.querySelectorAll("td")].find(el => +el.dataset.r === row && +el.dataset.c === col) || null;
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

// --- 行と列の追加・削除 (ボタンと、オーバーレイの中のキーから。ソースへの書き戻しは閉じるとき) ---

function addRow(o, doc, above) {  // いまの行の上 / 下に、空の行を足す (見出しの上でもよい。その行が新しい見出しになる)
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
  const trs = [...o.root.querySelector("tbody").children];  // 消すと見出しとデータが残らない (2 行未満になる) ときは、消さない
  if (trs.length <= 2) return;
  trs[o.lastCell.row].remove();
  renumber(o);
  o.lastCell.row = Math.min(o.lastCell.row, trs.length - 2);
  goCell(o, doc, cellOf(o, o.lastCell.row, Math.min(o.lastCell.col, o.ncol - 1)));
}
function addColumn(o, doc, left) {  // いまの列の左 / 右に、空の列を足す (すべての行)
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
  open = {view, from: block.from, to: block.to, text: block.text, indent, aligns, ncol, root,
          lastCell: {row: 1, col: 0}};
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

  // 大きさは常に同じ (画面の 8 割 × 6 割)。置く場所はソースの表の近く (画面からはみ出さない範囲で)
  const a = view.coordsAtPos(block.from) || {left: 0, right: 0, top: 0, bottom: 0};
  const b = view.coordsAtPos(block.to) || a;
  const width = Math.min(win.innerWidth - 16, Math.max(360, Math.floor(win.innerWidth * 0.8)));
  const height = Math.min(win.innerHeight - 16, Math.max(200, Math.floor(win.innerHeight * 0.6)));
  root.style.width = width + "px";
  root.style.height = height + "px";
  root.style.left = Math.max(8, Math.min(Math.min(a.left, b.left) - 6, win.innerWidth - width - 8)) + "px";
  root.style.top = Math.max(8, Math.min(a.top - 6, win.innerHeight - height - 8)) + "px";
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
    if (ev.key === "Tab") { ev.preventDefault(); moveCell(open, doc, ev.shiftKey ? -1 : 1); return; }
    if (ev.key === "Enter" && ev.altKey) { ev.preventDefault(); insertBreak(doc); return; }  // セルの中の改行
    if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); addRow(open, doc, false); return; }
    if (ev.key === "Backspace" && ev.altKey && ev.shiftKey) { ev.preventDefault(); deleteColumn(open, doc); return; }
    if (ev.key === "Backspace" && ev.altKey) { ev.preventDefault(); deleteRow(open, doc); return; }
    if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); moveDown(open, doc); }
  };
  root.addEventListener("keydown", open.onKey);
  open.track = () => {  // 編集しているセルを覚える (書き戻したあと、カーソルをそこへ戻す)
    const el = doc.activeElement;
    const cell = el && el.closest ? el.closest("td") : null;
    if (cell && root.contains(cell)) open.lastCell = {row: +cell.dataset.r, col: +cell.dataset.c};
  };
  root.addEventListener("keyup", open.track);
  root.addEventListener("focusin", open.track);
  root.addEventListener("click", open.track);
  open.onPaste = ev => {  // 表計算からのタブ区切りのテキスト。いまの行の下に、表の行として足す
    const cd = ev.clipboardData;
    if (!cd || typeof cd.getData !== "function") return;
    const text = cd.getData("text/plain");
    if (!text || text.indexOf("\t") < 0) return;  // タブのない普通のテキストは、ブラウザーの貼り付けに任せる
    ev.preventDefault();
    ev.stopPropagation();
    const pasted = text.replace(/\r\n?/g, "\n").replace(/\n+$/, "").split("\n").map(l => l.split("\t"));
    const tbody = open.root.querySelector("tbody");
    const trs = [...tbody.children];
    const row = Math.max(0, open.lastCell.row), col = open.lastCell.col;
    const width = Math.max(open.ncol, ...pasted.map(r => r.length));
    if (width > open.ncol) {  // 貼った行に列が多いときは、全部の行をその列の数まで広げる (見出しも)
      [...open.root.querySelectorAll("tr")].forEach(tr => { while (tr.children.length < width) tr.appendChild(mkCell(doc, "")); });
      for (let c = open.ncol; c < width; c++) open.aligns.push([false, false]);
      open.ncol = width;
    }
    let ref = trs[row] ? trs[row].nextSibling : null;  // いまの行の下へ、順番に足す (基準点は固定)
    pasted.forEach(cells => {
      const tr = doc.createElement("tr");
      for (let c = 0; c < open.ncol; c++) tr.appendChild(mkCell(doc, c < cells.length ? cells[c].replace(/\|/g, "\\|") : ""));
      tbody.insertBefore(tr, ref);
    });
    renumber(open);
    goCell(open, doc, cellOf(open, row + 1, Math.min(col, open.ncol - 1)));
  };
  root.addEventListener("paste", open.onPaste);
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
  o.root.removeEventListener("keyup", o.track);
  o.root.removeEventListener("focusin", o.track);
  o.root.removeEventListener("click", o.track);
  o.root.removeEventListener("paste", o.onPaste);
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
