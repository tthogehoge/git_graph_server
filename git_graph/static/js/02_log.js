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

  async function more() {
    if (loading || done || my !== token) return;
    loading = true;
    try {
      const r = src ? await api(src.path, Object.assign({}, src.params, {skip: skip, limit: CHUNK}))
                    : await api("/api/log", {rev: rev, skip: skip, limit: CHUNK});
      if (my !== token) return;
      rows.insertAdjacentHTML("beforeend", r.commits.map(rowHtml).join(""));
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

function showCompareLogs(a, b, fromHistory, restoreScroll) {
  document.body.classList.add("compare-logs-mode");
  if (!fromHistory) pushScreen({screen: "compare-logs", a: a, b: b});
  setDocumentTitle(t("compare_logs_heading", {from: a.slice(0, 7), to: b.slice(0, 7)}));
  const my = ++token;
  if (observer) { observer.disconnect(); observer = null; }
  curRow = null;
  listView.innerHTML = '<h2>' + esc(t("compare_logs_heading", {from: a.slice(0, 7), to: b.slice(0, 7)})) + '</h2>' +
    '<div class="compare-logs"><section class="compare-log-col"><h3>A: ' + esc(a.slice(0, 7)) + '</h3>' +
    '<table class="log"><tbody id="compareRowsA"></tbody></table></section>' +
    '<section class="compare-log-col"><h3>B: ' + esc(b.slice(0, 7)) + '</h3>' +
    '<table class="log"><tbody id="compareRowsB"></tbody></table></section></div>' +
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
      return [commitsA.map(rowHtml).join(""), commitsB.map(rowHtml).join("")];
    }
    const target = Math.max(ia, ib);
    const side = (commits, index) => {
      const html = [];
      for (let i = index; i < target; i++) html.push(gapRow);
      html.push(...commits.slice(0, index).map(rowHtml));
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
        rowsA.insertAdjacentHTML("beforeend", ra.commits.map(rowHtml).join(""));
        rowsB.insertAdjacentHTML("beforeend", rb.commits.map(rowHtml).join(""));
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

