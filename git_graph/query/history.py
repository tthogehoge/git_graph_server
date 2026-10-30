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
