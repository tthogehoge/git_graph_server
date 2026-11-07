// バンドルしたエディタを jsdom に読み込んで、競合ごとの採用ボタンが働くことを確認する。
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import {JSDOM} from "jsdom";

const BUNDLE = new URL("../../../git_graph/static/vendor/merge_editor.js", import.meta.url);
const TEXT = "a\n<<<<<<< HEAD\nO\n||||||| base\nB\n=======\nT\n>>>>>>> feature\nz\n<<<<<<< HEAD\nO2\n=======\nT2\n>>>>>>> feature\n";

function load() {
  const dom = new JSDOM("<!DOCTYPE html><div id='top'></div><div id='result'></div>", {runScripts: "outside-only", pretendToBeVisual: true});
  const w = dom.window;
  // jsdom に無い、レイアウトの API (CodeMirror が測定に使う) を空の値で補う
  const rect = {left: 0, right: 0, top: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0};
  w.Range.prototype.getClientRects = () => [];
  w.Range.prototype.getBoundingClientRect = () => rect;
  w.Element.prototype.getClientRects = () => [];
  w.document.elementFromPoint = () => null;
  w.eval(fs.readFileSync(BUNDLE, "utf8"));
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
  const w = load();
  let changes = 0, saves = 0;
  const ed = w.GGMerge.createTextEditor({
    parent: w.document.getElementById("result"), text: "a\r\nb\r\n", phrases: {Find: "検索"},
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
  assert.equal(w.document.querySelector(".cm-conflict-bar"), null);  // マージエディタの部品は出ない
  assert.equal(ed.view.state.phrase("Find"), "検索");
  ed.destroy();
  assert.equal(w.document.querySelectorAll(".cm-editor").length, 0);
});
