// 「表」ボタン (06_edit.js) から開く、表の直接編集 (オーバーレイ)。カーソルのある表の上に
// contenteditable な HTML の表を浮かせて、セルの中をブラウザーと同じ操作で編集できるようにする。
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
function focusCell(doc, td, atEnd) {  // セルへカーソル (キャレット) を置く
  td.focus();
  const sel = doc.defaultView.getSelection();
  if (!sel) return;
  const range = doc.createRange();
  range.selectNodeContents(td);
  range.collapse(!atEnd);
  sel.removeAllRanges();
  sel.addRange(range);
}
function goCell(o, doc, td) {  // セルを覚えて、そこへ移る
  o.lastCell = {row: +td.dataset.r, col: +td.dataset.c};
  focusCell(doc, td, false);
}
function moveCell(o, doc, dir) {  // オーバーレイの中の Tab / Shift+Tab。セルの間を動く (端では止まる)
  const cells = [...o.root.querySelectorAll("td")];
  const i = cells.findIndex(td => td.dataset.r == o.lastCell.row && td.dataset.c == o.lastCell.col);
  const next = cells[i + dir];
  if (next) goCell(o, doc, next);
}
function moveDown(o, doc) {  // オーバーレイの中の Enter。下のセルへ (セルの中の改行は、Markdown のセルに書けないので入れない)
  const cells = [...o.root.querySelectorAll("td")];
  const i = cells.findIndex(td => td.dataset.r == o.lastCell.row && td.dataset.c == o.lastCell.col);
  const next = cells[i + o.ncol];
  if (next) goCell(o, doc, next);
}

// カーソルが表の中なら、その表のオーバーレイを出す。表の外なら何もしない (false)
export function mdTableOverlay(view) {
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

  const mk = (tag, r, c, text) => {
    const el = doc.createElement(tag);
    el.dataset.r = String(r); el.dataset.c = String(c);
    el.textContent = text === "" ? BLANK : text;  // 空のセルは、高さを保つために幅 0 の空白を入れておく
    Object.assign(el.style, {border: "1px solid #aaa", padding: "2px 8px", minWidth: "48px",
                             outline: "none", whiteSpace: "pre", font: "inherit", textAlign: "left", verticalAlign: "top"});
    if (tag === "td") el.setAttribute("contenteditable", "true");
    return el;
  };
  const root = doc.createElement("div");
  root.className = "gg-md-table-overlay";
  Object.assign(root.style, {position: "fixed", zIndex: "1000", background: "#fff", color: "#222",
                             border: "1px solid #888", boxShadow: "0 4px 16px rgba(0,0,0,.3)",
                             padding: "4px", overflow: "auto"});
  const table = doc.createElement("table");
  table.style.borderCollapse = "collapse";
  const trH = doc.createElement("tr");
  for (let c = 0; c < ncol; c++) trH.appendChild(mk("th", 0, c, c < rows[0].cells.length ? rows[0].cells[c].text : ""));
  const thead = doc.createElement("thead");
  thead.appendChild(trH);
  const tbody = doc.createElement("tbody");
  for (let r = 2; r < rows.length; r++) {  // 区切りの行は見出しの下の線 (HTML の表では出さない)
    const tr = doc.createElement("tr");
    for (let c = 0; c < ncol; c++) tr.appendChild(mk("td", r - 1, c, c < rows[r].cells.length ? rows[r].cells[c].text : ""));
    tbody.appendChild(tr);
  }
  table.appendChild(thead);
  table.appendChild(tbody);
  root.appendChild(table);

  // 置く場所は、ソースの表の上 (画面の座標。表の始まりが上に切れていたら、画面の上端に合わせる)
  const a = view.coordsAtPos(block.from) || {left: 0, right: 0, top: 0, bottom: 0};
  const b = view.coordsAtPos(block.to) || a;
  const left = Math.max(0, Math.min(a.left, b.left) - 6), top = Math.max(0, a.top - 6);
  root.style.left = left + "px";
  root.style.top = top + "px";
  root.style.maxWidth = Math.max(200, win.innerWidth - left - 12) + "px";
  root.style.maxHeight = Math.max(120, win.innerHeight - top - 12) + "px";
  doc.body.appendChild(root);

  // カーソルのあったセル (見出しと区切りの行は、オーバーレイでは見出しの行) を覚えて、そこへ移る
  let srcRow = 0;  // ソースの行 (ブロックの中の番号。0 = 見出し、1 = 区切り)
  for (let i = 0; i < block.lineStarts.length; i++) if (head - block.from >= block.lineStarts[i]) srcRow = i;
  const posInLine = head - block.from - block.lineStarts[srcRow];
  const srcCells = rows[srcRow].cells;
  let col = srcCells.findIndex(s => s.from <= posInLine && posInLine <= s.to);
  if (col < 0) col = srcCells.findIndex(s => s.from > posInLine);  // パイプの上なら、右のセル
  if (col < 0) col = srcCells.length - 1;
  const ovrRow = srcRow <= 1 ? 1 : srcRow - 1;  // オーバーレイの行の番号 (セルに付けた data-r。tbody の中。1 = 最初のデータの行)
  const tds = [...tbody.querySelectorAll("td")];
  const idx = (ovrRow - 1) * ncol + Math.min(Math.max(col, 0), ncol - 1);
  open = {view, from: block.from, to: block.to, text: block.text, indent, aligns, ncol, root,
          lastCell: {row: 1, col: 0}};
  goCell(open, doc, tds[Math.min(idx, tds.length - 1)]);

  open.onKey = ev => {
    if (ev.key === "Escape") { ev.preventDefault(); closeOverlay(); return; }
    if (ev.key === "Tab") { ev.preventDefault(); moveCell(open, doc, ev.shiftKey ? -1 : 1); return; }
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
  const place = {row: o.lastCell.row === 0 ? 0 : o.lastCell.row + 1, col: o.lastCell.col, offset: 0};
  const anchor = cellPos(out, o.from, place);  // カーソルは、編集していたセルの中へ
  view.dispatch({changes: {from: o.from, to: o.to, insert: out},
                 selection: {anchor: anchor, head: anchor}, userEvent: "input"});
}
