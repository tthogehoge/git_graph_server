let cmpActOpen = false;
let abMenuHash = null;  // AB操作メニューを A/B のコミットの右クリックで開いたときの、そのコミット (ツールバーから開いたときは null)
let abActOpen = false;
function restoreMenuPosition(menu) {
  menu.classList.remove("near-pointer");
  menu.style.removeProperty("--menu-left");
  menu.style.removeProperty("--menu-top");
}
function positionMenuNearPointer(menu, ev) {
  if (!ev || (ev.detail === 0 && ev.type !== "contextmenu")) return;
  const gap = 12, edge = 8;
  menu.classList.add("near-pointer");
  menu.style.setProperty("--menu-left", (ev.clientX + gap) + "px");
  menu.style.setProperty("--menu-top", (ev.clientY + gap) + "px");
  const rect = menu.getBoundingClientRect();
  let left = ev.clientX + gap, top = ev.clientY + gap;
  if (left + rect.width > window.innerWidth - edge) left = ev.clientX - rect.width - gap;
  if (top + rect.height > window.innerHeight - edge) top = ev.clientY - rect.height - gap;
  left = Math.max(edge, Math.min(left, window.innerWidth - rect.width - edge));
  top = Math.max(edge, Math.min(top, window.innerHeight - rect.height - edge));
  menu.style.setProperty("--menu-left", left + "px");
  menu.style.setProperty("--menu-top", top + "px");
}
function setOtherMenu(open) {  // 「その他」のメニュー
  const btn = document.getElementById("otherMenuBtn");
  document.getElementById("otherActions").hidden = !open;
  btn.setAttribute("aria-expanded", open ? "true" : "false");
  btn.textContent = open ? t("other").replace("▾", "▴") : t("other");
}
function closeMenus() {  // 開いているメニュー (操作 / AB操作 / その他) をすべて閉じる
  const changed = cmpActOpen || abActOpen || !document.getElementById("otherActions").hidden;
  cmpActOpen = false;
  abActOpen = false;
  abMenuHash = null;
  setOtherMenu(false);
  if (changed) updateCmpClasses();
}
function clearSelection() {  // 選択 (A / B / 操作対象 / グラフのノード / ログの行) をすべて解除する
  clearCmp();
  if (selected) { selected.classList.remove("sel"); selected = null; selectedKey = null; }
  if (curRow) { curRow.classList.remove("cur"); curRow = null; }
  closeMenus();
}
let stashByHash = {};  // stash のコミットのハッシュ -> stash@{n}
function loadStashes() {
  return fetch("/api/stash-list").then(r => r.ok ? r.json() : null).then(r => {
    if (!r) return;
    stashByHash = {};
    r.commits.forEach(c => { stashByHash[c[0]] = c[2]; });
    updateCmpClasses();
  }).catch(() => {});
}
// 編集中のファイルや同名の管理外ファイルに操作を止められたとき、止めているファイルを見せて、stash / 破棄 / キャンセルを選んでもらう。
// kind: "checkout" (既定) / "pull" / "merge" / "rebase" / "cherry" / "revert" / "stash" (stash の適用: stash は選べず、破棄して上書きして適用するだけ)
function askCheckoutConflict(r, kind) {
  kind = kind || "checkout";
  return new Promise(resolve => {
    const m = document.getElementById("coModal");
    if (kind === "checkout") {
      document.getElementById("coTitle").textContent = t("checkout_blocked");
      document.getElementById("coSub").textContent = t("checkout_blocked_hint");
      document.getElementById("coHint").innerHTML = esc(t("co_hint_stash")) + "<br>" + esc(t("co_hint_discard"));
      document.getElementById("coStashBtn").textContent = t("stash_checkout");
      document.getElementById("coDiscardBtn").textContent = t("discard_checkout");
    } else {
      const op = {pull: "pull", merge: t("merge"), stash: t("stash_apply"),
                  rebase: t("rebase"), cherry: t("cherry_pick"), revert: t("revert")}[kind] || kind;
      document.getElementById("coTitle").textContent = t("co_blocked_op", {op: op});
      document.getElementById("coSub").textContent = t("co_blocked_hint_op");
      document.getElementById("coHint").innerHTML = kind === "stash" ? esc(t("co_hint_force_apply"))
        : esc(t("co_hint_stash")) + "<br>" + esc(t("co_hint_discard"));
      document.getElementById("coStashBtn").textContent = t("co_stash_retry");
      document.getElementById("coDiscardBtn").textContent = kind === "stash" ? t("co_force_apply") : t("co_discard_retry");
    }
    document.getElementById("coStashBtn").hidden = kind === "stash";  // stash の適用では、さらに stash を積んでも解決しない
    const list = fs => "<ul>" + fs.map(f => "<li>" + esc(f) + "</li>").join("") + "</ul>";
    let html = "";
    if (r.modified.length) html += "<h3>" + esc(t("co_modified")) + "</h3>" + list(r.modified);
    if (r.untracked.length) html += "<h3>" + esc(t(kind === "checkout" ? "co_untracked" : "co_untracked_op")) + "</h3>" + list(r.untracked);
    document.getElementById("coFiles").innerHTML = html;
    const done = v => { m.hidden = true; m.onclick = null; resolve(v); };
    m.hidden = false;
    m.onclick = ev => {
      const b = ev.target.closest("[data-co]");
      if (b) done(b.dataset.co === "cancel" ? null : b.dataset.co);
      else if (ev.target === m) done(null);
    };
  });
}
function openActionDialog(title, fields, submitLabel, opts) {  // opts: {wide, validate(values) -> エラー文 or null}
  opts = opts || {};
  return new Promise(resolve => {
    const modal = document.getElementById("actionModal"), form = document.getElementById("actionForm");
    form.className = "action-dialog" + (opts.wide ? " wide" : "");
    document.getElementById("actionError").textContent = "";
    let cleanup = null;
    const finish = value => {
      if (cleanup) cleanup();
      modal.hidden = true;
      form.onsubmit = null;
      modal.onclick = null;
      window.removeEventListener("keydown", onKeyDown);
      resolve(value);
    };
    const onKeyDown = ev => { if (ev.key === "Escape") finish(null); };
    document.getElementById("actionTitle").textContent = title;
    document.getElementById("actionFields").innerHTML = fields;
    document.getElementById("actionSubmit").textContent = submitLabel;
    modal.hidden = false;
    if (opts.onOpen) cleanup = opts.onOpen(form) || null;
    form.onsubmit = ev => {
      ev.preventDefault();
      const values = Object.fromEntries(new FormData(form).entries());
      form.querySelectorAll('input[type="checkbox"][name]').forEach(input => { values[input.name] = input.checked; });
      if (opts.validate) {
        const err = opts.validate(values);
        document.getElementById("actionError").textContent = err || "";
        if (err) return;
      }
      finish(values);
    };
    modal.onclick = ev => {
      if (ev.target === modal || ev.target.closest("[data-dialog-cancel]")) finish(null);
    };
    window.addEventListener("keydown", onKeyDown);
    const first = form.querySelector("input, select");
    if (first) first.focus();
  });
}
function retryIfBlocked(r, kind, progressMsg, retry) {  // 手元の変更に邪魔されたら stash / 破棄を選ばせて再実行する
  if (!r || !r.blocked) return Promise.resolve(r);
  setGraphStatus("");
  return askCheckoutConflict(r, kind).then(mode => {
    if (!mode) return null;
    setGraphStatus(progressMsg, "progress");
    return retry(mode);
  });
}
function setCmpMsg(text, state) {  // state: true = 成功 / false = 失敗 / "progress" = 実行中
  setGraphStatus(text, state === "progress" ? "progress" : state ? "success" : "error", state === false);
}
function setRemoteMsg(text, state) {
  setCmpMsg(text, state);
}
async function doCmpAction(btn) {  // 選択対象に対するブランチ作成 / タグ作成 / チェックアウト
  if (!actionHash) return;
  const h = actionHash, act = btn.dataset.cact;
  let values, url, body, ask = null;
  if (act === "branch") {
    values = await openActionDialog(t("create_branch"), '<label>' + esc(t("branch_name")) + '<input type="text" name="name" required autofocus></label>' +
      '<label class="check"><input type="checkbox" name="checkout"> ' + esc(t("checkout_after_create")) + '</label>', t("create"));
    if (!values) return;
    url = "/api/branch"; body = {hash: h, name: values.name.trim(), checkout: values.checkout};
    if (!body.name) { setCmpMsg(t("branch_name_required"), false); return; }
    if (body.checkout) ask = t("confirm_create_checkout", {name: body.name});
  } else if (act === "tag") {
    values = await openActionDialog(t("create_tag"), '<label>' + esc(t("tag_name")) + '<input type="text" name="name" required autofocus></label>' +
      '<label>' + esc(t("message_optional")) + '<input type="text" name="message"></label>', t("create"));
    if (!values) return;
    url = "/api/tag"; body = {hash: h, name: values.name.trim(), message: values.message.trim()};
    if (!body.name) { setCmpMsg(t("tag_name_required"), false); return; }
  } else if (act === "checkout") {
    const branches = localBranches(h);
    const remoteRefs = remoteBranches(h).sort();
    const defaultRemoteRef = remoteRefs[0] || "";
    const defaultNewBranch = defaultNewBranchName(h);
    values = await openActionDialog(t("checkout"), '<label>' + esc(t("switch_to")) + '<select name="branch">' +
      branches.map(branch => '<option value="' + esc(branch) + '">' + esc(branch) + '</option>').join("") +
      '<option value="">' + esc(t("checkout_detached_option")) + '</option>' +
      '<option value="__new_branch__">' + esc(t("checkout_new_branch_option")) + '</option>' +
      '</select></label>' +
      '<label id="newBranchField" hidden>' + esc(t("new_branch_name")) + '<input type="text" name="newBranch" value="' + esc(defaultNewBranch) + '" placeholder="' + esc(t("new_branch_placeholder")) + '"></label>' +
      '<label id="trackUpstreamField" class="check" hidden><input type="checkbox" name="trackUpstream"' + (defaultRemoteRef ? ' checked' : '') + '> ' + esc(t("track_upstream")) + (defaultRemoteRef ? ' (' + esc(defaultRemoteRef) + ')' : '') + '</label>', t("checkout"), {
        onOpen: form => {
          const select = form.elements.branch, field = form.querySelector("#newBranchField"), track = form.querySelector("#trackUpstreamField");
          const update = () => { field.hidden = select.value !== "__new_branch__"; track.hidden = select.value !== "__new_branch__" || !defaultRemoteRef; };
          select.addEventListener("change", update);
          update();
          return () => select.removeEventListener("change", update);
        },
        validate: values => values.branch === "__new_branch__" && !values.newBranch.trim() ?
          t("new_branch_name_required") : null
      });
    if (!values) return;
    if (values.branch === "__new_branch__") {
      const name = values.newBranch.trim();
      url = "/api/branch"; body = {hash: h, name: name, checkout: true};
      if (values.trackUpstream && defaultRemoteRef) body.upstream = defaultRemoteRef;
      ask = t("confirm_create_checkout", {name: name});
    } else if (values.branch) {
      url = "/api/checkout"; body = {branch: values.branch};
      ask = t("confirm_checkout_branch", {branch: values.branch});
    } else {
      url = "/api/checkout"; body = {hash: h};
      ask = t("confirm_checkout_detached", {hash: h.slice(0, 7)});
    }
  } else return;
  if (ask && !confirm(ask)) return;
  btn.disabled = true; setCmpMsg(t("running"), "progress");
  const run = extra => post(url, Object.assign({}, body, extra || {}));
  run()
    .then(r => {
      if (!r.blocked) return r;
      setCmpMsg("", true);
      return askCheckoutConflict(r).then(mode => {
        if (!mode) return {cancelled: true, message: t("checkout_cancelled")};
        setCmpMsg(t("running"), "progress");
        return run({on_conflict: mode});
      });
    })
    .then(r => {
      if (r.cancelled) { setCmpMsg(r.message, false); return; }
      setCmpMsg(r.message, true);
      return refreshGraph().catch(() => {});
    })
    .catch(e => setCmpMsg(e.message, false))
    .finally(() => { btn.disabled = false; });
}
async function doDeleteBranch(btn) {
  const branches = localBranches(actionHash).filter(name => name !== currentBranch).sort();
  if (!branches.length) return;
  const values = await openActionDialog(t("delete_branch"),
    '<label>' + esc(t("branch_to_delete")) + '<select name="name">' + branches.map(branch =>
      '<option value="' + esc(branch) + '">' + esc(branch) + "</option>").join("") +
    "</select></label>", t("delete"));
  if (!values) return;
  const name = values.name;
  if (!confirm(t("confirm_delete_branch", {name: name}))) return;
  const body = {kind: "branch", name: name};
  btn.disabled = true; setCmpMsg(t("deleting"), "progress");
  post("/api/ref-delete", body)
    .then(r => {
      if (!r.needs_force) return r;
      if (!confirm(t("confirm_force_delete", {name: name}))) return null;
      return post("/api/ref-delete", Object.assign({}, body, {force: true}));
    })
    .then(r => {
      if (r === null) { setCmpMsg(t("force_delete_cancelled"), false); return; }
      setCmpMsg(r.message, true);
      return refreshGraph().catch(() => {});
    })
    .catch(e => setCmpMsg(t("delete_failed", {error: e.message}), false))
    .finally(() => { btn.disabled = false; });
}
async function doDeleteTag(btn) {
  const tags = localTags(actionHash).sort();
  if (!tags.length) return;
  const values = await openActionDialog(t("delete_tag"),
    '<label>' + esc(t("tag_to_delete")) + '<select name="name">' + tags.map(tag =>
      '<option value="' + esc(tag) + '">' + esc(tag) + "</option>").join("") +
    "</select></label>", t("delete"));
  if (!values) return;
  const name = values.name;
  if (!confirm(t("confirm_delete_tag", {name: name}))) return;
  btn.disabled = true; setCmpMsg(t("deleting"), "progress");
  post("/api/ref-delete", {kind: "tag", name: name})
    .then(r => { setCmpMsg(r.message, true); return refreshGraph().catch(() => {}); })
    .catch(e => setCmpMsg(t("delete_failed", {error: e.message}), false))
    .finally(() => { btn.disabled = false; });
}
async function doDeleteRemoteBranch(btn) {
  const branches = remoteBranches(actionHash).sort();
  if (!branches.length) return;
  const values = await openActionDialog(t("delete_remote_branch"),
    '<label>' + esc(t("remote_branch_to_delete")) + '<select name="name">' + branches.map(branch =>
      '<option value="' + esc(branch) + '">' + esc(branch) + "</option>").join("") +
    "</select></label>", t("delete"));
  if (!values) return;
  const name = values.name;
  if (!confirm(t("confirm_delete_remote_branch", {name: name}))) return;
  const body = {kind: "remote-branch", name: name};
  btn.disabled = true; setCmpMsg(t("deleting"), "progress");
  const run = extra => post("/api/ref-delete", Object.assign({}, body, extra || {}));
  try {
    let r = await run();
    if (r.auth_required) {
      const auth = await openActionDialog(t("http_auth"),
        '<label>' + esc(t("auth_username")) + '<input name="username" autocomplete="username" autofocus></label>' +
        '<label>' + esc(t("auth_password")) + '<input type="password" name="password" autocomplete="current-password"></label>',
        t("authenticate"));
      if (!auth) { setCmpMsg(t("push_cancelled"), false); return; }
      r = await run({auth: auth, auth_remote: r.auth_remote, auth_retry: true});
    }
    setCmpMsg(r.message, true);
    await refreshGraph().catch(() => {});
  } catch (e) {
    setCmpMsg(e.message, false);
  } finally {
    btn.disabled = false;
  }
}
async function doRemoteAction(btn) {  // fetch / pull / push
  const act = btn.dataset.cact;
  const url = "/api/" + act;
  let body = {}, ask = null;
  if (act === "fetch") {
    const values = await openActionDialog("fetch", '<label class="check"><input type="checkbox" name="prune"> ' + esc(t("prune_label")) + '</label>', "fetch");
    if (!values) return;
    body = {prune: values.prune};
  } else if (act === "pull") {
    const values = await openActionDialog("pull", '<label>' + esc(t("pull_mode")) + '<select name="mode">' +
      '<option value="ff-only">' + esc(t("pull_opt_ff")) + '</option>' +
      '<option value="merge">' + esc(t("pull_opt_merge")) + '</option>' +
      '<option value="rebase">' + esc(t("pull_opt_rebase")) + '</option></select></label>', "pull");
    if (!values) return;
    const mode = values.mode;
    body = {mode: mode};
    ask = t("confirm_pull", {mode: {"ff-only": t("pull_mode_ff"), "merge": t("pull_mode_merge"), "rebase": t("pull_mode_rebase")}[mode]});
  } else if (act === "push") {
    const branches = [...new Set([currentBranch, ...localBranches(actionHash)])]
      .filter(Boolean).sort((a, b) => a.localeCompare(b));
    if (!branches.length) return;
    // 操作メニューの push は選択中のコミットのブランチ、メニューバーの push は HEAD (現在のブランチ) を既定にする
    const selected = btn.dataset.pushSel ? localBranches(actionHash).sort((a, b) => a.localeCompare(b)) : [];
    const defaultBranch = selected.length ? (selected.includes(currentBranch) ? currentBranch : selected[0]) : (currentBranch || branches[0]);
    const remoteInfo = await api("/api/remotes", {});
    const remotes = remoteInfo.remotes || [];
    if (!remotes.length) { setRemoteMsg(t("no_remotes"), false); return; }
    const defaultRemote = remotes.some(r => r.name === "origin") ? "origin" : remotes[0].name;
    const values = await openActionDialog("push",
      '<label>' + esc(t("push_branch_select")) + '<select name="branch">' + branches.map(branch =>
        '<option value="' + esc(branch) + '"' + (branch === defaultBranch ? " selected" : "") + '>' + esc(branch) + (branch === currentBranch ? " (HEAD)" : "") + "</option>").join("") +
      '</select></label>' +
      '<label>' + esc(t("push_remote_select")) + '<select name="remote">' + remotes.map(r =>
        '<option value="' + esc(r.name) + '"' + (r.name === defaultRemote ? " selected" : "") + '>' + esc(r.name) + "</option>").join("") +
      '</select></label>' +
      '<label>' + esc(t("push_remote_branch")) + '<input name="remoteBranch" value="' + esc(defaultBranch) + '" required></label>' +
      '<label class="check"><input type="checkbox" name="pushTags"> ' + esc(t("push_tags_label")) + '</label>' +
      '<label class="check"><input type="checkbox" name="force"> ' + esc(t("force_label")) + '</label>', "push", {
        onOpen: form => {
          const branch = form.elements.branch, remoteBranch = form.elements.remoteBranch;
          const update = () => { remoteBranch.value = branch.value; };
          branch.addEventListener("change", update);
          return () => branch.removeEventListener("change", update);
        }
      });
    if (!values) return;
    body = {branch: values.branch, remote: values.remote, remote_branch: values.remoteBranch.trim(), force: values.force, push_tags: !!values.pushTags};
    const what = t("what_branch", {name: values.branch}) + " → " + values.remote;
    ask = body.force
      ? t("confirm_push_force", {what: what})
      : t("confirm_push", {what: what});
  } else if (act === "push-tag") {
    body = {tag: btn.dataset.tag};
    ask = t("confirm_push_tag", {tag: body.tag});
  } else return;
  if (ask && !confirm(ask)) return;
  btn.disabled = true; setRemoteMsg(t("running_remote"), "progress");
  let p = post(url, body).then(async r => {
    if (!r.auth_required) return r;
    const auth = await openActionDialog(t("http_auth"),
      '<label>' + esc(t("auth_username")) + '<input name="username" autocomplete="username" autofocus></label>' +
      '<label>' + esc(t("auth_password")) + '<input type="password" name="password" autocomplete="current-password"></label>',
      t("authenticate"));
    if (!auth) return {cancelled: true, message: t("push_cancelled")};
    setRemoteMsg(t("running_remote"), "progress");
    return post(url, Object.assign({}, body, {auth: auth, auth_remote: r.auth_remote, auth_retry: true}));
  });
  if (act === "pull") {  // 編集中のファイルや管理外ファイルに邪魔されたら、stash / 破棄を選ばせて再実行する
    p = p.then(async r => {
      if (!r.blocked) return r;
      setRemoteMsg("", true);
      const mode = await askCheckoutConflict(r, "pull");
      if (!mode) return {cancelled: true, message: t("operation_cancelled")};
      setRemoteMsg(t("running_remote"), "progress");
      return post(url, Object.assign({}, body, {on_conflict: mode}));
    });
  }
  if (act === "push-tag") {
    p = p.then(r => {
      if (!r.needs_remote) return r;
      const input = prompt(t("prompt_push_tag_remote", {tag: r.tag, remotes: r.remotes.join(" / ")}), r.remote);
      if (input === null) return {cancelled: true, message: t("push_cancelled")};
      const remote = input.trim();
      if (r.remotes.indexOf(remote) < 0) throw new Error(t("remote_unregistered", {remote: remote}));
      setRemoteMsg(t("running_remote"), "progress");
      return post(url, Object.assign({}, body, {remote: remote}));
    });
  }
  if (act === "push") {
    p = p.then(r => {
      if (!r.needs_upstream) return r;
      const cancelled = {cancelled: true, message: t("push_cancelled")};
      let remote = r.remote;
      if (r.remotes && r.remotes.length > 1) {  // リモートが複数: push 先を選ばせる
        const input = prompt(t("prompt_push_remote", {branch: r.branch, remotes: r.remotes.join(" / ")}), r.remote);
        if (input === null) return cancelled;
        remote = input.trim();
        if (r.remotes.indexOf(remote) < 0) throw new Error(t("remote_unregistered", {remote: remote}));
      } else if (!confirm(t("confirm_set_upstream", {branch: r.branch, remote: r.remote}))) return cancelled;
      setRemoteMsg(t("running_remote"), "progress");
      return post(url, Object.assign({}, body, {set_upstream: true, remote: remote}));
    });
  }
  p.then(r => {
    if (r.cancelled) { setRemoteMsg(r.message, false); return; }
    if (act === "push") {
      // git push 自体の完了を先に通知し、グラフ更新は別の進捗表示で行う。
      setRemoteMsg(r.message, true);
      return refreshGraph().catch(() => {});
    }
    setRemoteMsg(r.message, r.ok !== false && !r.fetch_failed);
    return refreshGraph().catch(() => {}).then(() => {
      if (r.conflict) { clearCmp(); showWip(); }
    });
  })
    .catch(e => setRemoteMsg(e.message, false))
    .finally(() => { btn.disabled = false; });
}
async function doRemoteConfig(btn) {  // remote 追加 / 削除
  let url, body, ask = null;
  if (btn.dataset.cact === "remote-add") {
    const values = await openActionDialog(t("remote_add"),
      '<label>' + esc(t("name")) + '<input type="text" name="name" placeholder="origin" required autofocus></label>' +
      '<label>URL<input type="text" name="url" required></label>' +
      '<label class="check"><input type="checkbox" name="fetch" checked> ' + esc(t("fetch_after_add")) + '</label>', t("add"));
    if (!values) return;
    body = {name: values.name.trim(), url: values.url.trim(), fetch: !!values.fetch};
    if (!body.name || !body.url) { setRemoteMsg(t("name_url_required"), false); return; }
    url = "/api/remote-add";
  } else {
    let list;
    try { list = (await api("/api/remotes", {})).remotes; } catch (e) { setRemoteMsg(e.message, false); return; }
    if (!list.length) { setRemoteMsg(t("no_remotes"), false); return; }
    const values = await openActionDialog(t("remote_remove"),
      '<label>' + esc(t("remote_to_remove")) + '<select name="name">' + list.map(r =>
        '<option value="' + esc(r.name) + '">' + esc(r.name) + (r.url ? " — " + esc(r.url) : "") + "</option>").join("") +
      "</select></label>", t("delete"));
    if (!values) return;
    body = {name: values.name};
    url = "/api/remote-remove";
    ask = t("confirm_remove_remote", {name: body.name});
  }
  if (ask && !confirm(ask)) return;
  btn.disabled = true;
  setRemoteMsg(body.fetch ? t("running_fetch") : t("running"), "progress");
  post(url, body)
    .then(r => { setRemoteMsg(r.message, r.ok !== false && !r.fetch_failed); return refreshGraph().catch(() => {}); })
    .catch(e => setRemoteMsg(e.message, false))
    .finally(() => { btn.disabled = false; });
}
function clearCmp() {
  compareAHash = null; compareBHash = null; actionHash = null;
  updateCmpClasses();
  saveUiState();
}
function selectActionTarget(hash, reveal = true) { actionHash = hash; updateCmpClasses(); saveUiState(); if (reveal) revealHash(hash); else clearLocate(); }
