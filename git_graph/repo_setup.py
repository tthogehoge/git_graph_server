# 開くリポジトリの決定 (起動時の指定、または設定画面からの指定)。
# 起動時に指定されたパスが git リポジトリでなかったときは、サーバーを「リポジトリ未選択」(state.NEED_REPO) の状態で起動し、
# 開くパスとリポジトリのアドレス (任意) を入力する画面を出す。アドレスが指定されていればクローンしてから開く。
import os
import subprocess
import tempfile
from . import app_state as state
from .gitrun import http_origin, net_error, run_git_net_origin
from .i18n import tr, UserError
from .routing import route

CLONE_TIMEOUT = 3600  # クローンは大きなリポジトリだと時間がかかるので、通常の通信より長く待つ (秒)


def clean_path_text(text):
    """入力されたパスの前後の空白と、「パスのコピー」(Windows) が付ける引用符を取り除く。"""
    text = (text or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    return text


def absolute_path(text):
    """~ を展開して絶対パスにする。"""
    return os.path.abspath(os.path.expanduser(text))


def open_repo(path):
    """path を表示するリポジトリにする。git リポジトリでなければ UserError (このとき状態は変えない)。
    サブフォルダが指定されたときは、パスの扱い (status / diff はルート基準) がずれないよう作業ツリーのルートに揃える。"""
    path = absolute_path(path)
    if not os.path.isdir(path):
        raise UserError("setup_path_not_found", path=path)
    try:
        found = subprocess.run(["git", "-C", path, "rev-parse", "--git-dir"], capture_output=True)
    except FileNotFoundError:
        raise UserError("setup_git_missing")
    if found.returncode != 0:
        raise UserError("setup_not_a_repo", path=path)
    top = subprocess.run(["git", "-C", path, "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    has_worktree = top.returncode == 0
    repo = top.stdout.strip() if has_worktree else path
    state.REPO = repo
    state.HAS_WORKTREE = has_worktree
    state.WIP_ENABLED = has_worktree and not state.OPTS.no_wip
    state.PROJECT_NAME = os.path.basename(os.path.abspath(repo)) or repo
    state.START_WIP = state.OPTS.start == "wip"
    state.NEED_REPO = False
    from . import libgit
    libgit.reset_cache()  # 前に開いていた (または開こうとした) リポジトリのキャッシュを捨てる


def check_clone_url(url):
    """クローン元のアドレスを検査する。オプションに見えるもの (-で始まる) と制御文字は受け付けない。"""
    if not url or url.startswith("-") or any(ord(c) < 32 or ord(c) == 127 for c in url):
        raise UserError("setup_url_invalid")


def check_clone_dest(dest):
    """クローン先は、存在しないか、空のフォルダであること。"""
    if os.path.lexists(dest) and not (os.path.isdir(dest) and not os.listdir(dest)):
        raise UserError("setup_dest_not_empty", path=dest)


def clone_repo(url, dest, *, auth=None, auth_origin=None):
    """url を dest へクローンする。HTTP 認証が必要なのに使える認証情報が無いときは
    {"auth_required": True, "auth_origin": scheme://host[:port]} を返す (何も作られていない。画面が尋ねてやり直す)。
    成功したら None。失敗したら git のメッセージを ValueError にして返す。"""
    check_clone_url(url)
    check_clone_dest(dest)
    # まだリポジトリが無いので、必ず存在するフォルダで実行する (dest は絶対パス)。ext:: などの危険な転送は使わせない
    r, need = run_git_net_origin("-c", "protocol.ext.allow=never", "clone", "--", url, dest,
                                 auth=auth, origin=auth_origin or http_origin(url), timeout=CLONE_TIMEOUT,
                                 cwd=tempfile.gettempdir())
    if need:
        return {"auth_required": True, "auth_origin": need}
    if r.returncode != 0:
        raise ValueError(net_error(r, tr("git_command_failed")))
    return None


@route("/api/setup", method="POST")
def api_setup(body):
    """設定画面の「開く」。url があれば path へクローンしてから、path のリポジトリを開く。
    リポジトリを開いたあとは、画面が / へ移ると通常の画面 (WIP またはグラフ) になる。"""
    if not state.NEED_REPO:
        raise UserError("setup_already_open")
    path, url = body.get("path", ""), body.get("url", "")
    if not isinstance(path, str) or not isinstance(url, str):
        raise ValueError("bad body")
    path, url = clean_path_text(path), url.strip()
    if not path:
        raise UserError("setup_path_required")
    if url:
        if not state.WRITABLE:  # クローンはファイルを作る書き込み。--read-only のときはできない
            raise UserError("write_disabled")
        auth = body.get("auth")
        if auth is not None and not isinstance(auth, dict):
            raise UserError("auth_invalid")
        origin = body.get("auth_origin")
        result = clone_repo(url, absolute_path(path), auth=auth, auth_origin=origin if isinstance(origin, str) else None)
        if result:
            return result
    open_repo(path)
    return {"ok": True, "cloned": bool(url)}


__all__ = [
    "CLONE_TIMEOUT", "absolute_path", "api_setup", "check_clone_dest", "check_clone_url", "clean_path_text",
    "clone_repo", "open_repo",
]
