# ファイルツリー・内容・blame の読み取り。
# HTTP や画面文言は知らない。path の検証と git / 作業ツリーからの取得を行う。
import hashlib
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from .. import app_state as state
from .. import tmpdir
from ..gitrun import git, git_bytes, resolve_commit
from ..gitstate import CONFLICT_CODES
from ..i18n import tr, UserError
from ..textcodec import decode_text, edit_info

FILE_CONTENT_LIMIT = 2_000_000
WIP_HASH = "WIP"  # /files?hash=WIP で作業ツリーのファイルを表示する


def validate_file_path(path, allow_empty=True):
    """リポジトリルートからの相対パスとして安全な形にする。"""
    if "\0" in path or (not allow_empty and not path) or path.startswith("/"):
        raise ValueError("bad path")
    parts = path.split("/") if path else []
    if any(p in ("", ".", "..") for p in parts):
        raise ValueError("bad path")
    return path

def is_wip(q):
    return q.get("hash", [""])[0] == WIP_HASH

def wip_head():
    """作業ツリー表示でファイルのログに使う HEAD のハッシュ。コミットがまだなければ None。"""
    r = subprocess.run(["git", "-C", state.REPO, "rev-parse", "--verify", "--quiet", "HEAD^{commit}"],
                       capture_output=True, text=True)
    return r.stdout.strip() or None

def worktree_target(path):
    """作業ツリー内のパスを絶対パスにする。シンボリックリンクなどでリポジトリの外に出るものは拒否する。"""
    if not state.HAS_WORKTREE:
        raise UserError("wip_files_no_worktree")
    if "\\" in path or path.split("/")[0].lower() == ".git":
        raise ValueError("bad path")
    root = os.path.realpath(state.REPO)
    target = os.path.join(root, *path.split("/")) if path else root
    parent = os.path.realpath(os.path.dirname(target)) if path else root
    try:
        inside = os.path.commonpath([root, parent]) == root
    except ValueError:  # 別ドライブなど
        inside = False
    if not inside:
        raise ValueError("bad path")
    return target

def status_mark(xy):
    """git status の XY を、一覧に出す1文字の印にする (競合 U / 未追跡 ? / 追加 A / 削除 D / リネーム R / 変更 M)。"""
    if xy == "??":
        return "?"
    if xy in CONFLICT_CODES:
        return "U"
    for mark in "ARDC":
        if mark in xy:
            return "R" if mark == "C" else mark
    return "M"

def wip_status_marks(prefix):
    """prefix 以下の変更を {パス: (印, XY)} で返す。"""
    raw = git("status", "--porcelain=v1", "-z", "--untracked-files=all", "--", prefix or ".", literal=True)
    toks, out, i = raw.split("\0"), {}, 0
    while i < len(toks):
        t = toks[i]
        i += 1
        if len(t) < 4 or t[2] != " ":
            continue
        xy, p = t[:2], t[3:]
        if "R" in xy or "C" in xy:  # リネーム/コピーは続くトークンが元のパス
            i += 1
        if xy != "!!":
            out[p] = (status_mark(xy), xy)
    return out

def wip_tree(path):
    """作業ツリーのディレクトリ一覧。git に管理されているファイルと、無視されていない未追跡ファイルを出す。"""
    target = worktree_target(path)
    if path and (os.path.islink(target) or not os.path.isdir(target)):
        raise UserError("dir_not_found")
    prefix = path + "/" if path else ""
    args = ["ls-files", "-z", "--cached", "--others", "--exclude-standard", "--full-name"]
    raw = git(*args, "--", prefix or ".", literal=True)
    names = {}
    for item in raw.split("\0"):
        if not item or not item.startswith(prefix):
            continue
        rest = item[len(prefix):]
        name, sep, _ = rest.partition("/")
        if sep:
            names[name] = "tree"
        else:
            names.setdefault(name, "blob")
    marks, dir_marks = wip_status_marks(prefix), {}
    for p, (mark, _) in marks.items():
        if not p.startswith(prefix) or "/" not in p[len(prefix):]:
            continue
        dir_marks.setdefault(p[len(prefix):].split("/", 1)[0], set()).add(mark)
    entries = []
    for name, kind in names.items():
        full = os.path.join(target, name)
        if not os.path.lexists(full):  # 削除済み (まだコミットしていない) のファイル
            continue
        size = None
        if kind == "blob":
            if os.path.isdir(full) and not os.path.islink(full):
                kind = "commit"  # サブモジュール
            else:
                size = os.lstat(full).st_size
        entry = {"name": name, "type": kind, "size": size, "mode": ""}
        if kind == "tree" and name in dir_marks:  # 中に変更があるディレクトリ: 競合 > 未追跡のみ > 変更 の順
            m = dir_marks[name]
            entry["st"] = "U" if "U" in m else "?" if m == {"?"} else "M"
        elif (prefix + name) in marks:
            entry["st"], entry["xy"] = marks[prefix + name]
        entries.append(entry)
    return {"hash": WIP_HASH, "head": wip_head(), "path": path,
            "entries": sorted(entries, key=lambda x: (x["type"] != "tree", x["name"]))}

def wip_file(path):
    target = worktree_target(path)
    if os.path.islink(target):  # git と同じく、リンク先のパスを内容として扱う
        raw = os.fsencode(os.readlink(target))
    elif os.path.isfile(target):
        with open(target, "rb") as f:
            raw = f.read(FILE_CONTENT_LIMIT + 1)
    else:
        raise UserError("not_a_file")
    truncated = len(raw) > FILE_CONTENT_LIMIT
    data = raw[:FILE_CONTENT_LIMIT]
    text, encoding = decode_text(data, truncated)
    size = len(raw) if os.path.islink(target) else os.path.getsize(target)
    res = {"hash": WIP_HASH, "path": path, "content": text or "", "encoding": encoding,
           "binary": text is None, "truncated": truncated, "size": size,
           "sha": hashlib.sha256(raw).hexdigest()}
    if os.path.islink(target):
        res.update(editable=False, edit_reason="edit_symlink")
    else:
        res.update(edit_info(raw, text, encoding, truncated))
    return res


def tree(hash_, path=""):
    """コミットまたは WIP のディレクトリ一覧。"""
    path = validate_file_path(path)
    if hash_ == WIP_HASH:
        return wip_tree(path)
    h = resolve_commit(hash_)
    treeish = h if not path else h + ":" + path
    try:
        raw = git("ls-tree", "-z", "-l", treeish)
    except subprocess.CalledProcessError:
        raise UserError("dir_not_found")
    entries = []
    for item in raw.split("\0"):
        if not item:
            continue
        head, name = item.split("\t", 1)
        mode, kind, obj, size = (head.split() + [""])[:4]
        entries.append({"name": name, "type": kind, "size": None if size == "-" else int(size), "mode": mode})
    return {"hash": h, "path": path, "entries": sorted(entries, key=lambda x: (x["type"] != "tree", x["name"]))}


def file_content(hash_, path):
    """コミットまたは WIP のファイル内容。"""
    path = validate_file_path(path, allow_empty=False)
    if hash_ == WIP_HASH:
        return wip_file(path)
    h = resolve_commit(hash_)
    spec = h + ":" + path
    kind = git("cat-file", "-t", spec).strip()
    if kind != "blob":
        raise UserError("not_a_file")
    raw = subprocess.check_output(["git", "-C", state.REPO, "cat-file", "blob", spec])
    truncated = len(raw) > FILE_CONTENT_LIMIT
    data = raw[:FILE_CONTENT_LIMIT]
    text, encoding = decode_text(data, truncated)
    return {"hash": h, "path": path, "content": text or "", "encoding": encoding,
            "binary": text is None, "truncated": truncated, "size": len(raw)}


def blame(hash_, path):
    """コミットまたは WIP の blame。"""
    path = validate_file_path(path, allow_empty=False)
    if hash_ == WIP_HASH:  # 作業ツリーの内容を blame する。未コミットの行はハッシュが 0000000 になる
        worktree_target(path)
        h = WIP_HASH
        r = subprocess.run(["git", "-C", state.REPO, "blame", "--line-porcelain", "--", path],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=dict(os.environ, GIT_LITERAL_PATHSPECS="1"))
        if r.returncode != 0:
            raise UserError("wip_blame_untracked", path=path)
        out = r.stdout
    else:
        h = resolve_commit(hash_)
        out = git("blame", "--line-porcelain", h, "--", path, literal=True)
    raw_lines = out.splitlines()
    lines, i = [], 0
    while i < len(raw_lines):
        header = raw_lines[i].split()
        if len(header) < 3:
            raise ValueError("bad blame output")
        commit, line_no = header[0], int(header[2])
        author, author_time, author_tz = "", "", "+0000"
        i += 1
        while i < len(raw_lines) and not raw_lines[i].startswith("\t"):
            item = raw_lines[i]
            if item.startswith("author "):
                author = item[7:]
            elif item.startswith("author-time "):
                author_time = item[12:]
            elif item.startswith("author-tz "):
                author_tz = item[10:]
            i += 1
        if i >= len(raw_lines):
            raise ValueError("bad blame output")
        offset = int(author_tz[1:3]) * 60 + int(author_tz[3:5])
        if author_tz[0] == "-":
            offset = -offset
        date = datetime.fromtimestamp(
            int(author_time), timezone(timedelta(minutes=offset))
        ).date().isoformat()
        lines.append({"hash": commit, "line": line_no, "author": author,
                      "date": date, "content": raw_lines[i][1:]})
        i += 1
    return {"hash": h, "path": path, "lines": lines}



def _git_raw(*args, env=None):
    r = subprocess.run(["git", "-C", state.REPO, *args], capture_output=True, env=env)
    if r.returncode != 0:
        raise ValueError(r.stderr.decode("utf-8", "replace").strip() or tr("git_command_failed"))
    return r.stdout


def file_bytes(hash_, path):
    """ダウンロードするファイルを (ファイル名, バイト列) で返す。コミットのファイルは変換せず、そのままの blob を返す。"""
    path = validate_file_path(path, allow_empty=False)
    if hash_ == WIP_HASH:
        target = worktree_target(path)
        if os.path.islink(target):  # 表示と同じく、リンク先のパスを内容として扱う
            raw = os.fsencode(os.readlink(target))
        elif os.path.isfile(target):
            with open(target, "rb") as f:
                raw = f.read()
        else:
            raise UserError("not_a_file")
    else:
        h = resolve_commit(hash_)
        spec = h + ":" + path
        if git("cat-file", "-t", spec).strip() != "blob":
            raise UserError("not_a_file")
        raw = _git_raw("cat-file", "blob", spec)
    return os.path.basename(path) or "download", raw


def patch_bytes(hash_, path):
    """コミットのファイルのパッチを (ファイル名, バイト列) で返す。"""
    path = validate_file_path(path, allow_empty=False)
    h = resolve_commit(hash_)
    raw = git_bytes("show", "--format=", "--first-parent", "-M", "--no-color", h, "--", path, literal=True)
    return (os.path.basename(path) or "patch") + ".patch", raw


def file_exists_at(hash_, path):
    path = validate_file_path(path, allow_empty=False)
    if hash_ == WIP_HASH:
        target = worktree_target(path)
        return os.path.lexists(target)
    try:
        git("cat-file", "-e", resolve_commit(hash_) + ":" + path)
        return True
    except subprocess.CalledProcessError:
        return False


def blob_or_empty(hash_, path):
    """存在しなければ空バイト列。"""
    path = validate_file_path(path, allow_empty=False)
    if hash_ == WIP_HASH:
        target = worktree_target(path)
        if os.path.islink(target):
            return os.fsencode(os.readlink(target))
        if os.path.isfile(target):
            with open(target, "rb") as f:
                return f.read()
        return b""
    try:
        return _git_raw("cat-file", "blob", resolve_commit(hash_) + ":" + path)
    except ValueError:
        return b""


def rev_or_none(hash_):
    if hash_ == WIP_HASH:
        return WIP_HASH
    try:
        return resolve_commit(hash_)
    except Exception:
        return None


def wip_patch(path):
    """作業ツリーの変更をパッチとして返す。"""
    path = validate_file_path(path, allow_empty=False)
    worktree_target(path)
    raw = git_bytes("diff", "--no-color", "-M", "HEAD", "--", path, literal=True)
    return (os.path.basename(path) or "patch") + ".patch", raw


__all__ = [
    "FILE_CONTENT_LIMIT", "WIP_HASH", "blame", "blob_or_empty", "file_bytes", "file_content",
    "file_exists_at", "is_wip", "patch_bytes", "rev_or_none", "status_mark", "tree",
    "validate_file_path", "wip_file", "wip_head", "wip_patch", "wip_status_marks", "wip_tree",
    "worktree_target",
]
