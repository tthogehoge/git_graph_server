// パッチの作成 (ダウンロード) と適用 (アップロード)。グラフ画面とファイル表示で共通。
// 画面ごとに違うもの (文言 t・API の呼び出し post・メッセージの出し先 say) は、使う側から渡す。
// 「その他」メニューの 3 項目 (static/menubar.html。作業ツリー = WIP のパッチ作成 / 適用 (apply) / 適用 (am)) は bind が動かす。
const PatchOps = (function () {
  function bytesToBase64(bytes) {
    let s = "";
    for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(s);
  }
  // 失敗 (変更なしなど) のときにページを移動しないよう、fetch で取得して保存する。失敗は say(text, false) で知らせる
  function download(params, name, say) {
    return fetch("/patch?" + new URLSearchParams(params))
      .then(r => r.ok ? r.blob() : r.text().then(m => { throw new Error(m || r.status); }))
      .then(blob => {
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = name;
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 10000);
      })
      .catch(e => say(e.message, false));
  }
  let picker = null;
  function closeOpenMenus() {
    // グラフ画面はグローバル closeMenus。ファイル表示の page_menubar はローカルなので DOM でも閉じる
    if (typeof closeMenus === "function") closeMenus();
    ["cmpActions", "abActions", "otherActions"].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.hidden = true;
    });
    const otherBtn = document.getElementById("otherMenuBtn");
    if (otherBtn) {
      otherBtn.setAttribute("aria-expanded", "false");
      if (typeof t === "function") otherBtn.textContent = t("other");
    }
    const cmpBtn = document.getElementById("cmpActToggle");
    if (cmpBtn) cmpBtn.setAttribute("aria-expanded", "false");
    const abBtn = document.getElementById("abActToggle");
    if (abBtn) abBtn.setAttribute("aria-expanded", "false");
  }
  // パッチファイルを選ばせて適用する。mode: "apply" (git apply: 作業ツリーだけ変える) | "am" (git am: コミットを積む)。
  // o = {t, post, say, before, applied}。before() が false なら始めない (編集中の変更を捨ててよいか、などの確認)。
  // 適用に成功したら applied(結果) を呼ぶ。say(text, state): state は true (成功) / false (失敗) / "progress" (実行中)
  function pick(mode, o) {
    if (o.before && !o.before()) return;
    if (!picker) {
      picker = document.createElement("input");
      picker.type = "file";
      // hidden 属性だとファイル選択が出ない環境があるので画面外に置く
      picker.style.cssText = "position:fixed;left:-10000px;top:0;width:1px;height:1px;opacity:0;";
      document.body.appendChild(picker);
    }
    const input = picker;
    input.value = "";
    input.onchange = () => {
      closeOpenMenus();  // 選んだあとでメニューを閉じる
      const f = input.files[0];
      if (!f) return;
      const size = f.size < 1024 ? f.size + " B" : Math.ceil(f.size / 1024) + " KB";
      if (!confirm(o.t(mode === "am" ? "confirm_patch_am" : "confirm_patch_apply", {name: f.name, size: size}))) return;
      o.say(o.t("wip_execute"), "progress");
      f.arrayBuffer()
        .then(buf => o.post(mode === "am" ? "/api/patch-am" : "/api/patch-apply", {data: bytesToBase64(new Uint8Array(buf))}))
        .then(r => { o.say(r.message, true); if (o.applied) o.applied(r); })
        .catch(e => o.say(e.message, false));
    };
    // キャンセル時は onchange が来ないので cancel でメニューを閉じる
    input.oncancel = () => { closeOpenMenus(); };
    // メニューはここでは閉じない。先に hidden にするとファイル選択がキャンセルされる
    try {
      if (typeof input.showPicker === "function") input.showPicker();
      else input.click();
    } catch (e) {
      input.click();
    }
  }
  // メニューの 3 項目 (wipPatchCreateBtn / wipPatchApplyBtn / wipPatchAmBtn) を動かす。有効・無効は画面側で決める
  function bind(o) {
    const on = (id, fn) => { const b = document.getElementById(id); if (b) b.addEventListener("click", fn); };
    on("wipPatchCreateBtn", () => download({hash: "WIP"}, "wip.patch", o.say));
    // stopImmediatePropagation: 同じクリックで「メニューを閉じる」が走るとファイル選択がキャンセルされる
    on("wipPatchApplyBtn", ev => { ev.stopImmediatePropagation(); pick("apply", o); });
    on("wipPatchAmBtn", ev => { ev.stopImmediatePropagation(); pick("am", o); });
  }
  return {bytesToBase64, download, pick, bind};
})();
