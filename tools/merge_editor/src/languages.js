// ファイルの編集画面の構文ハイライト。閲覧画面 (files.html の languageFor) が対応している言語と同じものを、CodeMirror の言語で用意する。
import {bracketMatching, defaultHighlightStyle, StreamLanguage, syntaxHighlighting} from "@codemirror/language";
import {cpp} from "@codemirror/lang-cpp";
import {css} from "@codemirror/lang-css";
import {go} from "@codemirror/lang-go";
import {html} from "@codemirror/lang-html";
import {java} from "@codemirror/lang-java";
import {javascript} from "@codemirror/lang-javascript";
import {json} from "@codemirror/lang-json";
import {markdown} from "@codemirror/lang-markdown";
import {python} from "@codemirror/lang-python";
import {rust} from "@codemirror/lang-rust";
import {sql} from "@codemirror/lang-sql";
import {xml} from "@codemirror/lang-xml";
import {yaml} from "@codemirror/lang-yaml";
import {ruby} from "@codemirror/legacy-modes/mode/ruby";
import {shell} from "@codemirror/legacy-modes/mode/shell";

// 拡張子 → 言語名と、拡張子の無いファイル名 → 言語名。files.html の languageFor() と同じ対応にそろえる
// (tests/test_merge_editor.py が突き合わせるので、files.html に言語を足したら、ここにも足す)。
export const EXTENSIONS = {py: "python", pyw: "python", js: "javascript", jsx: "javascript", mjs: "javascript", cjs: "javascript", ts: "typescript", tsx: "typescript", java: "java", c: "c", h: "c", cc: "c", cpp: "c", hpp: "c", go: "go", rs: "rust", rb: "ruby", ru: "ruby", sql: "sql", sh: "shell", bash: "shell", zsh: "shell", json: "json", css: "css", html: "html", htm: "html", xml: "html", md: "markdown", markdown: "markdown", yml: "yaml", yaml: "yaml"};
export const FILENAMES = {dockerfile: "shell", makefile: "shell"};

const SUPPORT = {
  python: () => python(),
  javascript: ext => javascript({jsx: ext === "jsx"}),
  typescript: ext => javascript({typescript: true, jsx: ext === "tsx"}),
  java: () => java(),
  c: () => cpp(),
  go: () => go(),
  rust: () => rust(),
  ruby: () => StreamLanguage.define(ruby),
  sql: () => sql(),
  shell: () => StreamLanguage.define(shell),
  json: () => json(),
  css: () => css(),
  html: ext => ext === "xml" ? xml() : html(),  // 閲覧画面は xml も html 扱いだが、編集では xml の文法を使う
  markdown: () => markdown(),
  yaml: () => yaml(),
};

function fileParts(path) {  // files.html の languageFor() と同じ分け方
  const name = path.toLowerCase().split("/").pop();
  return {name: name, ext: name.includes(".") ? name.split(".").pop() : ""};
}

// path のファイルの言語名 (対応していなければ "")
export function languageName(path) {
  const {name, ext} = fileParts(path);
  return FILENAMES[name] || EXTENSIONS[ext] || "";
}

// path のファイルのハイライトと括弧の対応表示の拡張。対応していない言語は null
export function languageExtensions(path) {
  const lang = languageName(path);
  if (!lang) return null;
  return [SUPPORT[lang](fileParts(path).ext), syntaxHighlighting(defaultHighlightStyle, {fallback: true}), bracketMatching()];
}
