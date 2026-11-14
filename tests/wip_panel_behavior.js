// 3 画面共通の WIP の一覧とコミットフォーム (static/wip_panel.js) の振る舞いの検査。
// 実行: node tests/wip_panel_behavior.js (jsdom が require できること)。tests/test_wip_panel.py から呼ぶ。失敗は終了コード 1 と標準エラーへ。
const fs = require("fs"), path = require("path");
const {JSDOM} = require("jsdom");
const source = fs.readFileSync(path.join(__dirname, "..", "git_graph", "static", "wip_panel.js"), "utf8");
const failures = [];
const check = (cond, msg) => { if (!cond) failures.push(msg); };
const file = (p, extra) => Object.assign({path: p, st: "M", added: 2, deleted: 1}, extra || {});

async function scenario(name, status, opts) {
  opts = opts || {};
  const dom = new JSDOM('<div id="root"></div>', {url: "http://x/", runScripts: "outside-only"});
  const w = dom.window;
  w.eval(source + "; window.WipPanel = WipPanel;");
  w.confirm = () => true;
  const calls = [], posts = [], changes = [];
  const host = {
    t: (k, p) => k + (p ? JSON.stringify(p) : ""),
    get: (url, params) => { calls.push(params); return Promise.resolve(typeof status === "function" ? status(params) : status); },
    post: (url, body) => { posts.push([url, body]); return Promise.resolve({message: "done", hash: "commit-hash"}); },
    dialog: () => Promise.resolve(null),
    canWrite: () => opts.canWrite !== false,
    afterChange: (...args) => { changes.push(args); },
  };
  const panel = w.WipPanel.create(host);
  const root = w.document.getElementById("root");
  await panel.render(root, {});
  const q = s => root.querySelector(s), all = s => [...root.querySelectorAll(s)];
  const ck = (kind, p) => root.querySelector('.wipck[data-kind="' + kind + '"][data-path="' + p + '"]');
  return {w, root, q, all, ck, panel, calls, posts, name};
}
const base = {head: "abc1234", head_message: "prev", staged: [], unstaged: [], untracked: [], conflicts: [], amend_files: []};

(async () => {
  // 通常: ステージ済みだけが既定でチェック済み (同じファイルでも、ステージ済みと未ステージは別々)
  let s = await scenario("plain", Object.assign({}, base, {staged: [file("tracked.txt")], unstaged: [file("tracked.txt")], untracked: [file("new.txt", {st: "?"})]}));
  check(s.ck("staged", "tracked.txt").checked, "plain: staged is checked");
  check(!s.ck("unstaged", "tracked.txt").checked, "plain: unstaged is not checked");
  check(!s.ck("untracked", "new.txt").checked, "plain: untracked is not checked");
  check(s.q("#wipCommit").textContent.includes("(1)"), "plain: commit button counts 1");
  for (const id of ["#wipBranch", "#wipDiscard", "#wipRm", "#wipStash", "#wipAmendCk", "#wipMsg"]) check(s.q(id), "plain: has " + id);
  check(!s.q("#wipAbort") && !s.q("[data-resolve]"), "plain: no abort / resolve");
  check(s.all("tr.frow .st").length === 3 && s.all("tr.frow .fb .plus").length === 3, "plain: diff marks (badge and bars) on every row");
  // コミット: ステージ済みだけをコミットする / ブランチ名も送る
  s.q("#wipMsg").value = "msg"; s.q("#wipBranch").value = " topic ";
  s.q("#wipCommit").click(); await new Promise(r => setTimeout(r, 10));
  const post = s.posts.find(p => p[0] === "/api/wip-commit");
  check(post && post[1].message === "msg" && post[1].branch === "topic" && JSON.stringify(post[1].staged_paths) === '["tracked.txt"]' && post[1].paths.length === 0 && post[1].amend === false,
        "plain: commit payload " + JSON.stringify(post));
  check(s.changes.length === 1 && s.changes[0][1].hash === "commit-hash", "plain: commit hash is passed to afterChange");

  // amend: 切り替えると amend の一覧を取り、メッセージを直前のものにする
  s = await scenario("amend", p => p && p.amend ? Object.assign({}, base, {amend_files: [file("a.txt", {in_head: true})]}) : Object.assign({}, base, {staged: [file("tracked.txt")]}));
  s.q("#wipAmendCk").checked = true; s.q("#wipAmendCk").dispatchEvent(new s.w.Event("change", {bubbles: true}));
  await new Promise(r => setTimeout(r, 10));
  check(s.calls.some(p => p && p.amend === 1), "amend: fetches amend list");
  check(s.q("#wipMsg").value === "prev" && s.ck("amend", "a.txt").checked && !s.q("#wipBranch") && !s.q("#wipStash"), "amend: previous message, file checked, no branch/stash");

  // マージ中: 競合ファイルはチェック済み、未追跡は未チェック。解決ボタンと中断。stash / discard は出さず、rm は出す
  s = await scenario("merging", Object.assign({}, base, {merging: true, conflicts: [file("tracked.txt", {st: "U", label: "label_conflict"})], untracked: [file("new.txt", {st: "?"})]}));
  check(s.ck("conflict", "tracked.txt").checked && !s.ck("untracked", "new.txt").checked, "merging: conflict checked, untracked not");
  check(["ours", "theirs", "current"].every(x => s.q('[data-resolve="' + x + '"]')), "merging: resolve buttons");
  check(s.q("#wipAbort") && s.q("#wipAbort").dataset.cmd === "merge" && !s.q("#wipContinue"), "merging: abort(merge), no continue");
  check(s.q("#wipRm") && !s.q("#wipStash") && !s.q("#wipDiscard"), "merging: rm only");

  // チェリーピック / リバート中も、競合ファイルはチェック済み
  for (const flag of ["cherry_picking", "reverting"]) {
    s = await scenario(flag, Object.assign({}, base, {[flag]: true, conflicts: [file("tracked.txt", {st: "U"})]}));
    check(s.ck("conflict", "tracked.txt").checked, flag + ": conflict checked");
  }
  // リベース中: 続行ボタンと、ours / theirs の逆転の案内。競合ファイルは既定でチェックしない
  s = await scenario("rebasing", Object.assign({}, base, {rebasing: true, conflicts: [file("tracked.txt", {st: "U"})]}));
  check(s.q("#wipContinue") && s.q("#wipAbort").dataset.cmd === "rebase", "rebasing: continue + abort");
  check(s.root.textContent.includes("rebase_side_hint"), "rebasing: side hint");
  check(!s.ck("conflict", "tracked.txt").checked, "rebasing: conflict not checked by default");
  // am 中は、コミットのフォームを出さない
  s = await scenario("applying", Object.assign({}, base, {applying: true, conflicts: [file("tracked.txt", {st: "U"})]}));
  check(!s.q("#wipCommit") && s.q("#wipAbort").dataset.cmd === "am", "applying: no commit form, abort(am)");
  // 読み取り専用: チェック欄もフォームも出さないが、一覧は出す
  s = await scenario("readonly", Object.assign({}, base, {unstaged: [file("tracked.txt")]}), {canWrite: false});
  check(!s.q(".wipck") && !s.q("#wipCommit") && s.q("table.files tr.frow"), "readonly: list only");
  // 変更なし
  s = await scenario("clean", base);
  check(!s.q("table.files") && s.root.textContent.includes("no_changes"), "clean: no changes text");
  // 破棄: 競合ファイルは破棄できない
  s = await scenario("discard", Object.assign({}, base, {conflicts: [file("c.txt", {st: "U"})], unstaged: [file("u.txt")]}));
  s.ck("conflict", "c.txt").checked = true; s.ck("conflict", "c.txt").dispatchEvent(new s.w.Event("change", {bubbles: true}));
  s.ck("unstaged", "u.txt").checked = false;
  s.q("#wipDiscard").click();
  check(s.posts.length === 0 && s.root.textContent.includes("discard_conflict"), "discard: refuses conflicted files");
  // ファイル一覧の共通部品: 同じ入力は同じ表になる (グラフのコミット詳細も同じ関数)
  const w = new JSDOM("", {runScripts: "outside-only"}).window;
  w.eval(source + "; window.WipPanel = WipPanel;");
  const html = w.WipPanel.filesTable({hash: "abc"}, [file("d/x.txt", {old_path: "d/y.txt", st: "R"})], 0, {t: k => k});
  check(html.includes("d/y.txt =&gt; d/x.txt") && html.includes("stR") && html.includes("hash=abc") &&
        html.includes("file=d%2Fx.txt") && html.includes("view=diff"), "filesTable: rename, badge, file-view URL");

  if (failures.length) { console.error(failures.join("\n")); process.exit(1); }
  console.log("OK");
})().catch(e => { console.error(e.stack || String(e)); process.exit(1); });
