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

async function runSetup(body, busyKey) {
  $("setupError").textContent = "";
  $("setupSubmit").disabled = true;
  const initBtn = $("setupInitOpen");
  if (initBtn) initBtn.disabled = true;
  $("setupBusy").textContent = t(busyKey);
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
      if (initBtn) initBtn.disabled = false;
      $("setupBusy").textContent = "";
    }
  }
}

$("setupForm").addEventListener("submit", async ev => {
  ev.preventDefault();
  const url = $("setupUrl").value.trim();
  const body = {path: $("setupPath").value, url};
  if (authOrigin) {
    body.auth = {username: $("setupUser").value, password: $("setupPass").value};
    body.auth_origin = authOrigin;
  }
  await runSetup(body, url ? "setup_cloning" : "setup_opening");
});

// クローンせず、フォルダを git init してから開く (既存ファイルは未追跡の WIP)
$("setupInitOpen").addEventListener("click", async () => {
  const url = $("setupUrl").value.trim();
  if (url) {
    $("setupError").textContent = t("setup_init_with_url");
    return;
  }
  await runSetup({path: $("setupPath").value, url: "", init: true}, "setup_init_opening");
});
