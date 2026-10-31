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
    if len(sys.argv) > 1:
        cmd += ["-C"] + sys.argv[1:]

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
    _sync_title(win)


def find_icon():
    """アイコンファイルを探す。このスクリプトと同じ場所の git_graph.ico (Windows) / git_graph.png (Linux)。無ければ None。"""
    names = {"Windows": ["git_graph.ico"], "Linux": ["git_graph.png", "git_graph.ico"]}.get(platform.system(), [])
    for name in names:
        path = ICON_DIR / name
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
