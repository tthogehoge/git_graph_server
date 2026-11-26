// Markdown の編集で、画像のドロップ / 図のペーストを attachments へアップロードしてリンクにする部品
// (git_graph/static/files/js/06_md_attach.js) の振る舞いの検査。本物の CodeMirror (vendor/text_editor.js) に付けて動かす。
// 実行: node tests/md_attach_behavior.js (jsdom が require できること)。tests/test_md_attach.py から呼ぶ。失敗は終了コード 1 と標準エラーへ。
const fs = require("fs"), path = require("path");
const {JSDOM} = require("jsdom");
const root = path.join(__dirname, "..", "git_graph", "static");
const textBundle = fs.readFileSync(path.join(root, "vendor", "text_editor.js"), "utf8");
const attachSource = fs.readFileSync(path.join(root, "files", "js", "06_md_attach.js"), "utf8");
const failures = [];
const check = (cond, msg) => { if (!cond) failures.push(msg); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function until(cond, what) {
  for (let i = 0; i < 200; i++) { if (cond()) return true; await sleep(10); }
  failures.push("待ちきれませんでした: " + what);
  return false;
}

const DOC = "# T\n\nbody\n";  // 5 は 3 行目 (body) の行頭
// respond(path, body) → {ok, status, text} または、それを返す Promise
function scenario(mdPath, respond) {
  const dom = new JSDOM('<div id="main"><div id="editor"></div></div>', {url: "http://x/", runScripts: "outside-only", pretendToBeVisual: true});
  const w = dom.window;
  // jsdom に無い、レイアウトの API (CodeMirror が測定に使う) を空の値で補う
  const rect = {left: 0, right: 0, top: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0};
  w.Range.prototype.getClientRects = () => [];
  w.Range.prototype.getBoundingClientRect = () => rect;
  w.Element.prototype.getClientRects = () => [];
  w.document.elementFromPoint = () => null;
  w.eval(textBundle);
  w.eval(attachSource + "; window.MdAttach = MdAttach;");
  const host = w.document.getElementById("editor");
  const ed = w.GGText.createTextEditor({parent: host, text: DOC, path: mdPath});
  ed.view.dispatch({selection: {anchor: 5}});
  const uploads = [], statuses = [];
  let refreshed = 0, mainSaw = 0;
  w.document.getElementById("main").addEventListener("paste", () => { mainSaw++; });
  w.document.getElementById("main").addEventListener("drop", () => { mainSaw++; });  // 画面全体のドロップ (今のディレクトリへ置く) の受け口
  w.fetch = (url, init) => {
    const body = JSON.parse(init.body);
    uploads.push({url: url, method: init.method, body: body});
    return Promise.resolve(respond ? respond(body.path, body) : {ok: true, status: 200, text: ""}).then(r => ({ok: r.ok, status: r.status, text: () => Promise.resolve(r.text)}));
  };
  w.MdAttach.bind(host, {
    getView: () => ed.view, mdPath: () => mdPath, t: (k, p) => k + (p ? JSON.stringify(p) : ""),
    status: (m, kind) => { statuses.push([m, kind]); }, refresh: () => { refreshed++; },
  });
  const file = (name, type, bytes) => new w.File([new w.Uint8Array(bytes || [1, 2, 3])], name, {type: type});  // [1,2,3] の base64 は AQID
  const pasteEvent = (files, text) => {
    const ev = new w.Event("paste", {bubbles: true, cancelable: true});
    Object.defineProperty(ev, "clipboardData", {value: {files: files, getData: t => (t === "text/plain" ? (text || "") : "")}});
    ed.view.contentDOM.dispatchEvent(ev);
    return ev;
  };
  const dropEvent = (files, types) => {
    const ev = new w.Event("drop", {bubbles: true, cancelable: true});
    Object.defineProperty(ev, "dataTransfer", {value: {files: files, types: types || ["Files"], dropEffect: "", getData: () => ""}});
    Object.defineProperty(ev, "clientX", {value: 1}); Object.defineProperty(ev, "clientY", {value: 1});
    ed.view.contentDOM.dispatchEvent(ev);
    return ev;
  };
  return {w, ed, host, uploads, statuses, file, pasteEvent, dropEvent, refreshed: () => refreshed, mainSaw: () => mainSaw,
          done: n => until(() => statuses.some(s => s[1] !== "progress") && statuses.filter(s => s[1] !== "progress").length >= (n || 1), "アップロードの完了")};
}

(async () => {
  {  // 図のペースト (スクリーンショットは image.png で来る) → attachments/image-日時.png にして、カーソルの位置にリンクを貼る
    const s = scenario("docs/a.md");
    const ev = s.pasteEvent([s.file("image.png", "image/png")]);
    check(ev.defaultPrevented, "paste: 画像のペーストはエディタに任せず、こちらで受ける");
    check(s.ed.getText().includes("<!--attach-1-0-->"), "paste: アップロードの間は、場所取りの印が入る");
    await s.done();
    const up = s.uploads[0];
    check(s.uploads.length === 1 && up.url === "/api/file-upload" && up.method === "POST", "paste: /api/file-upload へ POST する");
    const m = up && /^docs\/attachments\/(image-\d{8}-\d{6})\.png$/.exec(up.body.path);
    check(m, "paste: Markdown と同じ階層の attachments/image-日時.png (実際: " + (up && up.body.path) + ")");
    check(up && up.body.overwrite === false && up.body.data === "AQID", "paste: 上書きしない。中身は base64");
    const link = m ? "![" + m[1] + "](attachments/" + m[1] + ".png)" : "?";
    check(s.ed.getText() === "# T\n\n" + link + "body\n", "paste: カーソルの位置にリンクが入り、印は残らない (実際: " + JSON.stringify(s.ed.getText()) + ")");
    check(s.ed.view.state.selection.main.head === 5 + link.length, "paste: カーソルはリンクの後ろ");
    check(s.refreshed() === 1, "paste: 一覧を更新する");
    check(JSON.stringify(s.statuses[s.statuses.length - 1]) === '["upload_done{\\"n\\":1}","success"]', "paste: 完了を知らせる");
    check(s.mainSaw() === 0, "paste: 画面全体の受け口には渡さない");
  }
  {  // 文字も入っているペースト (表計算のセルなど) は、文字を貼る。アップロードしない
    const s = scenario("a.md");
    s.pasteEvent([s.file("image.png", "image/png")], "cell text");
    await sleep(60);
    check(s.uploads.length === 0 && !s.ed.getText().includes("<!--attach"), "paste: 文字が入っているときはアップロードしない");
  }
  {  // ファイルの無いペースト (文字だけ)
    const s = scenario("a.md");
    s.pasteEvent([], "plain");
    await sleep(30);
    check(s.uploads.length === 0, "paste: ファイルが無ければ何もしない");
  }
  {  // ドロップ: ドロップした位置 / 名前はそのまま / リンクの中で壊れる文字は %xx
    const s = scenario("README.md");
    s.ed.view.posAtCoords = () => 3;
    const ev = s.dropEvent([s.file("my pic (1).png", "image/png")]);
    check(ev.defaultPrevented, "drop: ファイルのドロップは、CodeMirror (中身を貼る) に任せず、こちらで受ける");
    check(s.mainSaw() === 0, "drop: 画面全体のドロップの受け口 (今のディレクトリへ置く) には渡さない");
    await s.done();
    check(s.uploads[0].body.path === "attachments/my pic (1).png", "drop: ルートの Markdown は attachments/ (実際: " + s.uploads[0].body.path + ")");
    check(s.ed.getText() === "# T![my pic (1)](attachments/my%20pic%20%281%29.png)\n\nbody\n", "drop: ドロップした位置にリンク (実際: " + JSON.stringify(s.ed.getText()) + ")");
  }
  {  // 画像以外はリンク。日本語の名前はそのまま。複数ファイルは 1 行ずつ
    const s = scenario("docs/sub/a.md");
    s.dropEvent([s.file("report.pdf", "application/pdf"), s.file("日本語.png", "image/png")]);
    await s.done();
    check(s.uploads.map(u => u.body.path).join() === "docs/sub/attachments/report.pdf,docs/sub/attachments/日本語.png", "multi: 順にアップロード (実際: " + s.uploads.map(u => u.body.path) + ")");
    check(s.ed.getText().includes("[report.pdf](attachments/report.pdf)\n![日本語](attachments/日本語.png)"), "multi: 画像以外は [名前](…)、画像は ![名前](…) (実際: " + JSON.stringify(s.ed.getText()) + ")");
    check(s.statuses[s.statuses.length - 1][0] === 'upload_done{"n":2}', "multi: 2 件");
  }
  {  // 同じ名前が既にあれば、上書きせず、名前を変える
    const exists = new Set(["docs/attachments/a.png", "docs/attachments/a-2.png"]);
    const s = scenario("docs/x.md", p => exists.has(p) ? {ok: false, status: 400, text: "File already exists: " + p} : {ok: true, status: 200, text: ""});
    s.pasteEvent([s.file("a.png", "image/png")]);
    await s.done();
    check(s.uploads.map(u => u.body.path).join() === "docs/attachments/a.png,docs/attachments/a-2.png,docs/attachments/a-3.png", "exists: a → a-2 → a-3 (実際: " + s.uploads.map(u => u.body.path) + ")");
    check(s.uploads.every(u => u.body.overwrite === false), "exists: 上書きしない");
    check(s.ed.getText().includes("![a-3](attachments/a-3.png)"), "exists: リンクは、実際に書いた名前");
  }
  {  // 失敗: 印は消えて元の文書に戻る。エラーを知らせる。一覧は更新しない
    const s = scenario("a.md", () => ({ok: false, status: 500, text: "boom"}));
    s.dropEvent([s.file("a.png", "image/png"), s.file("b.png", "image/png")]);
    await s.done();
    check(s.ed.getText() === DOC, "error: 印が残らず、元の文書に戻る (実際: " + JSON.stringify(s.ed.getText()) + ")");
    check(s.uploads.length === 1, "error: 失敗したら、残りはアップロードしない");
    const last = s.statuses[s.statuses.length - 1];
    check(last[1] === "error" && last[0].includes("boom"), "error: エラーを知らせる");
    check(s.refreshed() === 0, "error: 何も書けていなければ、一覧は更新しない");
  }
  {  // アップロードの間に打ち込んでも、リンクは印の場所に入る
    let release;
    const s = scenario("a.md", () => new Promise(r => { release = () => r({ok: true, status: 200, text: ""}); }));
    s.pasteEvent([s.file("p.png", "image/png")]);
    await until(() => !!release, "アップロードの開始");
    s.ed.view.dispatch({changes: {from: 0, insert: "TOP\n"}});  // 先頭に打ち込む (位置がずれる)
    release();
    await s.done();
    check(s.ed.getText() === "TOP\n# T\n\n![p](attachments/p.png)body\n", "typing: ずれても、リンクは印の場所 (実際: " + JSON.stringify(s.ed.getText()) + ")");
  }
  {  // アップロードの間に、印を消されたら、貼らない
    let release;
    const s = scenario("a.md", () => new Promise(r => { release = () => r({ok: true, status: 200, text: ""}); }));
    s.pasteEvent([s.file("p.png", "image/png")]);
    await until(() => !!release, "アップロードの開始");
    s.ed.setText(DOC);  // 印ごと消える
    release();
    await s.done();
    check(s.ed.getText() === DOC, "removed: 印が無ければ何も貼らない");
  }
  {  // アップロードの間に、編集画面を閉じても、何も起きない (別のファイルに貼らない)
    let release;
    const s = scenario("a.md", () => new Promise(r => { release = () => r({ok: true, status: 200, text: ""}); }));
    s.pasteEvent([s.file("p.png", "image/png")]);
    await until(() => !!release, "アップロードの開始");
    s.host.remove();  // 編集画面を閉じる
    release();
    await s.done();
    check(s.ed.getText().includes("<!--attach-1-0-->") && !s.ed.getText().includes("![p]"), "closed: 閉じたエディタには触らない");
  }
  {  // 文字のドラッグ (Files でない) は、エディタに任せる
    const s = scenario("a.md");
    s.dropEvent([], ["text/plain"]);
    await sleep(30);
    check(s.uploads.length === 0, "text drop: 文字のドラッグはアップロードしない");
    check(s.mainSaw() === 1, "text drop: 文字のドラッグは止めない (CodeMirror と上位へ伝わる)");
  }
  {  // 名前と拡張子の決め方
    const s = scenario("a.md");
    const M = s.w.MdAttach, now = new s.w.Date(2026, 10, 25, 9, 5, 7);
    const n = (name, type) => M.attachName({name: name, type: type}, now);
    check(n("image.png", "image/png") === "image-20261125-090507.png", "name: ペーストの image.png は日時の名前");
    check(n("", "image/jpeg") === "image-20261125-090507.jpg", "name: 名前の無い画像は、MIME から拡張子");
    check(n("shot", "image/png") === "shot.png", "name: 拡張子の無い画像は、MIME から拡張子");
    check(n("a/b\\c<d>.pdf", "application/pdf") === "c_d_.pdf", "name: パス区切りは落とし、使えない文字は _");
    check(n("..", "") === "file-20261125-090507", "name: 空になる名前は file-日時");
    check(n("a.tar.gz", "application/gzip") === "a.tar.gz", "name: 拡張子は 2 重でも保つ");
    check(M.withSuffix("a.png", 2) === "a-2.png" && M.withSuffix("noext", 3) === "noext-3", "name: 重なったときの接尾辞");
    check(M.attachDir("README.md") === "attachments" && M.attachDir("a/b/c.md") === "a/b/attachments", "dir: Markdown と同じ階層");
    check(M.linkPart("a b(c)#d?%") === "a%20b%28c%29%23d%3F%25", "link: 壊れる文字だけ %xx");
  }

  if (failures.length) { console.error(failures.join("\n")); process.exit(1); }
  console.log("ok");
  process.exit(0);
})();
