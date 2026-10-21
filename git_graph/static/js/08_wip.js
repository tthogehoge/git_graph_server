// ---- 作業ツリーの変更 (WIP) ----
// チェックしたファイルだけを add してコミットする (TortoiseGit のコミットダイアログと同じ考え方)。
// 既定ではステージ済みのファイルがチェック済み。チェックを外したステージ済みファイルはステージから外れる。
let wipSel = {}, wipStaged = new Set(), wipMerging = false, wipMsg = "";  // wipSel: パス → ユーザーが付け外ししたチェック状態
// amend モード: チェック中は「直前のコミットの親」と作業ツリーの差分を出し、メッセージも直前のものを入れる
let wipAmendOn = false, wipMsgNormal = "", wipHeadMsg = "";  // wipMsgNormal: amend にする前に書いていたメッセージ (解除で戻す)
function wipIsChecked(f) {  // amend: 直前のコミットのファイルは、外さない限りコミットに残る (チェック済みが既定)
  return f.path in wipSel ? wipSel[f.path] : (wipMerging || !!f.in_head || wipStaged.has(f.path));
}
function wipCheckCell(f) {
  return '<td class="fk"><input type="checkbox" class="wipck" data-kind="' + esc(f.wip) + '" data-dir="' + esc(dirOf(f.path)) + '" data-path="' + esc(f.path) + '"' +
    (wipIsChecked(f) ? " checked" : "") + (f.in_head ? ' title="' + t("wip_file_remove_title") + '"' : "") + "></td>";
}
function wipCks(kind) {  // 操作できる (disabled でない) チェックボックス
  return [...detailView.querySelectorAll(".wipck" + (kind ? '[data-kind="' + kind + '"]' : ""))].filter(x => !x.disabled);
}
function wipPaths() {  // チェックされたパス (同じファイルが ステージ済み と 未ステージ の両方に出ても1件)
  const s = new Set();
  wipCks().forEach(el => { if (el.checked) s.add(el.dataset.path); });
  return [...s];
}
function setWipPath(p, v) {  // 同じパスのチェックボックスをまとめて切り替える
  wipSel[p] = v;
  wipCks().forEach(x => { if (x.dataset.path === p) x.checked = v; });
}
function wipSync() {  // コミットボタンの件数と、グループの全選択チェックを更新
  const n = wipPaths().length, b = detailView.querySelector("#wipCommit");
  const dis = detailView.querySelector("#wipDiscard");
  if (dis) { dis.disabled = n === 0; dis.textContent = t("wip_discard") + " (" + n + ")"; }
  if (b) {
    b.disabled = n === 0;
    b.textContent = t("wip_commit") + " (" + n + ")" + (wipAmendOn ? " amend" : "");
  }
  detailView.querySelectorAll(".wipdir").forEach(dc => {
    const cks = wipCks(dc.dataset.kind).filter(x => x.dataset.dir === dc.dataset.dir), c = cks.filter(x => x.checked).length;
    dc.disabled = cks.length === 0;
    dc.checked = cks.length > 0 && c === cks.length;
    dc.indeterminate = c > 0 && c < cks.length;
  });
  detailView.querySelectorAll(".wipall").forEach(all => {
    const cks = wipCks(all.dataset.kind), c = cks.filter(x => x.checked).length;
    all.disabled = cks.length === 0;
    all.checked = cks.length > 0 && c === cks.length;
    all.indeterminate = c > 0 && c < cks.length;
  });
}
function wipSay(text, ok) {
  const el = detailView.querySelector("#wipMsgOut");
  if (el) { el.textContent = text; el.className = "amsg " + (ok ? "ok" : "ng"); }
  else setGraphStatus(text, ok ? "success" : "error");
}
function setWipAmend(on) {  // amend のチェックを切り替える: 差分の一覧とメッセージを入れ替えて再表示
  if (on === wipAmendOn) return;
  wipSel = {};  // 一覧が変わるので、付け外ししたチェックはリセット
  if (on) { wipMsgNormal = wipMsg; wipMsg = wipHeadMsg; }  // 直前のコミットのメッセージを入れておく
  else { wipMsg = wipMsgNormal; wipMsgNormal = ""; }
  wipAmendOn = on;
  showWip(true);
}
function doWipCommit() {
  const amend = wipAmendOn, paths = wipPaths(), ta = detailView.querySelector("#wipMsg"), msg = ta ? ta.value : "";
  const branchInput = detailView.querySelector("#wipBranch");
  const branch = branchInput ? branchInput.value.trim() : "";
  if (!msg.trim()) { wipSay(t("message_required"), false); return; }
  if (!amend && !paths.length) { wipSay(t("select_files"), false); return; }
  if (amend) {
    const chk = new Set(paths), drops = curFiles.filter(f => f.in_head && !chk.has(f.path)).length;
    const dropText = drops ? "\n" + t("amend_drop", {n: drops}) : "";
    if (!confirm(t("amend_confirm", {drop: dropText}))) return;
  }
  const btn = detailView.querySelector("#wipCommit");
  if (btn) btn.disabled = true;
  wipSay(t("wip_execute"), true);
  post("/api/wip-commit", {paths: paths, message: msg, amend: amend, branch: branch})
    .then(r => {
      wipMsg = ""; wipMsgNormal = ""; wipSel = {}; wipAmendOn = false;
      showWip(true, r.message);
      refreshGraph().catch(() => {});
    })
    .catch(e => { wipSay(e.message, false); wipSync(); });
}
detailView.addEventListener("change", ev => {
  if (ev.target.id === "wipAmendCk") { setWipAmend(ev.target.checked); return; }
  const ck = ev.target.closest(".wipck"), all = ev.target.closest(".wipall"), dc = ev.target.closest(".wipdir");
  if (ck) setWipPath(ck.dataset.path, ck.checked);
  else if (dc) wipCks(dc.dataset.kind).filter(x => x.dataset.dir === dc.dataset.dir).forEach(x => setWipPath(x.dataset.path, dc.checked));
  else if (all) wipCks(all.dataset.kind).forEach(x => setWipPath(x.dataset.path, all.checked));
  else return;
  wipSync();
});
detailView.addEventListener("input", ev => { if (ev.target.id === "wipMsg") wipMsg = ev.target.value; });

function resolveBarHtml() {  // 競合ファイルを ours / theirs で解決するボタン (チェックしたファイルが対象)
  return '<div class="wipres"><button class="abtn" data-resolve="ours">' + esc(t("resolve_ours")) + '</button>' +
    '<button class="abtn" data-resolve="theirs">' + esc(t("resolve_theirs")) + '</button>' +
    '<span class="hint">' + esc(t("resolve_hint")) + '</span></div>';
}
function opBannerHtml(cmd, canWrite) {  // マージなどの途中であることと、中断ボタン
  if (!cmd) return "";
  return '<div class="wipop"><span>' + esc(t("op_banner", {cmd: cmd})) + '</span>' +
    (canWrite ? '<button class="abtn danger" id="wipAbort" data-cmd="' + esc(cmd) + '">' + esc(t("abort_button", {cmd: cmd})) + '</button>' : "") + '</div>';
}
function doWipResolve(side) {
  const paths = wipCks("conflict").filter(x => x.checked).map(x => x.dataset.path);
  if (!paths.length) { wipSay(t("select_files"), false); return; }
  if (!confirm(t("confirm_resolve", {n: paths.length, side: side}))) return;
  wipSay(t("wip_execute"), true);
  post("/api/resolve", {paths: paths, side: side})
    .then(r => { wipSel = {}; showWip(true, r.message); refreshGraph().catch(() => {}); })
    .catch(e => wipSay(e.message, false));
}
function doWipDiscard() {
  const paths = wipPaths();
  if (!paths.length) return;
  const confl = new Set(curFiles.filter(f => f.wip === "conflict").map(f => f.path)), hit = paths.filter(p => confl.has(p));
  if (hit.length) { wipSay(t("discard_conflict", {files: hit.slice(0, 3).join(", ")}), false); return; }
  if (!confirm(t("confirm_discard", {n: paths.length}))) return;
  const btn = detailView.querySelector("#wipDiscard");
  if (btn) btn.disabled = true;
  wipSay(t("wip_execute"), true);
  post("/api/discard", {paths: paths})
    .then(r => { wipSel = {}; showWip(true, r.message); refreshGraph().catch(() => {}); })
    .catch(e => { wipSay(e.message, false); wipSync(); });
}
function doWipAbort() {
  const b = detailView.querySelector("#wipAbort");
  if (!b) return;
  if (!confirm(t("confirm_abort", {cmd: b.dataset.cmd}))) return;
  b.disabled = true;
  post("/api/abort", {})
    .then(r => { wipSel = {}; wipMsg = ""; wipMsgNormal = ""; wipAmendOn = false; showWip(true, r.message); return refreshGraph().catch(() => {}); })
    .catch(e => { b.disabled = false; wipSay(e.message, false); });
}

function showWip(fromHistory, okMsg) {
  document.body.classList.remove("compare-logs-mode");
  if (!fromHistory) pushScreen({screen: "wip"});
  setDocumentTitle(t("wip_title"));
  const btns = '<button class="back-btn">' + t("back_history") + '</button><button class="sbtn" id="wipReload">' + t("reload") + '</button>';
  detailView.innerHTML = btns + '<p class="hint">' + t("wip_loading") + '</p>';
  showView("detail"); curDetailHash = null;
  api("/api/status", wipAmendOn ? {amend: 1} : {}).then(s => {
    wipStaged = new Set(s.staged.map(f => f.path)); wipMerging = !!s.merging; wipHeadMsg = s.head_message || "";
    const canAmend = !!(s.head && !s.merging && !s.rebasing);
    if (wipAmendOn && !s.amend_files) {  // マージ中になったなどで amend できなくなった: 通常のコミットに戻す
      wipMsg = wipMsgNormal; wipMsgNormal = ""; wipAmendOn = false;
    }
    const canCommit = !!DATA.writable;  // 書き込みが無効 (--read-only など) のときはチェック欄を出さない
    const opCmd = s.merging ? "merge" : s.rebasing ? "rebase" : s.cherry_picking ? "cherry-pick" : s.reverting ? "revert" : null;
    const sections = wipAmendOn
      ? [["amend", t("amend_content"), s.amend_files]]
      : [["conflict", t("conflict"), s.conflicts], ["staged", t("staged"), s.staged],
         ["unstaged", t("unstaged"), s.unstaged], ["untracked", t("untracked"), s.untracked]];
    let files = [], body = "";
    sections.forEach(([kind, title, items]) => {
      if (!items.length) return;
      const list = items.map(f => Object.assign({wip: kind}, f));
      body += "<h3>" + (canCommit ? '<input type="checkbox" class="wipall" data-kind="' + kind + '" title="' + t("select_all") + '">' : "") +
        title + " (" + list.length + ")</h3>" + (kind === "conflict" && canCommit ? resolveBarHtml() : "") + filesHtml({}, list, files.length, canCommit, true);
      files = files.concat(list);
    });
    curCtx = {}; curFiles = files;
    const hint = wipAmendOn
      ? t("amend_hint")
      : t("wip_hint");
    const box = canCommit && (files.length || wipAmendOn)
      ? '<div class="wipbox">' +
        '<textarea id="wipMsg" placeholder="' + t("wip_commit_message") + '"></textarea>' +
        (!wipAmendOn ? '<input type="text" id="wipBranch" placeholder="' + esc(t("wip_commit_branch_placeholder")) + '" aria-label="' + esc(t("wip_commit_branch")) + '">' : "") +
        (canAmend ? '<label class="amendrow"><input type="checkbox" id="wipAmendCk"' + (wipAmendOn ? " checked" : "") +
          '>' + t("amend_checkbox") + '</label>' : "") +
        '<div class="arow"><button class="wbtn" id="wipCommit">' + t("wip_commit") + '</button>' +
        (!wipAmendOn && !opCmd ? '<button class="wbtn danger" id="wipDiscard">' + t("wip_discard") + '</button>' : "") +
        '<span class="hint">' + hint + '</span></div>' +
        '<div class="amsg" id="wipMsgOut"></div></div>' : "";
    detailView.innerHTML = btns + "<h2>" + t("wip_title") + "</h2>" + opBannerHtml(opCmd, canCommit) +
      '<div class="meta">HEAD: ' + (s.head ? chips(s.head) + esc(s.head.slice(0, 7)) : t("no_commits_yet")) + "</div>" +
      (body || '<p class="hint">' + t("no_changes") + '</p>') + box +
      (s.truncated ? '<p class="hint">' + t("truncated_files") + '</p>' : "");
    const ta = detailView.querySelector("#wipMsg");
    if (ta) ta.value = wipMsg;
    if (okMsg) wipSay(okMsg, true);
    wipSync();
  }).catch(e => {
    detailView.innerHTML = btns + '<p class="hint">' + esc(t("error")) + esc(e.message) + "</p>";
  });
}

document.getElementById("quitBtn").addEventListener("click", () => {  // サーバーを終了する
  if (!confirm(t("confirm_quit"))) return;
  post("/api/shutdown", {})
    .then(() => {
      document.body.innerHTML = '<div style="padding:24px;font-size:14px">' + esc(t("server_stopped")) + "</div>";
    })
    .catch(e => setGraphStatus(t("quit_failed", {error: e.message}), "error"));
});
document.getElementById("reload").addEventListener("click", ev => {  // グラフを最新の状態に更新
  const b = ev.currentTarget;
  b.disabled = true; b.textContent = t("reload_busy");
  refreshGraph().catch(() => {}).finally(() => { b.disabled = false; b.textContent = t("reload"); });
});

