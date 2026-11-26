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
function caretEnd(w, el) {  // セル (contenteditable) の末尾へキャレットを置く
  el.focus();
  const r = w.document.createRange();
  r.selectNodeContents(el);
  r.collapse(false);
  const s = w.getSelection();
  s.removeAllRanges();
  s.addRange(r);
}
function caretAt(w, el, offset) {  // セルの中の offset 文字目の後ろ (先頭は 0) へキャレットを置く
  el.focus();
  const r = w.document.createRange();
  r.setStart(el.firstChild, offset);
  r.setEnd(el.firstChild, offset);
  const s = w.getSelection();
  s.removeAllRanges();
  s.addRange(r);
}
function fakeWrap(w, cell, perLine, lineH) {  // 折り返しの代わりのレイアウト (テスト用): cell の中のテキストを perLine 文字ごとに折り返すものとして、行ごとの矩形を返す
  w.Range.prototype.getClientRects = function() {
    const inside = n => n === cell || cell.contains(n);
    if (this.collapsed || !inside(this.startContainer) || !inside(this.endContainer)) return [];
    const abs = (container, offset) => {  // 境界の、セルの中での文字の位置
      const pr = w.document.createRange();
      pr.selectNodeContents(cell);
      pr.setEnd(container, offset);
      return pr.toString().length;
    };
    const from = abs(this.startContainer, this.startOffset), to = abs(this.endContainer, this.endOffset);
    const rects = [];
    for (let i = Math.floor(from / perLine); i <= Math.floor(Math.max(from, to - 1) / perLine); i++)
      rects.push({top: i * lineH, bottom: (i + 1) * lineH, height: lineH, left: 0, right: 10, width: 10});
    return rects;
  };
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
  assert.equal(ov.querySelectorAll("thead").length, 0);  // 見出しもふつうの行 (tbody) に入れる (上にも行を足せる)
  const rows = ov.querySelectorAll("tbody tr"), tds = ov.querySelectorAll("tbody td");
  assert.equal(rows.length, 3);  // 見出し + データの 2 行 (区切りの行は出さない)
  assert.equal(tds.length, 6);
  assert.equal(rows[0].children[0].textContent, "名前");  // 最初の行が見出し
  assert.equal(rows[0].children[0].style.background, "rgb(246, 248, 250)", "見出しの行は、色を変えて見分けがつく");
  assert.equal(rows[1].children[0].textContent, "秋子");
  assert.equal(tds[0].style.whiteSpace, "pre-wrap");  // 長いセルは、セルの中で折り返す
  // Tab で次のセルへ動く (エディタの Tab は働かない)
  const tab = new w.KeyboardEvent("keydown", {key: "Tab", bubbles: true, cancelable: true});
  ov.dispatchEvent(tab);
  assert.equal(tab.defaultPrevented, true);
  // セルを直接書き換えて Esc で閉じると、ソースに書き戻る
  tds[2].textContent = "あきこ";
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

test("the overlay's buttons and keys add and delete rows and columns, and the result is written back", () => {
  const w = load();
  const ed = editor(w, DOC);
  inCell(ed, 5);  // 「秋子」のセルの中
  assert.equal(w.GGText.mdTableOverlay(ed.view, {addRowAbove: "↑ 行", addRow: "↓ 行", delRow: "− 行",
                                                 addColLeft: "← 列", addCol: "→ 列", delCol: "− 列"}), true);
  const ov = w.document.querySelector(".gg-md-table-overlay");
  const btns = [...ov.querySelectorAll("button")];
  assert.deepEqual(btns.map(b => b.textContent), ["↑ 行", "↓ 行", "− 行", "← 列", "→ 列", "− 列"]);
  assert.ok(ov.querySelector("table").parentElement.style.overflow === "auto", "ボタンの行はスクロールの外 (スクロールしても見えている)");
  assert.equal(ov.querySelectorAll("tbody tr").length, 3);  // 見出し + データの 2 行

  // ↓ 行: いまの行 (秋子) の下に空の行が足される
  btns[1].click();
  assert.equal(ov.querySelectorAll("tbody tr").length, 4);
  ov.dispatchEvent(new w.KeyboardEvent("keydown", {key: "Escape", bubbles: true, cancelable: true}));
  assert.equal(ed.getText(),
    "intro\n\n| 名前 | 年齢 |\n| :--- | ---: |\n| 秋子 | 32   |\n|      |      |\n| 太郎 | 8    |\noutro\n");

  // ↓ 行でカーソルは足された行に動っているので、そのまま Alt+Backspace で消す → 元に戻る
  assert.equal(w.GGText.mdTableOverlay(ed.view), true);
  const ov2 = w.document.querySelector(".gg-md-table-overlay");
  ov2.dispatchEvent(new w.KeyboardEvent("keydown", {key: "Backspace", altKey: true, bubbles: true, cancelable: true}));
  assert.equal(ov2.querySelectorAll("tbody tr").length, 3);
  ov2.dispatchEvent(new w.KeyboardEvent("keydown", {key: "Escape", bubbles: true, cancelable: true}));
  assert.equal(ed.getText(), DOC);

  // 見出しの行で ↑ 行: 見出しの「上」に空の行が足される (その行が、新しい見出しになる)
  ed.view.dispatch({selection: {anchor: ed.view.state.doc.line(3).from + 3}});  // 見出しの「名前」
  assert.equal(w.GGText.mdTableOverlay(ed.view), true);
  const ov7 = w.document.querySelector(".gg-md-table-overlay");
  ov7.querySelectorAll("button")[0].click();  // ↑ 行
  assert.equal(ov7.querySelectorAll("tbody tr").length, 4);
  assert.equal(ov7.querySelectorAll("tbody tr")[0].children[0].textContent, "\u200b");  // 足されたのは空の行
  ov7.dispatchEvent(new w.KeyboardEvent("keydown", {key: "Escape", bubbles: true, cancelable: true}));
  assert.equal(ed.getText(),
    "intro\n\n|      |      |\n| :--- | ---: |\n| 名前 | 年齢 |\n| 秋子 | 32   |\n| 太郎 | 8    |\noutro\n");

  // − 行 (カーソルは新しい見出しの行にある) で元に戻す: 見出しの行を消すと、次の行が見出しに繰り上がる
  assert.equal(w.GGText.mdTableOverlay(ed.view), true);
  const ov8 = w.document.querySelector(".gg-md-table-overlay");
  ov8.querySelectorAll("button")[2].click();  // − 行
  assert.equal(ov8.querySelectorAll("tbody tr").length, 3);
  ov8.dispatchEvent(new w.KeyboardEvent("keydown", {key: "Escape", bubbles: true, cancelable: true}));
  assert.equal(ed.getText(), DOC);
  ed.view.dispatch({selection: {anchor: ed.view.state.doc.line(3).from + 3}});  // 見出しの「名前」
  assert.equal(w.GGText.mdTableOverlay(ed.view), true);
  const ov9 = w.document.querySelector(".gg-md-table-overlay");
  ov9.querySelectorAll("button")[3].click();  // ← 列
  assert.equal(ov9.querySelectorAll("tbody tr")[0].children.length, 3);
  ov9.dispatchEvent(new w.KeyboardEvent("keydown", {key: "Escape", bubbles: true, cancelable: true}));
  assert.equal(ed.getText(),
    "intro\n\n|     | 名前 | 年齢 |\n| --- | :--- | ---: |\n|     | 秋子 | 32   |\n|     | 太郎 | 8    |\noutro\n");
  // (以降の確認のため、元の 2 列に戻す: Alt+Shift+Backspace で足した列 (カーソルはそこ) を消す)
  ed.view.dispatch({selection: {anchor: ed.view.state.doc.line(3).from + 3}});
  assert.equal(w.GGText.mdTableOverlay(ed.view), true);
  const ov10 = w.document.querySelector(".gg-md-table-overlay");
  ov10.dispatchEvent(new w.KeyboardEvent("keydown", {key: "Backspace", altKey: true, shiftKey: true, bubbles: true, cancelable: true}));
  ov10.dispatchEvent(new w.KeyboardEvent("keydown", {key: "Escape", bubbles: true, cancelable: true}));
  assert.equal(ed.getText(), DOC);

  // 見出しのセルも書ける (td も contenteditable)
  assert.equal(w.GGText.mdTableOverlay(ed.view), true);
  const ov5 = w.document.querySelector(".gg-md-table-overlay");
  assert.equal(ov5.querySelectorAll("tbody tr")[0].children[0].getAttribute("contenteditable"), "true");
  ov5.querySelectorAll("tbody tr")[0].children[0].textContent = "氏名";
  ov5.dispatchEvent(new w.KeyboardEvent("keydown", {key: "Escape", bubbles: true, cancelable: true}));
  assert.ok(ed.getText().startsWith("intro\n\n| 氏名 | 年齢 |"));

  // データの行が 1 つだけの表では行を消さず、列が 2 つの表では列を消さない
  const ed2 = editor(w, "intro\n\n| a | b |\n| --- | --- |\n| x | y |\noutro\n");
  inCell(ed2, 5);
  assert.equal(w.GGText.mdTableOverlay(ed2.view), true);
  const ov6 = w.document.querySelector(".gg-md-table-overlay");
  ov6.dispatchEvent(new w.KeyboardEvent("keydown", {key: "Backspace", altKey: true, bubbles: true, cancelable: true}));
  assert.equal(ov6.querySelectorAll("tbody tr").length, 2);  // 消すと見出ししか残らないので、行は消さない
  ov6.dispatchEvent(new w.KeyboardEvent("keydown", {key: "Backspace", altKey: true, shiftKey: true, bubbles: true, cancelable: true}));
  assert.equal(ov6.querySelectorAll("tbody tr")[0].children.length, 2);  // 列が 2 つしかないので、列も消さない
  ov6.dispatchEvent(new w.KeyboardEvent("keydown", {key: "Escape", bubbles: true, cancelable: true}));
  assert.equal(ed2.getText(),  // 行も列も消えていない (整形だけは効く)
    "intro\n\n| a   | b   |\n| --- | --- |\n| x   | y   |\noutro\n");
});

test("in the overlay, Ctrl+Enter adds a row below and Alt+Enter makes a line break (written back as <br>)", () => {
  const w = load();
  const ed = editor(w, DOC);
  inCell(ed, 5);  // 「秋子」のセルの中
  assert.equal(w.GGText.mdTableOverlay(ed.view), true);
  const ov = w.document.querySelector(".gg-md-table-overlay");
  const ev = new w.KeyboardEvent("keydown", {key: "Enter", altKey: true, bubbles: true, cancelable: true});
  ov.dispatchEvent(ev);  // セルの中で改行 (キャレットは「秋子」の先頭にあるので、改行が先に入る)
  assert.equal(ev.defaultPrevented, true);
  ov.dispatchEvent(new w.KeyboardEvent("keydown", {key: "Escape", bubbles: true, cancelable: true}));
  assert.equal(ed.getText(),
    "intro\n\n| 名前     | 年齢 |\n| :------- | ---: |\n| <br>秋子 | 32   |\n| 太郎     | 8    |\noutro\n");

  // ソースの <br> は、開くときにセルの中の改行になる (Esc だけで閉じれば、書き戻しで元の形のまま)
  ed.view.dispatch({selection: {anchor: ed.view.state.doc.line(5).from + 3}});
  assert.equal(w.GGText.mdTableOverlay(ed.view), true);
  const ov2 = w.document.querySelector(".gg-md-table-overlay");
  const cell = ov2.querySelectorAll("tbody tr")[1].children[0];
  assert.equal(cell.textContent, "\n秋子");  // <br> が改行になっている
  ov2.dispatchEvent(new w.KeyboardEvent("keydown", {key: "Escape", bubbles: true, cancelable: true}));
  assert.equal(ed.getText(),
    "intro\n\n| 名前     | 年齢 |\n| :------- | ---: |\n| <br>秋子 | 32   |\n| 太郎     | 8    |\noutro\n");

  // Ctrl+Enter: 行を下に足す
  ed.view.dispatch({selection: {anchor: ed.view.state.doc.line(6).from + 3}});  // 「太郎」
  assert.equal(w.GGText.mdTableOverlay(ed.view), true);
  const ov3 = w.document.querySelector(".gg-md-table-overlay");
  const ev3 = new w.KeyboardEvent("keydown", {key: "Enter", ctrlKey: true, bubbles: true, cancelable: true});
  ov3.dispatchEvent(ev3);
  assert.equal(ev3.defaultPrevented, true);
  assert.equal(ov3.querySelectorAll("tbody tr").length, 4);
  ov3.dispatchEvent(new w.KeyboardEvent("keydown", {key: "Escape", bubbles: true, cancelable: true}));
  assert.equal(ed.getText(),
    "intro\n\n| 名前     | 年齢 |\n| :------- | ---: |\n| <br>秋子 | 32   |\n| 太郎     | 8    |\n|          |      |\noutro\n");
});

test("pasting tab separated text into the overlay overwrites the cells (rows and columns are added when needed)", () => {
  const w = load();
  const ed = editor(w, DOC);
  inCell(ed, 5);  // 「秋子」のセルの中
  assert.equal(w.GGText.mdTableOverlay(ed.view), true);
  const ov = w.document.querySelector(".gg-md-table-overlay");
  const ev = new w.Event("paste", {bubbles: true, cancelable: true});
  ev.clipboardData = {getData: () => "p\tq\nr\ts"};
  ov.dispatchEvent(ev);
  assert.equal(ev.defaultPrevented, true);
  assert.equal(ov.querySelectorAll("tbody tr").length, 3);  // 行の数は変わらない (上書き)
  assert.equal(ov.querySelectorAll("tbody tr")[1].children[0].textContent, "p");
  assert.equal(ov.querySelectorAll("tbody tr")[2].children[1].textContent, "s");
  ov.dispatchEvent(new w.KeyboardEvent("keydown", {key: "Escape", bubbles: true, cancelable: true}));
  assert.equal(ed.getText(),
    "intro\n\n| 名前 | 年齢 |\n| :--- | ---: |\n| p    | q    |\n| r    | s    |\noutro\n");

  // 列が足りなければ、全部の行 (見出しも) が広がる (カーソルは、貼り始めたセル「p」にある)
  assert.equal(w.GGText.mdTableOverlay(ed.view), true);
  const ov2 = w.document.querySelector(".gg-md-table-overlay");
  const ev2 = new w.Event("paste", {bubbles: true, cancelable: true});
  ev2.clipboardData = {getData: () => "x\ty\tz"};
  ov2.dispatchEvent(ev2);
  assert.equal(ov2.querySelectorAll("tbody tr")[0].children.length, 3);
  ov2.dispatchEvent(new w.KeyboardEvent("keydown", {key: "Escape", bubbles: true, cancelable: true}));
  assert.equal(ed.getText(),
    "intro\n\n| 名前 | 年齢 |     |\n| :--- | ---: | --- |\n| x    | y    | z   |\n| r    | s    |     |\noutro\n");

  // 行が足りなければ、末尾に足す (最後の行で 2 行分)
  assert.equal(w.GGText.mdTableOverlay(ed.view), true);
  const ov3 = w.document.querySelector(".gg-md-table-overlay");
  ov3.dispatchEvent(new w.KeyboardEvent("keydown", {key: "Enter", bubbles: true, cancelable: true}));  // 最後の行 (r) へ
  const ev3 = new w.Event("paste", {bubbles: true, cancelable: true});
  ev3.clipboardData = {getData: () => "n1\tn2\nm1\tm2"};
  ov3.dispatchEvent(ev3);
  assert.equal(ov3.querySelectorAll("tbody tr").length, 4);
  ov3.dispatchEvent(new w.KeyboardEvent("keydown", {key: "Escape", bubbles: true, cancelable: true}));
  assert.equal(ed.getText(),
    "intro\n\n| 名前 | 年齢 |     |\n| :--- | ---: | --- |\n| x    | y    | z   |\n| n1   | n2   |     |\n| m1   | m2   |     |\noutro\n");
});

test("copying a selection across cells puts tab separated text on the clipboard", () => {
  const w = load();
  const ed = editor(w, DOC);
  inCell(ed, 5);  // 「秋子」のセルの中
  assert.equal(w.GGText.mdTableOverlay(ed.view), true);
  const ov = w.document.querySelector(".gg-md-table-overlay");
  const cells = ov.querySelectorAll("tbody td");
  const copyEvent = () => {
    const ev = new w.Event("copy", {bubbles: true, cancelable: true});
    const store = {};
    ev.clipboardData = {setData: (t, v) => { store[t] = v; }, get text() { return store["text/plain"]; }};
    return ev;
  };
  const select = (a, ao, b, bo) => {
    const r = w.document.createRange();
    r.setStart(a, ao);
    r.setEnd(b, bo);
    const s = w.getSelection();
    s.removeAllRanges();
    s.addRange(r);
  };
  // 1 つのセルの中の選択は、ふつうのコピー (妨げない)
  select(cells[2].firstChild, 0, cells[2].firstChild, 1);
  const ev1 = copyEvent();
  ov.dispatchEvent(ev1);
  assert.equal(ev1.defaultPrevented, false);
  // 「秋子」から「8」まで (2 × 2) を選ぶと、タブ区切りのテキストになる
  select(cells[2].firstChild, 0, cells[5].firstChild, 1);
  const ev2 = copyEvent();
  ov.dispatchEvent(ev2);
  assert.equal(ev2.defaultPrevented, true);
  assert.equal(ev2.clipboardData.text, "秋子\t32\n太郎\t8");
});

test("dragging the mouse across cells selects them (and the drag copy works)", () => {
  const w = load();
  const ed = editor(w, DOC);
  inCell(ed, 5);
  assert.equal(w.GGText.mdTableOverlay(ed.view), true);
  const ov = w.document.querySelector(".gg-md-table-overlay");
  const cells = ov.querySelectorAll("tbody td");
  const md = (target, opts = {}) => new w.MouseEvent("mousedown", {bubbles: true, cancelable: true, button: 0, ...opts});
  // 「秋子」で押して、「8」までドラッグすると、セルをまたぐ選択になる
  cells[2].dispatchEvent(md(cells[2]));
  assert.equal(w.document.activeElement, cells[2], "押したセルにフォーカスがある");
  cells[5].dispatchEvent(new w.MouseEvent("mousemove", {bubbles: true}));
  const sel = w.getSelection();
  assert.equal(sel.rangeCount, 1);
  const r = sel.getRangeAt(0);
  assert.equal(r.collapsed, false, "選択がつぶれていない");
  assert.ok(cells[2].contains(r.startContainer) && cells[5].contains(r.endContainer), "「秋子」から「8」まで");
  // そのままコピーすると、タブ区切りのテキストになる
  const ev = new w.Event("copy", {bubbles: true, cancelable: true});
  const store = {};
  ev.clipboardData = {setData: (t, v) => { store[t] = v; }, get text() { return store["text/plain"]; }};
  ov.dispatchEvent(ev);
  assert.equal(ev.defaultPrevented, true);
  assert.equal(ev.clipboardData.text, "秋子\t32\n太郎\t8");
  // 放したあとは、ドラッグを始めたセルのまま (キャレットはつぶれていない選択を保つ)
  w.document.dispatchEvent(new w.MouseEvent("mouseup", {bubbles: true}));
  assert.equal(sel.rangeCount, 1);
  assert.equal(sel.getRangeAt(0).collapsed, false);
  // 単にクリックしただけのセルは、キャレットが置かれた状態になる (ふつうに編集できる)
  cells[4].dispatchEvent(md(cells[4]));
  w.document.dispatchEvent(new w.MouseEvent("mouseup", {bubbles: true}));
  assert.equal(w.document.activeElement, cells[4]);
  const s2 = w.getSelection();
  assert.equal(s2.rangeCount, 1);
  assert.equal(s2.getRangeAt(0).collapsed, true, "クリックはキャレット");
});

test("the overlay always has the same size at the center of the screen (the cursor position and the table size do not change it)", () => {
  const w = load();
  const ed = editor(w, DOC);
  inCell(ed, 5);  // 表の中の下のほうの行
  assert.equal(w.GGText.mdTableOverlay(ed.view), true);
  const ov = w.document.querySelector(".gg-md-table-overlay");
  assert.ok(ov.style.width && ov.style.height, "大きさが決まっている: " + ov.style.width + " × " + ov.style.height);
  assert.equal(ov.style.left, Math.floor((w.innerWidth - parseInt(ov.style.width)) / 2) + "px", "画面の真ん中 (横)");
  assert.equal(ov.style.top, Math.floor((w.innerHeight - parseInt(ov.style.height)) / 2) + "px", "画面の真ん中 (縦)");
  const size = [ov.style.left, ov.style.top, ov.style.width, ov.style.height];
  ov.dispatchEvent(new w.KeyboardEvent("keydown", {key: "Escape", bubbles: true, cancelable: true}));
  ed.view.dispatch({selection: {anchor: ed.view.state.doc.line(3).from + 3}});  // 見出し (表の中の上のほう)
  assert.equal(w.GGText.mdTableOverlay(ed.view), true);
  const ov2 = w.document.querySelector(".gg-md-table-overlay");
  assert.deepEqual([ov2.style.left, ov2.style.top, ov2.style.width, ov2.style.height], size, "どこで開いても、大きさも場所も同じ");
});

test("the overlay's up / down keys use the wrapped visual lines of tall cells", () => {
  const w = load();
  const long = "あいうえおかきくけこさしすせそ";  // 15 文字
  const ed = editor(w, "intro\n\n| 名前 | 備考 |\n| --- | --- |\n| 秋子 | " + long + " |\n| 太郎 | 8    |\noutro\n");
  inCell(ed, 5);
  assert.equal(w.GGText.mdTableOverlay(ed.view), true);
  const ov = w.document.querySelector(".gg-md-table-overlay");
  const cell = ov.querySelectorAll("tbody tr")[1].children[1];  // 長いセル (5 文字ごとに折り返す → 3 行)
  fakeWrap(w, cell, 5, 16);
  const key = name => ov.dispatchEvent(new w.KeyboardEvent("keydown", {key: name, bubbles: true, cancelable: true}));
  const focused = () => w.document.activeElement.textContent;
  // 折り返しの途中 (2 行目) では、上下ともセルの中にとどまる
  caretAt(w, cell, 6);
  key("ArrowUp");
  assert.equal(focused(), long, "2 行目で ↑ は動かない");
  key("ArrowDown");
  assert.equal(focused(), long, "2 行目で ↓ は動かない");
  // 1 行目でも ↓ は動かない (折り返しの続きがある)。最後の行では ↓ が、下のセル (太郎の「8」) へ動く
  caretAt(w, cell, 0);
  key("ArrowDown");
  assert.equal(focused(), long, "1 行目で ↓ は動かない");
  caretAt(w, cell, 15);
  key("ArrowUp");
  assert.equal(focused(), long, "最後の行で ↑ は動かない");
  key("ArrowDown");
  assert.equal(focused(), "8", "下の端 → 下のセルへ");
  // 1 行目の先頭で ↑ は、上のセル (見出しの「備考」) へ
  caretAt(w, cell, 0);
  key("ArrowUp");
  assert.equal(focused(), "備考");
});

test("the overlay's arrow keys move between cells at the cell edges", () => {
  const w = load();
  const ed = editor(w, DOC);
  inCell(ed, 5);  // 「秋子」のセルの中 (キャレットは先頭)
  assert.equal(w.GGText.mdTableOverlay(ed.view), true);
  const ov = w.document.querySelector(".gg-md-table-overlay");
  const key = name => ov.dispatchEvent(new w.KeyboardEvent("keydown", {key: name, bubbles: true, cancelable: true}));
  const focused = () => w.document.activeElement.textContent;
  // 開いた直後、カーソルは「秋子」(1 つ目の列) の先頭。左端のセルの先頭で ← は動かない
  key("ArrowLeft");
  assert.equal(focused(), "秋子");
  // Tab で「32」へ動って、その先頭で ← : 前のセル (秋子) へ
  key("Tab");
  assert.equal(focused(), "32");
  key("ArrowLeft");
  assert.equal(focused(), "秋子");
  // → : セルの先頭では動かない。末尾なら、次のセル (32) へ
  key("ArrowRight");
  assert.equal(focused(), "秋子");
  caretEnd(w, w.document.activeElement);
  key("ArrowRight");
  assert.equal(focused(), "32");
  // ↓ : 下のセル (太郎の「8」) へ。↑ : 上へ戻る (さらに ↑ で見出しの「年齢」へ。そこからは動かない)
  key("ArrowDown");
  assert.equal(focused(), "8");
  key("ArrowUp");
  assert.equal(focused(), "32");
  key("ArrowUp");
  assert.equal(focused(), "年齢");
  key("ArrowUp");
  assert.equal(focused(), "年齢");
});

test("the overlay's Ctrl+Z / Ctrl+Y undo and redo cell edits and structural changes", () => {
  const w = load();
  const ed = editor(w, DOC);
  inCell(ed, 5);  // 「秋子」のセルの中
  assert.equal(w.GGText.mdTableOverlay(ed.view), true);
  const ov = w.document.querySelector(".gg-md-table-overlay");
  const cellOfRow1 = () => ov.querySelectorAll("tbody tr")[1].children[0];
  cellOfRow1().textContent = "あきこ";
  ov.dispatchEvent(new w.Event("input", {bubbles: true}));
  ov.dispatchEvent(new w.KeyboardEvent("keydown", {key: "z", ctrlKey: true, bubbles: true, cancelable: true}));
  assert.equal(cellOfRow1().textContent, "秋子", "セルの入力を戻す");
  ov.dispatchEvent(new w.KeyboardEvent("keydown", {key: "y", ctrlKey: true, bubbles: true, cancelable: true}));
  assert.equal(cellOfRow1().textContent, "あきこ", "やり直す");
  // 行の追加も戻せる
  ov.querySelectorAll("button")[1].click();  // ↓ 行
  assert.equal(ov.querySelectorAll("tbody tr").length, 4);
  ov.dispatchEvent(new w.KeyboardEvent("keydown", {key: "z", ctrlKey: true, bubbles: true, cancelable: true}));
  assert.equal(ov.querySelectorAll("tbody tr").length, 3, "行の追加を戻す");
  ov.dispatchEvent(new w.KeyboardEvent("keydown", {key: "z", ctrlKey: true, bubbles: true, cancelable: true}));
  assert.equal(cellOfRow1().textContent, "秋子", "さらに戻ると、セルの入力も戻る");
  ov.dispatchEvent(new w.KeyboardEvent("keydown", {key: "y", ctrlKey: true, bubbles: true, cancelable: true}));
  assert.equal(cellOfRow1().textContent, "あきこ", "やり直すと、セルの入力も戻る");
  ov.dispatchEvent(new w.KeyboardEvent("keydown", {key: "Escape", bubbles: true, cancelable: true}));
  assert.ok(ed.getText().includes("| あきこ"), "書き戻し: " + ed.getText().split("\n")[4]);
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
