// Markdown の表 (GFM) の編集支援。表の中にカーソルがあるときだけ働くキー操作 (Tab でセル移動、
// Enter で次の行、Alt+Enter で行追加、Ctrl/Cmd+Enter で列追加、Alt+Backspace / Alt+Shift+Backspace で
// 行 / 列の削除) と、カーソルが表から出たときのパイプの縦揃え (全角を 2 桁として幅を数えて整える。
// 幅が FORMAT_MAX_WIDTH 桁を超える表は、縦揃えをしない)、
// 表の中へのタブ区切りテキストの貼り付け (表計算からコピーしたもの) を、表の行に変換する をまとめたもの。
// 文字列だけの変換 (formatMarkdownTable と mdTable*) は、エディタを介さず io を立てずにテスト
// (test/md_table.test.js) できる純関数にしてある。
import {Prec} from "@codemirror/state";
import {EditorView, keymap} from "@codemirror/view";

// --- 幅の計数 (整形でパイプの縦を揃えるため。全角文字は 2 と数える) ---

const WIDE = [
  [0x1100, 0x115f], [0x2329, 0x232a], [0x2e80, 0x303e], [0x3041, 0x33ff], [0x3400, 0x4dbf],
  [0x4e00, 0x9fff], [0xa000, 0xa4cf], [0xac00, 0xd7a3], [0xf900, 0xfaff], [0xfe30, 0xfe6f],
  [0xff00, 0xff60], [0xffe0, 0xffe6],
];
function charWidth(s) {
  const c = s.codePointAt(0);
  return WIDE.some(([a, b]) => c >= a && c <= b) ? 2 : 1;
}
function displayWidth(s) {
  let w = 0;
  for (const ch of s) w += charWidth(ch);
  return w;
}

// --- 行の読み取り ---

function pipeIndices(line) {  // エスケープされていない "|" の位置 (\| はセルの内容)
  const out = [];
  for (let i = 0; i < line.length; i++) {
    if (line[i] === "\\") { i++; continue; }
    if (line[i] === "|") out.push(i);
  }
  return out;
}
function isRowLine(line) {  // 先頭パイプの前も、最後のパイプの後も空白だけの行が、表の行
  const ps = pipeIndices(line);
  if (ps.length < 2) return false;
  return line.slice(0, ps[0]).trim() === "" && line.slice(ps[ps.length - 1] + 1).trim() === "";
}
function rowCells(line) {  // 行の各セルの (行の中の) 位置と内容。表の行でなければ null
  if (!isRowLine(line)) return null;
  const ps = pipeIndices(line), cells = [];
  for (let k = 1; k < ps.length; k++) {
    const a = ps[k - 1] + 1, b = ps[k];
    let from = a, to = b;
    while (from < to && line[from] === " ") from++;
    while (to > from && line[to - 1] === " ") to--;
    if (from === to) from = to = (a + b) >> 1;  // 空のセルは、パイプとパイプの真ん中に置く
    cells.push({from: from, to: to, text: line.slice(from, to)});
  }
  return {indent: line.slice(0, ps[0]), cells: cells};
}
function isSepRow(cells) {  // 区切りの行 (--- / :---: など) か
  return cells.length > 0 && cells.every(c => /^:?-+:?$/.test(c.text));
}
function tableIsValid(rows) {
  if (rows.length < 3 || !rows[0] || !rows[1]) return false;
  const ncol = rows[0].cells.length;
  if (ncol < 2 || !isSepRow(rows[1].cells)) return false;
  // 見出しより多いセルの行があるとき (セルの中に「|」を生で書いてある) は、区切りがどれか分からないので補助しない
  return rows.every(r => r && r.cells.length >= 1 && r.cells.length <= ncol);
}
function ncolOf(rows) {  // 列の数は、見出しの行で決める (少ない行は、整形と表示が空セルを補う)
  return rows[0].cells.length;
}
function rowLine(cells, indent) {  // セルの内容から、行を 1 行作る (整形前の素の形)
  return indent + "| " + cells.join(" | ") + " |";
}

function padRight(s, w) {
  return s + " ".repeat(w - displayWidth(s));
}
function sepCell(w, left, right) {  // 区切りの行のセル。元の : の位置を残して、幅を列に合わせる
  if (left && right) return ":" + "-".repeat(w - 2) + ":";
  if (left) return ":" + "-".repeat(w - 1);
  if (right) return "-".repeat(w - 1) + ":";
  return "-".repeat(w);
}

// --- 文字列だけの変換 (テストから直接使う) ---

// 整形してよい幅の上限 (全角を 2 として数えた桁数)。これを超える表は、画面に入りきらずに
// 横に長くなるだけで縦揃えの意味がないので、ほかの補助は効かせたまま整形だけをしない
export const FORMAT_MAX_WIDTH = 100;

// 表のブロックのテキストを、列の幅を数えて整形する。表でないときと、整形結果が max 桁
// (既定は FORMAT_MAX_WIDTH) を超えるときは null (max に Infinity を渡すと、幅を数えない。末尾に改行 1 つが付いていてもよい)
export function formatMarkdownTable(text, max = FORMAT_MAX_WIDTH) {
  const lines = String(text).split("\n");
  const tail = lines.length > 1 && lines[lines.length - 1] === "" ? "\n" : "";  // 末尾の改行は、整形してから返す
  if (tail) lines.pop();
  const rows = lines.map(rowCells);
  if (!tableIsValid(rows)) return null;
  const ncol = ncolOf(rows), widths = [];
  for (let c = 0; c < ncol; c++) {
    let w = 3;  // 区切りの --- のぶん
    for (const r of rows) if (c < r.cells.length) w = Math.max(w, displayWidth(r.cells[c].text));
    widths.push(w);
  }
  const sep = isSepRow(rows[1].cells) ? rows[1].cells.map(c => {
    const m = /^(:?)(-+)(:?)$/.exec(c.text);
    return m ? [m[1] !== "", m[3] !== ""] : [false, false];
  }) : null;
  const out = rows.map((r, i) => {
    const parts = [];
    for (let c = 0; c < ncol; c++) {
      if (i === 1 && sep) {
        const [l, t] = sep[c] || [false, false];
        parts.push(sepCell(widths[c], l, t));
      } else {
        parts.push(padRight(c < r.cells.length ? r.cells[c].text : "", widths[c]));
      }
    }
    return r.indent + "| " + parts.join(" | ") + " |";
  });
  if (out.some(l => displayWidth(l) > max)) return null;
  return out.join("\n") + tail;
}

// データの行 (rowIdx 番目) の後ろに空の行を足す。戻り値は {text, row, col} (row / col は置き換え後のカーソル位置の目印)
export function mdTableInsertRowAfter(text, rowIdx, colIdx) {
  const lines = String(text).split("\n");
  const rows = lines.map(rowCells);
  if (!tableIsValid(rows)) return null;
  if (rowIdx < 1 || rowIdx >= rows.length) return null;  // 見出しの直後は区切りの行が来るので足せない
  lines.splice(rowIdx + 1, 0, rowLine([], rows[rowIdx].indent));
  return {text: lines.join("\n"), row: rowIdx, col: colIdx};
}
export function mdTableAppendRow(text) {  // 最後の行の後ろに空の行を足す
  const lines = String(text).split("\n");
  const rows = lines.map(rowCells);
  if (!tableIsValid(rows)) return null;
  lines.push(rowLine([], rows[rows.length - 1].indent));
  return {text: lines.join("\n"), row: rows.length, col: 0};
}
export function mdTableInsertColumnAfter(text, rowIdx, colIdx) {  // colIdx 番目の列の後ろに空の列を足す
  const lines = String(text).split("\n");
  const rows = lines.map(rowCells);
  if (!tableIsValid(rows)) return null;
  if (colIdx < 0 || colIdx >= ncolOf(rows)) return null;
  for (let i = 0; i < rows.length; i++) {
    const cells = rows[i].cells.map(c => c.text);
    cells.splice(Math.min(colIdx + 1, cells.length), 0, i === 1 ? "---" : "");
    lines[i] = rowLine(cells, rows[i].indent);
  }
  return {text: lines.join("\n"), row: rowIdx, col: colIdx + 1};
}
export function mdTableDeleteRow(text, rowIdx) {
  const lines = String(text).split("\n");
  const rows = lines.map(rowCells);
  if (!tableIsValid(rows)) return null;
  if (rowIdx < 2 || rowIdx >= rows.length) return null;  // 見出しと区切りは消せない
  if (rows.length === 3) return {text: "", row: 0, col: 0};  // データの行がこれしかなければ、表ごと消す
  lines.splice(rowIdx, 1);
  return {text: lines.join("\n"), row: Math.min(rowIdx, lines.length - 1), col: 0};
}
export function mdTableDeleteColumn(text, rowIdx, colIdx) {
  const lines = String(text).split("\n");
  const rows = lines.map(rowCells);
  if (!tableIsValid(rows)) return null;
  const ncol = ncolOf(rows);
  if (ncol <= 2 || colIdx < 0 || colIdx >= ncol) return null;  // 列が 2 しかないと、消すと壊れる
  for (let i = 0; i < rows.length; i++) {
    const cells = rows[i].cells.filter((_, c) => c !== colIdx).map(c => c.text);
    lines[i] = rowLine(cells, rows[i].indent);
  }
  return {text: lines.join("\n"), row: rowIdx, col: Math.min(Math.max(colIdx, 0), ncol - 2)};
}

// --- エディタのコマンド (md_table_extension が使う) ---

function blockOf(doc, pos) {  // pos のある行を含む表 (連続する表の行) の情報。表がなければ null
  const lineNo = doc.lineAt(pos).number;
  if (!isRowLine(doc.line(lineNo).text)) return null;
  let fromLine = lineNo, toLine = lineNo;
  while (fromLine > 1 && isRowLine(doc.line(fromLine - 1).text)) fromLine--;
  while (toLine < doc.lines && isRowLine(doc.line(toLine + 1).text)) toLine++;
  const from = doc.line(fromLine).from, to = doc.line(toLine).to;
  const lineStarts = [];
  let blockText = "", seen = false;
  for (let n = fromLine; n <= toLine; n++) {
    if (seen) blockText += "\n";
    lineStarts.push(blockText.length);
    blockText += doc.line(n).text;
    seen = true;
  }
  const rows = blockText.split("\n").map(rowCells);
  if (!tableIsValid(rows)) return null;
  return {from, to, text: blockText, rows, fromLine, toLine, lineStarts};
}

function cellPos(text, blockFrom, place) {  // 整形後のテキストの (row, col) 番目のセルの中の位置 (絶対位置)
  const lines = text.split("\n");
  const row = Math.min(Math.max(place.row, 0), lines.length - 1);
  const spans = rowCells(lines[row]);
  if (!spans) return blockFrom;
  const idx = Math.min(Math.max(place.col, 0), spans.cells.length - 1);
  const s = spans.cells[idx];
  const start = blockFrom + lines.slice(0, row).reduce((a, l) => a + l.length + 1, 0);
  return start + s.from + Math.max(0, Math.min(place.offset || 0, s.to - s.from));
}
function currentCell(view) {  // カーソルのあるセル {block, row, col, offset}。表の中になければ null
  const sel = view.state.selection.main, head = sel.head;
  const block = blockOf(view.state.doc, head);
  if (!block) return null;
  const line = view.state.doc.lineAt(head), posInLine = head - line.from;
  const row = line.number - block.fromLine;
  const cells = block.rows[row].cells;
  let col = cells.findIndex(s => s.from <= posInLine && posInLine <= s.to);
  if (col < 0) {
    col = cells.findIndex(s => s.from > posInLine);  // パイプの上なら、右のセルとみなす
    if (col < 0) col = cells.length - 1;
  }
  if (sel.from !== sel.to) {  // セルの内容を選んだ状態 (Tab で移った直後) なら、選んだ範囲がそのセルのときだけセルとみなす
    const base = block.from + block.lineStarts[row];
    const s = cells[col];
    if (sel.from !== base + s.from || sel.to !== base + s.to) return null;
  }
  return {block, row, col: col, offset: Math.max(0, posInLine - cells[col].from)};
}
function replaceBlock(view, block, newText, place, userEvent) {
  const anchor = cellPos(newText, block.from, place);
  view.dispatch({changes: {from: block.from, to: block.to, insert: newText},
                 selection: {anchor: anchor, head: anchor}, scrollIntoView: true, userEvent: userEvent});
  return true;
}
function op(view, block, operation, userEvent) {  // 純関数の mdTable* を整形してからブロックに置き換える
  const formatted = formatMarkdownTable(operation.text);
  // 幅の上限を超える表は、縦揃えをせずに、足した行・列だけを素の形で置く (ほかの補助は効かせておく)
  if (formatted == null) {
    if (formatMarkdownTable(operation.text, Infinity) == null) return false;
    return replaceBlock(view, block, operation.text, operation, userEvent);
  }
  return replaceBlock(view, block, formatted, operation, userEvent);
}

function moveCell(view, dir) {  // dir > 0 で次のセル、< 0 で前のセル
  const cur = currentCell(view);
  if (!cur) return false;
  const {block, row, col} = cur, rows = block.rows;
  if (dir > 0) {
    if (col < rows[row].cells.length - 1) return select(view, block, row, col + 1);
    if (row < rows.length - 1) return select(view, block, row + 1, 0);
    return op(view, block, mdTableAppendRow(block.text), "input");
  }
  if (col > 0) return select(view, block, row, col - 1);
  if (row > 0) return select(view, block, row - 1, rows[row - 1].cells.length - 1);
  return false;  // 表の先頭のセルでは、もとの Shift-Tab (インデントを戻す) に任せる
}
function select(view, block, row, col) {
  const rel = block.text.split("\n")[row], spans = rowCells(rel).cells;
  const s = spans[Math.min(col, spans.length - 1)];
  const start = block.from + block.lineStarts[row];
  view.dispatch({selection: {anchor: start + s.from, head: start + s.to}, scrollIntoView: true});
  return true;
}
function enterRow(view) {  // 最後の行なら、次の行を作る。そうでなければ、次の行の同じ列へ
  const cur = currentCell(view);
  if (!cur) return false;
  const {block, row, col} = cur;
  if (row === block.rows.length - 1) return op(view, block, mdTableAppendRow(block.text), "input");
  return select(view, block, row + 1, Math.min(col, block.rows[row + 1].cells.length - 1));
}
function insertRowBelow(view) {  // Alt+Enter
  const cur = currentCell(view);
  if (!cur) return false;
  const ins = mdTableInsertRowAfter(cur.block.text, cur.row, cur.col);
  if (!ins) return false;
  return op(view, cur.block, Object.assign({}, ins, {offset: cur.offset}), "input");
}
function insertColumnRight(view) {  // Ctrl/Cmd+Enter
  const cur = currentCell(view);
  if (!cur) return false;
  return op(view, cur.block, mdTableInsertColumnAfter(cur.block.text, cur.row, cur.col), "input");
}
function deleteRow(view) {  // Alt+Backspace
  const cur = currentCell(view);
  if (!cur) return false;
  const removal = mdTableDeleteRow(cur.block.text, cur.row);
  if (!removal || removal.text === "") return false;  // 表ごとの削除は、Ctrl+A や外から消してもらう
  return op(view, cur.block, removal, "delete");
}
function deleteColumn(view) {  // Alt+Shift+Backspace
  const cur = currentCell(view);
  if (!cur) return false;
  const removal = mdTableDeleteColumn(cur.block.text, cur.row, cur.col);
  if (!removal) return false;
  return op(view, cur.block, removal, "delete");
}

// タブ区切りのテキスト (表計算からのコピー) を、表の行に変換して現在の行の下に挿入する
function onPaste(ev, view) {
  const cd = ev.clipboardData;
  if (!cd || typeof cd.getData !== "function") return false;
  const text = cd.getData("text/plain");
  if (!text || text.indexOf("\t") < 0) return false;
  const cur = currentCell(view);
  if (!cur) return false;
  const {block, row} = cur;
  const pasted = text.replace(/\r\n?/g, "\n").replace(/\n+$/, "").split("\n")
    .map(l => l.split("\t").map(c => c.replace(/\|/g, "\\|")));
  const lines = block.text.split("\n");
  pasted.forEach((cells, i) => lines.splice(row + 1 + i, 0, rowLine(cells, block.rows[row].indent)));
  // 貼った行に列が多いときは、全部の行をその列の数まで広げる (見出しと区切りも)
  const rows = lines.map(rowCells);
  const max = Math.max.apply(null, rows.map(r => r.cells.length));
  for (let i = 0; i < rows.length; i++) {
    if (rows[i].cells.length >= max) continue;
    const cells = rows[i].cells.map(c => c.text);
    while (cells.length < max) cells.push(i === 1 ? "---" : "");
    lines[i] = rowLine(cells, rows[i].indent);
  }
  const joined = lines.join("\n");
  if (formatMarkdownTable(joined, Infinity) == null) return false;
  const formatted = formatMarkdownTable(joined);  // 幅の上限を超えるなら、素の形 (余白をそろえない) のまま入れる
  return replaceBlock(view, block, formatted == null ? joined : formatted, {row: row + 1, col: 0}, "input.paste");
}

// カーソルが表から出たら、その表を整形する (打っている最中は動かさないので、ちらつかない)
function mdTableExtension() {
  let inBlock = null;  // 前回カーソルのあった表の範囲 (doc の位置)
  return [
    Prec.high(keymap.of([
      {key: "Tab", run: view => moveCell(view, 1)},
      {key: "Shift-Tab", run: view => moveCell(view, -1)},
      {key: "Enter", run: enterRow},
      {key: "Alt-Enter", run: insertRowBelow},
      {key: "Mod-Enter", run: insertColumnRight},
      {key: "Alt-Backspace", run: deleteRow},
      {key: "Alt-Shift-Backspace", run: deleteColumn},
    ])),
    EditorView.updateListener.of(update => {
      const head = update.state.selection.main.head;
      const block = blockOf(update.state.doc, head);
      if (block) { inBlock = {from: block.from, to: block.to}; return; }
      if (!inBlock) return;
      const from = update.docChanged ? update.changes.mapPos(inBlock.from, 1) : inBlock.from;
      const to = update.docChanged ? update.changes.mapPos(inBlock.to, -1) : inBlock.to;
      inBlock = null;
      if (to <= from) return;
      const text = update.view.state.sliceDoc(from, to);
      const formatted = formatMarkdownTable(text);
      if (formatted == null || formatted === text) return;
      update.view.dispatch({changes: {from: from, to: to, insert: formatted}});
    }),
    EditorView.domEventHandlers({paste: onPaste}),
  ];
}

// オーバーレイの編集 (md_table_overlay.js) が、表の解析と書き戻しで使う純関数も出す
export {mdTableExtension, blockOf, cellPos, rowCells, rowLine, tableIsValid};
