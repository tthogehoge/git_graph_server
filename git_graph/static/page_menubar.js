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
    updateRefButtons().catch(() => {});
  }

  // この画面でできない操作は、隠さず無効にする。できるかどうかは can の関数 (無ければ常にできない) で決める
  enable($("compareWipBtn"), () => worktree); enable($("setABtn")); enable($("setBBtn")); enable($("unsetABtn")); enable($("unsetBBtn")); enable($("compareBtn"));
  enable($("quitBtn")); enable($("keyboardHelpBtn")); enable($("clearCmpBtn")); enable($("swapAbBtn")); enable($("backBtn"));
  enable($("wipBtn"), () => worktree); enable($("topBtn"));
  enable($("wipCommitMenuBtn"), () => writable && worktree);
  enable(bar.querySelector('[data-cact="branch"]'), () => writable); enable(bar.querySelector('[data-cact="tag"]'), () => writable);
  enable(bar.querySelector('[data-cact="checkout"]'), () => writable && worktree); enable($("resetBtn"), () => writable && worktree);
  bar.querySelectorAll("#remoteActions [data-cact]").forEach(b => enable(b, () => writable && worktree));

  // ---- git 操作 (グラフ画面と同じ API・確認文。成功したら、ブランチ / HEAD の変化を表示に反映するため読み込み直す) ----
  // 対象 = 操作の対象のコミット、A / B = 比較の 2 つのコミット。作業ツリーが要る操作は、書き込めて作業ツリーがあるときだけ使える
  const A = () => sel.compareA, B = () => sel.compareB, short7 = h => (h || "").slice(0, 7);
  const OP_LABEL = () => ({merge: T.merge, rebase: T.rebase, cherry: T.cherry_pick, revert: T.revert});
  const reloadSoon = () => setTimeout(() => location.reload(), 800);
  const clearAB = () => { sel.compareA = sel.compareB = null; save(); refresh(); };  // A / B のコミットは、操作のあとで変わることがある
  async function opHead(needBranch, mergeKey) {  // 途中の操作 (マージ・リベースなど) があれば止める
    const hd = await getJson("/api/head");
    if (hd.merging) throw new Error(T[mergeKey || "op_in_progress_finish"]);
    if (needBranch && !hd.branch) throw new Error(T.detached_rebase);
    return hd;
  }
  // spec: {needBranch, mergeKey, ask(hd) → 確認文, busy, err, kind, url, body, clear (A/B を解除するか)}。競合で止まったら、作業ツリーの画面 (競合の解決) へ移る
  async function gitOp(spec) {
    try {
      const hd = await opHead(spec.needBranch, spec.mergeKey);
      if (!confirm(spec.ask(hd))) return;
      mbSay(T[spec.busy], "progress");
      let r = await postJson(spec.url, spec.body);
      if (r.blocked) {  // 編集中のファイルや管理外ファイルに止められた: stash / 破棄を選ばせて再実行する
        mbSay("");
        const mode = await askBlockedDialog(r, OP_LABEL()[spec.kind] || spec.kind);
        if (!mode) { mbSay(T.operation_cancelled, false); return; }
        mbSay(T[spec.busy], "progress");
        r = await postJson(spec.url, Object.assign({}, spec.body, {on_conflict: mode}));
      }
      if (spec.clear) clearAB();
      mbSay(r.message, !r.conflict);
      if (r.conflict) go(viewUrl("WIP")); else reloadSoon();
    } catch (e) { mbSay(fmt(T[spec.err], {error: e.message}), false); }
  }
  const branchOf = (hd, key) => hd.branch ? fmt(T.what_branch, {name: hd.branch}) : T[key];
  async function noFfDialog(title) {  // --no-ff を選ばせる。キャンセルなら null
    const v = await openDialog(title, form => { field(form, "--no-ff", input("checkbox", "noFf"), true).title = T.tip_no_ff; }, T.merge);
    return v ? {noFf: !!v.noFf} : null;
  }
  async function doMerge() {
    const h = target(), o = h && await noFfDialog(T.merge);
    if (!o) return;
    gitOp({mergeKey: "merge_in_progress_resolve", busy: "merging", err: "merge_error", kind: "merge", url: "/api/merge", body: {hash: h, no_ff: o.noFf},
           ask: hd => fmt(T.confirm_merge, {hash: short7(h), into: hd.branch ? fmt(T.what_branch, {name: hd.branch}) : "detached HEAD", noff: o.noFf ? " (--no-ff)" : ""})});
  }
  const doRebase = () => { const h = target(); if (h) gitOp({needBranch: true, busy: "rebasing", err: "rebase_error", kind: "rebase", url: "/api/rebase", body: {hash: h},
    ask: hd => fmt(T.confirm_rebase, {branch: hd.branch, hash: short7(h)})}); };
  const doCherry = () => { const h = target(); if (h) gitOp({busy: "cherry_picking", err: "cherry_pick_error", kind: "cherry", url: "/api/cherry-pick", body: {hash: h},
    ask: hd => fmt(T.confirm_cherry_pick, {hash: short7(h), target: branchOf(hd, "target_detached_head")})}); };
  const doRevert = () => { const h = target(); if (h) gitOp({busy: "reverting", err: "revert_error", kind: "revert", url: "/api/revert", body: {hash: h},
    ask: hd => fmt(T.confirm_revert, {hash: short7(h), target: branchOf(hd, "target_detached_head")})}); };
  // A → B の操作 (どれも A / B の両方が決まっているときだけ押せる)
  const abOp = (spec) => () => {
    const from = A(), to = B();
    if (!from || !to) return;
    gitOp(Object.assign({clear: true, body: {from: from, to: to}, ask: hd => fmt(T[spec.confirm], {branch: hd.branch, from: short7(from), to: short7(to)})}, spec));
  };
  const doRebaseRange = abOp({needBranch: true, confirm: "confirm_rebase_range", busy: "rebasing", err: "rebase_error", kind: "rebase", url: "/api/rebase-range"});
  const doRebaseOnto = abOp({confirm: "confirm_rebase_onto", busy: "rebasing", err: "rebase_error", kind: "rebase", url: "/api/rebase-onto"});
  const doCherryOnto = abOp({confirm: "confirm_cherry_onto", busy: "cherry_picking", err: "cherry_pick_error", kind: "cherry", url: "/api/cherry-pick-onto"});
  const doRebaseDescendants = abOp({needBranch: true, confirm: "confirm_rebase_descendants", busy: "rebasing", err: "rebase_error", kind: "rebase", url: "/api/rebase-descendants"});
  const doRebaseEquivalent = abOp({needBranch: true, confirm: "confirm_rebase_equivalent", busy: "rebasing", err: "rebase_error", kind: "rebase", url: "/api/rebase-equivalent"});
  async function doMergeInto() {  // A を B へマージ
    const from = A(), to = B(), o = from && to && await noFfDialog(T.merge_into);
    if (!o) return;
    gitOp({mergeKey: "merge_in_progress_resolve", busy: "merging", err: "merge_error", kind: "merge", url: "/api/merge-into", clear: true,
           body: {from: from, to: to, no_ff: o.noFf}, ask: () => fmt(T.confirm_merge_into, {from: short7(from), to: short7(to), noff: o.noFf ? " (--no-ff)" : ""})});
  }
  async function findEquivalent(url, label) {  // A / B を、同じ変更を持つコミットに置き換える (結果は A / B の選択に反映する)
    const from = A(), to = B();
    if (!from || !to) return;
    try {
      mbSay(label + "…", "progress");
      const r = await postJson(url, {from: from, to: to});
      if (r.from && r.to) sel.compareA = r.from;
      if (r.to) sel.compareB = r.to;
      save(); refresh();
      mbSay(r.message, true);
    } catch (e) { mbSay(e.message, false); }
  }
  // 対象のコミットを指しているブランチ / リモート追跡ブランチ / タグ (ブランチ・タグの操作の選択肢)。操作のあとは読み込み直すので、ページごとに 1 回だけ取る
  let labelsP = null;
  const refsOf = async h => {
    labelsP = labelsP || getJson("/api/ref-labels");
    const list = (await labelsP).labels[h] || [];
    const names = kinds => list.filter(r => kinds.includes(r.kind)).map(r => r.name).sort();
    return {local: names(["branch", "current"]), remote: names(["remote"]), tags: names(["tag"])};
  };
  async function pickName(title, label, names, submit) {  // 候補から 1 つ選ばせる。キャンセルなら null
    const v = await openDialog(title, form => { field(form, label, selectEl("name", names.map(n => [n, n]), names[0]), false); }, submit);
    return v ? v.name : null;
  }
  async function doRenameBranch() {
    const h = target(), names = h ? (await refsOf(h)).local : [];
    if (!names.length) return;
    const v = await openDialog(T.rename_branch, form => {
      const old = selectEl("old", names.map(n => [n, n]), names[0]);
      const name = input("text", "new_name"); name.value = names[0]; name.required = true;
      old.addEventListener("change", () => { name.value = old.value; });  // 選んだブランチ名を、新しい名前の初期値にする
      field(form, T.branch_to_rename, old, false);
      field(form, T.new_branch_name, name, false);
    }, T.rename);
    if (!v) return;
    const to = v.new_name.trim();
    if (!to || to === v.old) { mbSay(T.branch_rename_same, false); return; }
    mbSay(T.renaming, "progress");
    try { const r = await postJson("/api/branch-rename", {old: v.old, new: to}); mbSay(r.message, true); reloadSoon(); }
    catch (e) { mbSay(fmt(T.rename_failed, {error: e.message}), false); }
  }
  async function doDeleteRef(kind) {  // kind: branch | tag | remote-branch
    const h = target();
    if (!h) return;
    const refs = await refsOf(h), cur = (await getJson("/api/head")).branch;
    const names = kind === "tag" ? refs.tags : kind === "branch" ? refs.local.filter(n => n !== cur) : refs.remote;
    if (!names.length) return;
    const text = {branch: ["delete_branch", "branch_to_delete", "confirm_delete_branch"], tag: ["delete_tag", "tag_to_delete", "confirm_delete_tag"],
                  "remote-branch": ["delete_remote_branch", "remote_branch_to_delete", "confirm_delete_remote_branch"]}[kind];
    const name = await pickName(T[text[0]], T[text[1]], names, T.delete);
    if (!name || !confirm(fmt(T[text[2]], {name: name}))) return;
    const body = {kind: kind, name: name};
    mbSay(T.deleting, "progress");
    try {
      let r = await postJson("/api/ref-delete", body);
      if (r.needs_force) {  // マージされていないブランチ
        if (!confirm(fmt(T.confirm_force_delete, {name: name}))) { mbSay(T.force_delete_cancelled, false); return; }
        r = await postJson("/api/ref-delete", Object.assign({}, body, {force: true}));
      }
      if (r.auth_required) {  // リモートのブランチの削除は、認証を求められることがある
        const auth = await openDialog(T.http_auth, form => {
          field(form, T.auth_username, input("text", "username"), false);
          field(form, T.auth_password, input("password", "password"), false);
        }, T.authenticate);
        if (!auth) { mbSay(T.push_cancelled, false); return; }
        r = await postJson("/api/ref-delete", Object.assign({}, body, {auth: auth, auth_remote: r.auth_remote, auth_retry: true}));
      }
      mbSay(r.message, true);
      reloadSoon();
    } catch (e) { mbSay(fmt(T.delete_failed, {error: e.message}), false); }
  }
  async function doResetBranch() {  // A を先端に持つローカルブランチを B へ移動する
    const from = A(), to = B();
    if (!from || !to) return;
    const names = (await refsOf(from)).local, cur = (await getJson("/api/head")).branch;
    if (!names.length) { mbSay(fmt(T.reset_branch_no_branch, {hash: short7(from)}), false); return; }
    const v = await openDialog(T.reset_branch, form => {
      const branch = selectEl("branch", names.map(n => [n, n + (n === cur ? " (HEAD)" : "")]), names.includes(cur) ? cur : names[0]);
      const mode = selectEl("mode", [["mixed", T.reset_opt_mixed], ["soft", T.reset_opt_soft], ["hard", T.reset_opt_hard]], "mixed");
      field(form, T.reset_branch_select, branch, false);
      const modeLabel = field(form, T.reset_mode, mode, false).parentElement;
      const update = () => { modeLabel.hidden = branch.value !== cur; };  // 現在のブランチのときだけ、reset の方式を選ばせる (ほかのブランチは ref を動かすだけ)
      branch.addEventListener("change", update);
      update();
    }, T.reset_submit);
    if (!v) return;
    const isCur = v.branch === cur;
    const ask = isCur ? fmt(T[{hard: "confirm_reset_hard", soft: "confirm_reset_soft", mixed: "confirm_reset_mixed"}[v.mode]], {hash: short7(to)})
                      : fmt(T.confirm_reset_branch, {branch: v.branch, hash: short7(to)});
    if (!confirm(ask)) return;
    mbSay(T.resetting, "progress");
    try { const r = await postJson("/api/reset-branch", {from: from, to: to, branch: v.branch, mode: v.mode}); clearAB(); mbSay(r.message, true); reloadSoon(); }
    catch (e) { mbSay(fmt(T.reset_error, {error: e.message}), false); }
  }
  // 元に戻す / やり直す (操作の履歴はサーバーにある。グラフ画面と共通)
  async function doUndoRedo(dir) {
    const undo = dir === "undo";
    closeMenus();
    try {
      const h = await getJson("/api/undo-history"), label = undo ? h.next_undo : h.next_redo;
      if (!(undo ? h.can_undo : h.can_redo) || !label) { mbSay(T[undo ? "undo_nothing" : "redo_nothing"], false); return; }
      if (!confirm(fmt(T[undo ? "undo_confirm" : "redo_confirm"], {op: label}))) return;
      const busy = T[undo ? "undo_busy" : "redo_busy"];
      mbSay(busy, "progress");
      let r = await postJson("/api/" + dir, {});
      if (r.worktree_changed) {  // 操作のあとに作業ツリーが変わっていたら、上書きしてよいか確認する
        mbSay("");
        if (!confirm(fmt(T.undo_worktree_force, {msg: r.message}))) return;
        mbSay(busy, "progress");
        r = await postJson("/api/" + dir, {force: true});
      }
      mbSay(r.message, true);
      reloadSoon();
    } catch (e) { mbSay(fmt(T.undo_failed, {error: e.message}), false); }
  }
  async function updateUndoButtons() {  // 戻す先 / やり直す先が無いときは無効にする
    if (!writable) return;
    const h = await getJson("/api/undo-history"), undo = $("undoBtn"), redo = $("redoBtn");
    undo.disabled = !h.can_undo; undo.title = h.can_undo ? fmt(T.tip_undo_op, {op: h.next_undo}) : T.tip_undo;
    redo.disabled = !h.can_redo; redo.title = h.can_redo ? fmt(T.tip_redo_op, {op: h.next_redo}) : T.tip_redo;
  }
  async function updateRefButtons() {  // 対象のコミットに、操作できるブランチ / タグがあるときだけ押せる
    const h = target(), ids = ["renameBranchBtn", "deleteBranchBtn", "deleteRemoteBranchBtn", "deleteTagBtn"];
    if (!writable || !h) return;
    const [refs, hd] = await Promise.all([refsOf(h), getJson("/api/head")]);
    $("renameBranchBtn").disabled = !refs.local.length;
    $("deleteBranchBtn").disabled = !refs.local.some(n => n !== hd.branch);
    $("deleteRemoteBranchBtn").disabled = !refs.remote.length;
    $("deleteTagBtn").disabled = !refs.tags.length;
  }
  const on = (id, fn) => $(id).addEventListener("click", () => Promise.resolve(fn()).catch(fail));
  on("mergeBtn", doMerge); on("rebaseBtn", doRebase); on("cherryBtn", doCherry); on("revertBtn", doRevert);
  on("mergeIntoBtn", doMergeInto); on("cherryOntoBtn", doCherryOnto); on("rebaseOntoBtn", doRebaseOnto); on("resetBranchBtn", doResetBranch);
  on("rebaseRangeBtn", doRebaseRange); on("rebaseDescendantsBtn", doRebaseDescendants); on("rebaseEquivalentBtn", doRebaseEquivalent);
  on("findEquivalentParentsBtn", () => findEquivalent("/api/find-equivalent-parents", T.find_equivalent_parents));
  on("findEquivalentBranchBtn", () => findEquivalent("/api/find-equivalent-branch", T.find_equivalent_branch));
  on("renameBranchBtn", doRenameBranch); on("deleteBranchBtn", () => doDeleteRef("branch")); on("deleteTagBtn", () => doDeleteRef("tag"));
  on("deleteRemoteBranchBtn", () => doDeleteRef("remote-branch"));
  on("undoBtn", () => doUndoRedo("undo")); on("redoBtn", () => doUndoRedo("redo"));
  ["renameBranchBtn", "deleteBranchBtn", "deleteRemoteBranchBtn", "deleteTagBtn", "undoBtn", "redoBtn"].forEach(id => { $(id).disabled = true; });  // 状態が分かるまでは押せない
  const worktreeOps = ["mergeBtn", "rebaseBtn", "cherryBtn", "revertBtn", "mergeIntoBtn", "cherryOntoBtn", "rebaseOntoBtn", "rebaseRangeBtn", "rebaseDescendantsBtn", "rebaseEquivalentBtn"];
  worktreeOps.forEach(id => enable($(id), () => writable && worktree));
  ["resetBranchBtn", "renameBranchBtn", "deleteBranchBtn", "deleteRemoteBranchBtn", "deleteTagBtn", "undoBtn", "redoBtn"].forEach(id => enable($(id), () => writable));
  enable($("findEquivalentParentsBtn")); enable($("findEquivalentBranchBtn"));
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
  applyUnavailable();
  refresh();
  updateUndoButtons().catch(() => {});

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
