import os
import subprocess
import time
from . import app_state as state
from .routing import route


@route("/api/stash", method="POST")
def api_stash(body):
    """stash の apply / pop / drop。stash の位置 (stash@{n}) は変わるので、コミットのハッシュから毎回引き直す。
    apply / pop で競合したときは、git の通常の動作どおり作業ツリーに競合を残す (pop は stash も残る)。"""
    from .gitrun import git, resolve_commit, run_git_write
    from .i18n import UserError, tr
    from .rebase import is_cherry_picking, is_merging, is_rebasing, is_reverting
    op = body.get("op")
    if op not in ("apply", "pop", "drop"):
        raise ValueError("bad op")
    h = resolve_commit(body.get("hash", ""))
    sel = None
    for l in git("stash", "list", "--format=%H %gd").splitlines():
        sh, _, name = l.partition(" ")
        if sh == h:
            sel = name
            break
    if sel is None:
        raise UserError("stash_not_found")
    if op == "drop":
        run_git_write("stash", "drop", sel)
        return {"ok": True, "message": tr("stash_dropped", sel=sel, hash=h[:7])}
    if not state.HAS_WORKTREE:
        raise UserError("stash_no_worktree")
    if is_merging() or is_rebasing() or is_cherry_picking() or is_reverting():
        raise UserError("op_in_progress")
    r = subprocess.run(["git", "-C", state.REPO, "stash", op, sel], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        unmerged = subprocess.run(["git", "-C", state.REPO, "-c", "core.quotepath=false", "diff", "--name-only", "--diff-filter=U"],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
        if unmerged:
            raise ValueError(tr("stash_conflict_pop" if op == "pop" else "stash_conflict", sel=sel) + "\n" + unmerged)
        raise ValueError((r.stderr or r.stdout).strip() or tr("stash_apply_failed"))
    return {"ok": True, "message": tr("stash_applied" if op == "apply" else "stash_popped", sel=sel)}

def parse_checkout_blockers(text):
    """checkout が止まったときのエラーから、止めているファイルを取り出す。
    modified: 編集中 (ステージ済み含む) で上書きされてしまうファイル / untracked: 同名の管理外ファイル"""
    res = {"modified": [], "untracked": []}
    kind = None
    for line in text.splitlines():
        if "would be overwritten by" in line:
            if "local changes" in line:
                kind = "modified"
            elif "untracked working tree files" in line:
                kind = "untracked"
            else:
                kind = None
            continue
        if kind and line.startswith("\t") and line.strip():
            res[kind].append(line[1:])
        else:
            kind = None
    return res

def stash_all_changes():
    """未コミットの変更 (未追跡ファイルも) を stash に退避する。作った stash の名前を返す (変更がなければ None)。"""
    from .gitrun import run_git_write
    from .i18n import tr
    def top():
        r = subprocess.run(["git", "-C", state.REPO, "rev-parse", "-q", "--verify", "refs/stash"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        return r.stdout.strip()
    before = top()
    run_git_write("stash", "push", "--include-untracked", "-m",
                  tr("stash_commit_msg", time=time.strftime("%Y-%m-%d %H:%M:%S")))
    return "stash@{0}" if top() != before else None

def discard_blockers(blockers):
    """チェックアウトを止めているファイルだけを破棄する (編集中の変更は HEAD の状態に戻し、管理外ファイルは削除)。"""
    from .gitrun import git, git_c_locale, run_git_write
    from .i18n import UserError
    top = git("rev-parse", "--show-toplevel").strip()
    top_real = os.path.realpath(top)
    for path in blockers["modified"]:
        in_head = git_c_locale("cat-file", "-e", "HEAD:" + path, cwd=top).returncode == 0
        if in_head:
            run_git_write("checkout", "HEAD", "--", path, literal=True, cwd=top)
        else:  # インデックスにだけ追加されたファイル
            run_git_write("rm", "-f", "-q", "--", path, literal=True, cwd=top)
    for path in blockers["untracked"]:
        full = os.path.join(top_real, path)
        parent = os.path.realpath(os.path.dirname(full))
        if os.path.commonpath([parent, top_real]) != top_real:
            raise UserError("path_outside_repo", path=path)
        if os.path.isdir(full) and not os.path.islink(full):
            raise UserError("dir_no_autodelete", path=path)
        if os.path.lexists(full):
            os.remove(full)

def checkout_resolving(args, mode):
    """git checkout <args> を実行する。成功なら None を返す。
    編集中のファイル/同名の管理外ファイルで止まったとき、mode が None なら {blocked, modified, untracked} を返し、
    "stash" なら全変更を退避してから、"discard" なら止めているファイルだけ破棄してから再実行する。"""
    from .gitrun import git_c_locale
    from .i18n import UserError, tr
    if mode not in (None, "stash", "discard"):
        raise UserError("on_conflict_invalid")
    stashed = None
    if mode == "stash":
        stashed = stash_all_changes()
    for _ in range(3):
        r = git_c_locale("checkout", *args)
        if r.returncode == 0:
            return {"stashed": stashed} if stashed else None
        text = (r.stderr + r.stdout).strip()
        blockers = parse_checkout_blockers(text)
        if not (blockers["modified"] or blockers["untracked"]):
            raise ValueError(text or tr("checkout_failed"))
        if mode is None:
            return {"blocked": True, **blockers}
        if mode == "stash":
            raise ValueError(tr("checkout_still_blocked") + "\n" + text)
        discard_blockers(blockers)
    raise UserError("checkout_failed")

def with_stash_note(res, msg):
    from .i18n import tr
    if res and res.get("stashed"):
        msg += tr("stash_note", stash=res["stashed"])
    return msg

@route("/api/branch", method="POST")
def api_branch(body):
    from .gitrun import check_ref_name, resolve_commit, run_git_write
    from .i18n import UserError, tr
    h = resolve_commit(body.get("hash", ""))
    name = str(body.get("name", "")).strip()
    check_ref_name("branch", name)
    if body.get("checkout"):
        upstream = body.get("upstream")
        if upstream:
            if not isinstance(upstream, str) or upstream.startswith("-") or "\0" in upstream:
                raise UserError("remote_branch_not_found")
            ref = "refs/remotes/" + upstream
            r = subprocess.run(["git", "-C", state.REPO, "rev-parse", "--verify", "--quiet", ref + "^{commit}"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode != 0 or r.stdout.strip() != h:
                raise UserError("remote_branch_not_found")
            checkout_args = ["-b", name, "--track", upstream]
        else:
            checkout_args = ["-b", name, h]
        res = checkout_resolving(checkout_args, body.get("on_conflict"))
        if res and res.get("blocked"):
            return res
        return {"ok": True, "message": with_stash_note(res, tr("branch_created_checkout", name=name))}
    run_git_write("branch", name, h)
    return {"ok": True, "message": tr("branch_created", name=name)}

@route("/api/tag", method="POST")
def api_tag(body):
    from .gitrun import check_ref_name, resolve_commit, run_git_write
    from .i18n import tr
    h = resolve_commit(body.get("hash", ""))
    name = str(body.get("name", "")).strip()
    msg = str(body.get("message", "")).strip()
    check_ref_name("tag", name)
    if msg:
        run_git_write("tag", "-a", name, "-m", msg, h)
        return {"ok": True, "message": tr("tag_created_annotated", name=name)}
    run_git_write("tag", name, h)
    return {"ok": True, "message": tr("tag_created", name=name)}

@route("/api/ref-delete", method="POST")
def api_delete_ref(body):
    from .gitrun import HTTP_AUTH_CACHE, auth_for_remote, check_ref_name, git, is_http_auth_error, net_error, remember_auth, run_git_net, run_git_write
    from .i18n import UserError, tr
    kind = body.get("kind")
    if kind == "remote-branch":
        name = str(body.get("name", "")).strip()
        remotes = git("remote").split()
        remote = next((r for r in sorted(remotes, key=len, reverse=True) if name.startswith(r + "/")), None)
        branch = name[len(remote) + 1:] if remote else ""
        if not remote or not branch:
            raise UserError("remote_branch_not_found")
        check_ref_name("branch", branch)
        auth = auth_for_remote(remote, body)
        r = run_git_net("push", remote, "--delete", branch, auth=auth)
        if r.returncode != 0:
            if is_http_auth_error(r) and not body.get("auth_retry"):
                return {"auth_required": True, "auth_remote": remote}
            if body.get("auth_retry"):
                HTTP_AUTH_CACHE.pop(remote, None)
            raise ValueError(net_error(r, tr("git_command_failed")))
        remember_auth(remote, auth)
        return {"ok": True, "message": tr("remote_branch_deleted", name=name)}
    if kind not in ("branch", "tag"):
        raise UserError("ref_delete_only")
    name = str(body.get("name", "")).strip()
    check_ref_name(kind, name)
    ref = ("refs/heads/" if kind == "branch" else "refs/tags/") + name
    exists = subprocess.run(["git", "-C", state.REPO, "show-ref", "--verify", "--quiet", ref])
    if exists.returncode != 0:
        raise UserError("ref_not_exist", name=repr(name))
    if kind == "branch":
        if body.get("force") is True:
            run_git_write("branch", "-D", "--", name)
        else:
            r = subprocess.run(["git", "-C", state.REPO, "branch", "-d", "--", name],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode != 0:
                ancestor = subprocess.run(["git", "-C", state.REPO, "merge-base", "--is-ancestor", name, "HEAD"])
                if ancestor.returncode == 1:
                    return {"needs_force": True}
                raise ValueError((r.stderr or r.stdout).strip() or tr("branch_delete_failed"))
        return {"ok": True, "message": tr("branch_deleted", name=name)}
    run_git_write("tag", "-d", "--", name)
    return {"ok": True, "message": tr("tag_deleted", name=name)}

@route("/api/checkout", method="POST")
def api_checkout(body):
    from .gitrun import check_ref_name, resolve_commit
    from .i18n import tr
    branch = body.get("branch")
    if branch:
        branch = str(branch)
        check_ref_name("branch", branch)
        res = checkout_resolving([branch, "--"], body.get("on_conflict"))
        if res and res.get("blocked"):
            return res
        return {"ok": True, "message": with_stash_note(res, tr("checkout_branch_done", branch=branch))}
    h = resolve_commit(body.get("hash", ""))
    res = checkout_resolving(["--detach", h], body.get("on_conflict"))
    if res and res.get("blocked"):
        return res
    return {"ok": True, "message": with_stash_note(res, tr("checkout_detached_done", hash=h[:7]))}

@route("/api/reset", method="POST")
def api_reset(body):
    """現在の HEAD を選択したコミットへ移動する。"""
    from .gitrun import git, resolve_commit, run_git_write
    from .i18n import UserError, tr
    from .rebase import is_cherry_picking, is_merging, is_rebasing, is_reverting
    mode = body.get("mode", "mixed")
    if mode not in ("soft", "mixed", "hard"):
        raise UserError("reset_mode_invalid")
    if is_merging() or is_rebasing() or is_cherry_picking() or is_reverting():
        raise UserError("reset_blocked")
    h = resolve_commit(body.get("hash", ""))
    before = git("rev-parse", "HEAD").strip() if has_head() else None
    run_git_write("reset", "--" + mode, h)
    if before == h:
        return {"ok": True, "message": tr("reset_noop", hash=h[:7])}
    return {"ok": True, "message": tr("reset_done", hash=h[:7], mode=mode)}

def has_head():
    return subprocess.run(["git", "-C", state.REPO, "rev-parse", "-q", "--verify", "HEAD"],
                          capture_output=True).returncode == 0

def has_parent():
    return subprocess.run(["git", "-C", state.REPO, "rev-parse", "-q", "--verify", "HEAD^"],
                          capture_output=True).returncode == 0

@route("/api/discard", method="POST")
def api_discard(body):
    """選んだファイルの変更を破棄する (取り消せない)。追跡中のファイルはステージ済み・未ステージとも HEAD の状態に戻し、
    HEAD にない新規ファイル (ステージ済み) は削除し、未追跡ファイルは削除する。競合中のファイルと、マージなどの途中は対象外。"""
    from .gitrun import run_git_write
    from .i18n import UserError, tr
    from .rebase import is_cherry_picking, is_merging, is_rebasing, is_reverting, paths_in_head
    from .wip import parse_status, read_status_raw
    if not state.WIP_ENABLED:
        raise UserError("wip_disabled")
    if is_merging() or is_rebasing() or is_cherry_picking() or is_reverting():
        raise UserError("discard_blocked")
    paths = body.get("paths")
    if not isinstance(paths, list) or not paths or not all(isinstance(p, str) for p in paths):
        raise UserError("select_files")
    checked = set(paths)
    s = parse_status(read_status_raw(), limit=10 ** 9)
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
    step = 200
    for i in range(0, len(remove), step):  # HEAD にないファイル (git add 済みの新規ファイル): インデックスと作業ツリーから消す
        run_git_write("rm", "-q", "-f", "--", *remove[i:i + step], literal=True)
    restore = sorted(restore)
    for i in range(0, len(restore), step):
        run_git_write("checkout", "HEAD", "--", *restore[i:i + step], literal=True)
    for i in range(0, len(clean), step):
        run_git_write("clean", "-f", "-q", "--", *clean[i:i + step], literal=True)
    return {"ok": True, "message": tr("discarded", n=len(checked))}

@route("/api/branch-rename", method="POST")
def api_branch_rename(body):
    """ローカルブランチの名前を変える (git branch -m)。リモートのブランチは変わらない。"""
    from .gitrun import check_ref_name, run_git_write
    from .i18n import UserError, tr
    from .remote import branch_config
    old = str(body.get("old", "")).strip()
    new = str(body.get("new", "")).strip()
    if not old or "\0" in old:
        raise UserError("branch_name_invalid")
    check_ref_name("branch", new)
    exists = lambda name: subprocess.run(["git", "-C", state.REPO, "show-ref", "--verify", "--quiet", "refs/heads/" + name],
                                         capture_output=True).returncode == 0
    if not exists(old):
        raise UserError("ref_not_exist", name=repr(old))
    if old == new:
        raise UserError("branch_rename_same")
    if exists(new):
        raise UserError("branch_exists", name=repr(new))
    has_remote = bool(branch_config(old, "remote"))
    run_git_write("branch", "-m", "--", old, new)
    return {"ok": True, "message": tr("branch_renamed_remote" if has_remote else "branch_renamed", old=old, new=new)}


__all__ = [
    "api_branch", "api_branch_rename", "api_checkout", "api_delete_ref",
    "api_discard", "api_reset", "api_stash", "api_tag", "checkout_resolving",
    "discard_blockers", "parse_checkout_blockers", "stash_all_changes",
    "with_stash_note",
]
