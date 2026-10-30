import os
import subprocess
from . import app_state as state
from .engine import commit
from .gitrun import git, git_bytes
from .gitstate import (  # noqa: F401  (読み取りだけのヘルパーは gitstate に移した。互換のため再公開する)
    addable_paths, amend_base, amend_head_changes, commit_info, is_cherry_picking, is_merging, is_rebasing,
    is_reverting, parse_name_status, parse_numstat, parse_status, read_status_raw, stash_conflict_state,
)
from .i18n import tr, UserError
from .routing import route


def wip_count(status):
    return len({f["path"] for k in ("staged", "unstaged", "untracked", "conflicts") for f in status[k]})

def amend_files(s):
    """amend 後のコミットの内容 = 「直前のコミットの親」と作業ツリーの差分のファイル一覧。
    直前のコミットで変更したファイルは in_head=True (チェックを外すとコミットから外れる)。WIP の変更があるものは tag=WIP。"""
    base = amend_base()
    letters = parse_name_status(git("diff", "--name-status", "-z", "-M", base, "--"))
    in_head = amend_head_changes(base)
    pending = {f["path"] for k in ("staged", "unstaged") for f in s[k]}
    out = []
    for f in parse_numstat(git("diff", "--numstat", "-z", "-M", base, "--")):
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

@route("/api/status")
def api_status(q):
    """作業ツリーの変更 (WIP)。ステージ済み/未ステージには追加・削除行数も付ける。"""
    if not state.WIP_ENABLED:
        raise UserError("wip_disabled")
    s = parse_status(read_status_raw())
    for key, extra in (("staged", ["--cached"]), ("unstaged", [])):
        if not s[key]:
            continue
        stats = {f["path"]: f for f in parse_numstat(git("diff", *extra, "--numstat", "-z", "-M", "--"))}
        for f in s[key]:
            st = stats.get(f["path"])
            if st:
                f["added"], f["deleted"] = st["added"], st["deleted"]
    try:
        s["head"] = git("rev-parse", "--verify", "--quiet", "HEAD").strip()
    except subprocess.CalledProcessError:
        s["head"] = ""
    s["merging"] = is_merging()
    s["rebasing"] = is_rebasing()
    s["cherry_picking"] = is_cherry_picking()
    s["reverting"] = is_reverting()
    s["stash_conflict"] = stash_conflict_state() is not None  # stash の apply / pop が競合したまま (中断できる)
    if s["head"]:
        s["head_message"] = commit_info(s["head"])["message"]  # amend のときにメッセージの初期値にする
        if q.get("amend", [""])[0] == "1" and not s["merging"] and not s["rebasing"]:
            s["amend_files"] = amend_files(s)
    return s

def wip_diff(kind, path, specs):
    if not state.HAS_WORKTREE:
        raise UserError("no_worktree")
    if kind == "staged":
        return git_bytes("diff", "--cached", "--no-color", "-M", "--", *specs, literal=True)
    if kind in ("unstaged", "conflict"):
        return git_bytes("diff", "--no-color", "--", path, literal=True)
    if kind == "untracked":
        # 任意のファイルを読ませないよう、いま未追跡と報告されているパスだけを対象にする
        known = {f["path"] for f in parse_status(read_status_raw())["untracked"]}
        if path not in known:
            raise UserError("not_untracked")
        full = os.path.join(state.REPO, path)
        if os.path.islink(full):
            return f"symlink -> {os.readlink(full)}\n".encode()
        r = subprocess.run(["git", "-C", state.REPO, "diff", "--no-index", "--no-color", "--", "/dev/null", path],
                           capture_output=True)
        if r.returncode not in (0, 1):  # 1 は「差分あり」
            raise ValueError(r.stderr.decode("utf-8", errors="replace").strip() or tr("git_diff_failed"))
        return r.stdout
    if kind == "amend":  # 直前のコミットの親 と 作業ツリー の差分 (未追跡ファイルは新規ファイルとして)
        if path in {f["path"] for f in parse_status(read_status_raw())["untracked"]}:
            return wip_diff("untracked", path, specs)
        return git_bytes("diff", "--no-color", "-M", amend_base(), "--", *specs, literal=True)
    raise ValueError("bad wip")

@route("/api/wip-commit", method="POST", engine=True)
def api_wip_commit(body):
    """チェックされたファイルを add してコミットする (TortoiseGit のコミットダイアログと同じ考え方)。
    ステージ済みでチェックのないファイルは、ステージから外す (作業ツリーの内容はそのまま)。"""
    r = commit.commit(body.get("message", ""), body.get("paths"), staged_paths=body.get("staged_paths"),
                      amend=body.get("amend") is True, branch=body.get("branch", ""))
    if not r["amend"]:
        message = tr("wip_committed", hash=r["hash"][:7], n=r["count"])
    elif not r["count"]:
        message = tr("wip_amended_msg_only", hash=r["hash"][:7])
    elif r["dropped"]:
        message = tr("wip_amended_dropped", hash=r["hash"][:7], n=r["count"], dropped=r["dropped"])
    else:
        message = tr("wip_amended", hash=r["hash"][:7], n=r["count"])
    return {"ok": True, "message": message}


__all__ = [
    "addable_paths", "amend_base", "amend_files", "amend_head_changes", "api_status", "api_wip_commit",
    "parse_name_status", "wip_count", "wip_diff",
]
