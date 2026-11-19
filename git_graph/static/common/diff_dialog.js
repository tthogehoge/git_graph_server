// WIP の差分ページで使う共通部品 (commit / remote の両方が使う)。文言は先に定義した T から引く。
const fmt = (text, values) => Object.entries(values || {}).reduce((s, [k, v]) => s.split("{" + k + "}").join(String(v)), text);
function postJson(url, body) {
  return fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)})
    .then(r => r.ok ? r.json() : r.text().then(m => { throw new Error(m || r.status); }));
}
function openDialog(title, build, submitLabel) {  // build(form): 入力欄を作る。送信で {name: 値} を返し、キャンセルで null
  return new Promise(resolve => {
    const bg = document.createElement("div"), form = document.createElement("form");
    bg.id = "dlgBg"; form.id = "dlg";
    const h = document.createElement("h2"); h.textContent = title; form.appendChild(h);
    build(form);
    const btns = document.createElement("div"); btns.className = "dbtns";
    const cancel = document.createElement("button"), ok = document.createElement("button");
    cancel.type = "button"; cancel.className = "wbtn"; cancel.textContent = T.cancel;
    ok.type = "submit"; ok.className = "wbtn"; ok.textContent = submitLabel;
    btns.append(cancel, ok); form.appendChild(btns); bg.appendChild(form); document.body.appendChild(bg);
    const finish = v => { window.removeEventListener("keydown", onKey); bg.remove(); resolve(v); };
    const onKey = ev => { if (ev.key === "Escape") finish(null); };
    window.addEventListener("keydown", onKey);
    cancel.onclick = () => finish(null);
    bg.onclick = ev => { if (ev.target === bg) finish(null); };
    form.onsubmit = ev => {
      ev.preventDefault();
      const v = {};
      form.querySelectorAll("input[name], select[name]").forEach(el => { v[el.name] = el.type === "checkbox" ? el.checked : el.value; });
      finish(v);
    };
    const first = form.querySelector("input[type=text]"); if (first) first.focus();
  });
}
function field(form, labelText, el, isCheck) {
  const l = document.createElement("label");
  if (isCheck) { l.appendChild(el); l.append(" " + labelText); } else { l.append(labelText); l.appendChild(el); }
  form.appendChild(l);
  return el;
}
function input(type, name, checked) { const el = document.createElement("input"); el.type = type; el.name = name; if (checked) el.checked = true; return el; }
function selectEl(name, options, selected) {  // options: [[値, 表示], ...]
  const s = document.createElement("select"); s.name = name;
  options.forEach(([value, text]) => {
    const o = document.createElement("option"); o.value = value; o.textContent = text;
    if (value === selected) o.selected = true;
    s.appendChild(o);
  });
  return s;
}
function listBlock(form, title, files) {  // 見出しつきのファイル一覧 (空なら何も出さない)
  if (!files || !files.length) return;
  const h = document.createElement("h3"), ul = document.createElement("ul");
  h.textContent = title;
  files.forEach(f => { const li = document.createElement("li"); li.textContent = f; ul.appendChild(li); });
  form.append(h, ul);
}
function hintLine(form, text) { const p = document.createElement("p"); p.className = "hint"; p.textContent = text; form.appendChild(p); }
// 編集中のファイルや管理外ファイルに操作 (pull / checkout) を止められたとき、stash / 破棄を選んでもらう (null = 取りやめ)。r は止まった結果、op は操作名
async function askBlockedDialog(r, op) {
  const v = await openDialog(fmt(T.co_blocked_op, {op: op}), form => {
    hintLine(form, T.co_blocked_hint_op);
    listBlock(form, T.co_modified, r.modified);
    listBlock(form, T.co_untracked_op, r.untracked);
    hintLine(form, T.co_hint_stash + "\n" + T.co_hint_discard);
    form.appendChild(selectEl("mode", [["stash", T.co_stash_retry], ["discard", T.co_discard_retry]], "stash"));
  }, T.ok);
  return v ? v.mode : null;
}
