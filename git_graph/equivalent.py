# 「同じ変更を持つコミット」の探索 (読み取りだけ)。リベースの操作 (engine/rebase.py) と、探索の API (rebase.py) が使う。
import subprocess
from . import app_state as state
from .gitrun import git, resolve_commit
from .i18n import UserError


def equivalent_commit_prefix(a, b, allow_merges=False):
    """A/B の共通親から、親相対の差分が連続して一致するコミット数を返す。"""
    try:
        base = git("merge-base", a, b).strip()
    except subprocess.CalledProcessError as e:
        raise UserError("rebase_equivalent_no_base") from e

    def commit_parents(commit):
        return git("rev-list", "--parents", "-n", "1", commit).split()[1:]

    def path_from_base(tip):
        path = git("rev-list", "--reverse", "--first-parent", f"{base}..{tip}").splitlines()
        previous = base
        for commit in path:
            ps = commit_parents(commit)
            if not ps or ps[0] != previous or (len(ps) != 1 and not allow_merges):
                raise UserError("rebase_equivalent_merges")
            previous = commit
        return path

    a_path = path_from_base(a)
    b_path = path_from_base(b)
    matched = 0
    while matched < len(a_path) and matched < len(b_path):
        old_parent = base if matched == 0 else a_path[matched - 1]
        b_parent = base if matched == 0 else b_path[matched - 1]
        a_diff = git("diff", "--no-ext-diff", "--binary", "--full-index", old_parent, a_path[matched])
        b_diff = git("diff", "--no-ext-diff", "--binary", "--full-index", b_parent, b_path[matched])
        if a_diff != b_diff:
            break
        matched += 1
    return base, a_path, b_path, matched

def first_parent_commits(tip):
    """先端からルートまでの first-parent コミットを新しい順に返す。"""
    out = git("log", "--first-parent", "--format=%H%x1f%P%x1f%s", tip)
    empty_tree = subprocess.check_output(
        ["git", "-C", state.REPO, "hash-object", "-t", "tree", "--stdin"], input=b""
    ).decode("ascii").strip()
    commits = []
    for line in out.splitlines():
        commit, parents, subject = (line.split("\x1f", 2) + ["", "", ""])[:3]
        parent_list = parents.split()
        commits.append({"hash": commit, "parent": parent_list[0] if parent_list else empty_tree,
                        "subject": subject})
    return commits

def nearest_equivalent_parent(a, b):
    """両方の先端から親へ探し、件名とコミット時点のツリーが一致する最初の組を返す。"""
    a_commits = first_parent_commits(a)
    b_commits = first_parent_commits(b)
    for a_index, a_commit in enumerate(a_commits):
        for b_index, b_commit in enumerate(b_commits):
            if a_commit["subject"] != b_commit["subject"]:
                continue
            comparison = subprocess.run(
                ["git", "-C", state.REPO, "diff", "--quiet", "--no-ext-diff",
                 a_commit["hash"], b_commit["hash"]],
                capture_output=True
            )
            if comparison.returncode == 0:
                return a_commits, b_commits, a_index, b_index
            if comparison.returncode != 1:
                raise ValueError(comparison.stderr.decode("utf-8", errors="replace").strip()
                                 or "git diff failed")
    return a_commits, b_commits, None, None


def find_equivalent_parents(a, b):
    """A/B の先端から親へたどって、件名と親相対差分が一致する最も近いコミットの組を返す (無ければ None)。
    戻り値: {"from": A 側のコミット, "to": B 側のコミット}。"""
    a = resolve_commit(a)
    b = resolve_commit(b)
    try:
        a_commits, b_commits, a_index, b_index = nearest_equivalent_parent(a, b)
    except UserError:
        return None
    if a_index is None:
        return None
    return {"from": a_commits[a_index]["hash"], "to": b_commits[b_index]["hash"]}


def find_equivalent_branches(a, b):
    """B の子孫のローカルブランチのうち、A の先端から親へたどって同一差分を持つ履歴があるものを返す。
    戻り値: [{"branch", "tip", "commit" (一致した B 側のコミット), "matched", "remaining" (A 側の残りコミット数)}]。"""
    a = resolve_commit(a)
    b = resolve_commit(b)
    candidates = []
    for ref in git("for-each-ref", "--format=%(refname)", "refs/heads").splitlines():
        name = ref.removeprefix("refs/heads/")
        tip = git("rev-parse", ref + "^{commit}").strip()
        if subprocess.run(["git", "-C", state.REPO, "merge-base", "--is-ancestor", b, tip],
                          capture_output=True).returncode != 0:
            continue
        if tip == a:
            continue
        a_commits, b_commits, a_index, b_index = nearest_equivalent_parent(a, tip)
        if a_index is not None:
            candidates.append({"branch": name, "tip": tip, "commit": b_commits[b_index]["hash"], "matched": 1,
                               "remaining": a_index})
    return candidates


__all__ = ["equivalent_commit_prefix", "find_equivalent_branches", "find_equivalent_parents", "first_parent_commits", "nearest_equivalent_parent"]
