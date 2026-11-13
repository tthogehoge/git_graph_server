// 差分ページの「差分エディタ」ボタン。いま見ているファイルの差分を、CodeMirror の差分エディタ (変更前 | 変更後) で見る。
// 表示そのもの (内容の取得・エディタ・ツールバー) は diff_editor_view.js。ここは、テキスト差分との切り替えだけ。
// エディタ (1MB ほど) はボタンを押したときに読み込む。内容は /api/diff-sources に、このページのクエリをそのまま渡して取る。
// クエリに editor=1 があるときも、最初から差分エディタで出す。
// 選んだ表示は、このタブの中で覚えておく (左のファイル一覧で別のファイルへ移っても、差分エディタのまま)。
// 文言 T と fmt は、先に入れてあるダイアログの部品 (diff_dialog.js) のもの。
(function () {
  const btn = document.getElementById("dedit"), box = document.getElementById("dbox");
  if (!btn || !box) return;
  const KEY = "git-graph-diff-editor";
  const text = document.querySelector("#main .diff");
  let shown = false, scriptLoad = null;

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
  const view = createDiffEditorView({
    box: box,
    text: (key, values) => fmt(T[key], values),
    loadScript: loadScript,
    phrases: () => ({
      "Find": T.editor_find, "Replace": T.editor_replace, "next": T.editor_next, "previous": T.editor_previous, "all": T.editor_all,
      "match case": T.editor_match_case, "regexp": T.editor_regexp, "by word": T.editor_by_word, "replace": T.editor_replace_one,
      "replace all": T.editor_replace_all, "close": T.editor_close, "Go to line": T.editor_goto_line, "go": T.editor_go,
    }),
  });
  function show() {
    shown = true;
    remember(true);
    text.hidden = true;
    box.hidden = false;
    btn.textContent = T.diff_editor_close;
    btn.title = T.tip_diff_editor_close;
    view.load(new URLSearchParams(location.search));
  }
  function hide() {
    shown = false;
    remember(false);
    view.close();
    box.hidden = true;
    text.hidden = false;
    btn.textContent = T.diff_editor;
    btn.title = T.tip_diff_editor;
    const url = new URL(location.href);  // editor=1 で開かれたときは、戻したあとの再読み込みでエディタに戻らないよう消す
    if (url.searchParams.has("editor") && history.replaceState) {
      url.searchParams.delete("editor");
      history.replaceState(null, "", url.pathname + url.search + url.hash);
    }
  }
  btn.onclick = () => { if (shown) hide(); else show(); };
  if (isOn() || new URLSearchParams(location.search).get("editor") === "1") show();
})();
