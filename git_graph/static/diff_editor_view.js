// 差分エディタ (変更前 | 変更後) の表示。/diff の「差分エディタ」ボタンと、/files の右ペインの差分で共用する。
// createDiffEditorView({box, text, phrases, loadScript}) は、box の中にツールバー・注意書き・変更前 / 変更後の見出し・エディタを作り、
//   load(params): /api/diff-sources (params は /diff と同じ hash / from・to / wip / path / old) から読み込んで出す。古い読み込みは捨てる。
//   close(): エディタを閉じて内容を捨てる。
// 引数: box (中身を作る要素) / text(key, values) (文言) / phrases() (検索パネルなどの文言) / loadScript() (GGText を読み込む Promise)。
// エディタ本体 (GGText.createDiffEditor) は 1MB ほどあるので、ページには埋め込まず、最初に load するときに loadScript で読み込む。
function createDiffEditorView(opts) {
  const box = opts.box, text = opts.text;
  if (!document.getElementById("diffEditorViewCss")) {  // 見た目は、使うページ (/diff, /files) に依らず同じ
    const style = document.createElement("style");
    style.id = "diffEditorViewCss";
    style.textContent = [
      ".de-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin: 8px 0 4px; }",
      ".de-bar button { background: #e8eefc; border: 1px solid #b8c7ee; padding: 4px 8px; border-radius: 3px; cursor: pointer; }",
      ".de-bar button:hover { background: #d6e2fb; }",
      ".de-bar button:disabled { opacity: .5; cursor: default; }",
      ".de-bar label { display: inline-flex; align-items: center; gap: 4px; cursor: pointer; }",
      ".de-count { color: #666; }",
      ".de-note { color: #666; margin: 4px 0; white-space: pre-wrap; }",
      ".de-note.err { color: #c00; }",
      ".de-labels { display: flex; margin-top: 8px; border: 1px solid #ddd; border-bottom: none; background: #f6f8fa; font-weight: bold; }",
      ".de-labels[hidden] { display: none; }",
      ".de-labels span { flex: 1 1 0; min-width: 0; padding: 3px 8px; word-break: break-all; }",
      ".de-labels span + span { border-left: 1px solid #ddd; }",
      ".de-host { border: 1px solid #ddd; }",
      ".de-host:empty { border: none; }",
    ].join("\n");
    document.head.appendChild(style);
  }
  box.textContent = "";
  const make = (tag, cls, label) => {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (label !== undefined) el.textContent = label;
    return el;
  };
  const bar = make("div", "de-bar"), prev = make("button", "", text("diff_editor_prev")), next = make("button", "", text("diff_editor_next"));
  const count = make("span", "de-count"), collapseLabel = make("label"), collapse = make("input");
  prev.type = next.type = "button";
  collapse.type = "checkbox";
  collapse.checked = true;
  collapseLabel.append(collapse, document.createTextNode(text("diff_editor_collapse")));
  bar.append(prev, next, count, collapseLabel);
  const notes = make("div"), labels = make("div", "de-labels"), labelOld = make("span"), labelNew = make("span"), host = make("div", "de-host");
  labels.hidden = true;
  labels.append(labelOld, labelNew);
  box.append(bar, notes, labels, host);
  let editor = null, data = null, seq = 0;

  function setNotes(lines, isError) {
    notes.textContent = "";
    for (const line of lines) notes.appendChild(make("div", isError ? "de-note err" : "de-note", line));
  }
  function clear() {
    if (editor) editor.destroy();
    editor = null;
    host.textContent = "";
    labels.hidden = true;
    prev.disabled = next.disabled = true;
    count.textContent = "";
  }
  const sideName = side => side.label + (side.exists ? "" : " " + text("diff_editor_missing"));
  function render() {  // data (両側の内容) から、エディタを作り直す (折りたたみの切り替えでも使う)
    clear();
    const [a, b] = [data.old, data.new];
    const lines = [];
    if (a.binary || b.binary) {
      lines.push(text("diff_editor_binary"));
    } else if (!a.exists && !b.exists) {
      lines.push(text("diff_editor_unavailable"));
    } else {
      if (a.truncated || b.truncated) lines.push(text("large_file_head"));
      if (a.exists && b.exists && a.eol && b.eol && a.eol !== b.eol) {  // エディタは改行を LF として比べるので、改行コードだけの違いは出ない
        lines.push(text("diff_editor_eol", {old: a.eol.toUpperCase(), new: b.eol.toUpperCase()}));
      }
      labelOld.textContent = sideName(a);
      labelNew.textContent = sideName(b);
      labels.hidden = false;
      editor = GGText.createDiffEditor({parent: host, old: a.content, new: b.content, path: data.path, collapse: collapse.checked, phrases: opts.phrases()});
      const chunks = editor.chunkCount();
      prev.disabled = next.disabled = !chunks;
      count.textContent = chunks ? text("diff_editor_chunks", {n: chunks}) : text("diff_editor_none");
    }
    setNotes(lines, false);
  }
  prev.onclick = () => { if (editor) editor.jump(-1); };
  next.onclick = () => { if (editor) editor.jump(1); };
  collapse.onchange = () => { if (data) render(); };
  return {
    load: params => {
      const mine = ++seq;
      data = null;
      clear();
      setNotes([], false);
      const url = "/api/diff-sources?" + (params instanceof URLSearchParams ? params : new URLSearchParams(params));
      const fetched = fetch(url).then(r => r.ok ? r.json() : r.text().then(m => { throw new Error(m || r.status); }));
      return Promise.all([fetched, opts.loadScript()]).then(([r]) => {
        if (mine !== seq) return;  // 読み込んでいる間に、閉じられたか、別のファイルを読み込み直した
        data = r;
        render();
      }).catch(e => { if (mine === seq) setNotes([e.message], true); });
    },
    close: () => { seq++; data = null; clear(); setNotes([], false); },
  };
}
