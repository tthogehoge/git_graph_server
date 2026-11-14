// ファイル表示 / 差分ページに、共通の WIP の一覧とコミットフォーム (wip_panel.js) を置く。
// 通信・入力ダイアログの部品は diff_dialog.js、文言は T、メッセージ欄は page_menubar.js の mbSay。
// opts.diff: 差分ページ (URL の path / wip で、開いているファイルの行を強調し、amend の状態も URL で決める)
// opts.onFiles(files): 一覧が決まったとき (省略可。ファイル表示で、畳んだ欄の見出しに件数を出すのに使う)
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
    afterChange: (message, change) => {  // 作業ツリーの状態が変わったので、表示 (一覧・差分・ファイル) をページごと読み込み直す
      panel.say(message, true);
      if (!opts.diff && opts.onFiles && change && change.hash) {
        const params = new URLSearchParams(location.search);
        if (params.get("hash") === "WIP" && params.get("dto") === "WIP" && params.has("dfrom")) {
          params.set("dfrom", change.hash);
          params.delete("dold");
          history.replaceState(history.state, "", location.pathname + "?" + params + location.hash);
        }
      }
      setTimeout(() => location.reload(), 800);
    },
    onFiles: opts.onFiles,
    amendOverride: opts.diff ? () => q.get("wip") === "amend" : undefined,
    current: opts.diff ? f => q.get("path") === f.path && q.get("wip") === f.wip : undefined,
  });
  panel.render(root, {});
  return panel;
}
