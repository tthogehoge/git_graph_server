// 差分ページの「差分エディタ」ボタン。いま見ているファイルの差分を、CodeMirror の差分エディタ (変更前 | 変更後) で見る。
// 内容は /api/diff-sources (このページのクエリをそのまま渡す) から取り、エディタ (1MB ほど) はボタンを押したときに読み込む。
// 選んだ表示は、このタブの中で覚えておく (左のファイル一覧で別のファイルへ移っても、差分エディタのまま)。
// 文言 T と fmt は、先に入れてあるダイアログの部品 (diff_dialog.js) のもの。
(function () {
  const btn = document.getElementById("dedit"), box = document.getElementById("dbox");
  if (!btn || !box) return;
  const KEY = "git-graph-diff-editor";
  const text = document.querySelector("#main .diff");
  const host = document.getElementById("dhost"), notes = document.getElementById("dnotes"), count = document.getElementById("dcount");
  const prev = document.getElementById("dprev"), next = document.getElementById("dnext"), collapse = document.getElementById("dcollapse");
  const labels = document.getElementById("dlabels"), labelOld = document.getElementById("dlold"), labelNew = document.getElementById("dlnew");
  let editor = null, data = null, shown = false, scriptLoad = null;

  const isOn = () => { try { return sessionStorage.getItem(KEY) === "1"; } catch (e) { return false; } };
  const remember = on => {
    try { if (on) sessionStorage.setItem(KEY, "1"); else sessionStorage.removeItem(KEY); } catch (e) { /* 覚えられなくても、表示は切り替わる */ }
  };
  function loadScript() {  // GGText (CodeMirror 6 + 言語 + Vim)。ファイル表示と同じバンドルを、最初に使うときに読み込む
    if (window.GGText) return Promise.resolve();
    if (!scriptLoad) {
      scriptLoad = new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = "/static/vendor/text_editor.js";
        script.onload = resolve;
        script.onerror = () => { scriptLoad = null; reject(new Error(T.editor_load_failed)); };  // 次の機会に読み直せるようにする
        document.head.appendChild(script);
      });
    }
    return scriptLoad;
  }
  const phrases = () => ({
    "Find": T.editor_find, "Replace": T.editor_replace, "next": T.editor_next, "previous": T.editor_previous, "all": T.editor_all,
    "match case": T.editor_match_case, "regexp": T.editor_regexp, "by word": T.editor_by_word, "replace": T.editor_replace_one,
    "replace all": T.editor_replace_all, "close": T.editor_close, "Go to line": T.editor_goto_line, "go": T.editor_go,
  });
  function fetchSources() {
    return fetch("/api/diff-sources" + location.search)
      .then(r => r.ok ? r.json() : r.text().then(m => { throw new Error(m || r.status); }));
  }
  function setNotes(lines, isError) {
    notes.textContent = "";
    for (const line of lines) {
      const div = document.createElement("div");
      div.className = isError ? "meta hint err" : "meta hint";
      div.textContent = line;
      notes.appendChild(div);
    }
  }
  function closeEditor() {
    if (editor) editor.destroy();
    editor = null;
    host.textContent = "";
  }
  function setControls(chunks) {
    prev.disabled = next.disabled = !chunks;
    count.textContent = chunks ? fmt(T.diff_editor_chunks, {n: chunks}) : T.diff_editor_none;
  }
  const sideName = side => side.label + (side.exists ? "" : " " + T.diff_editor_missing);
  function build() {  // data (両側の内容) から、エディタを作り直す (折りたたみの切り替えでも使う)
    closeEditor();
    const [a, b] = [data.old, data.new];
    const lines = [];
    labels.hidden = true;
    prev.disabled = next.disabled = true;
    count.textContent = "";
    if (a.binary || b.binary) {
      lines.push(T.diff_editor_binary);
    } else if (!a.exists && !b.exists) {
      lines.push(T.diff_editor_unavailable);
    } else {
      if (a.truncated || b.truncated) lines.push(T.large_file_head);
      if (a.exists && b.exists && a.eol && b.eol && a.eol !== b.eol) {  // エディタは改行を LF として比べるので、改行コードだけの違いは出ない
        lines.push(fmt(T.diff_editor_eol, {old: a.eol.toUpperCase(), new: b.eol.toUpperCase()}));
      }
      labelOld.textContent = sideName(a);
      labelNew.textContent = sideName(b);
      labels.hidden = false;
      editor = GGText.createDiffEditor({parent: host, old: a.content, new: b.content, path: data.path, collapse: collapse.checked, phrases: phrases()});
      setControls(editor.chunkCount());
    }
    setNotes(lines, false);
  }
  function show() {
    shown = true;
    remember(true);
    text.hidden = true;
    box.hidden = false;
    btn.textContent = T.diff_editor_close;
    btn.title = T.tip_diff_editor_close;
    if (data) { build(); return; }
    setNotes([], false);
    Promise.all([fetchSources(), loadScript()]).then(([r]) => {
      data = r;
      if (shown) build();  // 読み込んでいる間に、テキスト差分へ戻されていたら作らない
    }).catch(e => { if (shown) setNotes([e.message], true); });
  }
  function hide() {
    shown = false;
    remember(false);
    closeEditor();
    box.hidden = true;
    text.hidden = false;
    btn.textContent = T.diff_editor;
    btn.title = T.tip_diff_editor;
  }
  btn.onclick = () => { if (shown) hide(); else show(); };
  prev.onclick = () => { if (editor) editor.jump(-1); };
  next.onclick = () => { if (editor) editor.jump(1); };
  collapse.onchange = () => { if (data && shown) build(); };
  if (isOn()) show();
})();
