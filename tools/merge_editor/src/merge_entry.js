// マージエディタの CodeMirror 6 部分。ブラウザーでは window.GGMerge として使う (esbuild で 1 つの JS にまとめる)。
//   上段: ours | theirs を並べた読み取り専用の MergeView (違いを強調)
//   下段: 結果の編集ペイン。競合マーカーの範囲を色分けし、各競合の上に採用ボタンを出す。
// ファイル編集のエディタ (構文ハイライトと Vim モードで大きくなる) は別のバンドル (text_entry.js) にしてある。
import {EditorState, StateField} from "@codemirror/state";
import {Decoration, drawSelection, EditorView, highlightActiveLine, keymap, lineNumbers, WidgetType} from "@codemirror/view";
import {defaultKeymap, history, historyKeymap} from "@codemirror/commands";
import {MergeView} from "@codemirror/merge";
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
    for (const choice of CHOICES) {
      if (choice === "base" && !this.hasBase) continue;
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.choice = choice;
      button.textContent = this.labels[choice] || choice;
      button.addEventListener("mousedown", ev => ev.preventDefault());  // エディタのフォーカスを奪わない
      button.addEventListener("click", () => this.pick(choice, this.from));
      bar.appendChild(button);
    }
    return bar;
  }
  ignoreEvent() { return true; }
}

const SECTION_CLASS = {ours: "cm-conf-ours", base: "cm-conf-base", theirs: "cm-conf-theirs"};

function buildDecorations(state, labels, pick) {
  const text = state.doc.toString();
  const blocks = findConflicts(text);
  const ranges = [];
  const lineAt = pos => state.doc.lineAt(pos);
  const addLines = (from, to, cls) => {  // [from, to) の各行に行の装飾を付ける
    let pos = from;
    while (pos < to) {
      const line = lineAt(pos);
      ranges.push(Decoration.line({class: cls}).range(line.from));
      pos = line.to + 1;
    }
  };
  for (const b of blocks) {
    ranges.push(Decoration.widget({widget: new ConflictBar(b, labels, pick), block: true, side: -1}).range(b.from));
    ranges.push(Decoration.line({class: "cm-conf-marker cm-conf-marker-start"}).range(lineAt(b.lines.start).from));
    addLines(b.oursFrom, b.oursTo, SECTION_CLASS.ours);
    if (b.lines.base !== null) {
      ranges.push(Decoration.line({class: "cm-conf-marker"}).range(lineAt(b.lines.base).from));
      addLines(b.baseFrom, b.baseTo, SECTION_CLASS.base);
    }
    ranges.push(Decoration.line({class: "cm-conf-marker"}).range(lineAt(b.lines.sep).from));
    addLines(b.theirsFrom, b.theirsTo, SECTION_CLASS.theirs);
    ranges.push(Decoration.line({class: "cm-conf-marker cm-conf-marker-end"}).range(lineAt(b.lines.end).from));
  }
  return {blocks: blocks, decorations: Decoration.set(ranges, true)};
}

// opts: {top, result, ours, theirs, text, labels, onChange}
//   top / result: 親の要素。labels: {ours, theirs, both, both_rev, base} (ボタンの文言)。
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
  const field = StateField.define({
    create: state => buildDecorations(state, labels, pick),
    update: (value, tr) => tr.docChanged ? buildDecorations(tr.state, labels, pick) : value,
    provide: f => EditorView.decorations.from(f, value => value.decorations),
  });
  const notify = () => opts.onChange && opts.onChange(view.state.field(field).blocks.length);
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
    conflictCount: () => view.state.field(field).blocks.length,
    resolveAll: choice => {
      const text = view.state.doc.toString(), out = resolveAll(text, choice);
      if (out !== text) view.dispatch({changes: {from: 0, to: text.length, insert: out}, userEvent: "input.resolve"});
    },
    jump: dir => {  // 次 (dir > 0) / 前の競合へ移動する。端まで行ったら反対側の端に戻る
      const blocks = view.state.field(field).blocks;
      if (!blocks.length) return false;
      const head = view.state.selection.main.head;
      const target = dir > 0 ? (blocks.find(b => b.from > head) || blocks[0])
                             : ([...blocks].reverse().find(b => b.from < head) || blocks[blocks.length - 1]);
      view.dispatch({selection: {anchor: target.from}, effects: EditorView.scrollIntoView(target.from, {y: "center"})});
      view.focus();
      return true;
    },
    focus: () => view.focus(),
    destroy: () => { view.destroy(); topView.destroy(); },
  };
}

export {findConflicts, resolution, resolveAll};
