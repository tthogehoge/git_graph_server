import ctypes
import inspect
import io
import os
import platform
import queue
import re
import threading
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Windows の embeddable 版 Python などでも git_graph パッケージを import できるようにする
sys.path.insert(0, str(Path(__file__).resolve().parent))

import webview

URL_PATTERN = re.compile(r"http://127\.0\.0\.1:\d+/?")
FROZEN = getattr(sys, "frozen", False)  # PyInstaller で exe 化されている
APP_ID = "git_graph.webview"
ICON_DIR = Path(__file__).resolve().parent
url_queue = queue.Queue()
_DEBUG = False  # --debug で pywebview の開発者ツールを有効にする


def build_command():
    # サーバは subprocess ではなく同じプロセスのスレッドで動かす (exe 化すると sys.executable が自分自身になり、
    # 自分を再起動し続けてしまうため)。そのため先頭はダミーの argv[0]。
    cmd = ["git_graph_server.py", "--desktop"]  # --desktop: 外部エディタ / 差分ツールのボタンを出す

    # exe 化すると設定ファイルの既定の場所が一時展開先になるので、exe の隣にあるものを使う
    if FROZEN:
        conf = Path(sys.executable).resolve().parent / "git_graph_server.json"
        if conf.is_file():
            cmd += ["--config", str(conf)]

    if platform.system() == "Windows":
        cmd += [
            "--font", "Yu Gothic",
        ]
    else:
        cmd += [
        ]

    # 引数を渡す
    cmd += sys.argv[1:]  # [repo] [rev ...]

    return cmd


def _add_bundled_graphviz_to_path():
    """同梱の Graphviz (graphviz/bin) があれば PATH の先頭に足す。サーバは dot を PATH から探すため。
    探す場所: exe (または このスクリプト) と同じフォルダ → PyInstaller の一時展開先 (--add-data で同梱したとき)。"""
    bases = [Path(sys.executable).resolve().parent] if FROZEN else [Path(__file__).resolve().parent]
    if getattr(sys, "_MEIPASS", None):
        bases.append(Path(sys._MEIPASS))
    for base in bases:
        for name in ("graphviz", "Graphviz"):
            bindir = base / name / "bin"
            if bindir.is_dir():
                os.environ["PATH"] = str(bindir) + os.pathsep + os.environ.get("PATH", "")
                return str(bindir)
    return None


class _StdoutTee(io.TextIOBase):
    """サーバの標準出力から URL を拾う。--noconsole の exe では sys.stdout が None なので、それも吸収する。"""

    def __init__(self, orig):
        self.orig = orig
        self.found = False
        self.buf = ""

    def writable(self):
        return True

    def write(self, s):
        if self.orig is not None:
            try:
                self.orig.write(s)
            except Exception:
                pass
        if not self.found:
            self.buf += s
            m = URL_PATTERN.search(self.buf)
            if m:
                self.found = True
                url_queue.put(m.group(0))
        return len(s)

    def flush(self):
        if self.orig is not None:
            try:
                self.orig.flush()
            except Exception:
                pass


def child_command(path, start_path=""):
    """submodule を別のウィンドウ (別プロセス) で開くコマンド。子はこのアプリをもう一つ起動する (リポジトリは submodule のパス)。
    ポートは渡さない: 子は既定のポートを試し、使用中なら空いているポートを自分で選ぶ (同じポートは使えない)。"""
    from git_graph.launcher import forwarded_args
    base = [sys.executable] if FROZEN else [sys.executable, str(Path(__file__).resolve())]
    args = base + [path] + forwarded_args() + (["--start-path", start_path] if start_path else [])
    if _DEBUG:
        args.append("--debug")
    return args


class ServerThread(threading.Thread):
    """git_graph サーバを同じプロセスのスレッドで動かす。/api/restart されたときは同じポートで起動し直す。"""

    def __init__(self, argv):
        super().__init__(daemon=True)
        self.argv = argv
        self.restart = False
        self.error = None

    def _request_restart(self):  # サーバの /api/restart から呼ばれる (os.execv ではアプリごと置き換わってしまうため)
        from git_graph import app_state
        args = [a for a in self.argv[1:] if a != "--open"]
        if "--port" not in args:
            args = ["--port", str(app_state.OPTS.port)] + args  # 実際に使用中のポートを引き継ぐ
        self.argv = self.argv[:1] + args
        self.restart = True
        app_state.SERVER.shutdown()

    def run(self):
        try:
            while True:
                # runpy で git_graph.__main__ を実行すると、PyInstaller は __main__ を同梱しないため exe で動かない。
                # 直接 import して main() を呼ぶ (関数内の import でも PyInstaller は依存として拾う)
                from git_graph import app_state
                from git_graph.server import main as server_main
                app_state.RESTART_HOOK = self._request_restart
                app_state.CHILD_CMD = child_command
                self.restart = False
                sys.argv = list(self.argv)
                try:
                    server_main()
                except SystemExit as e:  # サーバ側の sys.exit(メッセージ) は、スレッドを終わらせるだけにする
                    if e.code not in (None, 0):
                        self.error = e.code
                        print(e.code, file=sys.stderr)
                if not self.restart:
                    break
                for name in [n for n in sys.modules if n == "git_graph" or n.startswith("git_graph.")]:
                    del sys.modules[name]  # 再起動では、ソースの更新も反映されるよう読み込み直す
        except BaseException as e:
            self.error = e
            raise
        finally:
            url_queue.put(None)  # 起動に失敗したときに、URL の待ち受けを解除する


_GESTURE_NAV_JS = """
(function () {
    if (window._gestureNavInstalled) return;
    window._gestureNavInstalled = true;

    const THRESHOLD = 50;   // ドラッグ判定の最小移動量 (px)
    let startX = null;
    let startY = null;
    let didNavigate = false;

    document.addEventListener('mousedown', (e) => {
        if (e.button === 2) {
            startX = e.clientX;
            startY = e.clientY;
            didNavigate = false;
        }
    }, true);

    document.addEventListener('mouseup', (e) => {
        if (e.button === 2 && startX !== null && startY !== null) {
            const dx = e.clientX - startX;
            const dy = e.clientY - startY;
            if (Math.max(Math.abs(dx), Math.abs(dy)) >= THRESHOLD) {
                didNavigate = true;
                if (Math.abs(dx) > Math.abs(dy)) {
                    if (dx < 0) history.back();
                    else        history.forward();
                } else {
                    location.reload();
                }
            }
            startX = null;
            startY = null;
        }
    }, true);

    document.addEventListener('contextmenu', (e) => {
        if (didNavigate) {
            e.preventDefault();
            didNavigate = false;
        }
    }, true);
})();
"""

_FIND_BAR_JS = """
(function () {
    if (window._findBarInstalled) return;
    window._findBarInstalled = true;

    let bar = null, input = null, status = null;
    let matches = [];
    let idx = -1;
    let styleEl = null;

    function ensureStyle() {
        if (styleEl) return;
        styleEl = document.createElement("style");
        styleEl.textContent =
            "mark.gg-find { background: #ffe566; color: inherit; padding: 0; border-radius: 1px; }" +
            "mark.gg-find.gg-find-cur { background: #ff9632; outline: 1px solid #c60; }";
        document.head.appendChild(styleEl);
    }

    function ensureBar() {
        if (bar) return;
        ensureStyle();
        bar = document.createElement("div");
        bar.id = "ggFindBar";
        bar.style.cssText =
            "position:fixed;top:8px;right:8px;z-index:10000;display:none;align-items:center;gap:4px;" +
            "padding:4px 8px;background:#f6f6f6;border:1px solid #bbb;border-radius:4px;" +
            "box-shadow:0 2px 8px #0003;font:13px system-ui,sans-serif";
        input = document.createElement("input");
        input.type = "search";
        input.placeholder = "Find in page";
        input.autocomplete = "off";
        input.spellcheck = false;
        input.style.cssText = "width:180px;padding:3px 6px;border:1px solid #bbb;border-radius:3px;font:inherit";
        status = document.createElement("span");
        status.style.cssText = "min-width:4.5em;text-align:center;color:#555;font-size:12px";
        const prev = document.createElement("button");
        prev.type = "button"; prev.textContent = "\u2191"; prev.title = "Previous";
        const next = document.createElement("button");
        next.type = "button"; next.textContent = "\u2193"; next.title = "Next";
        const close = document.createElement("button");
        close.type = "button"; close.textContent = "\u00d7"; close.title = "Close";
        [prev, next, close].forEach(function (b) {
            b.style.cssText = "padding:2px 8px;border:1px solid #bbb;border-radius:3px;background:#fff;cursor:pointer;font:inherit";
        });
        bar.appendChild(input);
        bar.appendChild(status);
        bar.appendChild(prev);
        bar.appendChild(next);
        bar.appendChild(close);
        document.body.appendChild(bar);

        var timer = null;
        input.addEventListener("input", function () {
            clearTimeout(timer);
            timer = setTimeout(function () { search(input.value); }, 120);
        });
        input.addEventListener("keydown", function (e) {
            if (e.key === "Enter") { e.preventDefault(); go(e.shiftKey ? -1 : 1); }
            else if (e.key === "Escape") { e.preventDefault(); hide(); }
        });
        prev.addEventListener("click", function () { go(-1); });
        next.addEventListener("click", function () { go(1); });
        close.addEventListener("click", hide);
    }

    function clearMarks() {
        document.querySelectorAll("mark.gg-find").forEach(function (m) {
            var p = m.parentNode;
            if (!p) return;
            while (m.firstChild) p.insertBefore(m.firstChild, m);
            p.removeChild(m);
            p.normalize();
        });
        matches = [];
        idx = -1;
    }

    function collectTextNodes(root) {
        var out = [];
        var skip = { SCRIPT:1, STYLE:1, NOSCRIPT:1, TEXTAREA:1, INPUT:1, SELECT:1 };
        var tw = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
            acceptNode: function (n) {
                if (!n.nodeValue || !n.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
                var p = n.parentElement;
                while (p) {
                    if (skip[p.tagName] || p.id === "ggFindBar") return NodeFilter.FILTER_REJECT;
                    if (p.hasAttribute && p.hasAttribute("hidden")) return NodeFilter.FILTER_REJECT;
                    p = p.parentElement;
                }
                return NodeFilter.FILTER_ACCEPT;
            }
        });
        var n;
        while ((n = tw.nextNode())) out.push(n);
        return out;
    }

    function search(q) {
        clearMarks();
        q = (q || "").trim();
        if (!q) {
            status.textContent = "";
            input.style.outline = "";
            return;
        }
        var qLower = q.toLowerCase();
        var nodes = collectTextNodes(document.body);
        var byNode = [];
        nodes.forEach(function (node) {
            var t = node.nodeValue;
            var lower = t.toLowerCase();
            var from = 0;
            var list = [];
            while (true) {
                var i = lower.indexOf(qLower, from);
                if (i < 0) break;
                list.push({ start: i, end: i + q.length });
                from = i + Math.max(1, q.length);
            }
            if (list.length) byNode.push({ node: node, list: list, text: t });
        });
        var marks = [];
        byNode.forEach(function (item) {
            var list = item.list;
            var text = item.text;
            var frag = document.createDocumentFragment();
            var last = 0;
            list.forEach(function (p) {
                if (p.start > last) frag.appendChild(document.createTextNode(text.slice(last, p.start)));
                var mark = document.createElement("mark");
                mark.className = "gg-find";
                mark.textContent = text.slice(p.start, p.end);
                frag.appendChild(mark);
                marks.push(mark);
                last = p.end;
            });
            if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
            item.node.parentNode.replaceChild(frag, item.node);
        });
        matches = marks;
        if (matches.length) {
            setCurrent(0);
            input.style.outline = "";
        } else {
            status.textContent = "0/0";
            input.style.outline = "1px solid #c00";
        }
    }

    function setCurrent(i) {
        matches.forEach(function (m, k) {
            if (k === i) m.classList.add("gg-find-cur");
            else m.classList.remove("gg-find-cur");
        });
        var m = matches[i];
        if (m && m.scrollIntoView) {
            try { m.scrollIntoView({ block: "center", inline: "nearest" }); }
            catch (e) { m.scrollIntoView(true); }
        }
        idx = i;
        status.textContent = (i + 1) + "/" + matches.length;
    }

    function go(delta) {
        if (!matches.length) {
            search(input.value);
            return;
        }
        var n = idx + delta;
        if (n < 0) n = matches.length - 1;
        if (n >= matches.length) n = 0;
        setCurrent(n);
    }

    function show() {
        ensureBar();
        bar.style.display = "flex";
        input.focus();
        input.select();
        if (input.value) search(input.value);
    }

    function hide() {
        if (!bar) return;
        bar.style.display = "none";
        clearMarks();
        status.textContent = "";
        input.style.outline = "";
    }

    document.addEventListener("keydown", function (e) {
        if ((e.ctrlKey || e.metaKey) && !e.altKey && (e.key === "f" || e.key === "F")) {
            e.preventDefault();
            e.stopPropagation();
            show();
        } else if (e.key === "F3" && bar && bar.style.display !== "none") {
            e.preventDefault();
            go(e.shiftKey ? -1 : 1);
        }
    }, true);
})();
"""




def _inject_gesture_nav(win):
    win.evaluate_js(_GESTURE_NAV_JS)


def _sync_title(win):
    """ウィンドウのタイトルを、表示中のページの <title> に合わせる (トップは「プロジェクト名 - git graph」)。"""
    try:
        title = win.evaluate_js("document.title")
        if title:
            win.set_title(title)
    except Exception:  # set_title のない古い pywebview など。タイトルは固定のままにする
        pass


def _on_loaded(win):
    _inject_gesture_nav(win)
    try:
        win.evaluate_js(_FIND_BAR_JS)
    except Exception:
        pass
    _sync_title(win)


def find_icon():
    """アイコンファイルを探す。このスクリプトと同じ場所の git_graph.ico (Windows) / git_graph.png (Linux)。無ければ None。"""
    names = {"Windows": ["git_graph.ico"], "Linux": ["git_graph.png", "git_graph.ico"]}.get(platform.system(), [])
    bases = [Path(sys.executable).resolve().parent] if FROZEN else [Path(__file__).resolve().parent]
    if getattr(sys, "_MEIPASS", None):
        bases.append(Path(sys._MEIPASS))
    for base in bases:
        for name in names:
            path = base / name
            if path.is_file():
                return str(path)
    return None


def _set_taskbar_identity():
    """Windows のタスクバーで python.exe のアイコンにまとめられないよう、このアプリ専用の ID を付ける。"""
    if platform.system() == "Windows":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except (AttributeError, OSError):
            pass


def _stop_server(th, url):
    if th.is_alive():
        request = urllib.request.Request(
            url.rstrip("/") + "/api/shutdown",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5):
                pass
        except (urllib.error.URLError, TimeoutError):
            pass
        th.join(timeout=10)  # 終わらなくても daemon なので、プロセスの終了と一緒に消える


def _close_window_when_server_stops(th, win, window_closed):
    th.join()
    if not window_closed.is_set():
        win.destroy()


def main():
    global _DEBUG
    # サーバは --debug を知らないので、ここで取り除いてから渡す
    _DEBUG = "--debug" in sys.argv
    if _DEBUG:
        sys.argv = [a for a in sys.argv if a != "--debug"]

    _add_bundled_graphviz_to_path()
    sys.stdout = _StdoutTee(sys.stdout)
    if sys.stderr is None:  # --noconsole の exe では None
        sys.stderr = open(os.devnull, "w")

    proc = ServerThread(build_command())
    proc.start()

    url = url_queue.get()
    if url is None:
        raise SystemExit(f"サーバを起動できませんでした: {proc.error}")

    # pywebview は既定でファイルのダウンロードを無効にしている (パッチ作成・ファイルのダウンロードが何も起きない原因)。
    # settings は新しい pywebview のみ (6 系では dict ではなく UserDict なので isinstance(dict) では判定できない)
    try:
        webview.settings["ALLOW_DOWNLOADS"] = True
    except (AttributeError, KeyError, TypeError):
        pass
    win = webview.create_window(
        "Git Graph",
        url=url,
        width=1400,
        height=900,
    )
    win.events.loaded += lambda: _on_loaded(win)
    window_closed = threading.Event()
    win.events.closed += window_closed.set
    threading.Thread(
        target=_close_window_when_server_stops,
        args=(proc, win, window_closed),
        daemon=True,
    ).start()

    start_args = {}
    if _DEBUG:
        start_args["debug"] = True
    icon = find_icon()
    if icon and "icon" in inspect.signature(webview.start).parameters:  # icon 引数は新しい pywebview のみ
        start_args["icon"] = icon
    _set_taskbar_identity()
    try:
        webview.start(**start_args)
    finally:
        _stop_server(proc, url)


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()  # exe 化したとき multiprocessing が自分自身を再起動しても増殖しないように
    main()
