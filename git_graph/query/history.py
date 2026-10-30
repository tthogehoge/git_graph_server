# ログ・ブランチ・タグ・reflog・stash 一覧・コミット詳細・比較・差分の読み取り。
import re
import subprocess
from .. import app_state as state
from ..textcodec import decode_text
from .. import libgit
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
    if libgit.available():
        from datetime import datetime, timezone, timedelta
        commits = []
        for h, base, name, subject in libgit.list_stashes():
            try:
                c = libgit.repo()[h]
                off = getattr(c, "commit_time_offset", 0) or 0
                date = datetime.fromtimestamp(c.commit_time, timezone(timedelta(minutes=off))).strftime("%Y-%m-%d %H:%M")
            except Exception:
                date = ""
            commits.append([h, h[:7], name, date, subject])
        return {"commits": commits, "has_more": False, "total": len(commits)}
    try:
        out = git("stash", "list", "--format=%H%x1f%h%x1f%gd%x1f%ci%x1f%gs")
    except subprocess.CalledProcessError:
        out = ""
    commits = []
    for l in out.splitlines():
        h, short, sel, date, subj = (l.split("") + ["", "", "", "", ""])[:5]
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
    if libgit.available():  # ブランチごとに rev-parse を起動しない
        from datetime import datetime, timezone, timedelta
        local, remote = [], []
        for ref, target, peeled, is_head in libgit.list_ref_infos():
            if ref.startswith("refs/heads/"):
                bucket, name = local, ref[len("refs/heads/"):]
            elif ref.startswith("refs/remotes/") and not ref.endswith("/HEAD") and not state.OPTS.no_remotes:
                bucket, name = remote, ref[len("refs/remotes/"):]
            else:
                continue
            c = libgit.peel_commit(libgit.repo()[peeled or target])
            if c is None:
                continue
            off = getattr(c, "commit_time_offset", 0) or 0
            date = datetime.fromtimestamp(c.commit_time, timezone(timedelta(minutes=off))).date().isoformat()
            h = str(c.id)
            bucket.append((ref, [h, h[:7], ("★ " if is_head and bucket is local else "") + name,
                                 date, (c.message or "").split("\n", 1)[0]]))
        commits = [row for _, row in sorted(local)] + [row for _, row in sorted(remote)]
        return {"commits": commits, "has_more": False, "total": len(commits)}
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
    if libgit.available():
        from datetime import datetime, timezone, timedelta
        rows = []
        for ref, target, peeled, is_head in libgit.list_ref_infos():
            if not ref.startswith("refs/tags/"):
                continue
            h = peeled or target
            try:
                c = libgit.repo()[h]
                if not hasattr(c, "commit_time"):
                    c = libgit.peel_commit(c)
                if c is None or not hasattr(c, "commit_time"):
                    continue
                off = getattr(c, "commit_time_offset", 0) or 0
                date = datetime.fromtimestamp(c.commit_time, timezone(timedelta(minutes=off))).date().isoformat()
                subj = (c.message or "").split("\n", 1)[0]
            except Exception:
                continue
            rows.append((c.commit_time, [h, h[:7], ref[len("refs/tags/"):], date, subj]))
        rows.sort(key=lambda x: -x[0])
        commits = [r[1] for r in rows]
        return {"commits": commits, "has_more": False, "total": len(commits)}
    fmt = "%(refname)%1f%(creatordate:short)%1f%(*subject)%1f%(subject)"
    out = git("for-each-ref", "--sort=-creatordate", "--format=" + fmt, "refs/tags")
    commits = []
    for l in out.splitlines():
        ref, date, psubj, subj = (l.split("\x1f") + [""] * 4)[:4]
        try:
            h = git("rev-parse", "--verify", "--quiet", ref + "^{commit}").strip()
        except subprocess.CalledProcessError:
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
    if libgit.available():
        parents = libgit.commit_parents(info["hash"])
        base = parents[0] if parents else libgit.EMPTY_TREE
        letters, files = libgit.diff_name_numstat_trees(base, info["hash"])
        info["files"] = files
    else:
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
        if libgit.available():
            files = libgit.diff_name_numstat_trees(a["hash"], None)[1]
            base_paths = libgit.tree_paths(a["hash"])
        else:
            files = parse_numstat(git("diff", "--numstat", "-z", "-M", a["hash"], "--"))
            base_paths = set(git("ls-tree", "-r", "--name-only", "-z", a["hash"]).split("\0"))
        included = {f["path"] for f in files}
        for f in st["untracked"]:
            path = f["path"]
            if path in included or path in base_paths:
                continue
            # untracked: count lines as added
            full = __import__("os").path.join(state.REPO, path)
            try:
                with open(full, "rb") as fh:
                    n = sum(1 for _ in fh)
            except OSError:
                n = 0
            files.append({"path": path, "old_path": None, "added": n, "deleted": 0, "wip": "compare"})
        return {"from": a, "to": {"hash": "WIP"}, "files": files}
    b = commit_info(to_hash)
    if libgit.available():
        files = libgit.diff_name_numstat_trees(a["hash"], b["hash"])[1]
    else:
        raw = git("diff", "--numstat", "-z", "-M", a["hash"], b["hash"], "--")
        files = parse_numstat(raw)
    return {"from": a, "to": b, "files": files}



def merge_base(from_hash, to_hash):
    """2つのコミットの共通祖先 (最も新しいもの)。"""
    a = resolve_commit(from_hash)
    b = resolve_commit(to_hash)
    if libgit.available():
        base = libgit.merge_base(a, b) or ""
    else:
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
        if libgit.available():
            tracked_in_base = libgit.path_in_tree(from_hash, path)
        else:
            tracked_in_base = bool(git(
                "ls-tree", "-r", "--name-only", "-z", from_hash, "--", path, literal=True
            ).strip("\0"))
        if path in untracked and not tracked_in_base:
            out = wip_diff("untracked", path, specs)
        elif libgit.available():
            out = libgit.unified_diff(from_hash, paths=[path] if not old else specs)
        else:
            out = git_bytes("diff", "--no-color", "-M", from_hash, "--", *specs, literal=True)
    elif wip:  # 作業ツリーの変更
        out = wip_diff(wip, path, specs)
    elif from_hash or to_hash:
        if not (HASH_RE.fullmatch(from_hash) and HASH_RE.fullmatch(to_hash)):
            raise ValueError("bad hash")
        if libgit.available():
            out = libgit.unified_diff(from_hash, to_hash, paths=specs)
        else:
            out = git_bytes("diff", "--no-color", "-M", from_hash, to_hash, "--", *specs, literal=True)
    else:
        if not HASH_RE.fullmatch(hash_):
            raise ValueError("bad hash")
        if libgit.available():
            parents = libgit.commit_parents(hash_)
            if parents:
                out = libgit.unified_diff(parents[0], hash_, paths=specs)
            else:
                out = libgit.unified_diff(libgit.EMPTY_TREE, hash_, paths=specs)
        else:
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
