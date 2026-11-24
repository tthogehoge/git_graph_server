// pywebview 用のページ内検索 (git_graph_webview.py の _FIND_BAR_JS。Ctrl+F の検索バー) と、CodeMirror のエディタの検索が競合しないことの検査。
// 実行: node tests/webview_find_bar_behavior.js (jsdom が require できること)。tests/test_webview_find_bar.py から呼ぶ。失敗は終了コード 1 と標準エラーへ。
const fs = require("fs"), path = require("path");
const {JSDOM} = require("jsdom");
const py = fs.readFileSync(path.join(__dirname, "..", "git_graph_webview.py"), "utf8");
const m = py.match(/_FIND_BAR_JS = """([\s\S]*?)"""/);
if (!m) { console.error("_FIND_BAR_JS が見つかりません"); process.exit(1); }
const source = m[1];
const failures = [];
const check = (cond, msg) => { if (!cond) failures.push(msg); };
const sleep = ms => new Promise(r => setTimeout(r, ms));

function page() {
  const dom = new JSDOM(
    '<body><p id="plain">hello page</p>' +
    '<div class="cm-editor"><div class="cm-panels"><input id="cmsearch"></div>' +
    '<div class="cm-content" id="cm" tabindex="0">hello editor</div></div></body>',
    {url: "http://x/", runScripts: "outside-only"});
  const w = dom.window;
  w.eval(source);
  const bar = () => w.document.getElementById("ggFindBar");
  const shown = () => !!bar() && bar().style.display !== "none";
  // キーを target に送る。戻り値: {prevented: 既定の動作が止められたか, reached: target (エディタ) まで届いたか}
  const press = (target, init) => {
    let reached = false;
    const probe = () => { reached = true; };
    target.addEventListener("keydown", probe);  // CodeMirror のキー処理 (contentDOM の bubbling) の代わり
    const ev = new w.KeyboardEvent("keydown", Object.assign({bubbles: true, cancelable: true}, init));
    target.dispatchEvent(ev);
    target.removeEventListener("keydown", probe);
    return {prevented: ev.defaultPrevented, reached};
  };
  return {w, bar, shown, press, $: id => w.document.getElementById(id)};
}

(async () => {
  const ctrlF = {key: "f", ctrlKey: true};

  {  // エディタの外では、これまでどおり検索バーが開く
    const p = page();
    const r = p.press(p.w.document.body, ctrlF);
    check(p.shown(), "エディタの外の Ctrl+F で検索バーが開く");
    check(r.prevented, "エディタの外の Ctrl+F は、ブラウザの既定の動作を止める");
  }
  {  // エディタの中の Ctrl+F は、検索バーを開かず、エディタまで届く (エディタ自身の検索に任せる)
    const p = page();
    const r = p.press(p.$("cm"), ctrlF);
    check(!p.shown(), "エディタの中の Ctrl+F では検索バーを開かない");
    check(r.reached, "エディタの中の Ctrl+F がエディタまで届く (止めない)");
    check(!r.prevented, "エディタの中の Ctrl+F の既定の動作は、エディタ側が決める (こちらでは止めない)");
  }
  {  // エディタの検索パネルの入力欄 (エディタの中) でも同じ
    const p = page();
    const r = p.press(p.$("cmsearch"), ctrlF);
    check(!p.shown() && r.reached && !r.prevented, "エディタの検索パネルの入力欄の Ctrl+F も、エディタに任せる");
  }
  {  // Cmd+F (Mac) も同じ
    const p = page();
    const outside = p.press(p.w.document.body, {key: "f", metaKey: true});
    check(p.shown() && outside.prevented, "エディタの外の Cmd+F で検索バーが開く");
    const p2 = page();
    const inside = p2.press(p2.$("cm"), {key: "f", metaKey: true});
    check(!p2.shown() && inside.reached && !inside.prevented, "エディタの中の Cmd+F はエディタに任せる");
  }
  {  // F3: 検索バーが開いているときも、エディタの中ではエディタの「次を検索」に任せる
    const p = page();
    p.press(p.w.document.body, ctrlF);  // 検索バーを開く
    const inside = p.press(p.$("cm"), {key: "F3"});
    check(inside.reached && !inside.prevented, "エディタの中の F3 はエディタに任せる");
    const outside = p.press(p.w.document.body, {key: "F3"});
    check(outside.prevented, "エディタの外の F3 は、検索バーの次へ進む");
  }
  {  // ページ内検索は、エディタの中の文字を探さない (<mark> を差し込むと、エディタが DOM の変化を編集と受け取るため)
    const p = page();
    p.press(p.w.document.body, ctrlF);
    const input = p.bar().querySelector("input");
    input.value = "hello";
    input.dispatchEvent(new p.w.Event("input", {bubbles: true}));
    await sleep(250);
    const marks = [...p.w.document.querySelectorAll("mark.gg-find")];
    check(marks.length === 1, "ページ内検索の一致は、エディタの外の 1 件だけ (実際は " + marks.length + " 件)");
    check(marks.every(x => !x.closest(".cm-editor")), "エディタの中に <mark> を差し込まない");
    check(p.$("cm").textContent === "hello editor" && p.$("cm").childNodes.length === 1, "エディタの DOM は検索で変わらない");
  }

  if (failures.length) { console.error(failures.join("\n")); process.exit(1); }
  console.log("ok");
  process.exit(0);
})();
