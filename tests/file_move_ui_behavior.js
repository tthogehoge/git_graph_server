// ファイル表示の「名前の変更・移動」を、実サーバーと実際の /files ページのスクリプト (jsdom で実行) で通しで確かめる。
// 実行: GG_BASE=http://127.0.0.1:ポート GG_REPO=リポジトリ node tests/file_move_ui_behavior.js (jsdom が require できること。サーバーは --lang en --allow-write で起動済み)。
// tests/test_file_move_ui.py から呼ぶ。失敗は終了コード 1 と標準エラーへ。
const {JSDOM} = require("jsdom");
const {execFileSync} = require("child_process");
const fs = require("fs"), path = require("path");
const BASE = process.env.GG_BASE, REPO = process.env.GG_REPO;
const sh = (...a) => execFileSync("git", ["-C", REPO, ...a], {encoding: "utf8"});
const ex = p => fs.existsSync(path.join(REPO, p));
const sleep = ms => new Promise(r => setTimeout(r, ms));
const failures = [];
const check = (cond, msg) => { if (!cond) failures.push(msg); };

(async () => {
  const dom = await JSDOM.fromURL(BASE + "/files?hash=WIP", {
    runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) {
      w.netErrors = [];  // 失敗した通信 (原因を追うために、待ちきれたときに付ける)
      w.fetch = (u, o = {}) => {
        // jsdom の AbortSignal は Node fetch が受け取れないので、abort を Node 側へ橋渡しする。
        let options = o;
        if (o.signal && !(o.signal instanceof AbortSignal)) {
          const controller = new AbortController();
          if (o.signal.aborted) controller.abort(o.signal.reason);
          else o.signal.addEventListener("abort", () => controller.abort(o.signal.reason), {once: true});
          options = Object.assign({}, o, {signal: controller.signal});
        }
        return fetch(new URL(u, BASE), options).then(r => { if (!r.ok) r.clone().text().then(m => w.netErrors.push(r.status + " " + String(u).slice(0, 60) + " " + m.slice(0, 120))); return r; },
                                                          e => { w.netErrors.push("fetch failed " + String(u).slice(0, 60) + " " + e); throw e; });
      };
      // jsdom に無い、レイアウトの API (CodeMirror が測定に使う) を空の値で補う
      const rect = {left: 0, right: 0, top: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0};
      w.Range.prototype.getClientRects = () => []; w.Range.prototype.getBoundingClientRect = () => rect;
      w.Element.prototype.getClientRects = () => []; w.document.elementFromPoint = () => null;
      w.confirm = () => true; w.scrollTo = () => {};
    },
  });
  const w = dom.window, d = w.document, $ = id => d.getElementById(id);
  const g = expr => w.eval(expr);  // ページの let の変数 (current / currentFile など) は window に出ないので、ページの中で評価して読む
  async function until(cond, what) {
    for (let i = 0; i < 400; i++) { try { if (cond()) return true; } catch (e) { /* まだ準備中 */ } await sleep(20); }
    let state = "";
    try { state = JSON.stringify({current: g("current"), currentFile: g("currentFile"), content: $("content").textContent.replace(/\s+/g, " ").slice(0, 160), netErrors: w.netErrors}); } catch (e) { /* 状態は取れなくてもよい */ }
    failures.push("待ちきれませんでした: " + what + " " + state);  // ページの状態も付けて、原因を追いやすくする
    return false;
  }
  const toast = kind => { const e = d.querySelector("#statusToasts .status-toast." + kind); return e ? e.dataset.message : ""; };
  const clearToasts = () => d.querySelectorAll("#statusToasts .status-toast").forEach(e => e.remove());
  async function moveVia(btnId, newPath) {  // ボタンを押して、ダイアログに新しいパスを入れて送信する。ダイアログの初期の入力と選択を返す
    await until(() => !$(btnId).hidden && !$(btnId).disabled, btnId + " が押せる");
    $(btnId).click();
    if (!await until(() => $("dlg"), "ダイアログが開く")) throw new Error("no dialog");
    const el = $("dlg").querySelector("input[name=path]");
    const initial = [el.selectionStart, el.selectionEnd, el.value];
    el.value = newPath;
    $("dlg").dispatchEvent(new w.Event("submit", {cancelable: true}));
    return initial;
  }
  const staged = () => sh("status", "--porcelain").split("\n").filter(l => l && !l.startsWith("??")).join();

  // ページの起動処理が、最初のディレクトリ一覧を出し終えるまで待つ (その前にファイルを開くと、起動処理に表示を消される)
  await until(() => typeof w.loadFile === "function" && g("dirEntries") && $("content").textContent.includes("keep.txt"), "ページの準備 (最初の一覧)");

  // 1. ファイルの名前の変更 (追跡中 → git mv でステージ)
  w.loadFile("dir/a.txt", true);
  await until(() => g("fileData") && g("currentFile") === "dir/a.txt", "a.txt を開く");
  check(!$("fileMoveBtn").hidden, "作業ツリーのファイルには、名前の変更・移動のボタンが出る");
  const initial = await moveVia("fileMoveBtn", "dir/b.txt");
  check(initial[2] === "dir/a.txt" && initial[0] === 4 && initial[1] === 5, "ダイアログは、今のパスを入れて、名前の部分 (拡張子の前まで) を選んでおく: " + JSON.stringify(initial));
  await until(() => g("currentFile") === "dir/b.txt" && g("fileData"), "b.txt として開き直す");
  check(!ex("dir/a.txt") && ex("dir/b.txt"), "名前が変わる");
  check(staged() === "R  dir/a.txt -> dir/b.txt", "名前の変更がステージされる: " + JSON.stringify(sh("status", "--porcelain")));
  check(!$("dlg"), "ダイアログは閉じる");
  check(/dir\/a\.txt.*dir\/b\.txt/.test(toast("success")), "成功を知らせる: " + toast("success"));

  // 2. 別のディレクトリへ移動 (無いディレクトリは作る)。左の一覧も、新しい場所へ
  await moveVia("fileMoveBtn", "other/deep/b.txt");
  await until(() => g("currentFile") === "other/deep/b.txt" && g("fileData"), "新しい場所で開き直す");
  check(ex("other/deep/b.txt") && !ex("dir/b.txt"), "別のディレクトリへ移る");
  check(g("current") === "other/deep", "左の一覧は、新しい場所: " + g("current"));

  // 3. フォルダの名前の変更 (一覧の「名前の変更・移動」)
  w.openDir("other");
  await until(() => g("current") === "other" && !g("currentFile"), "other を開く");
  await moveVia("dirMoveBtn", "renamed");
  // 見出し (パンくず) に renamed が出るまで待つ (元の other の一覧にも deep があるので、本文だけでは見分けられない。
  // 一覧の読み込みが終わる前に次へ進むと、遅れて届いた応答が表示中の一覧を上書きする)
  await until(() => g("current") === "renamed" && g("title.textContent").includes("renamed") && $("content").textContent.includes("deep"), "renamed の一覧が出る");
  check(ex("renamed/deep/b.txt") && !ex("other"), "フォルダごと動く");

  // 4. ルートのフォルダには、ボタンが出ない
  w.openDir("");
  await until(() => g("current") === "" && !g("currentFile") && $("content").textContent.includes("keep.txt"), "ルートの一覧が出る");
  check($("dirMoveBtn").hidden, "ルートは動かせないので、ボタンを出さない");
  w.openDir("renamed");
  await until(() => g("current") === "renamed" && $("content").textContent.includes("deep"), "サブディレクトリの一覧が出る");
  check(!$("dirMoveBtn").hidden, "サブディレクトリには、ボタンが出る");

  // 5. 未追跡のファイル
  w.loadFile("u.txt", true);
  await until(() => g("currentFile") === "u.txt" && g("fileData"), "u.txt を開く");
  await moveVia("fileMoveBtn", "v.txt");
  await until(() => g("currentFile") === "v.txt" && g("fileData"), "v.txt を開く");
  check(ex("v.txt") && !ex("u.txt"), "未追跡のファイルも、名前が変わる");

  // 6. 移動先に既にあれば、上書きせずエラー (何も変わらない)
  clearToasts();
  w.loadFile("keep.txt", true);
  await until(() => g("currentFile") === "keep.txt" && g("fileData"), "keep.txt を開く");
  await moveVia("fileMoveBtn", "v.txt");
  await until(() => toast("error") && !$("dlg"), "エラーが出る");
  check(/Already exists: v\.txt/.test(toast("error")), "既にあるパスを示す: " + toast("error"));
  check(ex("keep.txt") && fs.readFileSync(path.join(REPO, "v.txt"), "utf8") === "untracked\n" && g("currentFile") === "keep.txt", "何も変わらない");

  // 7. 今と同じパス・空のパスは、送らずに断る
  clearToasts();
  await moveVia("fileMoveBtn", "keep.txt");
  await until(() => toast("error"), "同じパスのエラー");
  check(/same as the current/.test(toast("error")) && ex("keep.txt"), "同じパスは断る: " + toast("error"));
  clearToasts();
  await moveVia("fileMoveBtn", "  ");
  await until(() => toast("error"), "空のパスのエラー");
  check(/invalid/.test(toast("error")) && ex("keep.txt"), "空のパスは断る: " + toast("error"));

  // 8. 編集中は、ボタンを出さない
  await until(() => !$("fileEditBtn").disabled, "編集できる");
  $("fileEditBtn").click();
  await until(() => g("editing") === true, "編集に入る");
  check($("fileMoveBtn").hidden, "編集中は、名前の変更・移動のボタンを出さない");

  if (failures.length) { console.error(failures.join("\n")); process.exit(1); }
  console.log("ok");
  process.exit(0);
})().catch(e => { console.error("ERR", e); process.exit(2); });
