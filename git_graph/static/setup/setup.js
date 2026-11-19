// 設定画面 (/setup) の画面の動作。翻訳 I18N は、このファイルより先に読む /static/i18n.js。

function t(key, values = {}) {  // {name} を values[name] に置き換える (値の中の $ は特別扱いしない)
  return (I18N[key] ?? key).replace(/\{(\w+)\}/g, (m, name) => name in values ? String(values[name]) : m);
}
const $ = id => document.getElementById(id);
let authOrigin = null;  // 認証が必要だと言われた origin (認証情報はこの origin にだけ使う)

async function post(path, body) {
  const res = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const text = await res.text();
  if (!res.ok) throw new Error(text);
  return JSON.parse(text);
}

$("setupForm").addEventListener("submit", async ev => {
  ev.preventDefault();
  const url = $("setupUrl").value.trim();
  const body = {path: $("setupPath").value, url};
  if (authOrigin) {
    body.auth = {username: $("setupUser").value, password: $("setupPass").value};
    body.auth_origin = authOrigin;
  }
  $("setupError").textContent = "";
  $("setupSubmit").disabled = true;
  $("setupBusy").textContent = t(url ? "setup_cloning" : "setup_opening");
  let moving = false;
  try {
    const res = await post("/api/setup", body);
    if (res.auth_required) {  // 認証が必要。ユーザー名とパスワードを尋ねて、もう一度
      authOrigin = res.auth_origin;
      $("setupAuthNote").textContent = t("setup_auth_needed", {origin: authOrigin});
      $("setupAuth").hidden = false;
      $("setupUser").focus();
      return;
    }
    moving = true;
    location.href = "/";
  } catch (e) {
    $("setupError").textContent = e.message;
  } finally {
    if (!moving) {
      $("setupSubmit").disabled = false;
      $("setupBusy").textContent = "";
    }
  }
});
