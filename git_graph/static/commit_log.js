// コミットのログの行・ブランチ / タグの表示・コミットのドラッグ&ドロップ (CommitLog)。グラフのログ画面とファイル表示のログで共通。
// 行 (tr.clickable[data-h]) の見た目は commit_log.css (グラフでは app.css の目印の位置に差し込む)。
// 前提: ページに t() (翻訳) がある。コピー記号 (.copy) のクリックは、ページ側 (グラフは js/01_core.js、ファイル表示は copy.js) が受け持つ。
const CommitLog = (function () {
  const COLORS = {branch: "#b7e4a7", current: "#b7e4a7", remote: "#a7c8f0", tag: "#ffe9a0", head: "#f4a7a7", stash: "#d9c7f2"};  // ブランチ / タグの色 (graph.py の COLORS と同じ)
  function esc(s) { return String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c])); }

  function copyLink(h) {  // フルハッシュをコピーするリンク
    return '<a class="copy" data-copy="' + h + '" title="' + esc(t("copy_hash_title")) + '">📋</a>';
  }
  // refs: [{name, kind}] (kind: branch / current / remote / tag / head / stash)。currentBranch: いま開いているブランチ名 (★ を付ける)
  function chipsHtml(refs, currentBranch) {
    return (refs || []).map(r => {
      const current = r.kind === "current" || (r.kind === "branch" && r.name === currentBranch);
      return '<span class="chip" style="background:' + (COLORS[r.kind] || "#ddd") + '"' +
        (current ? ' title="' + esc(t("current_branch")) + '"' : "") + ">" + (current ? "★ " : "") + esc(r.name) + "</span>";
    }).join("");
  }
  // ログの 1 行。c = [hash, short, author, date, subject, parents?]
  // opts: extraClass (行のクラス) / graphTd (マージグラフの列) / chips (ブランチ・タグの HTML) / graphLink (ハッシュをグラフ画面へのリンクにする)
  function rowHtml(c, opts) {
    opts = opts || {};
    const hash = opts.graphLink
      ? '<a class="commit-graph" href="/?commit_hash=' + encodeURIComponent(c[0]) + '">' + esc(c[1]) + "</a>"
      : esc(c[1]);
    return '<tr class="clickable' + (opts.extraClass ? " " + opts.extraClass : "") + '" data-h="' + c[0] + '">' + (opts.graphTd || "") +
      '<td class="h">' + hash + copyLink(c[0]) + '</td><td class="log-cell">' +
      '<div class="log-main"><div class="log-subj" title="' + esc(c[4]) + '"><span class="rc">' + (opts.chips || "") + "</span>" + esc(c[4]) + "</div>" +
      '<div class="meta">' + esc(c[2]) + " / " + esc(c[3]) + "</div></div></td></tr>";
  }

  function tableHtml(rowsHtml) {  // ログの表。幅が広いときに件名と作者 / 日付を 1 行に並べるため、container の要素で包む (グラフでは #listView が container)
    return '<div class="log-host"><table class="log"><tbody>' + rowsHtml + "</tbody></table></div>";
  }

  // コミット (ログの行・グラフのノード) のドラッグ&ドロップ。ドラッグ元が A、ドロップ先が B。
  // マウス / ペンの左ボタンで、ほとんど動かさずに離したときは、これまでどおりのクリックとして扱う。画面ごとの違いは opts で渡す:
  //   hitAt(x, y)            その位置のコミット {el, hash, ...} (無ければ null)。el は drop-target の強調を付ける要素
  //   tip(fromHash, hit)     ドラッグに追従して出す案内の文字列 (hit: いま重なっているコミット。無ければ null)
  //   onStart()              ドラッグが始まった (開いているメニューを閉じるなど)
  //   onDrop(fromHash, hit, ev)  自分自身以外のコミットの上で離した
  // 戻り値の start(el, hash, ev) を、ドラッグ元の pointerdown から呼ぶ。
  const DRAG_THRESHOLD = 6;  // px
  function createDrag(opts) {
    let drag = null, clickUntil = 0;
    function start(el, hash, ev) {
      if (ev.button !== 0 || ev.pointerType === "touch" || !hash) return;  // マウス / ペンの左ボタンだけ
      drag = {el: el, hash: hash, x: ev.clientX, y: ev.clientY, active: false, over: null, tip: null};
    }
    function end(ev, cancel) {
      const d = drag;
      drag = null;
      if (!d || !d.active) return;
      document.body.classList.remove("node-dragging");
      d.el.classList.remove("drag-src");
      if (d.over) d.over.classList.remove("drop-target");
      if (d.tip) d.tip.remove();
      clickUntil = Date.now() + 400;  // ドラッグの終わりに出る click は無かったことにする (背景のクリックで選択が外れないように)
      if (cancel) return;
      const hit = opts.hitAt(ev.clientX, ev.clientY);
      if (!hit || hit.hash === d.hash) return;  // 何もないところや自分自身に落としたら、何もしない
      opts.onDrop(d.hash, hit, ev);
    }
    document.addEventListener("pointermove", ev => {
      const d = drag;
      if (!d) return;
      if (!d.active) {
        if (Math.hypot(ev.clientX - d.x, ev.clientY - d.y) < DRAG_THRESHOLD) return;
        d.active = true;
        if (opts.onStart) opts.onStart();
        if (window.getSelection) window.getSelection().removeAllRanges();
        document.body.classList.add("node-dragging");
        d.el.classList.add("drag-src");
        d.tip = document.createElement("div");
        d.tip.id = "dragTip";
        document.body.appendChild(d.tip);
      }
      const hit = opts.hitAt(ev.clientX, ev.clientY), over = hit && hit.hash !== d.hash ? hit.el : null;
      if (over !== d.over) {
        if (d.over) d.over.classList.remove("drop-target");
        if (over) over.classList.add("drop-target");
        d.over = over;
      }
      d.tip.textContent = opts.tip(d.hash, over ? hit : null);
      d.tip.style.left = (ev.clientX + 14) + "px";
      d.tip.style.top = (ev.clientY + 14) + "px";
    });
    document.addEventListener("pointerup", ev => end(ev, false));
    document.addEventListener("pointercancel", ev => end(ev, true));
    document.addEventListener("keydown", ev => {  // ドラッグ中の Escape はドラッグだけを取り消す
      if (ev.key === "Escape" && drag && drag.active) { ev.stopPropagation(); end(ev, true); }
    }, true);
    document.addEventListener("click", ev => {
      if (Date.now() < clickUntil) { ev.stopPropagation(); ev.preventDefault(); }
    }, true);
    return {start: start};
  }

  return {COLORS, copyLink, chipsHtml, rowHtml, tableHtml, createDrag};
})();
