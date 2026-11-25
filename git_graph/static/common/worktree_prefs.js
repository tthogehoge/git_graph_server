// ワークツリー設定 (.git/git-graph.json)。
// ページに埋め込まれた WORKTREE_PREFS を初期値にする。画面での切り替えはメモリ上だけ更新し、
// メニュー「ワークツリー設定を保存」で初めてファイルへ書く。
(function (global) {
  const base = (typeof WORKTREE_PREFS === "object" && WORKTREE_PREFS) ? Object.assign({}, WORKTREE_PREFS) : {};

  function has(key) { return Object.prototype.hasOwnProperty.call(base, key); }

  function getBool(key, fallback) {
    if (has(key)) return !!base[key];
    return fallback;
  }

  function setLocal(key, value) {
    base[key] = value;
  }

  function save(opts) {
    // opts: {includeOpts?: bool} — 既定 true でサーバー側のグラフ用 OPTS もファイルへ含める
    const body = {
      prefs: Object.assign({}, base),
      include_opts: !opts || opts.includeOpts !== false,
    };
    return fetch("/api/prefs", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body),
    }).then(r => r.ok ? r.json() : r.text().then(m => { throw new Error(m || r.status); }));
  }

  global.WorktreePrefs = {
    getBool: getBool,
    setBool: function (key, value) { setLocal(key, !!value); },
    setNumber: function (key, value) { setLocal(key, value); },
    all: function () { return Object.assign({}, base); },
    has: has,
    save: save,
  };
})(window);
