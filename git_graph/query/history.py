# ログ・ブランチ・タグ・reflog・stash 一覧・コミット詳細・比較・差分の読み取り。
import re
import subprocess
from .. import app_state as state
from ..textcodec import decode_text
from ..gitrun import check_ref_name, git, git_bytes, HASH_RE, resolve_commit
from ..gitstate import commit_info, parse_numstat
from ..i18n import tr, UserError
from .status import status as worktree_status, wip_diff


# コミット / A..B / A..B^@ (B の親たち。A..B から B 自身を除いた範囲 = グラフの線の間のコミット)
REV_RE = re.compile(r"[0-9a-f]{40}(\.\.[0-9a-f]{40}(\^@)?)?")

DIFF_LIMIT = 400000


def rev_args(rev):
    """rev を git に渡す引数に直す。A..B^@ は「A から見えない B の親たち」(B 自身は含めない)。"""
    if not rev.endswith("^@"):
        return [rev]
    a, b = rev[:-2].split("..")
    parents = git("rev-list", "--parents", "-n", "1", b, "--").split()[1:]
    return [f"^{a}"] + parents


def paging(skip, limit):
    skip = int(skip)
    limit = min(int(limit), 2000)
    if skip < 0 or limit < 1:
        raise ValueError("bad paging")
    return skip, limit


def log(rev, path="", skip=0, limit=500, graph=False):
    if not REV_RE.fullmatch(rev):
        raise ValueError("bad rev")
    if "\0" in path:
        raise ValueError("bad path")
    skip, limit = paging(skip, limit)
    args = ["log", "--parents", "--date=short", f"--skip={skip}", "-n", str(limit),
            "--format=%H%x1f%h%x1f%an%x1f%ad%x1f%P%x1f%s"]
    if graph:
        args.append("--topo-order")
    args += rev_args(rev) + ["--"]
    if path:
        args.append(path)
    out = git(*args, literal=bool(path))
    commits = []
    for l in out.splitlines():
        if not l:
            continue
        h, short, author, date, parents, subj = (l.split("\x1f", 5) + [""] * 6)[:6]
        commits.append([h, short, author, date, subj, parents.split()])
    if graph and ".." in rev and commits:
        listed = set(git("rev-list", *rev_args(rev), "--").split())
        for c in commits:
            c[5] = [p for p in c[5] if p in listed]
    res = {"commits": commits, "has_more": len(commits) == limit}
    if skip == 0:
        count_args = ["rev-list", "--count", *rev_args(rev), "--"]
        if path:
            count_args.append(path)
        res["total"] = int(git(*count_args, literal=bool(path)).strip())
    return res


def stash_list():
    """stash の一覧 (新しい順)。api_log と同じ [hash, short, 選択子, 日時, 件名] の形。"""
    out = git("stash", "list", "--date=short", "--format=%H%x1f%h%x1f%gd%x1f%ad%x1f%s")
    commits = []
    for l in out.splitlines():
        if not l:
            continue
        h, short, sel, date, subj = (l.split("\x1f", 4) + [""] * 5)[:5]
        commits.append([h, short, sel, date, subj])
    return {"commits": commits, "has_more": False, "total": len(commits)}


def _ref_list(refs, fmt):
    try:
        out = git("for-each-ref", f"--format={fmt}", refs)
    except subprocess.CalledProcessError:
        return []
    return [l for l in out.splitlines() if l]


def branches():
    """ローカルブランチ → リモート追跡ブランチの順。現在のブランチには ★。"""
    head = git("rev-parse", "--abbrev-ref", "HEAD").strip()
    local = _ref_list("refs/heads", "%(refname:short)%x1f%(objectname:short)%x1f%(objectname)")
    remote = _ref_list("refs/remotes", "%(refname:short)%x1f%(objectname:short)%x1f%(objectname)")
    rows = []
    for l in local:
        name, short, full = l.split("\x1f", 2)
        mark = "★" if name == head else ""
        rows.append([name, short, full, mark, "local"])
    for l in remote:
        name, short, full = l.split("\x1f", 2)
        if name.endswith("/HEAD"):
            continue
        rows.append([name, short, full, "", "remote"])
    return {"branches": rows, "head": head}


def branches_containing(hash_):
    h = resolve_commit(hash_)
    local = git("branch", "--contains", h, "--format=%(refname:short)%x1f%(objectname)").splitlines()
    remote = git("branch", "-r", "--contains", h, "--format=%(refname:short)%x1f%(objectname)").splitlines()
    rows = []
    for l in local:
        if not l:
            continue
        name, tip = l.split("\x1f", 1)
        rows.append([name, tip, "local"])
    for l in remote:
        if not l or l.endswith("/HEAD"):
            continue
        name, tip = l.split("\x1f", 1)
        rows.append([name, tip, "remote"])
    return {"branches": rows, "hash": h}


def tags():
    out = _ref_list("refs/tags", "%(refname:short)%x1f%(objectname:short)%x1f%(*objectname)%x1f%(objectname)")
    rows = []
    for l in out:
        name, short, peeled, full = (l.split("\x1f", 3) + [""] * 4)[:4]
        tip = peeled or full
        rows.append([name, short, tip])
    rows.reverse()  # 新しい順に近づける
    return {"tags": rows}


def reflog(branch="", skip=0, limit=500):
    skip, limit = paging(skip, limit)
    ref = "HEAD" if not branch else check_ref_name(branch)
    args = ["reflog", "show", "--date=short", f"--skip={skip}", f"-n", str(limit),
            f"--format=%H%x1f%h%x1f%gd%x1f%ad%x1f%s", ref]
    out = git(*args)
    commits = []
    for l in out.splitlines():
        if not l:
            continue
        h, short, sel, date, subj = (l.split("\x1f", 4) + [""] * 5)[:5]
        commits.append([h, short, sel, date, subj])
    return {"commits": commits, "has_more": len(commits) == limit}


def commit(hash_):
    h = resolve_commit(hash_)
    info = commit_info(h)
    info["hash"] = h
    return info


def compare(from_hash, to_hash):
    """2コミット間、またはコミットと WIP の差分ファイル一覧。"""
    if to_hash.upper() == "WIP":
        st = worktree_status()
        return {"files": st.get("files", []), "from": resolve_commit(from_hash), "to": "WIP"}
    a, b = resolve_commit(from_hash), resolve_commit(to_hash)
    out = git("diff", "--name-status", "-M", a, b)
    files = []
    for l in out.splitlines():
        if not l:
            continue
        parts = l.split("\t")
        status, path = parts[0], parts[-1]
        files.append({"status": status[0], "path": path})
    return {"files": files, "from": a, "to": b}


def merge_base(from_hash, to_hash):
    a, b = resolve_commit(from_hash), resolve_commit(to_hash)
    try:
        base = git("merge-base", a, b).strip()
    except subprocess.CalledProcessError:
        raise UserError("no_merge_base")
    return {"hash": base, "from": a, "to": b}


def diff(path, old="", hash_="", from_hash="", to_hash="", wip=""):
    """1ファイルの差分テキスト。"""
    if "\0" in path:
        raise ValueError("bad path")
    specs = [path]
    if old:
        specs = [old, path]
    if wip == "compare":
        # from と作業ツリー
        from_hash = resolve_commit(from_hash)
        untracked = set()
        try:
            raw = git("status", "--porcelain=v1", "-z", "--untracked-files=all", "--", path, literal=True)
            for t in raw.split("\0"):
                if t.startswith("?? "):
                    untracked.add(t[3:])
        except subprocess.CalledProcessError:
            pass
        tracked_in_base = bool(git(
            "ls-tree", "-r", "--name-only", "-z", from_hash, "--", path, literal=True
        ).strip("\0"))
        if path in untracked and not tracked_in_base:
            out = wip_diff("untracked", path, specs)
        else:
            out = git_bytes("diff", "--no-color", "-M", from_hash, "--", *specs, literal=True)
    elif wip:  # 作業ツリーの変更
        out = wip_diff(wip, path, specs)
    elif from_hash or to_hash:
        if not (HASH_RE.fullmatch(from_hash) and HASH_RE.fullmatch(to_hash)):
            raise ValueError("bad hash")
        out = git_bytes("diff", "--no-color", "-M", from_hash, to_hash, "--", *specs, literal=True)
    else:
        if not HASH_RE.fullmatch(hash_):
            raise ValueError("bad hash")
        out = git_bytes("show", "--format=", "--first-parent", "-M", "--no-color", hash_, "--", *specs, literal=True)
    truncated = len(out) > DIFF_LIMIT
    text, encoding = decode_text(out[:DIFF_LIMIT], truncated=truncated)
    if text is None:
        # バイナリ差分でも git の "Binary files ... differ" 等は表示できるようにする。
        text, encoding = out[:DIFF_LIMIT].decode("utf-8", errors="replace"), "UTF-8 (binary diff)"
    return {"diff": text, "encoding": encoding, "truncated": truncated}


__all__ = [
    "DIFF_LIMIT", "REV_RE", "branches", "branches_containing", "commit", "compare", "diff",
    "log", "merge_base", "paging", "reflog", "rev_args", "stash_list", "tags",
]
