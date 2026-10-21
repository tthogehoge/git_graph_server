
const cks = () => [...document.querySelectorAll(".wck")];
function paths() { return [...new Set(cks().filter(x => x.checked).map(x => x.dataset.path))]; }
function sync() {
  const n = paths().length, b = document.getElementById("cbtn");
  if (b) {
    b.disabled = n === 0;
    b.textContent = T.wip_commit + " (" + n + ")";
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
  const setPath = (p, v) => cks().filter(y => y.dataset.path === p).forEach(y => { y.checked = v; });
  const g = ev.target.closest(".gall"), d = ev.target.closest(".dck");
  if (g) cks().filter(x => x.dataset.kind === g.dataset.kind).forEach(x => setPath(x.dataset.path, g.checked));
  else if (d) cks().filter(x => x.dataset.kind === d.dataset.kind && x.dataset.dir === d.dataset.dir)
    .forEach(x => setPath(x.dataset.path, d.checked));
  else if (ev.target.closest(".wck")) setPath(ev.target.dataset.path, ev.target.checked);
  else return;
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
  fetch("/api/wip-commit", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({paths: paths(), message: msg, amend: false, branch: branch})})
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
sync();
