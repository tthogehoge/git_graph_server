// マージエディタ (/merge) の画面の動作。エディタ本体 (CodeMirror 6) は vendor/merge_editor.js (window.GGMerge)。
// I18N と MERGE_PATH (対象ファイルのパス) は pages.py が先に埋め込む。
// 方式は 2 つ。どちらも、結果 (競合マーカーの入った内容) は 1 つで、方式を切り替えても引き継ぐ。
// 「競合マーカー方式」は、上段の ours | theirs と、下段の結果 (競合ごとの採用ボタンつき)。
// 「ベース編集方式」は、ours | 結果 と theirs | 結果 を 2 行に並べる (2 つの結果は連動する。差分の矢印ボタンで取り込む)。
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
let editor = null, baseEditor = null;     // 方式ごとのエディタ (使うときに作る)。内容は共通で、切り替えるときに渡す
let baseline = "";                        // 最後に読み込んだ / 保存した内容 (エディタの改行は LF)。これと違えば、未保存の編集がある
let conflicts = 0, busy = false;
// 競合の採用などのショートカットキー (WinMerge と同じ向き。左 = ours、右 = theirs)
const SHORTCUTS = {ours: "Alt+\u2192", theirs: "Alt+\u2190", both: "Alt+Shift+\u2192", both_rev: "Alt+Shift+\u2190"};

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
const activeEditor = () => mode === "base" ? baseEditor : editor;
const dirty = () => !!activeEditor() && activeEditor().getText() !== baseline;

function refreshButtons() {
  const none = !conflicts;
  [$("prevBtn"), $("nextBtn"), $("allOursBtn"), $("allTheirsBtn")].forEach(b => { b.disabled = busy || none; });
  [$("basePrevBtn"), $("baseNextBtn"), $("regenBtn"), $("reloadBtn"), $("saveBtn"), $("resolveBtn")].forEach(b => { b.disabled = busy; });
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

function showUnsupported(d) {
  say(t("merge_unsupported", {reason: t(d.reason)}), false);
}

const conflictLabels = () => ({ours: t("merge_pick_ours"), theirs: t("merge_pick_theirs"), both: t("merge_pick_both"),
                               both_rev: t("merge_pick_both_rev"), base: t("merge_pick_base"), keys: SHORTCUTS, revert: t("merge_revert_chunk")});
function createMarkersEditor(text) {
  editor = GGMerge.createMergeEditor({top: $("top"), result: $("result"), ours: data.ours, theirs: data.theirs, text: text,
                                      onChange: onConflictCount, labels: conflictLabels()});
}
function createBaseEditor(text) {
  baseEditor = GGMerge.createBaseMergeEditor({oursParent: $("oursPair"), theirsParent: $("theirsPair"), ours: data.ours, theirs: data.theirs,
                                              text: text, onChange: onConflictCount, labels: conflictLabels()});
}
// 方式を切り替える。結果の内容は、切り替える前の方式のものを引き継ぐ (どちらの方式のエディタにも、同じ内容を入れる)
function setMode(next, remember) {
  const text = activeEditor() ? activeEditor().getText() : data.working;
  mode = next;
  if (remember) { try { localStorage.setItem(MODE_KEY, next); } catch (e) { /* 保存できなくても切り替わる (次回は引き継がれない) */ } }
  $("markerMode").hidden = next !== "markers";
  $("baseMode").hidden = $("baseTools").hidden = next !== "base";
  $("hint").textContent = (next === "base" ? t("merge_base_hint") : t("merge_hint")) + " " + t("merge_keys_hint") + (data.rebasing ? " " + t("rebase_side_hint") : "");
  // エディタは、見える状態になってから作る (隠れた要素の中では、大きさを測れない)
  const first = !editor && !baseEditor;
  if (next === "markers") { if (!editor) createMarkersEditor(text); else if (editor.getText() !== text) editor.setText(text); }
  else if (!baseEditor) createBaseEditor(text); else if (baseEditor.getText() !== text) baseEditor.setText(text);
  const shown = activeEditor();
  if (first) { baseline = shown.getText(); if (conflicts) shown.jump(1); }
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

function discardOk() { return !dirty() || confirm(t("merge_confirm_discard")); }
function save(resolve) {
  const ed = activeEditor();
  if (!ed || busy) return;
  if (resolve && conflicts && !confirm(t("merge_confirm_markers"))) return;
  const text = ed.getText();
  setBusy(true); say(t("wip_execute"), true);
  post("/api/file-save", {path: MERGE_PATH, content: text, sha: data.sha})
    .then(r => {
      data.sha = r.sha; baseline = text;
      if (!resolve) { say(t("merge_saved"), true); return null; }
      return post("/api/resolve", {paths: [MERGE_PATH], side: "current"}).then(() => {
        say(t("merge_resolved"), true);
        location.href = "/files?hash=WIP";  // 残りの競合は、ファイル表示の WIP 一覧に出る
      });
    })
    .catch(e => say(e.message, false))
    .finally(() => setBusy(false));
}

$("modeMarkersBtn").onclick = () => { if (data && data.supported && mode !== "markers") setMode("markers", true); };
$("modeBaseBtn").onclick = () => { if (data && data.supported && mode !== "base") setMode("base", true); };
$("prevBtn").onclick = () => activeEditor().jump(-1);
$("nextBtn").onclick = () => activeEditor().jump(1);
$("basePrevBtn").onclick = () => baseEditor.jumpDiff(-1);
$("baseNextBtn").onclick = () => baseEditor.jumpDiff(1);
$("allOursBtn").onclick = () => activeEditor().resolveAll("ours");
$("allTheirsBtn").onclick = () => activeEditor().resolveAll("theirs");
$("regenBtn").onclick = () => {  // ベースつき (diff3) の 3 way マージの結果に作り直す
  if (!discardOk()) return;
  activeEditor().setText(data.merged);
  say("", true);
  if (conflicts) activeEditor().jump(1);
};
$("reloadBtn").onclick = () => {  // 作業ツリーの現在の内容を読み込み直す
  if (!discardOk()) return;
  setBusy(true);
  loadSources().then(d => {
    if (!d.supported) { showUnsupported(d); return; }
    data.sha = d.sha;
    activeEditor().setText(d.working);
    baseline = activeEditor().getText();
    say("", true);
  }).catch(e => say(e.message, false)).finally(() => setBusy(false));
};
$("saveBtn").onclick = () => save(false);
$("resolveBtn").onclick = () => save(true);
document.addEventListener("keydown", ev => {
  if ((ev.ctrlKey || ev.metaKey) && !ev.altKey && ev.key === "s") { ev.preventDefault(); save(false); return; }
  // WinMerge と同じ。Alt+↓ / Alt+↑: 次 / 前の競合、Alt+→: ours を採用、Alt+←: theirs を採用 (Shift を足すと両方)。
  // ブラウザーの「戻る / 進む」や、エディタの行の移動より先に受けて、効かなくする
  if (!ev.altKey || ev.ctrlKey || ev.metaKey || !activeEditor() || busy) return;
  const choice = ev.key === "ArrowRight" ? (ev.shiftKey ? "both" : "ours") : ev.key === "ArrowLeft" ? (ev.shiftKey ? "both_rev" : "theirs") : "";
  if (ev.key === "ArrowDown" && !ev.shiftKey) activeEditor().jump(1);
  else if (ev.key === "ArrowUp" && !ev.shiftKey) activeEditor().jump(-1);
  else if (choice) activeEditor().adopt(choice);
  else return;
  ev.preventDefault(); ev.stopPropagation();
}, true);
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
