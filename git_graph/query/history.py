# ログ・ブランチ・タグ・reflog・stash 一覧・コミット詳細・比較・差分の読み取り。
import re
import subprocess
from .. import app_state as state
from ..files import decode_text
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
    # 各行の末尾に親コミット (--parents で path 指定時は書き換え後の親) を付ける。
    # graph=True のときはマージグラフを描けるよう、git log --graph と同じく --topo-order で並べる。
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
        # 範囲指定 (A..B) では範囲外の親へ線を伸ばさない (ログに現れず、線が最後まで残るため)
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
    """stash の一覧 (新しい順)。log と同じ [hash, short, 選択子, 日時, 件名] の形で返す。"""
    try:
        out = git("stash", "list", "--format=%H%x1f%h%x1f%gd%x1f%ci%x1f%gs")
    except subprocess.CalledProcessError:
        out = ""
    commits = []
    for l in out.splitlines():
        h, short, sel, date, subj = (l.split("\x1f") + [""] * 5)[:5]
        commits.append([h, short, sel, date[:16], subj])
    return {"commits": commits, "has_more": False, "total": len(commits)}


def _ref_list(refs, fmt):
    """for-each-ref の結果を行ごとのリストにする。"""
    out = git("for-each-ref", "--format=" + fmt, *refs)
    rows = []
    for l in out.splitlines():
        parts = (l.split("\x1f") + [""] * 6)[:6]
        rows.append(parts)
    return rows


def branches():
    """ローカルブランチ → リモート追跡ブランチの順の一覧。現在のブランチには ★ を付ける。"""
    fmt = "%(refname)%1f%(objectname)%1f%(HEAD)%1f%(committerdate:short)%1f%(subject)"
    commits = []
    for ref, name_, head, date, subj in (r[:5] for r in _ref_list(["refs/heads"], fmt)):
        h = git("rev-parse", ref + "^{commit}").strip()
        commits.append([h, h[:7], ("★ " if head == "*" else "") + ref[len("refs/heads/"):], date, subj])
    if not state.OPTS.no_remotes:
        for ref, name_, head, date, subj in (r[:5] for r in _ref_list(["refs/remotes"], fmt)):
            if ref.endswith("/HEAD"):
                continue
            h = git("rev-parse", ref + "^{commit}").strip()
            commits.append([h, h[:7], ref[len("refs/remotes/"):], date, subj])
    return {"commits": commits, "has_more": False, "total": len(commits)}


def branches_containing(hash_):
    """指定コミットを含むローカル / リモート追跡ブランチと各 tip を返す。"""
    h = resolve_commit(hash_)
    refs = ["refs/heads"]
    if not state.OPTS.no_remotes:
        refs.append("refs/remotes")
    out = git("for-each-ref", "--contains=" + h, "--format=%(refname)%1f%(objectname)", *refs)
    branches_out = []
    for line in out.splitlines():
        ref, tip = line.split("\x1f", 1)
        if ref.endswith("/HEAD"):
            continue
        name = ref.removeprefix("refs/heads/").removeprefix("refs/remotes/")
        branches_out.append({"ref": ref, "name": name, "tip": tip})
    branches_out.sort(key=lambda b: (0 if b["ref"].startswith("refs/heads/") else 1, b["name"].casefold(), b["name"]))
    return {"branches": branches_out}


def tags():
    """タグの一覧 (新しい順)。注釈付きタグは指しているコミットを使う。"""
    fmt = "%(refname)%1f%(creatordate:short)%1f%(*subject)%1f%(subject)"
    out = git("for-each-ref", "--sort=-creatordate", "--format=" + fmt, "refs/tags")
    commits = []
    for l in out.splitlines():
        ref, date, psubj, subj = (l.split("\x1f") + [""] * 4)[:4]
        try:
            h = git("rev-parse", "--verify", "--quiet", ref + "^{commit}").strip()
        except subprocess.CalledProcessError:  # コミットを指さないタグ (ツリーなど) は出さない
            continue
        commits.append([h, h[:7], ref[len("refs/tags/"):], date, psubj or subj])
    return {"commits": commits, "has_more": False, "total": len(commits)}


def reflog(branch="", skip=0, limit=500):
    """reflog (新しい順)。branch 省略で HEAD、指定でそのローカルブランチ。"""
    if branch:
        check_ref_name("branch", branch)
        ref, label = "refs/heads/" + branch, branch
        if subprocess.run(["git", "-C", state.REPO, "show-ref", "--verify", "--quiet", ref]).returncode != 0:
            raise UserError("branch_not_found")
    else:
        ref, label = "HEAD", "HEAD"
    skip, limit = paging(skip, limit)
    try:  # --date=iso にすると %gd が "名前@{日時}" になる (リフログの時刻を出したい)。番号は並び順から振る
        out = git("reflog", "show", "--date=iso", f"--skip={skip}", "-n", str(limit),
                  "--format=%H%x1f%h%x1f%gd%x1f%gs", ref, "--")
    except subprocess.CalledProcessError:  # コミットがまだない / reflog がない
        out = ""
    commits = []
    for i, l in enumerate(x for x in out.splitlines() if x):
        h, short, gd, subj = (l.split("\x1f") + [""] * 4)[:4]
        when = gd[gd.find("@{") + 2:-1][:16] if "@{" in gd else ""
        commits.append([h, short, f"{label}@{{{skip + i}}}", when, subj])
    res = {"commits": commits, "has_more": len(commits) == limit}
    if skip == 0:
        res["total"] = len(commits) if not res["has_more"] else sum(
            1 for _ in git("reflog", "show", "--format=%H", ref, "--").splitlines())
    return res


def commit(hash_):
    info = commit_info(hash_)
    raw = git("show", "--numstat", "-z", "-M", "--first-parent", "--format=", info["hash"], "--")
    info["files"] = parse_numstat(raw)
    return info


def compare(from_hash, to_hash):
    """2つのコミット間、またはコミットと WIP の差分ファイル一覧。"""
    a = commit_info(from_hash)
    if to_hash == "WIP":
        if not state.WIP_ENABLED or not state.HAS_WORKTREE:
            raise UserError("wip_disabled")
        st = worktree_status()
        files = parse_numstat(git("diff", "--numstat", "-z", "-M", a["hash"], "--"))
        included = {f["path"] for f in files}
        base_paths = set(git("ls-tree", "-r", "--name-only", "-z", a["hash"]).split("\0"))
        for f in st["untracked"]:
            path = f["path"]
            if path in included or path in base_paths:
                continue
            result = subprocess.run(
                ["git", "-C", state.REPO, "diff", "--no-index", "--numstat", "-z",
                 "--", "/dev/null", path],
                capture_output=True,
            )
            if result.returncode not in (0, 1):
                raise ValueError(result.stderr.decode("utf-8", errors="replace").strip() or tr("git_diff_failed"))
            for added in parse_numstat(result.stdout.decode("utf-8", errors="replace")):
                added["wip"] = "compare"
                files.append(added)
        return {"from": a, "to": {"hash": "WIP"}, "files": files}
    b = commit_info(to_hash)
    raw = git("diff", "--numstat", "-z", "-M", a["hash"], b["hash"], "--")
    return {"from": a, "to": b, "files": parse_numstat(raw)}


def merge_base(from_hash, to_hash):
    """2つのコミットの共通祖先 (最も新しいもの)。"""
    a = resolve_commit(from_hash)
    b = resolve_commit(to_hash)
    try:
        base = git("merge-base", a, b).strip()
    except subprocess.CalledProcessError:
        base = ""
    return {"hash": base}


def diff(path, old="", hash_="", from_hash="", to_hash="", wip=""):
    """1ファイルの差分。hash、from/to のコミット間、または from/WIP の比較を指定する。"""
    if not path or "\0" in path or "\0" in old:
        raise ValueError("bad path")
    specs = [old, path] if old else [path]
    if wip == "compare":
        if not HASH_RE.fullmatch(from_hash):
            raise ValueError("bad hash")
        if not state.WIP_ENABLED or not state.HAS_WORKTREE:
            raise UserError("wip_disabled")
        untracked = {f["path"] for f in worktree_status()["untracked"]}
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
