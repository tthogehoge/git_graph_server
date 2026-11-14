// 作業ツリーの変更 (WIP) の一覧とコミットフォーム。グラフ・ファイル表示・差分の 3 画面で、この 1 つの実装を使う。
// (見た目も動作も同じにするため、画面ごとに作り直さない。画面ごとの違いは下の host だけで表す)
//
//   const panel = WipPanel.create({
//     t(key, params)            文言 (グラフは t、ほかの画面は T と fmt)
//     get(url, params)          GET して JSON を返す Promise
//     post(url, body)           POST して JSON を返す Promise
//     dialog(title, fieldsHtml, submitLabel)   入力ダイアログ。入力値のオブジェクト、取りやめなら null を返す Promise
//     canWrite()                書き込めるか (読み取り専用のときはチェック欄とフォームを出さない)
//     afterChange(message, {stash, hash})   操作が成功したあと (hash はコミット時、グラフは再描画、ほかの画面は読み直す)
//     onFiles(files, status)    一覧が決まったとき (省略可)
//     amendOverride()           amend の状態を画面の都合で決めるとき true / false を返す (省略可。差分ページは URL の wip=amend)
//     current(f)                いま開いているファイルの行か (省略可。差分ページで強調する)
//   });
//   panel.render(root, {top(s, files), mid(s, files), error(e), okMsg})   root に、一覧とフォームを描く
//
// チェック・メッセージ・ブランチ名は localStorage で、amend は sessionStorage で、画面をまたいで引き継ぐ。
const WipPanel = (function () {
  const KEEP_KEY = "git_graph_wip_diff", KEEP_MS = 6 * 60 * 60 * 1000;  // 放置された古い状態を使わないよう、一定時間たったものは捨てる
  const OPEN_KEY = "git-graph-wip-open-dirs", AMEND_KEY = "git_graph_wip_amend";
  const DIR_COLLAPSE = 10;  // 1つのディレクトリの変更がこの件数以上なら、既定で畳む
  const ST_CLASS = {M: "stM", T: "stM", A: "stA", D: "stD", R: "stR", C: "stR", U: "stU", "?": "stN"};
  const esc = s => String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
  const dirOf = path => { const i = path.lastIndexOf("/"); return i < 0 ? "" : path.slice(0, i); };
  const badge = f => f.st ? '<span class="st ' + (ST_CLASS[f.st] || "stM") + '">' + esc(f.st) + "</span>" : "";
  // 差分はファイル表示の右ペインで開く。ctx は {hash} (1コミット) または {from, to} (比較)
  function diffUrl(ctx, f) {
    ctx = ctx || {};
    const p = {hash: ctx.hash || ctx.to || "WIP", path: dirOf(f.path), file: f.path, view: "diff"};
    if (ctx.from) p.dfrom = ctx.from;
    if (ctx.to) p.dto = ctx.to;
    else if (ctx.wip === "compare") p.dto = "WIP";
    if (f.wip && ctx.wip !== "compare") p.dwip = f.wip;
    if (f.old_path) p.dold = f.old_path;
    return "/files?" + new URLSearchParams(p);
  }
  let openDirs = new Set();  // 開いているディレクトリ ("種別|パス")
  try { openDirs = new Set(JSON.parse(sessionStorage.getItem(OPEN_KEY) || "[]")); } catch (e) { /* 読めなければ全部畳んだまま */ }
  const saveOpenDirs = () => { try { sessionStorage.setItem(OPEN_KEY, JSON.stringify([...openDirs])); } catch (e) { /* 同上 */ } };

  // ファイルの一覧 (変更の種類・パス・増減・棒グラフ)。グラフのコミットの詳細と WIP の両方で使う。
  // o: {t, check(f) → チェック欄の td の html (WIP のとき), collapse (ディレクトリを畳む), current(f)}
  function filesTable(ctx, files, offset, o) {
    o = o || {};
    const t = o.t;
    offset = offset || 0;
    if (!files.length) return '<p class="hint">' + esc(t("no_changed_files")) + "</p>";
    let add = 0, del = 0, max = 0;
    files.forEach(f => { add += f.added || 0; del += f.deleted || 0; max = Math.max(max, (f.added || 0) + (f.deleted || 0)); });
    const scale = max > 12 ? 12 / max : 1;
    const dirCount = {}, dirStat = {}, dirSeen = {};
    if (o.collapse) files.forEach(f => {
      const dir = dirOf(f.path);
      dirCount[dir] = (dirCount[dir] || 0) + 1;
      dirStat[dir] = dirStat[dir] || [0, 0];
      dirStat[dir][0] += f.added || 0; dirStat[dir][1] += f.deleted || 0;
    });
    const rows = files.map((f, k) => {
      const a = f.added || 0, d = f.deleted || 0;
      const dir = dirOf(f.path), folded = o.collapse && dirCount[dir] >= DIR_COLLAPSE;
      const open = folded && openDirs.has(f.wip + "|" + dir);
      let head = "";
      if (folded && !dirSeen[dir]) {
        dirSeen[dir] = true;
        head = '<tr class="drow" data-dir="' + esc(dir) + '" data-kind="' + esc(f.wip) + '" title="' + esc(t("dir_toggle")) + '">' +
          (o.check ? '<td class="fk"><input type="checkbox" class="wipdir" data-kind="' + esc(f.wip) + '" data-dir="' + esc(dir) + '"></td>' : "") +
          '<td class="fp" colspan="3"><span class="dtog">' + (open ? "▼" : "▶") + "</span> " + esc(dir ? dir + "/" : t("dir_root")) +
          ' <span class="hint">(' + esc(t("n_items", {n: dirCount[dir]})) + ') <span class="plus">+' + dirStat[dir][0] +
          '</span> <span class="minus">-' + dirStat[dir][1] + "</span></span></td></tr>";
      }
      const pa = a ? Math.max(1, Math.round(a * scale)) : 0, pd = d ? Math.max(1, Math.round(d * scale)) : 0;
      const name = f.old_path ? f.old_path + " => " + f.path : f.path;
      const cls = "frow" + (folded && !open ? " dhide" : "") + (o.current && o.current(f) ? " cur" : "");
      return head + '<tr class="' + cls + '" data-k="' + (offset + k) + '" data-kind="' + esc(f.wip || "") + '" data-dir="' + esc(dir) + '">' + (o.check ? o.check(f) : "") +
        '<td class="fp">' + badge(f) + (f.tag ? '<span class="hint">[' + esc(f.tag) + "] </span>" : "") + '<a href="' + esc(diffUrl(ctx, f)) +
        '">' + esc(name) + '</a></td><td class="fc">' +
        (f.label ? esc(t(f.label)) : f.added === null ? "Bin" : a + d) + '</td><td class="fb"><span class="plus">' + "+".repeat(pa) +
        '</span><span class="minus">' + "-".repeat(pd) + "</span></td></tr>";
    }).join("");
    return '<div class="meta">' + files.length + (files.some(f => f.added !== undefined)
        ? " files changed, " + add + " insertions(+), " + del + " deletions(-)" : " files") + "</div>" +
      '<table class="files">' + rows + "</table>";
  }

  function create(host) {
    const t = host.t;
    let root = null;
    let sel = {};  // 「種類 + パス」→ ユーザーが付け外ししたチェック状態 (ステージ済みと未ステージは別々にチェックできる)
    let merging = false, rebasing = false, files = [], started = false;
    // amend モード: チェック中は「直前のコミットの親」と作業ツリーの差分を出し、メッセージも直前のものを入れる
    let amendOn = false, msg = "", msgNormal = "", headMsg = "", branchVal = "";  // msgNormal: amend にする前に書いていたメッセージ (解除で戻す)
    const key = (kind, path) => kind + "\0" + path;

    // ---- 画面をまたぐ状態の保存 ----
    function keepLoad() {
      try {
        const k = JSON.parse(localStorage.getItem(KEEP_KEY));
        return k && Date.now() - k.t < KEEP_MS ? k : null;
      } catch (e) { return null; }
    }
    function keepSave() {
      if (amendOn) return;  // amend のあいだは共有しない (一覧が別物のため)
      try { localStorage.setItem(KEEP_KEY, JSON.stringify({t: Date.now(), sel: sel, msg: msg, branch: branchVal})); } catch (e) { /* 保存できなくても動作には影響しない */ }
    }
    function keepClear() { try { localStorage.removeItem(KEEP_KEY); } catch (e) { /* 同上 */ } }
    function keepPull() {  // ほかの画面で付け外しした内容を取り込む
      const k = keepLoad();
      if (!k) return;
      sel = Object.assign({}, k.sel || {});
      if (typeof k.msg === "string") msg = k.msg;
      if (typeof k.branch === "string") branchVal = k.branch;
    }
    function saveAmend() { try { sessionStorage.setItem(AMEND_KEY, amendOn ? "1" : ""); } catch (e) { /* 同上 */ } }
    function loadAmend() {
      if (host.amendOverride) { const v = host.amendOverride(); if (typeof v === "boolean") return v; }
      try { return sessionStorage.getItem(AMEND_KEY) === "1"; } catch (e) { return false; }
    }
    function reset(clearText) {  // 操作が成功したあとの状態
      sel = {}; keepClear();
      if (clearText) { msg = ""; msgNormal = ""; branchVal = ""; amendOn = false; saveAmend(); }
    }

    // ---- チェック ----
    function isChecked(f) {  // amend: 直前のコミットのファイルは、外さない限りコミットに残る (チェック済みが既定)。マージ中も、未追跡ファイル以外は全部チェック済みが既定
      const k = key(f.wip, f.path);
      return k in sel ? sel[k] : ((merging && f.wip !== "untracked") || !!f.in_head || f.wip === "staged");
    }
    function checkCell(f) {
      return '<td class="fk"><input type="checkbox" class="wipck" data-kind="' + esc(f.wip) + '" data-dir="' + esc(dirOf(f.path)) + '" data-path="' + esc(f.path) + '"' +
        (isChecked(f) ? " checked" : "") + (f.in_head ? ' title="' + esc(t("wip_file_remove_title")) + '"' : "") + "></td>";
    }
    const cks = kind => [...root.querySelectorAll(".wipck" + (kind ? '[data-kind="' + kind + '"]' : ""))].filter(x => !x.disabled);  // 操作できる (disabled でない) チェックボックス
    const paths = () => { const s = new Set(); cks().forEach(el => { if (el.checked) s.add(el.dataset.path); }); return [...s]; };  // 同じファイルが ステージ済み と 未ステージ の両方に出ても1件
    const stagedPaths = () => [...new Set(cks("staged").filter(el => el.checked).map(el => el.dataset.path))];  // チェックされたステージ済みのファイル
    const workPaths = () => [...new Set(cks().filter(el => el.checked && el.dataset.kind !== "staged").map(el => el.dataset.path))];  // 作業ツリーの内容をコミットするファイル (未ステージ・未追跡・競合・amend)
    function stagePlan() {
      const stage = new Set(), unstage = new Set();
      cks().forEach(el => {
        if (el.dataset.kind === "unstaged" || el.dataset.kind === "untracked") {
          if (el.checked) stage.add(el.dataset.path);
        } else if (el.dataset.kind === "staged" && !el.checked) unstage.add(el.dataset.path);
      });
      stage.forEach(path => unstage.delete(path));
      return {stage_paths: [...stage], unstage_paths: [...unstage]};
    }
    function setCk(el, v) {  // このチェックボックスだけを切り替える (同じパスの別の欄は変えない)
      sel[key(el.dataset.kind, el.dataset.path)] = v;
      el.checked = v;
      keepSave();
    }
    function sync() {  // コミットボタンの件数と、グループの全選択チェックを更新
      const n = paths().length, b = root.querySelector("#wipCommit");
      const dis = root.querySelector("#wipDiscard");
      const stage = root.querySelector("#wipStage"), plan = stagePlan();
      if (stage) {
        const count = new Set(plan.stage_paths.concat(plan.unstage_paths)).size;
        stage.disabled = count === 0;
        stage.textContent = t("stage_button") + " (" + count + ")";
      }
      if (dis) { dis.disabled = n === 0; dis.textContent = t("wip_discard") + " (" + n + ")"; }
      const rm = root.querySelector("#wipRm");
      if (rm) { rm.disabled = n === 0; rm.textContent = t("wip_rm") + " (" + n + ")"; }
      if (b) {
        b.disabled = n === 0;
        b.textContent = t("wip_commit") + " (" + n + ")" + (amendOn ? " amend" : "");
      }
      root.querySelectorAll(".wipdir").forEach(dc => {
        const list = cks(dc.dataset.kind).filter(x => x.dataset.dir === dc.dataset.dir), c = list.filter(x => x.checked).length;
        dc.disabled = list.length === 0;
        dc.checked = list.length > 0 && c === list.length;
        dc.indeterminate = c > 0 && c < list.length;
      });
      root.querySelectorAll(".wipall").forEach(all => {
        const list = cks(all.dataset.kind), c = list.filter(x => x.checked).length;
        all.disabled = list.length === 0;
        all.checked = list.length > 0 && c === list.length;
        all.indeterminate = c > 0 && c < list.length;
      });
    }
    function say(text, ok) {
      const el = root.querySelector("#wipMsgOut");
      if (el) { el.textContent = text; el.className = "amsg " + (ok ? "ok" : "ng"); }
      else if (host.say) host.say(text, ok);
    }
    const run = (btn, text) => { if (btn) btn.disabled = true; say(text || t("wip_execute"), true); };  // 操作の実行中

    // ---- 操作 ----
    function setAmend(on) {  // amend のチェックを切り替える: 差分の一覧とメッセージを入れ替えて再表示
      if (on === amendOn) return;
      sel = {};  // 一覧が変わるので、付け外ししたチェックはリセット
      if (on) { msgNormal = msg; msg = headMsg; }  // 直前のコミットのメッセージを入れておく
      else { msg = msgNormal; msgNormal = ""; }
      amendOn = on;
      saveAmend();
      render(root, lastOpts);
    }
    function doCommit() {
      const ta = root.querySelector("#wipMsg"), text = ta ? ta.value : "";
      const branchInput = root.querySelector("#wipBranch");
      const branch = branchInput ? branchInput.value.trim() : "";
      const list = paths();
      if (!text.trim()) { say(t("message_required"), false); return; }
      if (!amendOn && !list.length) { say(t("select_files"), false); return; }
      if (amendOn) {
        const chk = new Set(list), drops = files.filter(f => f.in_head && !chk.has(f.path)).length;
        const dropText = drops ? "\n" + t("amend_drop", {n: drops}) : "";
        if (!confirm(t("amend_confirm", {drop: dropText}))) return;
      }
      run(root.querySelector("#wipCommit"));
      // paths: 作業ツリーの内容をコミットするファイル / staged_paths: ステージ済みの内容だけをコミットするファイル (未ステージの変更は残る)
      host.post("/api/wip-commit", {paths: workPaths(), staged_paths: stagedPaths(), message: text, amend: amendOn, branch: branch})
        .then(r => { reset(true); host.afterChange(r.message, {hash: r.hash}); })
        .catch(e => { say(e.message, false); sync(); });
    }
    function doStage() {
      const plan = stagePlan();
      if (!plan.stage_paths.length && !plan.unstage_paths.length) return;
      run(root.querySelector("#wipStage"));
      host.post("/api/stage", plan)
        .then(r => { reset(false); host.afterChange(r.message); })
        .catch(e => { say(e.message, false); sync(); });
    }
    function doResolve(side) {  // 競合ファイルを ours / theirs / 現在の状態 で解決する (チェックしたファイルが対象)
      const list = cks("conflict").filter(x => x.checked).map(x => x.dataset.path);
      if (!list.length) { say(t("select_files"), false); return; }
      if (!confirm(side === "current" ? t("confirm_resolve_current", {n: list.length}) : t("confirm_resolve", {n: list.length, side: side}))) return;
      say(t("wip_execute"), true);
      host.post("/api/resolve", {paths: list, side: side})
        .then(r => { reset(false); host.afterChange(r.message); })
        .catch(e => say(e.message, false));
    }
    function doDiscard() {
      const list = paths();
      if (!list.length) return;
      const confl = new Set(files.filter(f => f.wip === "conflict").map(f => f.path)), hit = list.filter(p => confl.has(p));
      if (hit.length) { say(t("discard_conflict", {files: hit.slice(0, 3).join(", ")}), false); return; }
      if (!confirm(t("confirm_discard", {n: list.length}))) return;
      run(root.querySelector("#wipDiscard"));
      host.post("/api/discard", {paths: list})
        .then(r => { reset(false); host.afterChange(r.message); })
        .catch(e => { say(e.message, false); sync(); });
    }
    async function doRm() {  // 追跡中のファイルを git rm する (--cached / -f はダイアログで選ぶ)
      const list = paths();
      if (!list.length) return;
      const untracked = new Set(files.filter(f => f.wip === "untracked").map(f => f.path)), hit = list.filter(p => untracked.has(p));
      if (hit.length) { say(t("rm_untracked", {files: hit.slice(0, 3).join(", ")}), false); return; }
      const fields = '<p class="hint">' + esc(t("rm_dialog_note", {n: list.length})) + "</p>" +
        '<label class="check"><input type="checkbox" name="cached"> ' + esc(t("rm_cached_label")) + "</label>" +
        '<label class="check"><input type="checkbox" name="force"> ' + esc(t("rm_force_label")) + "</label>";
      const values = await host.dialog(t("wip_rm"), fields, t("wip_rm"));
      if (!values) return;
      run(root.querySelector("#wipRm"));
      host.post("/api/rm", {paths: list, cached: !!values.cached, force: !!values.force})
        .then(r => { reset(false); host.afterChange(r.message); })
        .catch(e => { say(e.message, false); sync(); });
    }
    async function doStash() {  // 作業ツリーの変更を stash に退避する (チェックしたファイルだけ / すべて)
      const list = paths(), n = list.length;
      const fields = "<label>" + esc(t("stash_message")) + '<input type="text" name="message" autofocus></label>' +
        (n ? "<label>" + esc(t("stash_scope")) + '<select name="scope">' +
          '<option value="all" selected>' + esc(t("stash_scope_all")) + "</option>" +
          '<option value="selected">' + esc(t("stash_scope_selected", {n: n})) + "</option></select></label>" : "") +
        '<label class="check"><input type="checkbox" name="untracked" checked> ' + esc(t("stash_include_untracked")) + "</label>";
      const values = await host.dialog(t("wip_stash"), fields, t("wip_stash"));
      if (!values) return;
      const body = {message: values.message.trim(), untracked: !!values.untracked};
      if (n && values.scope === "selected") body.paths = list;
      const btn = root.querySelector("#wipStash");
      run(btn);
      host.post("/api/stash-push", body)
        .then(r => { reset(false); host.afterChange(r.message, {stash: true}); })
        .catch(e => { say(e.message, false); if (btn) btn.disabled = false; });
    }
    function doContinue() {
      const b = root.querySelector("#wipContinue");
      if (!b) return;
      b.disabled = true;
      host.post("/api/continue", {})
        .then(r => { reset(true); host.afterChange(r.message); })
        .catch(e => { b.disabled = false; say(e.message, false); });
    }
    function doAbort() {
      const b = root.querySelector("#wipAbort");
      if (!b) return;
      if (!confirm(b.dataset.cmd === "stash" ? t("confirm_abort_stash") : t("confirm_abort", {cmd: b.dataset.cmd}))) return;
      b.disabled = true;
      host.post("/api/abort", {})
        .then(r => { reset(true); host.afterChange(r.message); })
        .catch(e => { b.disabled = false; say(e.message, false); });
    }

    // ---- 部品の html ----
    function resolveBarHtml() {  // 競合ファイルを ours / theirs / 現在の状態 で解決するボタン (チェックしたファイルが対象)
      return '<div class="wipres"><button class="wbtn" data-resolve="ours">' + esc(t("resolve_ours")) + "</button>" +
        '<button class="wbtn" data-resolve="theirs">' + esc(t("resolve_theirs")) + "</button>" +
        '<button class="wbtn" data-resolve="current">' + esc(t("resolve_current")) + "</button>" +
        '<span class="hint">' + esc(t("resolve_hint")) + (rebasing ? " " + esc(t("rebase_side_hint")) : "") + "</span></div>";
    }
    function opBannerHtml(cmd, canWrite) {  // マージなどの途中であることと、中断ボタン
      if (!cmd) return "";
      const stash = cmd === "stash";  // stash の適用が競合したまま (git の操作の途中ではないので、文言が違う)
      return '<div class="wipop"><span>' + esc(stash ? t("op_banner_stash") : t("op_banner", {cmd: cmd})) + "</span>" +
        (canWrite && cmd === "rebase" ? '<button class="wbtn" id="wipContinue">' + esc(t("continue_button")) + "</button>" : "") +
        (canWrite ? '<button class="wbtn danger" id="wipAbort" data-cmd="' + esc(cmd) + '">' + esc(stash ? t("abort_button_stash") : t("abort_button", {cmd: cmd})) + "</button>" : "") + "</div>";
    }

    // ---- 描画 ----
    let lastOpts = {};
    function render(el, o) {
      root = el; lastOpts = o || {};
      bind(root);
      return host.get("/api/status", amendOn ? {amend: 1} : {}).then(s => {
        merging = !!(s.merging || s.applying || s.cherry_picking || s.reverting); rebasing = !!s.rebasing; headMsg = s.head_message || "";
        if (!started) {  // 最初の描画のとき、別の画面で決めた amend の状態を引き継ぐ (差分ページは URL で決まる)
          started = true;
          const wanted = loadAmend();
          if (wanted !== amendOn) {
            if (wanted) { keepPull(); msgNormal = msg; msg = headMsg; } else { msg = msgNormal; msgNormal = ""; }
            amendOn = wanted; sel = {};
            if (amendOn) return render(root, lastOpts);  // 一覧が amend のものになるので、もう一度取る
          }
          if (host.amendOverride) saveAmend();  // 画面の都合で決めたときは、ほかの画面にも同じ状態を引き継ぐ
        }
        const canAmend = !!(s.head && !s.merging && !s.applying && !(s.rebasing && (s.conflicts || []).length));  // 競合していないリベース中 (edit で止まったとき) は、そのコミットを amend できる
        if (amendOn && !s.amend_files) {  // マージ中になったなどで amend できなくなった: 通常のコミットに戻す
          msg = msgNormal; msgNormal = ""; amendOn = false; saveAmend();
        }
        if (!amendOn) keepPull();
        const canCommit = !!host.canWrite();  // 書き込みが無効 (--read-only など) のときはチェック欄を出さない
        const opCmd = s.merging ? "merge" : s.rebasing ? "rebase" : s.applying ? "am" : s.cherry_picking ? "cherry-pick" : s.reverting ? "revert" : s.stash_conflict ? "stash" : null;
        const sections = amendOn
          ? [["amend", t("amend_content"), s.amend_files]]
          : [["conflict", t("conflict"), s.conflicts], ["staged", t("staged"), s.staged],
             ["unstaged", t("unstaged"), s.unstaged], ["untracked", t("untracked"), s.untracked]];
        let list = [], body = "";
        sections.forEach(([kind, title, items]) => {
          if (!items.length) return;
          const group = items.map(f => Object.assign({wip: kind}, f));
          body += "<h3>" + (canCommit ? '<input type="checkbox" class="wipall" data-kind="' + kind + '" title="' + esc(t("select_all")) + '">' : "") +
            title + " (" + group.length + ")</h3>" + (kind === "conflict" && canCommit ? resolveBarHtml() : "") +
            filesTable({}, group, list.length, {t: t, check: canCommit ? checkCell : null, collapse: true, current: host.current});
          list = list.concat(group);
        });
        files = list;
        if (host.onFiles) host.onFiles(files, s);
        const hint = amendOn ? t("amend_hint") : t("wip_hint");
        const box = canCommit && !s.applying && (files.length || amendOn)
          ? '<div class="wipbox">' +
            '<textarea id="wipMsg" placeholder="' + esc(t("wip_commit_message")) + '"></textarea>' +
            (!amendOn ? '<input type="text" id="wipBranch" placeholder="' + esc(t("wip_commit_branch_placeholder")) + '" aria-label="' + esc(t("wip_commit_branch")) + '">' : "") +
            (canAmend ? '<label class="amendrow"><input type="checkbox" id="wipAmendCk"' + (amendOn ? " checked" : "") + ">" + esc(t("amend_checkbox")) + "</label>" : "") +
            '<div class="arow">' + (!amendOn ? '<button class="wbtn" id="wipStage">' + esc(t("stage_button")) + "</button>" : "") +
            '<button class="wbtn" id="wipCommit">' + esc(t("wip_commit")) + "</button>" +
            (!amendOn && !opCmd ? '<button class="wbtn" id="wipStash" title="' + esc(t("tip_wip_stash")) + '">' + esc(t("wip_stash")) + "</button>" : "") +
            (!amendOn && !opCmd ? '<button class="wbtn danger" id="wipDiscard">' + esc(t("wip_discard")) + "</button>" : "") +
            (!amendOn ? '<button class="wbtn danger" id="wipRm" title="' + esc(t("tip_wip_rm")) + '">' + esc(t("wip_rm")) + "</button>" : "") +
            '<span class="hint">' + esc(hint) + "</span></div>" +
            '<div class="amsg" id="wipMsgOut"></div></div>' : "";
        root.innerHTML = (o && o.top ? o.top(s, files) : "") + opBannerHtml(opCmd, canCommit) + (o && o.mid ? o.mid(s, files) : "") +
          '<div class="wip-panel">' + (body || '<p class="hint">' + esc(t("no_changes")) + "</p>") + box +
          (s.truncated ? '<p class="hint">' + esc(t("truncated_files")) + "</p>" : "") + "</div>";
        const ta = root.querySelector("#wipMsg");
        if (ta) ta.value = msg;
        const bi = root.querySelector("#wipBranch");
        if (bi) bi.value = branchVal;
        if (o && o.okMsg) say(o.okMsg, true);
        sync();
        return {status: s, files: files};
      }).catch(e => {
        root.innerHTML = o && o.error ? o.error(e) : '<p class="hint">' + esc(t("error")) + esc(e.message) + "</p>";
      });
    }

    // ---- 操作の受け付け (root に 1 度だけ付ける) ----
    function bind(el) {
      if (el.__wipPanelBound) return;
      el.__wipPanelBound = true;
      el.addEventListener("change", ev => {
        if (ev.target.id === "wipAmendCk") { setAmend(ev.target.checked); return; }
        const ck = ev.target.closest(".wipck"), all = ev.target.closest(".wipall"), dc = ev.target.closest(".wipdir");
        if (ck) setCk(ck, ck.checked);
        else if (dc) cks(dc.dataset.kind).filter(x => x.dataset.dir === dc.dataset.dir).forEach(x => setCk(x, dc.checked));
        else if (all) cks(all.dataset.kind).forEach(x => setCk(x, all.checked));
        else return;
        sync();
      });
      el.addEventListener("input", ev => {
        if (ev.target.id === "wipMsg") msg = ev.target.value;
        else if (ev.target.id === "wipBranch") branchVal = ev.target.value;
        else return;
        keepSave();
      });
      el.addEventListener("click", ev => {
        const hit = id => ev.target.closest(id);
        if (hit("#wipCommit")) { doCommit(); return; }
        if (hit("#wipStage")) { doStage(); return; }
        if (hit("#wipStash")) { doStash(); return; }
        if (hit("#wipDiscard")) { doDiscard(); return; }
        if (hit("#wipRm")) { doRm(); return; }
        if (hit("#wipAbort")) { doAbort(); return; }
        if (hit("#wipContinue")) { doContinue(); return; }
        const rb = hit("[data-resolve]");
        if (rb) { doResolve(rb.dataset.resolve); return; }
        const dr = hit("tr.drow");
        if (dr && dr.closest(".wip-panel")) {  // ディレクトリ行: 配下のファイルを表示 / 非表示
          if (ev.target.closest("input")) return;
          const k = dr.dataset.kind + "|" + dr.dataset.dir, open = !openDirs.has(k);
          if (open) openDirs.add(k); else openDirs.delete(k);
          saveOpenDirs();
          dr.querySelector(".dtog").textContent = open ? "▼" : "▶";
          dr.closest("table").querySelectorAll("tr.frow").forEach(r => {
            if (r.dataset.dir === dr.dataset.dir) r.classList.toggle("dhide", !open);
          });
          return;
        }
        const tr = hit("tr.frow");
        if (tr && tr.closest(".wip-panel") && !ev.target.closest("a, input, td.fk"))  // 行のどこをクリックしても差分を開く
          location.href = diffUrl({}, files[+tr.dataset.k]);
      });
    }

    return {render: render, say: say, reset: reset, amend: () => amendOn, files: () => files};
  }

  return {create: create, filesTable: filesTable, diffUrl: diffUrl, dirOf: dirOf, badge: badge};
})();
