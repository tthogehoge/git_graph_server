// 差分ページの「外部差分ツール」ボタン。このページのクエリ (path / hash / from / to / wip ...) をそのままサーバーに渡す。
(function () {
  const btn = document.getElementById("xdiff"), msg = document.getElementById("xmsg");
  if (!btn) return;
  btn.onclick = () => {
    btn.disabled = true;
    msg.textContent = ""; msg.className = "meta hint";
    fetch("/api/open-diff", {method: "POST", headers: {"Content-Type": "application/json"},
                             body: JSON.stringify(Object.fromEntries(new URLSearchParams(location.search)))})
      .then(r => r.ok ? r.json() : r.text().then(m => { throw new Error(m || r.status); }))
      .then(r => { msg.textContent = r.message; })
      .catch(e => { msg.textContent = e.message; msg.className = "meta hint err"; })
      .finally(() => { btn.disabled = false; });
  };
})();
