function doRebase() {
  if (!actionHash) return;
  const h = actionHash, btn = document.getElementById("rebaseBtn");
  btn.disabled = true;
  fetch("/api/head").then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }))
    .then(hd => {
      if (hd.merging) throw new Error(t("op_in_progress_finish"));
      if (!hd.branch) throw new Error(t("detached_rebase"));
      if (!confirm(t("confirm_rebase", {branch: hd.branch, hash: h.slice(0, 7)}))) return null;
      setGraphStatus(t("rebasing"), "progress");
      return post("/api/rebase", {hash: h});
    })
    .then(r => retryIfBlocked(r, "rebase", t("rebasing"), mode => post("/api/rebase", {hash: h, on_conflict: mode})))
    .then(r => {
      if (r === null) { setGraphStatus(""); return; }
      clearCmp();
      setGraphStatus(r.message, "success");
      return refreshGraph().catch(() => {});
    })
    .catch(e => { setGraphStatus(t("rebase_error", {error: e.message}), "error"); })
    .finally(() => { btn.disabled = false; });
}
function doRebaseRange() {
  if (!compareAHash || !compareBHash) return;
  const from = compareAHash, to = compareBHash, btn = document.getElementById("rebaseRangeBtn");
  btn.disabled = true;
  fetch("/api/head").then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }))
    .then(hd => {
      if (hd.merging) throw new Error(t("op_in_progress_finish"));
      if (!hd.branch) throw new Error(t("detached_rebase"));
      if (!confirm(t("confirm_rebase_range", {branch: hd.branch, from: from.slice(0, 7), to: to.slice(0, 7)}))) return null;
      setGraphStatus(t("rebasing"), "progress");
      return post("/api/rebase-range", {from: from, to: to});
    })
    .then(r => retryIfBlocked(r, "rebase", t("rebasing"), mode => post("/api/rebase-range", {from: from, to: to, on_conflict: mode})))
    .then(r => {
      if (r === null) { setGraphStatus(""); return; }
      clearCmp();
      setGraphStatus(r.message, "success");
      return refreshGraph().catch(() => {});
    })
    .catch(e => { setGraphStatus(t("rebase_error", {error: e.message}), "error"); })
    .finally(() => { btn.disabled = false; });
}
function doRebaseDescendants() {
  if (!compareAHash || !compareBHash) return;
  const from = compareAHash, to = compareBHash, btn = document.getElementById("rebaseDescendantsBtn");
  btn.disabled = true;
  fetch("/api/head").then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }))
    .then(hd => {
      if (hd.merging) throw new Error(t("op_in_progress_finish"));
      if (!hd.branch) throw new Error(t("detached_rebase"));
      if (!confirm(t("confirm_rebase_descendants", {from: from.slice(0, 7), to: to.slice(0, 7)}))) return null;
      setGraphStatus(t("rebasing"), "progress");
      return post("/api/rebase-descendants", {from: from, to: to});
    })
    .then(r => retryIfBlocked(r, "rebase", t("rebasing"), mode => post("/api/rebase-descendants", {from: from, to: to, on_conflict: mode})))
    .then(r => {
      if (r === null) { setGraphStatus(""); return; }
      clearCmp();
      setGraphStatus(r.message, "success");
      return refreshGraph().catch(() => {});
    })
    .catch(e => { setGraphStatus(t("rebase_error", {error: e.message}), "error"); })
    .finally(() => { btn.disabled = false; });
}
function doRebaseEquivalent() {
  if (!compareAHash || !compareBHash) return;
  const from = compareAHash, to = compareBHash, btn = document.getElementById("rebaseEquivalentBtn");
  btn.disabled = true;
  fetch("/api/head").then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }))
    .then(hd => {
      if (hd.merging) throw new Error(t("op_in_progress_finish"));
      if (!hd.branch) throw new Error(t("detached_rebase"));
      if (!confirm(t("confirm_rebase_equivalent", {from: from.slice(0, 7), to: to.slice(0, 7)}))) return null;
      setGraphStatus(t("rebasing"), "progress");
      return post("/api/rebase-equivalent", {from: from, to: to});
    })
    .then(r => retryIfBlocked(r, "rebase", t("rebasing"), mode => post("/api/rebase-equivalent", {from: from, to: to, on_conflict: mode})))
    .then(r => {
      if (r === null) { setGraphStatus(""); return; }
      clearCmp();
      setGraphStatus(r.message, "success");
      return refreshGraph().catch(() => {});
    })
    .catch(e => { setGraphStatus(t("rebase_error", {error: e.message}), "error"); })
    .finally(() => { btn.disabled = false; });
}
function doFindEquivalentBranch() {
  if (!compareAHash || !compareBHash) return;
  const from = compareAHash, to = compareBHash, btn = document.getElementById("findEquivalentBranchBtn");
  btn.disabled = true;
  setCmpMsg(t("find_equivalent_branch") + "…", "progress");
  post("/api/find-equivalent-branch", {from: from, to: to})
    .then(r => {
      compareBHash = r.to;
      updateCmpClasses();
      saveUiState();
      setCmpMsg("", true);
      setCmpMsg(r.message, true);
    })
    .catch(e => setCmpMsg(e.message, false))
    .finally(() => { btn.disabled = false; });
}
function doFindEquivalentParents() {
  if (!compareAHash || !compareBHash) return;
  const from = compareAHash, to = compareBHash, btn = document.getElementById("findEquivalentParentsBtn");
  btn.disabled = true;
  setCmpMsg(t("find_equivalent_parents") + "…", "progress");
  post("/api/find-equivalent-parents", {from: from, to: to})
    .then(r => {
      if (!r.from || !r.to) { setCmpMsg(r.message, true); return; }
      compareAHash = r.from;
      compareBHash = r.to;
      updateCmpClasses();
      saveUiState();
      setCmpMsg("", true);
      setCmpMsg(r.message, true);
    })
    .catch(e => setCmpMsg(e.message, false))
    .finally(() => { btn.disabled = false; });
}
function doCherryPick() {
  if (!actionHash) return;
  const h = actionHash, btn = document.getElementById("cherryBtn");
  btn.disabled = true;
  fetch("/api/head").then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }))
    .then(hd => {
      if (hd.merging) throw new Error(t("op_in_progress_finish"));
      if (!confirm(t("confirm_cherry_pick", {hash: h.slice(0, 7), target: hd.branch ? t("what_branch", {name: hd.branch}) : t("target_detached_head")}))) return null;
      setGraphStatus(t("cherry_picking"), "progress");
      return post("/api/cherry-pick", {hash: h});
    })
    .then(r => retryIfBlocked(r, "cherry", t("cherry_picking"), mode => post("/api/cherry-pick", {hash: h, on_conflict: mode})))
    .then(r => {
      if (r === null) { setGraphStatus(""); return; }
      clearCmp();
      setGraphStatus(r.message, "success");
      return refreshGraph().catch(() => {});
    })
    .catch(e => { setGraphStatus(t("cherry_pick_error", {error: e.message}), "error"); })
    .finally(() => { btn.disabled = false; });
}
function doRevert() {
  if (!actionHash) return;
  const h = actionHash, btn = document.getElementById("revertBtn");
  btn.disabled = true;
  fetch("/api/head").then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }))
    .then(hd => {
      if (hd.merging) throw new Error(t("op_in_progress_finish"));
      if (!confirm(t("confirm_revert", {hash: h.slice(0, 7), target: hd.branch ? t("what_branch", {name: hd.branch}) : t("target_detached_head")}))) return null;
      setGraphStatus(t("reverting"), "progress");
      return post("/api/revert", {hash: h});
    })
    .then(r => retryIfBlocked(r, "revert", t("reverting"), mode => post("/api/revert", {hash: h, on_conflict: mode})))
    .then(r => {
      if (r === null) { setGraphStatus(""); return; }
      clearCmp();
      setGraphStatus(r.message, "success");
      return refreshGraph().catch(() => {});
    })
    .catch(e => { setGraphStatus(t("revert_error", {error: e.message}), "error"); })
    .finally(() => { btn.disabled = false; });
}
async function doRenameBranch(btn) {
  const branches = localBranches(actionHash).sort();
  if (!branches.length) return;
  const values = await openActionDialog(t("rename_branch"),
    '<label>' + esc(t("branch_to_rename")) + '<select name="old">' + branches.map(branch =>
      '<option value="' + esc(branch) + '">' + esc(branch) + "</option>").join("") + "</select></label>" +
    '<label>' + esc(t("new_branch_name")) + '<input type="text" name="new_name" value="' + esc(branches[0]) + '" required autofocus></label>',
    t("rename"),
    {onOpen: form => {  // 選んだブランチ名を、新しい名前の初期値にする
      const sel = form.elements["old"], inp = form.elements["new_name"];
      sel.addEventListener("change", () => { inp.value = sel.value; });
    }});
  if (!values) return;
  const oldName = values.old, newName = values.new_name.trim();
  if (!newName || newName === oldName) { setCmpMsg(t("branch_rename_same"), false); return; }
  btn.disabled = true; setCmpMsg(t("renaming"), "progress");
  post("/api/branch-rename", {old: oldName, new: newName})
    .then(r => { setCmpMsg(r.message, true); return refreshGraph().catch(() => {}); })
    .catch(e => setCmpMsg(t("rename_failed", {error: e.message}), false))
    .finally(() => { btn.disabled = false; });
}
async function doReset() {
  if (!actionHash) return;
  const h = actionHash, btn = document.getElementById("resetBtn");
  const options = await openActionDialog(t("reset"),
    '<label>' + esc(t("reset_mode")) + '<select name="mode">' +
    '<option value="mixed" selected>' + esc(t("reset_opt_mixed")) + '</option>' +
    '<option value="soft">' + esc(t("reset_opt_soft")) + '</option>' +
    '<option value="hard">' + esc(t("reset_opt_hard")) + '</option>' +
    '</select></label>', t("reset_submit"));
  if (!options) return;
  const mode = options.mode;
  if (!confirm(t({hard: "confirm_reset_hard", soft: "confirm_reset_soft", mixed: "confirm_reset_mixed"}[mode], {hash: h.slice(0, 7)}))) return;
  btn.disabled = true;
  setGraphStatus(t("resetting"), "progress");
  try {
    const r = await post("/api/reset", {hash: h, mode: mode});
    clearCmp();
    setGraphStatus(r.message, "success");
    await refreshGraph().catch(() => {});
  } catch (e) {
    setGraphStatus(t("reset_error", {error: e.message}), "error");
  } finally {
    btn.disabled = false;
  }
}
async function doRebaseI() {  // 対話的リベース (git rebase -i 相当)
  if (!actionHash) return;
  const h = actionHash, btn = document.getElementById("irebaseBtn");
  btn.disabled = true;
  try {
    const hd = await fetch("/api/head").then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }));
    if (hd.merging) throw new Error(t("op_in_progress_finish"));
    setGraphStatus(t("loading_rebase_commits"), "progress");
    const info = await fetch("/api/rebase-todo?hash=" + encodeURIComponent(h))
      .then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }));
    setGraphStatus("");
    const msgs = {};
    const opt = (v, label) => '<option value="' + v + '">' + label + "</option>";
    const rows = info.commits.map(c => {
      msgs[c.hash] = c.message;
      return '<div class="ri-row pick" data-h="' + c.hash + '"><div class="ri-main">' +
        '<select name="act_' + c.hash + '">' + opt("pick", "pick") + opt("reword", "reword") + opt("squash", "squash") +
        opt("fixup", "fixup") + opt("drop", "drop") + "</select>" +
        "<code>" + esc(c.short) + '</code><span class="ri-subj" title="' + esc(c.subject) + '">' + esc(c.subject) + "</span>" +
        '<span class="ri-author">' + esc(c.author) + " " + esc(c.date) + "</span>" +
        '<button type="button" data-ri="up" title="' + esc(t("ri_up")) + '">▲</button>' +
        '<button type="button" data-ri="down" title="' + esc(t("ri_down")) + '">▼</button></div>' +
        '<textarea name="msg_' + c.hash + '" rows="3" hidden>' + esc(c.message) + "</textarea></div>";
    }).join("");
    const fields = '<div class="ri-note">' + t("ri_note", {branch: esc(info.branch), hash: esc(h.slice(0, 7))}) + '</div>' +
      '<div class="ri-list">' + rows + "</div>";
    const fieldsEl = document.getElementById("actionFields");
    const validate = values => {
      const acts = Object.keys(values).filter(k => k.startsWith("act_"));
      const kept = acts.filter(k => values[k] !== "drop");
      if (kept.length && ["squash", "fixup"].includes(values[kept[0]]))
        return t("ri_first_squash");
      const bad = acts.find(k => values[k] === "reword" && !(values["msg_" + k.slice(4)] || "").trim());
      if (bad) return t("ri_reword_required");
      return null;
    };
    const p = openActionDialog(t("interactive_rebase"), fields, t("run_rebase"), {wide: true, validate: validate});
    fieldsEl.onclick = ev => {  // 並べ替え
      const b = ev.target.closest("[data-ri]");
      if (!b) return;
      const row = b.closest(".ri-row");
      if (b.dataset.ri === "up" && row.previousElementSibling) row.parentNode.insertBefore(row, row.previousElementSibling);
      if (b.dataset.ri === "down" && row.nextElementSibling) row.parentNode.insertBefore(row.nextElementSibling, row);
    };
    fieldsEl.onchange = ev => {  // 操作の種類に合わせて表示を切り替える
      const sel = ev.target.closest("select");
      if (!sel) return;
      const row = sel.closest(".ri-row");
      row.className = "ri-row " + sel.value;
      row.querySelector("textarea").hidden = sel.value !== "reword";
    };
    const values = await p;
    fieldsEl.onclick = null; fieldsEl.onchange = null;
    if (!values) return;
    const todo = Object.keys(values).filter(k => k.startsWith("act_"))
      .map(k => ({hash: k.slice(4), action: values[k], message: values["msg_" + k.slice(4)] || ""}));
    const n = a => todo.filter(t => t.action === a).length;
    const note = [n("reword") && t("ri_part_reword", {n: n("reword")}), n("squash") && t("ri_part_squash", {n: n("squash")}),
                  n("fixup") && t("ri_part_fixup", {n: n("fixup")}), n("drop") && t("ri_part_drop", {n: n("drop")})].filter(Boolean).join(" / ");
    if (!confirm(t("confirm_rebase_i", {branch: info.branch, count: todo.length, hash: h.slice(0, 7), note: note ? " (" + note + ")" : ""}))) return;
    setGraphStatus(t("rebasing"), "progress");
    let r = await post("/api/rebase-i", {hash: h, todo: todo});
    r = await retryIfBlocked(r, "rebase", t("rebasing"), mode => post("/api/rebase-i", {hash: h, todo: todo, on_conflict: mode}));
    if (r === null) { setGraphStatus(""); return; }
    clearCmp();
    setGraphStatus(r.message, "success");
    await refreshGraph().catch(() => {});
  } catch (e) {
    setGraphStatus(t("rebase_i_error", {error: e.message}), "error");
  } finally {
    btn.disabled = false;
  }
}
async function doMerge() {
  if (!actionHash) return;
  const options = await openActionDialog(t("merge"), '<label class="check" title="' + esc(t("tip_no_ff")) + '">' +
    '<input type="checkbox" name="noFf"> --no-ff</label>', t("merge"));
  if (!options) return;
  const h = actionHash, noFf = options.noFf;
  const btn = document.getElementById("mergeBtn");
  btn.disabled = true;
  fetch("/api/head").then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }))
    .then(hd => {
      if (hd.merging) throw new Error(t("merge_in_progress_resolve"));
      const into = hd.branch ? t("what_branch", {name: hd.branch}) : "detached HEAD";
      if (!confirm(t("confirm_merge", {hash: h.slice(0, 7), into: into, noff: noFf ? " (--no-ff)" : ""}))) return null;
      setGraphStatus(t("merging"), "progress");
      return post("/api/merge", {hash: h, no_ff: noFf});
    })
    .then(r => retryIfBlocked(r, "merge", t("merging"), mode => post("/api/merge", {hash: h, no_ff: noFf, on_conflict: mode})))
    .then(r => {
      if (r === null) { setGraphStatus(""); return; }
      clearCmp();
      setGraphStatus(r.message, r.conflict ? "error" : "success");
      return refreshGraph().catch(() => {}).then(() => {
        if (r.conflict) showWip();
      });
    })
    .catch(e => { setGraphStatus(t("merge_error", {error: e.message}), "error"); })
    .finally(() => { btn.disabled = false; });
}
function selectCmpA(hash) {
  compareAHash = hash;
  if (compareBHash === hash) compareBHash = null;
  if (compareAHash && compareBHash) { abActOpen = true; cmpActOpen = false; abMenuHash = null; setOtherMenu(false); }
  updateCmpClasses();
  if (nodeEl[hash]) ensureGraphVisible(nodeEl[hash], true);
  saveUiState();
}
function selectCompareHash(slot, hash) {
  if (slot === "A") selectCmpA(hash);
  else selectCmpB(hash);
}
function selectCmpB(hash) {
  compareBHash = hash;
  if (compareAHash === hash) compareAHash = null;
  if (compareAHash && compareBHash) { abActOpen = true; cmpActOpen = false; abMenuHash = null; setOtherMenu(false); }
  updateCmpClasses();
  if (nodeEl[hash]) ensureGraphVisible(nodeEl[hash], true);
  saveUiState();
}

// ---- 元に戻す / やり直し / 操作履歴 (engine.py) ----
function doUndoRedo(dir, quiet) {  // dir: "undo" | "redo"。quiet はショートカットから (確認ダイアログを出さず、結果をステータスに出す)
  const undo = dir === "undo", url = "/api/" + dir;
  closeMenus();
  api("/api/undo-history", {}).then(h => {
    const label = undo ? h.next_undo : h.next_redo;
    if (!(undo ? h.can_undo : h.can_redo) || !label) {
      setGraphStatus(t(undo ? "undo_nothing" : "redo_nothing"), "error", true);
      return null;
    }
    if (!quiet && !confirm(t(undo ? "undo_confirm" : "redo_confirm", {op: label}))) return null;
    setGraphStatus(t(undo ? "undo_busy" : "redo_busy"), "progress");
    return post(url, {}).then(r => {  // 操作のあとに作業ツリーが変わっていたら、上書きしてよいか確認する
      if (!r.worktree_changed) return r;
      setGraphStatus("", "info", true);
      if (!confirm(t("undo_worktree_force", {msg: r.message}))) return null;
      setGraphStatus(t(undo ? "undo_busy" : "redo_busy"), "progress");
      return post(url, {force: true});
    });
  }).then(r => {
    if (!r) { setGraphStatus("", "info", true); return; }
    setGraphStatus(r.message, "success", true);
    updateUndoButtons();
    return Promise.all([loadStashes(), refreshGraph().catch(() => {})]).then(() => {
      if (curView === "list" && listView.dataset.src === "/api/stash-list") showStashList(true);
    });
  }).catch(e => setGraphStatus(t("undo_failed", {error: e.message}), "error", true));
}

function updateUndoButtons() {  // 元に戻す先 / やり直す先が無いときは無効にする (--no-undo や読み取り専用では常に無効)
  api("/api/undo-history", {}).then(h => {
    const undo = document.getElementById("undoBtn"), redo = document.getElementById("redoBtn");
    undo.disabled = !h.can_undo;
    undo.title = h.can_undo ? t("tip_undo_op", {op: h.next_undo}) : t("tip_undo");
    redo.disabled = !h.can_redo;
    redo.title = h.can_redo ? t("tip_redo_op", {op: h.next_redo}) : t("tip_redo");
  }).catch(() => {});
}

function showOpHistory() {
  closeMenus();
  api("/api/undo-history", {}).then(h => {
    const rows = h.entries.map(e => {
      const note = e.done ? "" : " (" + t("op_history_undone") + ")",
            warn = e.undoable ? "" : ' <span class="hint">' + esc(t("op_history_irreversible", {reason: e.problem})) + "</span>";
      return '<tr class="' + (e.done ? "" : "oph-undone") + '"><td>' + esc(new Date(e.time * 1000).toLocaleString()) +
        "</td><td>" + esc(e.label) + esc(note) + warn + "</td></tr>";
    }).join("");
    const body = rows ? '<table class="log"><tbody>' + rows + "</tbody></table>" : '<div class="hint">' + esc(t("op_history_empty")) + "</div>";
    listView.innerHTML = "<h2>" + esc(t("op_history_title")) + "</h2>" +
      '<div class="meta">' + esc(t("op_history_help")) + "</div>" + body;
    setDocumentTitleFromHtml(esc(t("op_history_title")));
    showView("list");
    listView.dataset.src = "";
  }).catch(e => setGraphStatus(t("undo_failed", {error: e.message}), "error", true));
}
