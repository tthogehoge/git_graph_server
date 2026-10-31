
// 更新ボタン: ページを読み込み直して、最新の WIP の状態にする。
// チェック・コミットメッセージ・ブランチ名は localStorage (wip_diff_commit.js)、開いているディレクトリは sessionStorage (diff_fold.js) に
// 残っているので復元される。左ペインと本文のスクロール位置だけは、ここで保存して戻す。
(function () {
  const KEY = "git-graph-diff-scroll", side = document.getElementById("side"), main = document.getElementById("main");
  try {
    const k = JSON.parse(sessionStorage.getItem(KEY) || "null");
    sessionStorage.removeItem(KEY);
    if (k && k.url === location.href) {
      if (side) side.scrollTop = k.side || 0;
      if (main) main.scrollTop = k.main || 0;
    }
  } catch (e) { /* 復元できなくても動作には影響しない */ }
  const btn = document.getElementById("rld");
  if (!btn) return;
  btn.addEventListener("click", () => {
    btn.disabled = true;
    try {
      sessionStorage.setItem(KEY, JSON.stringify({url: location.href, side: side ? side.scrollTop : 0, main: main ? main.scrollTop : 0}));
    } catch (e) { /* 同上 */ }
    location.reload();
  });
})();
