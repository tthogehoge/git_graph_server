# ログ・ブランチ・タグ・reflog・stash 一覧・コミット詳細・比較・差分のデータ API。
import re
import subprocess
from . import app_state as state
from .files import decode_text
from .gitrun import check_ref_name, git, git_bytes, HASH_RE, resolve_commit
from .gitstate import commit_info, parse_numstat
from .i18n import UserError
from .routing import route
from .wip import wip_diff


REV_RE = re.compile(r"[0-9a-f]{40}(\.\.[0-9a-f]{40})?")


@route("/api/log")
def api_log(q):
    rev = q.get("rev", [""])[0]
    if not REV_RE.fullmatch(rev):
        raise ValueError("bad rev")
    path = q.get("path", [""])[0]
    if "\0" in path:
        raise ValueError("bad path")
    skip, limit = paging(q)
    args = ["log", "--date=short", f"--skip={skip}", "-n", str(limit),
            "--format=%H%x1f%h%x1f%an%x1f%ad%x1f%s", rev, "--"]
    if path:
        args.append(path)
    out = git(*args, literal=bool(path))
    commits = [l.split("\x1f") for l in out.splitlines() if l]
    res = {"commits": commits, "has_more": len(commits) == limit}
    if skip == 0:
        count_args = ["rev-list", "--count", rev, "--"]
        if path:
            count_args.append(path)
        res["total"] = int(git(*count_args, literal=bool(path)).strip())
    return res


def paging(q):
    skip = int(q.get("skip", ["0"])[0])
    limit = min(int(q.get("limit", ["500"])[0]), 2000)
    if skip < 0 or limit < 1:
        raise ValueError("bad paging")
    return skip, limit


@route("/api/stash-list")
def api_stash_list(q):
    """stash の一覧 (新しい順)。api_log と同じ [hash, short, 選択子, 日時, 件名] の形で返す。"""
    try:
        out = git("stash", "list", "--format=%H%x1f%h%x1f%gd%x1f%ci%x1f%gs")
    except subprocess.CalledProcessError:
        out = ""
    commits = []
    for l in out.splitlines():
        h, short, sel, date, subj = (l.split("\x1f") + [""] * 5)[:5]
        commits.append([h, short, sel, date[:16], subj])
    return {"commits": commits, "has_more": False, "total": len(commits)}


def _ref_list(refs, fmt, kinds):
    """for-each-ref の結果を api_log と同じ [hash, short, 名前, 日付, 件名] の形にする。"""
    out = git("for-each-ref", "--format=" + fmt, *refs)
    rows = []
    for l in out.splitlines():
        parts = (l.split("\x1f") + [""] * 6)[:6]
        rows.append(parts)
    return rows


@route("/api/branches")
def api_branches(q):
    """ローカルブランチ → リモート追跡ブランチの順の一覧。現在のブランチには ★ を付ける。"""
    fmt = "%(refname)%1f%(objectname)%1f%(HEAD)%1f%(committerdate:short)%1f%(subject)"
    commits = []
    for ref, name_, head, date, subj in (r[:5] for r in _ref_list(["refs/heads"], fmt, None)):
        h = git("rev-parse", ref + "^{commit}").strip()
        commits.append([h, h[:7], ("★ " if head == "*" else "") + ref[len("refs/heads/"):], date, subj])
    if not state.OPTS.no_remotes:
        for ref, name_, head, date, subj in (r[:5] for r in _ref_list(["refs/remotes"], fmt, None)):
            if ref.endswith("/HEAD"):
                continue
            h = git("rev-parse", ref + "^{commit}").strip()
            commits.append([h, h[:7], ref[len("refs/remotes/"):], date, subj])
    return {"commits": commits, "has_more": False, "total": len(commits)}


@route("/api/branches-containing")
def api_branches_containing(q):
    """指定コミットを含むローカル / リモート追跡ブランチと各 tip を返す。"""
    h = resolve_commit(q.get("hash", [""])[0])
    refs = ["refs/heads"]
    if not state.OPTS.no_remotes:
        refs.append("refs/remotes")
    out = git("for-each-ref", "--contains=" + h, "--format=%(refname)%1f%(objectname)", *refs)
    branches = []
    for line in out.splitlines():
        ref, tip = line.split("\x1f", 1)
        if ref.endswith("/HEAD"):
            continue
        name = ref.removeprefix("refs/heads/").removeprefix("refs/remotes/")
        branches.append({"ref": ref, "name": name, "tip": tip})
    branches.sort(key=lambda b: (0 if b["ref"].startswith("refs/heads/") else 1, b["name"].casefold(), b["name"]))
    return {"branches": branches}


@route("/api/tags")
def api_tags(q):
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


@route("/api/reflog")
def api_reflog(q):
    """reflog (新しい順)。branch 省略で HEAD、指定でそのローカルブランチ。"""
    branch = q.get("branch", [""])[0]
    if branch:
        check_ref_name("branch", branch)
        ref, label = "refs/heads/" + branch, branch
        if subprocess.run(["git", "-C", state.REPO, "show-ref", "--verify", "--quiet", ref]).returncode != 0:
            raise UserError("branch_not_found")
    else:
        ref, label = "HEAD", "HEAD"
    skip, limit = paging(q)
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


@route("/api/commit")
def api_commit(q):
    info = commit_info(q.get("hash", [""])[0])
    raw = git("show", "--numstat", "-z", "-M", "--first-parent", "--format=", info["hash"], "--")
    info["files"] = parse_numstat(raw)
    return info


@route("/api/compare")
def api_compare(q):
    """2つのコミット間の差分 (from → to) のファイル一覧。"""
    a = commit_info(q.get("from", [""])[0])
    b = commit_info(q.get("to", [""])[0])
    raw = git("diff", "--numstat", "-z", "-M", a["hash"], b["hash"], "--")
    return {"from": a, "to": b, "files": parse_numstat(raw)}


@route("/api/merge-base")
def api_merge_base(q):
    """2つのコミットの共通祖先 (最も新しいもの)。"""
    a = resolve_commit(q.get("from", [""])[0])
    b = resolve_commit(q.get("to", [""])[0])
    try:
        base = git("merge-base", a, b).strip()
    except subprocess.CalledProcessError:
        base = ""
    return {"hash": base}


DIFF_LIMIT = 400000


def api_diff(q):
    """1ファイルの差分。hash=... (1コミット) か from=...&to=... (2コミット間) を指定する。"""
    path = q.get("path", [""])[0]
    old = q.get("old", [""])[0]
    if not path or "\0" in path or "\0" in old:
        raise ValueError("bad path")
    specs = [old, path] if old else [path]
    wip = q.get("wip", [""])[0]
    if wip:  # 作業ツリーの変更
        out = wip_diff(wip, path, specs)
    elif "from" in q or "to" in q:
        a, b = q.get("from", [""])[0], q.get("to", [""])[0]
        if not (HASH_RE.fullmatch(a) and HASH_RE.fullmatch(b)):
            raise ValueError("bad hash")
        out = git_bytes("diff", "--no-color", "-M", a, b, "--", *specs, literal=True)
    else:
        h = q.get("hash", [""])[0]
        if not HASH_RE.fullmatch(h):
            raise ValueError("bad hash")
        out = git_bytes("show", "--format=", "--first-parent", "-M", "--no-color", h, "--", *specs, literal=True)
    truncated = len(out) > DIFF_LIMIT
    diff, encoding = decode_text(out[:DIFF_LIMIT], truncated=truncated)
    if diff is None:
        # バイナリ差分でも git の "Binary files ... differ" 等は表示できるようにする。
        diff, encoding = out[:DIFF_LIMIT].decode("utf-8", errors="replace"), "UTF-8 (binary diff)"
    return {"diff": diff, "encoding": encoding, "truncated": truncated}


__all__ = [
    "DIFF_LIMIT", "api_branches", "api_branches_containing", "api_commit", "api_compare",
    "api_diff", "api_log", "api_merge_base", "api_reflog", "api_stash_list", "api_tags", "paging",
]
