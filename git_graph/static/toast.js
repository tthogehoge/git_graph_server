// 画面共通のトースト通知。#statusToasts に積み上げて表示する (グラフ画面・ファイル画面で同じ見た目・同じ API)。
// 前提: ページに t() と esc() がある。
const statusToastTimers = new Map();
function setStatus(message, type, clearProgress) {
  // type: "success" | "error" | "progress" | "info"(省略時)
  // clearProgress: true のとき進行中トーストを先に消す (明示的に進捗以外を出すとき)
  const container = document.getElementById("statusToasts");
  if (!container) return;
  const progress = type === "progress";  // 進捗かどうかは表示文言ではなく type で明示する (文言は翻訳で変わる)
  if (clearProgress || !progress) {
    container.querySelectorAll(".status-toast.progress").forEach(toast => {
      clearTimeout(statusToastTimers.get(toast));
      statusToastTimers.delete(toast);
      toast.remove();
    });
  }
  if (!message) return;
  const toast = [...container.children].find(el =>
    el.dataset.message === message && el.classList.contains(progress ? "progress" : (type || "info")));
  if (toast) {
    clearTimeout(statusToastTimers.get(toast));
    statusToastTimers.delete(toast);
  }
  const item = toast || document.createElement("div");
  item.className = "status-toast " + (type || "info");
  item.setAttribute("role", "status");
  item.setAttribute("aria-live", "polite");
  item.dataset.message = message;
  item.innerHTML = '<span class="status-toast-message"></span><button type="button" class="status-toast-close" aria-label="' + esc(t("close_notification")) + '" title="' + esc(t("close_notification")) + '">×</button>';
  item.querySelector(".status-toast-message").textContent = message;
  item.querySelector(".status-toast-close").addEventListener("click", () => {
    clearTimeout(statusToastTimers.get(item));
    statusToastTimers.delete(item);
    item.remove();
  });
  if (!toast) container.appendChild(item);
  if (!progress) {
    statusToastTimers.set(item, setTimeout(() => {
      item.remove();
      statusToastTimers.delete(item);
    }, 15000));
  }
}
// グラフ画面の既存呼び出し互換
function setGraphStatus(message, type, clearProgress) {
  return setStatus(message, type, clearProgress);
}
