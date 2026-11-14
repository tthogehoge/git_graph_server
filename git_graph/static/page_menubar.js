// ファイル表示の共通メニューバー (static/menubar.html。グラフ画面と同じ部品) の動作。
// グラフ画面では js/ の部品が同じ部品を動かすので、ここは data-page がグラフ以外のときだけ働く。
// 方針: その画面でできる操作はその場で行い、できない操作は隠さず無効にする (理由は title に出す)。
// push / pull / fetch は wip_diff_remote.js。ダイアログの部品 (openDialog など) は diff_dialog.js、文言は T。
function mbSay(text, state) {  // メッセージ欄。state: true = 成功 / false = 失敗 / "progress" (または省略) = 実行中・案内
  const el = document.getElementById("mbmsg");
  if (!el) return;
  el.textContent = text || "";
  el.className = state === true ? "ok" : state === false ? "ng" : "";
}
(function () {
  const bar = document.getElementById("compareToolbar");
  if (!bar || bar.dataset.page === "graph") return;
  const $ = id => document.getElementById(id);
  const page = bar.dataset.page;  // "files" (legacy "diff" URLs redirect here)
  const q = new URLSearchParams(location.search);
  const HASH = /^[0-9a-f]{7,40}$/;
  // いま表示しているコミット (作業ツリーなどは空)。差分はサーバーが data-commit に入れ、ファイル表示は URL の hash
  const here = bar.dataset.commit || (page === "files" && HASH.test(q.get("hash") || "") ? q.get("hash") : "");
  const writable = bar.dataset.writable === "1", worktree = bar.dataset.worktree === "1";
  const go = url => { location.href = url; };
  const getJson = url => fetch(url).then(r => r.ok ? r.json() : r.text().then(m => { throw new Error(m || r.status); }));
  const fail = e => mbSay(e.message, false);

  // 選択 (対象 / A / B)。グラフ画面と同じ sessionStorage に残すので、グラフへ戻っても引き継がれる
  const KEY = "git_graph_ui_sel";
  const load = () => { try { const o = JSON.parse(sessionStorage.getItem(KEY) || "null"); return o && typeof o === "object" ? o : {}; } catch (e) { return {}; } };
  const sel = load();
  for (const k of ["compareA", "compareB", "actionHash"]) if (!HASH.test(sel[k] || "")) sel[k] = null;
  const save = () => { try { sessionStorage.setItem(KEY, JSON.stringify({compareA: sel.compareA, compareB: sel.compareB, actionHash: sel.actionHash})); } catch (e) { /* 保存できなくても動作には影響しない */ } };
  const target = () => sel.actionHash || here;  // 操作の対象: グラフで選んだコミット、無ければ表示中のコミット

  // 指定したコミットを、いまと同じ種類のページで開く。開いているディレクトリ / ファイルはそのまま引き継ぐ (無ければページ側が案内する)
  function viewUrl(hash) {
    const p = new URLSearchParams();
    if (page === "files") {
      p.set("hash", hash);
      ["path", "file"].forEach(k => { if (q.get(k)) p.set(k, q.get(k)); });
      if (q.get("file_view")) p.set("file_view", "1");
      return "/files?" + p;
    }
    p.set("hash", hash);
    if (q.get("path")) p.set("path", q.get("path"));
    if (page === "diff" && q.get("path")) {
      p.set("file", q.get("path")); p.set("view", "diff");
      if (q.get("wip")) p.set("dwip", q.get("wip"));
      if (q.get("old")) p.set("dold", q.get("old"));
      if (q.get("from")) p.set("dfrom", q.get("from"));
      if (q.get("to")) p.set("dto", q.get("to"));
    }
    return "/files?" + p;
  }
  async function headCommit() {
    const r = await getJson("/api/head");
    if (!r.head) throw new Error(T.mb_no_commit);
    return r.head;
  }

  // ---- この画面でできる操作 (id → 動作)。ここに無いボタンは、下で無効にする ----
  const can = {};  // ボタン要素 → 有効かどうかの関数
  const enable = (el, fn) => { if (el) can[el.id || (el.dataset.cact && "cact:" + el.dataset.cact)] = fn || (() => true); };

  $("backBtn").addEventListener("click", () => history.back());
  $("wipBtn").addEventListener("click", () => go(viewUrl("WIP")));
  $("topBtn").addEventListener("click", () => headCommit().then(h => go(viewUrl(h))).catch(fail));

  const hashButtons = [["actionTarget", () => T.target_label, "actionHash"], ["cmpA", () => "A", "compareA"], ["cmpB", () => "B", "compareB"]];
  hashButtons.forEach(([id, label, key]) => $(id).addEventListener("click", () => { if (sel[key]) go(viewUrl(sel[key])); }));
  $("swapAbBtn").addEventListener("click", () => { [sel.compareA, sel.compareB] = [sel.compareB, sel.compareA]; save(); refresh(); });
  $("clearCmpBtn").addEventListener("click", () => { sel.compareA = sel.compareB = sel.actionHash = null; save(); refresh(); });
  for (const slot of ["A", "B"]) {
    const key = "compare" + slot;
    $("set" + slot + "Btn").addEventListener("click", () => {  // 設定済みなら解除 (グラフ画面と同じ)
      const h = target();
      if (!h) return;
      sel[key] = sel[key] === h ? null : h;
      save(); closeMenus(); refresh();
    });
    $("unset" + slot + "Btn").addEventListener("click", () => { sel[key] = null; save(); closeMenus(); refresh(); });
  }
  $("compareWipBtn").addEventListener("click", () => {  // 操作の対象のコミットと、作業ツリー (WIP) の間の差分
    const h = target();
    if (!h) return;
    closeMenus();
    go("/files?" + new URLSearchParams({hash: "WIP", dfrom: h, dto: "WIP"}));
  });
  // ページ側 (ファイル表示のログ / blame の行のクリックなど) から、操作の対象を変える。開いたあとのページで、メニューの対象にも反映される
  window.mbSetTarget = h => {
    if (!HASH.test(h || "")) return;
    sel.actionHash = h;
    save(); refresh();
  };
  $("compareBtn").addEventListener("click", () => {  // A と B の間の差分
    const p = new URLSearchParams({hash: sel.compareB, dfrom: sel.compareA, dto: sel.compareB});
    go("/files?" + p);
  });

  // ---- 状態に合わせて、表示と有効 / 無効を決める ----
  const abIds = ["unsetABtn", "unsetBBtn", "compareBtn", "compareLogsBtn", "mergeIntoBtn", "cherryOntoBtn", "rebaseOntoBtn", "resetBranchBtn",
                 "rebaseRangeBtn", "rebaseDescendantsBtn", "rebaseEquivalentBtn", "findEquivalentParentsBtn", "findEquivalentBranchBtn"];
  function refresh() {
    const A = sel.compareA, B = sel.compareB, h = target();
    hashButtons.forEach(([id, label, key]) => {
      const el = $(id);
      el.textContent = sel[key] ? label() + ": " + sel[key].slice(0, 7) : "";
      el.hidden = !sel[key];
      el.title = T.tip_go_commit;
    });
    $("swapAbBtn").hidden = !A || !B;
    $("clearCmpBtn").disabled = !A && !B && !sel.actionHash;
    $("cmpActToggle").disabled = !h;
    $("abActToggle").disabled = !A || !B;
    $("actionsEmpty").hidden = !!h;
    $("aActions").hidden = !h;
    $("setABtn").textContent = h && A === h ? T.unset_a : T.set_as_a;
    $("setBBtn").textContent = h && B === h ? T.unset_b : T.set_as_b;
    $("unsetABtn").hidden = !A; $("unsetBBtn").hidden = !B;
    ["compareBtn", ...abIds.slice(3)].forEach(id => { $(id).hidden = !A || !B; });
    $("wipBtn").classList.toggle("mbhere", page === "files" ? q.get("hash") === "WIP" : bar.dataset.wip === "1");
    $("topBtn").classList.remove("mbhere");
    hashButtons.forEach(([id, label, key]) => $(id).classList.toggle("mbhere", !!here && sel[key] === here));
  }

  // この画面でできない操作は、隠さず無効にする。できるかどうかは can の関数 (無ければ常にできない) で決める
  enable($("compareWipBtn"), () => worktree); enable($("setABtn")); enable($("setBBtn")); enable($("unsetABtn")); enable($("unsetBBtn")); enable($("compareBtn"));
  enable($("quitBtn")); enable($("keyboardHelpBtn")); enable($("clearCmpBtn")); enable($("swapAbBtn")); enable($("backBtn"));
  enable($("wipBtn"), () => worktree); enable($("topBtn"));
  enable($("wipCommitMenuBtn"), () => writable && worktree);
  enable(bar.querySelector('[data-cact="branch"]'), () => writable); enable(bar.querySelector('[data-cact="tag"]'), () => writable);
  enable(bar.querySelector('[data-cact="checkout"]'), () => writable && worktree); enable($("resetBtn"), () => writable && worktree);
  bar.querySelectorAll("#remoteActions [data-cact]").forEach(b => enable(b, () => writable && worktree));
  function applyUnavailable() {
    const skip = new Set(["actionTarget", "cmpA", "cmpB", "cmpActToggle", "abActToggle", "otherMenuBtn", "reload", "rld", "reloadBtn", "navGraphBtn", "navFilesBtn", "clearCmpBtn"]);
    bar.querySelectorAll("button").forEach(b => {
      if (skip.has(b.id)) return;
      const id = b.id || (b.dataset.cact && "cact:" + b.dataset.cact);
      const f = can[id];
      if (f && f()) return;
      b.disabled = true;
      b.title = T.mb_unavailable;
    });
  }
  // 元に戻す / やり直す (グラフ画面の操作の履歴を使う) は、この画面ではまだ使えない
  applyUnavailable();
  refresh();

  // ---- メニューの開閉 (操作 / AB操作 / その他) ----
  const menus = [["cmpActToggle", "cmpActions"], ["abActToggle", "abActions"], ["otherMenuBtn", "otherActions"]];
  const closeMenus = () => menus.forEach(([b, m]) => { $(m).hidden = true; $(b).setAttribute("aria-expanded", "false"); });
  const menuButtons = menu => [...menu.querySelectorAll(".abtn")].filter(b => !b.disabled && !b.closest("[hidden]"));
  const toolbarItems = () => [...bar.querySelectorAll("button, a[href]")]
    .filter(b => !b.disabled && !b.closest(".dropdown, [hidden]"));
  const leftPane = $("tree") || $("side"), mainPane = $("main");
  const regions = [bar, leftPane, mainPane].filter(Boolean);
  regions.forEach(region => { region.tabIndex = -1; });
  function focusRegion(region) {
    if (region === bar) {
      closeMenus();
      (toolbarItems()[0] || bar).focus();
    } else if (region === mainPane) {
      const target = [...mainPane.querySelectorAll("button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled)")]
        .find(el => !el.closest("[hidden]"));
      (target || mainPane).focus({preventScroll: true});
    } else if (region) region.focus({preventScroll: true});
  }
  function hasOpenDialog() {
    return [...document.querySelectorAll('[role="dialog"], #dlgBg')]
      .some(el => !el.closest("[hidden]"));
  }
  document.addEventListener("keydown", ev => {
    if (ev.defaultPrevented || hasOpenDialog()) return;
    if (ev.altKey && !ev.ctrlKey && !ev.metaKey && ev.key.toLowerCase() === "m") {
      const target = ev.target;
      if (target && (["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) ||
          target.isContentEditable || target.closest(".cm-editor"))) return;
      ev.preventDefault();
      focusRegion(bar);
      return;
    }
    if (ev.key !== "F6" || ev.ctrlKey || ev.metaKey || ev.altKey) return;
    ev.preventDefault();
    closeMenus();
    const active = document.activeElement;
    const current = regions.findIndex(region => region.contains(active) ||
      (region === leftPane && active.id === "sideSplitter"));
    const direction = ev.shiftKey ? -1 : 1;
    const next = current < 0
      ? (direction > 0 ? 0 : regions.length - 1)
      : (current + direction + regions.length) % regions.length;
    focusRegion(regions[next]);
  });
  menus.forEach(([b, m]) => $(b).addEventListener("click", ev => {
    ev.stopPropagation();
    const open = $(m).hidden;
    closeMenus();
    $(m).hidden = !open;
    $(b).setAttribute("aria-expanded", String(open));
    if (open && ev.detail === 0) menuButtons($(m))[0]?.focus();
  }));
  bar.addEventListener("keydown", ev => {
    const toggle = ev.target.closest("#cmpActToggle, #abActToggle, #otherMenuBtn");
    if (toggle && (ev.key === "ArrowDown" || ev.key === "ArrowUp")) {
      ev.preventDefault();
      const menu = $(toggle.getAttribute("aria-controls"));
      if (menu.hidden) toggle.click();
      const buttons = menuButtons(menu);
      buttons[ev.key === "ArrowDown" ? 0 : buttons.length - 1]?.focus();
      return;
    }
    const menu = ev.target.closest(".dropdown");
    if (!menu) {
      const items = toolbarItems();
      const index = items.indexOf(ev.target.closest("button, a[href]"));
      if (index < 0) return;
      let next;
      if (ev.key === "ArrowRight") next = (index + 1) % items.length;
      else if (ev.key === "ArrowLeft") next = (index + items.length - 1) % items.length;
      else if (ev.key === "Home") next = 0;
      else if (ev.key === "End") next = items.length - 1;
      else return;
      ev.preventDefault();
      items[next].focus();
      return;
    }
    const buttons = menuButtons(menu);
    if (ev.key === "Escape") {
      ev.preventDefault();
      closeMenus();
      $(menus.find(([, id]) => id === menu.id)[0]).focus();
      return;
    }
    if (!buttons.length) return;
    const index = buttons.indexOf(ev.target.closest(".abtn"));
    if (index < 0) return;
    let next;
    if (ev.key === "ArrowDown") next = (index + 1) % buttons.length;
    else if (ev.key === "ArrowUp") next = (index + buttons.length - 1) % buttons.length;
    else if (ev.key === "Home") next = 0;
    else if (ev.key === "End") next = buttons.length - 1;
    else return;
    ev.preventDefault();
    buttons[next].focus();
  });
  document.addEventListener("click", ev => { if (!ev.target.closest(".menu-wrap")) closeMenus(); });
  document.addEventListener("keydown", ev => { if (ev.key === "Escape") closeMenus(); });
  bar.querySelectorAll(".dropdown .abtn").forEach(b => b.addEventListener("click", () => closeMenus()));

  // ---- ブランチ・タグの作成 (操作の対象のコミットに対して) ----
  async function targetCommit() {
    const h = target();
    if (HASH.test(h || "")) return h;
    const f = page === "files" ? q.get("hash") : "";
    return HASH.test(f || "") ? f : headCommit();  // 作業ツリーの表示で何も選んでいなければ HEAD
  }
  async function createBranch() {
    const h = await targetCommit();
    const v = await openDialog(T.create_branch + " (" + h.slice(0, 7) + ")", form => {
      field(form, T.branch_name, input("text", "name"), false).required = true;
      field(form, T.checkout_after_create, input("checkbox", "checkout"), true);
    }, T.create);
    if (!v) return;
    const name = v.name.trim();
    if (!name) { mbSay(T.branch_name_required, false); return; }
    if (v.checkout && !confirm(fmt(T.confirm_create_checkout, {name: name}))) return;
    const body = {hash: h, name: name, checkout: !!v.checkout};
    let r = await postJson("/api/branch", body);
    if (r.blocked) {  // 編集中のファイルや管理外ファイルに、チェックアウトを止められた
      const mode = await askBlockedDialog(r, "checkout");
      if (!mode) { mbSay(T.operation_cancelled, false); return; }
      r = await postJson("/api/branch", Object.assign({}, body, {on_conflict: mode}));
    }
    mbSay(r.message, true);
    if (body.checkout) setTimeout(() => location.reload(), 800);  // 切り替わった作業ツリーを表示に反映する
  }
  async function createTag() {
    const h = await targetCommit();
    const v = await openDialog(T.create_tag + " (" + h.slice(0, 7) + ")", form => {
      field(form, T.tag_name, input("text", "name"), false).required = true;
      field(form, T.message_optional, input("text", "message"), false);
    }, T.create);
    if (!v) return;
    const name = v.name.trim();
    if (!name) { mbSay(T.tag_name_required, false); return; }
    const r = await postJson("/api/tag", {hash: h, name: name, message: (v.message || "").trim()});
    mbSay(r.message, true);
  }
  bar.querySelector('[data-cact="branch"]').addEventListener("click", () => createBranch().catch(fail));
  bar.querySelector('[data-cact="tag"]').addEventListener("click", () => createTag().catch(fail));

  // ---- チェックアウト (操作の対象のコミットを。そのコミットに付いているブランチ / 切り離し / 新しいブランチ) ----
  async function doCheckout() {
    const h = await targetCommit();
    const refs = await getJson("/api/commit-refs?hash=" + encodeURIComponent(h));
    const defaultRemote = refs.remote[0] || "";
    const slash = defaultRemote.indexOf("/");
    const defaultNew = slash >= 0 ? defaultRemote.slice(slash + 1) : defaultRemote;
    const v = await openDialog(T.checkout + " (" + h.slice(0, 7) + ")", form => {
      const branch = selectEl("branch", [...refs.local.map(b => [b, b]), ["", T.checkout_detached_option], ["__new_branch__", T.checkout_new_branch_option]], refs.local[0] || "");
      field(form, T.switch_to, branch, false);
      const name = input("text", "newBranch"); name.value = defaultNew; name.placeholder = T.new_branch_placeholder;
      const nameLabel = field(form, T.new_branch_name, name, false).parentElement;
      const track = input("checkbox", "trackUpstream", !!defaultRemote);
      const trackLabel = field(form, T.track_upstream + (defaultRemote ? " (" + defaultRemote + ")" : ""), track, true).parentElement;
      const update = () => { const isNew = branch.value === "__new_branch__"; nameLabel.hidden = !isNew; trackLabel.hidden = !isNew || !defaultRemote; };
      branch.addEventListener("change", update);
      update();
    }, T.checkout);
    if (!v) return;
    let url, body, ask;
    if (v.branch === "__new_branch__") {
      const name = v.newBranch.trim();
      if (!name) { mbSay(T.new_branch_name_required, false); return; }
      url = "/api/branch"; body = {hash: h, name: name, checkout: true};
      if (v.trackUpstream && defaultRemote) body.upstream = defaultRemote;
      ask = fmt(T.confirm_create_checkout, {name: name});
    } else if (v.branch) {
      url = "/api/checkout"; body = {branch: v.branch};
      ask = fmt(T.confirm_checkout_branch, {branch: v.branch});
    } else {
      url = "/api/checkout"; body = {hash: h};
      ask = fmt(T.confirm_checkout_detached, {hash: h.slice(0, 7)});
    }
    if (!confirm(ask)) return;
    mbSay(T.running, "progress");
    let r = await postJson(url, body);
    if (r.blocked) {  // 編集中のファイルや管理外ファイルに、チェックアウトを止められた
      mbSay("");
      const mode = await askBlockedDialog(r, "checkout");
      if (!mode) { mbSay(T.checkout_cancelled, false); return; }
      mbSay(T.running, "progress");
      r = await postJson(url, Object.assign({}, body, {on_conflict: mode}));
    }
    mbSay(r.message, true);
    setTimeout(() => location.reload(), 800);  // 切り替わった作業ツリー / ブランチを表示に反映する
  }

  // ---- リセット (現在の HEAD を、操作の対象のコミットへ) ----
  async function doReset() {
    const h = await targetCommit();
    const v = await openDialog(T.reset + " (" + h.slice(0, 7) + ")", form => {
      field(form, T.reset_mode, selectEl("mode", [["mixed", T.reset_opt_mixed], ["soft", T.reset_opt_soft], ["hard", T.reset_opt_hard]], "mixed"), false);
    }, T.reset_submit);
    if (!v) return;
    const key = {hard: "confirm_reset_hard", soft: "confirm_reset_soft", mixed: "confirm_reset_mixed"}[v.mode];
    if (!confirm(fmt(T[key], {hash: h.slice(0, 7)}))) return;
    mbSay(T.resetting, "progress");
    try {
      const r = await postJson("/api/reset", {hash: h, mode: v.mode});
      mbSay(r.message, true);
      setTimeout(() => location.reload(), 800);
    } catch (e) {
      mbSay(fmt(T.reset_error, {error: e.message}), false);
    }
  }
  bar.querySelector('[data-cact="checkout"]').addEventListener("click", () => doCheckout().catch(fail));
  $("resetBtn").addEventListener("click", () => doReset().catch(fail));

  // ---- 終了 / 再起動 (グラフ画面と同じ確認ダイアログ) ----
  const quitModal = $("quitModal");
  async function stopServer(action) {
    quitModal.hidden = true;
    try {
      await postJson(action === "restart" ? "/api/restart" : "/api/shutdown", {});
    } catch (e) {
      mbSay(fmt(T.quit_failed, {error: e.message}), false);
      return;
    }
    document.body.textContent = action === "restart" ? T.server_restarting : T.server_stopped;
    document.body.style.padding = "24px";
    if (action === "restart") {  // サーバーが戻ったら、いまのページを読み込み直す
      const tryReload = () => fetch("/").then(() => location.reload()).catch(() => setTimeout(tryReload, 500));
      setTimeout(tryReload, 1000);
    }
  }
  $("quitBtn").addEventListener("click", () => { quitModal.hidden = false; $("quitModalQuit").focus(); });
  $("quitModalCancel").addEventListener("click", () => { quitModal.hidden = true; });
  $("quitModalQuit").addEventListener("click", () => stopServer("quit"));
  $("quitModalRestart").addEventListener("click", () => stopServer("restart"));
  const keyboardHelp = $("keyboardHelp");
  function closeKeyboardHelp() {
    keyboardHelp.hidden = true;
    $("otherMenuBtn").focus();
  }
  $("keyboardHelpBtn").addEventListener("click", () => {
    keyboardHelp.hidden = false;
    $("keyboardHelpClose").focus();
  });
  $("keyboardHelpClose").addEventListener("click", closeKeyboardHelp);
  keyboardHelp.addEventListener("click", ev => { if (ev.target === keyboardHelp) closeKeyboardHelp(); });
  keyboardHelp.addEventListener("keydown", ev => {
    if (ev.key === "Escape") {
      ev.preventDefault();
      ev.stopPropagation();
      closeKeyboardHelp();
    } else if (ev.key === "Tab") {
      ev.preventDefault();
      $("keyboardHelpClose").focus();
    }
  });
})();
