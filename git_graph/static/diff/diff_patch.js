// /diff ページの「パッチ作成」ボタン (コミット・比較) と、左ペイン (WIP) のパッチ作成・適用ボタン (右ペインの WIP と同じ操作)
(function () {
  const KEEP_KEY = "git_graph_wip_diff";  // wip_diff_commit.js と同じキー。適用でファイルが変わるのでチェックの記憶は捨てる
  function say(msg, text, ok) {
    if (!msg) return;
    msg.textContent = text;
    msg.style.color = ok ? "#1a7f37" : (ok === false ? "#cf222e" : "");
  }
  // 失敗 (変更なしなど) のときにページを移動しないよう、fetch で取得して保存する
  function download(params, name, btns, msg) {
    say(msg, "");
    btns.forEach(function (b) { b.disabled = true; });
    fetch("/patch?" + new URLSearchParams(params))
      .then(function (r) { return r.ok ? r.blob() : r.text().then(function (m) { throw new Error(m || r.status); }); })
      .then(function (blob) {
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = name;
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(function () { URL.revokeObjectURL(a.href); }, 10000);
      })
      .catch(function (e) { say(msg, e.message, false); })
      .then(function () { btns.forEach(function (b) { b.disabled = false; }); });
  }
  const btn = document.getElementById("pbtn"), msg = document.getElementById("pmsg");
  const sideCreate = document.getElementById("sidePatchCreate"), sideMsg = document.getElementById("spmsg");
  if (btn && msg) btn.onclick = function () { download(JSON.parse(btn.dataset.params), btn.dataset.name, [btn], msg); };
  if (sideCreate) sideCreate.onclick = function () { download(JSON.parse(sideCreate.dataset.params), sideCreate.dataset.name, [sideCreate], sideMsg); };

  const bar = document.getElementById("sidePatch"), apply = document.getElementById("sidePatchApply");
  const am = document.getElementById("sidePatchAm"), picker = document.getElementById("sidePatchFile");
  if (!bar || !apply || !am || !picker) return;
  let mode = "apply";  // "apply": git apply (作業ツリーだけ変える) / "am": git am (コミットを積む)
  function pick(m) { mode = m; picker.value = ""; picker.click(); }
  apply.onclick = function () { pick("apply"); };
  am.onclick = function () { pick("am"); };
  function toBase64(bytes) {
    let s = "";
    for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(s);
  }
  picker.onchange = function () {
    const f = picker.files[0];
    if (!f) return;
    const size = f.size < 1024 ? f.size + " B" : Math.ceil(f.size / 1024) + " KB";
    const text = (mode === "am" ? bar.dataset.confirmAm : bar.dataset.confirmApply).split("{name}").join(f.name).split("{size}").join(size);
    if (!confirm(text)) return;
    say(sideMsg, bar.dataset.wait, true);
    apply.disabled = am.disabled = true;
    f.arrayBuffer()
      .then(function (buf) {
        return fetch(mode === "am" ? "/api/patch-am" : "/api/patch-apply", {method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({data: toBase64(new Uint8Array(buf))})});
      })
      .then(function (r) { return r.ok ? r.json() : r.text().then(function (m) { throw new Error(m || r.status); }); })
      .then(function (r) {
        try { localStorage.removeItem(KEEP_KEY); } catch (e) { /* 消せなくても動作には影響しない */ }
        say(sideMsg, r.message, true);
        setTimeout(function () { location.reload(); }, 800);
      })
      .catch(function (e) { say(sideMsg, e.message, false); apply.disabled = am.disabled = false; });
  };
})();
