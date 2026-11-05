# submodule を、別のプロセス (もう一つのサーバー) で開く。
#
# サーバーの状態 (対象リポジトリ・オプション・元に戻す履歴など) はプロセスごとに 1 つだけなので、
# submodule は同じプロセスで切り替えず、別のプロセスを起動して開く。
# ポートは共有できない (同じアドレスの同じポートで待ち受けられるのは 1 プロセスだけ) ので、
# 子プロセスには空いているポートを渡し、画面 (ブラウザー) にはそのポートを返して新しいタブで開かせる。
# pywebview では、子プロセスも自分のウィンドウを開くので、ポートは要らない (起動コマンドは app_state.CHILD_CMD で渡す)。
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from . import app_state as state
from .gitrun import git, is_loopback
from .i18n import tr, UserError
from .query.files import validate_file_path
from .routing import route

SCRIPT = Path(__file__).resolve().parent.parent / "git_graph_server.py"
READY_TIMEOUT = 20      # 子のサーバーが待ち受けを始めるまで待つ秒数
EARLY_EXIT_WAIT = 1.5   # ウィンドウを開く方式の子が、すぐ異常終了していないかを見る秒数
FLAG_OPTIONS = ("no_remotes", "no_tags", "include_tips", "full_layout", "no_count", "no_wip",
                "read_only", "allow_write", "no_undo", "desktop")
_CHILDREN = {}  # submodule のパス -> (Popen, ポート)。同じ submodule をもう一度開くときに使い回す


def forwarded_args():
    """子のサーバーへ引き継ぐオプション。リポジトリ・リビジョン・ポートは引き継がない (ポートは子ごとに別でなければならない)。"""
    o = state.OPTS
    args = []
    if o.host != "127.0.0.1":
        args += ["--host", o.host]
    for name in FLAG_OPTIONS:
        if getattr(o, name):
            args.append("--" + name.replace("_", "-"))
    args += ["--merge-max-nodes", str(o.merge_max_nodes), "--cluster-min-nodes", str(o.cluster_min_nodes),
             "--rankdir", o.rankdir, "--font", o.font, "--start", o.start, "--lang", state.UI_LANG]
    if o.config:
        args += ["--config", o.config]
    return args


def check_submodule(path):
    """登録された submodule (インデックス上の gitlink) のパスとして安全な形にする。登録されていなければ UserError。"""
    path = validate_file_path(path, allow_empty=False)
    entries = [e for e in git("ls-files", "-s", "-z", "--", path, literal=True).split("\0") if e]
    if len(entries) != 1 or not entries[0].startswith("160000 "):
        raise UserError("submodule_not_found", path=path)
    return path


def submodule_dir(path):
    """登録された submodule の作業ツリーのディレクトリ。初期化されていなければ UserError。"""
    path = check_submodule(path)
    target = os.path.join(state.REPO, *path.split("/"))
    if os.path.islink(target) or not os.path.exists(os.path.join(target, ".git")):
        raise UserError("submodule_not_initialized", path=path)
    return target


def free_port(host):
    """待ち受けられるポートを、OS に選ばせて返す (閉じてから子が使うので、ごくまれに他に取られることはある)。"""
    family, kind, proto, _, addr = socket.getaddrinfo(host, 0, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE)[0]
    with socket.socket(family, kind, proto) as s:
        s.bind(addr)
        return s.getsockname()[1]


def _failure_text(errfile):
    try:
        errfile.seek(0)
        return errfile.read(2000).decode("utf-8", errors="replace").strip()
    except (OSError, ValueError):
        return ""


def _wait_ready(proc, host, port, errfile):
    """子のサーバーが待ち受けを始めるまで待つ。先に終了した (起動に失敗した) ときは UserError。"""
    connect_host = "127.0.0.1" if host in ("", "0.0.0.0") else host
    deadline = time.monotonic() + READY_TIMEOUT
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise UserError("submodule_start_failed", error=_failure_text(errfile) or proc.returncode)
        try:
            with socket.create_connection((connect_host, port), timeout=0.3):
                return
        except OSError:
            time.sleep(0.1)
    proc.terminate()
    raise UserError("submodule_start_timeout")


def _wait_early_exit(proc, errfile):
    """ウィンドウを開く方式の子は、待ち受けの確認ができない。すぐ異常終了したときだけ失敗として返す。"""
    try:
        proc.wait(timeout=EARLY_EXIT_WAIT)
    except subprocess.TimeoutExpired:
        return
    if proc.returncode != 0:
        raise UserError("submodule_start_failed", error=_failure_text(errfile) or proc.returncode)


@route("/api/submodule-open", method="POST")
def api_submodule_open(body):
    """submodule を別のプロセスで開く。リポジトリは変更しない。
    ブラウザーの場合は子のサーバーのポートを返す (画面がそのポートを新しいタブで開く)。
    pywebview の場合は子が自分のウィンドウを開くので、ポートは返さない。"""
    path = body.get("path")
    if not isinstance(path, str):
        raise ValueError("bad path")
    target = submodule_dir(path)
    for key in [k for k, (p, _) in _CHILDREN.items() if p.poll() is not None]:
        del _CHILDREN[key]  # 終了した子は忘れる (ゾンビも残さない)
    window = state.CHILD_CMD is not None
    if path in _CHILDREN:
        port = _CHILDREN[path][1]
        return {"ok": True, "port": port, "message": tr("submodule_already_open", path=path)}
    port = None if window else free_port(state.OPTS.host)
    if window:
        argv = state.CHILD_CMD(target)
    else:
        argv = [sys.executable, str(SCRIPT), target, "--port", str(port)] + forwarded_args()
    with tempfile.TemporaryFile() as errfile:  # 起動に失敗したときのメッセージを取るため。子はこのファイルへ書き続けてもよい
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=errfile)
        except OSError as e:
            raise UserError("submodule_start_failed", error=e)
        if window:
            _wait_early_exit(proc, errfile)
        else:
            _wait_ready(proc, state.OPTS.host, port, errfile)
    _CHILDREN[path] = (proc, port)
    return {"ok": True, "port": port, "message": tr("submodule_opened_window" if window else "submodule_opened", path=path)}


__all__ = ["FLAG_OPTIONS", "api_submodule_open", "check_submodule", "forwarded_args", "free_port", "submodule_dir"]
