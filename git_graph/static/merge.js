// マージエディタ (/merge) の画面の動作。エディタ本体 (CodeMirror 6) は vendor/merge_editor.js (window.GGMerge)。
// I18N と MERGE_PATH (対象ファイルのパス) は pages.py が先に埋め込む。
function t(key, values = {}) {
  let text = key in I18N ? I18N[key] : key;
  Object.entries(values).forEach(([name, value]) => { text = text.replaceAll("{" + name + "}", () => String(value)); });
  return text;
}
const $ = id => document.getElementById(id);
let editor = null, data = null;
let baseline = "";   // 最後に読み込んだ / 保存した内容 (エディタの改行は LF)。これと違えば、未保存の編集がある
let conflicts = 0, busy = false;

function say(text, ok) {
  const box = $("msg");
  box.textContent = text;
  box.className = "msg" + (ok === false ? " err" : ok ? " ok" : "");
}
function request(url, options) {
  return fetch(url, options).then(r => r.ok ? r.json() : r.text().then(m => { throw new Error(m || r.status); }));
}
const loadSources = () => request("/api/merge-sources?" + new URLSearchParams({path: MERGE_PATH}));
const post = (url, body) => request(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
const dirty = () => !!editor && editor.getText() !== baseline;

function refreshButtons() {
  const none = !conflicts;
  [$("prevBtn"), $("nextBtn"), $("allOursBtn"), $("allTheirsBtn")].forEach(b => { b.disabled = busy || none; });
  [$("regenBtn"), $("reloadBtn"), $("saveBtn"), $("resolveBtn")].forEach(b => { b.disabled = busy; });
}
function setBusy(on) { busy = on; refreshButtons(); }
function onConflictCount(n) {
  conflicts = n;
  $("count").textContent = t("merge_count", {n: n});
  $("count").classList.toggle("done", n === 0);
  refreshButtons();
}

function showUnsupported(d) {
  say(t("merge_unsupported", {reason: t(d.reason)}), false);
}
function start(d) {
  data = d;
  if (!d.supported) { showUnsupported(d); return; }
  ["tools", "hint", "heads", "top", "resultHead", "result"].forEach(id => { $(id).hidden = false; });
  $("hint").textContent = t("merge_hint") + (d.rebasing ? " " + t("rebase_side_hint") : "");
  $("encoding").textContent = [d.encoding, d.eol ? d.eol.toUpperCase() : ""].filter(Boolean).join(" / ");
  editor = GGMerge.createMergeEditor({
    top: $("top"), result: $("result"), ours: d.ours, theirs: d.theirs, text: d.working, onChange: onConflictCount,
    labels: {ours: t("merge_pick_ours"), theirs: t("merge_pick_theirs"), both: t("merge_pick_both"),
             both_rev: t("merge_pick_both_rev"), base: t("merge_pick_base")},
  });
  baseline = editor.getText();
  if (conflicts) editor.jump(1);
}

function discardOk() { return !dirty() || confirm(t("merge_confirm_discard")); }
function save(resolve) {
  if (!editor || busy) return;
  if (resolve && conflicts && !confirm(t("merge_confirm_markers"))) return;
  const text = editor.getText();
  setBusy(true); say(t("wip_execute"), true);
  post("/api/file-save", {path: MERGE_PATH, content: text, sha: data.sha})
    .then(r => {
      data.sha = r.sha; baseline = text;
      if (!resolve) { say(t("merge_saved"), true); return null; }
      return post("/api/resolve", {paths: [MERGE_PATH], side: "current"}).then(() => {
        say(t("merge_resolved"), true);
        location.href = "/diff?wip=unstaged";  // 残りの競合は、WIP 画面の一覧に出る
      });
    })
    .catch(e => say(e.message, false))
    .finally(() => setBusy(false));
}

$("prevBtn").onclick = () => editor.jump(-1);
$("nextBtn").onclick = () => editor.jump(1);
$("allOursBtn").onclick = () => editor.resolveAll("ours");
$("allTheirsBtn").onclick = () => editor.resolveAll("theirs");
$("regenBtn").onclick = () => {  // ベースつき (diff3) の 3 way マージの結果に作り直す
  if (!discardOk()) return;
  editor.setText(data.merged);
  say("", true);
  if (conflicts) editor.jump(1);
};
$("reloadBtn").onclick = () => {  // 作業ツリーの現在の内容を読み込み直す
  if (!discardOk()) return;
  setBusy(true);
  loadSources().then(d => {
    if (!d.supported) { showUnsupported(d); return; }
    data.sha = d.sha;
    editor.setText(d.working);
    baseline = editor.getText();
    say("", true);
  }).catch(e => say(e.message, false)).finally(() => setBusy(false));
};
$("saveBtn").onclick = () => save(false);
$("resolveBtn").onclick = () => save(true);
document.addEventListener("keydown", ev => {
  if ((ev.ctrlKey || ev.metaKey) && ev.key === "s") { ev.preventDefault(); save(false); }
});
window.addEventListener("beforeunload", ev => {
  if (dirty()) { ev.preventDefault(); ev.returnValue = ""; }  // 保存していない編集が消える
});

say(t("loading"), true);
loadSources().then(d => { say("", true); start(d); }).catch(e => say(e.message, false));
