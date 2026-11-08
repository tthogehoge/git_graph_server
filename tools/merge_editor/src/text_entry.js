// ファイルの編集画面 (/files の編集) のエディタ。ブラウザーでは window.GGText として使う (esbuild で 1 つの JS にまとめる)。
// 閲覧画面と同じ言語の構文ハイライト (languages.js) と、Vim モードがあるので、マージエディタとは別のバンドルにしてある。
import {Compartment, EditorState, Prec} from "@codemirror/state";
import {drawSelection, EditorView, highlightActiveLine, keymap, lineNumbers} from "@codemirror/view";
import {defaultKeymap, history, historyKeymap, indentLess, insertTab} from "@codemirror/commands";
import {indentUnit} from "@codemirror/language";
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

// opts: {parent, text, path, vim, phrases, onChange, onSave, onQuit}
//   path: ファイルのパス (拡張子で構文ハイライトの言語を決める。閲覧画面と同じ言語に対応)。vim: Vim モードにするか。
//   phrases: 検索パネルなどの文言の翻訳 ({"Find": "検索", ...})。onChange(): 内容が変わるたびに呼ぶ。
//   onSave(): Ctrl+S / Cmd+S (Vim モードは :w も) で呼ぶ。onQuit(force): Vim モードの :q (:q! は force=true) で呼ぶ。
// 改行は LF として扱う (保存するときに、元の改行コードに戻すのは呼び出し側)。折り返しはしない。
export function createTextEditor(opts) {
  const vimSlot = new Compartment();
  const vimExtension = on => on ? vim({status: true}) : [];
  const view = new EditorView({
    parent: opts.parent,
    state: EditorState.create({
      doc: opts.text,
      extensions: [
        lineNumbers(), history(), drawSelection(), highlightActiveLine(), highlightSelectionMatches(), search({top: true}),
        indentUnit.of("\t"), EditorState.tabSize.of(4),  // Tab はタブ文字を入れる (選択が複数行ならインデント)
        languageExtensions(opts.path || "") || [],
        Prec.high(vimSlot.of(vimExtension(!!opts.vim))),  // Vim のキーは、ほかのキー割り当てより先に効かせる
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
  textEditors.set(view, opts);
  return {
    view: view,
    language: languageName(opts.path || ""),
    getText: () => view.state.doc.toString(),
    setText: text => view.dispatch({changes: {from: 0, to: view.state.doc.length, insert: text}}),
    setVim: on => view.dispatch({effects: vimSlot.reconfigure(vimExtension(on))}),
    focus: () => view.focus(),
    destroy: () => { textEditors.delete(view); view.destroy(); },
  };
}

export {Vim, getCM} from "@replit/codemirror-vim";  // テストで Ex コマンドを直接動かすため
