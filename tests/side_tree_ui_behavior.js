// 左ペインのツリー (フォルダのクリックで展開、「右ペインで開く」アイコン、キーボード)・ドラッグ&ドロップでの移動・選択してまとめての移動を、
// 実サーバーと実際の /files ページのスクリプト (jsdom で実行) で通しで確かめる。
// 実行: GG_BASE=http://127.0.0.1:ポート GG_REPO=リポジトリ node tests/side_tree_ui_behavior.js (jsdom が require できること。サーバーは --lang en --allow-write で起動済み)。
// tests/test_side_tree_ui.py から呼ぶ。失敗は終了コード 1 と標準エラーへ。
const {JSDOM} = require("jsdom");
const {execFileSync} = require("child_process");
const fs = require("fs"), path = require("path");
const BASE = process.env.GG_BASE, REPO = process.env.GG_REPO;
const sh = (...a) => execFileSync("git", ["-C", REPO, ...a], {encoding: "utf8"});
const ex = p => fs.existsSync(path.join(REPO, p));
const read = p => fs.readFileSync(path.join(REPO, p), "utf8");
const sleep = ms => new Promise(r => setTimeout(r, ms));
const failures = [];
const check = (cond, msg) => { if (!cond) failures.push(msg); };
const MOVE_MIME = "application/x-git-graph-move";

(async () => {
  const dom = await JSDOM.fromURL(BASE + "/files?hash=WIP", {
    runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(w) {
      w.netErrors = [];
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
      const rect = {left: 0, right: 0, top: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0};  // jsdom に無い、レイアウトの API (CodeMirror が測定に使う) を空の値で補う
      w.Range.prototype.getClientRects = () => []; w.Range.prototype.getBoundingClientRect = () => rect;
      w.Element.prototype.getClientRects = () => []; w.document.elementFromPoint = () => null; w.Element.prototype.scrollIntoView = () => {};
      w.confirm = () => true; w.scrollTo = () => {};
    },
  });
  const w = dom.window, d = w.document, $ = id => d.getElementById(id);
  const g = expr => w.eval(expr);  // ページの let の変数 (current / currentFile など) は window に出ないので、ページの中で評価して読む
  async function until(cond, what) {
    for (let i = 0; i < 400; i++) { try { if (cond()) return true; } catch (e) { /* まだ準備中 */ } await sleep(20); }
    let state = "";
    try { state = JSON.stringify({current: g("current"), currentFile: g("currentFile"), content: $("content").textContent.replace(/\s+/g, " ").slice(0, 120), netErrors: w.netErrors, toasts: toasts()}); } catch (e) { /* 状態は取れなくてもよい */ }
    failures.push("待ちきれませんでした: " + what + " " + state);
    return false;
  }
  const side = p => [...d.querySelectorAll("#list .entry")].find(e => e.dataset.path === p) || null;
  const sidePaths = () => [...d.querySelectorAll("#list .entry:not(.root)")].map(e => e.dataset.path);
  const right = p => d.querySelector('#content tr.dir-log-row[data-path="' + p + '"]');
  const toasts = () => [...d.querySelectorAll("#statusToasts .status-toast")].map(e => e.className.replace("status-toast", "").trim() + ":" + e.dataset.message).join(" | ");
  const toast = kind => { const e = d.querySelector("#statusToasts .status-toast." + kind); return e ? e.dataset.message : ""; };
  const clearToasts = () => d.querySelectorAll("#statusToasts .status-toast").forEach(e => e.remove());
  const staged = () => sh("status", "--porcelain").split("\n").filter(l => l && !l.startsWith("??")).sort().join(" | ");
  const click = (el, init) => el.dispatchEvent(new w.MouseEvent("click", Object.assign({bubbles: true, cancelable: true}, init)));
  const key = (el, k) => el.dispatchEvent(new w.KeyboardEvent("keydown", {key: k, bubbles: true, cancelable: true}));
  // 移動の後始末 (左のツリーと右ペインの読み直し) が終わるまで待つ。成功のトーストは、その前に出る
  const settled = () => until(() => g("moving") === false && !d.querySelector("#list .side-note"), "移動の後始末が終わる");
  const selectedPath = () => { const e = d.querySelector("#list .entry.selected"); return e ? e.dataset.path : null; };

  // ドラッグ&ドロップ: jsdom には DataTransfer が無いので、同じ入れ物を dragstart / dragover / drop に渡す
  function fakeTransfer() {
    const store = {};
    return {types: [], effectAllowed: "", dropEffect: "", setData(t, v) { store[t] = v; if (this.types.indexOf(t) < 0) this.types.push(t); }, getData: t => store[t] || "", store: store};
  }
  const fire = (el, type, dt) => { const ev = new w.Event(type, {bubbles: true, cancelable: true}); Object.defineProperty(ev, "dataTransfer", {value: dt}); el.dispatchEvent(ev); return ev; };
  // from を掴んで to の上へ。置ける (dragover を受け付けた) なら置く。置けたかを返す
  function drag(from, to, opts) {
    opts = opts || {};
    const dt = fakeTransfer();
    fire(from, "dragstart", dt);
    const over = fire(to, "dragover", dt);
    const ok = over.defaultPrevented;
    const hover = ok && to.classList.contains("drop-hover");
    if (ok && !opts.noDrop) fire(to, "drop", dt);
    if (!opts.keep) fire(from, "dragend", dt);
    return {ok: ok, hover: hover, dt: dt};
  }
  async function dialogSubmit(values) {  // 開いたダイアログの入力欄に入れて送信する
    if (!await until(() => $("dlg"), "ダイアログが開く")) throw new Error("no dialog");
    const initial = {};
    Object.keys(values).forEach(name => { const el = $("dlg").querySelector("input[name=" + name + "]"); initial[name] = el.value; el.value = values[name]; });
    $("dlg").dispatchEvent(new w.Event("submit", {cancelable: true}));
    return initial;
  }

  // 左のツリーは、右ペインの一覧より先に描かれる。右ペインにルートの一覧が出る (起動直後の案内文が消える) まで待つ
  await until(() => typeof w.loadFile === "function" && g("dirEntries") && side("top.txt") && $("content").textContent.includes("top.txt"), "最初の左のツリーと右ペインの一覧");

  // ---- 1. 左のツリー: ルートの行・フォルダ (▸ とアイコン)・ファイル ----
  check(!!d.querySelector("#list .entry.root[data-path='']"), "ルートの行がある");
  check(["a", "b", "c", "dest", "top.txt"].every(p => side(p)) && !side("a/x.txt"), "最初は、ルートの項目だけ (フォルダは閉じている): " + sidePaths());
  check(side("a").querySelector(".twisty").textContent === "▸" && !!side("a").querySelector("button.open-right"), "フォルダには、展開の印と、右ペインで開くアイコンがある");
  check(!side("top.txt").querySelector("button.open-right") && !side("top.txt").querySelector(".twisty").textContent, "ファイルには、アイコンも展開の印も無い");
  check(side("a").getAttribute("draggable") === "true", "作業ツリーの行は、ドラッグできる");

  // ---- 2. フォルダのクリックは、展開するだけ (右ペインは動かさない) ----
  click(side("a"));
  await until(() => side("a/x.txt") && side("a/sub"), "a が展開する");
  check(side("a").querySelector(".twisty").textContent === "▾", "展開の印が変わる");
  check(+side("a/x.txt").dataset.depth === 2 && sidePaths().indexOf("a/x.txt") > sidePaths().indexOf("a"), "子は、フォルダの下に、1 段深く並ぶ");
  check(g("current") === "" && !g("currentFile") && $("content").textContent.includes("top.txt"), "右ペインは、動かない (ルートの一覧のまま): " +
        JSON.stringify({current: g("current"), currentFile: g("currentFile"), content: $("content").textContent.replace(/\s+/g, " ").slice(0, 100), netErrors: w.netErrors}));
  click(side("a/sub"));
  await until(() => side("a/sub/y.txt"), "入れ子のフォルダも展開する");
  click(side("a"));
  await until(() => !side("a/x.txt") && !side("a/sub/y.txt"), "もう一度クリックすると、子ごとたたまれる");
  check(g("current") === "", "たたんでも、右ペインは動かない");
  click(side("a"));
  await until(() => side("a/sub") && side("a/sub/y.txt"), "展開し直すと、入れ子の展開も覚えている");

  // ---- 3. 「右ペインで開く」アイコン: 右ペインだけ動く。左の展開はそのまま。開いているフォルダに印 ----
  side("b").querySelector("button.open-right").click();
  await until(() => g("current") === "b" && $("content").textContent.includes("z.txt"), "b を右ペインに開く");
  check(!!side("a/sub/y.txt"), "左の展開は、そのまま");
  check(side("b").classList.contains("opened") && !side("a").classList.contains("opened"), "右ペインで開いているフォルダの行に、印が付く");
  check(g("title.textContent").includes("b"), "右ペインの見出しは、b");
  side("a/x.txt").click();
  await until(() => g("currentFile") === "a/x.txt" && g("fileData"), "ファイルのクリックは、右ペインに開く");
  check(side("a/x.txt").classList.contains("selected"), "開いたファイルの行が選ばれる");

  // ---- 4. キーボード: → 展開 / 最初の子へ、← たたむ / 親へ、Enter 右ペインに開く ----
  click(side("c"));  // 展開して選ぶ
  await until(() => side("c/keep.txt"), "c を展開する");
  check(selectedPath() === "c", "クリックした行が選ばれる");
  key(side("c"), "ArrowLeft");
  await until(() => !side("c/keep.txt"), "← で、展開しているフォルダをたたむ");
  key(side("c"), "ArrowRight");
  await until(() => side("c/keep.txt"), "→ で、フォルダを展開する");
  key(side("c"), "ArrowRight");
  check(selectedPath() === "c/keep.txt", "→ をもう一度で、最初の子の行へ: " + selectedPath());
  key(side("c/keep.txt"), "ArrowLeft");
  check(selectedPath() === "c", "← で、ファイルから親のフォルダの行へ: " + selectedPath());
  key(side("c"), "Enter");
  await until(() => g("current") === "c" && !g("currentFile") && side("c").classList.contains("opened"), "Enter で、フォルダを右ペインに開く");
  check(!!side("c/keep.txt"), "Enter でも、左の展開は変わらない");

  // ---- 5. ドラッグ&ドロップ: 左から左のフォルダへ。置けない所には置けない ----
  const self = drag(side("b"), side("b"), {noDrop: true});
  check(!self.ok, "自分自身の上には置けない");
  const intoOwn = drag(side("a"), side("a/sub"), {noDrop: true});
  check(!intoOwn.ok, "フォルダを自分の中には置けない");
  const already = drag(side("c/keep.txt"), side("c"), {noDrop: true});
  check(!already.ok, "もうそのフォルダの中にあるものは、置けない");
  const onFile = drag(side("top.txt"), side("other.txt"), {noDrop: true});
  check(!onFile.ok, "ファイルの行には置けない");
  const ok1 = drag(side("top.txt"), side("b"), {noDrop: true, keep: true});
  check(ok1.ok && ok1.hover, "フォルダの行の上では、置ける (置き場の印が付く)");
  fire(side("top.txt"), "dragend", ok1.dt);
  check(!d.querySelector(".drop-hover") && !d.querySelector(".dragging"), "ドラッグが終わると、印が消える");
  clearToasts();
  check(drag(side("top.txt"), side("b")).ok, "top.txt を b へ");
  await until(() => ex("b/top.txt") && toast("success"), "top.txt が b へ移る");
  await settled();
  check(!ex("top.txt") && staged() === "R  top.txt -> b/top.txt", "追跡中なので、git mv でステージされる: " + staged());
  check(!!side("b/top.txt") && side("b").querySelector(".twisty").textContent === "▾", "動かし先のフォルダが展開して、動かしたものが見える");
  check(g("current") === "c" && !g("currentFile"), "右ペインは、動かない");

  // ---- 6. 右の一覧 → 左のフォルダ / 左 → 右の一覧のフォルダ / 右の一覧のあき / パンくず ----
  side("").querySelector("button.open-right").click();  // ルートの行のアイコン
  await until(() => g("current") === "" && right("other.txt") && right("b"), "ルートを右ペインに開く");
  check(drag(right("other.txt"), side("c")).ok, "右の一覧の行を、左のフォルダへ");
  await until(() => ex("c/other.txt"), "other.txt が c へ移る");
  await settled();
  check(!right("other.txt"), "右の一覧からも消える");
  check(side("c").querySelector(".twisty").textContent === "▾" && !!side("c/other.txt"), "左のツリーに、動かしたものが出る");
  check(drag(side("a/x.txt"), right("b")).ok, "左の行を、右の一覧のフォルダの行へ");
  await until(() => ex("b/x.txt") && !ex("a/x.txt"), "x.txt が b へ移る");
  await settled();
  check(!!side("b/x.txt") && !side("a/x.txt"), "左のツリーも更新される");
  check(!drag(side("u.txt"), $("content"), {noDrop: true}).ok, "もうそのフォルダ (ルート) の中にあるものは、右の一覧のあきにも置けない");
  side("c").querySelector("button.open-right").click();
  await until(() => g("current") === "c" && right("c/other.txt") && side("c").classList.contains("opened"), "c を右ペインに開く");
  check(drag(side("u.txt"), $("content")).ok, "左の行を、右の一覧のあきへ (開いている c へ)");
  await until(() => ex("c/u.txt") && !ex("u.txt"), "未追跡の u.txt が c へ移る");
  await settled();
  check(sh("status", "--porcelain").includes("?? c/u.txt"), "未追跡のものは、ステージされない");
  check(drag(side("b/x.txt"), d.querySelector('#title a[data-dir=""]')).ok, "左の行を、右ペインのパンくずのルートへ");
  await until(() => ex("x.txt") && !ex("b/x.txt"), "x.txt がルートへ移る");
  await settled();

  // ---- 7. 開いているファイルを動かすと、右ペインも追いかける ----
  side("x.txt").click();
  await until(() => g("currentFile") === "x.txt" && g("fileData"), "x.txt を開く");
  check(drag(side("x.txt"), side("dest")).ok, "開いているファイルを、フォルダへ");
  await until(() => g("currentFile") === "dest/x.txt" && g("fileData"), "右ペインは、新しい場所で開き直す");
  await settled();
  check(ex("dest/x.txt") && !!side("dest/x.txt") && side("dest/x.txt").classList.contains("selected"), "左のツリーでも、その行が選ばれる");

  // ---- 8. 選択 (Ctrl / Shift + クリック) してまとめて ----
  side("").querySelector("button.open-right").click();
  await until(() => g("current") === "" && !g("currentFile") && right("m1.txt"), "ルートを右ペインに開く");
  const selectedPaths = () => [...d.querySelectorAll("#list .entry.multi")].map(e => e.dataset.path).sort().join();
  click(side("m1.txt"));
  await until(() => g("currentFile") === "m1.txt", "m1.txt を開く");
  click(side("m2.txt"), {ctrlKey: true});
  check(selectedPaths() === "m1.txt,m2.txt", "Ctrl + クリックは、いま選んでいる行も含めて、選択に加える: " + selectedPaths());
  click(side("m2.txt"), {ctrlKey: true});
  check(selectedPaths() === "m1.txt", "もう一度 Ctrl + クリックすると、外れる: " + selectedPaths());
  click(side("m1.txt"));
  click(side("m3.txt"), {shiftKey: true});
  check(selectedPaths() === "m1.txt,m2.txt,m3.txt", "Shift + クリックは、範囲を選ぶ: " + selectedPaths());
  check(g("currentFile") === "m1.txt", "選ぶだけでは、右ペインは動かない");
  check(!$("selMoveBtn").disabled, "選ぶと、「移動…」が使える");
  d.dispatchEvent(new w.KeyboardEvent("keydown", {key: "Escape", bubbles: true}));
  side("m1.txt").dispatchEvent(new w.KeyboardEvent("keydown", {key: "Escape", bubbles: true}));
  check(selectedPaths() === "", "Esc で、選択を解く");
  click(side("m1.txt"), {ctrlKey: true}); click(side("m2.txt"), {ctrlKey: true}); click(side("m3.txt"), {ctrlKey: true});
  clearToasts();
  const multi = drag(side("m2.txt"), side("dest"));  // 選んだ行の 1 つを掴めば、選んだ全部
  check(multi.ok && JSON.parse(multi.dt.store[MOVE_MIME]).sort().join() === "m1.txt,m2.txt,m3.txt", "選んだ行の 1 つをドラッグすると、選んだ全部を運ぶ");
  await until(() => ex("dest/m1.txt") && ex("dest/m2.txt") && ex("dest/m3.txt"), "3 つとも dest へ移る");
  await settled();
  check(/3/.test(toast("success")) && !ex("m1.txt"), "まとめて移したことを知らせる: " + toast("success"));
  check(g("currentFile") === "dest/m1.txt", "開いていた m1.txt も追いかける: " + g("currentFile"));
  check(selectedPaths() === "", "移したあとは、選択が解ける");

  // 「移動…」ボタン: 選んだ行を、入力したフォルダへ (既定は、右ペインで開いているフォルダ)
  click(side("dest/m2.txt"));  // m2 を開く (選んでいる行)
  click(side("dest/m3.txt"), {ctrlKey: true});  // m3 を加える
  check(selectedPaths() === "dest/m2.txt,dest/m3.txt", "(m2 と m3 を選ぶ): " + selectedPaths());
  clearToasts();
  $("selMoveBtn").click();
  const initial = await dialogSubmit({dir: "moved/inner"});
  check(initial.dir === g("current"), "ダイアログの既定は、右ペインで開いているフォルダ: " + JSON.stringify(initial));
  await until(() => ex("moved/inner/m2.txt") && ex("moved/inner/m3.txt"), "入力したフォルダ (無ければ作る) へ移る");
  await settled();
  check(!!side("moved/inner/m2.txt"), "左のツリーで、移した先まで展開する");
  // 何も選んでいないとき: 案内を出す
  w.eval("sideMulti.clear(); applySideMulti(); selectEntry(null)");
  clearToasts();
  $("selMoveBtn").click();
  await until(() => toast("error"), "選んだものが無いときの案内");
  check(/Nothing to move/.test(toast("error")) && !$("dlg"), "選んだものが無ければ、案内を出して、ダイアログは開かない: " + toast("error"));

  // ---- 9. 閉じたフォルダの上にしばらくいると開く ----
  if (side("a/sub")) click(side("a"));  // a をたたむ
  await until(() => !side("a/sub"), "a をたたむ");
  const dt = fakeTransfer();
  fire(side("c/other.txt"), "dragstart", dt);
  fire(side("a"), "dragover", dt);
  await sleep(900);
  check(!!side("a/sub"), "閉じたフォルダの上に 0.7 秒いると、そのフォルダが開く");
  fire(side("c/other.txt"), "dragend", dt);
  check(ex("c/other.txt"), "(置かずに離したので、何も動かない)");

  // ---- 10. OS からのファイルのドロップ (アップロード) とは、混ざらない ----
  const osDrop = new w.Event("dragover", {bubbles: true, cancelable: true});
  Object.defineProperty(osDrop, "dataTransfer", {value: {types: ["Files"], dropEffect: ""}});
  side("b").dispatchEvent(osDrop);
  check(!d.querySelector(".drop-hover"), "OS からのファイルのドラッグには、移動の置き場の印を付けない");

  if (failures.length) { console.error(failures.join("\n")); process.exit(1); }
  console.log("ok");
  process.exit(0);
})().catch(e => { console.error(failures.concat(["ERR " + (e && e.stack || e)]).join("\n")); process.exit(2); });  // それまでの失敗も一緒に出す
