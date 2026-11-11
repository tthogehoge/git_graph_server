// 左ペインのキーボード操作: ↑↓ で選択、Enter で右ペイン表示、
// ディレクトリ行は ← で閉じ・→/Enter で開く。ファイル Enter 後は右ペインのボタンにフォーカス。
(function () {
  const FOCUS_KEY = "git-graph-diff-focus-actions";
  let selected = null;

  function isTypingTarget(el) {
    if (!el) return false;
    const tag = el.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || tag === "BUTTON") return true;
    if (el.isContentEditable) return true;
    if (el.closest && el.closest(".cm-editor")) return true;
    return false;
  }

  function navItems() {
    const side = document.getElementById("side");
    if (!side) return [];
    const out = [];
    side.querySelectorAll(".drow, .frow, a").forEach(node => {
      if (node.classList.contains("drow")) {
        out.push({node: node, type: "dir"});
      } else if (node.classList.contains("frow")) {
        if (node.classList.contains("dhide")) return;
        const a = node.querySelector("a");
        if (a) out.push({node: a, type: "file", href: a.getAttribute("href"), row: node});
      } else if (node.tagName === "A" && !node.closest(".frow")) {
        out.push({node: node, type: "file", href: node.getAttribute("href")});
      }
    });
    return out;
  }

  function select(item) {
    if (selected) selected.classList.remove("selected");
    selected = item ? item.node : null;
    if (selected) {
      selected.classList.add("selected");
      selected.scrollIntoView({block: "nearest"});
    }
  }

  function currentIndex(items) {
    if (selected) {
      for (let i = 0; i < items.length; i++) if (items[i].node === selected) return i;
    }
    const cur = document.querySelector("#side a.cur");
    if (cur) {
      for (let i = 0; i < items.length; i++) if (items[i].node === cur) return i;
    }
    return -1;
  }

  function dirKey(row) {
    return row.dataset.kind + "|" + row.dataset.dir;
  }

  function isDirOpen(row) {
    const tog = row.querySelector(".dtog");
    return tog && tog.textContent === "▼";
  }

  function toggleDir(row, wantOpen) {
    const open = isDirOpen(row);
    if (wantOpen === open) return;
    row.click();  // diff_fold.js の click ハンドラで開閉と sessionStorage を処理
  }

  function openFile(item) {
    if (!item || !item.href) return;
    try { sessionStorage.setItem(FOCUS_KEY, "1"); } catch (e) {}
    location.href = item.href;
  }

  function focusMainActions() {
    try {
      if (sessionStorage.getItem(FOCUS_KEY) !== "1") return;
      sessionStorage.removeItem(FOCUS_KEY);
    } catch (e) { return; }
    const btn = document.querySelector("#main h1 a.ebtn, #main h1 button.ebtn, #main h1 button");
    if (btn) btn.focus();
  }

  document.addEventListener("keydown", ev => {
    if (ev.key !== "ArrowUp" && ev.key !== "ArrowDown" && ev.key !== "ArrowLeft" &&
        ev.key !== "ArrowRight" && ev.key !== "Enter") return;
    if (ev.altKey || ev.ctrlKey || ev.metaKey) return;
    if (isTypingTarget(ev.target)) return;
    if (ev.target === document.getElementById("sideSplitter")) return;
    if (!ev.target.closest("#side")) return;

    const items = navItems();
    if (!items.length) return;
    let idx = currentIndex(items);

    if (ev.key === "ArrowUp" || ev.key === "ArrowDown") {
      ev.preventDefault();
      if (idx < 0) idx = ev.key === "ArrowDown" ? -1 : items.length;
      const next = ev.key === "ArrowUp" ? idx - 1 : idx + 1;
      if (next < 0 || next >= items.length) return;
      select(items[next]);
      return;
    }

    if (idx < 0) return;
    const item = items[idx];
    if (!item) return;

    if (item.type === "dir") {
      if (ev.key === "ArrowLeft") {
        ev.preventDefault();
        toggleDir(item.node, false);
        return;
      }
      if (ev.key === "ArrowRight" || ev.key === "Enter") {
        ev.preventDefault();
        if (!isDirOpen(item.node)) toggleDir(item.node, true);
        else if (ev.key === "ArrowRight") {
          // 開いているときは最初の子ファイルへ
          const kids = navItems();
          const i = kids.findIndex(x => x.node === item.node);
          for (let j = i + 1; j < kids.length; j++) {
            if (kids[j].type === "file") { select(kids[j]); return; }
            if (kids[j].type === "dir") break;
          }
        }
        return;
      }
    }

    if (item.type === "file") {
      if (ev.key === "Enter" || ev.key === "ArrowRight") {
        ev.preventDefault();
        openFile(item);
        return;
      }
      if (ev.key === "ArrowLeft") {
        // 親ディレクトリ行があればそこに選択を移す
        const row = item.row;
        if (row) {
          const drow = [...document.querySelectorAll(".drow")].find(
            d => d.dataset.kind === row.dataset.kind && d.dataset.dir === row.dataset.dir
          );
          if (drow) {
            ev.preventDefault();
            select({node: drow, type: "dir"});
            return;
          }
        }
      }
    }
  });

  // 初期選択は現在表示中のファイル
  const initial = document.querySelector("#side a.cur");
  if (initial) select({node: initial, type: "file"});
  focusMainActions();
})();
