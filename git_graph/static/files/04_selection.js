/* ---- files/04_selection.js ---- */
// ---- 左ペインでチェックしたファイル・ディレクトリのステージ状態 (作業ツリーの表示だけ) ----
selBar.hidden = !wipSelectionEnabled();
function setSelMsg(text, ok) { selMsg.textContent = text; selMsg.className = "msg" + (ok ? "" : " err"); }
function stagePlan() {
  const stage = new Set(), unstage = new Set();
  wipStatusFiles.forEach(f => {
    if (f.wip === "conflict") return;
    const checked = desiredStageChecked(f.wip, f.path);
    if (f.wip === "staged" && !checked) unstage.add(f.path);
    else if ((f.wip === "unstaged" || f.wip === "untracked") && checked) stage.add(f.path);
  });
  stageOverrides.forEach((checked, key) => {
    const split = key.indexOf("\0"), kind = key.slice(0, split), path = key.slice(split + 1);
    if (kind !== "tree") return;
    const matches = wipStatusFiles.filter(f => f.path === path || f.path.startsWith(path + "/"));
    matches.forEach(f => { stage.delete(f.path); unstage.delete(f.path); });
    if (checked && matches.some(f => f.wip === "unstaged" || f.wip === "untracked")) stage.add(path);
    else if (!checked && matches.some(f => f.wip === "staged")) unstage.add(path);
  });
  stage.forEach(path => unstage.delete(path));
  return {stage_paths: [...stage].sort(), unstage_paths: [...unstage].sort()};
}
function checkedStagePaths() {
  return [...new Set(wipStatusFiles.filter(f => f.wip !== "conflict" && desiredStageChecked(f.wip, f.path)).map(f => f.path))];
}
function setStageFromBox(c, checked) {
  if (c.dataset.kind === "tree") {
    const path = c.dataset.path, matches = wipStatusFiles.filter(f => f.path === path || f.path.startsWith(path + "/"));
    if (matches.length) matches.forEach(f => { if (f.wip !== "conflict") setStageChecked(f.wip, f.path, checked); });
    else setStageChecked("tree", path, checked);
  } else setStageChecked(c.dataset.kind, c.dataset.path, checked);
  syncTreeCheckBoxes();
}
function bindSelectionChecks(root) {
  root.querySelectorAll("input.ck").forEach(c => {
    c.onclick = ev => ev.stopPropagation();
    c.onchange = () => { setStageFromBox(c, c.checked); updateSelBar(); };
  });
  root.querySelectorAll("input.changeall").forEach(all => {
    all.onchange = () => {
      root.querySelectorAll("input.ck").forEach(c => {
        if (c.dataset.kind !== all.dataset.kind || c.disabled) return;
        c.checked = all.checked;
        setStageFromBox(c, all.checked);
      });
      updateSelBar();
    };
  });
}
function syncChangeGroupChecks() {
  changesEl.querySelectorAll("input.changeall").forEach(all => {
    const boxes = [...changesEl.querySelectorAll("input.ck")].filter(c => c.dataset.kind === all.dataset.kind && !c.disabled);
    const checked = boxes.filter(c => c.checked).length;
    all.disabled = boxes.length === 0;
    all.checked = boxes.length > 0 && checked === boxes.length;
    all.indeterminate = checked > 0 && checked < boxes.length;
  });
}
function activeSelectionBoxes() {
  return [...(sideMode === "changes" ? changesEl : list).querySelectorAll("input.ck:not(:disabled)")];
}
function updateSelBar() {
  const plan = stagePlan(), n = new Set(plan.stage_paths.concat(plan.unstage_paths)).size, boxes = activeSelectionBoxes();
  const rmCount = checkedStagePaths().length;
  syncChangeGroupChecks();
  selCount.textContent = n ? t("sel_count", {n: n}) : "";
  selAddBtn.disabled = !n;
  selRmBtn.disabled = selRmCachedBtn.disabled = !rmCount;
  selAll.disabled = !boxes.length;
  selAll.checked = boxes.length > 0 && boxes.every(b => b.checked);
  selAll.indeterminate = !selAll.checked && boxes.some(b => b.checked);
}
selAll.onchange = () => {  // いま表示している一覧の項目をすべてチェック / 外す
  const on = selAll.checked;
  activeSelectionBoxes().forEach(c => { c.checked = on; setStageFromBox(c, on); });
  updateSelBar();
};
function isUntrackedEntry(p) {  // 未追跡のファイル / 中がすべて未追跡のディレクトリ (git rm できない)
  const e = entryInfo[p] || {};
  return e.xy === "??" || (e.type === "tree" && e.st === "?") || wipStatusFiles.some(f => f.path === p && f.wip === "untracked");
}
function runSel(url, body, onDone) {
  if (!wipSelectionEnabled()) return;
  const wasEditing = editing;
  if (!leaveEdit()) return;
  selAddBtn.disabled = selRmBtn.disabled = selRmCachedBtn.disabled = true;
  setSelMsg(t("wip_execute"), true);
  fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)})
    .then(r => r.ok ? r.json() : r.text().then(m => { throw new Error(m || r.status); }))
    .then(r => { stageOverrides.clear(); stageDirty = false; setSelMsg(r.message, true); return onDone(r, wasEditing); })
    .catch(e => setSelMsg(e.message, false))
    .finally(updateSelBar);
}
function reloadAfterSel(dir, fileGone, wasEditing) {  // 一覧を読み直し、変更の印・差分ボタン・開いているファイルを合わせる
  entryInfo = {};
  if (fileGone) clearFile();
  return loadTree(dir, dir !== current).then(() => {
    if (!currentFile) showDir();
    else if (wasEditing) return loadFile(currentFile, false);  // 編集欄を閉じたので内容を出し直す
    else updateDiffBtn();
  }).then(refreshWipCommitPanel);
}
function refreshWipCommitPanel() {
  if (!wipCommitPanel) return;
  return wipCommitPanel.render(document.getElementById("wipCommitPanel"), {});
}
selAddBtn.onclick = () => {
  const plan = stagePlan();
  if (plan.stage_paths.length || plan.unstage_paths.length)
    runSel("/api/stage", plan, (r, wasEditing) => reloadAfterSel(current, false, wasEditing));
};
function underAny(path, dirs) { return dirs.some(d => path === d || path.startsWith(d + "/")); }
function doSelRm(cached) {  // チェックしたものを git rm / git rm --cached。変更があるものは -f で実行する (確認文で変更が失われることを伝える)
  const paths = checkedStagePaths().sort(), n = paths.length;
  if (!n) return;
  const bad = paths.filter(isUntrackedEntry);
  if (bad.length) { setSelMsg(t("rm_untracked", {files: bad.slice(0, 3).join(", ")}), false); return; }
  const changed = paths.some(p => { const e = entryInfo[p] || {}; return !!(e.xy || e.st) || wipFiles.some(f => f.path === p); });
  if (!confirm(t(cached ? "confirm_rm_cached_selected" : changed ? "confirm_rm_selected_changed" : "confirm_rm_selected", {n: n}))) return;
  runSel("/api/rm", {paths: paths, cached: cached, force: changed, recursive: true}, (r, wasEditing) => {
    let dir = current;  // 表示中のディレクトリごと削除したときは、残っている親に移る
    while (!cached && dir && underAny(dir, paths)) dir = dir.includes("/") ? dir.slice(0, dir.lastIndexOf("/")) : "";
    return reloadAfterSel(dir, !cached && !!currentFile && underAny(currentFile, paths), wasEditing);
  });
}
selRmBtn.onclick = () => doSelRm(false);
selRmCachedBtn.onclick = () => doSelRm(true);
function doRm(cached) {  // git rm / git rm --cached。変更のあるファイルは -f で実行する (確認文で変更が失われることを伝える)
  if (!currentFile || !CAN_EDIT) return;
  const path = currentFile, changed = !!(entryInfo[path] || {}).xy;
  const text = cached ? t("confirm_rm_cached_file", {path: path})
    : changed ? t("confirm_rm_file_changed", {path: path}) : t("confirm_rm_file", {path: path});
  if (!confirm(text)) return;
  fileRmBtn.disabled = fileRmCachedBtn.disabled = true;
  fetch("/api/rm", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({paths: [path], cached: cached, force: changed})})
    .then(r => r.ok ? r.json() : r.text().then(m => { throw new Error(m || r.status); }))
    .then(r => {
      entryInfo = {};  // 一覧を読み直す (ステージされた削除や、未追跡になったことを反映する)
      return loadTree(current, false).then(() => {
        if (cached) { updateDiffBtn(); setFileMsg(r.message, true); }
        else { clearFile(); showDir(); setFileMsg(r.message, true); }
      }).then(refreshWipCommitPanel);
    })
    .catch(e => setFileMsg(e.message, false))
    .finally(() => { fileRmBtn.disabled = fileRmCachedBtn.disabled = false; });
}
fileRmBtn.onclick = () => doRm(false);
fileRmCachedBtn.onclick = () => doRm(true);
