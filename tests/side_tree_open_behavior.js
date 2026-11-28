// 左ペインのツリーが、読み取り専用のコミット表示でも働くこと・URL で直接ファイル / フォルダを開いたとき、その所まで展開して印が付くこと・
// 「変更ファイル」のタブに切り替えて戻っても、ツリーが保たれ、ルートの行を選んでしまわないことを、実サーバーと実際の /files ページ (jsdom) で確かめる。
// 実行: GG_BASE=http://127.0.0.1:ポート GG_HEAD=コミットのハッシュ node tests/side_tree_open_behavior.js (jsdom が require できること)。
// tests/test_side_tree_ui.py から呼ぶ。失敗は終了コード 1 と標準エラーへ。
const {JSDOM} = require("jsdom");
const BASE = process.env.GG_BASE, HEAD = process.env.GG_HEAD;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const failures = [];
const check = (cond, msg) => { if (!cond) failures.push(msg); };

async function open(url) {
  const dom = await JSDOM.fromURL(BASE + url, {
    runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) {
      w.fetch = (u, o = {}) => {
        // jsdom の AbortSignal は Node fetch が受け取れないので、abort を Node 側へ橋渡しする。
        let options = o;
        if (o.signal && !(o.signal instanceof AbortSignal)) {
          const controller = new AbortController();
          if (o.signal.aborted) controller.abort(o.signal.reason);
          else o.signal.addEventListener("abort", () => controller.abort(o.signal.reason), {once: true});
          options = Object.assign({}, o, {signal: controller.signal});
        }
        return fetch(new URL(u, BASE), options);
      };
      const rect = {left: 0, right: 0, top: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0};  // jsdom に無い、レイアウトの API を空の値で補う
      w.Range.prototype.getClientRects = () => []; w.Range.prototype.getBoundingClientRect = () => rect; w.Element.prototype.getClientRects = () => [];
      w.document.elementFromPoint = () => null; w.Element.prototype.scrollIntoView = () => {}; w.confirm = () => true; w.scrollTo = () => {};
    },
  });
  const w = dom.window, g = e => w.eval(e);
  const rows = () => [...w.document.querySelectorAll("#list .entry")].map(e => e.dataset.path + (e.classList.contains("selected") ? "*" : "") + (e.classList.contains("opened") ? "^" : ""));
  async function until(cond, what) {
    for (let i = 0; i < 400; i++) { try { if (cond()) return true; } catch (e) { /* まだ準備中 */ } await sleep(20); }
    failures.push("待ちきれませんでした: " + what + " " + JSON.stringify(rows()));
    return false;
  }
  await until(() => typeof w.loadFile === "function" && g("dirEntries") && w.document.querySelector("#list .entry:not(.root)"), "ページの準備");
  await until(() => !w.document.getElementById("content").textContent.includes("Click a file"), "右ペインの表示 (起動直後の案内文が消える)");
  await sleep(300);  // 起動処理 (ファイルを開く) が終わるのを待つ
  const row = p => [...w.document.querySelectorAll("#list .entry")].find(e => e.dataset.path === p);
  return {w, g, rows, row, until};
}

(async () => {
  // 1. コミット表示 (読み取り専用): ツリーは働く。ドラッグ・チェック・移動ボタンは出さない
  let p = await open("/files?hash=" + HEAD);
  check(p.rows()[0] === "^" && ["a", "b", "top.txt"].every(x => p.rows().includes(x)) && !p.rows().includes("a/x.txt"), "コミット表示: ルートの行 (右ペインで開いている印) と、閉じたルートの項目: " + p.rows());
  p.row("a").click();
  await p.until(() => p.row("a/sub") && p.row("a/x.txt"), "コミット表示でも、フォルダが展開する");
  check(p.g("current") === "" && p.rows().indexOf("a*") >= 0, "展開しても、右ペインは動かない");
  check(![...p.w.document.querySelectorAll("#list .entry")].some(e => e.getAttribute("draggable")), "コミット表示の行は、ドラッグできない");
  check(p.w.document.querySelectorAll("#list input.ck").length === 0 && p.w.document.getElementById("selBar").hidden, "チェックと選択バー (移動を含む) は出ない");
  p.w.document.querySelector('#list .entry[data-path="a"]').dispatchEvent(new p.w.MouseEvent("click", {bubbles: true, ctrlKey: true}));
  check(p.w.document.querySelectorAll("#list .entry.multi").length === 0, "コミット表示では、Ctrl + クリックで選択しない");

  // 2. URL で、入れ子のフォルダの中のファイルを直接開く: その所まで展開し、そのファイルの行を選ぶ
  p = await open("/files?hash=WIP&path=a%2Fsub&file=a%2Fsub%2Fy.txt");
  check(["a", "a/sub", "a/sub/y.txt*", "a/x.txt"].every(x => p.rows().includes(x)) && !p.rows().includes("b/z.txt") && !p.rows().some(r => r.endsWith("^")),
        "ファイルを直接開く: 祖先まで展開し、そのファイルの行を選ぶ (フォルダの「開いている」印は付けない): " + p.rows());
  check(p.g("currentFile") === "a/sub/y.txt", "右ペインには、そのファイル");

  // 3. URL で、フォルダを直接開く: そのフォルダまで展開し、「右ペインで開いている」印を付ける
  p = await open("/files?hash=WIP&path=b");
  check(p.rows().includes("b^") && p.rows().includes("b/z.txt") && !p.rows().includes("a/x.txt"), "フォルダを直接開く: そのフォルダまで展開し、「開いている」印を付ける: " + p.rows());

  // 4. 「変更ファイル」に切り替えて戻っても、ツリーは保たれ、ルートの行を選んでしまわない
  p.row("a").click();
  await p.until(() => p.row("a/x.txt"), "a を展開する");
  const before = p.rows().map(r => r === "a*" ? "a" : r).join();  // 選んでいた a の「選んでいる」印だけは、タブを切り替えると外れる (元からの動作)
  p.w.eval('applySideMode("changes")');
  await sleep(200);
  p.w.eval('applySideMode("tree")');
  await sleep(300);
  check(p.rows().join() === before, "タブを切り替えて戻っても、ツリーは同じ (ルートの行は選ばれない): " + p.rows());

  if (failures.length) { console.error(failures.join("\n")); process.exit(1); }
  console.log("ok");
  process.exit(0);
})().catch(e => { console.error(failures.concat(["ERR " + (e && e.stack || e)]).join("\n")); process.exit(2); });
