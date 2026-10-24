// ログの行の左に描くマージグラフ (git log --graph 相当)。行を上から順に渡すと、各行の <td> を返す。
// レーン (縦の線) は「次に現れるはずのコミット」を持ち、コミットが来たらそのレーンへ合流させる。
// 行の高さは件名の折り返しなどで変わるので、縦方向は % 指定の <line> だけで描く。
// マージが多いと長い線で埋まるので、子から親まで LOG_GRAPH_SHORT 行を超える長い線は途中を描かない。
// 子の行から下へ、親 (分岐元) の行へ上から、それぞれ短い線を描いて「どこかへ繋がっている」ことを示す。
// 短い線の先には丸い印を付けて、線の端を下 / 上のコミットから離す (そこに繋がっているように見せない)。
// 長いかどうかは親に着くまで分からないので、長いと分かった時点で、描き済みの行を描き直す (flush)。
// アクション対象 (actionHash) のコミットにつながる線 (親への線と、子から着く線) は、長くても全部描く。
// 線の先 (親) がまだ読み込まれていないときは、読み込んだ範囲の端まで描く。
const LOG_GRAPH_COLORS = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e", "#17becf", "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22"];
const LOG_GRAPH_W = 12, LOG_GRAPH_X0 = 8, LOG_GRAPH_NODE_Y = 13;
const LOG_GRAPH_SHORT = 2;  // 子から親までこの行数以内の線は全部描く
const LOG_GRAPH_STUB = 8;  // 長い線の両端に描く短い線の長さ (px)
let logGraphCur = null;  // いま表示しているログのマージグラフ (アクション対象が変わったら sync() で描き直す)
function logGraph() {
  let lanes = [], nextColor = 0, n = 0, sel = "";
  const recs = [];  // 行番号 -> その行の記録 (描き直せるように持つ)
  const parkedBy = new Map();  // 長くなった線 (親のハッシュ -> 線たち)
  const edgeLanes = {}, inLanes = {};  // コミット -> 親への線 / そのコミットに着く線
  const selLanes = new Set();  // 長くても全部描く線
  const dirty = new Set();  // 描き直す行
  const newColor = () => LOG_GRAPH_COLORS[nextColor++ % LOG_GRAPH_COLORS.length];
  const free = arr => { const i = arr.indexOf(null); return i < 0 ? arr.length : i; };
  const x = i => LOG_GRAPH_X0 + i * LOG_GRAPH_W;
  const line = (x1, y1, x2, y2, color, cls) =>
    '<line' + (cls ? ' class="' + cls + '"' : '') + ' x1="' + x1 + '" y1="' + y1 + '" x2="' + x2 + '" y2="' + y2 + '" stroke="' + color + '"/>';
  const cap = (cx, cy, color) => '<circle cx="' + cx + '" cy="' + cy + '" r="1.6" fill="' + color + '"/>';
  const want = () => (typeof actionHash === "string" && actionHash) || "";
  const touch = l => {  // 長い線の描き方が変わるので、その線が通る行を描き直す
    if (!l.long) return;
    for (let k = l.start; k <= (l.endRow >= 0 ? l.endRow : n - 1); k++) dirty.add(k);
  };

  function selectLanes(hash) {
    selLanes.clear();
    (edgeLanes[hash] || []).concat(inLanes[hash] || []).forEach(l => selLanes.add(l));
  }
  function setSel(hash) {  // アクション対象が変わった
    const old = [...selLanes];
    sel = hash;
    selectLanes(hash);
    old.forEach(touch);
    selLanes.forEach(touch);
  }

  function cell(r) {
    const rec = recs[r], col = rec.col, cx = x(col), parts = [], under = [];
    let maxIdx = col;
    rec.through.forEach(l => {
      if (l.long && !selLanes.has(l)) return;  // 長い線は途中を描かない
      parts.push(line(x(l.idx), 0, x(l.idx), "100%", l.color));
      maxIdx = Math.max(maxIdx, l.idx);
    });
    selLanes.forEach(l => {  // 選択中のコミットにつながる長い線: 描かずに通り過ぎる行にも描く
      if (l.long && r > l.start + LOG_GRAPH_SHORT && (l.endRow < 0 || r < l.endRow)) {
        under.push(line(x(l.idx), 0, x(l.idx), "100%", l.color, "sel-line"));
        maxIdx = Math.max(maxIdx, l.idx);
      }
    });
    rec.mine.forEach(l => { parts.push(line(x(l.idx), 0, cx, LOG_GRAPH_NODE_Y, l.color)); maxIdx = Math.max(maxIdx, l.idx); });
    rec.arrived.forEach((l, k) => {  // 長い線の終わり (分岐元に着く線)
      if (selLanes.has(l)) {
        under.push(line(x(l.idx), 0, cx, LOG_GRAPH_NODE_Y, l.color, "sel-line"));
        maxIdx = Math.max(maxIdx, l.idx);
      } else {  // 上の行から離して、短い線を扇状に並べる (線が何本着くか分かる)
        const sx = cx + (k - (rec.arrived.length - 1) / 2) * 5;
        parts.push(line(sx, 4, cx, LOG_GRAPH_NODE_Y, l.color), cap(sx, 4, l.color));
      }
    });
    rec.made.forEach(m => {  // 親への線
      const l = m.lane;
      if (l.long && !selLanes.has(l)) {  // 長い線は、短い線だけ描く
        const ex = cx + Math.sign(x(m.to) - cx) * 4, ey = LOG_GRAPH_NODE_Y + LOG_GRAPH_STUB;
        parts.push(line(cx, LOG_GRAPH_NODE_Y, ex, ey, l.color), cap(ex, ey, l.color));
      } else {
        parts.push(line(cx, LOG_GRAPH_NODE_Y, x(m.to), "100%", l.color));
        maxIdx = Math.max(maxIdx, m.to);
      }
    });
    parts.push('<circle cx="' + cx + '" cy="' + LOG_GRAPH_NODE_Y + '" r="' + (rec.merge ? 4 : 3.5) + '" fill="' +
               (rec.merge ? "#fff" : rec.color) + '" stroke="' + rec.color + '"/>');
    const width = x(maxIdx) + LOG_GRAPH_X0;
    return '<td class="graph" style="width:' + width + 'px;min-width:' + width + 'px">' +
      '<svg width="' + width + '" aria-hidden="true">' + under.join("") + parts.join("") + "</svg></td>";
  }

  function row(c) {
    if (sel !== want()) setSel(want());
    const h = c[0], parents = Array.isArray(c[5]) ? c[5] : [], r = n++;
    // 長くなった線 (子から LOG_GRAPH_SHORT 行を超えても親に着かない線) は、列を空けて宙に浮かせる
    lanes.forEach((l, i) => {
      if (!l || r - l.start <= LOG_GRAPH_SHORT) return;
      l.long = true;
      lanes[i] = null;
      if (!parkedBy.has(l.h)) parkedBy.set(l.h, []);
      parkedBy.get(l.h).push(l);
      for (let k = l.start; k < r; k++) dirty.add(k);  // 描き済みの行は、短い線だけに描き直す
    });
    while (lanes.length && !lanes[lanes.length - 1]) lanes.pop();
    const before = lanes;
    const arrived = parkedBy.get(h) || [];  // 宙に浮いていた線のうち、このコミットに着くもの
    parkedBy.delete(h);
    const mineIdx = [];
    before.forEach((l, i) => { if (l && l.h === h) mineIdx.push(i); });
    const col = mineIdx.length ? mineIdx[0] : free(before);
    const color = mineIdx.length ? before[col].color : (arrived.length ? arrived[0].color : newColor());
    const after = before.slice();
    mineIdx.forEach(i => { after[i] = null; });
    while (after.length <= col) after.push(null);
    const newLane = (ph, c2, idx) => ({h: ph, color: c2, idx: idx, start: r, long: false, endRow: -1});
    const made = [];
    if (parents.length) { after[col] = newLane(parents[0], color, col); made.push({lane: after[col], to: col}); }
    parents.slice(1).forEach(p => {
      let j = after.findIndex(l => l && l.h === p);
      if (j < 0) { j = free(after); after[j] = newLane(p, newColor(), j); }
      made.push({lane: after[j], to: j});
    });
    while (after.length && !after[after.length - 1]) after.pop();
    const mine = mineIdx.map(i => before[i]);
    mine.forEach(l => { l.endRow = r; });
    arrived.forEach(l => { l.endRow = r; });
    recs.push({h: h, col: col, color: color, merge: parents.length > 1, mine: mine, arrived: arrived,
               made: made, through: before.filter(l => l && l.h !== h)});
    edgeLanes[h] = made.map(m => m.lane);
    inLanes[h] = mine.concat(arrived);
    lanes = after;
    if (h === sel) { selectLanes(h); selLanes.forEach(touch); }  // 選択中のコミットが読み込まれた
    return cell(r);
  }

  // 描き直す行を DOM に反映する (行を挿入したあとに呼ぶ)。まだ DOM に無い行は次の機会に回す
  row.flush = () => {
    if (!dirty.size) return;
    const trs = new Map();
    listView.querySelectorAll("tr[data-h]").forEach(tr => { if (tr.querySelector("td.graph")) trs.set(tr.dataset.h, tr); });
    dirty.forEach(r => {
      const tr = trs.get(recs[r].h);
      if (!tr) return;
      tr.querySelector("td.graph").outerHTML = cell(r);
      dirty.delete(r);
    });
  };
  row.sync = () => { if (sel !== want()) setSel(want()); row.flush(); };
  return row;
}

// git log を CHUNK 件ずつ取得して、末尾が見えたら続きを読み込む (全件読むまで続く)
// src を渡すと /api/log の代わりに src.path (src.params を付けて) から同じ形式の一覧を取る (stash 一覧 / reflog)
function showLog(titleHtml, rev, topHtml, fromHistory, restoreScroll, src, targetHash) {
  document.body.classList.remove("compare-logs-mode");
  if (!fromHistory) pushScreen({screen: "log", titleHtml: titleHtml, rev: rev, topHtml: topHtml, src: src || null, targetHash: targetHash || ""});
  setDocumentTitleFromHtml(titleHtml);
  const my = ++token;
  if (observer) { observer.disconnect(); observer = null; }
  curRow = null;
  listView.innerHTML = "<h2>" + titleHtml + "</h2>" + topHtml +
    '<div class="meta" id="count">' + esc(t("loading")) + '</div>' +
    '<table class="log"><tbody id="rows"></tbody></table><div id="sentinel" class="hint"></div>';
  showView("list");
  listView.dataset.src = src ? src.path : "";
  const rows = listView.querySelector("#rows"), count = listView.querySelector("#count"),
        sentinel = listView.querySelector("#sentinel");
  let skip = 0, done = false, loading = false, total = null;
  // /api/log の一覧 (コミット履歴) だけマージグラフを付ける (stash / reflog / ブランチ一覧などは付けない)
  const graphRow = (!src || src.path === "/api/log") ? logGraph() : null;
  logGraphCur = graphRow;

  async function more() {
    if (loading || done || my !== token) return;
    loading = true;
    try {
      const graphParam = graphRow ? {graph: 1} : {};
      const r = src ? await api(src.path, Object.assign({}, src.params, graphParam, {skip: skip, limit: CHUNK}))
                    : await api("/api/log", Object.assign({rev: rev, skip: skip, limit: CHUNK}, graphParam));
      if (my !== token) return;
      rows.insertAdjacentHTML("beforeend", r.commits.map(c => rowHtml(c, "", graphRow ? graphRow(c) : "")).join(""));
      updateCmpClasses();
      skip += r.commits.length;
      if (fromHistory && restoreScroll) {
        listView.scrollTop = Math.max(0, Math.min(restoreScroll, listView.scrollHeight - listView.clientHeight));
      }
      done = !r.has_more;
      if (r.total !== undefined) total = r.total;
      count.textContent = total === null ? t("n_items", {n: skip})
        : (done ? t("n_changes", {n: total}) : t("n_loaded", {skip: skip, total: total}));
      sentinel.textContent = done ? (skip === 0 ? t("none") : "") : t("loading");
    } catch (e) {
      sentinel.textContent = t("error") + e.message; done = true;
    } finally {
      loading = false;
    }
  }

  if (targetHash) {
    (async () => {
      while (!done && my === token) {
        await more();
        if (my !== token) return;
        const target = listView.querySelector('tr[data-h="' + targetHash + '"]');
        if (target) {
          target.classList.add("cur");
          target.scrollIntoView({block: "center"});
          break;
        }
      }
    })();
  } else if ("IntersectionObserver" in window) {
    const recheck = () => {
      if (!done && observer && my === token) { observer.unobserve(sentinel); observer.observe(sentinel); }
    };
    observer = new IntersectionObserver(es => { if (es[0].isIntersecting) more().then(recheck); },
                                        {root: listView, rootMargin: "600px"});
    observer.observe(sentinel);
  } else {
    (async () => { while (!done && my === token) await more(); })();
  }

}

async function showContainingBranchLog(hash, selectedRef, replaceHistory = false) {
  try {
    const result = await api("/api/branches-containing", {hash: hash});
    if (!result.branches.length) {
      setGraphStatus(t("branches_containing_none", {hash: hash.slice(0, 7)}), "error");
      return;
    }
    const branches = result.branches;
    const preferred = "refs/heads/" + currentBranch;
    const branch = branches.find(b => b.ref === selectedRef) ||
      branches.find(b => b.ref === preferred) || branches[0];
    const options = branches.map(b => '<option value="' + esc(b.ref) + '"' +
      (b.ref === branch.ref ? " selected" : "") + ">" + esc(b.name) + "</option>").join("");
    const top = '<button class="back-btn">' + t("back_history") + '</button><div class="meta"><label>' + esc(t("target_label")) +
      ': <select id="containingBranchSelect" data-h="' + esc(hash) + '">' + options + "</select></label></div>";
    showLog(esc(t("branch_log_title", {name: branch.name})), branch.tip, top, replaceHistory, 0, null, hash);
  } catch (e) {
    setGraphStatus(t("error") + e.message, "error");
  }
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

