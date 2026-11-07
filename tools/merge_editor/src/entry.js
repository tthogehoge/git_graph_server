// CodeMirror 6 を使うエディタ 2 つ。ブラウザーでは window.GGMerge として使う (esbuild で 1 つの JS にまとめる)。
//   createMergeEditor: マージエディタ
//     上段: ours | theirs を並べた読み取り専用の MergeView (違いを強調)
//     下段: 結果の編集ペイン。競合マーカーの範囲を色分けし、各競合の上に採用ボタンを出す。
//   createTextEditor: ファイル編集 (/files の編集画面) 用の、ふつうのテキストエディタ
import {EditorState, StateField} from "@codemirror/state";
import {Decoration, drawSelection, EditorView, highlightActiveLine, keymap, lineNumbers, WidgetType} from "@codemirror/view";
import {defaultKeymap, history, historyKeymap, indentLess, insertTab} from "@codemirror/commands";
import {indentUnit} from "@codemirror/language";
import {highlightSelectionMatches, search, searchKeymap} from "@codemirror/search";
import {MergeView} from "@codemirror/merge";
import {CHOICES, findConflicts, resolution, resolveAll} from "./conflicts.js";

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

const THEME = EditorView.theme({
  "&": {fontSize: "12px", backgroundColor: "#fff"},
  ".cm-content, .cm-gutters": {fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"},
  ".cm-gutters": {backgroundColor: "#f6f8fa", color: "#8c959f", borderRight: "1px solid #e3e3e3"},
  ".cm-activeLine": {backgroundColor: "rgba(120, 140, 200, 0.10)"},
  ".cm-conf-ours": {backgroundColor: "#e3f4e6"},
  ".cm-conf-base": {backgroundColor: "#eeeeee"},
  ".cm-conf-theirs": {backgroundColor: "#e0eafb"},
  ".cm-conf-marker": {backgroundColor: "#fff1c2", color: "#8a6d00"},
  ".cm-conflict-bar": {display: "flex", flexWrap: "wrap", alignItems: "center", gap: "6px", padding: "3px 6px", backgroundColor: "#fff8dc",
                       borderTop: "1px solid #e6d58a", fontFamily: "system-ui, sans-serif"},
  ".cm-conflict-names": {color: "#6b5a00", marginRight: "4px"},
  ".cm-conflict-bar button": {background: "#e8eefc", border: "1px solid #b8c7ee", borderRadius: "3px", padding: "1px 8px",
                               cursor: "pointer", font: "inherit"},
  ".cm-conflict-bar button:hover": {background: "#d6e2fb"},
}, {dark: false});

const VIEW_SIZE = EditorView.theme({
  "&": {height: "100%"},
  ".cm-scroller": {overflow: "auto"},
});

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

// opts: {parent, text, phrases, onChange, onSave}
//   phrases: 検索パネルなどの文言の翻訳 ({"Find": "検索", ...})。onChange(): 内容が変わるたびに呼ぶ。onSave(): Ctrl+S / Cmd+S で呼ぶ。
// 改行は LF として扱う (保存するときに、元の改行コードに戻すのは呼び出し側)。折り返しはしない。
export function createTextEditor(opts) {
  const view = new EditorView({
    parent: opts.parent,
    state: EditorState.create({
      doc: opts.text,
      extensions: [
        lineNumbers(), history(), drawSelection(), highlightActiveLine(), highlightSelectionMatches(), search({top: true}),
        indentUnit.of("\t"), EditorState.tabSize.of(4),  // Tab はタブ文字を入れる (選択が複数行ならインデント)
        EditorState.phrases.of(opts.phrases || {}),
        keymap.of([
          {key: "Mod-s", preventDefault: true, run: () => { if (opts.onSave) opts.onSave(); return true; }},
          {key: "Tab", run: insertTab, shift: indentLess},
          ...searchKeymap, ...defaultKeymap, ...historyKeymap,
        ]),
        THEME, VIEW_SIZE,
        EditorView.contentAttributes.of({spellcheck: "false", autocorrect: "off", autocapitalize: "off"}),
        EditorView.updateListener.of(update => { if (update.docChanged && opts.onChange) opts.onChange(); }),
      ],
    }),
  });
  return {
    view: view,
    getText: () => view.state.doc.toString(),
    setText: text => view.dispatch({changes: {from: 0, to: view.state.doc.length, insert: text}}),
    focus: () => view.focus(),
    destroy: () => view.destroy(),
  };
}

export {findConflicts, resolution, resolveAll};
