// 画面共通のメッセージログ。トースト (toast.js の setStatus) に出た通知を、タブの sessionStorage に残す。
// 画面の切り替え (グラフ ⇔ ファイル表示) や再読み込みをまたいで残り、「その他」メニューの「メッセージ一覧」で見られる。
// 前提: ページに t() がある。進行中 (progress) の通知は記録しない (あとに成功か失敗の通知が出るため)。
const MessageLog = (function () {
  const KEY = "gitGraphMessageLog", MAX = 300;

  function load() {
    try {
      const list = JSON.parse(sessionStorage.getItem(KEY) || "[]");
      return Array.isArray(list) ? list : [];
    } catch (e) { return []; }
  }
  function save(list) {
    try { sessionStorage.setItem(KEY, JSON.stringify(list)); } catch (e) { /* 保存できなくても通知の表示は続ける */ }
  }
  function esc(s) {
    return String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
  }

  // type: "success" | "error" | "info"。同じ通知が続けて呼ばれたとき (1 秒以内) は 1 件にまとめる
  function add(message, type) {
    if (!message) return;
    const list = load(), now = Date.now(), last = list[list.length - 1];
    if (last && last.message === message && last.type === type && now - last.time < 1000) return;
    list.push({time: now, type: type || "info", message: String(message)});
    if (list.length > MAX) list.splice(0, list.length - MAX);
    save(list);
  }
  function clear() { save([]); }

  const pad = n => String(n).padStart(2, "0");
  function stamp(ms) {
    const d = new Date(ms);
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) + " " + pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
  }
  // すべてコピー用のテキスト (古い順)
  function allText() {
    return load().map(m => "[" + stamp(m.time) + "] " + m.message).join("\n");
  }

  const MARK = {success: "✔", error: "✖", info: "•"};
  function bodyHtml() {
    const list = load().slice().reverse();  // 新しい順
    if (!list.length) return '<div class="hint">' + esc(t("message_log_empty")) + "</div>";
    const rows = list.map(m => '<tr class="msglog-row ' + esc(m.type) + '"><td class="msglog-mark">' + (MARK[m.type] || MARK.info) +
      '</td><td class="msglog-time">' + esc(stamp(m.time)) + '</td><td class="msglog-msg">' + esc(m.message) +
      '</td><td class="msglog-copy"><a class="copy" data-copy="' + esc(m.message) + '" title="' + esc(t("message_log_copy")) + '">📋</a></td></tr>').join("");
    return '<div class="msglog-bar"><button type="button" class="sbtn" id="msgLogCopyAll">' + esc(t("message_log_copy_all")) +
      '</button><button type="button" class="sbtn" id="msgLogClear">' + esc(t("message_log_clear")) + "</button></div>" +
      '<table class="log"><tbody>' + rows + "</tbody></table>";
  }
  // 一覧の HTML (開いた時点のスナップショット。表示中に増えた通知は、開き直すと出る)
  function html() { return '<div id="msgLog">' + bodyHtml() + "</div>"; }

  // 「すべてコピー」「クリア」は、画面を作り直しても効くよう document で受ける
  document.addEventListener("click", ev => {
    const copyAll = ev.target.closest("#msgLogCopyAll");
    if (copyAll) {
      const label = copyAll.dataset.label || (copyAll.dataset.label = copyAll.textContent);
      copyText(allText()).then(ok => {
        copyAll.textContent = ok ? "✅" : "⚠️";
        setTimeout(() => { copyAll.textContent = label; }, 1500);
      });
      return;
    }
    if (ev.target.closest("#msgLogClear")) {
      clear();
      const box = document.getElementById("msgLog");
      if (box) box.innerHTML = bodyHtml();
    }
  });

  return {add: add, clear: clear, html: html, allText: allText};
})();
