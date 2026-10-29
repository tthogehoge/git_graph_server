# 作業ツリーが汚れているときの扱い。編集中のファイルや同名の管理外ファイルに邪魔されて止まる git の操作
# (checkout / pull / merge / rebase / cherry-pick / stash apply など) を、stash か破棄のどちらかで解決して再実行する。
# 呼び出し側 (route) は、止まった結果 (blocked) をそのまま画面に返し、ユーザーが選んだ方法 (mode) で再度呼ぶ。
import os
import re
import subprocess
import time
from .. import app_state as state
from ..gitrun import git, git_c_locale, is_http_auth_error, run_git_write
from ..gitstate import is_cherry_picking, is_merging, is_rebasing, is_reverting
from ..i18n import tr, UserError


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
