// Markdown の編集で、画像などのファイルをドロップしたり、図 (スクリーンショットなど) をペーストしたら、
// その Markdown のあるディレクトリの attachments/ へアップロードして、Markdown のリンクを貼る。
//   画像 → ![名前](attachments/名前.png)、それ以外 → [名前.pdf](attachments/名前.pdf)
// アップロードは、ファイルのドロップと同じ /api/file-upload (作業ツリーに書く。コミットは自分でする)。
// 同じ名前が既にあるときは、上書きせず、名前を変える (a.png → a-2.png)。名前の無いペーストの画像は image-日時.png。
// プレビュー (編集画面の右) の相対パスの画像は /raw で出すので、アップロードが終われば、そのまま表示される。
const MdAttach = (function () {
  const DIR = "attachments";
  const MIME_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp", "image/svg+xml": "svg",
                    "image/bmp": "bmp", "image/avif": "avif"};
  const IMAGE_EXT = /\.(png|jpe?g|gif|webp|svg|bmp|avif)$/i;
  const TRIES = 100;  // 同じ名前があるとき、名前を変えて試す回数

  function pad(n) { return String(n).padStart(2, "0"); }
  function stamp(d) {
    return d.getFullYear() + pad(d.getMonth() + 1) + pad(d.getDate()) + "-" + pad(d.getHours()) + pad(d.getMinutes()) + pad(d.getSeconds());
  }
  function cleanName(name) {  // パス区切りは落として名前だけにする。ファイル名に使えない文字は _ にする
    const base = String(name || "").replace(/\\/g, "/").split("/").pop();
    return base.replace(/[\u0000-\u001f<>:"|?*]/g, "_").replace(/^[.\s]+|[.\s]+$/g, "");
  }
  // アップロードするファイル名。名前の無い画像 (ペーストした図は image.png) は image-日時.png。拡張子が無い画像は MIME から付ける
  function attachName(file, now) {
    const mime = (file && file.type) || "";
    const m = /^(.*?)(\.[A-Za-z0-9]+)?$/.exec(cleanName(file && file.name));
    let stem = m[1], dot = m[2] || "";
    if (mime.indexOf("image/") === 0 && (!stem || /^image$/i.test(stem))) stem = "image-" + stamp(now || new Date());
    if (!stem) stem = "file-" + stamp(now || new Date());
    if (!dot && MIME_EXT[mime]) dot = "." + MIME_EXT[mime];
    return stem + dot;
  }
  function withSuffix(name, n) {  // a.png → a-2.png
    const i = name.lastIndexOf(".");
    return i > 0 ? name.slice(0, i) + "-" + n + name.slice(i) : name + "-" + n;
  }
  // リンクの中でそのまま書くと壊れる文字だけ %xx にする (日本語などはそのまま。プレビューは decodeURIComponent で戻す)
  function linkPart(name) {
    return name.replace(/[ %()<>\[\]#?\\]/g, c => "%" + c.charCodeAt(0).toString(16).toUpperCase().padStart(2, "0"));
  }
  function isImage(file, name) {
    return ((file && file.type) || "").indexOf("image/") === 0 || IMAGE_EXT.test(name || "");
  }
  function markdownFor(file, name) {  // name: アップロードしたファイル名
    const rel = DIR + "/" + linkPart(name);
    if (!isImage(file, name)) return "[" + name.replace(/[\[\]\\]/g, " ") + "](" + rel + ")";
    return "![" + name.replace(/\.[^.]*$/, "").replace(/[\[\]\\]/g, " ").trim() + "](" + rel + ")";
  }
  function attachDir(mdPath) {  // その Markdown と同じ階層の attachments
    const i = String(mdPath || "").lastIndexOf("/");
    return (i < 0 ? "" : mdPath.slice(0, i + 1)) + DIR;
  }
  function fileToBase64(file) {
    return new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => { const s = String(r.result || ""), i = s.indexOf(","); resolve(i >= 0 ? s.slice(i + 1) : s); };
      r.onerror = () => reject(r.error || new Error("read failed"));
      r.readAsDataURL(file);
    });
  }

  // 場所取りの印 (見えない HTML コメント) を、リンクに置き換える。text が空なら印を消す (行に印だけのときは、その行ごと)。
  // アップロードしている間に打ち込まれても、印を探して置き換えるので、位置はずれない
  function replaceMark(view, mark, text) {
    if (!view.dom.isConnected) return false;  // もう編集画面を閉じている
    const doc = view.state.doc, idx = doc.toString().indexOf(mark);
    if (idx < 0) return false;  // 印を消された
    const from = idx;
    let to = idx + mark.length;
    if (!text && (idx === 0 || doc.sliceString(idx - 1, idx) === "\n") && doc.sliceString(to, to + 1) === "\n") to++;
    const sel = view.state.selection.main, keep = sel.empty && sel.head === idx + mark.length;  // 印のすぐ後ろにあるカーソルは、リンクの後ろへ
    const spec = {changes: {from: from, to: to, insert: text}};
    if (keep) spec.selection = {anchor: from + text.length};
    view.dispatch(spec);
    return true;
  }

  // host: エディタを入れた要素。deps: {getView(): EditorView, mdPath(): 編集中のパス, t(key, params), status(msg, kind, sticky), refresh()}
  function bind(host, deps) {
    let seq = 0;

    async function uploadUnique(dir, file) {
      const data = await fileToBase64(file), base = attachName(file);
      for (let n = 1; n <= TRIES; n++) {
        const name = n === 1 ? base : withSuffix(base, n), path = dir + "/" + name;
        const r = await fetch("/api/file-upload", {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({path: path, data: data, overwrite: false}),
        });
        if (r.ok) return name;
        const msg = await r.text();
        if (r.status === 400 && msg.indexOf(path) >= 0) continue;  // 同じ名前が既にある (上書きしない) → 別の名前で
        throw new Error(msg || String(r.status));
      }
      throw new Error(deps.t("upload_exists", {path: dir + "/" + base}));
    }

    async function attach(files, from, to) {
      const view = deps.getView();
      if (!view || !files.length) return;
      const id = ++seq, dir = attachDir(deps.mdPath());
      const marks = files.map((f, i) => "<!--attach-" + id + "-" + i + "-->");
      const marked = marks.join("\n");
      // 先に、場所取りの印を入れる (ドロップした場所 / ペーストしたカーソルの位置)
      view.dispatch({changes: {from: from, to: to, insert: marked}, selection: {anchor: from + marked.length}, scrollIntoView: true,
                     userEvent: "input.paste"});
      deps.status(deps.t("upload_running", {n: files.length}), "progress");
      let ok = 0, error = "";
      for (let i = 0; i < files.length; i++) {
        try {
          const name = await uploadUnique(dir, files[i]);
          replaceMark(view, marks[i], markdownFor(files[i], name));
          ok++;
        } catch (e) {
          error = e.message || String(e);
          for (let j = i; j < files.length; j++) replaceMark(view, marks[j], "");  // 残りの印は消す
          break;
        }
      }
      if (ok && deps.refresh) deps.refresh();  // 左の一覧と WIP の変更に、attachments を出す
      if (error) deps.status(deps.t("upload_failed", {error: error}), "error", true);
      else deps.status(deps.t("upload_done", {n: ok}), "success");
    }

    const hasFiles = ev => !!(ev.dataTransfer && [...(ev.dataTransfer.types || [])].indexOf("Files") >= 0);
    const toFiles = list => [...(list || [])].filter(f => f && typeof f.size === "number");

    // ペースト: 画像だけのとき (スクリーンショット、画像のコピーなど) だけ。文字も入っているとき (表計算のセルなど) は、文字を貼る
    const onPaste = ev => {
      const cd = ev.clipboardData, files = cd ? toFiles(cd.files) : [];
      if (!files.length || cd.getData("text/plain")) return;
      const view = deps.getView();
      if (!view) return;
      ev.preventDefault(); ev.stopPropagation();  // エディタ (と、画面全体のドロップの受け口) には渡さない
      const sel = view.state.selection.main;
      attach(files, sel.from, sel.to);
    };
    const onDragOver = ev => {
      if (!hasFiles(ev)) return;
      ev.preventDefault(); ev.stopPropagation();
      ev.dataTransfer.dropEffect = "copy";
    };
    const onDrop = ev => {
      const files = hasFiles(ev) ? toFiles(ev.dataTransfer.files) : [];
      const view = deps.getView();
      if (!files.length || !view) return;
      ev.preventDefault(); ev.stopPropagation();  // CodeMirror (ファイルの中身を貼ってしまう) と、画面全体のドロップ (今のディレクトリへ置く) には渡さない
      let pos = view.state.selection.main.head;
      try { const p = view.posAtCoords({x: ev.clientX, y: ev.clientY}); if (p != null) pos = p; } catch (e) { /* 座標から位置を引けないときは、カーソルの位置 */ }
      attach(files, pos, pos);
    };
    host.addEventListener("paste", onPaste, true);
    host.addEventListener("dragover", onDragOver, true);
    host.addEventListener("drop", onDrop, true);
    return () => {
      host.removeEventListener("paste", onPaste, true);
      host.removeEventListener("dragover", onDragOver, true);
      host.removeEventListener("drop", onDrop, true);
    };
  }

  return {DIR, attachName, withSuffix, linkPart, markdownFor, attachDir, bind};
})();
