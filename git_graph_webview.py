import platform
import queue
import re
import subprocess
import threading
import sys
import webview

URL_PATTERN = re.compile(r"http://127\.0\.0\.1:\d+/?")
url_queue = queue.Queue()


def build_command():
    cmd = [sys.executable, "-u", "git_graph_server.py"]

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
    win.events.loaded += lambda: _inject_gesture_nav(win)

    try:
        webview.start()
    finally:
        proc.terminate()


if __name__ == "__main__":
    main()
