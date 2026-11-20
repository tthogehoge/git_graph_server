/* ---- files/02_left_pane.js ---- */
// ---- 左ペインの表示: ファイル一覧 / コミットまたは HEAD との差分 ----
const SIDE_KEY = "gitGraphFilesSide";
let sideMode = "tree";  // "tree" | "changes"。保存先 (localStorage) が使えなくても切り替わるよう、メモリ上の値を正とする
try { sideMode = localStorage.getItem(SIDE_KEY) === "changes" ? "changes" : "tree"; } catch (e) { sideMode = "tree"; }
if (compareFrom && compareTo) sideMode = "changes";
// ファイル差分「全体」リンク (side=changes) では特定ファイルを選ばず変更一覧を開く
if (new URLSearchParams(location.search).get("side") === "changes") sideMode = "changes";
function activeList() { return sideMode === "changes" ? changesEl : list; }  // ↑↓ Enter の対象
function syncTreeSelection() {
  if (activeList() !== list) return;
  selectEntry([...list.querySelectorAll(".entry")].find(r => r.dataset.path === currentFile) || null);
}
function applySideMode() {
  const changes = sideMode === "changes";
  list.hidden = changes;
  changesEl.hidden = !changes;
  selBar.hidden = !wipSelectionEnabled();
  sideTreeBtn.classList.toggle("on", !changes);
  sideChangesBtn.classList.toggle("on", changes);
  selectEntry(null);
  updateSelBar();
  if (changes) syncChangesSelection();
  else syncTreeSelection();
}
function setSideMode(mode) {
  sideMode = mode;
  try { localStorage.setItem(SIDE_KEY, mode); } catch (e) { /* 保存できなくても切り替わる (次回は引き継がれない) */ }
  applySideMode();
}
sideTreeBtn.onclick = () => setSideMode("tree");
sideChangesBtn.onclick = () => setSideMode("changes");
const CHANGE_MARK = {A: "A", C: "A", R: "R", D: "D"};  // それ以外は M
function renderChanges() {  // コミット (または比較) の変更ファイル一覧
  if (isWip) return;
  sideChangesBtn.textContent = t("side_changes") + (commitChanges ? " (" + commitFiles.length + ")" : "");
  if (!commitChanges) { changesEl.innerHTML = '<p class="hint">' + esc(t("loading")) + '</p>'; return; }
  if (!commitFiles.length) { changesEl.innerHTML = '<p class="hint">' + esc(t("no_changed_files")) + '</p>'; return; }
  changesEl.innerHTML = commitFiles.map(f => {
    const mark = CHANGE_MARK[f.status] || "M", slash = f.path.lastIndexOf("/");
    const tip = t(COMMIT_MARK_KEY[mark] || "commit_mark_M") + (mark === "R" && f.old_path ? ": " + f.old_path : "");
    const num = f.added === null || f.deleted === null ? t("binary_short") : '<span class="add">+' + f.added + '</span> <span class="del">−' + f.deleted + '</span>';
    return '<div class="entry" data-type="change" data-path="' + esc(f.path) + '" title="' + esc(f.old_path ? f.old_path + " → " + f.path : f.path) + '"><span>' +
      '<span class="st ' + ST_CLASS[mark] + '" title="' + esc(tip) + '">' + mark + '</span>' +
      (slash < 0 ? "" : '<span class="dir">' + esc(f.path.slice(0, slash + 1)) + '</span>') + esc(f.path.slice(slash + 1)) + '</span><span class="size">' + num + '</span></div>';
  }).join("");
  changesEl.querySelectorAll(".entry").forEach(a => { a.onclick = () => { selectEntry(a); openChange(a.dataset.path); }; });
  syncChangesSelection();
}
function wipFilesChanged(files, status) {  // WIP パネルの一覧と状態が決まった (パネルを描き直すたびに呼ばれる)
  wipFiles = files;
  wipStatusFiles = status ? [
    ...(status.conflicts || []).map(f => Object.assign({wip: "conflict"}, f)),
    ...(status.staged || []).map(f => Object.assign({wip: "staged"}, f)),
    ...(status.unstaged || []).map(f => Object.assign({wip: "unstaged"}, f)),
    ...(status.untracked || []).map(f => Object.assign({wip: "untracked"}, f)),
  ] : files;
  if (!stageDirty) stageOverrides.clear();
  [...stageOverrides].forEach(([key, value]) => {
    if (!key.startsWith("tree\0")) return;
    const path = key.slice(5);
    const matches = wipStatusFiles.filter(f => (f.path === path || f.path.startsWith(path + "/")) && f.wip !== "conflict");
    if (!matches.length) return;
    matches.forEach(f => stageOverrides.set(stageKey(f.wip, f.path), value));
    stageOverrides.delete(key);
  });
  syncTreeCheckBoxes();
  updateDiffBtn();
  markWipCurrent();
  if (isWip && wipHeadFiles.length) renderWipChanges();
}
function wipStatusForPath(path) {
  const same = wipStatusFiles.filter(f => f.path === path);
  return ["conflict", "unstaged", "untracked", "staged", "amend"].map(k => same.find(f => f.wip === k)).find(Boolean);
}
function stageKey(kind, path) { return kind + "\0" + path; }
function desiredStageChecked(kind, path) {
  const key = stageKey(kind, path);
  if (stageOverrides.has(key)) return stageOverrides.get(key);
  let match = null;
  stageOverrides.forEach((value, treeKey) => {
    if (!treeKey.startsWith("tree\0")) return;
    const treePath = treeKey.slice(5);
    if ((path === treePath || path.startsWith(treePath + "/")) && (!match || treePath.length > match.path.length))
      match = {path: treePath, value: value};
  });
  return match ? match.value : kind === "staged";
}
function setStageChecked(kind, path, value) {
  if (kind === "tree") {
    const matches = wipStatusFiles.filter(f => (f.path === path || f.path.startsWith(path + "/")) && f.wip !== "conflict");
    if (matches.length) {
      matches.forEach(f => stageOverrides.set(stageKey(f.wip, f.path), value));
      stageDirty = true;
      return;
    }
  }
  stageOverrides.set(stageKey(kind, path), value);
  stageDirty = true;
}
function stageKindsForPath(path) {
  const kinds = [...new Set(wipStatusFiles.filter(f => f.path === path).map(f => f.wip))];
  if (kinds.length) return kinds;
  const e = entryInfo[path] || {}, xy = e.xy || "";
  if (xy === "??") return ["untracked"];
  if (xy.includes("U")) return ["conflict"];
  const result = [];
  if (xy[0] && xy[0] !== " ") result.push("staged");
  if (xy[1] && xy[1] !== " ") result.push("unstaged");
  return result;
}
function treeCheckState(path) {
  const treeKey = stageKey("tree", path);
  if (stageOverrides.has(treeKey)) return {checked: stageOverrides.get(treeKey), indeterminate: false};
  const statuses = wipStatusFiles.filter(f => f.path === path || f.path.startsWith(path + "/"));
  const values = statuses.length
    ? statuses.filter(f => f.wip !== "conflict").map(f => desiredStageChecked(f.wip, f.path))
    : stageKindsForPath(path).filter(k => k !== "conflict").map(k => desiredStageChecked(k, path));
  const checked = values.length > 0 && values.every(Boolean);
  return {checked: checked, indeterminate: values.some(Boolean) && !checked};
}
function syncTreeCheckBoxes() {
  list.querySelectorAll('input.ck[data-kind="tree"]').forEach(c => {
    const state = treeCheckState(c.dataset.path);
    c.checked = state.checked;
    c.indeterminate = state.indeterminate;
  });
}
function renderWipChanges() {
  sideChangesBtn.textContent = t("side_changes") + (wipHeadFiles.length ? " (" + wipHeadFiles.length + ")" : "");
  const from = wipCompareFrom();
  const ctx = from ? {from: from, wip: "compare"} : {};
  if (wipSelectionEnabled()) {
    const changes = new Map(wipHeadFiles.map(f => [f.path, f]));
    const labels = {conflict: "conflict", staged: "staged", unstaged: "unstaged", untracked: "untracked"};
    let body = "";
    ["conflict", "staged", "unstaged", "untracked"].forEach(kind => {
      const group = wipStatusFiles.filter(f => f.wip === kind && changes.has(f.path))
        .map(f => Object.assign({}, changes.get(f.path), f, {wip: kind}));
      if (!group.length) return;
      const check = f => '<td class="fk"><input type="checkbox" class="ck" data-kind="' + esc(f.wip) + '" data-path="' + esc(f.path) +
        '" aria-label="' + esc(f.path) + '"' + (desiredStageChecked(f.wip, f.path) ? " checked" : "") +
        (f.wip === "conflict" ? " disabled" : "") + "></td>";
      const all = kind === "conflict" ? "" : '<input type="checkbox" class="changeall" data-kind="' + kind +
        '" title="' + esc(t("select_all")) + '"> ';
      body += '<h3>' + all + esc(t(labels[kind])) + " (" + group.length + ")</h3>" +
        WipPanel.filesTable(ctx, group, 0, {t: t, check: check});
    });
    changesEl.innerHTML = body || '<p class="hint">' + esc(t("no_changed_files")) + "</p>";
  } else {
    const files = wipHeadFiles.map(f => {
      const status = wipStatusForPath(f.path);
      const st = status && status.st || f.st || (f.status === "A" || f.status === "C" ? "A" : f.status === "D" ? "D" : f.status === "R" ? "R" : "M");
      return Object.assign({}, f, {st: st});
    });
    changesEl.innerHTML = WipPanel.filesTable(ctx, files, 0, {t: t});
  }
  bindSelectionChecks(changesEl);
  updateSelBar();
  updateDiffBtn();
  markWipCurrent();
  // 再描画で要素が消えるので選択を付け直す（同一パスが複数グループにある場合も先頭の1つだけ）
  selectedEntry = null;
  if (sideMode === "changes") syncChangesSelection();
}
let wipChangesSeq = 0;
function wipCompareFrom() {
  return compareTo === "WIP" && compareFrom ? compareFrom : wipHead;
}
function wipSelectionEnabled() {
  if (!CAN_EDIT) return false;
  return compareTo !== "WIP" || !compareFrom || (!!wipHead && (compareFrom === wipHead ||
    (compareFrom.length >= 7 && wipHead.startsWith(compareFrom))));
}
function loadWipChanges() {
  if (!isWip) return Promise.resolve();
  const seq = ++wipChangesSeq, head = wipCompareFrom();
  if (!head) {
    updateCommitHeader({type: "wip", head: null});
    return api("/api/status", {}).then(s => {
      if (seq !== wipChangesSeq) return;
      wipHeadFiles = [];
      ["conflicts", "staged", "unstaged", "untracked"].forEach((key, index) => {
        const kinds = ["conflict", "staged", "unstaged", "untracked"];
        wipHeadFiles.push(...(s[key] || []).map(f => Object.assign({wip: kinds[index]}, f)));
      });
      renderWipChanges();
    }).catch(e => {
      if (seq === wipChangesSeq) changesEl.innerHTML = '<p class="hint">' + esc(t("error") + e.message) + "</p>";
    });
  }
  return api("/api/compare", {from: head, to: "WIP"}).then(r => {
    if (seq !== wipChangesSeq) return;
    if (compareTo === "WIP" && compareFrom) {
      updateCommitHeader({type: "compare", from: r.from, to: {hash: "WIP"}});
    } else {
      updateCommitHeader({type: "wip", head: r.from});
    }
    wipHeadFiles = r.files.map(f => Object.assign({wip: "compare"}, f));
    if (wipCompareChanges !== null) {
      wipCompareChanges = Object.create(null);
      wipCompareDirs = new Set();
      wipHeadFiles.forEach(f => {
        wipCompareChanges[f.path] = f;
        for (let i = f.path.indexOf("/"); i > 0; i = f.path.indexOf("/", i + 1)) {
          wipCompareDirs.add(f.path.slice(0, i));
        }
      });
      refreshBadges();
    }
    renderWipChanges();
  }).catch(e => {
    if (seq === wipChangesSeq) changesEl.innerHTML = '<p class="hint">' + esc(t("error") + e.message) + "</p>";
  });
}
function markWipCurrent() {  // WIP の変更一覧で、右ペインに差分を出しているファイルの行に印を付ける
  if (!isWip) return;
  changesEl.querySelectorAll("tr.frow").forEach(tr => {
    const a = tr.querySelector("a[href^='/files?']");
    const q = a ? new URL(a.href, location.href).searchParams : null;
    tr.classList.toggle("cur", !!(q && currentView === "diff" && q.get("file") === currentFile));
  });
}
function openWipChange(sel) {  // WIP の変更を、右ペインの差分エディタで開く
  const key = [sel.path, sel.wip || "", sel.from || "", sel.to || "", sel.old || ""].join("\0");
  if (currentFile === sel.path && currentView === "diff" && diffSel && diffSel.wip === sel.wip &&
      diffSel.from === sel.from && diffSel.to === sel.to && diffSel.old === sel.old) {
    showFilePane();
    return Promise.resolve();
  }
  if (pendingWipOpen && pendingWipOpen.key === key) {
    showFilePane();
    return pendingWipOpen.promise;
  }
  if (!leaveEdit()) return Promise.resolve();
  diffSel = sel;
  showFilePane();
  const dir = parentDir(sel.path);
  if (dir !== current) loadTree(dir, false);
  history.pushState({path: dir, file: sel.path, view: "diff"}, "", pageUrl(dir, sel.path, "diff"));
  const pending = {key: key, promise: null};
  pending.promise = loadFile(sel.path, false).then(() => renderView("diff")).finally(() => {
    if (pendingWipOpen === pending) pendingWipOpen = null;
  });
  pendingWipOpen = pending;
  return pending.promise;
}
let pendingWipOpen = null;
changesEl.addEventListener("click", ev => {
  if (!isWip || ev.button || ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.altKey) return;
  const row = ev.target.closest("tr.frow");
  if (!row) return;
  if (ev.target.closest("input")) return;
  const a = ev.target.closest("a[href^='/files?']") || row.querySelector("a[href^='/files?']");
  if (!a) return;
  const q = new URL(a.href, location.href).searchParams;
  const path = q.get("file");
  if (!path) return;
  ev.preventDefault();
  selectEntry(row);  // クリックした行を選択状態にする（open の途中で再描画されても付く）
  const from = q.get("dfrom"), to = q.get("dto");
  openWipChange(from && to
    ? {path: path, from: from, to: to, wip: "compare", old: q.get("dold") || ""}
    : {path: path, wip: q.get("dwip") || "unstaged", old: q.get("dold") || ""});
});
function syncChangesSelection() {  // いま開いているファイルが、変更ファイルの一覧にあれば選択する
  if (activeList() !== changesEl) return;
  const byEntry = [...changesEl.querySelectorAll(".entry")].find(r => r.dataset.path === currentFile);
  if (byEntry) { selectEntry(byEntry); return; }
  // WIP の tr.frow（同一パスが staged/unstaged など複数あるときは diffSel の wip を優先）
  const rows = [...changesEl.querySelectorAll("tr.frow")].filter(r => pathOfNavRow(r) === currentFile);
  let byFrow = rows[0] || null;
  if (rows.length > 1 && diffSel && diffSel.path === currentFile && diffSel.wip) {
    const match = rows.find(r => (r.dataset.kind || "") === diffSel.wip);
    if (match) byFrow = match;
  }
  selectEntry(byFrow);
}
function openChange(path) {  // 変更ファイルを、右ペインの差分エディタで開く (ファイルを開いて差分の表示にするまで、履歴は 1 つ)
  const f = commitChanges && commitChanges[path];
  if (!f) return Promise.resolve();
  if (path === currentFile && currentView === "diff") return Promise.resolve();  // 同じファイルなら leaveEdit しない (差分エディタを破棄しない)
  if (!leaveEdit()) return Promise.resolve();
  diffSel = compareFrom && compareTo ? {path: path, from: compareFrom, to: compareTo, old: f.old_path || ""} : null;
  showFilePane();
  const dir = parentDir(path);
  if (dir !== current) loadTree(dir, false);
  history.pushState({path: dir, file: path, view: "diff"}, "", pageUrl(dir, path, "diff"));
  return loadFile(path, false).then(() => renderView("diff"));  // 消されたファイルは、内容を読めなくても差分は出せる
}
fileDiffBtn.onclick = () => { if (currentFile && diffParams(currentFile)) openView("diff"); };  // このファイルの差分を右ペインに表示
fileExtDiffBtn.onclick = () => {
  const params = currentFile && diffParams(currentFile);
  if (!params) return;
  fileExtDiffBtn.disabled = true;
  fetch("/api/open-diff", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(params)})
    .then(r => r.ok ? r.json() : r.text().then(m => { throw new Error(m || r.status); }))
    .then(r => setFileMsg(r.message, true))
    .catch(e => setFileMsg(e.message, false))
    .finally(() => { fileExtDiffBtn.disabled = false; });
};
fileMergeBtn.onclick = () => {  // 競合ファイルをマージエディタで開く
  if (!currentFile || !isConflictFile(currentFile)) return;
  location.href = "/merge?" + new URLSearchParams({path: currentFile});
};
function logHash() { return isWip ? wipHead : hash; }  // ファイルのログの起点 (作業ツリーなら HEAD)
function esc(s) { return String(s).replace(/[&<>\"]/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", "\"":"&quot;"}[c])); }
function api(path, params) { return fetch(path + "?" + new URLSearchParams(params)).then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); })); }
// 時間のかかる調べものを、できた分から受け取る (サーバーが 1 行ずつ JSON を送る)。onEvent は 1 行ごとに呼ぶ。signal.abort() で受信をやめると、サーバーも調べるのをやめる
async function apiStream(path, params, onEvent, signal) {
  const res = await fetch(path + "?" + new URLSearchParams(Object.assign({stream: 1}, params)), {signal: signal});
  if (!res.ok) throw new Error((await res.text()) || res.status);
  let buf = "";
  const feed = (text, last) => {
    buf += text;
    const lines = buf.split("\n");
    buf = last ? "" : lines.pop();
    lines.forEach(line => {
      if (!line) return;
      const ev = JSON.parse(line);
      if (ev.error) throw new Error(ev.error);
      onEvent(ev);
    });
  };
  if (res.body && res.body.getReader) {
    const reader = res.body.getReader(), dec = new TextDecoder("utf-8");
    for (;;) {
      const {done, value} = await reader.read();
      if (done) break;
      feed(dec.decode(value, {stream: true}), false);
    }
    feed(dec.decode(), true);
  } else {
    feed(await res.text(), true);  // ストリームを読めない環境では、全部そろってからまとめて受け取る
  }
}
