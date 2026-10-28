import os
import re
import shutil
import subprocess
import sys
import threading
from collections import Counter, defaultdict
from . import app_state as state
from . import tmpdir
from .gitrun import git, resolve_commit, run_git_write
from .gitstate import (
    has_head, is_cherry_picking, is_merging, is_rebasing, is_reverting, parse_status,
    read_status_raw,
)
from .i18n import tr, UserError
from .refops import resolve_dirty_worktree, run_resolving, with_stash_note
from .routing import route


# ---- リベースで作り直されたコミットに付いていたタグを、新しいコミットへ付け替える ----
def tag_targets():
    """[{ref, sha, annotated, commit}]。注釈付きタグは、直接コミットを指しているものだけ (タグのタグは対象外)。"""
    fmt = "%(refname)%09%(objecttype)%09%(objectname)%09%(*objectname)%09%(*objecttype)"
    tags = []
    for line in git("for-each-ref", "--format=" + fmt, "refs/tags").splitlines():
        ref, kind, sha, peeled, peeled_kind = (line.split("\t") + [""] * 5)[:5]
        if kind == "commit":
            tags.append({"ref": ref, "sha": sha, "annotated": False, "commit": sha})
        elif kind == "tag" and peeled_kind == "commit":
            tags.append({"ref": ref, "sha": sha, "annotated": True, "commit": peeled})
    return tags


def commit_patch_ids(rev_range):
    """rev_range のマージ以外のコミットを古い順に [(コミット, patch-id)] で返す (空のコミットは含まれない)。"""
    log = subprocess.run(["git", "-C", state.REPO, "log", "--reverse", "--no-merges", "-p", "--format=commit %H", rev_range],
                         capture_output=True)
    if log.returncode != 0:
        return []
    pid = subprocess.run(["git", "-C", state.REPO, "patch-id", "--stable"], input=log.stdout, capture_output=True)
    result = []
    for line in pid.stdout.decode("utf-8", "replace").splitlines():
        patch_id, _, commit = line.partition(" ")
        if commit.strip():
            result.append((commit.strip(), patch_id))
    return result


def snapshot_for_tags(rev_range):
    """リベース前に呼ぶ。rev_range の中のコミットを指すタグがあれば、そのコミットの patch-id を返す (無ければ None)。"""
    tags = tag_targets()
    if not tags:
        return None
    commits = set(git("rev-list", rev_range).split())
    if not any(t["commit"] in commits for t in tags):
        return None
    return commit_patch_ids(rev_range)


def tag_mapping(old_ids, new_range):
    """リベース前後で同じ変更 (patch-id) を持つコミットを、古い順に突き合わせて {旧コミット: 新コミット} にする。"""
    new_ids = commit_patch_ids(new_range)
    used, mapping = set(), {}
    for old_commit, old_pid in old_ids or []:
        for new_commit, new_pid in new_ids:
            if new_pid == old_pid and new_commit not in used:
                mapping[old_commit] = new_commit
                used.add(new_commit)
                break
    return mapping


def move_tags(mapping):
    """mapping ({旧コミット: 新コミット}) の旧コミットに付いていたタグを、新コミットへ付け替えて、付け替えた数を返す。
    注釈付きタグは、メッセージ・タグ付けした人と日時を保ったまま、指す先だけ変えたタグを作り直す (署名は無効になる)。"""
    mapping = {old: new for old, new in mapping.items() if old != new}
    if not mapping:
        return 0
    lines = []
    for tag in tag_targets():
        new = mapping.get(tag["commit"])
        if not new:
            continue
        new_obj = new
        if tag["annotated"]:
            raw = subprocess.run(["git", "-C", state.REPO, "cat-file", "tag", tag["sha"]], capture_output=True)
            first, newline, rest = raw.stdout.partition(b"\n")
            if raw.returncode != 0 or first != b"object " + tag["commit"].encode():
                continue
            made = subprocess.run(["git", "-C", state.REPO, "mktag"], input=b"object " + new.encode() + newline + rest,
                                  capture_output=True)
            if made.returncode != 0:
                continue
            new_obj = made.stdout.decode().strip()
        lines.append(f"update {tag['ref']} {new_obj} {tag['sha']}")
    if lines:
        r = subprocess.run(["git", "-C", state.REPO, "update-ref", "--stdin"], input="\n".join(lines) + "\n",
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            raise ValueError((r.stderr or r.stdout).strip() or tr("rebase_failed"))
    return len(lines)


def with_tags_note(message, moved):
    return f"{message} {tr('rebase_tags_moved', n=moved)}" if moved else message


def rebase_tag_only_tips(base, onto, tips):
    """ブランチから到達できないタグ先履歴を onto の上に作り直し、旧新コミット対応表を返す。"""
    tips = set(tips)
    if not tips:
        return {}

    commits = {base}
    for tip in tips:
        commits.update(git("rev-list", f"{base}..{tip}").splitlines())
    if onto in commits:
        raise ValueError(tr("rebase_failed"))

    children = defaultdict(list)
    for commit in commits:
        ps = git("rev-list", "--parents", "-n", "1", commit).split()[1:]
        if commit != base and len(ps) != 1:
            raise UserError("rebase_descendants_merges")
        for parent in ps:
            if parent in commits:
                children[parent].append(commit)
            elif commit != base:
                raise UserError("rebase_descendants_merges")
    for value in children.values():
        value.sort()

    boundaries = {base, *tips}
    boundaries.update(commit for commit in commits if len(children[commit]) != 1)
    original_branch = api_head()["branch"]
    temp = f"git-graph-tag-rebase-{os.getpid()}-{threading.get_ident()}"
    while subprocess.run(["git", "-C", state.REPO, "show-ref", "--verify", "--quiet",
                          f"refs/heads/{temp}"]).returncode == 0:
        temp += "-x"

    mapping = {base: onto}
    queue = [base]
    temp_exists = False
    try:
        run_git_write("checkout", "-b", temp, base)
        temp_exists = True
        while queue:
            old_base = queue.pop(0)
            new_base = mapping[old_base]
            for first in children[old_base]:
                old_tip = first
                while old_tip not in boundaries:
                    old_tip = children[old_tip][0]
                old_segment = git("rev-list", "--reverse", f"{old_base}..{old_tip}").splitlines()
                run_git_write("checkout", "-B", temp, old_tip)
                r = subprocess.run(["git", "-C", state.REPO, "rebase", "--onto", new_base, old_base],
                                   capture_output=True, text=True, encoding="utf-8", errors="replace")
                if r.returncode != 0:
                    if is_rebasing():
                        subprocess.run(["git", "-C", state.REPO, "rebase", "--abort"], capture_output=True)
                        raise UserError("rebase_conflict", target=new_base[:7], cmd="git rebase --onto")
                    raise ValueError((r.stdout + r.stderr).strip() or tr("rebase_failed"))
                new_tip = git("rev-parse", "HEAD").strip()
                new_segment = git("rev-list", "--reverse", f"{new_base}..{new_tip}").splitlines()
                if len(old_segment) != len(new_segment):
                    raise UserError("rebase_descendants_merges")
                mapping.update(zip(old_segment, new_segment))
                mapping[old_tip] = new_tip
                queue.append(old_tip)
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
    return {old: new for old, new in mapping.items() if old != base}


@route("/api/head")
def api_head(q=None):
    r = subprocess.run(["git", "-C", state.REPO, "symbolic-ref", "--short", "-q", "HEAD"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return {"branch": r.stdout.strip() or None, "merging": (is_merging() or is_rebasing()) if state.HAS_WORKTREE else False}

@route("/api/merge", method="POST")
def api_merge(body):
    """選んだコミットを、いまチェックアウトしている HEAD にマージする。
    競合したときはマージ途中の状態で止める (WIP の一覧に競合ファイルが出るので、解決してコミットする)。
    編集中のファイルや同名の管理外ファイルに邪魔されてマージできないときは、止めているファイルを返す ({blocked, modified, untracked})。
    on_conflict="stash" で再実行すると全変更を stash してから、"discard" で再実行すると止めているファイルだけ破棄してからマージする。"""
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
    def run(c_locale):
        env = dict(os.environ, LC_ALL="C", LANGUAGE="C") if c_locale else None
        return subprocess.run(["git", "-C", state.REPO, *args, h], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env)

    r, blocked, stashed = run_resolving(run, body.get("on_conflict"))
    if blocked:
        return blocked
    stash = {"stashed": stashed}
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        if is_merging():  # 競合 (自動マージできなかった)
            return {"ok": True, "conflict": True,
                    "message": with_stash_note(stash, tr("merge_conflict", target=h[:7]))}
        raise ValueError(with_stash_note(stash, out or tr("merge_failed")))
    if git("rev-parse", "HEAD").strip() == before:  # git の出力文言 (ロケールで変わる) ではなく、HEAD が動いたかで判定する
        return {"ok": True, "message": with_stash_note(stash, tr("merge_uptodate", hash=h[:7]))}
    return {"ok": True, "message": with_stash_note(stash, tr("merge_done", hash=h[:7]))}

def _git_run(args, c_locale=False, env=None):
    env = dict(os.environ if env is None else env)
    if c_locale:
        env.update(LC_ALL="C", LANGUAGE="C")
    return subprocess.run(["git", "-C", state.REPO, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)

@route("/api/rebase", method="POST")
def api_rebase(body):
    """現在のブランチを、選んだコミットの上にリベースする。競合したら自動で中止して元に戻す。
    編集中のファイルに邪魔されて実行できないときは、止めているファイルを返す ({blocked, modified, untracked})。
    on_conflict="stash" で再実行すると全変更を stash してから、"discard" で再実行すると止めているファイルだけ破棄してからリベースする。"""
    if not state.HAS_WORKTREE:
        raise UserError("rebase_no_worktree")
    h = resolve_commit(body.get("hash", ""))
    if is_merging() or is_rebasing():
        raise UserError("merge_or_rebase_in_progress")
    head = api_head()
    if not head["branch"]:
        raise UserError("detached_rebase")
    before = git("rev-parse", "HEAD").strip()
    r, blocked, stashed = run_resolving(lambda c: _git_run(["rebase", h], c), body.get("on_conflict"), rebase_dirty=True)
    if blocked:
        return blocked
    stash = {"stashed": stashed}
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        if is_rebasing():  # 競合 → 元に戻す
            subprocess.run(["git", "-C", state.REPO, "rebase", "--abort"], capture_output=True)
            raise UserError("rebase_conflict", target=h[:7], cmd="git rebase")
        raise ValueError(with_stash_note(stash, out or tr("rebase_failed")))
    if git("rev-parse", "HEAD").strip() == before:
        return {"ok": True, "message": with_stash_note(stash, tr("rebase_noop", branch=head["branch"], hash=h[:7]))}
    return {"ok": True, "message": with_stash_note(stash, tr("rebase_done", branch=head["branch"], hash=h[:7]))}

@route("/api/rebase-range", method="POST")
def api_rebase_range(body):
    """A より後の現在のブランチのコミットを B の上にリベースする。
    競合したら自動で中止して元に戻す。
    編集中のファイルに邪魔されて実行できないときは、止めているファイルを返す ({blocked, modified, untracked})。"""
    if not state.HAS_WORKTREE:
        raise UserError("rebase_no_worktree")
    a = resolve_commit(body.get("from", ""))
    b = resolve_commit(body.get("to", ""))
    if is_merging() or is_rebasing():
        raise UserError("merge_or_rebase_in_progress")
    head = api_head()
    if not head["branch"]:
        raise UserError("detached_rebase")
    blocked, stashed = resolve_dirty_worktree(body.get("on_conflict"))
    if blocked:
        return blocked
    stash = {"stashed": stashed}
    before = git("rev-parse", "HEAD").strip()
    before_commits = set(git("rev-list", before).split())
    all_branch_commits = set(git("rev-list", "--branches").split())
    tag_tips = set()
    a_is_ancestor_of_b = subprocess.run(
        ["git", "-C", state.REPO, "merge-base", "--is-ancestor", a, b], capture_output=True
    ).returncode == 0
    for tag in tag_targets():
        tip = tag["commit"]
        if (tip in all_branch_commits
                or subprocess.run(["git", "-C", state.REPO, "merge-base", "--is-ancestor", a, tip],
                                  capture_output=True).returncode != 0
                or (a_is_ancestor_of_b
                    and subprocess.run(["git", "-C", state.REPO, "merge-base", "--is-ancestor", b, tip],
                                       capture_output=True).returncode == 0)):
            continue
        path_commits = set(git("rev-list", f"{a}..{tip}").split())
        if path_commits.isdisjoint(before_commits):
            tag_tips.add(tip)
    tag_mapping_only = rebase_tag_only_tips(a, b, tag_tips)
    old_ids = snapshot_for_tags(f"{a}..{before}")  # A..HEAD のコミットに付いたタグがあれば、付け替えのために変更を控えておく
    r = _git_run(["rebase", "--onto", b, a])
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        if is_rebasing():
            subprocess.run(["git", "-C", state.REPO, "rebase", "--abort"], capture_output=True)
            raise UserError("rebase_conflict", target=b[:7], cmd="git rebase --onto")
        raise ValueError(with_stash_note(stash, out or tr("rebase_failed")))
    mapping = tag_mapping(old_ids, f"{b}..HEAD") if old_ids else {}
    mapping.update(tag_mapping_only)
    moved = move_tags(mapping)
    if git("rev-parse", "HEAD").strip() == before:
        message = with_stash_note(stash, tr("rebase_noop", branch=head["branch"], hash=b[:7]))
        return {"ok": True, "message": with_tags_note(message, moved)}
    return {"ok": True, "message": with_tags_note(with_stash_note(stash, tr("rebase_range_done", branch=head["branch"], **{"from": a[:7], "to": b[:7]})), moved)}

@route("/api/rebase-descendants", method="POST")
def api_rebase_descendants(body):
    """A の子孫にある全ローカルブランチとブランチのないタグ先履歴を、分岐構造を保って B の上へ移す。

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
    blocked, stashed = resolve_dirty_worktree(body.get("on_conflict"))
    if blocked:
        return blocked
    stash = {"stashed": stashed}
    if a == b:
        return {"ok": True, "message": with_stash_note(stash, tr("rebase_noop", branch=head["branch"], hash=b[:7]))}

    def parents(commit):
        fields = git("rev-list", "--parents", "-n", "1", commit).split()
        return fields[1:]

    def is_ancestor(ancestor, descendant):
        return subprocess.run(["git", "-C", state.REPO, "merge-base", "--is-ancestor", ancestor, descendant]).returncode == 0

    # A を祖先に持つローカルブランチだけを対象にする。B が A の
    # 子孫側にある場合は、B を含む系統（通常は main など）を土台として
    # 残し、そこから分岐したブランチだけを載せ替える。
    b_descends_from_a = is_ancestor(a, b)
    all_branch_commits = set(git("rev-list", "--branches").split())
    branch_tips = []
    for ref in git("for-each-ref", "--format=%(refname)", "refs/heads").splitlines():
        tip = git("rev-parse", ref + "^{commit}").strip()
        if is_ancestor(a, tip) and not (b_descends_from_a and is_ancestor(b, tip)):
            branch_tips.append((ref, tip))

    commits = {a}
    for _, tip in branch_tips:
        if tip != a:
            commits.update(git("rev-list", f"{a}..{tip}").splitlines())
    tag_tips = {
        tag["commit"] for tag in tag_targets()
        if tag["commit"] not in all_branch_commits
        and is_ancestor(a, tag["commit"])
        and not (
            b_descends_from_a
            and (is_ancestor(b, tag["commit"]) or is_ancestor(tag["commit"], b))
        )
    }
    if not branch_tips and not tag_tips:
        raise ValueError(tr("rebase_failed"))
    for tip in tag_tips:
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

    # ここで止めるノードを、分岐点・末端・ブランチ/タグ参照の付いたコミットにする。
    boundaries = {a}
    boundaries.update(tip for _, tip in branch_tips)
    boundaries.update(tag_tips)
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
    moved = move_tags({old: new for old, new in mapping.items() if old != a})  # A は土台なので、A に付いたタグは動かさない
    return {"ok": True, "message": with_tags_note(with_stash_note(stash, tr("rebase_descendants_done", branches=changed, **{"from": a[:7], "to": b[:7]})), moved)}

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
    blocked, stashed = resolve_dirty_worktree(body.get("on_conflict"))
    if blocked:
        return blocked
    stash = {"stashed": stashed}
    if a == b:
        return {"ok": True, "message": with_stash_note(stash, tr("rebase_equivalent_noop", **{"from": a[:7], "to": b[:7]}))}

    target_branches = []
    for ref in git("for-each-ref", "--format=%(refname)", "refs/heads").splitlines():
        tip = git("rev-parse", ref + "^{commit}").strip()
        if tip == a:
            target_branches.append((ref, tip))
    if not target_branches:
        raise UserError("rebase_equivalent_head", **{"from": a[:7]})

    a_commits, b_commits, a_index, b_index = nearest_equivalent_parent(a, b)
    if a_index is None:
        return {"ok": True, "message": with_stash_note(stash, tr("rebase_equivalent_noop", **{"from": a[:7], "to": b[:7]}))}

    matched_a = a_commits[a_index]["hash"]
    matched_b = b_commits[b_index]["hash"]
    branch_commits = set(git("rev-list", "--branches").split())
    if matched_a == matched_b:
        return {"ok": True, "message": with_stash_note(stash, tr("rebase_equivalent_noop", **{"from": a[:7], "to": b[:7]}))}
    # a_commits は先端からの順なので、一致コミットより前が A 側のリベース対象。
    replay = a_commits[:a_index]
    replay_hashes = {c["hash"] for c in replay}
    tag_tips = set()
    for tag in tag_targets():
        tip = tag["commit"]
        if (tip in branch_commits
                or tip == matched_a
                or subprocess.run(["git", "-C", state.REPO, "merge-base", "--is-ancestor", tip, a],
                                  capture_output=True).returncode == 0
                or subprocess.run(["git", "-C", state.REPO, "merge-base", "--is-ancestor", tip, b],
                                  capture_output=True).returncode == 0
                or subprocess.run(["git", "-C", state.REPO, "merge-base", "--is-ancestor", matched_a, tip],
                                  capture_output=True).returncode != 0):
            continue
        path_commits = set(git("rev-list", f"{matched_a}..{tip}").split())
        if path_commits.isdisjoint(replay_hashes):
            tag_tips.add(tip)
    tag_only_mapping = rebase_tag_only_tips(matched_a, matched_b, tag_tips)
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
        moved = move_tags(tag_only_mapping)
        message = with_stash_note(stash, tr("rebase_equivalent_done", **{"from": a[:7], "to": new_tip[:7]}, matched=1))
        return {"ok": True, "message": with_tags_note(message, moved)}

    for commit in replay_hashes:
        parents = git("rev-list", "--parents", "-n", "1", commit).split()[1:]
        if len(parents) > 1:
            raise UserError("rebase_equivalent_merges")

    old_ids = snapshot_for_tags(f"{matched_a}..{a}")  # 載せ替える A 側のコミットに付いたタグの付け替え用
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
    mapping = tag_mapping(old_ids, f"{matched_b}..{new_tip}") if old_ids else {}
    mapping.update(tag_only_mapping)
    moved = move_tags(mapping)
    return {"ok": True, "message": with_tags_note(with_stash_note(stash, tr("rebase_equivalent_done", **{"from": a[:7], "to": new_tip[:7]}, matched=1)), moved)}

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
    競合したときと、変更が空になるとき (既に取り込み済み) は、中止して元に戻す。
    編集中のファイルや同名の管理外ファイルに邪魔されたときは、止めているファイルを返す ({blocked, modified, untracked})。"""
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
    r, blocked, stashed = run_resolving(lambda c: _git_run(["cherry-pick", h], c), body.get("on_conflict"))
    if blocked:
        return blocked
    stash = {"stashed": stashed}
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        if is_cherry_picking():
            unmerged = subprocess.run(["git", "-C", state.REPO, "-c", "core.quotepath=false", "diff", "--name-only", "--diff-filter=U"],
                                      capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
            subprocess.run(["git", "-C", state.REPO, "cherry-pick", "--abort"], capture_output=True)
            if not unmerged:  # 競合ではなく、変更が空になった
                raise UserError("cherry_empty", hash=h[:7])
            raise UserError("cherry_conflict", hash=h[:7])
        raise ValueError(with_stash_note(stash, out or tr("cherry_failed")))
    if git("rev-parse", "HEAD").strip() == before:
        return {"ok": True, "message": with_stash_note(stash, tr("cherry_noop", hash=h[:7]))}
    return {"ok": True, "message": with_stash_note(stash, tr("cherry_done", hash=h[:7]))}

@route("/api/revert", method="POST")
def api_revert(body):
    """選んだコミットの変更を打ち消すコミットを、いまの HEAD の上に作る (git revert)。
    競合したときと、変更が空になるとき (既に打ち消し済み) は、中止して元に戻す。マージコミットは第1親を基準にする。
    編集中のファイルや同名の管理外ファイルに邪魔されたときは、止めているファイルを返す ({blocked, modified, untracked})。"""
    if not state.HAS_WORKTREE:
        raise UserError("revert_no_worktree")
    h = resolve_commit(body.get("hash", ""))
    if is_merging() or is_rebasing() or is_cherry_picking() or is_reverting():
        raise UserError("op_in_progress")
    if not has_head():
        raise UserError("revert_no_commits")
    is_merge = len(git("rev-list", "--parents", "-n", "1", h).split()) > 2
    args = ["revert", "--no-edit"] + (["-m", "1"] if is_merge else []) + [h]
    r, blocked, stashed = run_resolving(lambda c: _git_run(args, c), body.get("on_conflict"))
    if blocked:
        return blocked
    stash = {"stashed": stashed}
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        if is_reverting():
            unmerged = subprocess.run(["git", "-C", state.REPO, "-c", "core.quotepath=false", "diff", "--name-only", "--diff-filter=U"],
                                      capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
            subprocess.run(["git", "-C", state.REPO, "revert", "--abort"], capture_output=True)
            if not unmerged:  # 競合ではなく、変更が空になった
                raise UserError("revert_empty", hash=h[:7])
            raise UserError("revert_conflict", hash=h[:7])
        raise ValueError(with_stash_note(stash, out or tr("revert_failed")))
    return {"ok": True, "message": with_stash_note(stash, tr("revert_done_merge" if is_merge else "revert_done", hash=h[:7]))}

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
    tmp = tmpdir.make_temp_dir("rebase")
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
        # todo の差し替えは python ではなく cp で行う。PyInstaller の exe などでは sys.executable が
        # アプリ自身なので、python のスクリプトのつもりで起動すると、引数がアプリのリポジトリ指定として
        # 解釈されて「seq_editor.py は git リポジトリではありません」になる。git は sh 経由で editor を呼ぶので、
        # Windows でも Git for Windows 付属の cp が使える (git が todo のパスを最後に付ける)。
        env = dict(os.environ, GIT_EDITOR=":", GIT_TERMINAL_PROMPT="0",
                   GIT_SEQUENCE_EDITOR=f'cp "{fwd(todo_path)}"')
        r, blocked, stashed = run_resolving(
            lambda c: _git_run(["rebase", "-i", base], c, env), body.get("on_conflict"), rebase_dirty=True)
        if blocked:
            return blocked
        stash = {"stashed": stashed}
        out = (r.stdout + r.stderr).strip()
        if r.returncode != 0:
            if is_rebasing():  # 競合など → 元に戻す
                subprocess.run(["git", "-C", state.REPO, "rebase", "--abort"], capture_output=True)
                tail = "\n".join(out.splitlines()[-6:])
                raise UserError("ri_aborted", tail=tail)
            raise ValueError(with_stash_note(stash, out or tr("rebase_failed")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if git("rev-parse", "HEAD").strip() == before:
        return {"ok": True, "message": with_stash_note(stash, tr("ri_noop", branch=head["branch"]))}
    n = Counter(a for a, _, _ in todo)
    parts = [tr(key, n=n[k]) for k, key in (("reword", "ri_part_reword"), ("squash", "ri_part_squash"),
                                           ("fixup", "ri_part_fixup"), ("drop", "ri_part_drop")) if n[k]]
    if parts:
        return {"ok": True, "message": with_stash_note(stash, tr("ri_done_parts", branch=head["branch"], base=base[:7], parts=" / ".join(parts)))}
    return {"ok": True, "message": with_stash_note(stash, tr("ri_done", branch=head["branch"], base=base[:7]))}


__all__ = [
    "REBASE_I_ACTIONS", "REBASE_I_MAX", "api_abort", "api_cherry_pick",
    "api_find_equivalent_branch", "api_find_equivalent_parents", "api_head", "api_merge",
    "api_rebase", "api_rebase_descendants", "api_rebase_equivalent", "api_rebase_i",
    "api_rebase_range", "api_rebase_todo", "api_resolve", "api_revert", "check_rebase_i_range",
    "equivalent_commit_prefix", "first_parent_commits", "nearest_equivalent_parent",
    "rebase_todo_hashes", "unmerged_stages",
]
