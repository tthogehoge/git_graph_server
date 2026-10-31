# アプリのインスタンスごとの一時ディレクトリ。
#
# 差分用 (外部差分ツールに渡すファイル) と undo 用 (退避したオブジェクト・一時 index) の一時ファイルを、
# システムの一時ディレクトリ直下に散らばらせず、この中の `<用途>_xxxx` というサブディレクトリにまとめる。
# ディレクトリ名に起動したプロセスの PID を入れてあり、異常終了などで残ったものは、
# そのプロセスがもう居なければ次の起動時に消す (別のインスタンスが使っているものは消さない)。
import atexit
import os
import re
import shutil
import tempfile
import time

INSTANCE_PREFIX = "git_graph_"
_INSTANCE_RE = re.compile(r"^git_graph_(\d+)_")
# このディレクトリにまとめる前の版が、システムの一時ディレクトリ直下に作っていたもの (古いものだけ消す)
_LEGACY_PREFIXES = ("git_graph_diff_", "git_graph_objects_", "git_graph_snap_", "git_graph_restore_")
LEGACY_KEEP_SECONDS = 24 * 3600
_dir = None


def instance_dir():
    """このインスタンスの一時ディレクトリ (最初に使うときに作る)。"""
    global _dir
    if _dir is None or not os.path.isdir(_dir):
        _dir = tempfile.mkdtemp(prefix=f"{INSTANCE_PREFIX}{os.getpid()}_")
    return _dir


def existing_instance_dir():
    """インスタンスの一時ディレクトリがすでにあれば、そのパス。無ければ None (読むだけのときに、作らずに済ませる)。"""
    return _dir if _dir and os.path.isdir(_dir) else None


def make_temp_dir(kind):
    """インスタンスの一時ディレクトリの中に、用途 (kind) の名前のサブディレクトリを作って返す。"""
    return tempfile.mkdtemp(prefix=kind + "_", dir=instance_dir())


def remove_instance_dir():
    """インスタンスの一時ディレクトリを、中身ごと消す (終了時)。"""
    global _dir
    if _dir:
        shutil.rmtree(_dir, ignore_errors=True)
        _dir = None


def pid_alive(pid):
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 他のユーザーのプロセスだが、存在はしている
    except OSError:
        return False
    try:  # 終了したが、親がまだ回収していないゾンビは、存在していても動いていない (Linux)
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as f:
            if f.read().rpartition(")")[2].split()[:1] == ["Z"]:
                return False
    except OSError:
        pass
    return True


def cleanup_stale(root=None):
    """前回までの異常終了などで残った一時ディレクトリを消す (起動時)。
    PID を名前に持つインスタンスのディレクトリは、そのプロセスがもう居ないものだけ。"""
    root = root or tempfile.gettempdir()
    now = time.time()
    try:
        names = os.listdir(root)
    except OSError:
        return
    for name in names:
        full = os.path.join(root, name)
        if not os.path.isdir(full):
            continue
        m = _INSTANCE_RE.match(name)
        if m:
            pid = int(m.group(1))
            if pid != os.getpid() and not pid_alive(pid):
                shutil.rmtree(full, ignore_errors=True)
        elif name.startswith(_LEGACY_PREFIXES):
            try:
                old = now - os.path.getmtime(full) > LEGACY_KEEP_SECONDS
            except OSError:
                continue
            if old:
                shutil.rmtree(full, ignore_errors=True)


atexit.register(remove_instance_dir)
