# ログ・ブランチ・タグ・reflog・stash 一覧・コミット詳細・比較・差分の読み取り。
import hashlib
import re
import subprocess
import threading
from collections import OrderedDict
from .. import app_state as state
from ..textcodec import decode_text
from .. import libgit
from ..gitrun import check_ref_name, git, git_bytes, HASH_RE, resolve_commit
from ..gitstate import commit_info, parse_numstat
from ..i18n import tr, UserError
from .status import status as worktree_status, wip_diff


# コミット / A..B / A..B^@ (B の親たち。A..B から B 自身を除いた範囲 = グラフの線の間のコミット)
REV_RE = re.compile(r"[0-9a-f]{40}(\.\.[0-9a-f]{40}(\^@)?)?|all")  # all: 全ブランチ・タグ・(リモート追跡ブランチ)・HEAD の全コミット

DIFF_LIMIT = 400000

# ログの検索 (/api/log の hash / grep / author / committer / since / until / all)
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
HASH_PREFIX_RE = re.compile(r"[0-9a-f]{4,40}")
HASH_CANDIDATES_MAX = 100  # ハッシュの前方一致で調べるコミットの上限


def rev_args(rev):
    """rev を git に渡す引数に直す。A..B^@ は「A から見えない B の親たち」(B 自身は含めない)。"""
    if rev == "all":
        return _all_refs_args()
    if not rev.endswith("^@"):
        return [rev]
    a, b = rev[:-2].split("..")
    parents = git("rev-list", "--parents", "-n", "1", b, "--").split()[1:]
    return [f"^{a}"] + parents


def _all_refs_args():
    """全コミット (stash は含めない)。"""
    return ["--branches", "--tags"] + ([] if getattr(state.OPTS, "no_remotes", False) else ["--remotes"]) + ["HEAD"]


def _refs_fingerprint():
    """全ブランチ・タグの先端が変わったら変わる値 (rev=all の一覧を覚えておくときの鍵)。"""
    refs = ["refs/heads", "refs/tags"] + ([] if getattr(state.OPTS, "no_remotes", False) else ["refs/remotes"])
    out = git("for-each-ref", "--format=%(objectname) %(refname)", *refs)
    try:
        head = git("rev-parse", "--verify", "--quiet", "HEAD^{commit}").strip()
    except subprocess.CalledProcessError:
        head = ""
    return hashlib.sha1((out + "\n" + head).encode("utf-8")).hexdigest()


def paging(skip, limit):
    skip = int(skip)
    limit = min(int(limit), 2000)
    if skip < 0 or limit < 1:
        raise ValueError("bad paging")
    return skip, limit


def _scope_revs(rev):
    """ログの範囲 rev を (含める側, 除く側) の rev のリストにする。"""
    args = rev_args(rev)
    if len(args) == 1:
        if ".." in args[0]:
            a, b = args[0].split("..")
            return [b], [a]
        return [args[0]], []
    return [a for a in args if not a.startswith("^")], [a[1:] for a in args if a.startswith("^")]


def _is_ancestor(h, rev):
    return subprocess.run(["git", "-C", state.REPO, "merge-base", "--is-ancestor", h, rev],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def _hash_candidates(prefix, rev, everywhere):
    """前方一致するコミット。everywhere=False のときは、ログの範囲 rev にあるものだけ。"""
    full = git("rev-parse", "--disambiguate=" + prefix).split()[:HASH_CANDIDATES_MAX]
    if not full:
        return []
    kinds = git("cat-file", "--batch-check=%(objectname) %(objecttype)", input="\n".join(full) + "\n")
    commits = [l.split()[0] for l in kinds.splitlines() if l.endswith(" commit")]
    if everywhere:
        return commits
    pos, neg = _scope_revs(rev)
    return [h for h in commits if any(_is_ancestor(h, p) for p in pos) and not any(_is_ancestor(h, n) for n in neg)]


def search_plan(rev, search):
    """検索条件 (hash / grep / author / committer / since / until と、全コミット対象の all) を、
    git log に渡す (絞り込みオプション, 走査の対象, --no-walk にするか) にする。条件が1つも無ければ None。
    走査の対象が空なら、ハッシュに一致するコミットが無い (結果なし)。"""
    search = search or {}
    terms = {k: (search.get(k) or "").strip() for k in ("hash", "grep", "author", "committer", "since", "until")}
    if not any(terms.values()):
        return None
    if any("\0" in v for v in terms.values()):
        raise ValueError("bad search")
    opts = []
    words = terms["grep"].split()
    for w in words:  # 件名・本文: 空白で区切った語を、すべて含むもの (AND)
        opts.append("--grep=" + w)
    if words:
        opts.append("--all-match")
    for key in ("author", "committer"):  # 名前・メールアドレスの一部
        if terms[key]:
            opts.append(f"--{key}={terms[key]}")
    if words or terms["author"] or terms["committer"]:
        opts += ["--fixed-strings", "--regexp-ignore-case"]  # 正規表現ではなく、大文字小文字を区別しない部分一致
    for key, time in (("since", "00:00:00"), ("until", "23:59:59")):
        if terms[key]:
            if not DATE_RE.fullmatch(terms[key]):
                raise UserError("search_bad_date")
            opts.append(f"--{key}={terms[key]} {time}")
    everywhere = bool(search.get("all")) or rev == "all"
    if terms["hash"]:
        prefix = terms["hash"].lower()
        if not HASH_PREFIX_RE.fullmatch(prefix):
            raise UserError("search_bad_hash")
        return opts, _hash_candidates(prefix, rev, everywhere), True
    if everywhere:
        revs = ["--branches", "--tags"] + ([] if getattr(state.OPTS, "no_remotes", False) else ["--remotes"]) + ["HEAD"]
    else:
        revs = rev_args(rev)
    return opts, revs, False


class _TopoList:
    """rev のログ全体を --topo-order で並べたハッシュの一覧 (と、その集合)。"""
    def __init__(self, hashes):
        self.hashes = hashes
        self._set = None

    def hash_set(self):
        if self._set is None:
            self._set = set(self.hashes)
        return self._set


# rev はコミットのハッシュ (A..B もハッシュ同士) なので、ログの中身はずっと変わらない。
# 件数が多いと rev-list --topo-order だけで数秒かかり、--skip を付けた git log も毎回同じ歩きをやり直すので、
# 最近使った数件の一覧を覚えておき、続きの読み込みや、対象のコミットへ飛ぶ位置の計算に使い回す。
# 先頭ページ (skip=0) は全件一覧を待たず git log -n で先に返し、裏で一覧を温める。
_TOPO_CACHE = OrderedDict()
_TOPO_CACHE_LOCK = threading.Lock()
TOPO_CACHE_MAX = 3  # 覚えておく一覧の数 (件数が多いとメモリを使うので少なめ)


_TOPO_INFLIGHT = {}  # 計算中の rev -> ロック (同じ一覧を同時に二重に計算しない)


def _topo_key(rev):
    # rev=all は、ブランチやタグが動くと中身が変わるので、先端の一覧を鍵に含める
    return (state.REPO, rev, _refs_fingerprint() if rev == "all" else "")


def topo_list(rev):
    key = _topo_key(rev)
    with _TOPO_CACHE_LOCK:
        hit = _TOPO_CACHE.get(key)
        if hit is not None:
            _TOPO_CACHE.move_to_end(key)
            return hit
        lock = _TOPO_INFLIGHT.setdefault(key, threading.Lock())
    with lock:  # 先に計算している人がいれば、その完了を待って結果を使う
        with _TOPO_CACHE_LOCK:
            hit = _TOPO_CACHE.get(key)
        if hit is None:
            try:
                hit = _TopoList(git("rev-list", "--topo-order", *rev_args(rev), "--").split())
                with _TOPO_CACHE_LOCK:
                    _TOPO_CACHE[key] = hit
                    while len(_TOPO_CACHE) > TOPO_CACHE_MAX:
                        _TOPO_CACHE.popitem(last=False)
            finally:
                with _TOPO_CACHE_LOCK:
                    _TOPO_INFLIGHT.pop(key, None)
        else:
            with _TOPO_CACHE_LOCK:
                _TOPO_INFLIGHT.pop(key, None)
    return hit


def topo_total_status(rev):
    """バックグラウンドの topo 一覧が完成しているかと、完成済みならその件数を返す。"""
    if not REV_RE.fullmatch(rev):
        raise ValueError("bad rev")
    key = _topo_key(rev)
    with _TOPO_CACHE_LOCK:
        hit = _TOPO_CACHE.get(key)
        if hit is not None:
            return {"total": len(hit.hashes), "pending": False}
        return {"total": None, "pending": key in _TOPO_INFLIGHT}


def _schedule_topo_list(rev):
    """続き読み・位置ジャンプ用に、裏で topo 一覧を温める。既にキャッシュ or 計算中なら何もしない。"""
    key = _topo_key(rev)
    with _TOPO_CACHE_LOCK:
        if key in _TOPO_CACHE or key in _TOPO_INFLIGHT:
            return
        _TOPO_INFLIGHT[key] = threading.Lock()

    def run():
        try:
            topo_list(rev)
        except Exception:
            pass

    threading.Thread(target=run, daemon=True).start()


def log_position(rev, h):
    """ログ (graph=1 と同じ --topo-order の並び) の中で、コミット h が先頭から何番目か。無ければ index=-1。
    対象のコミットへ飛ぶとき、そこまでのログを読まずに、その付近から読み始めるために使う。"""
    if not REV_RE.fullmatch(rev) or not re.fullmatch(r"[0-9a-f]{40,64}", h):
        raise ValueError("bad rev")
    hashes = topo_list(rev).hashes
    try:
        index = hashes.index(h)
    except ValueError:
        index = -1
    return {"index": index, "total": len(hashes)}


def _parse_log_lines(out):
    commits = []
    for l in out.splitlines():
        if not l:
            continue
        h, short, author, date, parents, subj = (l.split("\x1f", 5) + [""] * 6)[:6]
        commits.append([h, short, author, date, subj, parents.split()])
    return commits


def _filter_range_parents(rev, commits, listed):
    """範囲指定 (A..B) では範囲外の親へ線を伸ばさない (ログに現れず、線が最後まで残るため)。"""
    if ".." not in rev or not commits:
        return
    for c in commits:
        c[5] = [p for p in c[5] if p in listed]


def _log_first_page(rev, limit):
    """先頭ページは全件 topo を待たず git log -n --topo-order で返す。続き用の一覧は応答後に温める。

    履歴が limit 未満なら total もここで確定する。超える場合は total を付けず、
    裏でできた topo キャッシュを 2 ページ目以降 / log-position が使う。
    """
    args = ["log", "--parents", "--date=short", "-n", str(limit + 1), "--topo-order",
            "--format=%H%x1f%h%x1f%an%x1f%ad%x1f%P%x1f%s"]
    args += rev_args(rev) + ["--"]
    commits = _parse_log_lines(git(*args))
    has_more = len(commits) > limit
    commits = commits[:limit]
    if ".." in rev and commits:
        # キャッシュがあれば集合を再利用。なければ (並べ替えなしの) rev-list で範囲内ハッシュを取る
        key = _topo_key(rev)
        with _TOPO_CACHE_LOCK:
            hit = _TOPO_CACHE.get(key)
        if hit is not None:
            listed = hit.hash_set()
        else:
            listed = set(git("rev-list", *rev_args(rev), "--").split())
        _filter_range_parents(rev, commits, listed)
    res = {"commits": commits, "has_more": has_more}
    if not has_more:
        res["total"] = len(commits)
    else:
        _schedule_topo_list(rev)
    return res


def _log_from_topo_list(rev, skip, limit):
    """マージグラフ付きのログ (--topo-order)。並びは覚えておいた一覧から取り、該当の行だけを git log で引く。"""
    topo = topo_list(rev)
    window = topo.hashes[skip:skip + limit]
    commits = []
    if window:
        out = git("log", "--no-walk=unsorted", "--stdin", "--parents", "--date=short",
                  "--format=%H%x1f%h%x1f%an%x1f%ad%x1f%P%x1f%s", input="\n".join(window) + "\n")
        commits = _parse_log_lines(out)
        _filter_range_parents(rev, commits, topo.hash_set())
    return {
        "commits": commits,
        "has_more": skip + len(window) < len(topo.hashes),
        "total": len(topo.hashes),
    }


def log(rev, path="", skip=0, limit=500, graph=False, search=None):
    if not REV_RE.fullmatch(rev):
        raise ValueError("bad rev")
    if "\0" in path:
        raise ValueError("bad path")
    skip, limit = paging(skip, limit)
    plan = search_plan(rev, search)
    if graph and plan is None and not path:
        # 先頭ページかつ未キャッシュなら全件 rev-list を待たない。キャッシュ済み / 続きは一覧経由。
        if skip == 0:
            key = _topo_key(rev)
            with _TOPO_CACHE_LOCK:
                cached = key in _TOPO_CACHE
            if not cached:
                return _log_first_page(rev, limit)
        return _log_from_topo_list(rev, skip, limit)
    if plan is not None:
        graph = False  # 検索結果は途中のコミットが抜けるので、マージグラフは付けない
        if not plan[1]:  # ハッシュに一致するコミットが無い
            return {"commits": [], "has_more": False, "total": 0}
        walk = (["--no-walk=sorted"] if plan[2] else []) + plan[0] + plan[1]
    else:
        walk = rev_args(rev)
    # 各行の末尾に親コミット (--parents で path 指定時は書き換え後の親) を付ける。
    # graph=True のときはマージグラフを描けるよう、git log --graph と同じく --topo-order で並べる。
    args = ["log", "--parents", "--date=short", f"--skip={skip}", "-n", str(limit),
            "--format=%H%x1f%h%x1f%an%x1f%ad%x1f%P%x1f%s"]
    if graph:
        args.append("--topo-order")
    args += walk + ["--"]
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
        _filter_range_parents(rev, commits, set(git("rev-list", *rev_args(rev), "--").split()))
    res = {"commits": commits, "has_more": len(commits) == limit}
    if skip == 0:
        count_args = ["rev-list", "--count", *walk, "--"]
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
        prefixes = ("refs/heads/",) if state.OPTS.no_remotes else ("refs/heads/", "refs/remotes/")
        for ref, target, peeled, is_head in libgit.list_ref_infos(prefixes):  # タグなど他の ref は peel しない
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
    fmt = "%(refname)%1f%(objectname)%1f%(HEAD)%1f%(committerdate:short)%1f%(subject)%1f%(objecttype)"
    commits = []

    def commit_of(ref, objectname, objecttype):  # ブランチは普通コミットを直接指すので、rev-parse (プロセス起動) は要らない
        if objecttype == "commit":
            return objectname
        return git("rev-parse", ref + "^{commit}").strip()

    for ref, objectname, head, date, subj, otype in _ref_list(["refs/heads"], fmt):
        h = commit_of(ref, objectname, otype)
        commits.append([h, h[:7], ("★ " if head == "*" else "") + ref[len("refs/heads/"):], date, subj])
    if not state.OPTS.no_remotes:
        for ref, objectname, head, date, subj, otype in _ref_list(["refs/remotes"], fmt):
            if ref.endswith("/HEAD"):
                continue
            h = commit_of(ref, objectname, otype)
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
        for ref, target, peeled, is_head in libgit.list_ref_infos(("refs/tags/",)):
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
        if libgit.available():
            exists = libgit.repo().references.get(ref) is not None
        else:
            exists = subprocess.run(["git", "-C", state.REPO, "show-ref", "--verify", "--quiet", ref]).returncode == 0
        if not exists:
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
    "log", "log_position", "merge_base", "topo_list", "topo_total_status", "paging", "reflog", "rev_args", "search_plan", "stash_list", "tags",
]
