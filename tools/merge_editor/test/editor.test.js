// バンドルしたエディタを jsdom に読み込んで、競合ごとの採用ボタンが働くことを確認する。
import test, {after} from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import {JSDOM} from "jsdom";

const MERGE_BUNDLE = new URL("../../../git_graph/static/vendor/merge_editor.js", import.meta.url);
const TEXT_BUNDLE = new URL("../../../git_graph/static/vendor/text_editor.js", import.meta.url);
const TEXT = "a\n<<<<<<< HEAD\nO\n||||||| base\nB\n=======\nT\n>>>>>>> feature\nz\n<<<<<<< HEAD\nO2\n=======\nT2\n>>>>>>> feature\n";

const windows = [];
after(() => { windows.forEach(w => w.close()); });  // 閉じないと、jsdom の描画のタイマーが残って、テストが終わらない

function load(bundle = MERGE_BUNDLE) {
  const dom = new JSDOM("<!DOCTYPE html><div id='top'></div><div id='result'></div>", {runScripts: "outside-only", pretendToBeVisual: true});
  const w = dom.window;
  // jsdom に無い、レイアウトの API (CodeMirror が測定に使う) を空の値で補う
  const rect = {left: 0, right: 0, top: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0};
  w.Range.prototype.getClientRects = () => [];
  w.Range.prototype.getBoundingClientRect = () => rect;
  w.Element.prototype.getClientRects = () => [];
  w.document.elementFromPoint = () => null;
  w.eval(fs.readFileSync(bundle, "utf8"));
  windows.push(w);
  return w;
}

test("per-conflict buttons resolve the block and the count follows", () => {
  const w = load();
  const counts = [];
  const ed = w.GGMerge.createMergeEditor({
    top: w.document.getElementById("top"), result: w.document.getElementById("result"),
    ours: "ours\n", theirs: "theirs\n", text: TEXT,
    labels: {ours: "OURS", theirs: "THEIRS", both: "BOTH", both_rev: "BOTHREV", base: "BASE"},
    onChange: n => counts.push(n),
  });
  assert.equal(ed.conflictCount(), 2);
  const bars = [...w.document.querySelectorAll(".cm-conflict-bar")];
  assert.equal(bars.length, 2);
  const names = bars.map(b => [...b.querySelectorAll("button")].map(x => x.textContent).join(","));
  assert.deepEqual(names, ["OURS,THEIRS,BOTH,BOTHREV,BASE", "OURS,THEIRS,BOTH,BOTHREV"]);  // base が無い競合に「ベース」は出ない
  bars[0].querySelector("[data-choice=theirs]").click();
  assert.equal(ed.getText(), "a\nT\nz\n<<<<<<< HEAD\nO2\n=======\nT2\n>>>>>>> feature\n");
  assert.equal(ed.conflictCount(), 1);
  assert.equal(counts.at(-1), 1);
  assert.equal(w.document.querySelectorAll(".cm-conflict-bar").length, 1);
  assert.ok(w.document.querySelector(".cm-conf-ours"));
  ed.resolveAll("ours");
  assert.equal(ed.getText(), "a\nT\nz\nO2\n");
  assert.equal(ed.conflictCount(), 0);
  assert.equal(w.document.querySelectorAll(".cm-conflict-bar").length, 0);
  ed.setText(TEXT);
  assert.equal(ed.conflictCount(), 2);
  ed.destroy();
});

test("the top MergeView shows both sides read-only", () => {
  const w = load();
  const ed = w.GGMerge.createMergeEditor({
    top: w.document.getElementById("top"), result: w.document.getElementById("result"),
    ours: "same\nours only\n", theirs: "same\ntheirs only\n", text: "x\n", labels: {},
  });
  const panes = w.document.querySelectorAll("#top .cm-editor");
  assert.equal(panes.length, 2);
  assert.equal(ed.topView.a.state.doc.toString(), "same\nours only\n");
  assert.equal(ed.topView.b.state.doc.toString(), "same\ntheirs only\n");
  assert.equal(ed.topView.a.state.readOnly, true);
  ed.destroy();
});

test("the plain text editor (file edit): text, change/save callbacks, Tab and search phrases", () => {
  const w = load(TEXT_BUNDLE);
  let changes = 0, saves = 0;
  const ed = w.GGText.createTextEditor({
    parent: w.document.getElementById("result"), text: "a\r\nb\r\n", path: "notes.txt", phrases: {Find: "検索"},
    onChange: () => { changes++; }, onSave: () => { saves++; },
  });
  assert.equal(ed.getText(), "a\nb\n");  // 改行は LF にそろう (保存時に元の改行コードへ戻すのは呼び出し側)
  assert.equal(changes, 0);               // 最初の内容を入れただけでは、変更扱いにしない
  const key = (name, extra = {}) => {
    const ev = new w.KeyboardEvent("keydown", {key: name, bubbles: true, cancelable: true, ...extra});
    w.document.querySelector(".cm-content").dispatchEvent(ev);
    return ev;
  };
  const save = key("s", {ctrlKey: true});
  assert.equal(saves, 1);
  assert.equal(save.defaultPrevented, true);  // ブラウザーの「ページを保存」を出さない
  ed.view.dispatch({selection: {anchor: 0}});
  key("Tab");
  assert.equal(ed.getText(), "\ta\nb\n");     // Tab はタブ文字を入れる
  assert.equal(changes, 1);
  ed.setText("x\n");
  assert.equal(ed.getText(), "x\n");
  assert.equal(changes, 2);
  assert.equal(w.document.querySelectorAll(".cm-gutters").length, 1);
  assert.equal(ed.language, "");                                       // .txt は言語なし
  assert.equal(w.document.querySelector(".cm-lineWrapping"), null);     // 編集は折り返さない
  assert.equal(w.document.querySelectorAll(".cm-line span").length, 0);  // ハイライトの span は付かない
  assert.equal(ed.view.state.phrase("Find"), "検索");
  assert.equal(w.document.querySelector(".cm-vim-panel"), null);       // Vim モードは既定ではオフ
  ed.destroy();
  assert.equal(w.document.querySelectorAll(".cm-editor").length, 0);
});

// 閲覧画面 (files.html の languageFor) が対応している言語の、拡張子や名前の例
const SAMPLES = {
  "a.py": ["python", "def f():\n    return 1  # c\n"], "a.js": ["javascript", "const a = 1; // c\n"], "a.jsx": ["javascript", "const a = <b/>;\n"],
  "a.ts": ["typescript", "let a: number = 1;\n"], "a.tsx": ["typescript", "let a = <b/>;\n"], "A.java": ["java", "class A { int a; }\n"],
  "a.c": ["c", "int main(void) { return 0; }\n"], "a.hpp": ["c", "class A {};\n"], "a.go": ["go", "package main\n"],
  "a.rs": ["rust", "fn main() {}\n"], "a.rb": ["ruby", "def a\n  1\nend\n"], "a.sql": ["sql", "select 1 from t;\n"],
  "a.sh": ["shell", "echo $A # c\n"], "Dockerfile": ["shell", "FROM x # c\n"], "Makefile": ["shell", "a: b\n\techo $@\n"],
  "a.json": ["json", "{\"a\": [1, true]}\n"], "a.css": ["css", "a { color: red; }\n"], "a.html": ["html", "<p class=\"a\">x</p>\n"],
  "a.xml": ["html", "<a b=\"c\"/>\n"], "a.md": ["markdown", "# t\n\n*a*\n"], "a.yaml": ["yaml", "a: [1, true]\n"],
};
test("syntax highlighting for every language the viewer supports", () => {
  const w = load(TEXT_BUNDLE);
  for (const [path, [lang, text]] of Object.entries(SAMPLES)) {
    w.document.getElementById("result").replaceChildren();
    const ed = w.GGText.createTextEditor({parent: w.document.getElementById("result"), text: text, path: path});
    assert.equal(ed.language, lang, path);
    assert.ok(w.document.querySelectorAll(".cm-line span").length > 0, path + ": highlighted");
    ed.destroy();
  }
});

test("Vim mode: off by default, can be switched on and off, :w / :q / :q! call back", () => {
  const w = load(TEXT_BUNDLE);
  const calls = [];
  const ed = w.GGText.createTextEditor({
    parent: w.document.getElementById("result"), text: "abc\ndef\n", vim: true,
    onSave: () => calls.push("save"), onQuit: force => calls.push(force ? "quit!" : "quit"),
  });
  assert.ok(w.document.querySelector(".cm-vim-panel"), "status panel");
  const cm = w.GGText.getCM(ed.view);
  assert.ok(cm);
  const run = command => w.GGText.Vim.handleEx(cm, command);
  run("w"); run("wq"); run("x"); run("q"); run("q!");
  assert.deepEqual(calls, ["save", "save", "save", "quit", "quit!"]);
  const press = key => w.GGText.Vim.handleKey(cm, key, "user");
  ed.view.dispatch({selection: {anchor: 0}});
  press("x");                               // 標準モードの x は 1 文字削除
  assert.equal(ed.getText(), "bc\ndef\n");
  press("d"); press("d");                   // dd は行を削除
  assert.equal(ed.getText(), "def\n");
  press("u");                               // u でアンドゥ
  assert.equal(ed.getText(), "bc\ndef\n");
  ed.setVim(false);
  assert.equal(w.document.querySelector(".cm-vim-panel"), null);
  ed.setVim(true);
  assert.ok(w.document.querySelector(".cm-vim-panel"));
  ed.destroy();
});

test("read-only viewer (file view): shows the text, blocks typing, still allows selecting and searching, jumps to a line", () => {
  const w = load(TEXT_BUNDLE);
  let changes = 0, saves = 0;
  const ed = w.GGText.createTextEditor({
    parent: w.document.getElementById("result"), text: "def f():\n    return 1\nx = 2\n", path: "a.py", readOnly: true,
    onChange: () => { changes++; }, onSave: () => { saves++; },
  });
  assert.equal(ed.language, "python");
  assert.ok(w.document.querySelectorAll(".cm-line span").length > 0, "highlighted");
  assert.equal(w.document.querySelectorAll(".cm-gutters").length, 1);
  assert.equal(ed.view.state.readOnly, true);
  assert.equal(w.document.querySelector(".cm-activeLine"), null);  // 表示では、カーソル行を強調しない
  assert.ok(w.document.querySelector(".cm-lineWrapping"), "view wraps long lines");
  ed.view.dispatch({selection: {anchor: 0}});
  const ev = new w.KeyboardEvent("keydown", {key: "Tab", bubbles: true, cancelable: true});
  w.document.querySelector(".cm-content").dispatchEvent(ev);
  assert.equal(ed.getText(), "def f():\n    return 1\nx = 2");  // Tab でも書き換わらない。末尾の改行は、空の行として出さない
  assert.equal(changes, 0);
  ed.scrollToLine(2);
  const sel = ed.view.state.selection.main;
  assert.equal(ed.view.state.sliceDoc(sel.from, sel.to), "    return 1");  // 2 行目を選択する
  ed.scrollToLine(999);  // 行数を超えたら最後の行
  assert.equal(ed.view.state.doc.lineAt(ed.view.state.selection.main.from).number, 3);
  assert.equal(ed.view.state.doc.lines, 3);
  ed.scrollToLine(0);    // 0 以下なら先頭の行
  assert.equal(ed.view.state.doc.lineAt(ed.view.state.selection.main.from).number, 1);
  assert.equal(typeof ed.getScrollTop(), "number");
  ed.setScrollTop(0);
  ed.destroy();
});

test("read-only viewer: trailing newline is not shown as an extra empty line (LF and CRLF), empty file is one empty line", () => {
  const w = load(TEXT_BUNDLE);
  const lines = text => {
    w.document.getElementById("result").replaceChildren();
    const ed = w.GGText.createTextEditor({parent: w.document.getElementById("result"), text: text, path: "a.txt", readOnly: true});
    const n = ed.view.state.doc.lines;
    ed.destroy();
    return n;
  };
  assert.equal(lines("a\nb\n"), 2);
  assert.equal(lines("a\r\nb\r\n"), 2);
  assert.equal(lines("a\nb"), 2);
  assert.equal(lines("a\n\n"), 2);  // 末尾の空行は 1 つだけ残る
  assert.equal(lines("\n"), 1);
  assert.equal(lines(""), 1);
});

// ---- ベース編集方式: ours | 結果 と theirs | 結果 の 2 組 (結果は 1 つの内容で、競合マーカー入り) ----
const OURS = "one\nTWO main\nthree\nfour\nfive\nSIX main\n";
const THEIRS = "one\ntwo\nthree\nFOUR side\nfive\nSIX side\n";
// 競合しない変更 (2 行目の ours・4 行目の theirs) は取り込み済みで、6 行目だけが競合している
const MERGED = "one\nTWO main\nthree\nFOUR side\nfive\n<<<<<<< ours\nSIX main\n||||||| base\nsix\n=======\nSIX side\n>>>>>>> theirs\n";
function baseEditor(w, extra = {}) {
  const counts = [];
  const ed = w.GGMerge.createBaseMergeEditor({
    oursParent: w.document.getElementById("top"), theirsParent: w.document.getElementById("result"),
    ours: OURS, theirs: THEIRS, text: MERGED, labels: {revert: "取り込む"}, onChange: n => counts.push(n), ...extra,
  });
  return {ed, counts};
}
const frames = async (w, n = 2) => { for (let i = 0; i < n; i++) await new Promise(resolve => w.requestAnimationFrame(() => setTimeout(resolve, 20))); };
const press = (w, view, name, extra = {}) => {
  const ev = new w.KeyboardEvent("keydown", {key: name, bubbles: true, cancelable: true, ...extra});
  view.contentDOM.dispatchEvent(ev);
  return ev;
};

test("base editor: two pairs, the result (with conflict markers) in both, conflicts highlighted without per-conflict bars", () => {
  const w = load();
  const {ed, counts} = baseEditor(w);
  assert.equal(w.document.querySelectorAll("#top .cm-editor").length, 2);     // ours | 結果
  assert.equal(w.document.querySelectorAll("#result .cm-editor").length, 2);  // theirs | 結果
  assert.equal(ed.oursView.a.state.doc.toString(), OURS);
  assert.equal(ed.theirsView.a.state.doc.toString(), THEIRS);
  assert.equal(ed.oursView.a.state.readOnly, true);
  assert.equal(ed.baseA.state.readOnly, false);
  assert.equal(ed.getText(), MERGED);                                       // 結果の初めの内容は、渡した (競合マーカー入りの) 内容
  assert.equal(ed.baseB.state.doc.toString(), MERGED);
  assert.equal(ed.conflictCount(), 1);
  assert.equal(counts.at(0), 1);
  // 競合の範囲は、両方の結果のペインで赤っぽく強調する (行ごとのクラス)
  for (const parent of ["#top", "#result"]) {
    assert.equal(w.document.querySelectorAll(parent + " .cm-conf").length, 7, parent);          // マーカー 4 行 + ours / base / theirs が 1 行ずつ
    assert.equal(w.document.querySelectorAll(parent + " .cm-conf-marker").length, 4, parent);
    assert.equal(w.document.querySelectorAll(parent + " .cm-conf-ours, " + parent + " .cm-conf-theirs, " + parent + " .cm-conf-base").length, 3, parent);
  }
  assert.equal(w.document.querySelector(".cm-conflict-bar"), null);          // 採用ボタンの帯は出さない (左右の行の高さがずれるため)
  ed.destroy();
});

test("base editor: edits in either result are mirrored, the conflict count follows, no echo loop", () => {
  const w = load();
  const {ed, counts} = baseEditor(w);
  const same = () => ed.baseA.state.doc.toString() === ed.baseB.state.doc.toString();
  ed.baseA.dispatch({changes: {from: 0, insert: "HEAD\n"}});
  assert.ok(same());
  ed.baseB.dispatch({changes: {from: ed.baseB.state.doc.length, insert: "TAIL\n"}});
  assert.ok(same());
  assert.ok(ed.baseA.state.doc.toString().endsWith("TAIL\n"));
  assert.equal(ed.conflictCount(), 1);
  // マーカーの行を消すと、競合ではなくなる (両方のペインの強調も消える)
  const doc = ed.baseB.state.doc.toString(), at = doc.indexOf("<<<<<<< ours");
  ed.baseB.dispatch({changes: {from: at, to: at + "<<<<<<< ours\n".length}});
  assert.ok(same());
  assert.equal(ed.conflictCount(), 0);
  assert.equal(counts.at(-1), 0);
  assert.equal(w.document.querySelectorAll(".cm-conf").length, 0);
  const before = counts.length;
  ed.baseA.dispatch({changes: {from: 0, insert: "X"}});
  assert.ok(counts.length - before <= 2, "notifications: " + (counts.length - before));
  ed.destroy();
});

test("base editor: one undo history for both results (typing in either is undone in order)", () => {
  const w = load();
  const {ed} = baseEditor(w);
  // 近い位置の続けた入力は、CodeMirror が 1 回のアンドゥにまとめるので、離れた位置を使う
  ed.baseA.dispatch({changes: {from: 0, insert: "A1\n"}});
  ed.baseB.dispatch({changes: {from: ed.baseB.state.doc.length, insert: "B1\n"}});
  assert.equal(ed.getText(), "A1\n" + MERGED + "B1\n");
  press(w, ed.baseB, "z", {ctrlKey: true});                                // baseB で Ctrl+Z → 最後の編集 (baseB の入力) を戻す
  assert.equal(ed.getText(), "A1\n" + MERGED);
  press(w, ed.baseA, "z", {ctrlKey: true});
  assert.equal(ed.getText(), MERGED);
  assert.equal(ed.baseA.state.doc.toString(), ed.baseB.state.doc.toString());
  press(w, ed.baseB, "y", {ctrlKey: true});                                // baseB で Ctrl+Y → やり直し
  assert.equal(ed.getText(), "A1\n" + MERGED);
  press(w, ed.baseA, "y", {ctrlKey: true});
  assert.equal(ed.getText(), "A1\n" + MERGED + "B1\n");
  ed.destroy();
});

test("base editor: adopt resolves the conflict at the cursor; outside a conflict it only jumps; jump wraps", () => {
  const w = load();
  const {ed} = baseEditor(w);
  ed.baseA.dispatch({selection: {anchor: 0}});
  assert.equal(ed.adopt("ours"), false);                                   // カーソルは競合の外 → 次の競合へ移動するだけ
  assert.equal(ed.getText(), MERGED);
  const head = ed.baseA.state.selection.main.head;
  assert.equal(ed.baseA.state.doc.lineAt(head).text, "<<<<<<< ours");
  assert.equal(ed.adopt("both"), true);                                    // 競合の中 → 両方 (ours → theirs)
  assert.equal(ed.getText(), "one\nTWO main\nthree\nFOUR side\nfive\nSIX main\nSIX side\n");
  assert.equal(ed.baseB.state.doc.toString(), ed.getText());               // もう一方の結果にも反映
  assert.equal(ed.conflictCount(), 0);
  ed.setText(MERGED);
  ed.jump(1);
  assert.equal(ed.adopt("theirs"), true);
  assert.ok(ed.getText().endsWith("five\nSIX side\n"));
  ed.setText(MERGED);
  ed.jump(-1);                                                             // 前の競合 (1 つだけなので、同じ競合)
  assert.equal(ed.adopt("both_rev"), true);
  assert.ok(ed.getText().endsWith("five\nSIX side\nSIX main\n"));
  ed.setText(MERGED);
  ed.resolveAll("ours");
  assert.equal(ed.getText(), "one\nTWO main\nthree\nFOUR side\nfive\nSIX main\n");
  ed.destroy();
});

test("base editor: arrows are red for conflicts (one per conflict) and resolve the whole conflict; other arrows revert the difference", async () => {
  const w = load();
  const {ed} = baseEditor(w);
  await frames(w);
  const all = parent => [...w.document.querySelectorAll(parent + " .cm-merge-revert button")];
  const visible = parent => all(parent).filter(b => b.style.display !== "none");
  const oursArrows = visible("#top");
  // ours | 結果: 結果にある theirs の変更 (4 行目) の差分と、競合 (1 つ。複数の差分に分かれていても、矢印は 1 つ)
  assert.equal(oursArrows.length, 2, "visible ours arrows: " + oursArrows.length);
  assert.equal(oursArrows.filter(b => b.classList.contains("gg-conflict")).length, 1);
  assert.equal(all("#top").length, 3, "the conflict is split into two differences, one arrow is hidden");
  assert.equal(oursArrows[0].getAttribute("title"), "取り込む");
  assert.equal(oursArrows[0].textContent, "\u2192");
  // 競合の矢印 → 競合全体を ours で解決する
  oursArrows.find(b => b.classList.contains("gg-conflict")).dispatchEvent(new w.MouseEvent("mousedown", {bubbles: true, cancelable: true}));
  assert.equal(ed.getText(), "one\nTWO main\nthree\nFOUR side\nfive\nSIX main\n");
  assert.equal(ed.baseB.state.doc.toString(), ed.getText());
  assert.equal(ed.conflictCount(), 0);
  await frames(w);
  // theirs | 結果 の、競合ではない差分 (2 行目: 結果にある ours の変更) の矢印 → その差分だけ theirs の内容にする (ライブラリの動作)
  const theirsArrows = visible("#result");
  assert.ok(theirsArrows.length >= 1);
  assert.ok(theirsArrows.every(b => !b.classList.contains("gg-conflict")));
  theirsArrows[0].dispatchEvent(new w.MouseEvent("mousedown", {bubbles: true, cancelable: true}));
  assert.equal(ed.getText(), "one\ntwo\nthree\nFOUR side\nfive\nSIX main\n");
  assert.equal(ed.baseA.state.doc.toString(), ed.baseB.state.doc.toString());
  ed.destroy();
});

test("base editor: the arrow of the theirs pair resolves the conflict with theirs", async () => {
  const w = load();
  const {ed} = baseEditor(w);
  await frames(w);
  const red = [...w.document.querySelectorAll("#result .cm-merge-revert button")].filter(b => b.style.display !== "none" && b.classList.contains("gg-conflict"));
  assert.equal(red.length, 1);
  red[0].dispatchEvent(new w.MouseEvent("mousedown", {bubbles: true, cancelable: true}));
  assert.equal(ed.getText(), "one\nTWO main\nthree\nFOUR side\nfive\nSIX side\n");
  ed.destroy();
});

test("base editor: jumpDiff moves between differences; setText replaces both results (undoable)", () => {
  const w = load();
  const {ed} = baseEditor(w);
  ed.baseA.focus();
  assert.equal(ed.jumpDiff(1), true);
  assert.equal(ed.jumpDiff(-1), true);
  ed.setText(OURS);
  assert.equal(ed.baseB.state.doc.toString(), OURS);
  assert.equal(ed.conflictCount(), 0);
  press(w, ed.baseB, "z", {ctrlKey: true});
  assert.equal(ed.getText(), MERGED);
  ed.destroy();
});

test("marker editor: adopt resolves the conflict at the cursor too, and conflicts are highlighted with the red classes", () => {
  const w = load();
  const ed = w.GGMerge.createMergeEditor({
    top: w.document.getElementById("top"), result: w.document.getElementById("result"),
    ours: OURS, theirs: THEIRS, text: MERGED, labels: {ours: "O", theirs: "T", both: "B", both_rev: "R", base: "S", keys: {ours: "Alt+\u2192"}},
  });
  assert.equal(w.document.querySelectorAll("#result .cm-conf").length, 7);
  assert.equal(w.document.querySelector("[data-choice=ours]").getAttribute("title"), "Alt+\u2192");   // ボタンの説明にショートカットキー
  ed.view.dispatch({selection: {anchor: 0}});
  assert.equal(ed.adopt("theirs"), false);   // 外 → 移動だけ
  assert.equal(ed.getText(), MERGED);
  assert.equal(ed.adopt("theirs"), true);
  assert.equal(ed.getText(), "one\nTWO main\nthree\nFOUR side\nfive\nSIX side\n");
  ed.destroy();
});

test("diff editor: old | new side by side, read-only, changed lines marked, highlight by file name", () => {
  const w = load(TEXT_BUNDLE);
  const ed = w.GGText.createDiffEditor({
    parent: w.document.getElementById("top"), old: "a = 1\nb = 2\nc = 3\n", new: "a = 1\nb = 20\nc = 3\nd = 4\n", path: "x.py", collapse: false,
  });
  const [left, right] = [ed.view.a, ed.view.b];
  assert.equal(left.state.doc.toString(), "a = 1\nb = 2\nc = 3\n");
  assert.equal(right.state.doc.toString(), "a = 1\nb = 20\nc = 3\nd = 4\n");
  assert.equal(ed.language, "python");
  assert.equal(left.state.readOnly && right.state.readOnly, true);
  assert.equal(w.document.querySelectorAll(".cm-mergeViewEditor").length, 2);
  assert.ok(w.document.querySelectorAll(".cm-merge-a .cm-changedLine").length >= 1);   // 変更前側の変更された行
  assert.ok(w.document.querySelectorAll(".cm-merge-b .cm-changedLine").length >= 2);   // 変更後側 (変更した行と追加した行)
  assert.equal(ed.chunkCount(), 2);                                                     // 2 行目の変更と、4 行目の追加
  ed.destroy();
});

test("diff editor: jump moves between changes and wraps around; no changes → false", () => {
  const w = load(TEXT_BUNDLE);
  const old = Array.from({length: 40}, (_, i) => "line " + i).join("\n") + "\n";
  const lines = old.split("\n");
  lines[3] = "CHANGED 3"; lines[30] = "CHANGED 30";
  const ed = w.GGText.createDiffEditor({parent: w.document.getElementById("top"), old: old, new: lines.join("\n"), path: "x.txt", collapse: true});
  assert.equal(ed.chunkCount(), 2);
  const lineOfCursor = () => ed.view.b.state.doc.lineAt(ed.view.b.state.selection.main.head).number;
  assert.equal(ed.jump(1), true);
  assert.equal(lineOfCursor(), 4);
  assert.equal(ed.jump(1), true);
  assert.equal(lineOfCursor(), 31);
  assert.equal(ed.jump(1), true, "wraps to the first change");
  assert.equal(lineOfCursor(), 4);
  assert.equal(ed.jump(-1), true, "wraps to the last change");
  assert.equal(lineOfCursor(), 31);
  ed.destroy();
  const same = w.GGText.createDiffEditor({parent: w.document.getElementById("result"), old: "a\n", new: "a\n", path: "x.txt", collapse: false});
  assert.equal(same.chunkCount(), 0);
  assert.equal(same.jump(1), false);
  same.destroy();
});

test("diff editor: collapse folds long unchanged runs; added / deleted files are all-changed; CRLF and LF compare equal", () => {
  const w = load(TEXT_BUNDLE);
  const old = Array.from({length: 60}, (_, i) => "line " + i).join("\n") + "\n";
  const changed = old.replace("line 30\n", "line thirty\n");
  const folded = w.GGText.createDiffEditor({parent: w.document.getElementById("top"), old: old, new: changed, path: "a.txt", collapse: true});
  assert.ok(w.document.querySelectorAll(".cm-collapsedLines").length >= 1);
  folded.destroy();
  const flat = w.GGText.createDiffEditor({parent: w.document.getElementById("top"), old: old, new: changed, path: "a.txt", collapse: false});
  assert.equal(w.document.querySelectorAll(".cm-collapsedLines").length, 0);
  flat.destroy();
  const added = w.GGText.createDiffEditor({parent: w.document.getElementById("result"), old: "", new: "x\ny\n", path: "new.txt", collapse: false});
  assert.equal(added.chunkCount(), 1);
  added.destroy();
  const eol = w.GGText.createDiffEditor({parent: w.document.getElementById("result"), old: "a\r\nb\r\n", new: "a\nb\n", path: "e.txt", collapse: false});
  assert.equal(eol.chunkCount(), 0, "line ending differences are reported by the page, not shown as changes");
  eol.destroy();
});

// ---- 差分エディタの表示 (git_graph/static/diff_editor_view.js: /diff と /files の右ペインで共用) ----
const VIEW_SCRIPT = new URL("../../../git_graph/static/diff_editor_view.js", import.meta.url);
const side = (content, extra = {}) => ({kind: "commit", ref: "abc1234", label: "abc1234", exists: true, content: content, encoding: "UTF-8", binary: false, truncated: false, eol: "lf", ...extra});
const TEXTS = {
  diff_editor_prev: "PREV", diff_editor_next: "NEXT", diff_editor_collapse: "COLLAPSE", diff_editor_chunks: "{n} changes", diff_editor_none: "NONE",
  diff_editor_binary: "BINARY", diff_editor_unavailable: "UNAVAILABLE", diff_editor_eol: "EOL {old} -> {new}", diff_editor_missing: "(missing)", large_file_head: "TRUNCATED",
};

function loadView(answer) {
  const w = load(TEXT_BUNDLE);
  w.eval(fs.readFileSync(VIEW_SCRIPT, "utf8") + "\nwindow.createDiffEditorView = createDiffEditorView;");
  const urls = [];
  w.fetch = url => { urls.push(url); const r = answer(url); return Promise.resolve(r.ok === false ? {ok: false, status: 400, text: () => Promise.resolve(r.message)} : {ok: true, json: () => Promise.resolve(r)}); };
  const view = w.createDiffEditorView({
    box: w.document.getElementById("top"), text: (k, v = {}) => Object.entries(v).reduce((s, [n, x]) => s.replace("{" + n + "}", x), TEXTS[k] || k),
    phrases: () => ({}), loadScript: () => Promise.resolve(),
  });
  return {w, view, urls};
}
const notes = w => [...w.document.querySelectorAll(".de-note")].map(n => n.textContent);

test("diff editor view: loads /api/diff-sources, shows labels, change count and the editor", async () => {
  const {w, view, urls} = loadView(() => ({path: "x.py", old_path: "x.py", old: side("a = 1\nb = 2\n", {label: "Parent 1111111", kind: "parent"}), new: side("a = 1\nb = 3\nc = 4\n")}));
  await view.load({hash: "abc1234", path: "x.py"});
  assert.equal(urls[0], "/api/diff-sources?hash=abc1234&path=x.py");
  assert.equal(w.document.querySelectorAll(".cm-mergeViewEditor").length, 2);
  assert.equal(w.document.querySelector(".de-count").textContent, "1 changes");  // 隣り合う変更は 1 つにまとまる
  assert.deepEqual([...w.document.querySelectorAll(".de-labels span")].map(s => s.textContent), ["Parent 1111111", "abc1234"]);
  assert.equal(w.document.querySelector(".de-labels").hidden, false);
  const [prev, next] = w.document.querySelectorAll(".de-bar button");
  assert.deepEqual([prev.textContent, next.textContent, prev.disabled], ["PREV", "NEXT", false]);
  view.close();
  assert.equal(w.document.querySelectorAll(".cm-mergeViewEditor").length, 0);
  assert.equal(w.document.querySelector(".de-labels").hidden, true);
});

test("diff editor view: collapse checkbox rebuilds; no changes; missing side is marked", async () => {
  const lines = Array.from({length: 60}, (_, i) => "line " + i).join("\n") + "\n";
  const {w, view} = loadView(() => ({path: "x.txt", old_path: "x.txt", old: side(lines), new: side(lines.replace("line 30\n", "changed\n"))}));
  await view.load({path: "x.txt", wip: "unstaged"});
  assert.ok(w.document.querySelectorAll(".cm-collapsedLines").length >= 1);
  const box = w.document.querySelector(".de-bar input");
  box.checked = false;
  box.dispatchEvent(new w.Event("change"));
  assert.equal(w.document.querySelectorAll(".cm-collapsedLines").length, 0);
  assert.equal(w.document.querySelectorAll(".cm-mergeViewEditor").length, 2);
  const same = loadView(() => ({path: "x.txt", old_path: "x.txt", old: side("a\n"), new: side("a\n")}));
  await same.view.load({path: "x.txt"});
  assert.equal(same.w.document.querySelector(".de-count").textContent, "NONE");
  assert.equal(same.w.document.querySelector(".de-bar button").disabled, true);
  const added = loadView(() => ({path: "n.txt", old_path: "n.txt", old: side("", {exists: false, label: "(none)", kind: "empty"}), new: side("x\n")}));
  await added.view.load({path: "n.txt"});
  assert.deepEqual([...added.w.document.querySelectorAll(".de-labels span")].map(s => s.textContent), ["(none) (missing)", "abc1234"]);
});

test("diff editor view: binary / no file / truncated / line ending notes, and server errors", async () => {
  const bin = loadView(() => ({path: "b", old_path: "b", old: side("", {binary: true, eol: null}), new: side("", {binary: true, eol: null})}));
  await bin.view.load({path: "b"});
  assert.deepEqual(notes(bin.w), ["BINARY"]);
  assert.equal(bin.w.document.querySelectorAll(".cm-mergeViewEditor").length, 0);
  const none = loadView(() => ({path: "s", old_path: "s", old: side("", {exists: false}), new: side("", {exists: false})}));
  await none.view.load({path: "s"});
  assert.deepEqual(notes(none.w), ["UNAVAILABLE"]);
  const mixed = loadView(() => ({path: "e.txt", old_path: "e.txt", old: side("a\r\n", {eol: "crlf", truncated: true}), new: side("b\n")}));
  await mixed.view.load({path: "e.txt"});
  assert.deepEqual(notes(mixed.w), ["TRUNCATED", "EOL CRLF -> LF"]);
  const bad = loadView(() => ({ok: false, message: "bad path"}));
  await bad.view.load({path: "x"});
  assert.deepEqual(notes(bad.w), ["bad path"]);
  assert.equal(bad.w.document.querySelector(".de-note").className, "de-note err");
});

test("diff editor view: an older load that finishes late is dropped", async () => {
  const gates = [];
  const {w, view} = loadView(url => ({path: url, old_path: url, old: side("old\n"), new: side(url.includes("first") ? "FIRST\n" : "SECOND\n")}));
  const realFetch = w.fetch;
  w.fetch = url => new Promise(resolve => gates.push(() => resolve(realFetch(url))));  // 応答を、好きな順に返す
  const first = view.load({path: "first"}), second = view.load({path: "second"});
  gates[1]();
  await second;
  gates[0]();
  await first;
  assert.ok(w.document.querySelector(".cm-merge-b").textContent.includes("SECOND"));
  assert.ok(!w.document.querySelector(".cm-merge-b").textContent.includes("FIRST"));
  const closed = view.load({path: "third"});
  view.close();
  gates[2]();
  await closed;
  assert.equal(w.document.querySelectorAll(".cm-mergeViewEditor").length, 0);  // 読み込み中に閉じたら、出さない
});

test("diff editor: Vim mode on both panes, can be switched on and off, keys move the cursor", () => {
  const w = load(TEXT_BUNDLE);
  const ed = w.GGText.createDiffEditor({parent: w.document.getElementById("top"), old: "abc\ndef\n", new: "abc\nDEF\nghi\n", path: "x.txt", collapse: false, vim: true});
  assert.equal(w.document.querySelectorAll(".cm-vim-panel").length, 2, "status panel in each pane");
  const cm = w.GGText.getCM(ed.view.b);
  w.GGText.Vim.handleKey(cm, "l", "user");  // (j は、レイアウトの無い jsdom では画面上の行の移動が正しく測れないので、l で確かめる)
  assert.equal(ed.view.b.state.selection.main.head, 1);
  w.GGText.Vim.handleKey(cm, "x", "user");  // 読み取り専用なので、消せない
  w.GGText.Vim.handleKey(cm, "d", "user"); w.GGText.Vim.handleKey(cm, "d", "user");
  assert.equal(ed.view.b.state.doc.toString(), "abc\nDEF\nghi\n");
  ed.setVim(false);
  assert.equal(w.document.querySelectorAll(".cm-vim-panel").length, 0);
  ed.setVim(true);
  assert.equal(w.document.querySelectorAll(".cm-vim-panel").length, 2);
  ed.destroy();
  const plain = w.GGText.createDiffEditor({parent: w.document.getElementById("result"), old: "a\n", new: "b\n", path: "x.txt", collapse: false});
  assert.equal(w.document.querySelectorAll(".cm-vim-panel").length, 0, "off by default");
  plain.destroy();
});

test("diff editor view: Vim checkbox follows the shared setting, switches both panes, and reports the change", async () => {
  const saved = [];
  const w = load(TEXT_BUNDLE);
  w.eval(fs.readFileSync(VIEW_SCRIPT, "utf8") + "\nwindow.createDiffEditorView = createDiffEditorView;");
  w.fetch = () => Promise.resolve({ok: true, json: () => Promise.resolve({path: "x.txt", old_path: "x.txt", old: side("a\n"), new: side("b\n")})});
  let on = true;  // 設定が既にオンなら、最初からオンで出る
  const view = w.createDiffEditorView({
    box: w.document.getElementById("top"), text: k => TEXTS[k] || k, phrases: () => ({}), loadScript: () => Promise.resolve(),
    vim: {get: () => on, set: v => { on = v; saved.push(v); }},
  });
  await view.load({path: "x.txt"});
  const box = [...w.document.querySelectorAll(".de-bar input")].find(i => i.parentNode.textContent === "edit_vim");
  assert.equal(box.checked, true);
  assert.equal(w.document.querySelectorAll(".cm-vim-panel").length, 2);
  assert.equal(box.parentNode.title, "vim_readonly_tip");
  box.checked = false;
  box.dispatchEvent(new w.Event("change"));
  assert.deepEqual(saved, [false]);
  assert.equal(w.document.querySelectorAll(".cm-vim-panel").length, 0);
  const collapse = [...w.document.querySelectorAll(".de-bar input")].find(i => i.parentNode.textContent === "COLLAPSE");
  collapse.checked = false;  // 折りたたみで作り直しても、設定は保たれる
  box.checked = true;
  box.dispatchEvent(new w.Event("change"));
  collapse.dispatchEvent(new w.Event("change"));
  assert.equal(w.document.querySelectorAll(".cm-vim-panel").length, 2);
  view.close();
});
