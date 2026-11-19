// その他メニューの一覧・submodule 操作で、グラフ画面とファイル表示が共通で使う部品 (OtherLists)。
// 画面ごとの違いは deps で渡す (t / post / askAuth / esc)。
const OtherLists = (function () {
  function esc(s) {
    return String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
  }

  // submodule の 1 行。c = [hash, short, status, describe, subject]; sm = {path, url, initialized}
  // opts: {writable, t} (t は翻訳関数)
  function submoduleRowHtml(c, sm, opts) {
    opts = opts || {};
    const t = opts.t || (k => k);
    const e = opts.esc || esc;
    const writable = !!opts.writable;
    const sel = writable ? '<td class="sel"><input type="checkbox" class="submodule-sel" data-path="' + e(sm.path) + '"></td>' : "";
    return '<tr class="submodule-row' + (sm.initialized ? "" : " uninit") + '" data-path="' + e(sm.path) + '" data-init="' + (sm.initialized ? "1" : "") +
      '" title="' + e(t(sm.initialized ? "submodule_open_tip" : "submodule_uninit_tip")) + '">' + sel +
      '<td class="h">' + e(c[1]) + '</td><td class="log-cell"><div class="log-main"><div class="log-subj">' + e(c[4]) +
      '</div><div class="meta">' + e(c[2]) + (c[3] ? " / " + e(c[3]) : "") + "</div></div></td></tr>";
  }

  // 操作バー + 追加フォームの HTML。writable が false ならメッセージ欄だけ。
  function submoduleBarHtml(opts) {
    opts = opts || {};
    const t = opts.t || (k => k);
    const e = opts.esc || esc;
    if (!opts.writable) return '<div class="meta" id="submoduleMsg"></div>';
    return '<div class="submodule-bar"><label><input type="checkbox" id="submoduleAll"> ' + e(t("submodule_select_all")) + "</label> " +
      '<button class="sbtn" id="submoduleUpdateBtn" disabled title="' + e(t("submodule_update_tip")) + '">' + e(t("submodule_update")) + "</button> " +
      '<button class="sbtn" id="submoduleUpdateRemoteBtn" disabled title="' + e(t("submodule_update_remote_tip")) + '">' + e(t("submodule_update_remote")) + "</button> " +
      '<button class="sbtn" id="submoduleFetchBtn" disabled title="' + e(t("submodule_fetch_tip")) + '">' + e(t("submodule_fetch")) + "</button> " +
      '<label title="' + e(t("submodule_recursive_tip")) + '"><input type="checkbox" id="submoduleRecursive"> ' + e(t("submodule_recursive")) + "</label> " +
      '<button class="sbtn" id="submoduleAddToggle" title="' + e(t("submodule_add_tip")) + '">' + e(t("submodule_add_toggle")) + "</button></div>" +
      '<div class="submodule-bar" id="submoduleAddForm" hidden>' +
      '<input id="smAddUrl" size="42" placeholder="' + e(t("submodule_add_url")) + '"> ' +
      '<input id="smAddPath" size="22" placeholder="' + e(t("submodule_add_path")) + '"> ' +
      '<input id="smAddBranch" size="16" placeholder="' + e(t("submodule_add_branch")) + '"> ' +
      '<button class="sbtn" id="submoduleAddGo">' + e(t("submodule_add_go")) + "</button></div>" +
      '<div class="meta" id="submoduleMsg"></div>';
  }

  // HTTP 認証が必要だと返されたら、origin ごとに askAuth(origin) で認証情報を尋ねてやり直す。
  // deps: {post(url, body) → Promise, t(key, params), askAuth(origin) → Promise<{username,password}|null>}
  async function postWithAuth(url, body, doneKey, deps) {
    const post = deps.post, t = deps.t, askAuth = deps.askAuth;
    let r = await post(url, body);
    if (!r.auth_required) return r;
    const merged = new Map((r.results || []).map(x => [x.path, x]));
    const asked = new Set(), pending = (r.auth_requests || []).slice();
    while (pending.length) {
      const req = pending.shift();
      if (asked.has(req.origin)) continue;
      asked.add(req.origin);
      const auth = await askAuth(req.origin);
      if (!auth) {
        for (const p of (req.paths || [])) merged.set(p, {path: p, ok: false, error: t("submodule_auth_cancelled")});
        if (!req.paths) throw new Error(t("submodule_auth_cancelled"));
        continue;
      }
      r = await post(url, Object.assign({}, body, req.paths ? {paths: req.paths} : {}, {auth: auth, auth_origin: req.origin}));
      for (const x of (r.results || [])) merged.set(x.path, x);
      for (const q of (r.auth_requests || [])) pending.push(q);
      if (!r.results) return r;
    }
    const results = Array.from(merged.values()), done = results.filter(x => x.ok).length;
    return {ok: done === results.length, results: results, message: doneKey ? t(doneKey, {done: done, total: results.length}) : (r.message || "")};
  }

  // 操作履歴の表 HTML。entries: [{time, label, done, undoable, problem}]
  function opHistoryHtml(entries, opts) {
    opts = opts || {};
    const t = opts.t || (k => k);
    const e = opts.esc || esc;
    const rows = (entries || []).map(entry => {
      const note = entry.done ? "" : " (" + t("op_history_undone") + ")";
      const warn = entry.undoable ? "" : ' <span class="hint">' + e(t("op_history_irreversible", {reason: entry.problem || ""})) + "</span>";
      return '<tr class="' + (entry.done ? "" : "oph-undone") + '"><td class="h">' + e(new Date(entry.time * 1000).toLocaleString()) +
        '</td><td class="log-cell">' + e(entry.label) + e(note) + warn + "</td></tr>";
    }).join("");
    if (!rows) return '<div class="hint">' + e(t("op_history_empty")) + "</div>";
    return '<table class="log"><tbody>' + rows + "</tbody></table>";
  }

  // URL 入力から path 案を入れる (最後の要素から .git を除く)。パスを自分で触ったら止める。
  function bindAddPathHint(root) {
    if (!root) return;
    root.addEventListener("input", ev => {
      const el = ev.target;
      if (el.id === "smAddPath") { el.dataset.touched = "1"; return; }
      if (el.id !== "smAddUrl") return;
      const pathEl = root.querySelector("#smAddPath") || document.getElementById("smAddPath");
      if (!pathEl || pathEl.dataset.touched) return;
      const last = el.value.trim().replace(/[\/:]+$/, "").split(/[\/:]/).pop() || "";
      pathEl.value = last.replace(/\.git$/, "");
    });
  }

  // 選択状態とボタンの有効/無効を同期する。root 内の checkbox / ボタンを対象。
  function syncSel(root, busy) {
    root = root || document;
    const all = Array.from(root.querySelectorAll("input.submodule-sel"));
    const n = all.filter(el => el.checked).length;
    for (const id of ["submoduleUpdateBtn", "submoduleUpdateRemoteBtn", "submoduleFetchBtn"]) {
      const b = root.querySelector("#" + id) || document.getElementById(id);
      if (b) b.disabled = n === 0 || !!busy;
    }
    const top = root.querySelector("#submoduleAll") || document.getElementById("submoduleAll");
    if (top) { top.checked = all.length > 0 && n === all.length; top.indeterminate = n > 0 && n < all.length; }
  }

  function selectedPaths(root) {
    root = root || document;
    return Array.from(root.querySelectorAll("input.submodule-sel:checked")).map(el => el.dataset.path);
  }

  function recursiveOn(root) {
    root = root || document;
    const el = root.querySelector("#submoduleRecursive") || document.getElementById("submoduleRecursive");
    return !!(el && el.checked);
  }

  return {
    esc: esc,
    submoduleRowHtml: submoduleRowHtml,
    submoduleBarHtml: submoduleBarHtml,
    postWithAuth: postWithAuth,
    opHistoryHtml: opHistoryHtml,
    bindAddPathHint: bindAddPathHint,
    syncSel: syncSel,
    selectedPaths: selectedPaths,
    recursiveOn: recursiveOn,
  };
})();
