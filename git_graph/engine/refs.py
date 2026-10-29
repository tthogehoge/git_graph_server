# ブランチ・タグ・checkout・reset など、ref と HEAD を動かす git 操作。
# 引数を受け取って git を実行し、結果を dict で返す。HTTP の body も、画面に出す文言の組み立ても route 側の仕事。
# 止まったとき (編集中のファイルに邪魔された、など) は {"blocked": True, ...} を返し、route がそのまま画面へ返す。
import subprocess
from .. import app_state as state
from ..gitrun import (
    check_ref_name, git, HTTP_AUTH_CACHE, is_http_auth_error, net_error, remember_auth, resolve_commit, run_git_net,
    run_git_write,
)
from ..gitstate import branch_config, has_head, is_cherry_picking, is_merging, is_rebasing, is_reverting
from ..i18n import tr, UserError
from . import operation
from .dirty import checkout_resolving
from .remote import Auth


def _git_ok(*args):
    return subprocess.run(["git", "-C", state.REPO, *args], capture_output=True).returncode == 0


@operation("branch", touches_worktree=False, detail=lambda commit, name, *_, **__: name)
def create_branch(commit, name, *, checkout=False, upstream=None, on_conflict=None):
    """ブランチを作る。checkout=True なら、作ったブランチへ切り替える (upstream があれば、そのリモートブランチを追跡する)。
    戻り値: {"name", "checked_out", "stashed"} / 切り替えが止まったとき {"blocked": True, "modified", "untracked"}。"""
    h = resolve_commit(commit)
    name = str(name).strip()
    check_ref_name("branch", name)
    if not checkout:
        run_git_write("branch", name, h)
        return {"name": name, "checked_out": False, "stashed": None}
    if upstream:
        if not isinstance(upstream, str) or upstream.startswith("-") or "\0" in upstream:
            raise UserError("remote_branch_not_found")
        r = subprocess.run(["git", "-C", state.REPO, "rev-parse", "--verify", "--quiet", "refs/remotes/" + upstream + "^{commit}"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0 or r.stdout.strip() != h:
            raise UserError("remote_branch_not_found")
        args = ["-b", name, "--track", upstream]
    else:
        args = ["-b", name, h]
    res = checkout_resolving(args, on_conflict)
    if res and res.get("blocked"):
        return res
    return {"name": name, "checked_out": True, "stashed": (res or {}).get("stashed")}


@operation("tag", touches_worktree=False, detail=lambda commit, name, *_, **__: name)
def create_tag(commit, name, message=""):
    """タグを作る。message があれば注釈付きタグ。戻り値: {"name", "annotated"}。"""
    h = resolve_commit(commit)
    name = str(name).strip()
    message = str(message).strip()
    check_ref_name("tag", name)
    if message:
        run_git_write("tag", "-a", name, "-m", message, h)
    else:
        run_git_write("tag", name, h)
    return {"name": name, "annotated": bool(message)}


@operation("delete_ref", touches_worktree=False, detail=lambda kind, name, *_, **__: name)
def delete_ref(kind, name, *, force=False):
    """ローカルのブランチ / タグを消す。マージされていないブランチは force=True でないと消えず、{"needs_force": True} を返す。
    戻り値: {"kind", "name"}。"""
    if kind not in ("branch", "tag"):
        raise UserError("ref_delete_only")
    name = str(name).strip()
    check_ref_name(kind, name)
    ref = ("refs/heads/" if kind == "branch" else "refs/tags/") + name
    if not _git_ok("show-ref", "--verify", "--quiet", ref):
        raise UserError("ref_not_exist", name=repr(name))
    if kind == "tag":
        run_git_write("tag", "-d", "--", name)
    elif force:
        run_git_write("branch", "-D", "--", name)
    else:
        r = subprocess.run(["git", "-C", state.REPO, "branch", "-d", "--", name],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            if subprocess.run(["git", "-C", state.REPO, "merge-base", "--is-ancestor", name, "HEAD"]).returncode == 1:
                return {"needs_force": True}
            raise ValueError((r.stderr or r.stdout).strip() or tr("branch_delete_failed"))
    return {"kind": kind, "name": name}


def delete_remote_branch(name, *, auth=None):
    """リモートのブランチを消す (git push --delete)。リモート側が変わって元に戻せないので、履歴には記録しない。
    認証が必要なときは {"auth_required": True, "auth_remote": リモート名}。戻り値: {"name"}。"""
    auth = auth or Auth()
    name = str(name).strip()
    remote = next((r for r in sorted(git("remote").split(), key=len, reverse=True) if name.startswith(r + "/")), None)
    branch = name[len(remote) + 1:] if remote else ""
    if not remote or not branch:
        raise UserError("remote_branch_not_found")
    check_ref_name("branch", branch)
    credentials = auth.for_remote(remote)
    r = run_git_net("push", remote, "--delete", branch, auth=credentials)
    if r.returncode != 0:
        if is_http_auth_error(r) and not auth.retry:
            return {"auth_required": True, "auth_remote": remote}
        if auth.retry:
            HTTP_AUTH_CACHE.pop(remote, None)
        raise ValueError(net_error(r, tr("git_command_failed")))
    remember_auth(remote, credentials)
    return {"name": name}


@operation("checkout", touches_worktree=True, detail=lambda target, *_, **__: target if isinstance(target, str) else "")
def checkout(target, *, branch=False, on_conflict=None):
    """ブランチへ切り替える (branch=True。target はブランチ名) か、コミットへ切り替える (target はコミット。HEAD を切り離す)。
    戻り値: {"branch" または "hash", "stashed"} / 止まったとき {"blocked": True, "modified", "untracked"}。"""
    if branch:
        target = str(target)
        check_ref_name("branch", target)
        res = checkout_resolving([target, "--"], on_conflict)
        if res and res.get("blocked"):
            return res
        return {"branch": target, "stashed": (res or {}).get("stashed")}
    h = resolve_commit(target)
    res = checkout_resolving(["--detach", h], on_conflict)
    if res and res.get("blocked"):
        return res
    return {"hash": h, "stashed": (res or {}).get("stashed")}


@operation("reset", touches_worktree=True, detail=lambda commit, *_, **__: commit)
def reset(commit, mode="mixed"):
    """現在の HEAD を commit へ移動する (git reset --soft / --mixed / --hard)。戻り値: {"hash", "mode", "moved"}。"""
    if mode not in ("soft", "mixed", "hard"):
        raise UserError("reset_mode_invalid")
    if is_merging() or is_rebasing() or is_cherry_picking() or is_reverting():
        raise UserError("reset_blocked")
    h = resolve_commit(commit)
    before = git("rev-parse", "HEAD").strip() if has_head() else None
    run_git_write("reset", "--" + mode, h)
    return {"hash": h, "mode": mode, "moved": before != h}


@operation("branch_rename", touches_worktree=False, detail=lambda old, new, *_, **__: new)
def rename_branch(old, new):
    """ローカルブランチの名前を変える (git branch -m)。リモートのブランチは変わらない。
    戻り値: {"old", "new", "has_remote"} (has_remote: 追跡しているリモートがあり、そちらは変わらない)。"""
    old = str(old).strip()
    new = str(new).strip()
    if not old or "\0" in old:
        raise UserError("branch_name_invalid")
    check_ref_name("branch", new)
    exists = lambda name: _git_ok("show-ref", "--verify", "--quiet", "refs/heads/" + name)
    if not exists(old):
        raise UserError("ref_not_exist", name=repr(old))
    if old == new:
        raise UserError("branch_rename_same")
    if exists(new):
        raise UserError("branch_exists", name=repr(new))
    has_remote = bool(branch_config(old, "remote"))
    run_git_write("branch", "-m", "--", old, new)
    return {"old": old, "new": new, "has_remote": has_remote}
