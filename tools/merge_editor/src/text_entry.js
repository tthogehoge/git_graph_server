// /files のファイルの表示 (読み取り専用) と編集、/diff の差分エディタに使うエディタ。ブラウザーでは window.GGText として使う (esbuild で 1 つの JS にまとめる)。
// 言語ごとの構文ハイライト (languages.js) と Vim モードがあるので、マージエディタとは別のバンドルにしてある。
import {Compartment, EditorState, Prec} from "@codemirror/state";
import {drawSelection, EditorView, highlightActiveLine, keymap, lineNumbers} from "@codemirror/view";
import {defaultKeymap, history, historyKeymap, indentLess, insertTab} from "@codemirror/commands";
import {indentUnit} from "@codemirror/language";
import {getChunks, goToNextChunk, goToPreviousChunk, MergeView} from "@codemirror/merge";
import {highlightSelectionMatches, search, searchKeymap} from "@codemirror/search";
import {Vim, vim} from "@replit/codemirror-vim";
import {languageExtensions, languageName} from "./languages.js";
import {THEME, VIEW_SIZE} from "./theme.js";

// Vim モードの Ex コマンド (:w :wq :x :q :q!)。Vim の登録はアプリ全体で 1 つなので、押したエディタの opts を探して呼ぶ。
const textEditors = new WeakMap();  // EditorView → createTextEditor の opts
function defineEx(name, prefix, run) {
  Vim.defineEx(name, prefix, (cm, params) => {
    const opts = textEditors.get(cm.cm6);
    if (opts) run(opts, params);
  });
}
const save = opts => { if (opts.onSave) opts.onSave(); };  // 保存すると編集画面は閉じるので、:wq と :x も保存だけでよい
defineEx("write", "w", save);
defineEx("wq", "wq", save);
defineEx("xit", "x", save);
defineEx("quit", "q", (opts, params) => { if (opts.onQuit) opts.onQuit(/^\s*!/.test(params.argString || "")); });

// opts: {parent, text, path, readOnly, vim, phrases, onChange, onSave, onQuit}
//   path: ファイルのパス (拡張子で構文ハイライトの言語を決める)。readOnly: 読み取り専用 (ファイルの表示。text の末尾の改行は表示しない)。vim: Vim モードにするか。
//   phrases: 検索パネルなどの文言の翻訳 ({"Find": "検索", ...})。onChange(): 内容が変わるたびに呼ぶ。
//   onSave(): Ctrl+S / Cmd+S (Vim モードは :w も) で呼ぶ。onQuit(force): Vim モードの :q (:q! は force=true) で呼ぶ。
// 改行は LF として扱う (保存するときに、元の改行コードに戻すのは呼び出し側)。編集では折り返さない (表示 readOnly は折り返す)。
export function createTextEditor(opts) {
  const vimSlot = new Compartment();
  const vimExtension = on => on ? vim({status: true}) : [];
  const view = new EditorView({
    parent: opts.parent,
    state: EditorState.create({
      doc: opts.readOnly ? opts.text.replace(/\r?\n$/, "") : opts.text,  // 表示では、末尾の改行を空の行として数えない
      extensions: [
        lineNumbers(), history(), drawSelection(), opts.readOnly ? [] : highlightActiveLine(), highlightSelectionMatches(), search({top: true}),
        EditorState.readOnly.of(!!opts.readOnly),  // 読み取り専用でも、選択・コピー・検索 (Ctrl+F) はできる
        opts.readOnly ? EditorView.lineWrapping : [],  // 表示は長い行を折り返す (以前の表示と同じ)。編集は折り返さない
        indentUnit.of("\t"), EditorState.tabSize.of(4),  // Tab はタブ文字を入れる (選択が複数行ならインデント)
        languageExtensions(opts.path || "") || [],
        Prec.high(vimSlot.of(vimExtension(!!opts.vim))),  // Vim のキーは、ほかのキー割り当てより先に効かせる
        EditorState.phrases.of(opts.phrases || {}),
        keymap.of([
          {key: "Mod-s", preventDefault: true, run: () => { if (opts.onSave) opts.onSave(); return true; }},
          {key: "Tab", run: view => view.state.readOnly ? false : insertTab(view), shift: indentLess},  // insertTab は readOnly を見ない。表示では Tab をブラウザーに渡す (フォーカス移動)
          ...searchKeymap, ...defaultKeymap, ...historyKeymap,
        ]),
        THEME, VIEW_SIZE,
        EditorView.contentAttributes.of({spellcheck: "false", autocorrect: "off", autocapitalize: "off"}),
        EditorView.updateListener.of(update => { if (update.docChanged && opts.onChange) opts.onChange(); }),
      ],
    }),
  });
  textEditors.set(view, opts);
  return {
    view: view,
    language: languageName(opts.path || ""),
    getText: () => view.state.doc.toString(),
    setText: text => view.dispatch({changes: {from: 0, to: view.state.doc.length, insert: text}}),
    setVim: on => view.dispatch({effects: vimSlot.reconfigure(vimExtension(on))}),
    scrollToLine: n => {  // n 行目 (1 始まり) を選択して、画面の中ほどに出す
      const line = view.state.doc.line(Math.min(Math.max(1, n), view.state.doc.lines));
      view.dispatch({selection: {anchor: line.from, head: line.to}, effects: EditorView.scrollIntoView(line.from, {y: "center"})});
    },
    getScrollTop: () => view.scrollDOM.scrollTop,
    setScrollTop: top => { view.scrollDOM.scrollTop = top; },
    focus: () => view.focus(),
    destroy: () => { textEditors.delete(view); view.destroy(); },
  };
}

// 差分エディタ (/diff の「差分エディタ」ボタン)。変更前 | 変更後 を左右に並べた読み取り専用の MergeView で、
// 構文ハイライト・行番号・変更箇所の強調 (行と文字) と、検索 (Ctrl+F) ができる。高さは内容に合わせるので、ページ側がスクロールする。
// opts: {parent, old, new, path, collapse, vim, phrases}
//   old / new: 変更前 / 変更後の内容 (無い側は空文字)。path: 構文ハイライトの言語を決めるファイルのパス。
//   collapse: 変更のない行を折りたたむか。vim: Vim モードにするか (読み取り専用なので、移動・検索・コピーのキーだけ働く。両側のペインに効く)。phrases: 検索パネルなどの文言の翻訳 (createTextEditor と同じ)。
// 改行は LF として扱うので、改行コードだけの違いは差分に出ない (呼び出し側で、両側の改行コードを見て知らせる)。
export function createDiffEditor(opts) {
  const vimSlot = new Compartment();
  const vimExtension = on => on ? vim({status: true}) : [];
  const extensions = [
    lineNumbers(), drawSelection(), EditorState.readOnly.of(true), highlightSelectionMatches(), search({top: true}),
    EditorState.tabSize.of(4), EditorState.phrases.of(opts.phrases || {}),
    languageExtensions(opts.path || "") || [],
    Prec.high(vimSlot.of(vimExtension(!!opts.vim))),  // Vim のキーは、ほかのキー割り当てより先に効かせる
    keymap.of([...searchKeymap, ...defaultKeymap]),
    THEME,
    EditorView.contentAttributes.of({spellcheck: "false", autocorrect: "off", autocapitalize: "off"}),
  ];
  const view = new MergeView({
    a: {doc: opts.old, extensions: extensions}, b: {doc: opts.new, extensions: extensions},
    parent: opts.parent, highlightChanges: true, gutter: true,
    collapseUnchanged: opts.collapse ? {margin: 3, minSize: 4} : undefined,
  });
  const chunkCount = () => { const chunks = getChunks(view.b.state); return chunks ? chunks.chunks.length : 0; };
  return {
    view: view,
    language: languageName(opts.path || ""),
    chunkCount: chunkCount,
    jump: dir => {  // 次 (dir > 0) / 前の変更へ移動する。端まで行ったら反対側の端に戻る。変更が無ければ false
      if (!chunkCount()) return false;
      const go = dir > 0 ? goToNextChunk : goToPreviousChunk;
      if (go(view.b)) return true;
      view.b.dispatch({selection: {anchor: dir > 0 ? 0 : view.b.state.doc.length}});
      return go(view.b);
    },
    setVim: on => { for (const v of [view.a, view.b]) v.dispatch({effects: vimSlot.reconfigure(vimExtension(on))}); },
    focus: () => view.b.focus(),
    remeasure: () => { view.a.requestMeasure(); view.b.requestMeasure(); },
    destroy: () => view.destroy(),
  };
}

export {Vim, getCM} from "@replit/codemirror-vim";  // テストで Ex コマンドを直接動かすため
export {languageName};
