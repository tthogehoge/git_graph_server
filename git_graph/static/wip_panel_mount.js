// ファイル表示 / 差分ページに、共通の WIP の一覧とコミットフォーム (wip_panel.js) を置く。
// 通信・入力ダイアログの部品は diff_dialog.js、文言は T、メッセージ欄は page_menubar.js の mbSay。
// opts.diff: 差分ページ (URL の path / wip で、開いているファイルの行を強調し、amend の状態も URL で決める)
function mountWipPanel(root, opts) {
  opts = opts || {};
  const bar = document.getElementById("compareToolbar");
  const q = new URLSearchParams(location.search);
  const t = (key, params) => fmt(key in T ? T[key] : key, params || {});
  const get = (url, params) => fetch(url + "?" + new URLSearchParams(params)).then(r => r.ok ? r.json() : r.text().then(m => { throw new Error(m || r.status); }));
  const panel = WipPanel.create({
    t: t,
    get: get,
    post: postJson,
    dialog: (title, fieldsHtml, submitLabel) => openDialog(title, form => { form.insertAdjacentHTML("beforeend", fieldsHtml); }, submitLabel),
    canWrite: () => !!bar && bar.dataset.writable === "1" && bar.dataset.worktree === "1",
    say: (text, ok) => mbSay(text, ok),
    afterChange: message => {  // 作業ツリーの状態が変わったので、表示 (一覧・差分・ファイル) をページごと読み込み直す
      panel.say(message, true);
      setTimeout(() => location.reload(), 800);
    },
    amendOverride: opts.diff ? () => q.get("wip") === "amend" : undefined,
    current: opts.diff ? f => q.get("path") === f.path && q.get("wip") === f.wip : undefined,
  });
  panel.render(root, {});
  return panel;
}
