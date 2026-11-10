// /diff と /files の、左右ペインにまたがるメニューバー (#pageMenubar) の動作。
// WIP / HEAD / 対象・A・B (グラフで選んでいたコミット) への移動と、「その他」(ブランチ作成・タグ作成・再起動・終了)。
// push / pull / fetch は wip_diff_remote.js。ダイアログの部品 (openDialog など) は diff_dialog.js、文言は T。
function mbSay(text, state) {  // メニューバーのメッセージ欄。state: true = 成功 / false = 失敗 / "progress" (または省略) = 実行中・案内
  const el = document.getElementById("mbmsg");
  if (!el) return;
  el.textContent = text || "";
  el.className = state === true ? "ok" : state === false ? "ng" : "";
}
(function () {
  const bar = document.getElementById("pageMenubar");
  if (!bar) return;
  const page = bar.dataset.page;  // "files" | "diff"
  const here = bar.dataset.commit || "";  // いま表示しているコミット (作業ツリーなどは空)
  const q = new URLSearchParams(location.search);
  const go = url => { location.href = url; };
  const getJson = url => fetch(url).then(r => r.ok ? r.json() : r.text().then(m => { throw new Error(m || r.status); }));
  const fail = e => mbSay(e.message, false);

  // 指定したコミットを、いまと同じ種類のページで開く。開いているディレクトリ / ファイルはそのまま引き継ぐ (無ければページ側が案内する)
  function viewUrl(hash) {
    const p = new URLSearchParams();
    if (page === "files") {
      p.set("hash", hash);
      ["path", "file"].forEach(k => { if (q.get(k)) p.set(k, q.get(k)); });
      if (q.get("file_view")) p.set("file_view", "1");
      return "/files?" + p;
    }
    if (hash === "WIP") { p.set("wip", "unstaged"); return "/diff?" + p; }
    p.set("hash", hash);
    if (q.get("path")) p.set("path", q.get("path"));
    return "/diff?" + p;
  }
  async function headCommit() {
    const r = await getJson("/api/head");
    if (!r.head) throw new Error(T.mb_no_commit);
    return r.head;
  }

  const wipBtn = document.getElementById("mbWip"), headBtn = document.getElementById("mbHead");
  if (wipBtn) {
    const onWip = page === "files" ? q.get("hash") === "WIP" : bar.dataset.wip === "1";
    if (onWip) wipBtn.classList.add("mbhere");
    wipBtn.addEventListener("click", () => go(viewUrl("WIP")));
  }
  if (headBtn) headBtn.addEventListener("click", () => headCommit().then(h => go(viewUrl(h))).catch(fail));

  // グラフ画面で選んでいた 対象 / A / B (sessionStorage にグラフが残している) への移動
  const targets = document.getElementById("mbTargets");
  let sel = null;
  try { sel = JSON.parse(sessionStorage.getItem("git_graph_ui_sel") || "null"); } catch (e) { /* 読めなければ出さない */ }
  if (targets && sel) {
    const addTarget = (label, hash) => {
      if (!/^[0-9a-f]{7,40}$/.test(hash || "")) return;
      const b = document.createElement("button");
      b.type = "button"; b.className = "mbtn mbhash" + (hash === here ? " mbhere" : "");
      b.textContent = label + ": " + hash.slice(0, 7);
      b.title = T.tip_go_commit;
      b.addEventListener("click", () => go(viewUrl(hash)));
      targets.appendChild(b);
    };
    addTarget(T.target_label, sel.actionHash);
    addTarget("A", sel.compareA);
    addTarget("B", sel.compareB);
    if (page === "diff" && /^[0-9a-f]{7,40}$/.test(sel.compareA || "") && /^[0-9a-f]{7,40}$/.test(sel.compareB || "")) {
      const b = document.createElement("button");  // A と B の間の差分
      b.type = "button"; b.className = "mbtn"; b.textContent = T.compare_ab;
      b.addEventListener("click", () => {
        const p = new URLSearchParams({from: sel.compareA, to: sel.compareB});
        if (q.get("path")) p.set("path", q.get("path"));
        go("/diff?" + p);
      });
      targets.appendChild(b);
    }
  }

  // 「その他」メニュー
  const wrap = document.getElementById("mbOtherWrap"), toggle = document.getElementById("mbOther"), menu = document.getElementById("mbOtherMenu");
  const closeMenu = () => { menu.hidden = true; toggle.setAttribute("aria-expanded", "false"); };
  toggle.addEventListener("click", ev => {
    ev.stopPropagation();
    menu.hidden = !menu.hidden;
    toggle.setAttribute("aria-expanded", String(!menu.hidden));
  });
  document.addEventListener("click", ev => { if (!wrap.contains(ev.target)) closeMenu(); });
  document.addEventListener("keydown", ev => { if (ev.key === "Escape" && !menu.hidden) closeMenu(); });

  // ブランチ・タグを作る元のコミット: 表示中のコミット。作業ツリーなど、コミットが無い表示なら HEAD
  async function targetCommit() {
    if (here) return here;
    const h = page === "files" ? q.get("hash") : "";
    return /^[0-9a-f]{7,40}$/.test(h || "") ? h : headCommit();
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
  async function stopServer(action) {  // サーバーを再起動 / 終了する (グラフ画面の「終了」と同じ API)
    if (!confirm(action === "restart" ? T.confirm_restart_server : T.confirm_quit_server)) return;
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
  menu.addEventListener("click", ev => {
    const item = ev.target.closest("[data-mb]");
    if (!item) return;
    closeMenu();
    const act = item.dataset.mb;
    Promise.resolve().then(() => act === "branch" ? createBranch() : act === "tag" ? createTag() : stopServer(act)).catch(fail);
  });
})();
