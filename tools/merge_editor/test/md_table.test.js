// バンドルしたエディタを jsdom に読み込んで、Markdown の表 (GFM) の編集支援 (src/md_table.js) を確認する。
// 整形は純関数 (GGText.formatMarkdownTable)、キー操作は keydown を、貼り付けは paste イベントを送って確かめる。
import test, {after} from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import {JSDOM} from "jsdom";

const TEXT_BUNDLE = new URL("../../../git_graph/static/vendor/text_editor.js", import.meta.url);
const windows = [];
after(() => { windows.forEach(w => w.close()); });

function load() {
  const dom = new JSDOM("<!DOCTYPE html><div id='result'></div>", {runScripts: "outside-only", pretendToBeVisual: true});
  const w = dom.window;
  const rect = {left: 0, right: 0, top: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0};
  w.Range.prototype.getClientRects = () => [];
  w.Range.prototype.getBoundingClientRect = () => rect;
  w.Element.prototype.getClientRects = () => [];
  w.document.elementFromPoint = () => null;
  w.eval(fs.readFileSync(TEXT_BUNDLE, "utf8"));
  windows.push(w);
  return w;
}
function editor(w, text, path = "a.md") {
  w.document.getElementById("result").replaceChildren();
  return w.GGText.createTextEditor({parent: w.document.getElementById("result"), text: text, path: path});
}
function key(w, name, extra = {}) {
  const ev = new w.KeyboardEvent("keydown", {key: name, bubbles: true, cancelable: true, ...extra});
  w.document.querySelector(".cm-content").dispatchEvent(ev);
  return ev;
}
function paste(w, text) {
  const ev = new w.Event("paste", {bubbles: true, cancelable: true});
  ev.clipboardData = {getData: () => text};
  w.document.querySelector(".cm-content").dispatchEvent(ev);
  return ev;
}
const selText = ed => {
  const s = ed.view.state.selection.main;
  return ed.view.state.sliceDoc(s.from, s.to);
};
// セルの中へカーソルを置く (セルの内容の 1 文字目の後ろ)
function inCell(ed, lineNo, col) {
  const line = ed.view.state.doc.line(lineNo);
  ed.view.dispatch({selection: {anchor: line.from + (col === undefined ? 3 : (col ? 9 : 3))}});
}

// 見出し / 区切り / データ 2 行の表の前後に、表ではない段落が 1 行ずつある (初めから整形済み)
const TBL = "| 名前 | 年齢 |\n| :--- | ---: |\n| 秋子 | 32   |\n| 太郎 | 8    |\n";
const DOC = "intro\n\n" + TBL + "outro\n";  // 行: 1 intro, 2 空, 3 見出し, 4 区切り, 5 秋子, 6 太郎, 7 outro

test("format: tab stops (fullwidth characters count 2) and alignment markers", () => {
  const w = load();
  assert.equal(w.GGText.formatMarkdownTable("|a|名前|\n|-|--|\n|x|y|"),
               "| a   | 名前 |\n| --- | ---- |\n| x   | y    |");
  assert.equal(w.GGText.formatMarkdownTable(TBL.replace("| 秋子 | 32   |", "| 秋子 | 32 |").replace("| 太郎 | 8    |", "| 太郎 | 8 |")),
               TBL);  // 隙間だけを詰めて、並びはそのまま
  assert.equal(w.GGText.formatMarkdownTable("| 名前 | 年齢 |\n| :--- | :-: |\n| 秋子 | 32 |"),
               "| 名前 | 年齢 |\n| :--- | :--: |\n| 秋子 | 32   |");
  assert.equal(w.GGText.formatMarkdownTable("| a |"), null);  // 区切りの行が無いものや、表でないものは null
  assert.equal(w.GGText.formatMarkdownTable("aaa"), null);
  assert.equal(w.GGText.formatMarkdownTable("| a | b | c |\n| --- | --- | --- |\n| x | y |"),
               "| a   | b   | c   |\n| --- | --- | --- |\n| x   | y   |     |");  // 少ない行は、空セルで補う (表示と同じ)
});

test("a row with more cells than the header (a raw | in a cell) leaves the table alone", () => {
  const w = load();
  const t = "| a | b |\n| --- | --- |\n| x | y | z |\n";  // セルの中に生の「|」がある行は、区切りを決められない
  assert.equal(w.GGText.formatMarkdownTable(t), null);
  const ed = editor(w, "intro\n\n" + t + "outro\n");
  ed.view.dispatch({selection: {anchor: ed.view.state.doc.line(5).from + 3}});
  key(w, "Enter");
  key(w, "Shift-Tab");  // 表の補助は何もしない (Shift+Tab はインデントを戻すだけなので、文字は変わらない)
  assert.equal(ed.getText(), "intro\n\n| a | b |\n| --- | --- |\n| x\n| y | z |\noutro\n");
});

test("pasting data wider than the table widens every row (header and separator too)", () => {
  const w = load();
  const ed = editor(w, DOC);
  inCell(ed, 5);
  paste(w, "p\tq\tr");
  assert.equal(ed.getText(),
    "intro\n\n| 名前 | 年齢 |     |\n| :--- | ---: | --- |\n| 秋子 | 32   |     |\n| p    | q    | r   |\n| 太郎 | 8    |     |\noutro\n");
});

test("Tab / Shift+Tab move between cells, Tab on the last cell adds a row", () => {
  const w = load();
  const ed = editor(w, DOC);
  inCell(ed, 5);  // 「秋子」の中
  key(w, "Tab");
  assert.equal(selText(ed), "32");
  key(w, "Tab");
  assert.equal(selText(ed), "太郎");
  key(w, "Shift-Tab");
  assert.equal(selText(ed), "32");
  inCell(ed, 6);  // 最後の行の「太郎」
  key(w, "Shift-Tab");
  assert.equal(selText(ed), "32");   // 前の行の最後のセル
  key(w, "Shift-Tab");
  assert.equal(selText(ed), "秋子");
  key(w, "Shift-Tab");
  key(w, "Shift-Tab");
  assert.equal(selText(ed), ":---");  // 区切りの行まで戻る
  inCell(ed, 6, 1);  // 最後の行の「8」の中
  key(w, "Tab");
  const lines = ed.getText().split("\n");
  assert.equal(lines.length, 9);
  assert.ok(lines[6].startsWith("|") && lines[6].endsWith("|") && lines[6].includes("  "), "空の行が足される: " + lines[6]);
  const nl = ed.view.state.doc.line(7);
  assert.ok(ed.view.state.selection.main.from >= nl.from + 2 && ed.view.state.selection.main.from <= nl.from + 5, "新しい行の 1 つ目のセルの中");
});

test("Enter moves to the next row (same column) and adds a row on the last row", () => {
  const w = load();
  const ed = editor(w, DOC);
  inCell(ed, 5);
  key(w, "Enter");
  assert.equal(selText(ed), "太郎");
  inCell(ed, 6);
  key(w, "Enter");  // 最後の行 → 空の行が足されて、その 1 つ目のセルへ
  const lines = ed.getText().split("\n");
  assert.equal(lines.length, 9);
  assert.ok(lines[6].includes("  "));
  assert.equal(selText(ed), "");
});

test("Alt+Enter adds a row below, Ctrl+Enter adds a column right", () => {
  const w = load();
  const ed = editor(w, DOC);
  inCell(ed, 5);
  key(w, "Enter", {altKey: true});
  const lines = ed.getText().split("\n");
  assert.equal(lines.length, 9);
  assert.ok(lines[5].startsWith("|") && lines[5].includes("  "), lines[5]);
  assert.equal(lines.findIndex(l => l.includes("太郎")), 6);

  const ed2 = editor(w, DOC);
  ed2.view.dispatch({selection: {anchor: ed2.view.state.doc.line(3).from + 3}});  // 見出しの「名前」
  const ev = key(w, "Enter", {ctrlKey: true});
  assert.equal(ev.defaultPrevented, true);
  assert.equal(ed2.getText(),
    "intro\n\n| 名前 |     | 年齢 |\n| :--- | --- | ---: |\n| 秋子 |     | 32   |\n| 太郎 |     | 8    |\noutro\n");
  const nl = ed2.view.state.doc.line(3);  // 新しい列のセルへカーソルが移る (押した行 = 見出しの行)
  assert.ok(nl.text.startsWith("| 名前 |"));
  const s = ed2.view.state.selection.main;
  assert.ok(s.from >= nl.from + 8 && s.from <= nl.from + 12 && s.from === s.to);
});

test("Alt+Backspace deletes a row, Alt+Shift+Backspace deletes a column, both keep the table well-formed", () => {
  const w = load();
  const ed = editor(w, DOC);
  inCell(ed, 6);  // 太郎の行
  key(w, "Backspace", {altKey: true});
  assert.equal(ed.getText(), "intro\n\n| 名前 | 年齢 |\n| :--- | ---: |\n| 秋子 | 32   |\noutro\n");
  inCell(ed, 5);
  key(w, "Backspace", {altKey: true});
  assert.equal(ed.getText(), "intro\n\n| 名前 | 年齢 |\n| :--- | ---: |\n| 秋子 | 32   |\noutro\n");  // データの行が 1 つしかないときは消さない

  const ed2 = editor(w, DOC);
  ed2.view.dispatch({selection: {anchor: ed2.view.state.doc.line(5).from + 3}});
  key(w, "Enter", {ctrlKey: true});  // 列を 1 つ足して (カーソルはその新しいセル)、それを消す
  key(w, "Backspace", {altKey: true, shiftKey: true});
  assert.equal(ed2.getText(), DOC);  // 3 列にしてから中央の列を消すので、元の表に戻る
  key(w, "Backspace", {altKey: true, shiftKey: true});
  assert.equal(ed2.getText(), DOC);  // 2 列しかない表からは、列を消させない
  key(w, "Backspace", {altKey: true, shiftKey: true});  // 見出しの行でも同じ
  assert.equal(ed2.getText(), DOC);
});

test("leaving the table aligns the pipes (typing inside does not reformat yet)", () => {
  const w = load();
  const raw = "intro\n\n| 名前 | 年齢 |\n| --- | - |\n| 秋子 | 32 |\n| 太郎 | 8 |\n\noutro\n";
  const ed = editor(w, raw);
  inCell(ed, 5);
  key(w, "Tab");
  assert.ok(ed.getText().includes("| --- | - |"), "セルの移動だけでは、まだ整形しない");
  ed.view.dispatch({selection: {anchor: ed.getText().indexOf("outro") + 1}});  // 表の外へ
  assert.equal(ed.getText(),
    "intro\n\n| 名前 | 年齢 |\n| ---- | ---- |\n| 秋子 | 32   |\n| 太郎 | 8    |\n\noutro\n");
  ed.view.dispatch({selection: {anchor: ed.getText().indexOf("秋子")}});  // もう一度入っても、もう変わらない
  ed.view.dispatch({selection: {anchor: ed.getText().length}});
  assert.ok(!ed.getText().includes("| --- | - |"));
});

test("a table wider than the format width limit is not formatted (the other aids still work)", () => {
  const w = load();
  const long = "x".repeat(120);
  const wide = `| 名前 | ${long} |\n| --- | --- |\n| 秋子 | 32 |\n| 太郎 | 8 |\n`;
  assert.equal(w.GGText.formatMarkdownTable(wide), null);  // 整形すると上限 (100 桁) を超えるので、しない
  assert.match(w.GGText.formatMarkdownTable(wide, Infinity), /^\| 名前 +\|/);  // 幅の数えなし指示なら、整形する
  assert.equal(w.GGText.formatMarkdownTable("| a | b |\n| --- | --- |\n| " + long + " | c |\n"), null);

  // 表から出ても、縦揃えはしない (打っている途中に画面からあふれない)
  const ed = editor(w, "intro\n\n" + wide + "outro\n");
  ed.view.dispatch({selection: {anchor: ed.getText().indexOf("outro")}});
  assert.equal(ed.getText(), "intro\n\n" + wide + "outro\n");

  // 最後の行での Tab は行を足すが、ほかの行はそろえない
  inCell(ed, 6);  // 最後の行「太郎」→ 最後のセルまで Tab すると、行が足される
  key(w, "Tab");
  key(w, "Tab");
  assert.ok(ed.getText().includes("\n|  |\noutro\n"), "素の形の空の行が足される: " + JSON.stringify(ed.getText().slice(-10)));

  // 貼り付けも、足した行は素の形 (余白をそろえない) で入る
  const ed2 = editor(w, "intro\n\n" + wide + "outro\n");
  inCell(ed2, 5);
  const ev = paste(w, "p\tq");
  assert.equal(ev.defaultPrevented, true);
  assert.ok(ed2.getText().includes("\n| p | q |\n"), "貼った行が入る: " + JSON.stringify(ed2.getText().split("\n").slice(4, 6)));
  assert.ok(ed2.getText().startsWith("intro\n\n| 名前 | " + long + " |"), "すでにある行は変えない");
});

test("the table button opens a contenteditable overlay and writes the edits back to the source", () => {
  const w = load();
  const ed = editor(w, DOC);
  ed.view.dispatch({selection: {anchor: ed.view.state.doc.line(1).from + 1}});  // 表の外では開かない
  assert.equal(w.GGText.mdTableOverlay(ed.view), false);
  assert.equal(w.document.querySelector(".gg-md-table-overlay"), null);
  inCell(ed, 5);  // 「秋子」のセルの中
  assert.equal(w.GGText.mdTableOverlay(ed.view), true);
  const ov = w.document.querySelector(".gg-md-table-overlay");
  assert.ok(ov, "表の上にオーバーレイが出る");
  const ths = ov.querySelectorAll("thead th"), tds = ov.querySelectorAll("tbody td");
  assert.equal(ths.length, 2);
  assert.equal(ths[0].textContent, "名前");
  assert.equal(tds.length, 4);  // データの 2 行 × 2 列 (区切りの行は出さない)
  assert.equal(tds[0].textContent, "秋子");
  // Tab で次のセルへ動く (エディタの Tab は働かない)
  const tab = new w.KeyboardEvent("keydown", {key: "Tab", bubbles: true, cancelable: true});
  ov.dispatchEvent(tab);
  assert.equal(tab.defaultPrevented, true);
  // セルを直接書き換えて Esc で閉じると、ソースに書き戻る
  tds[0].textContent = "あきこ";
  const esc = new w.KeyboardEvent("keydown", {key: "Escape", bubbles: true, cancelable: true});
  ov.dispatchEvent(esc);
  assert.equal(esc.defaultPrevented, true);
  assert.equal(ed.getText(),
    "intro\n\n| 名前   | 年齢 |\n| :----- | ---: |\n| あきこ | 32   |\n| 太郎   | 8    |\noutro\n");
  assert.equal(w.document.querySelector(".gg-md-table-overlay"), null);  // 閉じたら消える
  const s = ed.view.state.selection.main;
  const at = ed.getText().indexOf("32");  // Tab で次のセル (32) に動ったあとだから、そこへ戻る
  assert.ok(at <= s.from && s.from <= at + 2, "カーソルは編集していたセルの中");
});

test("pasting tab separated text (spreadsheet) into a table adds the rows", () => {
  const w = load();
  const ed = editor(w, DOC);
  inCell(ed, 5);
  const ev = paste(w, "あ\tい\nう\tえ");
  assert.equal(ev.defaultPrevented, true);
  assert.equal(ed.getText(),
    "intro\n\n| 名前 | 年齢 |\n| :--- | ---: |\n| 秋子 | 32   |\n| あ   | い   |\n| う   | え   |\n| 太郎 | 8    |\noutro\n");
});

test("the table keys only work for markdown (plain text keeps its Tab behaviour)", () => {
  const w = load();
  const ed = editor(w, DOC, "a.txt");
  ed.view.dispatch({selection: {anchor: ed.view.state.doc.line(5).from + 3}});
  const ev = key(w, "Tab");
  assert.equal(ev.defaultPrevented, true);  // タブ文字を入れる (既存の動作)
  assert.ok(ed.getText().includes("\t"));
});
