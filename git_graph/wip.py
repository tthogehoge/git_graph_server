import os
import subprocess
from . import app_state as state
from .gitrun import check_ref_name, git, git_bytes, run_git_write
from .gitstate import (
    commit_info, has_head, has_parent, is_cherry_picking, is_merging, is_rebasing,
    is_reverting, parse_numstat, parse_status, read_status_raw,
)
from .i18n import tr, UserError
from .rebase import api_head
from .engine.dirty import checkout_resolving
from .routing import route


def wip_count(status):
    return len({f["path"] for k in ("staged", "unstaged", "untracked", "conflicts") for f in status[k]})

def amend_base():
    """amend 後のコミットの親 (HEAD^)。最初のコミットなら空ツリー。"""
    try:
        return git("rev-parse", "--verify", "--quiet", "HEAD^").strip()
    except subprocess.CalledProcessError:
        return git("hash-object", "-t", "tree", "/dev/null").strip()

def parse_name_status(raw):
    """--name-status -z の出力を {新しいパス: (変更の種類(M/A/D/R...), 元のパス or None)} にする。"""
    toks = raw.split("\0")
    out, i = {}, 0
    while i < len(toks):
        t = toks[i].lstrip("\n")
        if not t:
            i += 1
            continue
        if t[0] in "RC":
            if i + 2 >= len(toks):
                break
            out[toks[i + 2]] = (t[0], toks[i + 1])
            i += 3
        else:
            if i + 1 >= len(toks):
                break
            out[toks[i + 1]] = (t[0], None)
            i += 2
    return out

def amend_head_changes(base):
    """直前のコミット (HEAD) が変更したファイル {新しいパス: 元のパス or None}。"""
    names = parse_name_status(git("diff", "--name-status", "-z", "-M", base, "HEAD", "--"))
    return {path: old for path, (_, old) in names.items()}

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

def addable_paths(paths, step=200):
    """add -A できるパスだけにする。作業ツリーにもインデックスにも無いパス (直前のコミットで git rm 済みのファイルなど) は、
    add -A が「pathspec が見つからない」で失敗する。そのパスはインデックスの状態のままコミットに入る。"""
    paths = sorted(paths)
    in_index = set()
    for i in range(0, len(paths), step):
        out = git("ls-files", "-z", "--", *paths[i:i + step], literal=True)
        in_index.update(p for p in out.split("\0") if p)
    return [p for p in paths if p in in_index or os.path.lexists(os.path.join(state.REPO, p))]

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

@route("/api/wip-commit", method="POST")
def api_wip_commit(body):
    """チェックされたファイルを add してコミットする (TortoiseGit のコミットダイアログと同じ考え方)。
    ステージ済みでチェックのないファイルは、ステージから外す (作業ツリーの内容はそのまま)。"""
    if not state.WIP_ENABLED:
        raise UserError("wip_disabled")
    amend = body.get("amend") is True
    branch = str(body.get("branch", "")).strip()
    if branch and amend:
        raise UserError("amend_blocked")
    if branch:
        check_ref_name("branch", branch)
        current = api_head().get("branch")
        if branch != current:
            exists = subprocess.run(["git", "-C", state.REPO, "rev-parse", "--verify", "--quiet", "refs/heads/" + branch],
                                    capture_output=True).returncode == 0
            if exists:
                checkout = checkout_resolving([branch], None)
                if checkout and checkout.get("blocked"):
                    raise ValueError(tr("checkout_failed"))
            elif has_head():
                run_git_write("branch", branch, "HEAD")
                run_git_write("checkout", branch)
            else:
                run_git_write("checkout", "-b", branch)
    if amend:
        if not has_head():
            raise UserError("amend_no_commit")
        if is_merging() or is_rebasing():
            raise UserError("amend_blocked")
    msg = str(body.get("message", "")).strip()
    if not msg:
        raise UserError("message_required")
    paths = body.get("paths")
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
        raise UserError("select_files")
    if not paths and not body.get("staged_paths") and not amend:
        raise UserError("select_files")  # amend はメッセージだけの変更 (paths が空) もできる
    staged_only = body.get("staged_paths") or []  # ステージ済みの内容だけをコミットする (未ステージの変更は add しない)
    if not isinstance(staged_only, list) or not all(isinstance(p, str) for p in staged_only):
        raise UserError("select_files")
    if amend and staged_only:
        raise UserError("amend_blocked")
    keep = set(staged_only) - set(paths)  # paths にもあるものは、作業ツリーの内容をコミットする
    checked = set(paths) | keep  # コミットに入れるファイル
    s = parse_status(read_status_raw(), limit=10 ** 9)
    known = {f["path"] for k in ("staged", "unstaged", "untracked", "conflicts") for f in s[k]}
    not_staged = keep - {f["path"] for f in s["staged"]}
    if not_staged:
        raise UserError("files_unchanged", files=", ".join(sorted(not_staged)[:3]))
    head_changes = amend_head_changes(amend_base()) if amend else {}  # 直前のコミットが変更したファイル (チェックを外すとコミットから外す)
    if amend and head_changes and not checked:  # index を触る前に確認する (空のコミットは git が拒否する)
        raise UserError("amend_would_empty")
    bad = checked - known - set(head_changes)  # 任意のパスを add させないよう、いま変更ありと報告されているものだけを対象にする
    if bad:
        raise UserError("files_unchanged", files=", ".join(sorted(bad)[:3]))
    unstage = []
    for f in s["staged"]:
        if f["path"] not in checked:
            unstage.append(f["path"])
            if f.get("old_path"):  # リネームは元のパスも一緒に戻す
                unstage.append(f["old_path"])
    if is_merging() and (unstage or any(f["path"] not in checked for f in s["conflicts"])):
        raise UserError("merging_need_all")
    step = 200  # コマンドラインが長くなりすぎないように分割する
    if unstage:
        for i in range(0, len(unstage), step):
            part = unstage[i:i + step]
            if has_head():
                run_git_write("reset", "-q", "--", *part, literal=True)
            else:  # 最初のコミット前は HEAD がないので、インデックスから外す
                run_git_write("rm", "--cached", "-r", "-q", "-f", "--", *part, literal=True)
    drop = []  # amend: チェックを外した「直前のコミットのファイル」は、コミットから外す (親の状態に戻す。作業ツリーはそのまま)
    for path, old in head_changes.items():
        if path not in checked:
            drop.append(path)
            if old:
                drop.append(old)
    if drop:
        base = amend_base()
        for i in range(0, len(drop), step):
            part = drop[i:i + step]
            if has_parent():
                run_git_write("reset", "-q", base, "--", *part, literal=True)
            else:  # 最初のコミットには親がないので、インデックスから外す
                run_git_write("rm", "--cached", "-r", "-q", "-f", "--", *part, literal=True)
    # ステージ済みの削除や、直前のコミットで git rm 済みのファイルは add -A できない (パスが見つからず失敗する) が、
    # インデックスにはその状態が入っているのでそのままコミットに入る
    todo = addable_paths(set(paths))
    for i in range(0, len(todo), step):
        run_git_write("add", "-A", "--", *todo[i:i + step], literal=True)
    run_git_write("commit", *(["--amend"] if amend else []), "-m", msg)
    h = git("rev-parse", "HEAD").strip()
    if not amend:
        message = tr("wip_committed", hash=h[:7], n=len(checked))
    elif not checked:
        message = tr("wip_amended_msg_only", hash=h[:7])
    elif drop:
        message = tr("wip_amended_dropped", hash=h[:7], n=len(checked),
                     dropped=len([d for d in head_changes if d not in checked]))
    else:
        message = tr("wip_amended", hash=h[:7], n=len(checked))
    return {"ok": True, "message": message}


__all__ = [
    "addable_paths", "amend_base", "amend_files", "amend_head_changes", "api_status", "api_wip_commit",
    "parse_name_status", "wip_count", "wip_diff",
]
