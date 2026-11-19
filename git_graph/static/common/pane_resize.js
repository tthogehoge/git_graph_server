// 左ペイン (/diff は #side、/files は #tree) の幅をドラッグで変える。
// 幅は localStorage に残し、別のページへ移っても (差分 <-> ファイル表示 など) 保つ。
// ダブルクリックで初期の幅に戻す。ハンドルにフォーカスして ←/→ キーでも変えられる。
(function () {
  const pane = document.getElementById("side") || document.getElementById("tree");
  const bar = document.getElementById("sideSplitter");
  if (!pane || !bar) return;
  const KEY = "git-graph-side-width";
  const MIN_PANE = 160;  // 左ペインの最小幅
  const MIN_MAIN = 240;  // 右側 (本文) に最低限残す幅
  const STEP = 16;       // キー操作 1 回の変化量
  let dragging = false;

  function clamp(width) {
    return Math.max(MIN_PANE, Math.min(Math.max(MIN_PANE, window.innerWidth - MIN_MAIN), width));
  }
  function apply(width) { pane.style.width = clamp(width) + "px"; }
  function save() {
    try { localStorage.setItem(KEY, String(Math.round(pane.getBoundingClientRect().width))); } catch (e) { /* 保存できなくても動作には影響しない */ }
  }
  function reset() {
    pane.style.width = "";
    try { localStorage.removeItem(KEY); } catch (e) { /* 同上 */ }
  }

  try {
    const saved = parseInt(localStorage.getItem(KEY), 10);
    if (saved > 0) apply(saved);
  } catch (e) { /* 保存済みの幅が読めなければ初期の幅のまま */ }

  bar.addEventListener("pointerdown", ev => {
    dragging = true;
    bar.setPointerCapture(ev.pointerId);
    document.body.classList.add("resizing");
    ev.preventDefault();
  });
  bar.addEventListener("pointermove", ev => {
    if (!dragging) return;
    apply(ev.clientX - pane.getBoundingClientRect().left);
  });
  function stop() {
    if (dragging) save();
    dragging = false;
    document.body.classList.remove("resizing");
  }
  bar.addEventListener("pointerup", stop);
  bar.addEventListener("pointercancel", stop);
  bar.addEventListener("dblclick", reset);
  bar.addEventListener("keydown", ev => {
    if (ev.key !== "ArrowLeft" && ev.key !== "ArrowRight") return;
    ev.preventDefault();
    apply(pane.getBoundingClientRect().width + (ev.key === "ArrowRight" ? STEP : -STEP));
    save();
  });
  window.addEventListener("resize", () => {  // ウィンドウが狭くなったとき、本文が潰れないようにする
    if (pane.style.width) apply(parseFloat(pane.style.width));
  });
})();
