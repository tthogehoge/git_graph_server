import os
import re
import subprocess
import time
from . import app_state as state
from .gitrun import (
    auth_for_remote, check_ref_name, git, git_c_locale, HTTP_AUTH_CACHE, is_http_auth_error,
    net_error, remember_auth, resolve_commit, run_git_net, run_git_write,
)
from .gitstate import (
    branch_config, has_head, is_cherry_picking, is_merging, is_rebasing, is_reverting, parse_status,
    paths_in_head, read_status_raw,
)
from .i18n import tr, UserError
from .routing import route


@route("/api/stash", method="POST")
def api_stash(body):
    """stash の apply / pop / drop。stash の位置 (stash@{n}) は変わるので、コミットのハッシュから毎回引き直す。
    apply / pop で競合したときは、git の通常の動作どおり作業ツリーに競合を残す (pop は stash も残る)。
    編集中のファイルや同名の管理外ファイルに邪魔されて戻せないときは、止めているファイルを返す ({blocked, modified, untracked})。
    on_conflict="discard" で再実行すると、止めているファイルだけを破棄して (stash の内容で上書きして) 戻す。"""
    op = body.get("op")
    if op not in ("apply", "pop", "drop"):
        raise ValueError("bad op")
    on_conflict = body.get("on_conflict")
    if on_conflict not in (None, "discard"):
        raise UserError("on_conflict_invalid")
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
    for _ in range(3):  # 止めているファイルを破棄して再実行する (1回で済むはずだが、念のため数回まで)
        r = git_c_locale("stash", op, sel)  # 止めているファイルを解析するため、C ロケールで実行する
        if r.returncode == 0:
            return {"ok": True, "message": tr("stash_applied" if op == "apply" else "stash_popped", sel=sel)}
        unmerged = subprocess.run(["git", "-C", state.REPO, "-c", "core.quotepath=false", "diff", "--name-only", "--diff-filter=U"],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
        if unmerged:
            raise ValueError(tr("stash_conflict_pop" if op == "pop" else "stash_conflict", sel=sel) + "\n" + unmerged)
        text = (r.stderr + r.stdout).strip()
        blockers = parse_checkout_blockers(text)
        if not (blockers["modified"] or blockers["untracked"]):
            raise ValueError(text or tr("stash_apply_failed"))
        if on_conflict is None:
            return {"blocked": True, **blockers}
        discard_blockers(blockers)
    raise UserError("stash_apply_failed")

@route("/api/stash-push", method="POST")
def api_stash_push(body):
    """作業ツリーの変更を stash に退避する (WIP から)。paths があればそのファイルだけ、なければすべて。
    untracked が false でなければ、未追跡ファイルも含める。message は任意。"""
    if not state.WIP_ENABLED or not state.HAS_WORKTREE:
        raise UserError("wip_disabled")
    if is_merging() or is_rebasing() or is_cherry_picking() or is_reverting():
        raise UserError("stash_push_blocked")
    if not has_head():
        raise UserError("stash_no_commits")
    message = body.get("message")
    message = message.strip() if isinstance(message, str) else ""
    if "\0" in message:
        raise ValueError("bad message")
    untracked = body.get("untracked") is not False
    paths = body.get("paths")
    pathspec = None
    if paths is not None:
        if not isinstance(paths, list) or not paths or not all(isinstance(p, str) and p and "\0" not in p for p in paths):
            raise UserError("select_files")
        s = parse_status(read_status_raw(), limit=10 ** 9)
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
    return {"ok": True, "message": tr("stash_pushed", sel="stash@{0}")}

ALREADY_EXISTS_RE = re.compile(r"(.+) already exists, no checkout$")

def parse_checkout_blockers(text):
    """checkout / merge / pull / rebase / cherry-pick / revert / stash apply が止まったときのエラーから、止めているファイルを取り出す。
    modified: 編集中 (ステージ済み含む) で上書きされてしまうファイル / untracked: 同名の管理外ファイル"""
    res = {"modified": [], "untracked": []}
    kind = None
    for line in text.splitlines():
        m = ALREADY_EXISTS_RE.match(line)
        if m:  # stash apply が、同名の管理外ファイルに邪魔されたとき
            res["untracked"].append(m.group(1))
            kind = None
            continue
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

def stash_top():
    r = subprocess.run(["git", "-C", state.REPO, "rev-parse", "-q", "--verify", "refs/stash"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout.strip()

def stash_push(message, paths=None, untracked=True):
    """git stash push を実行する。paths があればそのファイルだけ。stash が作られたら True (変更がなければ False)。"""
    before = stash_top()
    args = ["stash", "push"]
    if untracked:
        args.append("--include-untracked")
    if message:
        args += ["-m", message]
    if paths:
        run_git_write(*args, "--", *paths, literal=True)
    else:
        run_git_write(*args)
    return stash_top() != before

def stash_all_changes():
    """未コミットの変更 (未追跡ファイルも) を stash に退避する。作った stash の名前を返す (変更がなければ None)。"""
    created = stash_push(tr("stash_commit_msg", time=time.strftime("%Y-%m-%d %H:%M:%S")))
    return "stash@{0}" if created else None

def discard_blockers(blockers):
    """チェックアウトを止めているファイルだけを破棄する (編集中の変更は HEAD の状態に戻し、管理外ファイルは削除)。"""
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

DIRTY_RE = re.compile(r"You have unstaged changes|Your index contains uncommitted changes")  # git pull --rebase が止まるとき

def dirty_tracked_files():
    """HEAD から変わっている追跡中のファイル (ステージ済み・未ステージ)。ファイル名が出ない止まり方 (pull --rebase) のときの対象。"""
    r = git_c_locale("diff", "--name-only", "-z", "HEAD")
    return [p for p in r.stdout.split("\0") if p] if r.returncode == 0 else []

def dirty_worktree_blockers():
    """作業ツリーが汚れているときの modified / untracked 一覧。"""
    r = git_c_locale("ls-files", "-o", "-z", "--exclude-standard")
    untracked = [p for p in r.stdout.split("\0") if p] if r.returncode == 0 else []
    return {"modified": dirty_tracked_files(), "untracked": untracked}

def resolve_dirty_worktree(mode):
    """作業ツリーがきれいである必要がある操作向け。汚れていれば checkout と同じ blocked / stash / discard で扱う。
    戻り値は (blocked, stash の名前)。blocked は {blocked, modified, untracked} (mode が None のときだけ)。"""
    if mode not in (None, "stash", "discard"):
        raise UserError("on_conflict_invalid")
    blockers = dirty_worktree_blockers()
    if not (blockers["modified"] or blockers["untracked"]):
        return None, None
    if mode is None:
        return {"blocked": True, **blockers}, None
    if mode == "stash":
        return None, stash_all_changes()
    discard_blockers(blockers)
    leftover = dirty_worktree_blockers()
    if leftover["modified"] or leftover["untracked"]:
        raise ValueError(tr("op_still_blocked"))
    return None, None

def run_resolving(run, mode, rebase_dirty=False):
    """編集中のファイルや同名の管理外ファイルに邪魔されて止まる git コマンド (pull / merge / rebase / cherry-pick など) を、チェックアウトと同じ方法で実行する。
    run(c_locale) は git を実行して CompletedProcess を返す (c_locale=True は、メッセージを解析できるよう C ロケールで)。
    mode: None=止まったら止めているファイルを返す / "stash"=最初に全変更を stash する / "discard"=止めているファイルだけ破棄して再実行する。
    戻り値は (最後の実行結果, blocked, stash の名前)。blocked は {blocked, modified, untracked} (mode が None で止まったときだけ)。
    止まった原因がそれ以外 (競合・通信エラーなど) なら blocked は None で、呼び出し側が実行結果を見て処理する。
    rebase_dirty: rebase は「変更があるので実行できない」とだけ言いファイル名を出さないので、変更中の全ファイルを対象にする。"""
    if mode not in (None, "stash", "discard"):
        raise UserError("on_conflict_invalid")
    stashed = stash_all_changes() if mode == "stash" else None
    r = run(False)
    for _ in range(3):
        if r.returncode == 0 or is_merging() or is_rebasing() or is_cherry_picking() or is_reverting():
            return r, None, stashed
        text = (r.stderr + r.stdout)
        if is_http_auth_error(r) or not (rebase_dirty or any(l.startswith("\t") for l in text.splitlines())):
            return r, None, stashed  # 止めているファイルの一覧 (タブ字下げ) がない失敗は、再実行して調べるまでもない
        c = run(True)  # 止めているファイルを解析するため、C ロケールで再実行する (止まる失敗は何も変更していない)
        if c.returncode == 0:
            return c, None, stashed
        text = c.stderr + c.stdout
        blockers = parse_checkout_blockers(text)
        if rebase_dirty and not (blockers["modified"] or blockers["untracked"]) and DIRTY_RE.search(text):
            blockers["modified"] = dirty_tracked_files()
        if not (blockers["modified"] or blockers["untracked"]):
            return r, None, stashed
        if mode is None:
            return r, {"blocked": True, **blockers}, None
        if mode == "stash":
            raise ValueError(with_stash_note({"stashed": stashed}, tr("op_still_blocked") + "\n" + text.strip()))
        discard_blockers(blockers)
        r = run(False)
    return r, None, stashed

def with_stash_note(res, msg):
    if res and res.get("stashed"):
        msg += tr("stash_note", stash=res["stashed"])
    return msg

@route("/api/branch", method="POST")
def api_branch(body):
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

@route("/api/discard", method="POST")
def api_discard(body):
    """選んだファイルの変更を破棄する (取り消せない)。追跡中のファイルはステージ済み・未ステージとも HEAD の状態に戻し、
    HEAD にない新規ファイル (ステージ済み) は削除し、未追跡ファイルは削除する。競合中のファイルと、マージなどの途中は対象外。"""
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

@route("/api/rm", method="POST")
def api_rm(body):
    """追跡中のファイルを git rm する (削除をステージする)。cached=True なら作業ツリーのファイルは残して追跡だけやめる (git rm --cached)。
    ステージ済みの変更や編集中の変更があるファイルは、git と同じく force=True でないと失敗する。"""
    if not state.WIP_ENABLED:
        raise UserError("wip_disabled")
    paths = body.get("paths")
    if not isinstance(paths, list) or not paths or not all(isinstance(p, str) and p for p in paths):
        raise UserError("select_files")
    paths = sorted(set(paths))
    step = 200
    tracked = set()
    for i in range(0, len(paths), step):
        out = git("ls-files", "-z", "--", *paths[i:i + step], literal=True)
        tracked.update(p for p in out.split("\0") if p)
    bad = [p for p in paths if p not in tracked]  # 追跡中のファイルだけ (ディレクトリや未追跡ファイルは対象外)
    if bad:
        raise UserError("rm_not_tracked", files=", ".join(bad[:3]))
    cached = body.get("cached") is True
    opts = (["--cached"] if cached else []) + (["-f"] if body.get("force") is True else [])
    for i in range(0, len(paths), step):
        run_git_write("rm", "-q", *opts, "--", *paths[i:i + step], literal=True)
    return {"ok": True, "message": tr("rm_cached_done" if cached else "rm_done", n=len(paths))}

@route("/api/branch-rename", method="POST")
def api_branch_rename(body):
    """ローカルブランチの名前を変える (git branch -m)。リモートのブランチは変わらない。"""
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
    "api_branch", "api_branch_rename", "api_checkout", "api_delete_ref", "api_discard", "api_reset", "api_rm",
    "api_stash", "api_tag", "checkout_resolving", "discard_blockers", "parse_checkout_blockers",
    "ALREADY_EXISTS_RE", "DIRTY_RE", "api_stash_push", "dirty_tracked_files",
    "dirty_worktree_blockers", "resolve_dirty_worktree", "run_resolving",
    "stash_all_changes", "stash_push", "stash_top", "with_stash_note",
]
