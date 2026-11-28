"""画面の通しテスト (実サーバー + jsdom) 用: 一時リポジトリを作って、サーバーを別プロセスで起動する。"""
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent


def start_ui_server(test, files, untracked=()):
    """files: {パス: 内容} をコミットしたリポジトリと、未追跡の untracked を作り、--allow-write --lang en でサーバーを起動する。
    戻り値: (リポジトリのパス, ポート)。後始末 (サーバーを止めて、一時ディレクトリを消す) は test.addCleanup に登録する。"""
    tmp = tempfile.TemporaryDirectory()
    repo = tmp.name

    def git(*args):
        subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.name", "Test User")
    git("config", "user.email", "test@example.com")
    for path, text in files.items():
        full = Path(repo, path)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(text, encoding="utf-8")
    git("add", ".")
    git("commit", "-q", "-m", "init")
    for path, text in dict(untracked).items():
        full = Path(repo, path)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(text, encoding="utf-8")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = subprocess.Popen([sys.executable, str(ROOT / "git_graph_server.py"), repo, "--port", str(port), "--lang", "en", "--allow-write"],
                              cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def stop():
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        tmp.cleanup()

    test.addCleanup(stop)
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            urlopen(f"http://127.0.0.1:{port}/", timeout=1).close()
            return repo, port
        except OSError:
            time.sleep(0.1)
    test.fail("サーバーが起動しない")
