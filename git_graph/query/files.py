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
        raw = subprocess.check_output(["git", "-C", state.REPO, "cat-file", "blob", spec])
    return path.rsplit("/", 1)[-1], raw


def wip_patch(base=None):
    """作業ツリーの変更 (ステージ済み・未ステージ・未追跡) をまとめた、base (既定は HEAD) からのパッチ。
    本物のインデックスは変えず、そのコピーに add -A して差分を取る。"""
    if base is None:
        try:
            base = git("rev-parse", "--verify", "--quiet", "HEAD^{commit}").strip()
        except subprocess.CalledProcessError:
            raise UserError("patch_no_head")
    tmp = tmpdir.make_temp_dir("patch")
    try:
        index = os.path.join(tmp, "index")
        real = os.path.join(state.REPO, git("rev-parse", "--git-path", "index").strip())
        env = dict(os.environ, GIT_INDEX_FILE=index)
        if os.path.isfile(real):
            shutil.copyfile(real, index)  # 手元のファイルの情報を再利用して速くする
        else:
            _git_raw("read-tree", base, env=env)
        _git_raw("add", "-A", env=env)
        return _git_raw("diff", "--cached", "--binary", "-M", base, env=env)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def patch_bytes(hash_="", from_hash="", to_hash="", has_from=False):
    """作成したパッチを (ファイル名, バイト列) で返す。
    hash=WIP: 作業ツリーの HEAD からの全変更 / hash=<コミット>: そのコミットの変更 (format-patch)。
    from/to: 2コミット間の差分。to=WIP のときは A から作業ツリーまでの差分。"""
    if has_from:
        a = resolve_commit(from_hash)
        if to_hash == WIP_HASH:
            if not state.WIP_ENABLED or not state.HAS_WORKTREE:
                raise UserError("wip_disabled")
            name, raw = a[:7] + "..wip.patch", wip_patch(a)
        else:
            b = resolve_commit(to_hash)
            name, raw = f"{a[:7]}..{b[:7]}.patch", git_bytes("diff", "--binary", "-M", a, b)
    elif hash_ == WIP_HASH:
        name, raw = "wip.patch", wip_patch()
    else:
        h = resolve_commit(hash_)
        parents = git("rev-list", "--parents", "-n", "1", h).split()[1:]
        if len(parents) > 1:  # マージは format-patch が出力しないので、最初の親との差分にする
            raw = git_bytes("diff", "--binary", "-M", parents[0], h)
        else:
            raw = git_bytes("format-patch", "-1", "--stdout", "--binary", *(["--root"] if not parents else []), h)
        name = h[:7] + ".patch"
    if not raw.strip():
        raise UserError("patch_empty")
    return name, raw


def file_exists_at(commit, path):
    """コミット (または WIP) に path の blob があるか。"""
    if commit == WIP_HASH:
        target = worktree_target(path)
        return os.path.isfile(target) or os.path.islink(target)
    r = subprocess.run(
        ["git", "-C", state.REPO, "cat-file", "-e", f"{commit}:{path}"], capture_output=True
    )
    return r.returncode == 0


def blob_or_empty(spec):
    """git の spec (hash:path / :path / :2:path) の内容。無ければ空。"""
    try:
        return git_bytes("cat-file", "blob", spec, literal=True)
    except subprocess.CalledProcessError:
        return b""


def rev_or_none(rev):
    r = subprocess.run(
        ["git", "-C", state.REPO, "rev-parse", "--verify", "--quiet", rev + "^{commit}"],
        capture_output=True, text=True,
    )
    return r.stdout.strip() or None


__all__ = [
    "FILE_CONTENT_LIMIT", "WIP_HASH", "blame", "blob_or_empty", "file_bytes", "file_content",
    "file_exists_at", "is_wip", "patch_bytes", "rev_or_none", "status_mark", "tree",
    "validate_file_path", "wip_file", "wip_head", "wip_patch", "wip_status_marks", "wip_tree",
    "worktree_target",
]


def commit_of(hash_):
    """リビジョン (ハッシュなど) をコミットのハッシュにする (無ければ UserError)。"""
    return resolve_commit(hash_)


def require_blob(commit, path):
    """commit の path がファイル (blob) であることを確かめる (違えば UserError)。"""
    if git("cat-file", "-t", commit + ":" + path).strip() != "blob":
        raise UserError("not_a_file")


def blob_for_worktree(commit, path):
    """commit の path の内容を、作業ツリーに置くときの形 (改行コードなど。autocrlf など) で返す。--filters が使えない git では素のまま。"""
    spec = commit + ":" + path
    r = subprocess.run(["git", "-C", state.REPO, "cat-file", "--filters", spec], capture_output=True)
    if r.returncode != 0:
        r = subprocess.run(["git", "-C", state.REPO, "cat-file", "blob", spec], capture_output=True)
        if r.returncode != 0:
            raise ValueError(r.stderr.decode("utf-8", errors="replace").strip() or tr("git_command_failed"))
    return r.stdout
