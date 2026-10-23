// 差分は別ページ (/diff) で開く。ctx は {hash} (1コミット) または {from, to} (2コミット間の比較)
function diffUrl(ctx, f) {
  const p = Object.assign({}, ctx, {path: f.path});
  if (f.old_path) p.old = f.old_path;
  if (f.wip) p.wip = f.wip;  // 作業ツリーの変更 (staged / unstaged / untracked / conflict)
  return "/diff?" + new URLSearchParams(p);
}

const ST_CLASS = {M: "stM", T: "stM", A: "stA", D: "stD", R: "stR", C: "stR", U: "stU", "?": "stN"};
function badge(f) {  // 変更の種類 (M/A/D/R...) のバッジ。WIP の一覧で使う
  return f.st ? '<span class="st ' + (ST_CLASS[f.st] || "stM") + '">' + esc(f.st) + "</span>" : "";
}
const WIP_DIR_COLLAPSE = 10;  // 1つのディレクトリの変更がこの件数以上なら、既定で畳む
const WIP_OPEN_KEY = "git-graph-wip-open-dirs";  // /diff ページと共有する
let wipOpenDirs = new Set();  // 開いているディレクトリ ("種別|パス")
try { wipOpenDirs = new Set(JSON.parse(sessionStorage.getItem(WIP_OPEN_KEY) || "[]")); } catch (e) {}
function saveWipOpenDirs() { try { sessionStorage.setItem(WIP_OPEN_KEY, JSON.stringify([...wipOpenDirs])); } catch (e) {} }
function dirOf(path) { const i = path.lastIndexOf("/"); return i < 0 ? "" : path.slice(0, i); }
function filesHtml(ctx, files, offset, withCheck, collapse) {
  offset = offset || 0;
  if (!files.length) return '<p class="hint">' + esc(t("no_changed_files")) + '</p>';
  let add = 0, del = 0, max = 0;
  files.forEach(f => { add += f.added || 0; del += f.deleted || 0; max = Math.max(max, (f.added || 0) + (f.deleted || 0)); });
  const scale = max > 40 ? 40 / max : 1;
  const dirCount = {}, dirStat = {}, dirSeen = {};
  if (collapse) files.forEach(f => {
    const dir = dirOf(f.path);
    dirCount[dir] = (dirCount[dir] || 0) + 1;
    dirStat[dir] = dirStat[dir] || [0, 0];
    dirStat[dir][0] += f.added || 0; dirStat[dir][1] += f.deleted || 0;
  });
  const rows = files.map((f, k) => {
    const a = f.added || 0, d = f.deleted || 0;
    const dir = dirOf(f.path), folded = collapse && dirCount[dir] >= WIP_DIR_COLLAPSE;
    const open = folded && wipOpenDirs.has(f.wip + "|" + dir);
    let head = "";
    if (folded && !dirSeen[dir]) {
      dirSeen[dir] = true;
      head = '<tr class="drow" data-dir="' + esc(dir) + '" data-kind="' + esc(f.wip) + '" title="' + esc(t("dir_toggle")) + '">' +
        (withCheck ? '<td class="fk"><input type="checkbox" class="wipdir" data-kind="' + esc(f.wip) + '" data-dir="' + esc(dir) + '"></td>' : "") +
        '<td class="fp" colspan="3"><span class="dtog">' + (open ? "▼" : "▶") + '</span> ' + esc(dir ? dir + "/" : t("dir_root")) +
        ' <span class="hint">(' + esc(t("n_items", {n: dirCount[dir]})) + ') <span class="plus">+' + dirStat[dir][0] +
        '</span> <span class="minus">-' + dirStat[dir][1] + '</span></span></td></tr>';
    }
    const pa = a ? Math.max(1, Math.round(a * scale)) : 0, pd = d ? Math.max(1, Math.round(d * scale)) : 0;
    const name = f.old_path ? f.old_path + " => " + f.path : f.path;
    return head + '<tr class="frow' + (folded && !open ? " dhide" : "") + '" data-k="' + (offset + k) + '" data-dir="' + esc(dir) + '">' + (withCheck ? wipCheckCell(f) : "") + '<td class="fp">' + badge(f) + (f.tag ? '<span class="hint">[' + esc(f.tag) + '] </span>' : "") + '<a href="' + esc(diffUrl(ctx, f)) +
      '">' + esc(name) + '</a></td><td class="fc">' +
      (f.label ? esc(t(f.label)) : f.added === null ? "Bin" : a + d) + '</td><td class="fb"><span class="plus">' + "+".repeat(pa) +
      '</span><span class="minus">' + "-".repeat(pd) + "</span></td></tr>";
  }).join("");
  return '<div class="meta">' + files.length + (files.some(f => f.added !== undefined)
      ? " files changed, " + add + " insertions(+), " + del + " deletions(-)" : " files") + "</div>" +
    '<table class="files">' + rows + "</table>";
}

let curDetailHash = null;

function localBranches(h) {
  const n = DATA.nodes[h];
  return n ? n.refs.filter(r => r.kind === "branch" || r.kind === "current").map(r => r.name) : [];
}
function remoteBranches(h) {
  const n = DATA.nodes[h];
  return n ? n.refs.filter(r => r.kind === "remote").map(r => r.name) : [];
}
function defaultNewBranchName(h) {
  const remote = remoteBranches(h).sort()[0] || "";
  const slash = remote.indexOf("/");
  return slash >= 0 ? remote.slice(slash + 1) : remote;
}
function stashNameOf(h) {  // グラフ上の stash ノードの名前 (stash@{n})
  const n = DATA.nodes[h], r = n && n.refs.find(x => x.kind === "stash");
  return r ? r.name : null;
}
function localTags(h) {
  const n = DATA.nodes[h];
  return n ? n.refs.filter(r => r.kind === "tag").map(r => r.name) : [];
}
function showRefList(kind) {  // kind: "branches" | "tags"
  showLog(t(kind === "tags" ? "tag_list_title" : "branch_list_title"), "",
          '<div class="meta">' + esc(t(kind === "tags" ? "tag_list_help" : "branch_list_help")) + '</div>',
          false, 0, {path: "/api/" + kind, params: {}});
}
function showStashList(fromHistory) {
  loadStashes();
  showLog(t("stash_list_title"), "", '<div class="meta">' + esc(t("stash_list_help")) + '</div>',
          !!fromHistory, 0, {path: "/api/stash-list", params: {}});
}
function doStash(op) {
  if (!actionHash) return;
  const h = actionHash, sel = stashByHash[h] || stashNameOf(h);
  if (!sel) return;
  const label = {apply: t("op_apply"), pop: t("op_pop"), drop: t("op_drop")}[op];
  const msg = {
    apply: t("stash_confirm_apply", {sel: sel}),
    pop: t("stash_confirm_pop", {sel: sel}),
    drop: t("stash_confirm_drop", {sel: sel, hash: h.slice(0, 7)}),
  }[op];
  if (!confirm(msg)) return;
  document.querySelectorAll("#stashActions button").forEach(b => { b.disabled = true; });
  setGraphStatus(t("stash_busy"), "progress");
  const reload = () => Promise.all([loadStashes(), refreshGraph().catch(() => {})]).then(() => {
    if (curView === "list" && listView.dataset.src === "/api/stash-list") showStashList(true);
  });
  post("/api/stash", {hash: h, op: op})
    .then(r => {  // 編集中のファイルや管理外ファイルに邪魔されたら、破棄して上書きするか選ばせる
      if (!r.blocked) return r;
      setGraphStatus("");
      return askCheckoutConflict(r, "stash").then(mode => {
        if (!mode) return {cancelled: true, message: t("operation_cancelled")};
        setGraphStatus(t("stash_busy"), "progress");
        return post("/api/stash", {hash: h, op: op, on_conflict: mode});
      });
    })
    .then(r => {
      if (r.cancelled) { setGraphStatus(r.message, "error"); return reload(); }
      clearCmp(); setGraphStatus(r.message, "success"); return reload();
    })
    .catch(e => { setGraphStatus(t("stash_op_failed", {op: label, error: e.message}), "error"); return reload(); })
    .finally(() => updateCmpClasses());
}
function showReflog(branch) {  // branch が空なら HEAD の reflog
  const names = [];
  Object.values(DATA.nodes).forEach(n => n.refs.forEach(r => {
    if ((r.kind === "branch" || r.kind === "current") && !names.includes(r.name)) names.push(r.name);
  }));
  names.sort();
  if (branch && !names.includes(branch)) names.push(branch);
  const opts = ['<option value=""' + (branch ? "" : " selected") + ">HEAD</option>"].concat(names.map(n =>
    '<option value="' + esc(n) + '"' + (n === branch ? " selected" : "") + ">" + esc(n) + "</option>")).join("");
  const top = '<div class="meta"><label>' + esc(t("target_label")) + ': <select class="reflog-ref">' + opts + "</select></label></div>" +
    '<div class="meta">' + esc(t("reflog_help")) + '</div>';
  showLog("reflog " + esc(branch || "HEAD"), "", top, false, 0,
          {path: "/api/reflog", params: branch ? {branch: branch} : {}});
}
listView.addEventListener("change", ev => {
  const sel = ev.target.closest(".reflog-ref");
  if (sel) showReflog(sel.value);
  const branchSel = ev.target.closest("#containingBranchSelect");
  if (branchSel) showContainingBranchLog(branchSel.dataset.h, branchSel.value);
});
function showDetail(h, fromHistory) {
  document.body.classList.remove("compare-logs-mode");
  if (!fromHistory) pushScreen({screen: "detail", hash: h});
  setDocumentTitle(t("commit_label") + " " + h.slice(0, 7));
  const back = '<button class="back-btn">' + t("back_history") + '</button>';
  detailView.innerHTML = back + '<p class="hint">' + esc(t("loading")) + '</p>';
  showView("detail");
  api("/api/commit", {hash: h}).then(c => {
    curCtx = {hash: c.hash}; curFiles = c.files; curDetailHash = c.hash;
    revealHash(c.hash);
    const parents = c.parents.map(p => esc(p.slice(0, 7))).join(", ") || t("none");
    detailView.innerHTML = back + "<h2>" + t("commit_label") + " " + esc(c.hash.slice(0, 7)) +
      ' <a class="detail-files" href="/files?hash=' + encodeURIComponent(c.hash) + '" title="' + esc(t("tip_files")) + '">' + esc(t("files")) + "</a></h2>" +
      '<span id="dChips">' + detailChips(c.hash) + "</span>" +
      '<div class="meta">' + esc(c.hash) + copyLink(c.hash) + "</div>" +
      '<div class="meta">Author: ' + esc(c.author) + "</div>" +
      '<div class="meta">Date: ' + esc(c.date) + "</div>" +
      '<div class="meta">Parent: ' + parents + "</div>" +
      "<pre>" + esc(c.message) + "</pre>" + filesHtml(curCtx, c.files) +
      '<div class="meta"><code>git show ' + esc(c.hash.slice(0, 7)) + "</code></div>";
  }).catch(e => {
    detailView.innerHTML = back + '<p class="hint">' + esc(t("error")) + esc(e.message) + "</p>";
  });
}

function post(path, body) {
  return fetch(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)})
    .then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }));
}
function doAction(btn) {  // ブランチ/タグのチップの × (削除)
  if (btn.dataset.act !== "delete-ref") return;
  const kind = btn.dataset.kind, name = btn.dataset.name;
  const url = "/api/ref-delete", body = {kind: kind, name: name};
  if (!confirm(t(kind === "branch" ? "confirm_delete_branch" : "confirm_delete_tag", {name: name}))) return;
  btn.disabled = true; setGraphStatus(t("deleting"), "progress");
  post(url, body)
    .then(r => {
      if (!r.needs_force) return r;
      if (!confirm(t("confirm_force_delete", {name: name}))) return null;
      return post(url, Object.assign({}, body, {force: true}));
    })
    .then(r => {
      if (r === null) { setGraphStatus(t("force_delete_cancelled")); return; }
      setGraphStatus(r.message, "success");
      return refreshGraph().catch(() => {});
    })
    .catch(e => setGraphStatus(t("delete_failed", {error: e.message}), "error", true))
    .finally(() => { btn.disabled = false; });
}

