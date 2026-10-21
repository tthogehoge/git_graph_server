import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from collections import Counter, defaultdict
from . import app_state as state
from .gitrun import git, resolve_commit, run_git_write
from .gitstate import (
    has_head, is_cherry_picking, is_merging, is_rebasing, is_reverting, parse_status,
    read_status_raw,
)
from .i18n import tr, UserError
from .routing import route


@route("/api/head")
def api_head(q=None):
    r = subprocess.run(["git", "-C", state.REPO, "symbolic-ref", "--short", "-q", "HEAD"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return {"branch": r.stdout.strip() or None, "merging": (is_merging() or is_rebasing()) if state.HAS_WORKTREE else False}

@route("/api/merge", method="POST")
def api_merge(body):
    """選んだコミットを、いまチェックアウトしている HEAD にマージする。
    競合したときはマージ途中の状態で止める (WIP の一覧に競合ファイルが出るので、解決してコミットする)。"""
    if not state.HAS_WORKTREE:
        raise UserError("merge_no_worktree")
    h = resolve_commit(body.get("hash", ""))
    if is_merging():
        raise UserError("merge_in_progress")
    if not has_head():
        raise UserError("merge_no_commits")
    before = git("rev-parse", "HEAD").strip()
    args = ["merge", "--no-edit"]
    if body.get("no_ff"):
        args.append("--no-ff")
    r = subprocess.run(["git", "-C", state.REPO, *args, h], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        if is_merging():  # 競合 (自動マージできなかった)
            return {"ok": True, "conflict": True,
                    "message": tr("merge_conflict", target=h[:7])}
        raise ValueError(out or tr("merge_failed"))
    if git("rev-parse", "HEAD").strip() == before:  # git の出力文言 (ロケールで変わる) ではなく、HEAD が動いたかで判定する
        return {"ok": True, "message": tr("merge_uptodate", hash=h[:7])}
    return {"ok": True, "message": tr("merge_done", hash=h[:7])}

@route("/api/rebase", method="POST")
def api_rebase(body):
    """現在のブランチを、選んだコミットの上にリベースする。競合したら自動で中止して元に戻す。"""
    if not state.HAS_WORKTREE:
        raise UserError("rebase_no_worktree")
    h = resolve_commit(body.get("hash", ""))
    if is_merging() or is_rebasing():
        raise UserError("merge_or_rebase_in_progress")
    head = api_head()
    if not head["branch"]:
        raise UserError("detached_rebase")
    before = git("rev-parse", "HEAD").strip()
    r = subprocess.run(["git", "-C", state.REPO, "rebase", h], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        if is_rebasing():  # 競合 → 元に戻す
            subprocess.run(["git", "-C", state.REPO, "rebase", "--abort"], capture_output=True)
            raise UserError("rebase_conflict", target=h[:7], cmd="git rebase")
        raise ValueError(out or tr("rebase_failed"))
    if git("rev-parse", "HEAD").strip() == before:
        return {"ok": True, "message": tr("rebase_noop", branch=head["branch"], hash=h[:7])}
    return {"ok": True, "message": tr("rebase_done", branch=head["branch"], hash=h[:7])}

@route("/api/rebase-range", method="POST")
def api_rebase_range(body):
    """A より後の現在のブランチのコミットを B の上にリベースする。
    競合したら自動で中止して元に戻す。"""
    if not state.HAS_WORKTREE:
        raise UserError("rebase_no_worktree")
    a = resolve_commit(body.get("from", ""))
    b = resolve_commit(body.get("to", ""))
    if is_merging() or is_rebasing():
        raise UserError("merge_or_rebase_in_progress")
    head = api_head()
    if not head["branch"]:
        raise UserError("detached_rebase")
    before = git("rev-parse", "HEAD").strip()
    r = subprocess.run(["git", "-C", state.REPO, "rebase", "--onto", b, a], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        if is_rebasing():
            subprocess.run(["git", "-C", state.REPO, "rebase", "--abort"], capture_output=True)
            raise UserError("rebase_conflict", target=b[:7], cmd="git rebase --onto")
        raise ValueError(out or tr("rebase_failed"))
    if git("rev-parse", "HEAD").strip() == before:
        return {"ok": True, "message": tr("rebase_noop", branch=head["branch"], hash=b[:7])}
    return {"ok": True, "message": tr("rebase_range_done", branch=head["branch"], **{"from": a[:7], "to": b[:7]})}

@route("/api/rebase-descendants", method="POST")
def api_rebase_descendants(body):
    """A の子孫にある全ローカルブランチを、分岐構造を保って B の上へ移す。

    A / 分岐点 / ブランチ先端を境界にして線形区間を近い順に処理する。
    各区間は一時ブランチ上で rebase --onto し、全区間が成功してから
    ローカルブランチの参照を一括更新するため、途中失敗で履歴が半端に
    書き換わらないようにする。
    """
    if not state.HAS_WORKTREE:
        raise UserError("rebase_no_worktree")
    a = resolve_commit(body.get("from", ""))
    b = resolve_commit(body.get("to", ""))
    if is_merging() or is_rebasing():
        raise UserError("merge_or_rebase_in_progress")
    head = api_head()
    if not head["branch"]:
        raise UserError("detached_rebase")
    if git("status", "--porcelain", "--untracked-files=all").strip():
        raise UserError("rebase_descendants_dirty")
    if a == b:
        return {"ok": True, "message": tr("rebase_noop", branch=head["branch"], hash=b[:7])}

    def parents(commit):
        fields = git("rev-list", "--parents", "-n", "1", commit).split()
        return fields[1:]

    def is_ancestor(ancestor, descendant):
        return subprocess.run(["git", "-C", state.REPO, "merge-base", "--is-ancestor", ancestor, descendant]).returncode == 0

    # A を祖先に持つローカルブランチだけを対象にする。B が A の
    # 子孫側にある場合は、B を含む系統（通常は main など）を土台として
    # 残し、そこから分岐したブランチだけを載せ替える。
    b_descends_from_a = is_ancestor(a, b)
    branch_tips = []
    for ref in git("for-each-ref", "--format=%(refname)", "refs/heads").splitlines():
        tip = git("rev-parse", ref + "^{commit}").strip()
        if is_ancestor(a, tip) and not (b_descends_from_a and is_ancestor(b, tip)):
            branch_tips.append((ref, tip))
    if not branch_tips:
        raise ValueError(tr("rebase_failed"))

    commits = {a}
    for _, tip in branch_tips:
        if tip != a:
            commits.update(git("rev-list", f"{a}..{tip}").splitlines())
    if b in commits:
        raise ValueError(tr("rebase_failed"))

    children = defaultdict(list)
    for commit in commits:
        ps = parents(commit)
        if commit != a and len(ps) != 1:
            raise UserError("rebase_descendants_merges")
        for parent in ps:
            if parent in commits:
                children[parent].append(commit)
            elif commit != a:
                # A より後の通常コミットが対象外の親を持つのはマージ構造。
                raise UserError("rebase_descendants_merges")
    for value in children.values():
        value.sort()

    # ここで止めるノードを、分岐点・末端・ブランチ参照の付いたコミットにする。
    boundaries = {a}
    boundaries.update(tip for _, tip in branch_tips)
    boundaries.update(commit for commit in commits if len(children[commit]) != 1)

    original_branch = head["branch"]
    temp = f"git-graph-rebase-{os.getpid()}-{threading.get_ident()}"
    while subprocess.run(["git", "-C", state.REPO, "show-ref", "--verify", "--quiet", f"refs/heads/{temp}"]).returncode == 0:
        temp += "-x"
    mapping = {a: b}
    queue = [a]
    temp_exists = False
    try:
        run_git_write("checkout", "-b", temp, a)
        temp_exists = True
        while queue:
            old_base = queue.pop(0)
            new_base = mapping[old_base]
            for first in children[old_base]:
                old_tip = first
                while old_tip not in boundaries:
                    next_nodes = children[old_tip]
                    if len(next_nodes) != 1:
                        raise UserError("rebase_descendants_merges")
                    old_tip = next_nodes[0]

                old_segment = git("rev-list", "--reverse", f"{old_base}..{old_tip}").splitlines()
                run_git_write("checkout", "-B", temp, old_tip)
                r = subprocess.run(["git", "-C", state.REPO, "rebase", "--onto", new_base, old_base],
                                   capture_output=True, text=True, encoding="utf-8", errors="replace")
                if r.returncode != 0:
                    conflicted = is_rebasing()
                    if conflicted:
                        subprocess.run(["git", "-C", state.REPO, "rebase", "--abort"], capture_output=True)
                        raise UserError("rebase_conflict", target=new_base[:7], cmd="git rebase --onto")
                    out = (r.stdout + r.stderr).strip()
                    if out:
                        raise ValueError(out)
                    raise ValueError(tr("rebase_failed"))
                new_tip = git("rev-parse", "HEAD").strip()
                new_segment = git("rev-list", "--reverse", f"{new_base}..{new_tip}").splitlines()
                if len(old_segment) != len(new_segment):
                    raise UserError("rebase_descendants_merges")
                mapping.update(zip(old_segment, new_segment))
                mapping[old_tip] = new_tip
                queue.append(old_tip)

        updates = [(name, tip, mapping.get(tip)) for name, tip in branch_tips]
        if any(new_tip is None for _, _, new_tip in updates):
            raise ValueError(tr("rebase_failed"))
        # 参照の更新は、全区間のリベースが成功した後にだけ行う。
        lines = [f"update {ref} {new_tip} {old_tip}" for ref, old_tip, new_tip in updates if new_tip != old_tip]
        if lines:
            r = subprocess.run(["git", "-C", state.REPO, "update-ref", "--stdin"], input="\n".join(lines) + "\n",
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode != 0:
                raise ValueError((r.stderr or r.stdout).strip() or tr("rebase_failed"))
        run_git_write("checkout", original_branch)
        run_git_write("branch", "-D", temp)
        temp_exists = False
    except Exception:
        if is_rebasing():
            subprocess.run(["git", "-C", state.REPO, "rebase", "--abort"], capture_output=True)
        if temp_exists:
            subprocess.run(["git", "-C", state.REPO, "checkout", original_branch], capture_output=True)
            subprocess.run(["git", "-C", state.REPO, "branch", "-D", temp], capture_output=True)
        raise
    changed = len([1 for _, old_tip, new_tip in updates if old_tip != new_tip])
    return {"ok": True, "message": tr("rebase_descendants_done", branches=changed, **{"from": a[:7], "to": b[:7]})}

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

@route("/api/find-equivalent-parents", method="POST")
def api_find_equivalent_parents(body):
    """A/B の先端から親へ探し、件名と親相対差分が一致するコミットを返す。"""
    a = resolve_commit(body.get("from", ""))
    b = resolve_commit(body.get("to", ""))
    try:
        a_commits, b_commits, a_index, b_index = nearest_equivalent_parent(a, b)
    except UserError:
        return {"message": tr("find_equivalent_parents_none")}
    if a_index is None:
        return {"message": tr("find_equivalent_parents_none")}
    new_a, new_b = a_commits[a_index]["hash"], b_commits[b_index]["hash"]
    return {"from": new_a, "to": new_b,
            "message": tr("find_equivalent_parents_found", a=new_a[:7], b=new_b[:7])}

@route("/api/rebase-equivalent", method="POST")
def api_rebase_equivalent(body):
    """A/B の先端から親へ同一差分を探し、A の一致コミット以降を B 側へ寄せる。"""
    if not state.HAS_WORKTREE:
        raise UserError("rebase_no_worktree")
    a = resolve_commit(body.get("from", ""))
    b = resolve_commit(body.get("to", ""))
    if is_merging() or is_rebasing():
        raise UserError("merge_or_rebase_in_progress")
    head = api_head()
    if not head["branch"]:
        raise UserError("detached_rebase")
    if git("status", "--porcelain", "--untracked-files=all").strip():
        raise UserError("rebase_descendants_dirty")
    if a == b:
        return {"ok": True, "message": tr("rebase_equivalent_noop", **{"from": a[:7], "to": b[:7]})}

    target_branches = []
    for ref in git("for-each-ref", "--format=%(refname)", "refs/heads").splitlines():
        tip = git("rev-parse", ref + "^{commit}").strip()
        if tip == a:
            target_branches.append((ref, tip))
    if not target_branches:
        raise UserError("rebase_equivalent_head", **{"from": a[:7]})

    a_commits, b_commits, a_index, b_index = nearest_equivalent_parent(a, b)
    if a_index is None:
        return {"ok": True, "message": tr("rebase_equivalent_noop", **{"from": a[:7], "to": b[:7]})}

    matched_a = a_commits[a_index]["hash"]
    matched_b = b_commits[b_index]["hash"]
    if matched_a == matched_b:
        return {"ok": True, "message": tr("rebase_equivalent_noop", **{"from": a[:7], "to": b[:7]})}
    # a_commits は先端からの順なので、一致コミットより前が A 側のリベース対象。
    replay = a_commits[:a_index]
    if not replay:
        new_tip = matched_b
        run_git_write("checkout", "--detach", a)
        try:
            for ref, old_tip in target_branches:
                run_git_write("update-ref", ref, new_tip, old_tip)
            run_git_write("checkout", head["branch"])
        except Exception:
            subprocess.run(["git", "-C", state.REPO, "checkout", head["branch"]], capture_output=True)
            raise
        return {"ok": True, "message": tr("rebase_equivalent_done", **{"from": a[:7], "to": new_tip[:7]}, matched=1)}

    replay_hashes = {c["hash"] for c in replay}
    for commit in replay_hashes:
        parents = git("rev-list", "--parents", "-n", "1", commit).split()[1:]
        if len(parents) > 1:
            raise UserError("rebase_equivalent_merges")

    temp = f"git-graph-rebase-equivalent-{os.getpid()}-{threading.get_ident()}"
    while subprocess.run(["git", "-C", state.REPO, "show-ref", "--verify", "--quiet", f"refs/heads/{temp}"]).returncode == 0:
        temp += "-x"
    try:
        run_git_write("checkout", "--detach", a)
        run_git_write("checkout", "-b", temp, a)
        r = subprocess.run(["git", "-C", state.REPO, "rebase", "--onto", matched_b, matched_a],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            if is_rebasing():
                subprocess.run(["git", "-C", state.REPO, "rebase", "--abort"], capture_output=True)
                raise UserError("rebase_conflict", target=matched_b[:7], cmd="git rebase --onto")
            raise ValueError((r.stdout + r.stderr).strip() or tr("rebase_failed"))
        new_tip = git("rev-parse", "HEAD").strip()
        for ref, old_tip in target_branches:
            run_git_write("update-ref", ref, new_tip, old_tip)
        run_git_write("checkout", head["branch"])
        run_git_write("branch", "-D", temp)
    except Exception:
        if is_rebasing():
            subprocess.run(["git", "-C", state.REPO, "rebase", "--abort"], capture_output=True)
        subprocess.run(["git", "-C", state.REPO, "checkout", head["branch"]], capture_output=True)
        subprocess.run(["git", "-C", state.REPO, "branch", "-D", temp], capture_output=True)
        raise
    return {"ok": True, "message": tr("rebase_equivalent_done", **{"from": a[:7], "to": new_tip[:7]}, matched=1)}

@route("/api/find-equivalent-branch", method="POST")
def api_find_equivalent_branch(body):
    """B の子孫ブランチから、A と最も近い連続同一差分の履歴を探す。"""
    a = resolve_commit(body.get("from", ""))
    b = resolve_commit(body.get("to", ""))
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
            candidates.append({
                "branch": name,
                "tip": tip,
                "commit": b_commits[b_index]["hash"],
                "matched": 1,
                "remaining": a_index,
            })

    if not candidates:
        return {"message": tr("find_equivalent_branch_none", **{"from": a[:7], "to": b[:7]})}
    nearest = min(c["remaining"] for c in candidates)
    best = [c for c in candidates if c["remaining"] == nearest]
    best.sort(key=lambda c: (-c["matched"], c["branch"]))
    labels = ", ".join(tr("find_equivalent_branch_candidate", branch=c["branch"],
                         commit=c["commit"][:7], matched=c["matched"]) for c in best)
    return {"to": best[0]["tip"],
            "message": tr("find_equivalent_branch_found", branches=labels, remaining=nearest)}

@route("/api/cherry-pick", method="POST")
def api_cherry_pick(body):
    """選んだコミットを、いまチェックアウトしている HEAD にチェリーピックする。
    競合したときと、変更が空になるとき (既に取り込み済み) は、中止して元に戻す。"""
    if not state.HAS_WORKTREE:
        raise UserError("cherry_no_worktree")
    h = resolve_commit(body.get("hash", ""))
    if is_merging() or is_rebasing() or is_cherry_picking() or is_reverting():
        raise UserError("op_in_progress")
    if not has_head():
        raise UserError("cherry_no_commits")
    if len(git("rev-list", "--parents", "-n", "1", h).split()) > 2:
        raise UserError("cherry_merge_commit")
    before = git("rev-parse", "HEAD").strip()
    r = subprocess.run(["git", "-C", state.REPO, "cherry-pick", h], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        if is_cherry_picking():
            unmerged = subprocess.run(["git", "-C", state.REPO, "-c", "core.quotepath=false", "diff", "--name-only", "--diff-filter=U"],
                                      capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
            subprocess.run(["git", "-C", state.REPO, "cherry-pick", "--abort"], capture_output=True)
            if not unmerged:  # 競合ではなく、変更が空になった
                raise UserError("cherry_empty", hash=h[:7])
            raise UserError("cherry_conflict", hash=h[:7])
        raise ValueError(out or tr("cherry_failed"))
    if git("rev-parse", "HEAD").strip() == before:
        return {"ok": True, "message": tr("cherry_noop", hash=h[:7])}
    return {"ok": True, "message": tr("cherry_done", hash=h[:7])}

@route("/api/revert", method="POST")
def api_revert(body):
    """選んだコミットの変更を打ち消すコミットを、いまの HEAD の上に作る (git revert)。
    競合したときと、変更が空になるとき (既に打ち消し済み) は、中止して元に戻す。マージコミットは第1親を基準にする。"""
    if not state.HAS_WORKTREE:
        raise UserError("revert_no_worktree")
    h = resolve_commit(body.get("hash", ""))
    if is_merging() or is_rebasing() or is_cherry_picking() or is_reverting():
        raise UserError("op_in_progress")
    if not has_head():
        raise UserError("revert_no_commits")
    is_merge = len(git("rev-list", "--parents", "-n", "1", h).split()) > 2
    args = ["revert", "--no-edit"] + (["-m", "1"] if is_merge else [])
    r = subprocess.run(["git", "-C", state.REPO, *args, h], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        if is_reverting():
            unmerged = subprocess.run(["git", "-C", state.REPO, "-c", "core.quotepath=false", "diff", "--name-only", "--diff-filter=U"],
                                      capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
            subprocess.run(["git", "-C", state.REPO, "revert", "--abort"], capture_output=True)
            if not unmerged:  # 競合ではなく、変更が空になった
                raise UserError("revert_empty", hash=h[:7])
            raise UserError("revert_conflict", hash=h[:7])
        raise ValueError(out or tr("revert_failed"))
    return {"ok": True, "message": tr("revert_done_merge" if is_merge else "revert_done", hash=h[:7])}

@route("/api/abort", method="POST")
def api_abort(body):
    """進行中のマージ / リベース / チェリーピック / リバートを中断して、開始前の状態に戻す。"""
    if not state.HAS_WORKTREE:
        raise UserError("abort_no_worktree")
    for cmd, active in (("merge", is_merging), ("rebase", is_rebasing),
                        ("cherry-pick", is_cherry_picking), ("revert", is_reverting)):
        if active():
            run_git_write(cmd, "--abort")
            return {"ok": True, "message": tr("aborted", cmd=cmd)}
    raise UserError("nothing_to_abort")

def unmerged_stages(path):
    """競合しているファイルが持つインデックスのステージ番号 (2 = ours, 3 = theirs)。片側で削除されていると、その番号がない。"""
    out = git("ls-files", "-u", "-z", "--", path, literal=True)
    return {rec.split("\t", 1)[0].split()[-1] for rec in out.split("\0") if "\t" in rec}

@route("/api/resolve", method="POST")
def api_resolve(body):
    """競合しているファイルを、ours か theirs の内容で丸ごと置き換えて解決済みにする (片側で削除されていたら削除する)。"""
    if not state.WIP_ENABLED:
        raise UserError("wip_disabled")
    side = body.get("side")
    if side not in ("ours", "theirs"):
        raise UserError("resolve_side_invalid")
    paths = body.get("paths")
    if not isinstance(paths, list) or not paths or not all(isinstance(p, str) for p in paths):
        raise UserError("select_files")
    s = parse_status(read_status_raw(), limit=10 ** 9)
    bad = set(paths) - {f["path"] for f in s["conflicts"]}  # 任意のパスを触らせない。いま競合と報告されているものだけ
    if bad:
        raise UserError("resolve_not_conflict", files=", ".join(sorted(bad)[:3]))
    stage = "2" if side == "ours" else "3"
    keep, remove = [], []
    for p in sorted(set(paths)):
        (keep if stage in unmerged_stages(p) else remove).append(p)
    step = 200
    for i in range(0, len(keep), step):
        part = keep[i:i + step]
        run_git_write("checkout", "--" + side, "--", *part, literal=True)
        run_git_write("add", "--", *part, literal=True)
    for i in range(0, len(remove), step):
        run_git_write("rm", "-q", "-f", "--", *remove[i:i + step], literal=True)
    return {"ok": True, "message": tr("resolved", n=len(keep) + len(remove), side=side)}

REBASE_I_MAX = 200

REBASE_I_ACTIONS = ("pick", "reword", "squash", "fixup", "drop")

def rebase_todo_hashes(base):
    """git rebase -i <base> が最初に作る todo と同じコミット列 (古い順)。"""
    out = git("rev-list", "--reverse", "--topo-order", "--right-only", "--cherry-pick",
              "--no-merges", base + "...HEAD")
    return out.split()

def check_rebase_i_range(base):
    """対話的リベースできる範囲か調べて、対象コミットのハッシュ (古い順) を返す。"""
    r = subprocess.run(["git", "-C", state.REPO, "rev-list", "--count", "--merges", base + "..HEAD"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode == 0 and r.stdout.strip() not in ("", "0"):
        raise UserError("ri_merges")
    hashes = rebase_todo_hashes(base)
    if not hashes:
        raise UserError("ri_nothing", base=base[:7])
    if len(hashes) > REBASE_I_MAX:
        raise UserError("ri_too_many", n=len(hashes), max=REBASE_I_MAX)
    return hashes

@route("/api/rebase-todo")
def api_rebase_todo(q):
    """対話的リベースの対象コミット一覧 (画面の編集ダイアログに出す)。"""
    if not state.HAS_WORKTREE:
        raise UserError("rebase_no_worktree")
    base = resolve_commit((q.get("hash") or [""])[0])
    head = api_head()
    if not head["branch"]:
        raise UserError("detached_rebase")
    hashes = check_rebase_i_range(base)
    out = git("log", "--no-walk=unsorted", "--date=short", "--format=%H%x1f%an%x1f%ad%x1f%s%x1f%B%x1e", *hashes)
    commits = []
    for rec in out.split("\x1e"):
        rec = rec.lstrip("\n")
        if not rec:
            continue
        h, an, ad, subj, msg = rec.split("\x1f", 4)
        commits.append({"hash": h, "short": h[:7], "author": an, "date": ad, "subject": subj, "message": msg.strip()})
    return {"base": base, "branch": head["branch"], "commits": commits}

@route("/api/rebase-i", method="POST")
def api_rebase_i(body):
    """git rebase -i 相当。画面で編集した todo (順序と pick/reword/squash/fixup/drop) をそのまま実行する。
    エディタは起こさず、todo は GIT_SEQUENCE_EDITOR で差し替える。reword は、そのコミット (squash/fixup を
    まとめた後) に exec で commit --amend -F を挟んで新しいメッセージにする。
    競合やエラーで止まったときは自動で rebase --abort して元に戻す。"""
    if not state.HAS_WORKTREE:
        raise UserError("rebase_no_worktree")
    base = resolve_commit(body.get("hash", ""))
    if is_merging() or is_rebasing():
        raise UserError("merge_or_rebase_in_progress")
    head = api_head()
    if not head["branch"]:
        raise UserError("detached_rebase")
    if not has_head():
        raise UserError("rebase_no_commits")
    items = body.get("todo")
    if not isinstance(items, list) or not items or not all(isinstance(i, dict) for i in items):
        raise UserError("todo_invalid")
    todo = []
    for it in items:
        act, h = it.get("action"), str(it.get("hash", ""))
        if act not in REBASE_I_ACTIONS or not re.fullmatch(r"[0-9a-f]{40}", h):
            raise UserError("todo_invalid")
        msg = str(it.get("message") or "").strip()
        if act == "reword" and not msg:
            raise UserError("ri_reword_msg_required", hash=h[:7])
        todo.append((act, h, msg))
    expected = check_rebase_i_range(base)
    if sorted(h for _, h, _ in todo) != sorted(expected):
        raise UserError("ri_changed")
    kept = [t for t in todo if t[0] != "drop"]
    if kept and kept[0][0] in ("squash", "fixup"):
        raise UserError("ri_first_squash")

    def fwd(path):  # sh から使うので区切りは / にそろえる (Windows 対策)
        return path.replace("\\", "/")

    before = git("rev-parse", "HEAD").strip()
    tmp = tempfile.mkdtemp(prefix="git_graph_rebase_")
    try:
        lines, pending = [], None  # pending: 直前のグループの reword 用 exec 行
        for act, h, msg in todo:
            if act in ("pick", "reword"):
                if pending:
                    lines.append(pending)
                    pending = None
                lines.append(f"pick {h}")
                if act == "reword":
                    mp = os.path.join(tmp, f"msg{len(lines)}.txt")
                    with open(mp, "w", encoding="utf-8", newline="\n") as f:
                        f.write(msg + "\n")
                    pending = f'exec git commit --amend -q -F "{fwd(mp)}"'
            else:  # squash / fixup / drop
                lines.append(f"{act} {h}")
        if pending:
            lines.append(pending)
        todo_path = os.path.join(tmp, "todo.txt")
        with open(todo_path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
        script = os.path.join(tmp, "seq_editor.py")
        with open(script, "w", encoding="utf-8") as f:
            f.write("import shutil, sys\nshutil.copyfile(sys.argv[1], sys.argv[2])\n")  # argv[2] が git の todo
        env = dict(os.environ, GIT_EDITOR=":", GIT_TERMINAL_PROMPT="0",
                   GIT_SEQUENCE_EDITOR=f'"{fwd(sys.executable)}" "{fwd(script)}" "{fwd(todo_path)}"')
        r = subprocess.run(["git", "-C", state.REPO, "rebase", "-i", base], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env)
        out = (r.stdout + r.stderr).strip()
        if r.returncode != 0:
            if is_rebasing():  # 競合など → 元に戻す
                subprocess.run(["git", "-C", state.REPO, "rebase", "--abort"], capture_output=True)
                tail = "\n".join(out.splitlines()[-6:])
                raise UserError("ri_aborted", tail=tail)
            raise ValueError(out or tr("rebase_failed"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if git("rev-parse", "HEAD").strip() == before:
        return {"ok": True, "message": tr("ri_noop", branch=head["branch"])}
    n = Counter(a for a, _, _ in todo)
    parts = [tr(key, n=n[k]) for k, key in (("reword", "ri_part_reword"), ("squash", "ri_part_squash"),
                                           ("fixup", "ri_part_fixup"), ("drop", "ri_part_drop")) if n[k]]
    if parts:
        return {"ok": True, "message": tr("ri_done_parts", branch=head["branch"], base=base[:7], parts=" / ".join(parts))}
    return {"ok": True, "message": tr("ri_done", branch=head["branch"], base=base[:7])}


__all__ = [
    "REBASE_I_ACTIONS", "REBASE_I_MAX", "api_abort", "api_cherry_pick",
    "api_find_equivalent_branch", "api_find_equivalent_parents", "api_head", "api_merge",
    "api_rebase", "api_rebase_descendants", "api_rebase_equivalent", "api_rebase_i",
    "api_rebase_range", "api_rebase_todo", "api_resolve", "api_revert", "check_rebase_i_range",
    "equivalent_commit_prefix", "first_parent_commits", "nearest_equivalent_parent",
    "rebase_todo_hashes", "unmerged_stages",
]
