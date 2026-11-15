// 操作の対象 / 比較の A・B の選択 (LogSel)。グラフ・ファイル表示・メニューバーで共通。
// 選択は sessionStorage に {compareA, compareB, actionHash} で残し、画面を移っても引き継ぐ (保存形式の唯一の実装)。
// ログの行の強調の見た目は log_select.css。行 (tr[data-h] / tr[data-hash]) への付け方は kinds / markRows。
const LogSel = (function () {
  const KEY = "git_graph_ui_sel";
  const CLASSES = {compareA: "cmpA", compareB: "cmpB", actionHash: "actionTarget"};  // 選択の種類 → 行のクラス
  const listeners = [];
  function notify(sel) { listeners.forEach(fn => fn(sel)); }
  function load() {  // 保存された選択。無い / 壊れているときは null
    try {
      const o = JSON.parse(sessionStorage.getItem(KEY) || "null");
      if (!o || typeof o !== "object") return null;
      const sel = {};
      for (const k in CLASSES) sel[k] = typeof o[k] === "string" && o[k] ? o[k] : null;
      return sel;
    } catch (e) { return null; }
  }
  function save(sel) {  // 選択を保存して、onChange に知らせる (保存できなくても、動作には影響しない)
    const o = {};
    for (const k in CLASSES) o[k] = sel[k] || null;
    try { sessionStorage.setItem(KEY, JSON.stringify(o)); } catch (e) { /* 使えなくてもよい */ }
    notify(o);
  }
  function clear() {
    try { sessionStorage.removeItem(KEY); } catch (e) { /* 使えなくてもよい */ }
    notify({compareA: null, compareB: null, actionHash: null});
  }
  function kinds(hash, sel) {  // hash のコミットの行に付ける強調のクラス (A / B / 操作の対象。空白区切り)
    if (!hash || !sel) return "";
    return Object.keys(CLASSES).filter(k => sel[k] === hash).map(k => CLASSES[k]).join(" ");
  }
  function markRows(root, sel) {  // root の中のコミットの行の強調を、sel に合わせて付け直す
    root.querySelectorAll("tr.cmpA, tr.cmpB, tr.actionTarget").forEach(tr => tr.classList.remove("cmpA", "cmpB", "actionTarget"));
    root.querySelectorAll("tr[data-h], tr[data-hash]").forEach(tr => {
      const c = kinds(tr.dataset.h || tr.dataset.hash, sel);
      if (c) tr.classList.add(...c.split(" "));
    });
  }
  function onChange(fn) { listeners.push(fn); }  // 同じ画面で選択が変わったとき (save / clear) に呼ばれる
  return {load, save, clear, kinds, markRows, onChange};
})();
