// 閲覧画面 (files.html) が以前使っていた、拡張子 → 言語の対応表。ファイル表示も CodeMirror にしたので、
// 対応する言語が減っていないことを確かめる (言語を足すのは構わないので、足りないものだけを調べる)。
import test from "node:test";
import assert from "node:assert/strict";
import {EXTENSIONS, FILENAMES, languageName} from "../src/languages.js";

const VIEWER_EXTENSIONS = {py: "python", pyw: "python", js: "javascript", jsx: "javascript", mjs: "javascript", cjs: "javascript", ts: "typescript", tsx: "typescript", java: "java", c: "c", h: "c", cc: "c", cpp: "c", hpp: "c", go: "go", rs: "rust", rb: "ruby", ru: "ruby", sql: "sql", sh: "shell", bash: "shell", zsh: "shell", json: "json", css: "css", html: "html", htm: "html", xml: "html", md: "markdown", markdown: "markdown", yml: "yaml", yaml: "yaml"};
const VIEWER_FILENAMES = {dockerfile: "shell", makefile: "shell"};

test("every extension and file name the old viewer highlighted is still supported", () => {
  for (const [ext, lang] of Object.entries(VIEWER_EXTENSIONS)) assert.equal(EXTENSIONS[ext], lang, ext);
  for (const [name, lang] of Object.entries(VIEWER_FILENAMES)) assert.equal(FILENAMES[name], lang, name);
});

test("languageName follows the viewer's rules: last path component, lower case, extension after the last dot", () => {
  assert.equal(languageName("src/App.PY"), "python");
  assert.equal(languageName("a/b/Dockerfile"), "shell");
  assert.equal(languageName("a/b/MAKEFILE"), "shell");
  assert.equal(languageName("a.tar.gz"), "");
  assert.equal(languageName("py"), "");
  assert.equal(languageName(".py"), "python");
  assert.equal(languageName("dir.py/readme"), "");
  assert.equal(languageName(""), "");
});
