function t(key, values = {}) {
  let text = key in I18N ? I18N[key] : key;
  Object.entries(values).forEach(([name, value]) => { text = text.replaceAll("{" + name + "}", () => String(value)); });
  return text;
}

const PROJECT_DOCUMENT_TITLE = document.title.slice(0, document.title.length - t("file_view").length - 3);  // ウィンドウタイトルは「プロジェクト名 - 表示対象」
function setDocumentTitle(label) {
  document.title = PROJECT_DOCUMENT_TITLE + " - " + (label || t("file_view"));
}

const qs = new URLSearchParams(location.search), hash = qs.get("hash"), fileViewTab = qs.get("file_view") === "1";
const compareFrom = qs.get("dfrom") || "", compareTo = qs.get("dto") || "";
const sideTabs = document.getElementById("sideTabs"), sideTreeBtn = document.getElementById("sideTreeBtn"), sideChangesBtn = document.getElementById("sideChangesBtn"), changesEl = document.getElementById("changes");
const fileViewEl = document.getElementById("fileView"), wipCommitView = document.getElementById("wipCommitView");
let wipCommitPanel = null;
const list = document.getElementById("list"), content = document.getElementById("content"), title = document.getElementById("title");
const fileActions = document.getElementById("fileActions"), fileLogBtn = document.getElementById("fileLogBtn"), fileBlameBtn = document.getElementById("fileBlameBtn"), fileContentBtn = document.getElementById("fileContentBtn");
const fileGraphLogBtn = document.getElementById("fileGraphLogBtn");
const dirActions = document.getElementById("dirActions"), dirLogBtn = document.getElementById("dirLogBtn"), dirCommitLogBtn = document.getElementById("dirCommitLogBtn");
const fileCommitLogBtn = document.getElementById("fileCommitLogBtn");
const dirListBtn = document.getElementById("dirListBtn"), dirGraphLogBtn = document.getElementById("dirGraphLogBtn");
const fileDownloadBtn = document.getElementById("fileDownloadBtn"), fileExtEditBtn = document.getElementById("fileExtEditBtn");
const fileRmBtn = document.getElementById("fileRmBtn"), fileRmCachedBtn = document.getElementById("fileRmCachedBtn");
const fileCopyBtn = document.getElementById("fileCopyBtn"), fileMsg = document.getElementById("fileMsg");
const fileDiffBtn = document.getElementById("fileDiffBtn"), fileEditBtn = document.getElementById("fileEditBtn");
const fileExtDiffBtn = document.getElementById("fileExtDiffBtn"), fileMergeBtn = document.getElementById("fileMergeBtn");
const selBar = document.getElementById("selBar"), selMsg = document.getElementById("selMsg");
const selAddBtn = document.getElementById("selAddBtn"), selRmBtn = document.getElementById("selRmBtn"), selRmCachedBtn = document.getElementById("selRmCachedBtn");
const commitEl = document.getElementById("commit");
let currentCommitMeta = null;

function formatAuthor(author) {
  return (author || "").replace(/\s*<.*>$/, "");
}
function formatDate(date) {
  return (date || "").slice(0, 16);
}
function firstLine(msg) {
  return (msg || "").split("\n", 1)[0];
}
function commitLinkHtml(hashVal) {  // ハッシュのリンク + そのコミットのブランチ / タグ
  const short = (hashVal || "").slice(0, 7);
  return '<a class="commit-graph" href="/?commit_hash=' + encodeURIComponent(hashVal) + '" title="' + esc(hashVal) + '">' + esc(short) + '</a>' + (refChips(hashVal) ? ' ' + refChips(hashVal) : "");
}
function renderCommitHeader(meta) {
  if (!commitEl) return;
  if (!meta) {
    commitEl.textContent = isWip
      ? t("working_tree_files") + (wipHead ? " (HEAD " + wipHead.slice(0, 7) + ")" : "")
      : (hash ? hash.slice(0, 7) : "");
    return;
  }
  if (meta.type === "compare") {
    const a = meta.from, b = meta.to;
    let html = '<div class="commit-header compare-header">';
    if (a) {
      const subA = firstLine(a.message);
      html += '<div class="compare-row">' +
        '<span class="tag tagA">A</span>' +
        commitLinkHtml(a.hash) + ' ' +
        '<span class="commit-subject" title="' + esc(a.message || "") + '">' + esc(subA) + '</span>' +
        '<div class="commit-sub meta">' + esc(formatAuthor(a.author)) + (a.date ? ' / ' + esc(formatDate(a.date)) : '') + '</div>' +
      '</div>';
    }
    if (b) {
      if (b.hash === "WIP") {
        html += '<div class="compare-row">' +
          '<span class="tag tagB">B</span>' +
          '<strong>' + esc(t("working_tree_files")) + '</strong> <span class="hint">(WIP)</span>' +
        '</div>';
      } else {
        const subB = firstLine(b.message);
        html += '<div class="compare-row">' +
          '<span class="tag tagB">B</span>' +
          commitLinkHtml(b.hash) + ' ' +
          '<span class="commit-subject" title="' + esc(b.message || "") + '">' + esc(subB) + '</span>' +
          '<div class="commit-sub meta">' + esc(formatAuthor(b.author)) + (b.date ? ' / ' + esc(formatDate(b.date)) : '') + '</div>' +
        '</div>';
      }
    }
    html += '</div>';
    commitEl.innerHTML = html;
  } else if (meta.type === "wip") {
    const h = meta.head;
    let html = '<div class="commit-header">' +
      '<div class="commit-subject"><strong>' + esc(t("working_tree_files")) + '</strong> <span class="hint">(WIP)</span></div>';
    if (h && h.hash) {
      const sub = firstLine(h.message);
      html += '<div class="commit-sub meta">' +
        'HEAD: ' + commitLinkHtml(h.hash) + ' ' +
        '<span class="commit-subject" title="' + esc(h.message || "") + '">' + esc(sub) + '</span>' +
        (h.author ? ' <span class="hint">(' + esc(formatAuthor(h.author)) + (h.date ? ' / ' + esc(formatDate(h.date)) : '') + ')</span>' : '') +
      '</div>';
    }
    html += '</div>';
    commitEl.innerHTML = html;
  } else if (meta.type === "commit" && meta.commit) {
    const c = meta.commit;
    const sub = firstLine(c.message);
    commitEl.innerHTML = '<div class="commit-header">' +
      '<div class="commit-subject" title="' + esc(c.message || "") + '">' + esc(sub) + '</div>' +
      '<div class="commit-sub meta">' +
        commitLinkHtml(c.hash) + ' ' +
        '<span class="commit-author" title="' + esc(c.author || "") + '">' + esc(formatAuthor(c.author)) + '</span> ' +
        '<span class="commit-date">' + esc(formatDate(c.date)) + '</span>' +
      '</div></div>';
  }
}
function updateCommitHeader(meta) {
  currentCommitMeta = meta;
  renderCommitHeader(meta);
}
if (commitEl) {
  commitEl.addEventListener("click", ev => {
    const link = ev.target.closest("a.commit-graph");
    if (link && window.opener && !window.opener.closed) {
      ev.preventDefault();
      window.opener.postMessage({type: "git-graph-commit", hash: new URL(link.href, location.href).searchParams.get("commit_hash")}, location.origin);
    }
  });
}
const CAN_EDIT = CAN_COPY && hash === "WIP";  // 作業ツリーのファイルだけ編集できる (書き込みが有効なとき)
const isWip = hash === "WIP";  // 作業ツリーのファイルを表示する
(function () {  // グラフへ戻るとき、このコミットの選択を復元する
  const g = document.getElementById("navGraphBtn");
  if (g && hash) g.href = isWip ? "/?select_wip=1" : "/?select_hash=" + encodeURIComponent(hash);  // WIP もコミットも、戻ったグラフで同じ選択にする
})();
fileCopyBtn.disabled = !CAN_COPY || isWip;
let current = "", currentFile = "", wipHead = null;
let dirEntries = null, dirLogSeq = 0;  // dirEntries: 表示中のディレクトリの項目 (読み込み前は null), dirLogSeq: 古い応答を捨てるための通し番号
let treeLogAbort = null;  // 項目ごとの最終コミットを受け取っている途中の接続 (別の表示に移ったら切る。サーバーも調べるのをやめる)
let dirLogOn = false;  // ディレクトリの本文に、項目の一覧ではなく「このディレクトリのログ」(または通常のコミットログ) を出しているか
let commitLogOn = false;  // dirLogOn のうち、パスで絞らない通常のコミットログ
let wipFiles = [];  // 作業ツリーの変更の一覧 (WIP パネルが決める。パス・種類 wip・変更前のパス old_path)
let wipStatusFiles = [];
let stageOverrides = new Map(), stageDirty = false;
let wipHeadFiles = [];  // HEAD または URL の比較対象コミットと作業ツリーの差分
let wipCompareChanges = compareTo === "WIP" && compareFrom ? Object.create(null) : null, wipCompareDirs = new Set();
let diffSel = null;  // 選択中の差分の WIP 種別または比較元
let commitFiles = [];  // このコミットで変更されたファイルの一覧 (左ペインの「変更ファイル」)
let entryInfo = {}, commitChanges = null, commitDirs = new Set();  // entryInfo: パス → 一覧の項目 (WIP の変更の種類 xy), commitChanges: パス → コミットでの変更
function diffParams(path) {  // このファイルの差分 API の引数。変更がなければ null
  if (isWip) {
    if (diffSel && diffSel.path === path) {
      if (diffSel.from && diffSel.to === "WIP") {
        const p = {from: diffSel.from, wip: "compare", path: path};
        if (diffSel.old) p.old = diffSel.old;
        return p;
      }
      if (diffSel.from && diffSel.to) {
        const p = {from: diffSel.from, to: diffSel.to, path: path};
        if (diffSel.old) p.old = diffSel.old;
        return p;
      }
      return diffSel.old ? {path: path, wip: diffSel.wip, old: diffSel.old} : {path: path, wip: diffSel.wip};
    }
    // ページが「コミットと WIP の比較」(dfrom / dto=WIP) のときは、
    // ファイル一覧から内容を開いても「差分」ボタンで比較差分を出せる
    if (compareFrom && compareTo === "WIP") {
      const f = wipCompareChanges && wipCompareChanges[path];
      const p = {from: compareFrom, wip: "compare", path: path};
      if (f && f.old_path) p.old = f.old_path;
      return p;
    }
    const xy = (entryInfo[path] || {}).xy;
    if (xy) {
      const st = entryInfo[path].st;
      const kind = xy === "??" ? "untracked" : st === "U" ? "conflict" : xy[1] !== " " ? "unstaged" : "staged";
      return {path: path, wip: kind};
    }
    // 一覧を読み込んでいないディレクトリのファイルは、WIP パネルの一覧から探す (複数あれば、競合・未ステージ・未追跡・ステージ済み・amend の順)
    const same = wipFiles.filter(f => f.path === path);
    const f = ["conflict", "unstaged", "untracked", "staged", "amend"].map(k => same.find(x => x.wip === k)).find(Boolean);
    if (!f) return null;
    return f.old_path ? {path: path, wip: f.wip, old: f.old_path} : {path: path, wip: f.wip};
  }
  if (diffSel && diffSel.path === path && diffSel.from && diffSel.to === "WIP") {
    const p = {from: diffSel.from, wip: "compare", path: path};
    if (diffSel.old) p.old = diffSel.old;
    return p;
  }
  if (diffSel && diffSel.path === path && diffSel.from && diffSel.to) {
    const p = {from: diffSel.from, to: diffSel.to, path: path};
    if (diffSel.old) p.old = diffSel.old;
    return p;
  }
  // ページが 2 コミット比較 (dfrom / dto) のときは、ファイル一覧からでも「差分」ボタンで比較差分を出せる
  if (compareFrom && compareTo && compareTo !== "WIP") {
    const f = commitChanges && commitChanges[path];
    const p = {from: compareFrom, to: compareTo, path: path};
    if (f && f.old_path) p.old = f.old_path;
    return p;
  }
  const f = commitChanges && commitChanges[path];
  if (!f) return null;
  const p = {hash: hash, path: path};
  if ((diffSel && diffSel.path === path && diffSel.old) || f.old_path) p.old = (diffSel && diffSel.path === path && diffSel.old) || f.old_path;
  return p;
}
function updateRmBtns() {  // git rm できるのは、作業ツリー表示の追跡中のファイルだけ (書き込みが有効なとき)
  const tracked = !!currentFile && (entryInfo[currentFile] || {}).xy !== "??";
  fileRmBtn.disabled = fileRmCachedBtn.disabled = !(CAN_EDIT && tracked);
}
function isConflictFile(path) {  // 競合中のファイルか (差分選択 or WIP の競合一覧)
  if (!path) return false;
  if (diffSel && diffSel.path === path && diffSel.wip === "conflict") return true;
  return wipStatusFiles.some(f => f.path === path && f.wip === "conflict");
}
function updateMergeBtn() {  // 競合ファイルはマージエディタで解決できる
  const show = CAN_EDIT && isConflictFile(currentFile);
  fileMergeBtn.hidden = !show;
  fileMergeBtn.disabled = !show;
}
function updateDiffBtn() {
  const p = currentFile && diffParams(currentFile);
  fileDiffBtn.disabled = !p;
  fileExtDiffBtn.hidden = !DESKTOP || currentView !== "diff" || !p;
  updateMergeBtn();
  updateRmBtns();
}
function showFilePane() {
  fileViewEl.hidden = false;
  wipCommitView.hidden = true;
}
function showWipCommitPane() {
  if (!isWip) return;
  fileViewEl.hidden = true;
  wipCommitView.hidden = false;
  document.getElementById("main").scrollTop = 0;
}
function openWipCommit() {
  if (!isWip || !CAN_EDIT || !wipCommitView.hidden || !leaveEdit()) return;
  const params = new URLSearchParams(location.search);
  params.set("wip_commit", "1");
  history.pushState({path: current, file: currentFile, view: currentView, wipCommit: true}, "", "?" + params);
  showWipCommitPane();
}
const wipCommitMenuBtn = document.getElementById("wipCommitMenuBtn");
wipCommitMenuBtn.disabled = !CAN_COPY;
wipCommitMenuBtn.onclick = () => {
  if (!CAN_COPY) return;
  if (isWip) openWipCommit();
  else location.href = "/files?hash=WIP&wip_commit=1";
};
document.getElementById("wipCommitBack").onclick = () => {
  if (history.state && history.state.wipCommit) { history.back(); return; }
  const params = new URLSearchParams(location.search);
  params.delete("wip_commit");
  history.replaceState({path: current, file: currentFile, view: currentView}, "", "?" + params);
  showFilePane();
};
function loadCommitChanges() {  // このコミットで変更されたファイル (差分ボタンを出すかどうかに使う)
  if (isWip || !hash) return;
  const load = compareFrom && compareTo
    ? api("/api/compare", {from: compareFrom, to: compareTo})
    : api("/api/commit", {hash: hash});
  load.then(c => {
    if (compareFrom && compareTo) {
      updateCommitHeader({type: "compare", from: c.from, to: c.to});
    } else {
      updateCommitHeader({type: "commit", commit: c});
    }
    commitChanges = {};
    commitDirs = new Set();
    commitFiles = c.files;
    c.files.forEach(f => {
      commitChanges[f.path] = f;
      for (let i = f.path.indexOf("/"); i > 0; i = f.path.indexOf("/", i + 1)) commitDirs.add(f.path.slice(0, i));  // 上のディレクトリすべて
    });
    updateDiffBtn();
    refreshBadges();
    renderChanges();
  }).catch(e => { changesEl.innerHTML = '<p class="hint">' + esc(t("error") + e.message) + '</p>'; });
}

