import os
import subprocess
import app_state as state
from routing import route


CONFLICT_CODES = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}

def read_status_raw():
    from gitrun import git
    return git("status", "--porcelain=v1", "-z", "--untracked-files=all")

def parse_status(raw, limit=5000):
    """git status --porcelain=v1 -z を ステージ済み / 未ステージ / 未追跡 / 競合 に分ける (パスはリポジトリルート基準)。"""
    res = {"staged": [], "unstaged": [], "untracked": [], "conflicts": [], "truncated": False}
    toks = raw.split("\0")
    i = n = 0
    while i < len(toks):
        t = toks[i]
        i += 1
        if len(t) < 4 or t[2] != " ":
            continue
        xy, path = t[:2], t[3:]
        old = None
        if "R" in xy or "C" in xy:  # リネーム/コピーは続くトークンが元のパス
            old = toks[i] if i < len(toks) else None
            i += 1
        if xy == "!!":
            continue
        n += 1
        if n > limit:
            res["truncated"] = True
            break
        if xy == "??":
            res["untracked"].append({"path": path, "st": "?", "label": "new_file"})  # label は翻訳キー (画面側で翻訳する)
        elif xy in CONFLICT_CODES:
            res["conflicts"].append({"path": path, "st": "U", "label": "label_conflict"})
        else:
            x, y = xy[0], xy[1]
            if x != " ":
                res["staged"].append({"path": path, "old_path": old if x in "RC" else None, "st": x})
            if y != " ":
                res["unstaged"].append({"path": path, "old_path": None, "st": y})
    return res

def wip_count(status):
    return len({f["path"] for k in ("staged", "unstaged", "untracked", "conflicts") for f in status[k]})

def amend_base():
    """amend 後のコミットの親 (HEAD^)。最初のコミットなら空ツリー。"""
    from gitrun import git
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
    from gitrun import git
    names = parse_name_status(git("diff", "--name-status", "-z", "-M", base, "HEAD", "--"))
    return {path: old for path, (_, old) in names.items()}

def amend_files(s):
    """amend 後のコミットの内容 = 「直前のコミットの親」と作業ツリーの差分のファイル一覧。
    直前のコミットで変更したファイルは in_head=True (チェックを外すとコミットから外れる)。WIP の変更があるものは tag=WIP。"""
    from gitrun import git
    from viewer import parse_numstat
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
    from gitrun import git
    from i18n import UserError
    from rebase import is_cherry_picking, is_merging, is_rebasing, is_reverting
    from viewer import commit_info, parse_numstat
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
    from gitrun import git_bytes
    from i18n import UserError, tr
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
    from gitrun import check_ref_name, git, run_git_write
    from i18n import UserError, tr
    from rebase import api_head, is_merging, is_rebasing
    from refops import checkout_resolving, has_head, has_parent
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
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths) or (not paths and not amend):
        raise UserError("select_files")  # amend はメッセージだけの変更 (paths が空) もできる
    checked = set(paths)
    s = parse_status(read_status_raw(), limit=10 ** 9)
    known = {f["path"] for k in ("staged", "unstaged", "untracked", "conflicts") for f in s[k]}
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
    # すでにステージ済みの削除は add -A できない (パスが見つからず失敗する) が、インデックスにあるのでそのままコミットに入る
    gone = {f["path"] for f in s["staged"] if f["st"] == "D"} - {f["path"] for k in ("unstaged", "untracked") for f in s[k]}
    todo = sorted(checked - gone)
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
    "CONFLICT_CODES", "amend_base", "amend_files", "amend_head_changes",
    "api_status", "api_wip_commit", "parse_name_status", "parse_status",
    "read_status_raw", "wip_count", "wip_diff",
]
