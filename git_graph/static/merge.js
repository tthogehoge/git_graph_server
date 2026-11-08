// マージエディタ (/merge) の画面の動作。エディタ本体 (CodeMirror 6) は vendor/merge_editor.js (window.GGMerge)。
// I18N と MERGE_PATH (対象ファイルのパス) は pages.py が先に埋め込む。
// 方式は 2 つ。「競合マーカー方式」は、競合マーカーの入った結果を、競合ごとのボタンで解決する。
// 「ベース編集方式」は、ours | base と theirs | base を並べて、base を編集して結果にする (2 つの base は連動する)。
function t(key, values = {}) {
  let text = key in I18N ? I18N[key] : key;
  Object.entries(values).forEach(([name, value]) => { text = text.replaceAll("{" + name + "}", () => String(value)); });
  return text;
}
const $ = id => document.getElementById(id);
const MODE_KEY = "gitGraphMergeMode";
let mode = "markers";  // "markers" | "base"。保存先 (localStorage) が使えない pywebview などでも切り替わるよう、メモリ上の値を正とする
try { if (localStorage.getItem(MODE_KEY) === "base") mode = "base"; } catch (e) { mode = "markers"; }
let data = null;
let editor = null, baseEditor = null;     // 方式ごとのエディタ (使うときに作る)
let baseline = "", baseBaseline = "";     // 最後に読み込んだ / 保存した内容 (エディタの改行は LF)。これと違えば、未保存の編集がある
let baseOriginal = "";                    // 本物の base (ベース編集方式の初期値)
let conflicts = 0, diffs = {ours: 0, theirs: 0}, busy = false;

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
const markersDirty = () => !!editor && editor.getText() !== baseline;
const baseDirty = () => !!baseEditor && baseEditor.getText() !== baseBaseline;
const dirty = () => markersDirty() || baseDirty();
const activeEditor = () => mode === "base" ? baseEditor : editor;

function refreshButtons() {
  const none = !conflicts, noDiff = !diffs.ours && !diffs.theirs;
  [$("prevBtn"), $("nextBtn"), $("allOursBtn"), $("allTheirsBtn")].forEach(b => { b.disabled = busy || none; });
  [$("basePrevBtn"), $("baseNextBtn")].forEach(b => { b.disabled = busy || noDiff; });
  [$("regenBtn"), $("reloadBtn"), $("baseOursBtn"), $("baseTheirsBtn"), $("baseResetBtn"), $("saveBtn"), $("resolveBtn")].forEach(b => { b.disabled = busy; });
  $("modeMarkersBtn").classList.toggle("active", mode === "markers");
  $("modeBaseBtn").classList.toggle("active", mode === "base");
  $("modeMarkersBtn").setAttribute("aria-pressed", String(mode === "markers"));
  $("modeBaseBtn").setAttribute("aria-pressed", String(mode === "base"));
}
function setBusy(on) { busy = on; refreshButtons(); }
function onConflictCount(n) {
  conflicts = n;
  $("count").textContent = t("merge_count", {n: n});
  $("count").classList.toggle("done", n === 0);
  refreshButtons();
}
function onDiffCounts(c) {
  diffs = {ours: c.ours, theirs: c.theirs};
  $("baseCount").textContent = t("merge_base_counts", diffs);
  $("baseCount").classList.toggle("done", !c.ours && !c.theirs);
  refreshButtons();
}

function showUnsupported(d) {
  say(t("merge_unsupported", {reason: t(d.reason)}), false);
}

function createMarkersEditor() {
  editor = GGMerge.createMergeEditor({
    top: $("top"), result: $("result"), ours: data.ours, theirs: data.theirs, text: data.working, onChange: onConflictCount,
    labels: {ours: t("merge_pick_ours"), theirs: t("merge_pick_theirs"), both: t("merge_pick_both"),
             both_rev: t("merge_pick_both_rev"), base: t("merge_pick_base")},
  });
  baseline = editor.getText();
  if (conflicts) editor.jump(1);
}
function createBaseEditor() {
  baseEditor = GGMerge.createBaseMergeEditor({
    oursParent: $("oursPair"), theirsParent: $("theirsPair"), ours: data.ours, theirs: data.theirs, base: data.base,
    labels: {revert: t("merge_revert_chunk")}, onChange: onDiffCounts,
  });
  baseOriginal = baseBaseline = baseEditor.getText();
}
function setMode(next, remember) {
  mode = next;
  if (remember) { try { localStorage.setItem(MODE_KEY, next); } catch (e) { /* 保存できなくても切り替わる (次回は引き継がれない) */ } }
  $("markerMode").hidden = $("markerTools").hidden = next !== "markers";
  $("baseMode").hidden = $("baseTools").hidden = next !== "base";
  $("hint").textContent = (next === "base" ? t("merge_base_hint") + (data.has_base ? "" : " " + t("merge_no_base")) : t("merge_hint"))
                          + (data.rebasing ? " " + t("rebase_side_hint") : "");
  // エディタは、見える状態になってから作る (隠れた要素の中では、大きさを測れない)
  if (next === "markers" && !editor) createMarkersEditor();
  if (next === "base" && !baseEditor) createBaseEditor();
  const shown = activeEditor();
  requestAnimationFrame(() => { if (shown) shown.remeasure(); });  // 隠れていた間に変わった大きさを測り直す
  refreshButtons();
}
function start(d) {
  data = d;
  if (!d.supported) { showUnsupported(d); return; }
  ["tools", "hint", "work"].forEach(id => { $(id).hidden = false; });
  $("encoding").textContent = [d.encoding, d.eol ? d.eol.toUpperCase() : ""].filter(Boolean).join(" / ");
  setMode(mode, false);
}

function discardOk(isDirty) { return !isDirty || confirm(t("merge_confirm_discard")); }
function save(resolve) {
  const ed = activeEditor(), savedMode = mode;
  if (!ed || busy) return;
  const text = ed.getText();
  if (savedMode === "markers") {
    if (resolve && conflicts && !confirm(t("merge_confirm_markers"))) return;
  } else if (text === baseOriginal && !confirm(t("merge_confirm_base_unchanged"))) {
    return;  // base のまま保存すると、ours / theirs のどちらの変更も入っていない内容になる
  }
  setBusy(true); say(t("wip_execute"), true);
  post("/api/file-save", {path: MERGE_PATH, content: text, sha: data.sha})
    .then(r => {
      data.sha = r.sha;
      if (savedMode === "base") baseBaseline = text; else baseline = text;
      if (!resolve) { say(t("merge_saved"), true); return null; }
      return post("/api/resolve", {paths: [MERGE_PATH], side: "current"}).then(() => {
        say(t("merge_resolved"), true);
        location.href = "/diff?wip=unstaged";  // 残りの競合は、WIP 画面の一覧に出る
      });
    })
    .catch(e => say(e.message, false))
    .finally(() => setBusy(false));
}

$("modeMarkersBtn").onclick = () => { if (data && data.supported && mode !== "markers") setMode("markers", true); };
$("modeBaseBtn").onclick = () => { if (data && data.supported && mode !== "base") setMode("base", true); };
$("prevBtn").onclick = () => editor.jump(-1);
$("nextBtn").onclick = () => editor.jump(1);
$("allOursBtn").onclick = () => editor.resolveAll("ours");
$("allTheirsBtn").onclick = () => editor.resolveAll("theirs");
$("regenBtn").onclick = () => {  // ベースつき (diff3) の 3 way マージの結果に作り直す
  if (!discardOk(markersDirty())) return;
  editor.setText(data.merged);
  say("", true);
  if (conflicts) editor.jump(1);
};
$("reloadBtn").onclick = () => {  // 作業ツリーの現在の内容を読み込み直す
  if (!discardOk(markersDirty())) return;
  setBusy(true);
  loadSources().then(d => {
    if (!d.supported) { showUnsupported(d); return; }
    data.sha = d.sha;
    editor.setText(d.working);
    baseline = editor.getText();
    say("", true);
  }).catch(e => say(e.message, false)).finally(() => setBusy(false));
};
$("basePrevBtn").onclick = () => baseEditor.jump(-1);
$("baseNextBtn").onclick = () => baseEditor.jump(1);
// base の入れ替えは、アンドゥで戻せる (2 つの base で 1 つの履歴)
$("baseOursBtn").onclick = () => baseEditor.setText(data.ours);
$("baseTheirsBtn").onclick = () => baseEditor.setText(data.theirs);
$("baseResetBtn").onclick = () => baseEditor.setText(baseOriginal);
$("saveBtn").onclick = () => save(false);
$("resolveBtn").onclick = () => save(true);
document.addEventListener("keydown", ev => {
  if ((ev.ctrlKey || ev.metaKey) && ev.key === "s") { ev.preventDefault(); save(false); }
});
window.addEventListener("beforeunload", ev => {
  if (dirty()) { ev.preventDefault(); ev.returnValue = ""; }  // 保存していない編集が消える
});

// 上下の高さを変える仕切り。ドラッグか、↑ ↓ キー (3% ずつ)。高さは方式ごとに覚える
function setupSplitter(splitter, upper, key) {
  let percent = 45, dragging = false;
  try { const saved = parseFloat(localStorage.getItem(key)); if (saved >= 15 && saved <= 85) percent = saved; } catch (e) { /* 既定の高さ */ }
  const clamp = value => Math.min(85, Math.max(15, value));
  const apply = () => { upper.style.flexBasis = percent + "%"; splitter.setAttribute("aria-valuenow", String(Math.round(percent))); };
  const remember = () => { try { localStorage.setItem(key, String(percent)); } catch (e) { /* 保存できなくても、この画面の中では効く */ } };
  splitter.addEventListener("pointerdown", ev => {
    dragging = true;
    splitter.setPointerCapture(ev.pointerId);
    document.body.classList.add("resizing");
    ev.preventDefault();
  });
  splitter.addEventListener("pointermove", ev => {
    if (!dragging) return;
    const box = $("work").getBoundingClientRect();
    percent = clamp((ev.clientY - box.top) / box.height * 100);
    apply();
  });
  const stop = () => {
    if (!dragging) return;
    dragging = false;
    document.body.classList.remove("resizing");
    remember();
  };
  splitter.addEventListener("pointerup", stop);
  splitter.addEventListener("pointercancel", stop);
  splitter.addEventListener("keydown", ev => {
    if (ev.key !== "ArrowUp" && ev.key !== "ArrowDown") return;
    percent = clamp(percent + (ev.key === "ArrowUp" ? -3 : 3));
    apply(); remember();
    ev.preventDefault();
  });
  apply();
}
setupSplitter($("markerSplitter"), $("markerUpper"), "gitGraphMergeSplitMarkers");
setupSplitter($("baseSplitter"), $("baseUpper"), "gitGraphMergeSplitBase");

say(t("loading"), true);
loadSources().then(d => { say("", true); start(d); }).catch(e => say(e.message, false));
