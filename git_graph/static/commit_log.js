// コミットのログの行・ブランチ / タグの表示・続きの読み込み (無限スクロール)・検索・コミットのドラッグ&ドロップ (CommitLog)。グラフのログ画面とファイル表示のログで共通。
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

  const CHUNK = 500;  // 1 回に読むログの件数

  // ---- ログの続きの読み込み (無限スクロール) ----
  // 末尾 (sentinel) が見えたら続きを CHUNK 件ずつ読む。IntersectionObserver が無いときは、全件を続けて読む。opts:
  //   load(skip) → Promise<{commits, has_more, total?}>   skip 件目からの 1 ページ
  //   onPage(r, at)          1 ページ読めた (行を足す・件数を更新する)。この時点で skip / done / total は更新済み。at: そのページの先頭の位置
  //   onError(e)             読み込みに失敗した (読み込みはそこで終わる)
  //   sentinel / root        末尾の要素 / スクロールする要素
  //   alive()                false になったら、その応答は捨てる (別の表示に移った)
  //   skip                   読み始める位置 (省略は先頭)
  function createPager(opts) {
    let skip = opts.skip || 0, done = false, loading = false, total = null, stopped = false, observer = null;
    const alive = () => !stopped && (!opts.alive || opts.alive());
    async function more() {
      if (loading || done || !alive()) return;
      loading = true;
      try {
        const r = await opts.load(skip);
        if (!alive()) return;
        const at = skip;
        skip += r.commits.length;
        done = !r.has_more;
        if (r.total !== undefined) total = r.total;
        opts.onPage(r, at);
      } catch (e) {
        if (!alive()) return;
        done = true;
        opts.onError(e);
      } finally {
        loading = false;
      }
    }
    function watch() {  // 戻り値の disconnect() で、外から止められる
      if ("IntersectionObserver" in window) {
        const recheck = () => {  // まだ末尾が見えていれば、続けて読む
          if (!done && observer && alive()) { observer.unobserve(opts.sentinel); observer.observe(opts.sentinel); }
        };
        observer = new IntersectionObserver(es => { if (es[0].isIntersecting) more().then(recheck); },
                                            {root: opts.root, rootMargin: "600px"});
        observer.observe(opts.sentinel);
        return observer;
      }
      (async () => { while (!done && alive()) await more(); })();
      return {disconnect() { stopped = true; }};
    }
    function stop() {  // 別の表示へ移る: 読み込み中の応答は捨て、見張りもやめる
      stopped = true;
      if (observer) { observer.disconnect(); observer = null; }
    }
    return {more: more, watch: watch, stop: stop,
            get skip() { return skip; }, set skip(v) { skip = v; },
            get done() { return done; },
            get total() { return total; }, set total(v) { total = v; }};
  }
  // 件数の表示 (読み込み中 / 全件 / 検索のヒット数)
  function countText(search, done, skip, total) {
    const key = search ? "search_hits" : "n_changes";
    return total === null
      ? (done ? t(key, {n: skip}) : t("n_loaded_unknown_total", {n: skip}))
      : (done ? t(key, {n: total}) : t("n_loaded", {skip: skip, total: total}));
  }

  // ---- ログの検索 (ハッシュ前方一致 / 件名・本文 / 作者 / コミッター / 期間 / 範囲)。見出しの検索ボタンを押したときだけ入力欄を出す ----
  const SEARCH_FIELDS = ["hash", "grep", "author", "committer", "since", "until"];
  function normSearch(o) {  // 入力が全部空なら null。キーの順は固定 (画面を戻すとき、同じログかどうかを比べるため)
    const s = {};
    SEARCH_FIELDS.forEach(k => { s[k] = String((o && o[k]) || "").trim(); });
    if (!SEARCH_FIELDS.some(k => s[k])) return null;
    s.scope = o.scope === "all" ? "all" : "rev";
    return s;
  }
  function searchParams(search) {  // /api/log に付ける検索の条件 (空の欄は付けない)
    const p = {};
    if (!search) return p;
    SEARCH_FIELDS.forEach(k => { if (search[k]) p[k] = search[k]; });
    if (search.scope === "all") p.scope = "all";
    return p;
  }
  function searchHtml(search) {  // 検索の入力欄 (search があれば開いた状態)。見出しには .log-search-btn のボタンを置く
    const s = search || {}, v = k => esc(s[k] || "");
    const field = (k, label, ph) => "<label>" + esc(t(label)) + '<input type="text" data-k="' + k + '" value="' + v(k) + '"' +
      (ph ? ' placeholder="' + esc(t(ph)) + '"' : "") + ' autocomplete="off" spellcheck="false"></label>';
    return '<div class="log-search" id="logSearch"' + (search ? "" : " hidden") + ">" +
      field("hash", "search_hash") + field("grep", "search_text", "search_text_ph") +
      field("author", "search_author") + field("committer", "search_committer") +
      "<label>" + esc(t("search_period")) + '<span class="log-search-period"><input type="date" data-k="since" value="' + v("since") + '"> ~ ' +
      '<input type="date" data-k="until" value="' + v("until") + '"></span></label>' +
      "<label>" + esc(t("search_scope")) + '<select data-k="scope"><option value="rev">' + esc(t("search_scope_rev")) + "</option>" +
      '<option value="all"' + (s.scope === "all" ? " selected" : "") + ">" + esc(t("search_scope_all")) + "</option></select></label>" +
      '<div class="log-search-btns"><button class="sbtn" id="logSearchGo">' + esc(t("search_go")) + "</button>" +
      '<button class="sbtn" id="logSearchClear">' + esc(t("clear")) + "</button></div></div>";
  }
  function readSearch(root) {  // root の中の入力欄の値 (空なら null)
    const box = root.querySelector("#logSearch"), o = {};
    if (!box) return null;
    box.querySelectorAll("[data-k]").forEach(el => { o[el.dataset.k] = el.value; });
    o.hash = (o.hash || "").toLowerCase();
    return normSearch(o);
  }
  // 検索ボタン・検索 / クリアのボタン・入力欄での Enter を、root で受ける。
  //   h.apply(search)  検索する (null は、検索をやめる)   h.toggled(box)  入力欄を開閉した (レイアウトが変わる)
  function bindSearch(root, h) {
    root.addEventListener("click", ev => {
      const btn = ev.target.closest(".log-search-btn");
      if (btn) {
        const box = root.querySelector("#logSearch");
        if (!box) return;
        if (btn.classList.contains("on")) { h.apply(null); return; }  // 検索中に押したら、検索をやめて閉じる
        box.hidden = !box.hidden;
        if (h.toggled) h.toggled(box);
        if (!box.hidden) box.querySelector("input").focus();
      } else if (ev.target.closest("#logSearchGo")) {
        h.apply(readSearch(root));
      } else if (ev.target.closest("#logSearchClear")) {
        h.apply(null);
      }
    });
    root.addEventListener("keydown", ev => {
      if (ev.key === "Enter" && ev.target.closest && ev.target.closest("#logSearch") && ev.target.tagName !== "BUTTON") {
        ev.preventDefault();
        h.apply(readSearch(root));
      }
    });
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

  return {COLORS, CHUNK, copyLink, chipsHtml, rowHtml, tableHtml, createPager, countText,
          normSearch, searchParams, searchHtml, readSearch, bindSearch, createDrag};
})();
