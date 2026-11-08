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

// ---- ベース編集方式: ours | base と theirs | base の 2 組 ----
const BASE = "one\ntwo\nthree\nfour\nfive\n", OURS = "one\nTWO ours\nthree\nfour\nfive\n", THEIRS = "one\ntwo\nthree\nFOUR theirs\nfive\n";
function baseEditor(w, extra = {}) {
  const counts = [];
  const ed = w.GGMerge.createBaseMergeEditor({
    oursParent: w.document.getElementById("top"), theirsParent: w.document.getElementById("result"),
    ours: OURS, theirs: THEIRS, base: BASE, labels: {revert: "取り込む"}, onChange: c => counts.push(c), ...extra,
  });
  return {ed, counts};
}
const plain = value => JSON.parse(JSON.stringify(value));  // jsdom のウィンドウの中で作った値は、別の Object なので、そのまま deepEqual できない
const frame = w => new Promise(resolve => w.requestAnimationFrame(() => setTimeout(resolve, 20)));

test("base editor: two pairs, each side compared with base, counts reported", () => {
  const w = load();
  const {ed, counts} = baseEditor(w);
  assert.equal(w.document.querySelectorAll("#top .cm-editor").length, 2);     // ours | base
  assert.equal(w.document.querySelectorAll("#result .cm-editor").length, 2);  // theirs | base
  assert.equal(ed.oursView.a.state.doc.toString(), OURS);
  assert.equal(ed.theirsView.a.state.doc.toString(), THEIRS);
  assert.equal(ed.oursView.a.state.readOnly, true);
  assert.equal(ed.theirsView.a.state.readOnly, true);
  assert.equal(ed.baseA.state.readOnly, false);
  assert.equal(ed.getText(), BASE);                                       // 初期値は本物の base
  assert.deepEqual(plain(ed.counts()), {ours: 1, theirs: 1});
  assert.deepEqual(plain(counts.at(0)), {ours: 1, theirs: 1});                   // 最初の 1 回
  ed.destroy();
});

test("base editor: edits in either base are mirrored to the other, and the counts follow", () => {
  const w = load();
  const {ed, counts} = baseEditor(w);
  const same = () => ed.baseA.state.doc.toString() === ed.baseB.state.doc.toString();
  ed.baseA.dispatch({changes: {from: 0, insert: "HEAD\n"}});
  assert.ok(same());
  assert.ok(ed.baseB.state.doc.toString().startsWith("HEAD\n"));
  ed.baseB.dispatch({changes: {from: ed.baseB.state.doc.length, insert: "TAIL\n"}});
  assert.ok(same());
  assert.ok(ed.baseA.state.doc.toString().endsWith("TAIL\n"));
  assert.deepEqual(plain(ed.counts()), {ours: 3, theirs: 3});                    // HEAD と TAIL の分だけ差分が増える
  assert.deepEqual(plain(counts.at(-1)), {ours: 3, theirs: 3});
  // 1 つの編集で、変更の通知が増え続けない (反映の反映でループしない)
  const before = counts.length;
  ed.baseA.dispatch({changes: {from: 0, insert: "X"}});
  assert.ok(counts.length - before <= 2, "notifications: " + (counts.length - before));
  ed.destroy();
});

test("base editor: one undo history for both bases (typing in either base is undone in order)", () => {
  const w = load();
  const {ed} = baseEditor(w);
  const key = (view, name, extra = {}) => {
    const ev = new w.KeyboardEvent("keydown", {key: name, bubbles: true, cancelable: true, ...extra});
    view.contentDOM.dispatchEvent(ev);
    return ev;
  };
  // 近い位置の続けた入力は、CodeMirror が 1 回のアンドゥにまとめるので、離れた位置を使う
  ed.baseA.dispatch({changes: {from: 0, insert: "A1\n"}});
  ed.baseB.dispatch({changes: {from: ed.baseB.state.doc.length, insert: "B1\n"}});
  assert.equal(ed.getText(), "A1\n" + BASE + "B1\n");
  key(ed.baseB, "z", {ctrlKey: true});                                     // baseB で Ctrl+Z → 最後の編集 (baseB の入力) を戻す
  assert.equal(ed.getText(), "A1\n" + BASE);
  assert.equal(ed.baseA.state.doc.toString(), ed.baseB.state.doc.toString());
  key(ed.baseA, "z", {ctrlKey: true});                                     // baseA で Ctrl+Z → baseA の入力も戻る
  assert.equal(ed.getText(), BASE);
  assert.equal(ed.baseA.state.doc.toString(), ed.baseB.state.doc.toString());
  key(ed.baseB, "y", {ctrlKey: true});                                     // baseB で Ctrl+Y → やり直し
  assert.equal(ed.getText(), "A1\n" + BASE);
  assert.equal(ed.baseA.state.doc.toString(), ed.baseB.state.doc.toString());
  key(ed.baseA, "y", {ctrlKey: true});
  assert.equal(ed.getText(), "A1\n" + BASE + "B1\n");
  ed.destroy();
});

test("base editor: setText replaces both bases and can be undone; jump moves between differences", () => {
  const w = load();
  const {ed} = baseEditor(w);
  ed.setText(OURS);
  assert.equal(ed.baseB.state.doc.toString(), OURS);
  assert.deepEqual(plain(ed.counts()), {ours: 0, theirs: 2});                     // base を ours にした → ours との差分は 0
  ed.setText(BASE);
  assert.deepEqual(plain(ed.counts()), {ours: 1, theirs: 1});
  ed.baseA.focus();
  assert.equal(ed.jump(1), true);
  assert.equal(ed.jump(1), true);   // 差分が 1 つなので、端まで行っても反対側に戻って同じ差分に着く
  assert.equal(ed.jump(-1), true);
  ed.setText(OURS); ed.setText(THEIRS.replace("FOUR theirs", "four"));       // どちらとも差分が 0 の組は動かない
  ed.destroy();
});

test("base editor: the revert buttons apply ours' / theirs' change to base (both bases follow)", async () => {
  const w = load();
  const {ed} = baseEditor(w);
  await frame(w);
  const buttons = [...w.document.querySelectorAll(".cm-merge-revert button")];
  assert.ok(buttons.length >= 2, "revert buttons: " + buttons.length);
  assert.equal(buttons[0].getAttribute("title"), "取り込む");              // ボタンの説明は呼び出し側の翻訳
  buttons[0].dispatchEvent(new w.MouseEvent("mousedown", {bubbles: true, cancelable: true}));  // ours の変更を base へ
  assert.equal(ed.getText(), OURS);
  assert.equal(ed.baseB.state.doc.toString(), OURS);
  await frame(w);
  const rest = [...w.document.querySelectorAll("#result .cm-merge-revert button")];
  assert.equal(rest.length, 2);        // base が ours になったので、theirs との差分は 2 つ (2 行目と 4 行目)
  rest[1].dispatchEvent(new w.MouseEvent("mousedown", {bubbles: true, cancelable: true}));     // 4 行目の theirs の変更を base へ
  assert.equal(ed.getText(), "one\nTWO ours\nthree\nFOUR theirs\nfive\n");
  assert.deepEqual(plain(ed.counts()), {ours: 1, theirs: 1});                     // 取り込んだ分、片方との差分は残る (ours 側は theirs の変更の分)
  ed.destroy();
});
