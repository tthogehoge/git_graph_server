# 外部アプリ (エディタ / 差分ツール) の起動。
#
# pywebview など、サーバーと同じ PC で動く画面 (--desktop) からだけ使える。使うアプリは設定ファイル
# (JSON) の "editor" と "diff_tool" に書く。コマンドは配列で書き、次の文字列を置き換える (シェルは通さない)。
#   editor:    {path}
#   diff_tool: {left} {right} {left_title} {right_title}
# 文字列 1 つで書いたときは、実行ファイルだけを指定したことになり、引数は上の既定の並びになる。
# editor を書かないときは OS の標準の編集アプリで開く。
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from . import app_state as state
from . import tmpdir
from .files import validate_file_path, worktree_target
from .gitrun import git_bytes, HASH_RE, is_loopback
from .i18n import tr, UserError
from .routing import route
from .wip import amend_base

CONFIG_NAME = "git_graph_server.json"
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / CONFIG_NAME
DEFAULT_COMMANDS = {"editor": ["{exe}", "{path}"], "diff_tool": ["{exe}", "{left}", "{right}"]}
TEMP_KEEP_SECONDS = 24 * 3600


def load_config(path, required):
    """設定ファイルを読む。required=False のときは、無ければ空の設定を返す。形式の誤りは UserError。"""
    path = Path(path)
    if not path.is_file():
        if required:
            raise UserError("ext_config_missing", path=str(path))
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("top level must be an object")
        for key in DEFAULT_COMMANDS:
            normalize_command(key, data.get(key))
    except (OSError, ValueError) as e:  # JSON の不正も ValueError
        raise UserError("ext_config_invalid", path=str(path), error=str(e))
    return data


def normalize_command(key, value):
    """設定値 (文字列 / 文字列の配列 / 未設定) を、置き換え前のコマンド配列にする。未設定は None。"""
    if value in (None, "", []):
        return None
    if isinstance(value, str):
        return [value if part == "{exe}" else part for part in DEFAULT_COMMANDS[key]]
    if isinstance(value, list) and all(isinstance(p, str) and p for p in value):
        return list(value)
    raise ValueError(f'"{key}" must be a string or a list of strings')


def configured(key):
    return normalize_command(key, state.CONFIG.get(key))


def require_desktop():
    """外部アプリを起動してよいのは、同じ PC の画面 (--desktop) からだけ。"""
    if not (state.DESKTOP and state.WRITABLE and is_loopback(state.OPTS.host)):
        raise UserError("ext_unavailable")


def expand(command, values):
    """コマンドの {名前} を values で置き換える。値にない {名前} はそのまま残す (str.format は使わない)。"""
    sub = lambda m: values.get(m.group(1), m.group(0))
    return [re.sub(r"\{(\w+)\}", sub, part) for part in command]


def launch(command):
    """アプリを起動して、終了を待たずに戻る。起動できなければ UserError。"""
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(command, **kwargs)
    except OSError as e:
        raise UserError("ext_launch_failed", app=command[0], error=str(e))


def open_with_os_editor(target):
    """設定がないときの既定。Windows は編集用の関連付け、無ければメモ帳。.py などを実行しないよう、"open" は使わない。"""
    if os.name == "nt":
        try:
            os.startfile(target, "edit")
            return
        except OSError:
            launch(["notepad.exe", target])
    elif sys.platform == "darwin":
        launch(["open", "-t", target])
    else:
        launch(["xdg-open", target])


@route("/api/open-editor", method="POST")
def api_open_editor(body):
    """作業ツリーのファイルを外部エディタで開く。"""
    require_desktop()
    path = body.get("path")
    if not isinstance(path, str):
        raise ValueError("bad path")
    path = validate_file_path(path, allow_empty=False)
    target = worktree_target(path)
    if os.path.islink(target) or not os.path.isfile(target):
        raise UserError("not_a_file")
    command = configured("editor")
    if command:
        launch(expand(command, {"path": target}))
    else:
        open_with_os_editor(target)
    return {"ok": True, "message": tr("ext_editor_started", path=path)}


def _cleanup_old_temp_dirs():
    """差分用のサブディレクトリのうち、古いものを消す (長く起動したままでも溜まらないように)。
    インスタンスの一時ディレクトリごと終了時に消えるので、ここで消すのは起動中に溜まった分だけ。"""
    root, now = tmpdir.instance_dir(), time.time()
    for name in os.listdir(root):
        full = os.path.join(root, name)
        if name.startswith("diff_") and os.path.isdir(full) and now - os.path.getmtime(full) > TEMP_KEEP_SECONDS:
            shutil.rmtree(full, ignore_errors=True)  # 外部ツールがまだ開いていて消せなければ、次の機会に


def blob_or_empty(spec):
    """git の spec (hash:path / :path / :2:path) の内容。無ければ空。"""
    try:
        return git_bytes("cat-file", "blob", spec, literal=True)
    except subprocess.CalledProcessError:
        return b""


def rev_or_none(rev):
    r = subprocess.run(["git", "-C", state.REPO, "rev-parse", "--verify", "--quiet", rev + "^{commit}"],
                       capture_output=True, text=True)
    return r.stdout.strip() or None


def diff_sides(q):
    """差分ページ (q はそのクエリ) の左 (古い側) と右 (新しい側) を返す。
    各辺は (kind, 値, タイトル): kind が "blob" なら git の spec、"file" なら作業ツリーの絶対パス、"empty" なら空。"""
    path = validate_file_path(q.get("path", [""])[0], allow_empty=False)
    old = q.get("old", [""])[0]
    left_path = validate_file_path(old, allow_empty=False) if old else path
    wip = q.get("wip", [""])[0]

    def blob(rev, p, title):
        return ("blob", f"{rev}:{p}", title)

    def work():
        target = worktree_target(path)
        if os.path.isfile(target) and not os.path.islink(target):
            return ("file", target, tr("working_tree"))
        return ("empty", "", tr("ext_deleted"))

    if wip:
        if not state.HAS_WORKTREE:
            raise UserError("no_worktree")
        if wip == "compare":
            base = q.get("from", [""])[0]
            if not HASH_RE.fullmatch(base):
                raise ValueError("bad hash")
            return blob(base, left_path, base[:7]), work()
        if wip == "staged":
            return blob("HEAD", left_path, "HEAD"), ("blob", ":" + path, tr("ext_index"))
        if wip == "unstaged":
            return ("blob", ":" + path, tr("ext_index")), work()
        if wip == "conflict":
            return ("blob", ":2:" + path, tr("ext_ours")), work()
        if wip == "untracked":
            return ("empty", "", ""), work()
        if wip == "amend":
            base = amend_base()
            return blob(base, left_path, base[:7]), work()
        raise ValueError("bad wip")
    if "from" in q or "to" in q:
        a, b = q.get("from", [""])[0], q.get("to", [""])[0]
        if not (HASH_RE.fullmatch(a) and HASH_RE.fullmatch(b)):
            raise ValueError("bad hash")
        return blob(a, left_path, a[:7]), blob(b, path, b[:7])
    h = q.get("hash", [""])[0]
    if not HASH_RE.fullmatch(h):
        raise ValueError("bad hash")
    parent = rev_or_none(h + "^1")
    left = blob(parent, left_path, parent[:7]) if parent else ("empty", "", "")
    return left, blob(h, path, h[:7])


def _label(text):
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in text) or "empty"


def materialize(side, directory, name, role):
    """辺を、差分ツールに渡すファイルにする。作業ツリーのファイルはそのまま渡し (ツール上で編集して保存できる)、
    コミットなどの内容は読み取り専用の一時ファイルにする。"""
    kind, value, title = side
    if kind == "file":
        return value
    stem, ext = os.path.splitext(name)
    out = os.path.join(directory, f"{stem}@{_label(title or role)}{ext}")
    with open(out, "wb") as f:
        f.write(blob_or_empty(value) if kind == "blob" else b"")
    os.chmod(out, 0o444)
    return out


@route("/api/open-diff", method="POST")
def api_open_diff(body):
    """1ファイルの差分を外部の差分ツールで開く。body は差分ページのクエリ (path, old, hash, from, to, wip) と同じ。"""
    require_desktop()
    command = configured("diff_tool")
    if not command:
        raise UserError("ext_no_diff_tool", config=str(state.CONFIG_PATH))
    q = {k: [v] for k, v in body.items() if isinstance(v, str)}
    left, right = diff_sides(q)
    _cleanup_old_temp_dirs()
    directory = tmpdir.make_temp_dir("diff")
    name = os.path.basename(q["path"][0])
    left_title = f"{left[2]}:{q.get('old', [''])[0] or q['path'][0]}" if left[2] else ""
    right_title = f"{right[2]}:{q['path'][0]}" if right[2] else ""
    launch(expand(command, {
        "left": materialize(left, directory, name, "left"), "right": materialize(right, directory, name, "right"),
        "left_title": left_title, "right_title": right_title,
    }))
    return {"ok": True, "message": tr("ext_diff_started", path=q["path"][0])}


__all__ = ["CONFIG_NAME", "DEFAULT_CONFIG_PATH", "api_open_diff", "api_open_editor", "configured", "diff_sides",
           "expand", "load_config", "normalize_command"]
