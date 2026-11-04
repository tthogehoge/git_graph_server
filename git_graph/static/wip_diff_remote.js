// WIP の差分ページの push / pull / fetch ボタン (グラフ画面のメニューバーと同じ操作)。
// ダイアログの部品 (openDialog / field / input / postJson / fmt) は diff_dialog.js、文言は T。
(function () {
  const bar = document.getElementById("sideRemote");
  if (!bar) return;
  const msg = document.getElementById("rmsg");
  const say = (text, state) => {  // state: true = 成功 / false = 失敗 / "progress" = 実行中
    msg.textContent = text;
    msg.style.color = state === "progress" ? "" : state ? "#1a7f37" : "#cf222e";
  };
  const select = (name, options, selected) => {
    const s = document.createElement("select"); s.name = name;
    options.forEach(([value, text]) => {
      const o = document.createElement("option"); o.value = value; o.textContent = text;
      if (value === selected) o.selected = true;
      s.appendChild(o);
    });
    return s;
  };
  const list = (form, title, files) => {
    if (!files || !files.length) return;
    const h = document.createElement("h3"), ul = document.createElement("ul");
    h.textContent = title;
    files.forEach(f => { const li = document.createElement("li"); li.textContent = f; ul.appendChild(li); });
    form.append(h, ul);
  };
  const hint = (form, text) => { const p = document.createElement("p"); p.className = "hint"; p.textContent = text; form.appendChild(p); };
  // 編集中のファイルや管理外ファイルに pull を止められたとき、stash / 破棄を選んでもらう (null = 取りやめ)
  async function askBlocked(r) {
    const v = await openDialog(fmt(T.co_blocked_op, {op: "pull"}), form => {
      hint(form, T.co_blocked_hint_op);
      list(form, T.co_modified, r.modified);
      list(form, T.co_untracked_op, r.untracked);
      hint(form, T.co_hint_stash + "\n" + T.co_hint_discard);
      form.appendChild(select("mode", [["stash", T.co_stash_retry], ["discard", T.co_discard_retry]], "stash"));
    }, T.ok);
    return v ? v.mode : null;
  }
  async function askAuth() {
    return openDialog(T.http_auth, form => {
      field(form, T.auth_username, input("text", "username"), false);
      const pw = input("password", "password"); pw.autocomplete = "current-password";
      field(form, T.auth_password, pw, false);
    }, T.authenticate);
  }
  // 実行前の入力。取りやめは null、続けるなら {body, ask}
  async function prepare(act) {
    if (act === "fetch") {
      const v = await openDialog("fetch", form => field(form, T.prune_label, input("checkbox", "prune"), true), "fetch");
      return v && {body: {prune: !!v.prune}, ask: null};
    }
    if (act === "pull") {
      const v = await openDialog("pull", form => field(form, T.pull_mode, select("mode", [
        ["ff-only", T.pull_opt_ff], ["merge", T.pull_opt_merge], ["rebase", T.pull_opt_rebase]], "ff-only"), false), "pull");
      if (!v) return null;
      const names = {"ff-only": T.pull_mode_ff, "merge": T.pull_mode_merge, "rebase": T.pull_mode_rebase};
      return {body: {mode: v.mode}, ask: fmt(T.confirm_pull, {mode: names[v.mode]})};
    }
    const info = await fetch("/api/push-targets").then(r => r.ok ? r.json() : r.text().then(m => { throw new Error(m || r.status); }));
    if (!info.remotes.length) { say(T.no_remotes, false); return null; }
    if (!info.branches.length) { say(T.push_no_commits, false); return null; }
    const def = info.branches.includes(info.current) ? info.current : info.branches[0];
    const defRemote = info.remotes.some(r => r.name === "origin") ? "origin" : info.remotes[0].name;
    const v = await openDialog("push", form => {
      const branch = field(form, T.push_branch_select, select("branch",
        info.branches.map(b => [b, b + (b === info.current ? " (HEAD)" : "")]), def), false);
      field(form, T.push_remote_select, select("remote", info.remotes.map(r => [r.name, r.name]), defRemote), false);
      const rb = field(form, T.push_remote_branch, input("text", "remoteBranch"), false);
      rb.value = def; rb.required = true;
      branch.addEventListener("change", () => { rb.value = branch.value; });
      field(form, T.push_tags_label, input("checkbox", "pushTags"), true);
      field(form, T.force_label, input("checkbox", "force"), true);
    }, "push");
    if (!v) return null;
    const body = {branch: v.branch, remote: v.remote, remote_branch: (v.remoteBranch || "").trim() || v.branch, force: !!v.force, push_tags: !!v.pushTags};
    const what = fmt(T.what_branch, {name: v.branch}) + " → " + v.remote;
    return {body: body, ask: fmt(body.force ? T.confirm_push_force : T.confirm_push, {what: what})};
  }
  async function run(act, body) {
    const url = "/api/" + act, wait = () => say(T.running_remote, "progress");
    let r = await postJson(url, body);
    if (r.auth_required) {
      const auth = await askAuth();
      if (!auth) return {cancelled: true, message: T.push_cancelled};
      wait();
      r = await postJson(url, Object.assign({}, body, {auth: auth, auth_remote: r.auth_remote, auth_retry: true}));
    }
    if (act === "pull" && r.blocked) {
      say("", true);
      const mode = await askBlocked(r);
      if (!mode) return {cancelled: true, message: T.operation_cancelled};
      wait();
      r = await postJson(url, Object.assign({}, body, {on_conflict: mode}));
    }
    if (act === "push" && r.needs_upstream) {  // upstream が無いブランチ: push 先を決めて設定する
      const cancelled = {cancelled: true, message: T.push_cancelled};
      let remote = r.remote;
      if (r.remotes && r.remotes.length > 1) {
        const answer = prompt(fmt(T.prompt_push_remote, {branch: r.branch, remotes: r.remotes.join(" / ")}), r.remote);
        if (answer === null) return cancelled;
        remote = answer.trim();
        if (r.remotes.indexOf(remote) < 0) throw new Error(fmt(T.remote_unregistered, {remote: remote}));
      } else if (!confirm(fmt(T.confirm_set_upstream, {branch: r.branch, remote: r.remote}))) return cancelled;
      wait();
      r = await postJson(url, Object.assign({}, body, {set_upstream: true, remote: remote}));
    }
    return r;
  }
  bar.querySelectorAll("[data-remote]").forEach(btn => btn.addEventListener("click", async () => {
    const act = btn.dataset.remote;
    try {
      const p = await prepare(act);
      if (!p) return;
      if (p.ask && !confirm(p.ask)) return;
      bar.querySelectorAll("button").forEach(b => { b.disabled = true; });
      say(T.running_remote, "progress");
      const r = await run(act, p.body);
      if (r.cancelled) { say(r.message, false); return; }
      say(r.message, r.ok !== false && !r.fetch_failed);
      if (act === "pull") setTimeout(() => location.reload(), 800);  // 取り込んだ変更 (競合も) を一覧に反映する
    } catch (e) {
      say(e.message, false);
    } finally {
      bar.querySelectorAll("button").forEach(b => { b.disabled = false; });
    }
  }));
})();
