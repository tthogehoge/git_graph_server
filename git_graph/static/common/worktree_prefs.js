// ワークツリー設定 (.git/git-graph.json)。ページに埋め込まれた WORKTREE_PREFS を読み、変更時は /api/prefs へ書く。
// localStorage よりリポジトリ側の値を優先する (別リポジトリで同じブラウザを使っても混ざらない)。
(function (global) {
  const base = (typeof WORKTREE_PREFS === "object" && WORKTREE_PREFS) ? WORKTREE_PREFS : {};
  let pending = {};
  let timer = null;

  function has(key) { return Object.prototype.hasOwnProperty.call(base, key); }

  function getBool(key, fallback) {
    if (has(key)) return !!base[key];
    return fallback;
  }

  function setLocal(key, value) {
    base[key] = value;
    pending[key] = value;
    if (timer) clearTimeout(timer);
    timer = setTimeout(flush, 200);
  }

  function flush() {
    timer = null;
    const body = {prefs: pending};
    pending = {};
    fetch("/api/prefs", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body),
    }).catch(() => { /* 保存失敗しても画面上の切り替えは維持 */ });
  }

  global.WorktreePrefs = {
    getBool: getBool,
    setBool: function (key, value) { setLocal(key, !!value); },
    setNumber: function (key, value) { setLocal(key, value); },
    all: function () { return Object.assign({}, base); },
    has: has,
  };
})(window);
