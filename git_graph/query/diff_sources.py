# 1 ファイルの差分の「変更前」と「変更後」の内容の読み取り (差分エディタ用)。
# どの 2 つを比べるかは、api_diff (query/history.py の diff) の指定と同じ規則で決める。
# HTTP や画面用の文言は知らない。両側の内容と、それがどこのものか (kind / ref) を dict で返す。
import os
import subprocess
from .. import app_state as state
from .. import libgit
from ..gitrun import HASH_RE
from ..gitstate import amend_base
from ..i18n import UserError
from ..textcodec import decode_text, detect_eol
from .files import FILE_CONTENT_LIMIT, rev_or_none, validate_file_path, worktree_target


def _blob(spec):
    """git の spec (hash:path / HEAD:path / :path) の blob の内容。無い・blob でなければ None。"""
    if libgit.available() and not spec.startswith(":"):
        kind, data = libgit.blob_at(spec)
        return data if kind == "blob" and data is not None else None
    r = subprocess.run(["git", "-C", state.REPO, "cat-file", "blob", spec], capture_output=True)  # 無いファイル (追加 / 削除) は普通なので、git のエラーは出さない
    return r.stdout if r.returncode == 0 else None


def _worktree(path):
    """作業ツリーのファイルの内容 (シンボリックリンクは、git と同じくリンク先のパス)。無ければ None。"""
    target = worktree_target(path)
    if os.path.islink(target):
        return os.fsencode(os.readlink(target))
    if os.path.isfile(target):
        with open(target, "rb") as f:
            return f.read(FILE_CONTENT_LIMIT + 1)
    return None


def _first_parent(commit):
    if libgit.available():
        parents = libgit.commit_parents(commit)
        return parents[0] if parents else None
    return rev_or_none(commit + "^")


def _side(kind, ref, raw):
    """片側の結果。raw が None のときは、その側にファイルが無い (追加 / 削除されたファイルの片側)。"""
    side = {"kind": kind, "ref": ref, "exists": raw is not None, "content": "", "encoding": "",
            "binary": False, "truncated": False, "eol": None}
    if raw is None:
        return side
    side["truncated"] = len(raw) > FILE_CONTENT_LIMIT
    text, encoding = decode_text(raw[:FILE_CONTENT_LIMIT], side["truncated"])
    side["encoding"] = encoding
    if text is None:
        side["binary"] = True
        return side
    side["content"] = text
    side["eol"] = detect_eol(text)[0]
    return side


def _hash(value):
    if not HASH_RE.fullmatch(value):
        raise ValueError("bad hash")
    return value


def _need_worktree(compare=False):
    if compare and not state.WIP_ENABLED:
        raise UserError("wip_disabled")
    if not state.HAS_WORKTREE:
        raise UserError("wip_disabled" if compare else "no_worktree")


def diff_sources(path, old="", hash_="", from_hash="", to_hash="", wip="", between=False):
    """path の差分の変更前 (old) と変更後 (new)。引数は query.history.diff と同じ (old は変更前のパス = リネーム前の名前)。
    between=True なら、from_hash / to_hash のコミット間 (どちらかが空でもよい)。
    それぞれ {kind, ref, exists, content, encoding, binary, truncated, eol}。
    kind: parent / commit / a / b / head / index / worktree / amend_base。ref はその側のコミット (短縮ハッシュ)。"""
    path = validate_file_path(path, allow_empty=False)
    old_path = validate_file_path(old) if old else path
    short = lambda h: h[:7]
    if wip == "compare":  # コミットと作業ツリー
        base = _hash(from_hash)
        _need_worktree(compare=True)
        before = _side("a", short(base), _blob(f"{base}:{old_path}"))
        after = _side("worktree", "", _worktree(path))
    elif wip:  # 作業ツリーの変更
        _need_worktree()
        if wip == "staged":
            before = _side("head", "", _blob(f"HEAD:{old_path}"))
            after = _side("index", "", _blob(f":{path}"))
        elif wip == "unstaged":
            before = _side("index", "", _blob(f":{old_path}"))
            after = _side("worktree", "", _worktree(path))
        elif wip == "untracked":
            before = _side("empty", "", None)
            after = _side("worktree", "", _worktree(path))
        elif wip == "conflict":  # 競合中は、HEAD 側 (ours) と、マーカー入りの作業ツリーの内容
            before = _side("head", "", _blob(f"HEAD:{path}"))
            after = _side("worktree", "", _worktree(path))
        elif wip == "amend":  # 直前のコミットの親 と 作業ツリー
            base = amend_base()  # 最初のコミットなら空ツリー (そのときは、変更前のファイルは無い)
            root = base == libgit.EMPTY_TREE
            before = _side("amend_base", "" if root else short(base), None if root else _blob(f"{base}:{old_path}"))
            after = _side("worktree", "", _worktree(path))
        else:
            raise ValueError("bad wip")
    elif between:  # 2 コミット間
        a, b = _hash(from_hash), _hash(to_hash)
        before = _side("a", short(a), _blob(f"{a}:{old_path}"))
        after = _side("b", short(b), _blob(f"{b}:{path}"))
    else:  # 1 コミットの変更 (最初の親との差分)
        commit = _hash(hash_)
        parent = _first_parent(commit)
        before = _side("parent", short(parent) if parent else "", _blob(f"{parent}:{old_path}") if parent else None)
        after = _side("commit", short(commit), _blob(f"{commit}:{path}"))
    return {"path": path, "old_path": old_path, "old": before, "new": after}


__all__ = ["diff_sources"]
