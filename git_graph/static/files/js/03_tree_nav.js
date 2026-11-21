/* ---- files/03_tree_nav.js ---- */
// ---- ブランチ / タグ / HEAD の表示 (グラフ画面と同じ名前・色)。コミット → 名前の一覧は、ページを開いたときに 1 回読む ----
var refLabels = {};  // コミット → [{name, kind}] (var: 読み込み前に refChips が呼ばれても落ちないようにする)
function refChips(h) { return CommitLog.chipsHtml((refLabels || {})[h], ""); }  // 札の描画はグラフ画面と共通 (commit_log.js)
function refreshRefLabels() {  // コミット / ブランチ操作のあと、左上のブランチ・タグ名を取り直す
  return api("/api/ref-labels", {}).then(r => {
    refLabels = r.labels || {};
    if (currentCommitMeta) renderCommitHeader(currentCommitMeta);
    refreshVisibleRefChips();  // 表示中のログ行のブランチ / タグも更新する
  }).catch(() => { /* 名前が出ないだけで他の表示は動く */ });
}
function refreshVisibleRefChips() {
  // ファイル / コミットログの行 (CommitLog.rowHtml の .rc)
  document.querySelectorAll("#content table.log tr.clickable[data-h]").forEach(tr => {
    const rc = tr.querySelector(".log-subj .rc");
    if (rc) rc.innerHTML = refChips(tr.getAttribute("data-h"));
  });
  // blame は行ごとにチップを埋め込んでいるので、表示中なら読み直す
  if (typeof currentView !== "undefined" && currentView === "blame" && currentFile && typeof loadFileBlame === "function") {
    loadFileBlame(currentFile);
  }
}
const refsReady = refreshRefLabels();  // ページを開いたときに 1 回読む
// コミット・push / pull / fetch のあと: ブランチ名と HEAD の表示を更新する (ページ全体の再読み込みの代わり / 直前の反映)
window.refreshFilesState = function () {
  if (typeof window.invalidateMenubarRefLabels === "function") window.invalidateMenubarRefLabels();
  return refreshRefLabels().then(() => {
    if (!isWip) return;
    // 作業ツリー表示中は HEAD が動いていることがあるので、ツリーと左上のコミット情報を読み直す
    return loadTree(current, false);
  }).then(() => {
    if (typeof refreshWipCommitPanel === "function") return refreshWipCommitPanel();
  }).catch(() => {});
};
function showError(e) {
  list.innerHTML = '<p class="hint">' + t("error") + esc(e.message) + '</p>';
  setStatus(t("error") + e.message, "error", true);
}
function pageUrl(path, file, view, dirlog) {  // 表示中のディレクトリ・ファイル・表示 (edit / blame / log) を URL に残す (戻る/進む・再読み込みで復元する)。dirlog: ファイルを開かずに、ディレクトリのログを出している
  const p = new URLSearchParams({hash: hash});
  if (path) p.set("path", path);
  if (file) p.set("file", file);
  if (file && view) p.set("view", view);
  else if (!file && dirlog) p.set("view", dirlog === "clog" ? "clog" : "log");  // clog: パスで絞らない通常のコミットログ
  if (file && view === "diff" && diffSel && diffSel.path === file) {
    if (diffSel.from && diffSel.to) { p.set("dfrom", diffSel.from); p.set("dto", diffSel.to); }
    if (diffSel.wip) p.set("dwip", diffSel.wip);
    if (diffSel.old) p.set("dold", diffSel.old);
  }
  if (fileViewTab) p.set("file_view", "1");
  return "?" + p;
}
const ST_CLASS = {M: "stM", A: "stA", D: "stD", R: "stR", U: "stU", "?": "stN"};
const COMMIT_MARK_KEY = {A: "commit_mark_A", M: "commit_mark_M", R: "commit_mark_R", D: "commit_mark_D"};
const COMPARE_MARK_KEY = {A: "compare_mark_A", M: "compare_mark_M", R: "compare_mark_R", D: "compare_mark_D"};
function changeBadge(e, path, changes, dirs, markKeys, dirKey) {
  if (!changes) return "";
  let mark, tip;
  if (e.type === "tree") {
    if (!dirs.has(path)) return "";
    mark = "M"; tip = t(dirKey);
  } else {
    const f = changes[path];
    if (!f) return "";
    mark = f.status === "A" || f.status === "C" ? "A" : f.status === "R" ? "R" : "M";
    tip = t(markKeys[mark] || markKeys.M) + (mark === "R" && f.old_path ? ": " + f.old_path : "");
  }
  return '<span class="st ' + ST_CLASS[mark] + '" title="' + esc(tip) + '">' + mark + '</span>';
}
function commitBadge(e, path) {  // コミット表示での、そのコミットで変更・追加されたものの印
  return changeBadge(e, path, commitChanges, commitDirs, COMMIT_MARK_KEY, "commit_dir_changed");
}
function refreshBadges() {  // 変更の一覧が読み込めたあと、表示済みの項目の印を更新する
  if (isWip && wipCompareChanges === null) return;
  document.querySelectorAll("#list .entry, #content tr.dir-log-row").forEach(el => {
    const e = entryInfo[el.dataset.path], slot = el.querySelector(".bd");
    if (e && slot) slot.innerHTML = statusBadge(e, el.dataset.path);
  });
}
function statusBadge(e, path) {  // WIP は作業ツリー状態または比較元との差分、コミット表示はそのコミットでの変更
  if (!isWip) return commitBadge(e, path);
  if (wipCompareChanges !== null) {
    return changeBadge(e, path, wipCompareChanges, wipCompareDirs, COMPARE_MARK_KEY, "compare_dir_changed");
  }
  if (!e.st) return "";
  const tip = e.type === "tree" ? t("wip_dir_changed") : e.xy === "??" ? t("untracked") : e.st === "U" ? t("conflict")
    : [e.xy[0] !== " " ? t("staged") : "", e.xy[1] !== " " ? t("unstaged") : ""].filter(Boolean).join(" / ");
  return '<span class="st ' + (ST_CLASS[e.st] || "stM") + '" title="' + esc(tip) + '">' + esc(e.st) + '</span>';
}
// submodule (type "commit") は、ファイルではなく別のリポジトリ。クリックで、別のプロセスのファイル表示を開く
function entryIcon(e) { return e.type === "tree" ? "📁" : e.type === "commit" ? "🔗" : "📄"; }
function entryTip(e) { return e.type === "commit" ? ' title="' + esc(t(isWip ? "submodule_entry_tip_wip" : "submodule_entry_tip")) + '"' : ""; }
function submoduleBadge(e) { return e.type === "commit" ? ' <span class="sm-badge">' + esc(t("submodule_entry")) + '</span>' : ""; }
function smMsg(text, ok, url) {
  // サブモジュール操作の結果も共通トーストで出す (成功時は URL を文言に含める)
  const msg = url ? (text + (text ? "\n" : "") + url) : text;
  setStatus(msg || "", ok ? "success" : "error", !ok);
}
let smOpening = false;
function openSubmoduleFiles(path) {
  const e = entryInfo[path] || {};
  if (smOpening || (!isWip && !e.sha)) return;
  smOpening = true;
  smMsg(t("submodule_opening", {path: path}), true);
  fetch("/api/submodule-open", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({path: path, files_hash: isWip ? "WIP" : e.sha})})
    .then(r => r.ok ? r.json() : r.text().then(m => { throw new Error(m || r.status); }))
    .then(r => {
      // ブラウザーでは、子のサーバーのファイル表示を新しいタブで開く (pywebview では、子が自分のウィンドウを開く)。
      // ポップアップとしてブロックされることがあるので、リンクも残す
      let url = "";
      if (r.port) { url = location.protocol + "//" + location.hostname + ":" + r.port + (r.start_path || "/"); window.open(url, "_blank"); }
      smMsg(r.message, true, url);
    })
    .catch(err => smMsg(err.message, false))
    .finally(() => { smOpening = false; });
}
function openEntry(path, type) { return type === "change" ? openChange(path) : type === "tree" ? openDir(path) : type === "commit" ? openSubmoduleFiles(path) : loadFile(path, true); }

// 左ペインのキーボード操作: ↑↓ で選択、Enter で右ペイン表示、← で親へ、→ でディレクトリに入る
// ファイル一覧は .entry、変更ファイル (コミット) も .entry、WIP 変更は table.files の tr.frow
let selectedEntry = null;
function selectEntry(el, keepScroll) {  // keepScroll はタブ切替など、ユーザーが覚えているスクロール位置を動かしたくないときに使う
  // 再描画や同一パスの複数行（staged/unstaged）などで複数に .selected が付くのを防ぐ
  document.querySelectorAll("#list .entry.selected, #changes .entry.selected, #changes tr.frow.selected").forEach(e => e.classList.remove("selected"));
  selectedEntry = el || null;
  if (selectedEntry) {
    selectedEntry.classList.add("selected");
    if (!keepScroll) selectedEntry.scrollIntoView({block: "nearest"});
  }
}
function toggleSelectedCheck() {
  // 選択行のチェックをスペースでトグル
  if (!selectedEntry || !selectedEntry.isConnected) return false;
  const c = selectedEntry.querySelector("input.ck:not(:disabled)");
  if (!c) return false;
  c.checked = !c.checked;
  setStageFromBox(c, c.checked);
  updateSelBar();
  return true;
}
function focusFileActionButton() {
  // ファイルを開いたあと、右ペインの操作ボタンにフォーカスを移す
  const btn = fileActions.querySelector("button:not([hidden]):not([disabled])");
  if (btn) btn.focus();
}
function parentDir(path) {
  if (!path) return "";
  const i = path.lastIndexOf("/");
  return i < 0 ? "" : path.slice(0, i);
}
function sideNavRows() {
  // 表示中の左ペイン一覧から、キーボードで辿る行を集める
  const root = activeList();
  const entries = [...root.querySelectorAll(".entry")];
  if (entries.length) return entries;
  // WIP 変更一覧 (wip_panel の table.files)
  return [...root.querySelectorAll("tr.frow:not(.dhide)")];
}
function pathOfNavRow(el) {
  if (!el) return null;
  if (el.dataset && el.dataset.path) return el.dataset.path;
  const a = el.querySelector && el.querySelector("a[href^='/files?']");
  if (!a) return null;
  try { return new URL(a.href, location.href).searchParams.get("file"); } catch (e) { return null; }
}
function openNavRow(el, key) {
  if (!el) return null;
  if (el.classList.contains("entry")) {
    const type = el.dataset.type, path = el.dataset.path;
    if (key === "ArrowRight" && type !== "tree" && type !== "commit") return null;
    selectEntry(el);
    return openEntry(path, type);
  }
  // tr.frow (WIP)
  if (key === "ArrowRight") return null;  // ディレクトリ相当ではない
  const a = el.querySelector("a[href^='/files?']");
  if (!a) return null;
  selectEntry(el);
  const q = new URL(a.href, location.href).searchParams;
  const path = q.get("file");
  if (!path) return null;
  const from = q.get("dfrom"), to = q.get("dto");
  return openWipChange(from && to
    ? {path: path, from: from, to: to, wip: "compare", old: q.get("dold") || ""}
    : {path: path, wip: q.get("dwip") || "unstaged", old: q.get("dold") || ""});
}
document.addEventListener("keydown", ev => {
  if (ev.key !== "ArrowUp" && ev.key !== "ArrowDown" && ev.key !== "ArrowLeft" &&
      ev.key !== "ArrowRight" && ev.key !== "Enter" && ev.key !== " ") return;
  if (ev.altKey || ev.ctrlKey || ev.metaKey) return;
  if (ev.target.closest("#compareToolbar")) return;
  const tag = ev.target && ev.target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || tag === "BUTTON") return;
  if (ev.target && (ev.target.isContentEditable || ev.target.closest(".cm-editor"))) return;
  if (ev.target === document.getElementById("sideSplitter")) return;  // スプリッタの ←→ は幅変更に使う
  if (!ev.target.closest("#tree")) return;
  const rows = sideNavRows();
  if (!rows.length) return;

  if (ev.key === " ") {
    if (toggleSelectedCheck()) ev.preventDefault();
    return;
  }

  if (ev.key === "ArrowLeft") {
    if (!current || activeList() !== list) return;  // 変更ファイルの一覧に、親ディレクトリは無い
    ev.preventDefault();
    openDir(parentDir(current));
    return;
  }

  if (ev.key === "Enter" || ev.key === "ArrowRight") {
    let el = selectedEntry;
    if (!el || !el.isConnected) {
      // 未選択なら現在開いているファイルを優先
      if (currentFile) el = rows.find(r => pathOfNavRow(r) === currentFile) || null;
    }
    if (!el) return;
    const p = openNavRow(el, ev.key);
    if (p === null) return;
    ev.preventDefault();
    if (p && typeof p.then === "function") {
      p.then(() => focusFileActionButton());
    }
    return;
  }

  // ↑ / ↓
  ev.preventDefault();
  let idx = selectedEntry ? rows.indexOf(selectedEntry) : -1;
  if (idx < 0 && currentFile) {
    for (let i = 0; i < rows.length; i++) if (pathOfNavRow(rows[i]) === currentFile) { idx = i; break; }
  }
  if (idx < 0) idx = ev.key === "ArrowDown" ? -1 : rows.length;
  const next = ev.key === "ArrowUp" ? idx - 1 : idx + 1;
  if (next < 0 || next >= rows.length) return;
  selectEntry(rows[next]);
});

function loadTree(path, push) {
  current = path;
  dirEntries = null;
  if (push) history.pushState({path: path, file: currentFile, view: currentView}, "", pageUrl(path, currentFile, currentView));
  return api("/api/tree", {hash: hash, path: path}).then(r => {
    dirEntries = r.entries;
    if (isWip) { wipHead = r.head || null; selBar.hidden = !wipSelectionEnabled(); loadWipChanges(); }
    if (currentCommitMeta) {
      renderCommitHeader(currentCommitMeta);
    } else if (compareFrom && compareTo) {
      const toLabel = compareTo === "WIP" ? t("working_tree_files") + " (WIP)" : compareTo.slice(0, 7);
      commitEl.textContent = "A: " + compareFrom.slice(0, 7) + " → B: " + toLabel;
    } else {
      commitEl.textContent = isWip
        ? t("working_tree_files") + (r.head ? " (HEAD " + r.head.slice(0, 7) + ")" : "") : r.hash.slice(0, 7);
    }
    const parts = path ? path.split("/") : [];
    let crumb = '<div class="crumb"><a data-dir="">📁 ' + t("root") + '</a>';
    parts.forEach((p, i) => { const d = parts.slice(0, i + 1).join("/"); crumb += ' / <a data-dir="' + esc(d) + '">' + esc(p) + '</a>'; });
    crumb += '</div>';
    list.innerHTML = crumb + (r.entries.length ? r.entries.map(e => {
      const p = path ? path + "/" + e.name : e.name;
      entryInfo[p] = e;
      const size = e.size === null ? "" : e.size.toLocaleString() + " B";
      const state = treeCheckState(p);
      const ck = wipSelectionEnabled() ? '<input type="checkbox" class="ck" data-kind="tree" data-path="' + esc(p) + '" aria-label="' + esc(p) + '"' +
        (state.checked ? " checked" : "") + '>' : "";
      return '<div class="entry" data-type="' + e.type + '" data-path="' + esc(p) + '"' + entryTip(e) + '>' + ck + '<span>' + entryIcon(e) + ' <span class="bd">' + statusBadge(e, p) + '</span>' + esc(e.name) + submoduleBadge(e) + '</span><span class="size">' + size + '</span></div>';
    }).join("") : '<p class="hint">' + t("empty_directory") + '</p>');
    list.querySelectorAll("[data-dir]").forEach(a => a.onclick = () => openDir(a.dataset.dir));
    list.querySelectorAll(".entry").forEach(a => {
      a.onclick = () => {
        selectEntry(a);
        openEntry(a.dataset.path, a.dataset.type);
      };
    });
    bindSelectionChecks(list);
    syncTreeCheckBoxes();
    // 一覧を作り直したあと、tree モードのときだけ選択を復元する。
    // changes モードで loadTree が走ると（別ディレクトリの変更を開いたときなど）
    // ここで selectEntry すると変更一覧の .selected が消えてしまうため。
    if (sideMode === "tree") {
      selectedEntry = null;
      if (currentFile) {
        const cur = [...list.querySelectorAll(".entry")].find(e => e.dataset.path === currentFile);
        if (cur) selectEntry(cur);
      }
    }
    updateSelBar();
  }).catch(e => {
    // 対象ファイルの親ディレクトリがこのコミットに無いとき (ログから古いコミットを選んだ場合など) はルートを表示する
    if (path) return loadTree("", false);
    showError(e);
  });
}
fileDownloadBtn.onclick = () => {  // Content-Disposition: attachment なので、画面は移動せず保存される
  if (currentFile) location.href = "/download?" + new URLSearchParams({hash: hash, path: currentFile});
};
fileUploadBtn.onclick = () => {  // ファイルを選んで、その内容でこのパスの作業ツリーのファイルを上書きする
  if (!currentFile || !CAN_EDIT || editing) return;
  let input = document.getElementById("fileUploadInput");
  if (!input) {
    input = document.createElement("input");
    input.type = "file"; input.id = "fileUploadInput"; input.hidden = true;
    document.body.appendChild(input);
  }
  input.value = "";
  input.onchange = async () => {
    const file = input.files && input.files[0];
    if (!file) return;
    fileUploadBtn.disabled = true;
    setFileMsg(t("upload_running", {n: 1}), "");
    try {
      const r = await fetch("/api/file-upload", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({path: currentFile, data: await fileToBase64(file), overwrite: true}),
      });
      if (!r.ok) throw new Error(await r.text() || r.status);
      setFileMsg((await r.json()).message, true);
      await loadTree(current, false);  // 変更の印を作り直す
      if (typeof loadWipChanges === "function") loadWipChanges();
      if (currentFile && (currentView === "" || currentView === "content")) loadFile(currentFile, false);  // 表示中なら内容を作り直す
    } catch (e) {
      setFileMsg(t("upload_failed", {error: e.message || String(e)}), false);
    } finally {
      fileUploadBtn.disabled = false;
    }
  };
  input.click();
};
fileExtEditBtn.onclick = () => {  // OS の標準のエディタ、または設定ファイルの editor で開く
  if (!currentFile) return;
  fileExtEditBtn.disabled = true;
  fetch("/api/open-editor", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({path: currentFile})})
    .then(r => r.ok ? r.json() : r.text().then(m => { throw new Error(m || r.status); }))
    .then(r => setFileMsg(r.message, true))
    .catch(e => setFileMsg(e.message, false))
    .finally(() => { fileExtEditBtn.disabled = false; });
};


// ---- WIP: ファイルをドラッグ&ドロップで作業ツリーへアップロード ----
function wipUploadDir() {
  // いま見ているディレクトリ。ファイルを開いているときはその親
  if (currentFile) return parentDir(currentFile);
  return current || "";
}
function safeUploadName(name) {
  // パス区切りや .. を落として basename だけ使う
  name = String(name || "").replace(/\\/g, "/");
  const base = name.split("/").pop() || "";
  if (!base || base === "." || base === "..") return "";
  return base;
}
function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => {
      const s = String(r.result || "");
      const i = s.indexOf(",");
      resolve(i >= 0 ? s.slice(i + 1) : s);
    };
    r.onerror = () => reject(r.error || new Error("read failed"));
    r.readAsDataURL(file);
  });
}
async function uploadDroppedFiles(fileList) {
  if (!isWip || !CAN_EDIT) return;
  const files = [...fileList].filter(f => f && f.name);
  if (!files.length) return;
  const dir = wipUploadDir();
  let okN = 0, skipN = 0, errMsg = "";
  setStatus(t("upload_running", {n: files.length}), "progress");
  for (const file of files) {
    const base = safeUploadName(file.name);
    if (!base) { skipN++; continue; }
    const path = dir ? dir + "/" + base : base;
    try {
      const data = await fileToBase64(file);
      let body = {path: path, data: data, overwrite: false};
      let r = await fetch("/api/file-upload", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const msg = await r.text();
        // 既存ファイル (メッセージにパスが含まれる) なら上書き確認
        if (r.status === 400 && msg.indexOf(path) >= 0) {
          if (!confirm(t("upload_overwrite_confirm", {path: path}))) { skipN++; continue; }
          body.overwrite = true;
          r = await fetch("/api/file-upload", {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify(body),
          });
          if (!r.ok) { errMsg = await r.text() || r.status; break; }
        } else {
          errMsg = msg || r.status;
          break;
        }
      }
      okN++;
    } catch (e) {
      errMsg = e.message || String(e);
      break;
    }
  }
  await loadTree(current, false);
  if (typeof loadWipChanges === "function") loadWipChanges();
  if (errMsg) setStatus(t("upload_failed", {error: errMsg}), "error", true);
  else if (okN) setStatus(skipN ? t("upload_done_skip", {n: okN, skip: skipN}) : t("upload_done", {n: okN}), "success");
  else setStatus(t("upload_none"), "error", true);
}
function bindWipFileDrop() {
  const zones = [document.getElementById("list"), document.getElementById("changes"), document.getElementById("main")].filter(Boolean);
  zones.forEach(zone => {
    zone.addEventListener("dragover", ev => {
      if (!isWip || !CAN_EDIT) return;
      if (![...ev.dataTransfer.types].includes("Files")) return;
      ev.preventDefault();
      ev.dataTransfer.dropEffect = "copy";
      zone.classList.add("drop-target");
    });
    zone.addEventListener("dragleave", ev => {
      if (!zone.contains(ev.relatedTarget)) zone.classList.remove("drop-target");
    });
    zone.addEventListener("drop", ev => {
      zone.classList.remove("drop-target");
      if (!isWip || !CAN_EDIT) return;
      if (!ev.dataTransfer || !ev.dataTransfer.files || !ev.dataTransfer.files.length) return;
      ev.preventDefault();
      uploadDroppedFiles(ev.dataTransfer.files);
    });
  });
}
bindWipFileDrop();
