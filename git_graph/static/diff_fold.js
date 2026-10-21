
const OPEN_KEY = "git-graph-wip-open-dirs";  // 右ペイン (WIP 一覧) と共有する。キーは "種別|ディレクトリ"
function loadOpen() { try { return new Set(JSON.parse(sessionStorage.getItem(OPEN_KEY) || "[]")); } catch (e) { return new Set(); } }
function saveOpen(s) { try { sessionStorage.setItem(OPEN_KEY, JSON.stringify([...s])); } catch (e) {} }
const openDirs = loadOpen();
function setDir(row, on) {
  row.querySelector(".dtog").textContent = on ? "▼" : "▶";
  document.querySelectorAll(".frow").forEach(r => {
    if (r.dataset.kind === row.dataset.kind && r.dataset.dir === row.dataset.dir) r.classList.toggle("dhide", !on);
  });
}
document.querySelectorAll(".drow").forEach(row => {
  const key = row.dataset.kind + "|" + row.dataset.dir;
  if (row.dataset.cur) openDirs.add(key);
  setDir(row, openDirs.has(key));
});
saveOpen(openDirs);
document.addEventListener("click", ev => {
  const row = ev.target.closest(".drow");
  if (!row || ev.target.closest("input")) return;
  const key = row.dataset.kind + "|" + row.dataset.dir, on = !openDirs.has(key);
  if (on) openDirs.add(key); else openDirs.delete(key);
  saveOpen(openDirs);
  setDir(row, on);
});
