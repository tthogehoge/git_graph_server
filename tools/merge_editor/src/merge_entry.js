// マージエディタの CodeMirror 6 部分。ブラウザーでは window.GGMerge として使う (esbuild で 1 つの JS にまとめる)。
// 画面の方式は 2 つ。どちらも、結果 (競合マーカーの入った内容) は 1 つで、方式を切り替えても引き継ぐ。
//   createMergeEditor (競合マーカー方式)
//     上段: ours | theirs を並べた読み取り専用の MergeView (違いを強調)
//     下段: 結果の編集ペイン。競合の範囲を赤っぽく強調し、各競合の上に採用ボタンを出す。
//   createBaseMergeEditor (ベース編集方式)
//     ours | 結果 と theirs | 結果 の 2 組の MergeView。結果は 2 か所に出て、どちらを編集しても連動する。
//     差分ごとの矢印ボタンで ours / theirs の変更を結果に取り込む。競合の範囲は、赤っぽく強調する。
// ファイル編集のエディタ (構文ハイライトと Vim モードで大きくなる) は別のバンドル (text_entry.js) にしてある。
import {Annotation, EditorState, StateField} from "@codemirror/state";
import {Decoration, drawSelection, EditorView, highlightActiveLine, keymap, lineNumbers, WidgetType} from "@codemirror/view";
import {defaultKeymap, history, historyKeymap, redo, undo} from "@codemirror/commands";
import {getChunks, goToNextChunk, goToPreviousChunk, MergeView} from "@codemirror/merge";
import {CHOICES, findConflicts, resolution, resolveAll} from "./conflicts.js";
import {THEME, VIEW_SIZE} from "./theme.js";

class ConflictBar extends WidgetType {
  constructor(block, labels, pick) {
    super();
    this.from = block.from; this.hasBase = block.baseFrom !== null;
    this.sides = block.oursLabel + "\u0000" + block.theirsLabel;
    this.labels = labels; this.pick = pick;
  }
  eq(other) {
    return other.from === this.from && other.hasBase === this.hasBase && other.sides === this.sides && other.labels === this.labels;
  }
  toDOM() {
    const bar = document.createElement("div");
    bar.className = "cm-conflict-bar";
    const [oursLabel, theirsLabel] = this.sides.split("\u0000");
    const names = document.createElement("span");
    names.className = "cm-conflict-names";
    names.textContent = oursLabel + " \u21c4 " + theirsLabel;
    bar.appendChild(names);
    const keys = this.labels.keys || {};
    for (const choice of CHOICES) {
      if (choice === "base" && !this.hasBase) continue;
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.choice = choice;
      button.textContent = this.labels[choice] || choice;
      if (keys[choice]) button.title = keys[choice];  // ショートカットキー
      button.addEventListener("mousedown", ev => ev.preventDefault());  // エディタのフォーカスを奪わない
      button.addEventListener("click", () => this.pick(choice, this.from));
      bar.appendChild(button);
    }
    return bar;
  }
  ignoreEvent() { return true; }
}

const SECTION_CLASS = {ours: "cm-conf-ours", base: "cm-conf-base", theirs: "cm-conf-theirs"};

// bars: 競合ごとの採用ボタンの帯を出すか (ベース編集方式は、矢印ボタンとショートカットで採用するので出さない。帯が入ると、左右の行の高さがずれる)
function buildDecorations(state, labels, bars, pick) {
  const text = state.doc.toString();
  const blocks = findConflicts(text);
  const ranges = [];
  const lineAt = pos => state.doc.lineAt(pos);
  const addLines = (from, to, cls) => {  // [from, to) の各行に行の装飾を付ける
    let pos = from;
    while (pos < to) {
      const line = lineAt(pos);
      ranges.push(Decoration.line({class: "cm-conf " + cls}).range(line.from));
      pos = line.to + 1;
    }
  };
  for (const b of blocks) {
    if (bars) ranges.push(Decoration.widget({widget: new ConflictBar(b, labels, pick), block: true, side: -1}).range(b.from));
    ranges.push(Decoration.line({class: "cm-conf cm-conf-marker cm-conf-marker-start"}).range(lineAt(b.lines.start).from));
    addLines(b.oursFrom, b.oursTo, SECTION_CLASS.ours);
    if (b.lines.base !== null) {
      ranges.push(Decoration.line({class: "cm-conf cm-conf-marker"}).range(lineAt(b.lines.base).from));
      addLines(b.baseFrom, b.baseTo, SECTION_CLASS.base);
    }
    ranges.push(Decoration.line({class: "cm-conf cm-conf-marker"}).range(lineAt(b.lines.sep).from));
    addLines(b.theirsFrom, b.theirsTo, SECTION_CLASS.theirs);
    ranges.push(Decoration.line({class: "cm-conf cm-conf-marker cm-conf-marker-end"}).range(lineAt(b.lines.end).from));
  }
  return {blocks: blocks, decorations: Decoration.set(ranges, true)};
}

function conflictField(labels, bars, pick) {
  return StateField.define({
    create: state => buildDecorations(state, labels, bars, pick),
    update: (value, tr) => tr.docChanged ? buildDecorations(tr.state, labels, bars, pick) : value,
    provide: f => EditorView.decorations.from(f, value => value.decorations),
  });
}

// ---- 結果のペインに対する操作 (2 つの方式で共通)。view は結果のエディタ、field は conflictField ----
const conflictsOf = (view, field) => view.state.field(field).blocks;

// 次 (dir > 0) / 前の競合へ移動する。端まで行ったら反対側の端に戻る
function jumpConflict(view, field, dir) {
  const blocks = conflictsOf(view, field);
  if (!blocks.length) return false;
  const head = view.state.selection.main.head;
  const target = dir > 0 ? (blocks.find(b => b.from > head) || blocks[0])
                         : ([...blocks].reverse().find(b => b.from < head) || blocks[blocks.length - 1]);
  view.dispatch({selection: {anchor: target.from}, effects: EditorView.scrollIntoView(target.from, {y: "center"})});
  view.focus();
  return true;
}

// カーソルがある競合を choice で解決する。カーソルが競合の中に無いときは、次の競合へ移動するだけで、内容は変えない (false)
function adoptAtCursor(view, field, choice) {
  const head = view.state.selection.main.head;
  const block = conflictsOf(view, field).find(b => head >= b.from && head < b.to);
  if (!block) { jumpConflict(view, field, 1); return false; }
  const change = resolution(view.state.doc.toString(), block, choice);
  if (!change) return false;  // base の無い競合で base を選んだ
  view.dispatch({changes: change, userEvent: "input.resolve", scrollIntoView: true});
  return true;
}

function resolveAllIn(view, choice) {
  const text = view.state.doc.toString(), out = resolveAll(text, choice);
  if (out !== text) view.dispatch({changes: {from: 0, to: text.length, insert: out}, userEvent: "input.resolve"});
}

// opts: {top, result, ours, theirs, text, labels, onChange}
//   top / result: 親の要素。labels: {ours, theirs, both, both_rev, base, keys} (ボタンの文言と、ショートカットキーの説明 keys)。
//   onChange(conflictCount): 結果の内容が変わるたび (最初の 1 回を含む) に呼ぶ。
export function createMergeEditor(opts) {
  const labels = opts.labels || {};
  const readOnly = [EditorState.readOnly.of(true), lineNumbers(), THEME];
  const topView = new MergeView({
    a: {doc: opts.ours, extensions: readOnly},
    b: {doc: opts.theirs, extensions: readOnly},
    parent: opts.top, highlightChanges: true, gutter: true,
  });

  let view = null;
  const pick = (choice, from) => {
    const text = view.state.doc.toString();
    const block = findConflicts(text).find(b => b.from === from);
    const change = block && resolution(text, block, choice);
    if (change) view.dispatch({changes: change, userEvent: "input.resolve", scrollIntoView: false});
  };
  const field = conflictField(labels, true, pick);
  const notify = () => opts.onChange && opts.onChange(conflictsOf(view, field).length);
  view = new EditorView({
    parent: opts.result,
    state: EditorState.create({
      doc: opts.text,
      extensions: [lineNumbers(), history(), drawSelection(), highlightActiveLine(), keymap.of([...defaultKeymap, ...historyKeymap]),
                   THEME, VIEW_SIZE, field,
                   EditorView.updateListener.of(update => { if (update.docChanged) notify(); })],
    }),
  });
  notify();

  return {
    view: view, topView: topView,
    getText: () => view.state.doc.toString(),
    setText: text => view.dispatch({changes: {from: 0, to: view.state.doc.length, insert: text}, userEvent: "input.reset"}),
    conflictCount: () => conflictsOf(view, field).length,
    resolveAll: choice => resolveAllIn(view, choice),
    jump: dir => jumpConflict(view, field, dir),
    adopt: choice => adoptAtCursor(view, field, choice),
    remeasure: () => { for (const v of [view, topView.a, topView.b]) v.requestMeasure(); },
    focus: () => view.focus(),
    destroy: () => { view.destroy(); topView.destroy(); },
  };
}

const mirrored = Annotation.define();  // もう一方の結果からの反映である印 (反映をまた反映し返さない)
const chunkCount = view => { const chunks = getChunks(view.state); return chunks ? chunks.chunks.length : 0; };

// opts: {oursParent, theirsParent, ours, theirs, text, labels, onChange}
//   oursParent: ours | 結果 の組を入れる要素。theirsParent: theirs | 結果 の組を入れる要素。text: 結果の初めの内容 (競合マーカー入り)。
//   labels: {revert, keys} (差分ごとの矢印ボタンの説明)。onChange(conflictCount): 結果が変わるたび (最初の 1 回を含む) に呼ぶ。
// 2 つの結果は、片方を変えるともう片方にも同じ変更が入る。アンドゥ/リドゥの履歴は 1 つ (baseA) にまとめてあり、
// どちらで入力しても、Ctrl+Z はその順に戻る。
// 矢印ボタン: 競合の外の差分は、ライブラリの動作 (その差分の部分を ours / theirs の内容にする)。競合の範囲と重なる差分は、
// 矢印を赤くして、競合全体を ours / theirs で解決する (1 つの競合が複数の差分に分かれるので、先頭の矢印だけを出す)。
export function createBaseMergeEditor(opts) {
  const labels = opts.labels || {};
  const side = [EditorState.readOnly.of(true), lineNumbers(), THEME];
  const field = conflictField(labels, false, null);
  let baseA = null, baseB = null, lastFocused = null;
  const arrows = [];  // 矢印ボタンの整理 (tidy) を、ペアごとに持つ
  const tidyAll = () => requestAnimationFrame(() => requestAnimationFrame(() => arrows.forEach(a => a.tidy())));  // ライブラリが矢印を測り直した後
  const notify = () => { if (opts.onChange && baseA) opts.onChange(conflictsOf(baseA, field).length); };
  const resultExtensions = (self, peer, withHistory) => [
    lineNumbers(), drawSelection(), highlightActiveLine(), THEME, field, withHistory ? history() : [],
    keymap.of([
      // 履歴は baseA だけが持つ。baseB で Ctrl+Z / Ctrl+Y を押したときも、baseA の履歴を戻す
      ...(withHistory ? historyKeymap : [
        {key: "Mod-z", run: () => undo(baseA), preventDefault: true},
        {key: "Mod-y", mac: "Mod-Shift-z", run: () => redo(baseA), preventDefault: true},
        {key: "Mod-Shift-z", run: () => redo(baseA), preventDefault: true},
      ]),
      ...defaultKeymap,
    ]),
    EditorView.domEventHandlers({focus: () => { lastFocused = self(); }}),
    EditorView.updateListener.of(update => {
      if (!update.docChanged) return;
      if (!update.transactions.some(tr => tr.annotation(mirrored))) peer().dispatch({changes: update.changes, annotations: mirrored.of(true)});
      notify();
      tidyAll();
    }),
  ];
  const renderArrow = () => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "gg-adopt";
    button.textContent = "\u2192";
    button.title = labels.revert || "";
    button.setAttribute("aria-label", labels.revert || "");
    return button;
  };
  const makePair = (parent, sideDoc, sideName, self, peer, withHistory) => {
    const mergeView = new MergeView({
      a: {doc: sideDoc, extensions: side}, b: {doc: opts.text, extensions: resultExtensions(self, peer, withHistory)},
      parent: parent, revertControls: "a-to-b", renderRevertControl: renderArrow, highlightChanges: true, gutter: true,
    });
    const revertDOM = mergeView.dom.querySelector(".cm-merge-revert");
    const result = mergeView.b;
    const blockOf = index => {  // この差分 (チャンク) と重なる競合
      const chunks = getChunks(result.state), chunk = chunks && chunks.chunks[index];
      if (!chunk) return null;
      return conflictsOf(result, field).find(b => (chunk.fromB < b.to && chunk.toB > b.from) ||
                                                  (chunk.fromB === chunk.toB && chunk.fromB >= b.from && chunk.fromB < b.to)) || null;
    };
    if (revertDOM) {
      revertDOM.addEventListener("mousedown", ev => {
        const button = ev.target.closest ? ev.target.closest("button") : null;
        const block = button && blockOf(Number(button.dataset.chunk));
        if (!block) return;  // 競合の外の差分は、ライブラリの動作に任せる
        ev.stopPropagation(); ev.preventDefault();
        const change = resolution(result.state.doc.toString(), block, sideName);
        if (change) result.dispatch({changes: change, userEvent: "revert"});
      }, true);  // ライブラリの処理より先に受ける
      const tidy = () => {
        const seen = new Set();
        for (const button of revertDOM.querySelectorAll("button")) {
          const block = blockOf(Number(button.dataset.chunk));
          button.classList.toggle("gg-conflict", !!block);
          const hide = !!block && seen.has(block.from);  // 同じ競合の 2 つ目以降の矢印は隠す
          if (block) seen.add(block.from);
          if (hide !== (button.style.display === "none")) button.style.display = hide ? "none" : "";
        }
      };
      arrows.push({tidy: tidy});
      new MutationObserver(tidy).observe(revertDOM, {childList: true});
    }
    return mergeView;
  };
  const viewOurs = makePair(opts.oursParent, opts.ours, "ours", () => baseA, () => baseB, true);
  const viewTheirs = makePair(opts.theirsParent, opts.theirs, "theirs", () => baseB, () => baseA, false);
  baseA = viewOurs.b; baseB = viewTheirs.b; lastFocused = baseA;
  notify();

  const current = () => lastFocused;  // 直前に触った方の結果
  return {
    oursView: viewOurs, theirsView: viewTheirs, baseA: baseA, baseB: baseB,
    getText: () => baseA.state.doc.toString(),
    setText: text => baseA.dispatch({changes: {from: 0, to: baseA.state.doc.length, insert: text}, userEvent: "input.reset"}),  // もう一方にも反映され、アンドゥできる
    conflictCount: () => conflictsOf(baseA, field).length,
    resolveAll: choice => resolveAllIn(baseA, choice),
    jump: dir => jumpConflict(current(), field, dir),
    adopt: choice => adoptAtCursor(current(), field, choice),
    jumpDiff: dir => {  // 直前に触った組の、次 (dir > 0) / 前の差分へ移動する。端まで行ったら反対側の端に戻る
      const view = current(), go = dir > 0 ? goToNextChunk : goToPreviousChunk;
      if (!chunkCount(view)) return false;
      if (go(view)) return true;
      view.dispatch({selection: {anchor: dir > 0 ? 0 : view.state.doc.length}});
      return go(view);
    },
    remeasure: () => { for (const v of [viewOurs.a, viewOurs.b, viewTheirs.a, viewTheirs.b]) v.requestMeasure(); },
    focus: () => baseA.focus(),
    destroy: () => { viewOurs.destroy(); viewTheirs.destroy(); },
  };
}

export {findConflicts, resolution, resolveAll};
