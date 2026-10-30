# 作業ツリーと stash に対する git 操作 (stash の apply / pop / drop / push、変更の破棄、git rm)。
# 引数を受け取って git を実行し、結果を dict で返す。HTTP の body も、画面に出す文言の組み立ても route 側の仕事。
import json
import os
import subprocess
from .. import app_state as state
from ..gitrun import git, git_c_locale, resolve_commit, run_git_write
from ..gitstate import (
    has_head, is_cherry_picking, is_merging, is_rebasing, is_reverting, paths_in_head, stash_marker_path,
    status_lists,
)
from ..i18n import tr, UserError
from . import operation
from .dirty import discard_blockers, parse_checkout_blockers, stash_push

_STEP = 200  # コマンドラインが長くなりすぎないよう、ファイルはこの数ずつ git に渡す


def write_stash_marker(commit, action):
    """stash の適用が競合したことを .git に残す (WIP に「中断」を出し、中断で適用前の状態に戻すために使う)。"""
    try:
        with open(stash_marker_path(), "w", encoding="utf-8") as f:
            json.dump({"stash": commit, "action": action}, f)
    except OSError:
        pass  # 目印が書けなくても、競合の解決はできる


def clear_stash_marker():
    try:
        os.remove(stash_marker_path())
    except OSError:
        pass


def _operation_in_progress():
    return is_merging() or is_rebasing() or is_cherry_picking() or is_reverting()


@operation("stash", touches_worktree=True, detail=lambda commit, *_, **__: commit)
def stash_apply(commit, action, *, on_conflict=None):
    """stash の apply / pop / drop。stash の位置 (stash@{n}) は変わるので、コミットのハッシュから毎回引き直す。
    apply / pop で競合したときは、git の通常の動作どおり作業ツリーに競合を残す (pop は stash も残る)。
    編集中のファイルや同名の管理外ファイルに邪魔されて戻せないときは、止めているファイルを返す ({blocked, modified, untracked})。
    on_conflict="discard" で再実行すると、止めているファイルだけを破棄して (stash の内容で上書きして) 戻す。
    戻り値: {"action", "sel", "hash"} / 止まったとき {"blocked": True, ...}。"""
    if action not in ("apply", "pop", "drop"):
        raise ValueError("bad op")
    if on_conflict not in (None, "discard"):
        raise UserError("on_conflict_invalid")
    h = resolve_commit(commit)
    sel = None
    for line in git("stash", "list", "--format=%H %gd").splitlines():
        sha, _, name = line.partition(" ")
        if sha == h:
            sel = name
            break
    if sel is None:
        raise UserError("stash_not_found")
    done = {"action": action, "sel": sel, "hash": h}
    if action == "drop":
        run_git_write("stash", "drop", sel)
        return done
    if not state.HAS_WORKTREE:
        raise UserError("stash_no_worktree")
    if _operation_in_progress():
        raise UserError("op_in_progress")
    clear_stash_marker()  # 前回の競合の目印は、新しい適用では不要
    for _ in range(3):  # 止めているファイルを破棄して再実行する (1回で済むはずだが、念のため数回まで)
        r = git_c_locale("stash", action, sel)  # 止めているファイルを解析するため、C ロケールで実行する
        if r.returncode == 0:
            return done
        unmerged = subprocess.run(["git", "-C", state.REPO, "-c", "core.quotepath=false", "diff", "--name-only", "--diff-filter=U"],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
        if unmerged:
            write_stash_marker(h, action)  # 競合を残したまま止まる。WIP の「中断」で適用前の状態に戻せるようにする
            raise ValueError(tr("stash_conflict_pop" if action == "pop" else "stash_conflict", sel=sel) + "\n" + unmerged)
        text = (r.stderr + r.stdout).strip()
        blockers = parse_checkout_blockers(text)
        if not (blockers["modified"] or blockers["untracked"]):
            raise ValueError(text or tr("stash_apply_failed"))
        if on_conflict is None:
            return {"blocked": True, **blockers}
        discard_blockers(blockers)
    raise UserError("stash_apply_failed")


@operation("stash_push", touches_worktree=True)
def stash_save(*, message="", paths=None, untracked=True):
    """作業ツリーの変更を stash に退避する (WIP から)。paths があればそのファイルだけ、なければすべて。
    untracked が False でなければ、未追跡ファイルも含める。戻り値: {"sel"}。"""
    if not state.WIP_ENABLED or not state.HAS_WORKTREE:
        raise UserError("wip_disabled")
    if _operation_in_progress():
        raise UserError("stash_push_blocked")
    if not has_head():
        raise UserError("stash_no_commits")
    message = message.strip() if isinstance(message, str) else ""
    if "\0" in message:
        raise ValueError("bad message")
    pathspec = None
    if paths is not None:
        if not isinstance(paths, list) or not paths or not all(isinstance(p, str) and p and "\0" not in p for p in paths):
            raise UserError("select_files")
        s = status_lists(limit=10 ** 9)
        untracked_paths = {f["path"] for f in s["untracked"]}
        known = untracked_paths | {f["path"] for k in ("staged", "unstaged") for f in s[k]}
        bad = set(paths) - known  # 任意のパスを指定させない。いま変更ありと報告されているものだけ
        if bad:
            raise UserError("files_unchanged", files=", ".join(sorted(bad)[:3]))
        if set(paths) & untracked_paths:
            untracked = True  # 未追跡ファイルを選んだなら、含めないと退避できない
        old_of = {f["path"]: f.get("old_path") for f in s["staged"]}
        pathspec = sorted(set(paths) | {old_of[p] for p in paths if old_of.get(p)})  # リネームは元のパスも一緒に退避する
    if not stash_push(message, pathspec, untracked):
        raise UserError("stash_nothing")
    return {"sel": "stash@{0}"}


@operation("discard", touches_worktree=True)
def discard_changes(paths):
    """選んだファイルの変更を破棄する (取り消せない)。追跡中のファイルはステージ済み・未ステージとも HEAD の状態に戻し、
    HEAD にない新規ファイル (ステージ済み) は削除し、未追跡ファイルは削除する。競合中のファイルと、マージなどの途中は対象外。
    戻り値: {"count"}。"""
    if not state.WIP_ENABLED:
        raise UserError("wip_disabled")
    if _operation_in_progress():
        raise UserError("discard_blocked")
    if not isinstance(paths, list) or not paths or not all(isinstance(p, str) for p in paths):
        raise UserError("select_files")
    checked = set(paths)
    s = status_lists(limit=10 ** 9)
    conflict = checked & {f["path"] for f in s["conflicts"]}
    if conflict:
        raise UserError("discard_conflict", files=", ".join(sorted(conflict)[:3]))
    untracked = {f["path"] for f in s["untracked"]}
    old_of = {f["path"]: f.get("old_path") for f in s["staged"]}
    known = untracked | {f["path"] for k in ("staged", "unstaged") for f in s[k]}
    bad = checked - known  # 任意のパスを消させない。いま変更ありと報告されているものだけ
    if bad:
        raise UserError("files_unchanged", files=", ".join(sorted(bad)[:3]))
    tracked = sorted(checked - untracked)
    in_head = paths_in_head(tracked)
    clean = sorted(checked & untracked)
    remove, restore = [], set()
    for p in tracked:
        if old_of.get(p):  # リネームは元のパスも戻す
            restore.add(old_of[p])
        (restore.add(p) if p in in_head else remove.append(p))
    for i in range(0, len(remove), _STEP):  # HEAD にないファイル (git add 済みの新規ファイル): インデックスと作業ツリーから消す
        run_git_write("rm", "-q", "-f", "--", *remove[i:i + _STEP], literal=True)
    restore = sorted(restore)
    for i in range(0, len(restore), _STEP):
        run_git_write("checkout", "HEAD", "--", *restore[i:i + _STEP], literal=True)
    for i in range(0, len(clean), _STEP):
        run_git_write("clean", "-f", "-q", "--", *clean[i:i + _STEP], literal=True)
    return {"count": len(checked)}


@operation("rm", touches_worktree=True)
def remove_files(paths, *, cached=False, force=False):
    """追跡中のファイルを git rm する (削除をステージする)。cached=True なら作業ツリーのファイルは残して追跡だけやめる (git rm --cached)。
    ステージ済みの変更や編集中の変更があるファイルは、git と同じく force=True でないと失敗する。戻り値: {"count", "cached"}。"""
    if not state.WIP_ENABLED:
        raise UserError("wip_disabled")
    if not isinstance(paths, list) or not paths or not all(isinstance(p, str) and p for p in paths):
        raise UserError("select_files")
    paths = sorted(set(paths))
    tracked = set()
    for i in range(0, len(paths), _STEP):
        out = git("ls-files", "-z", "--", *paths[i:i + _STEP], literal=True)
        tracked.update(p for p in out.split("\0") if p)
    bad = [p for p in paths if p not in tracked]  # 追跡中のファイルだけ (ディレクトリや未追跡ファイルは対象外)
    if bad:
        raise UserError("rm_not_tracked", files=", ".join(bad[:3]))
    opts = (["--cached"] if cached else []) + (["-f"] if force else [])
    for i in range(0, len(paths), _STEP):
        run_git_write("rm", "-q", *opts, "--", *paths[i:i + _STEP], literal=True)
    return {"count": len(paths), "cached": bool(cached)}
