"""Windows で、子プロセス (git / dot) を起動するたびに黒いコンソール窓が一瞬出るのを防ぐ。

pywebview を --noconsole の exe で動かすと、親プロセスにコンソールがないため、
git や dot を起動するたびに Windows が新しいコンソール窓を作ってしまう。
subprocess.Popen に既定で CREATE_NO_WINDOW を付けて、それを防ぐ (run / check_output も Popen を使う)。
creationflags を自分で指定している呼び出し (外部アプリの起動など) は、そのまま尊重する。
"""
import os
import subprocess

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def _wrap_popen(popen_cls):
    """popen_cls.__init__ を、creationflags 未指定のとき CREATE_NO_WINDOW を付けるものに差し替える。"""
    if getattr(popen_cls, "_git_graph_no_window", False):  # 再起動などで二重に包まない
        return False
    orig = popen_cls.__init__

    def __init__(self, *args, **kwargs):
        if not kwargs.get("creationflags"):
            kwargs["creationflags"] = CREATE_NO_WINDOW
        orig(self, *args, **kwargs)

    popen_cls.__init__ = __init__
    popen_cls._git_graph_no_window = True
    return True


def hide_console_windows():
    """Windows のときだけ有効にする。それ以外では何もしない。"""
    if os.name != "nt":
        return False
    return _wrap_popen(subprocess.Popen)
