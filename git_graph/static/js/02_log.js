// マージグラフの本体 (logGraph) は、ファイル表示のログと共通 (log_graph.js)。ここでは、ログを表示するときに作って行に付ける。
// git log を CHUNK 件ずつ取得して、末尾が見えたら続きを読み込む (全件読むまで続く)
// src を渡すと /api/log の代わりに src.path (src.params を付けて) から同じ形式の一覧を取る (stash 一覧 / reflog)
function logKey(titleHtml, rev, topHtml, src, targetHash, search) {
  return JSON.stringify([titleHtml, rev, topHtml, src || null, targetHash || "", search || null]);
}

// ---- ログの検索 (ハッシュ前方一致 / 件名・本文 / 作者 / コミッター / 期間 / 範囲)。見出しの検索ボタンを押したときだけ入力欄を出す ----
// 条件の正規化・API への渡し方・入力欄・ボタンの動きは、ファイル表示のログと共通 (commit_log.js)
let logCtx = null;  // いま表示しているログの引数 (検索し直すときに showLog へ渡す)
function normSearch(o) { return CommitLog.normSearch(o); }
function searchParams(search) { return CommitLog.searchParams(search); }
function logSearchHtml(search) { return CommitLog.searchHtml(search); }
function readLogSearch() { return CommitLog.readSearch(listView); }
function applyLogSearch(search) {  // 検索し直す: 履歴は増やさず、同じログを検索条件つきで読み直す
  const c = logCtx;
  if (!c) return;
  showLog(c.titleHtml, c.rev, c.topHtml, true, 0, c.src, c.targetHash, search);
  history.replaceState(Object.assign({}, history.state, {search: search}), "");
  if (search) return;
  const btn = listView.querySelector(".log-search-btn");
  if (btn) btn.focus();
}
CommitLog.bindSearch(listView, {
  apply: applyLogSearch,
  toggled: () => { if (logGraphCur) logGraphCur.sync(); },  // 行が下へずれる / 戻るので、選択中の線 (ログ全体にかぶせた svg) を描き直す
});

const TARGET_BEFORE = 100;  // 対象のコミットへ飛ぶとき、対象より前 (上) のこの件数から読み始める
function showLog(titleHtml, rev, topHtml, fromHistory, restoreScroll, src, targetHash, search, win) {
  search = normSearch(search);
  // 検索できるのはコミット履歴のログだけ (stash / reflog / ブランチ・タグ一覧や、特定のコミットを探すログは対象外)
  const searchable = (!src || src.path === "/api/log") && !targetHash;
  if (!searchable) search = null;
  document.body.classList.remove("compare-logs-mode");
  if (!fromHistory) pushScreen({screen: "log", titleHtml: titleHtml, rev: rev, topHtml: topHtml, src: src || null, targetHash: targetHash || "", search: search});
  setDocumentTitleFromHtml(titleHtml);
  const my = ++token;
  if (observer) { observer.disconnect(); observer = null; }
  curRow = null;
  logCtx = searchable ? {titleHtml: titleHtml, rev: rev, topHtml: topHtml, src: src || null, targetHash: targetHash || ""} : null;
  listView.innerHTML = "<h2>" + titleHtml +
    (searchable ? ' <button class="sbtn iconbtn log-search-btn' + (search ? " on" : "") + '" title="' + esc(t("search_open")) + '">🔍</button>' : "") + "</h2>" +
    (searchable ? logSearchHtml(search) : "") + topHtml +
    '<div class="meta" id="count">' + esc(t("loading")) + '</div><div id="earlier" class="hint"></div>' +
    '<table class="log"><tbody id="rows"></tbody></table><div id="sentinel" class="hint"></div>';
  showView("list");
  listView.dataset.src = src ? src.path : "";
  const rows = listView.querySelector("#rows"), count = listView.querySelector("#count"),
        sentinel = listView.querySelector("#sentinel"), earlier = listView.querySelector("#earlier");
  rows.dataset.key = logKey(titleHtml, rev, topHtml, src, targetHash, search);  // 詳細から戻るとき、同じログならそのまま見せる
  let winStart = 0;  // 読み始めた位置 (対象へ飛ぶときは先頭ではない)
  // /api/log の一覧 (コミット履歴) だけマージグラフを付ける (stash / reflog / ブランチ一覧などは付けない)。検索結果は途中のコミットが抜けるので付けない
  const graphRow = ((!src || src.path === "/api/log") && !search)
    ? logGraph({host: listView, rows: () => listView.querySelector("#rows"), target: () => actionHash}) : null;
  logGraphCur = graphRow;
  const searchP = searchParams(search);
  let totalPollScheduled = false;
  if (search) {
    const box = listView.querySelector("#logSearch");
    if (box && !restoreScroll) box.querySelector("input").focus({preventScroll: true});
  }

  // 続きの読み込み (CHUNK 件ずつ。末尾が見えたら読む) はファイル表示のログと共通 (commit_log.js)。ここで違うのは、行の描画 (マージグラフ / stash / サブモジュール) と、件数の表示
  const pg = CommitLog.createPager({
    root: listView, sentinel: sentinel,
    alive: () => my === token,
    load: skip => {
      const graphParam = graphRow ? {graph: 1} : {};
      return src ? api(src.path, Object.assign({}, src.params, graphParam, searchP, {skip: skip, limit: CHUNK}))
                 : api("/api/log", Object.assign({rev: rev, skip: skip, limit: CHUNK}, graphParam, searchP));
    },
    onPage: r => {
      rows.insertAdjacentHTML("beforeend", r.commits.map((c, i) => (src && src.path === "/api/submodules")
        ? submoduleRowHtml(c, r.submodules[i]) : rowHtml(c, "", graphRow ? graphRow(c) : "")).join(""));
      updateCmpClasses();
      if (fromHistory && restoreScroll) {
        listView.scrollTop = Math.max(0, Math.min(restoreScroll, listView.scrollHeight - listView.clientHeight));
      }
      count.textContent = CommitLog.countText(search, pg.done, pg.skip, pg.total);
      sentinel.textContent = pg.done ? (pg.skip === 0 ? t("none") : "") : t("loading");
      if (pg.total === null && graphRow && !src && !search && !totalPollScheduled) {
        totalPollScheduled = true;
        pollTotal();
      }
    },
    onError: e => { sentinel.textContent = t("error") + e.message; },
  });
  const more = () => pg.more();

  async function pollTotal() {
    if (my !== token || pg.total !== null) return;
    try {
      const result = await api("/api/log-total", {rev: rev});
      if (my !== token) return;
      if (result.total !== null) {
        pg.total = result.total;
        count.textContent = pg.done ? t("n_changes", {n: pg.total}) : t("n_loaded", {skip: pg.skip, total: pg.total});
      } else if (result.pending) {
        setTimeout(pollTotal, 1000);
      }
    } catch (e) {
      // The visible page remains usable if the optional total-count request fails.
    }
  }

  // 読み始めた位置が先頭でないとき、これより前を読み込むボタンを出す (押すと、少し前から読み直す)
  function showEarlier() {
    earlier.innerHTML = "";
    if (winStart <= 0) return;
    const btn = document.createElement("button");
    btn.className = "sbtn";
    btn.textContent = t("earlier_commits", {n: winStart});
    btn.onclick = () => {
      const first = rows.firstElementChild;
      showLog(titleHtml, rev, topHtml, true, 0, src, targetHash, search,
              {start: Math.max(0, winStart - CHUNK / 2), focus: first ? first.dataset.h : ""});
    };
    earlier.appendChild(btn);
  }
  function watch() { observer = pg.watch(); }  // 続きを読み込む: 末尾が見えたら CHUNK 件ずつ (別の画面へ移るときは、observer.disconnect() で止める)

  if (targetHash) {
    (async () => {
      let missing = false;
      // 対象まで先頭から読むと、件数が多いときに時間がかかる。対象の位置を先に調べて、その少し前から読む
      try {
        const pos = await api("/api/log-position", {rev: rev, hash: targetHash});
        if (my !== token) return;
        if (pos.index >= 0) {
          pg.skip = winStart = win && win.start !== undefined ? win.start : Math.max(0, pos.index - TARGET_BEFORE);
          pg.total = pos.total;
        } else {
          missing = true;  // このログに無い: 探して全部読むことはせず、先頭だけを見せる
        }
      } catch (e) {
        if (my !== token) return;  // 位置が分からなければ、先頭から読む
      }
      showEarlier();
      if (missing) { await more(); if (my === token && !pg.done) watch(); return; }
      const find = h => h ? listView.querySelector('tr[data-h="' + h + '"]') : null;
      const goal = (win && win.focus) || targetHash;
      while (!pg.done && !missing && my === token) {
        await more();
        if (my !== token) return;
        if (find(goal)) break;
      }
      const target = find(targetHash), focus = find(goal);
      if (target) target.classList.add("cur");
      if (focus) focus.scrollIntoView({block: goal === targetHash ? "center" : "start"});
      if (!pg.done) watch();
    })();
  } else {
    watch();
  }

}

function showTargetLog(hash, replaceHistory = false) {
  // 対象のコミットを、全ブランチ・タグを含む全コミットのログの中で見せる (ブランチを調べる必要が無いので、すぐ始められる)。
  // 連打されたときに履歴を増やさないよう、すでに同じ対象のログを開いているなら履歴は足さない
  const st = history.state;
  const same = !!st && st.screen === "log" && st.rev === "all" && st.targetHash === hash;
  const top = '<button class="back-btn">' + t("back_history") + '</button><div class="meta">' + esc(t("target_label")) +
    ": " + esc(hash.slice(0, 7)) + "</div>";
  showLog(esc(t("all_log_title")), "all", top, replaceHistory || same, 0, null, hash);
}

// table-layout: fixed は先頭行で列幅を決める。先頭が colspan=2 の空行 (log-gap) だと左右で列幅が食い違うので、<col> で固定する
const COMPARE_COLS = '<colgroup><col class="c-hash"><col></colgroup>';
function showCompareLogs(a, b, fromHistory, restoreScroll) {
  document.body.classList.add("compare-logs-mode");
  if (!fromHistory) pushScreen({screen: "compare-logs", a: a, b: b});
  setDocumentTitle(t("compare_logs_heading", {from: a.slice(0, 7), to: b.slice(0, 7)}));
  const my = ++token;
  if (observer) { observer.disconnect(); observer = null; }
  curRow = null;
  listView.innerHTML = '<h2>' + esc(t("compare_logs_heading", {from: a.slice(0, 7), to: b.slice(0, 7)})) + '</h2>' +
    '<div class="compare-logs"><section class="compare-log-col"><h3>A: ' + esc(a.slice(0, 7)) + '</h3>' +
    '<table class="log">' + COMPARE_COLS + '<tbody id="compareRowsA"></tbody></table></section>' +
    '<section class="compare-log-col"><h3>B: ' + esc(b.slice(0, 7)) + '</h3>' +
    '<table class="log">' + COMPARE_COLS + '<tbody id="compareRowsB"></tbody></table></section></div>' +
    '<div id="compareLogSentinel" class="hint">' + esc(t("loading")) + '</div>';
  showView("list");
  listView.dataset.src = "";
  const rowsA = listView.querySelector("#compareRowsA"), rowsB = listView.querySelector("#compareRowsB"),
        sentinel = listView.querySelector("#compareLogSentinel");
  const gapRow = '<tr class="log-gap"><td colspan="2"></td></tr>';
  let skipA = 0, skipB = 0, doneA = false, doneB = false;
  let initial = true, loading = false, base = "";

  function hasCommit(commits, hash) {
    return commits.some(c => c[0] === hash);
  }

  function alignedRows(commitsA, commitsB) {
    const ia = base ? commitsA.findIndex(c => c[0] === base) : -1;
    const ib = base ? commitsB.findIndex(c => c[0] === base) : -1;
    if (ia < 0 || ib < 0) {
      return [commitsA.map(c => rowHtml(c)).join(""), commitsB.map(c => rowHtml(c)).join("")];
    }
    const target = Math.max(ia, ib);
    const side = (commits, index) => {
      const html = [];
      for (let i = index; i < target; i++) html.push(gapRow);
      html.push(...commits.slice(0, index).map(c => rowHtml(c)));
      html.push(...commits.slice(index).map((c, i) => rowHtml(c, i === 0 ? "log-common log-common-start" : "log-common")));
      return html.join("");
    };
    return [side(commitsA, ia), side(commitsB, ib)];
  }

  function syncCompareRows() {
    const listA = Array.from(rowsA.querySelectorAll("tr"));
    const listB = Array.from(rowsB.querySelectorAll("tr"));
    const commonA = listA.findIndex(row => row.classList.contains("log-common"));
    const commonB = listB.findIndex(row => row.classList.contains("log-common"));
    if (commonA < 0 || commonA !== commonB) return;
    for (let i = 0; i <= commonA; i++) {
      listA[i].style.height = "";
      listB[i].style.height = "";
      const height = Math.max(listA[i].getBoundingClientRect().height, listB[i].getBoundingClientRect().height);
      listA[i].style.height = height + "px";
      listB[i].style.height = height + "px";
    }
  }

  async function more() {
    if (loading || (doneA && doneB) || my !== token) return;
    loading = true;
    try {
      if (initial) {
        const [mb, firstA, firstB] = await Promise.all([
          api("/api/merge-base", {from: a, to: b}),
          api("/api/log", {rev: a, skip: skipA, limit: CHUNK}),
          api("/api/log", {rev: b, skip: skipB, limit: CHUNK})
        ]);
        if (my !== token) return;
        base = mb.hash || "";
        const commitsA = firstA.commits.slice(), commitsB = firstB.commits.slice();
        skipA += firstA.commits.length; skipB += firstB.commits.length;
        doneA = !firstA.has_more; doneB = !firstB.has_more;
        if (base) {
          while ((!hasCommit(commitsA, base) && !doneA) || (!hasCommit(commitsB, base) && !doneB)) {
            const needA = !hasCommit(commitsA, base) && !doneA;
            const needB = !hasCommit(commitsB, base) && !doneB;
            const [ra, rb] = await Promise.all([
              needA ? api("/api/log", {rev: a, skip: skipA, limit: CHUNK}) : Promise.resolve({commits: [], has_more: false}),
              needB ? api("/api/log", {rev: b, skip: skipB, limit: CHUNK}) : Promise.resolve({commits: [], has_more: false})
            ]);
            if (my !== token) return;
            commitsA.push(...ra.commits); commitsB.push(...rb.commits);
            skipA += ra.commits.length; skipB += rb.commits.length;
            if (needA) doneA = !ra.has_more;
            if (needB) doneB = !rb.has_more;
          }
        }
        const aligned = alignedRows(commitsA, commitsB);
        rowsA.innerHTML = aligned[0]; rowsB.innerHTML = aligned[1];
        initial = false;
        requestAnimationFrame(syncCompareRows);
      } else {
        const [ra, rb] = await Promise.all([
          doneA ? Promise.resolve({commits: [], has_more: false}) : api("/api/log", {rev: a, skip: skipA, limit: CHUNK}),
          doneB ? Promise.resolve({commits: [], has_more: false}) : api("/api/log", {rev: b, skip: skipB, limit: CHUNK})
        ]);
        if (my !== token) return;
        rowsA.insertAdjacentHTML("beforeend", ra.commits.map(c => rowHtml(c)).join(""));
        rowsB.insertAdjacentHTML("beforeend", rb.commits.map(c => rowHtml(c)).join(""));
        skipA += ra.commits.length; skipB += rb.commits.length;
        doneA = doneA || !ra.has_more; doneB = doneB || !rb.has_more;
      }
      updateCmpClasses();
      const done = doneA && doneB;
      sentinel.textContent = done ? "" : t("loading");
      if (fromHistory && restoreScroll) {
        listView.scrollTop = Math.max(0, Math.min(restoreScroll, listView.scrollHeight - listView.clientHeight));
      }
    } catch (e) {
      sentinel.textContent = t("error") + e.message;
      doneA = true; doneB = true;
    } finally {
      loading = false;
    }
  }
  if ("IntersectionObserver" in window) {
    const recheck = () => {
      if (!(doneA && doneB) && observer && my === token) { observer.unobserve(sentinel); observer.observe(sentinel); }
    };
    observer = new IntersectionObserver(es => { if (es[0].isIntersecting) more().then(recheck); },
                                        {root: listView, rootMargin: "600px"});
    observer.observe(sentinel);
  } else {
    (async () => { while (!(doneA && doneB) && my === token) await more(); })();
  }
}
