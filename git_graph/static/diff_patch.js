// /diff ページの「パッチ作成」ボタン。失敗 (変更なしなど) のときにページを移動しないよう、fetch で取得して保存する
(function () {
  const btn = document.getElementById("pbtn"), msg = document.getElementById("pmsg");
  if (!btn || !msg) return;
  btn.onclick = function () {
    msg.textContent = "";
    btn.disabled = true;
    fetch("/patch?" + new URLSearchParams(JSON.parse(btn.dataset.params)))
      .then(function (r) { return r.ok ? r.blob() : r.text().then(function (m) { throw new Error(m || r.status); }); })
      .then(function (blob) {
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = btn.dataset.name;
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(function () { URL.revokeObjectURL(a.href); }, 10000);
      })
      .catch(function (e) { msg.textContent = e.message; })
      .then(function () { btn.disabled = false; });
  };
})();
