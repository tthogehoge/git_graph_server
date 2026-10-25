import ctypes
import inspect
import platform
import queue
import re
import subprocess
import threading
import sys
import urllib.error
import urllib.request
from pathlib import Path
import webview

URL_PATTERN = re.compile(r"http://127\.0\.0\.1:\d+/?")
APP_ID = "git_graph.webview"
ICON_DIR = Path(__file__).resolve().parent
url_queue = queue.Queue()


def build_command():
    cmd = [sys.executable, "-u", "git_graph_server.py", "--desktop"]  # --desktop: 外部エディタ / 差分ツールのボタンを出す

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


def monitor_stdout(proc):
    for line in proc.stdout:
        print(line, end="")

        m = URL_PATTERN.search(line)
        if m:
            url_queue.put(m.group(0))
            break


_GESTURE_NAV_JS = """
(function () {
    if (window._gestureNavInstalled) return;
    window._gestureNavInstalled = true;

    const THRESHOLD = 50;   // ドラッグ判定の最小移動量 (px)
    let startX = null;
    let didNavigate = false;

    document.addEventListener('mousedown', (e) => {
        if (e.button === 2) {
            startX = e.clientX;
            didNavigate = false;
        }
    }, true);

    document.addEventListener('mouseup', (e) => {
        if (e.button === 2 && startX !== null) {
            const dx = e.clientX - startX;
            if (Math.abs(dx) >= THRESHOLD) {
                didNavigate = true;
                if (dx < 0) history.back();
                else        history.forward();
            }
            startX = null;
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


def _stop_server(proc, url):
    if proc.poll() is None:
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
            if proc.poll() is None:
                proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def _close_window_when_server_stops(proc, win, window_closed):
    proc.wait()
    if not window_closed.is_set():
        win.destroy()


def main():
    proc = subprocess.Popen(
        build_command(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )

    threading.Thread(
        target=monitor_stdout,
        args=(proc,),
        daemon=True,
    ).start()

    url = url_queue.get()

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
    main()
