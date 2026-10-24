// ログの行の左に描くマージグラフ (git log --graph 相当)。行を上から順に渡すと、各行の <td> を返す。
// レーン (縦の線) は「次に現れるはずのコミット」を持ち、コミットが来たらそのレーンへ合流させる。
// 行の高さは件名の折り返しなどで変わるので、縦方向は % 指定の <line> だけで描く。
// マージが多いと長い線で埋まるので、子から親まで LOG_GRAPH_SHORT 行を超える長い線は途中を描かない。
// 子の行から下へ短い線を描き、親 (分岐元) の行ではノードの右横に短い線を並べて、「どこかへ繋がっている」ことを示す。
// 短い線の先には丸い印を付けて、線の端を下 / 上のコミットから離す (そこに繋がっているように見せない)。
// 長いかどうかは親に着くまで分からないので、長いと分かった時点で、描き済みの行を描き直す (flush)。
// アクション対象 (actionHash) のコミットにつながる線 (親への線と、子から着く線) は、長くても全部、他の線の上に白い縁取り付きで重ねて描く
// (行ごとの <svg> ではなく、ログ全体にかぶせる 1 つの <svg> に描くので、長い線でも行を描き直さない)。
// 線の先 (親) がまだ読み込まれていないときは、読み込んだ範囲の端まで描く。
const LOG_GRAPH_COLORS = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e", "#17becf", "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22"];
const LOG_GRAPH_W = 12, LOG_GRAPH_X0 = 8, LOG_GRAPH_NODE_Y = 13;
const LOG_GRAPH_SHORT = 50;  // 子から親までこの行数以内の線は全部描く
const LOG_GRAPH_STUB = 8;  // 長い線の両端に描く短い線の長さ (px)
const LOG_GRAPH_SIDE = 5;  // 分岐元に着く短い線の、ノードの右横への長さ (px。隣の列の線 (+12px) に届かない長さ)
let logGraphCur = null;  // いま表示しているログのマージグラフ (アクション対象が変わったら sync() で描き直す)
window.addEventListener("resize", () => { if (logGraphCur) logGraphCur.sync(); });  // 行の高さが変わるので、選択中の線を描き直す
function logGraph() {
  let lanes = [], nextColor = 0, n = 0, sel = "", selRowsPrev = new Set(), pendingSel = false;
  const recs = [];  // 行番号 -> その行の記録 (描き直せるように持つ)
  const parkedBy = new Map();  // 長くなった線 (親のハッシュ -> 線たち)
  const edgeLanes = {}, inLanes = {}, rowOf = {};  // コミット -> 親への線 / そのコミットに着く線 / 行番号
  const dirty = new Set();  // 描き直す行 (長い線と分かって、短い線だけに描き直す行 / 選択中の線の目印を消す行)
  const selLanes = new Set();  // 選択中のコミットにつながる線 (全部描くので、行の短い目印は描かない)
  const newColor = () => LOG_GRAPH_COLORS[nextColor++ % LOG_GRAPH_COLORS.length];
  const free = arr => { const i = arr.indexOf(null); return i < 0 ? arr.length : i; };
  const x = i => LOG_GRAPH_X0 + i * LOG_GRAPH_W;
  const line = (x1, y1, x2, y2, color, cls) =>
    '<line' + (cls ? ' class="' + cls + '"' : '') + ' x1="' + x1 + '" y1="' + y1 + '" x2="' + x2 + '" y2="' + y2 + '" stroke="' + color + '"/>';
  const cap = (cx, cy, color) => '<circle cx="' + cx + '" cy="' + cy + '" r="1.8" fill="' + color + '"/>';
  const want = () => (typeof actionHash === "string" && actionHash) || "";
  function cell(r) {
    const rec = recs[r], col = rec.col, cx = x(col), parts = [];
    let maxIdx = col, minRight = 0;
    rec.through.forEach(l => {
      if (l.long) return;  // 長い線は途中を描かない
      parts.push(line(x(l.idx), 0, x(l.idx), "100%", l.color));
      maxIdx = Math.max(maxIdx, l.idx);
    });
    rec.mine.forEach(l => { parts.push(line(x(l.idx), 0, cx, LOG_GRAPH_NODE_Y, l.color)); maxIdx = Math.max(maxIdx, l.idx); });
    const ticks = rec.arrived.filter(l => !selLanes.has(l));  // 選択中の線は全部描くので、目印は要らない
    ticks.forEach((l, k) => {  // 長い線の終わり (分岐元に着く線): 縦の線と重ならないよう、ノードの右横へ短い線を櫛の歯のように並べる
      const m = ticks.length, step = Math.min(4, 14 / Math.max(1, m - 1));
      const y = LOG_GRAPH_NODE_Y + (k - (m - 1) / 2) * step, ex = cx + 4 + LOG_GRAPH_SIDE;
      parts.push(line(cx + 4, y, ex, y, l.color), cap(ex, y, l.color));
      minRight = Math.max(minRight, ex + 2);
    });
    rec.made.forEach(m => {  // 親への線
      const l = m.lane;
      if (l.long && selLanes.has(l)) return;  // 選択中: 全部描く線なので、目印は描かない
      if (l.long) {  // 長い線は、短い線だけ描く
        const ex = cx + Math.sign(x(m.to) - cx) * 4, ey = LOG_GRAPH_NODE_Y + LOG_GRAPH_STUB;
        parts.push(line(cx, LOG_GRAPH_NODE_Y, ex, ey, l.color), cap(ex, ey, l.color));
      } else {
        parts.push(line(cx, LOG_GRAPH_NODE_Y, x(m.to), "100%", l.color));
        maxIdx = Math.max(maxIdx, m.to);
      }
    });
    parts.push('<circle cx="' + cx + '" cy="' + LOG_GRAPH_NODE_Y + '" r="' + (rec.merge ? 4 : 3.5) + '" fill="' +
               (rec.merge ? "#fff" : rec.color) + '" stroke="' + rec.color + '"/>');
    const width = Math.max(x(maxIdx) + LOG_GRAPH_X0, minRight);
    return '<td class="graph" style="width:' + width + 'px;min-width:' + width + 'px">' +
      '<svg width="' + width + '" aria-hidden="true">' + parts.join("") + "</svg></td>";
  }

  function row(c) {
    if (sel !== want()) sel = want();
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
    rowOf[h] = r;
    if (h === sel) pendingSel = true;  // 選択中のコミットの行ができた: 線が決まるので、次の sync で目印を消す
    recs.push({h: h, col: col, color: color, merge: parents.length > 1, mine: mine, arrived: arrived,
               made: made, through: before.filter(l => l && l.h !== h)});
    edgeLanes[h] = made.map(m => m.lane);
    inLanes[h] = mine.concat(arrived);
    lanes = after;
    return cell(r);
  }

  // 描き直す行を DOM に反映する (行を挿入したあとに呼ぶ)。まだ DOM に無い行は次の機会に回す
  row.flush = () => {
    if (!dirty.size) return;
    const tbody = listView.querySelector("#rows");
    if (!tbody) return;
    dirty.forEach(r => {
      const tr = tbody.rows[r];  // 行は順に並んでいるので、番号で取れる
      if (!tr) return;
      dirty.delete(r);
      const td = tr.querySelector("td.graph");
      if (td && tr.dataset.h === recs[r].h) td.outerHTML = cell(r);
    });
  };

  // アクション対象につながる線: 親への線と、子から着く線。それぞれ { 出る行, 出る先の列, 線 }
  function selEdges() {
    const r = rowOf[sel];
    if (!sel || r === undefined) return [];
    const res = recs[r].made.map(m => ({from: r, to: m.to, lane: m.lane}));
    (inLanes[sel] || []).forEach(l => {  // 子から着く線は、その線を引き始めた (子の) 行から
      const m = recs[l.start].made.find(e => e.lane === l);
      if (m) res.push({from: l.start, to: m.to, lane: l});
    });
    return res;
  }
  // ログ全体にかぶせる 1 つの <svg> に、選択中の線を全部描く (他の線の上に、白い縁取り付きで)
  function applySel() {  // 選択中の線が変わったら、その線の両端の行の短い目印を消す / 戻す (行の描き直し)
    const edges = selEdges(), now = new Set(edges.map(e => e.lane));
    const rows = new Set();
    edges.forEach(e => { if (e.lane.long) { rows.add(e.from); if (e.lane.endRow >= 0) rows.add(e.lane.endRow); } });
    selLanes.forEach(l => { if (l.long) { rows.add(l.start); if (l.endRow >= 0) rows.add(l.endRow); } });
    selRowsPrev.forEach(r => rows.add(r));
    selRowsPrev = new Set();
    edges.forEach(e => { if (e.lane.long) { selRowsPrev.add(e.from); if (e.lane.endRow >= 0) selRowsPrev.add(e.lane.endRow); } });
    selLanes.clear();
    now.forEach(l => selLanes.add(l));
    rows.forEach(r => dirty.add(r));
  }
  function drawOverlay() {
    let svg = listView.querySelector(":scope > svg.log-sel-overlay");
    const tbody = listView.querySelector("#rows"), edges = tbody && listView.offsetParent !== null ? selEdges() : [];
    if (!edges.length) { if (svg) svg.remove(); return; }
    if (!svg) {
      svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("class", "log-sel-overlay");
      listView.appendChild(svg);
    }
    const lv = listView.getBoundingClientRect(), ox = listView.scrollLeft - lv.left, oy = listView.scrollTop - lv.top;
    const info = r => {  // 行のグラフ列の位置 (ログ全体の中の座標)
      const tr = tbody.rows[r], el = tr && tr.querySelector("td.graph svg");
      if (!el) return null;
      const b = el.getBoundingClientRect();
      return {left: b.left + ox, top: b.top + oy, bottom: b.bottom + oy};
    };
    const last = info(tbody.rows.length - 1);
    const paths = [];
    edges.forEach(e => {
      const a = info(e.from);
      if (!a) return;
      const pts = [[a.left + x(recs[e.from].col), a.top + LOG_GRAPH_NODE_Y], [a.left + x(e.to), a.bottom]];
      const end = e.lane.endRow >= 0 ? info(e.lane.endRow) : null;
      if (end) pts.push([a.left + x(e.lane.idx), end.top], [end.left + x(recs[e.lane.endRow].col), end.top + LOG_GRAPH_NODE_Y]);
      else if (last) pts.push([a.left + x(e.lane.idx), last.bottom]);  // 親が未読み込み: 読み込んだ範囲の端まで
      const d = pts.map(p => p[0].toFixed(1) + "," + p[1].toFixed(1)).join(" ");
      paths.push('<polyline points="' + d + '" fill="none" stroke="#fff" stroke-width="6" stroke-linejoin="round" stroke-linecap="round"/>' +
                 '<polyline points="' + d + '" fill="none" stroke="' + e.lane.color + '" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>');
    });
    svg.setAttribute("width", listView.scrollWidth);
    svg.setAttribute("height", listView.scrollHeight);
    svg.innerHTML = paths.join("");
  }
  row.sync = () => {
    if (sel !== want()) { sel = want(); pendingSel = true; }
    if (pendingSel || selLanes.size) { pendingSel = false; applySel(); }  // 行が増えて、選択中の線の先 (親) が決まったときも
    row.flush(); drawOverlay();
  };  // アクション対象が変わった / 行が増えた / 画面が戻った
  return row;
}

// git log を CHUNK 件ずつ取得して、末尾が見えたら続きを読み込む (全件読むまで続く)
// src を渡すと /api/log の代わりに src.path (src.params を付けて) から同じ形式の一覧を取る (stash 一覧 / reflog)
function logKey(titleHtml, rev, topHtml, src, targetHash) {
  return JSON.stringify([titleHtml, rev, topHtml, src || null, targetHash || ""]);
}
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
  rows.dataset.key = logKey(titleHtml, rev, topHtml, src, targetHash);  // 詳細から戻るとき、同じログならそのまま見せる
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

