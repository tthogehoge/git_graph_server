# 作業ツリーの状態 (status) と WIP 差分の読み取り。
import os
import subprocess
from .. import app_state as state
from ..gitrun import git, git_bytes
from ..gitstate import (
    amend_base, amend_head_changes, commit_info, diff_name_numstat, is_cherry_picking, is_merging,
    is_rebasing, is_reverting, parse_numstat, parse_status, read_head, read_status_raw, status_lists,
    stash_conflict_state,
)
from ..i18n import tr, UserError


def wip_count(status):
    return len({f["path"] for k in ("staged", "unstaged", "untracked", "conflicts") for f in status[k]})


def amend_files(s):
    """amend 後のコミットの内容 = 「直前のコミットの親」と作業ツリーの差分のファイル一覧。
    直前のコミットで変更したファイルは in_head=True (チェックを外すとコミットから外れる)。WIP の変更があるものは tag=WIP。
    作業ツリーとの差分は --raw --numstat を 1 プロセスで取る。"""
    base = amend_base()
    letters, numstat = diff_name_numstat(base, "--")
    in_head = amend_head_changes(base)
    pending = {f["path"] for k in ("staged", "unstaged") for f in s[k]}
    out = []
    for f in numstat:
        f["st"] = letters.get(f["path"], ("M", None))[0]
        f["wip"] = "amend"
        if f["path"] in in_head:
            f["in_head"] = True
        if f["path"] in pending:
            f["tag"] = "WIP"
        out.append(f)
    for f in s["untracked"]:
        out.append(dict(f, wip="amend", tag="WIP"))
    return out


def status(amend=False):
    """作業ツリーの変更 (WIP)。ステージ済み/未ステージには追加・削除行数も付ける。
    amend=True のときは amend 用のファイル一覧も付ける (マージ/リベース中は付けない)。"""
    if not state.WIP_ENABLED:
        raise UserError("wip_disabled")
    s = status_lists()
    from .. import libgit
    head, _ = read_head()
    for key, extra in (("staged", ["--cached"]), ("unstaged", [])):
        if not s[key]:
            continue
        if libgit.available() and head:
            if key == "staged":
                _, numstat = libgit.diff_name_numstat_trees(head, ":index")
            else:
                files = libgit.diff_index_workdir()
                numstat = files
            stats = {f["path"]: f for f in numstat}
        else:
            stats = {f["path"]: f for f in parse_numstat(git("diff", *extra, "--numstat", "-z", "-M", "--"))}
        for f in s[key]:
            st = stats.get(f["path"])
            if st:
                f["added"], f["deleted"] = st["added"], st["deleted"]
    # HEAD はファイルを読むだけ。メッセージが要るときだけ log を 1 回 (rev-parse + show の 2 回を避ける)
    s["head"], _ = read_head()
    s["merging"] = is_merging()
    s["rebasing"] = is_rebasing()
    s["cherry_picking"] = is_cherry_picking()
    s["reverting"] = is_reverting()
    s["stash_conflict"] = stash_conflict_state() is not None  # stash の apply / pop が競合したまま (中断できる)
    if s["head"]:
        from .. import libgit
        if libgit.available():
            s["head_message"] = libgit.commit_message(s["head"])
        else:
            try:
                s["head_message"] = git("log", "-1", "--format=%B", s["head"]).strip()
            except subprocess.CalledProcessError:
                s["head_message"] = commit_info(s["head"])["message"]
        if amend and not s["merging"] and not s["rebasing"]:
            s["amend_files"] = amend_files(s)
    return s


def wip_diff(kind, path, specs):
    if not state.HAS_WORKTREE:
        raise UserError("no_worktree")
    from .. import libgit
    paths = [path] if path else list(specs)
    if kind == "staged":
        if libgit.available():
            return libgit.unified_diff("HEAD", cached=True, paths=paths)
        return git_bytes("diff", "--cached", "--no-color", "-M", "--", *specs, literal=True)
    if kind in ("unstaged", "conflict"):
        if libgit.available():
            return libgit.unified_diff(paths=paths)
        return git_bytes("diff", "--no-color", "--", path, literal=True)
    if kind == "untracked":
        # 任意のファイルを読ませないよう、いま未追跡と報告されているパスだけを対象にする
        known = {f["path"] for f in status_lists()["untracked"]}
        if path not in known:
            raise UserError("not_untracked")
        full = os.path.join(state.REPO, path)
        if os.path.islink(full):
            return ("symlink -> " + os.readlink(full) + "\n").encode()
        if libgit.available():
            return libgit.unified_diff_no_index(path)
        r = subprocess.run(["git", "-C", state.REPO, "diff", "--no-index", "--no-color", "--", "/dev/null", path],
                           capture_output=True)
        if r.returncode not in (0, 1):  # 1 は「差分あり」
            raise ValueError(r.stderr.decode("utf-8", errors="replace").strip() or tr("git_diff_failed"))
        return r.stdout
    if kind == "amend":  # 直前のコミットの親 と 作業ツリー の差分 (未追跡ファイルは新規ファイルとして)
        if path in {f["path"] for f in status_lists()["untracked"]}:
            return wip_diff("untracked", path, specs)
        base = amend_base()
        if libgit.available():
            return libgit.unified_diff(base, paths=paths)
        return git_bytes("diff", "--no-color", "-M", base, "--", *specs, literal=True)
    raise ValueError("bad wip")


__all__ = ["amend_files", "status", "wip_count", "wip_diff"]
