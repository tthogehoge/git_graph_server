
const cks = () => [...document.querySelectorAll(".wck")];
function paths() { return [...new Set(cks().filter(x => x.checked).map(x => x.dataset.path))]; }
// ステージ済みと未ステージは別々にチェックできる。ステージ済みだけチェックしたファイルは、ステージ済みの内容だけをコミットする
function pathsOf(staged) { return [...new Set(cks().filter(x => x.checked && (x.dataset.kind === "staged") === staged).map(x => x.dataset.path))]; }
function sync() {
  const n = paths().length, b = document.getElementById("cbtn");
  if (b) {
    b.disabled = n === 0;
    b.textContent = T.wip_commit + " (" + n + ")";
  }
  const rm = document.getElementById("rbtn");
  if (rm) {
    rm.disabled = n === 0;
    rm.textContent = T.wip_rm + " (" + n + ")";
  }
  const discard = document.getElementById("dbtn");
  if (discard) {
    discard.disabled = n === 0;
    discard.textContent = T.wip_discard + " (" + n + ")";
  }
  document.querySelectorAll(".gall").forEach(g => {
    const c = cks().filter(x => x.dataset.kind === g.dataset.kind), k = c.filter(x => x.checked).length;
    g.checked = c.length > 0 && k === c.length;
    g.indeterminate = k > 0 && k < c.length;
  });
  document.querySelectorAll(".dck").forEach(g => {
    const c = cks().filter(x => x.dataset.kind === g.dataset.kind && x.dataset.dir === g.dataset.dir), k = c.filter(x => x.checked).length;
    g.checked = c.length > 0 && k === c.length;
    g.indeterminate = k > 0 && k < c.length;
  });
}
document.addEventListener("change", ev => {
  const g = ev.target.closest(".gall"), d = ev.target.closest(".dck");
  if (g) cks().filter(x => x.dataset.kind === g.dataset.kind).forEach(x => { x.checked = g.checked; });
  else if (d) cks().filter(x => x.dataset.kind === d.dataset.kind && x.dataset.dir === d.dataset.dir)
    .forEach(x => { x.checked = d.checked; });
  else if (!ev.target.closest(".wck")) return;
  sync();
});
const cbtn = document.getElementById("cbtn");
if (cbtn) cbtn.addEventListener("click", () => {
  const out = document.getElementById("cout"), msg = document.getElementById("cmsg").value;
  const say = (text, ok) => { out.textContent = text; out.style.color = ok ? "#1a7f37" : "#cf222e"; };
  if (!msg.trim()) { say(T.message_required, false); return; }
  if (!paths().length) { say(T.select_files, false); return; }
  const branch = document.getElementById("cbranch").value.trim();
  cbtn.disabled = true; say(T.wip_execute, true);
  fetch("/api/wip-commit", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({paths: pathsOf(false), staged_paths: pathsOf(true), message: msg, amend: false, branch: branch})})
    .then(r => r.ok ? r.json() : r.text().then(m => { throw new Error(m || r.status); }))
    .then(r => { say(r.message, true); setTimeout(() => location.reload(), 800); })
    .catch(e => { say(e.message, false); sync(); });
});
const dbtn = document.getElementById("dbtn");
if (dbtn) dbtn.addEventListener("click", () => {
  const selected = cks().filter(x => x.checked), selectedPaths = paths();
  if (!selectedPaths.length) return;
  const conflicts = [...new Set(selected.filter(x => x.dataset.kind === "conflict").map(x => x.dataset.path))];
  const out = document.getElementById("cout"), say = (text, ok) => {
    out.textContent = text; out.style.color = ok ? "#1a7f37" : "#cf222e";
  };
  if (conflicts.length) { say(T.discard_conflict.replace("{files}", conflicts.slice(0, 3).join(", ")), false); return; }
  if (!confirm(T.confirm_discard.replace("{n}", selectedPaths.length))) return;
  dbtn.disabled = true; say(T.wip_execute, true);
  fetch("/api/discard", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({paths: selectedPaths})})
    .then(r => r.ok ? r.json() : r.text().then(m => { throw new Error(m || r.status); }))
    .then(r => { say(r.message, true); setTimeout(() => location.reload(), 800); })
    .catch(e => { say(e.message, false); sync(); });
});
// ---- git rm / stash / 競合の解決 / 中断 (右ペインの WIP と同じ操作) ----
const fmt = (text, values) => Object.entries(values || {}).reduce((s, [k, v]) => s.split("{" + k + "}").join(String(v)), text);
const outEl = () => document.getElementById("cout");
function say(text, ok) { const out = outEl(); out.textContent = text; out.style.color = ok ? "#1a7f37" : "#cf222e"; }
function postJson(url, body) {
  return fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)})
    .then(r => r.ok ? r.json() : r.text().then(m => { throw new Error(m || r.status); }));
}
function done(r) { say(r.message, true); setTimeout(() => location.reload(), 800); }
function openDialog(title, build, submitLabel) {  // build(form): 入力欄を作る。送信で {name: 値} を返し、キャンセルで null
  return new Promise(resolve => {
    const bg = document.createElement("div"), form = document.createElement("form");
    bg.id = "dlgBg"; form.id = "dlg";
    const h = document.createElement("h2"); h.textContent = title; form.appendChild(h);
    build(form);
    const btns = document.createElement("div"); btns.className = "dbtns";
    const cancel = document.createElement("button"), ok = document.createElement("button");
    cancel.type = "button"; cancel.className = "wbtn"; cancel.textContent = T.cancel;
    ok.type = "submit"; ok.className = "wbtn"; ok.textContent = submitLabel;
    btns.append(cancel, ok); form.appendChild(btns); bg.appendChild(form); document.body.appendChild(bg);
    const finish = v => { window.removeEventListener("keydown", onKey); bg.remove(); resolve(v); };
    const onKey = ev => { if (ev.key === "Escape") finish(null); };
    window.addEventListener("keydown", onKey);
    cancel.onclick = () => finish(null);
    bg.onclick = ev => { if (ev.target === bg) finish(null); };
    form.onsubmit = ev => {
      ev.preventDefault();
      const v = {};
      form.querySelectorAll("input[name], select[name]").forEach(el => { v[el.name] = el.type === "checkbox" ? el.checked : el.value; });
      finish(v);
    };
    const first = form.querySelector("input[type=text]"); if (first) first.focus();
  });
}
function field(form, labelText, el, isCheck) {
  const l = document.createElement("label");
  if (isCheck) { l.appendChild(el); l.append(" " + labelText); } else { l.append(labelText); l.appendChild(el); }
  form.appendChild(l);
  return el;
}
function input(type, name, checked) { const el = document.createElement("input"); el.type = type; el.name = name; if (checked) el.checked = true; return el; }

const rbtn = document.getElementById("rbtn");
if (rbtn) rbtn.addEventListener("click", async () => {  // 追跡中のファイルを git rm (--cached / -f はダイアログで選ぶ)
  const sel = paths();
  if (!sel.length) return;
  const untracked = [...new Set(cks().filter(x => x.checked && x.dataset.kind === "untracked").map(x => x.dataset.path))];
  if (untracked.length) { say(fmt(T.rm_untracked, {files: untracked.slice(0, 3).join(", ")}), false); return; }
  const values = await openDialog(T.wip_rm, form => {
    const note = document.createElement("p"); note.className = "hint"; note.textContent = fmt(T.rm_dialog_note, {n: sel.length}); form.appendChild(note);
    field(form, T.rm_cached_label, input("checkbox", "cached"), true);
    field(form, T.rm_force_label, input("checkbox", "force"), true);
  }, T.wip_rm);
  if (!values) return;
  rbtn.disabled = true; say(T.wip_execute, true);
  postJson("/api/rm", {paths: sel, cached: !!values.cached, force: !!values.force}).then(done).catch(e => { say(e.message, false); sync(); });
});
const sbtn = document.getElementById("sbtn");
if (sbtn) sbtn.addEventListener("click", async () => {  // stash (退避): すべて / チェックしたファイルだけ
  const sel = paths(), n = sel.length;
  const values = await openDialog(T.wip_stash, form => {
    field(form, T.stash_message, input("text", "message"), false);
    if (n) {
      const s = document.createElement("select"); s.name = "scope";
      [["all", T.stash_scope_all], ["selected", fmt(T.stash_scope_selected, {n: n})]].forEach(([v, text]) => {
        const o = document.createElement("option"); o.value = v; o.textContent = text; s.appendChild(o);
      });
      field(form, T.stash_scope, s, false);
    }
    field(form, T.stash_include_untracked, input("checkbox", "untracked", true), true);
  }, T.wip_stash);
  if (!values) return;
  const body = {message: (values.message || "").trim(), untracked: !!values.untracked};
  if (n && values.scope === "selected") body.paths = sel;
  sbtn.disabled = true; say(T.wip_execute, true);
  postJson("/api/stash-push", body).then(done).catch(e => { say(e.message, false); sbtn.disabled = false; });
});
document.querySelectorAll("[data-resolve]").forEach(b => b.addEventListener("click", () => {  // 競合ファイルを ours / theirs で解決
  const side = b.dataset.resolve, sel = [...new Set(cks().filter(x => x.checked && x.dataset.kind === "conflict").map(x => x.dataset.path))];
  if (!sel.length) { say(T.select_files, false); return; }
  if (!confirm(fmt(T.confirm_resolve, {n: sel.length, side: side}))) return;
  say(T.wip_execute, true);
  postJson("/api/resolve", {paths: sel, side: side}).then(done).catch(e => say(e.message, false));
}));
const abtn = document.getElementById("abtn");
if (abtn) abtn.addEventListener("click", () => {  // マージなどの途中を中断する
  if (!confirm(fmt(T.confirm_abort, {cmd: abtn.dataset.cmd}))) return;
  abtn.disabled = true; say(T.wip_execute, true);
  postJson("/api/abort", {}).then(done).catch(e => { abtn.disabled = false; say(e.message, false); });
});
sync();
