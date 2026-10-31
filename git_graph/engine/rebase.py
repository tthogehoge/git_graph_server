# リベース (rebase / rebase-range / rebase-descendants / rebase-equivalent)。
# 引数を受け取って git を実行し、結果を dict で返す。HTTP の body も、画面に出す文言の組み立ても route 側の仕事。
# 競合したときは自動で中止して元に戻す (UserError)。編集中のファイルに邪魔されたときは {"blocked": True, ...} を返す。
# 戻り値の moved_tags は、リベースで作り直されたコミットに付いていて、新しいコミットへ付け替えたタグの数。
import os
import re
import shutil
import subprocess
import threading
from collections import Counter, defaultdict
from .. import app_state as state
from .. import tmpdir
from ..query.equivalent import nearest_equivalent_parent
from ..gitrun import git, resolve_commit, run_git_write
from ..gitstate import has_head, head_state, is_merging, is_rebasing
from ..i18n import tr, UserError
from . import operation
from .dirty import git_run, resolve_dirty_worktree, run_resolving, with_stash_note
from .tags import move_tags, snapshot_for_tags, tag_mapping, tag_targets


def _check_can_rebase():
    """作業ツリーがあるか確かめる (リベースは作業ツリーを書き換える)。"""
    if not state.HAS_WORKTREE:
        raise UserError("rebase_no_worktree")


def _current_branch():
    if is_merging() or is_rebasing():
        raise UserError("merge_or_rebase_in_progress")
    branch = head_state()["branch"]
    if not branch:
        raise UserError("detached_rebase")
    return branch


def _abort_rebase():
    subprocess.run(["git", "-C", state.REPO, "rebase", "--abort"], capture_output=True)


def _unused_temp_branch(prefix):
    temp = f"{prefix}-{os.getpid()}-{threading.get_ident()}"
    while subprocess.run(["git", "-C", state.REPO, "show-ref", "--verify", "--quiet", f"refs/heads/{temp}"]).returncode == 0:
        temp += "-x"
    return temp


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
    original_branch = head_state()["branch"]
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


def _replayed_revs(a):
    """A より後のコミット (HEAD と、A を祖先に持つ全ブランチ・タグの先端まで) を選ぶ git log の引数。付け替えの対象になりうる範囲。"""
    return ["--ancestry-path", f"^{a}", "HEAD", "--branches", "--tags"]


@operation("rebase", touches_worktree=True, detail=lambda commit, *_, **__: commit)
def rebase(commit, *, on_conflict=None):
    """現在のブランチを、選んだコミットの上にリベースする。競合したら自動で中止して元に戻す。
    編集中のファイルに邪魔されて実行できないときは、止めているファイルを返す ({blocked, modified, untracked})。
    on_conflict="stash" で再実行すると全変更を stash してから、"discard" で再実行すると止めているファイルだけ破棄してからリベースする。
    戻り値: {"branch", "hash", "noop" (HEAD が動かなかった), "stashed"} / blocked。"""
    _check_can_rebase()
    h = resolve_commit(commit)
    branch = _current_branch()
    before = git("rev-parse", "HEAD").strip()
    r, blocked, stashed = run_resolving(lambda c: git_run(["rebase", h], c), on_conflict, rebase_dirty=True)
    if blocked:
        return blocked
    if r.returncode != 0:
        if is_rebasing():  # 競合 → 元に戻す
            _abort_rebase()
            raise UserError("rebase_conflict", target=h[:7], cmd="git rebase")
        raise ValueError(with_stash_note({"stashed": stashed}, (r.stdout + r.stderr).strip() or tr("rebase_failed")))
    return {"branch": branch, "hash": h, "noop": git("rev-parse", "HEAD").strip() == before, "stashed": stashed}


@operation("rebase", touches_worktree=True)
def rebase_range(a, b, *, on_conflict=None):
    """A より後の現在のブランチのコミットを B の上にリベースする。
    競合したら自動で中止して元に戻す。ブランチから到達できないタグ先の履歴と、A..HEAD のコミットに付いたタグも追従させる。
    編集中のファイルに邪魔されて実行できないときは、止めているファイルを返す ({blocked, modified, untracked})。
    戻り値: {"branch", "from", "to", "noop", "stashed", "moved_tags"} / blocked。"""
    _check_can_rebase()
    a = resolve_commit(a)
    b = resolve_commit(b)
    branch = _current_branch()
    blocked, stashed = resolve_dirty_worktree(on_conflict, onto=b, revs=_replayed_revs(a))
    if blocked:
        return blocked
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
    r = git_run(["rebase", "--onto", b, a])
    if r.returncode != 0:
        if is_rebasing():
            _abort_rebase()
            raise UserError("rebase_conflict", target=b[:7], cmd="git rebase --onto")
        raise ValueError(with_stash_note({"stashed": stashed}, (r.stdout + r.stderr).strip() or tr("rebase_failed")))
    mapping = tag_mapping(old_ids, f"{b}..HEAD") if old_ids else {}
    mapping.update(tag_mapping_only)
    moved = move_tags(mapping)
    return {"branch": branch, "from": a, "to": b, "noop": git("rev-parse", "HEAD").strip() == before,
            "stashed": stashed, "moved_tags": moved}


@operation("rebase", touches_worktree=True)
def rebase_descendants(a, b, *, on_conflict=None):
    """A の子孫にある全ローカルブランチとブランチのないタグ先履歴を、分岐構造を保って B の上へ移す。

    A / 分岐点 / ブランチ先端を境界にして線形区間を近い順に処理する。
    各区間は一時ブランチ上で rebase --onto し、全区間が成功してから
    ローカルブランチの参照を一括更新するため、途中失敗で履歴が半端に
    書き換わらないようにする。
    戻り値: {"branch", "from", "to", "noop" (A == B), "branches" (移したブランチの数), "stashed", "moved_tags"} / blocked。
    """
    _check_can_rebase()
    a = resolve_commit(a)
    b = resolve_commit(b)
    branch = _current_branch()
    blocked, stashed = resolve_dirty_worktree(on_conflict, onto=b, revs=_replayed_revs(a))
    if blocked:
        return blocked
    if a == b:
        return {"branch": branch, "from": a, "to": b, "noop": True, "branches": 0, "stashed": stashed, "moved_tags": 0}

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

    original_branch = branch
    temp = _unused_temp_branch("git-graph-rebase")
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
                        _abort_rebase()
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
            _abort_rebase()
        if temp_exists:
            subprocess.run(["git", "-C", state.REPO, "checkout", original_branch], capture_output=True)
            subprocess.run(["git", "-C", state.REPO, "branch", "-D", temp], capture_output=True)
        raise
    changed = len([1 for _, old_tip, new_tip in updates if old_tip != new_tip])
    moved = move_tags({old: new for old, new in mapping.items() if old != a})  # A は土台なので、A に付いたタグは動かさない
    return {"branch": branch, "from": a, "to": b, "noop": False, "branches": changed, "stashed": stashed, "moved_tags": moved}


@operation("rebase", touches_worktree=True)
def rebase_equivalent(a, b, *, on_conflict=None):
    """A/B の先端から親へ同一差分を探し、A の一致コミット以降を B 側へ寄せる。
    戻り値: {"from", "to" (新しい先端), "noop" (寄せるものがない), "stashed", "moved_tags"} / blocked。"""
    _check_can_rebase()
    a = resolve_commit(a)
    b = resolve_commit(b)
    branch = _current_branch()
    blocked, stashed = resolve_dirty_worktree(on_conflict, onto=b, revs=[f"^{b}", a])  # B 側に無い A のコミットが付け替え対象
    if blocked:
        return blocked
    nothing = {"from": a, "to": b, "noop": True, "stashed": stashed, "moved_tags": 0}
    if a == b:
        return nothing

    target_branches = []
    for ref in git("for-each-ref", "--format=%(refname)", "refs/heads").splitlines():
        tip = git("rev-parse", ref + "^{commit}").strip()
        if tip == a:
            target_branches.append((ref, tip))
    if not target_branches:
        raise UserError("rebase_equivalent_head", **{"from": a[:7]})

    a_commits, b_commits, a_index, b_index = nearest_equivalent_parent(a, b)
    if a_index is None:
        return nothing

    matched_a = a_commits[a_index]["hash"]
    matched_b = b_commits[b_index]["hash"]
    branch_commits = set(git("rev-list", "--branches").split())
    if matched_a == matched_b:
        return nothing
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
            run_git_write("checkout", branch)
        except Exception:
            subprocess.run(["git", "-C", state.REPO, "checkout", branch], capture_output=True)
            raise
        moved = move_tags(tag_only_mapping)
        return {"from": a, "to": new_tip, "noop": False, "stashed": stashed, "moved_tags": moved}

    for commit in replay_hashes:
        parents = git("rev-list", "--parents", "-n", "1", commit).split()[1:]
        if len(parents) > 1:
            raise UserError("rebase_equivalent_merges")

    old_ids = snapshot_for_tags(f"{matched_a}..{a}")  # 載せ替える A 側のコミットに付いたタグの付け替え用
    temp = _unused_temp_branch("git-graph-rebase-equivalent")
    try:
        run_git_write("checkout", "--detach", a)
        run_git_write("checkout", "-b", temp, a)
        r = subprocess.run(["git", "-C", state.REPO, "rebase", "--onto", matched_b, matched_a],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            if is_rebasing():
                _abort_rebase()
                raise UserError("rebase_conflict", target=matched_b[:7], cmd="git rebase --onto")
            raise ValueError((r.stdout + r.stderr).strip() or tr("rebase_failed"))
        new_tip = git("rev-parse", "HEAD").strip()
        for ref, old_tip in target_branches:
            run_git_write("update-ref", ref, new_tip, old_tip)
        run_git_write("checkout", branch)
        run_git_write("branch", "-D", temp)
    except Exception:
        if is_rebasing():
            _abort_rebase()
        subprocess.run(["git", "-C", state.REPO, "checkout", branch], capture_output=True)
        subprocess.run(["git", "-C", state.REPO, "branch", "-D", temp], capture_output=True)
        raise
    mapping = tag_mapping(old_ids, f"{matched_b}..{new_tip}") if old_ids else {}
    mapping.update(tag_only_mapping)
    moved = move_tags(mapping)
    return {"from": a, "to": new_tip, "noop": False, "stashed": stashed, "moved_tags": moved}


# ---- 対話的リベース (git rebase -i 相当) ----
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


def rebase_todo(base):
    """対話的リベースの対象コミット一覧 (画面の編集ダイアログに出す)。読み取りだけ。
    戻り値: {"base", "branch", "commits": [{hash, short, author, date, subject, message}]}。"""
    if not state.HAS_WORKTREE:
        raise UserError("rebase_no_worktree")
    base = resolve_commit(base)
    branch = head_state()["branch"]
    if not branch:
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
    return {"base": base, "branch": branch, "commits": commits}


@operation("rebase_i", touches_worktree=True, detail=lambda base, *_, **__: base if isinstance(base, str) else "")
def rebase_interactive(base, todo, *, on_conflict=None):
    """git rebase -i 相当。画面で編集した todo (順序と pick/reword/squash/fixup/drop) をそのまま実行する。
    エディタは起こさず、todo は GIT_SEQUENCE_EDITOR で差し替える。reword は、そのコミット (squash/fixup を
    まとめた後) に exec で commit --amend -F を挟んで新しいメッセージにする。
    競合やエラーで止まったときは自動で rebase --abort して元に戻す。
    todo は [{"action", "hash" (40桁), "message" (reword のとき必須)}] を実行する順に並べたもの。
    戻り値: {"branch", "base", "noop", "counts" (操作ごとの数), "stashed"} / blocked。"""
    _check_can_rebase()
    base = resolve_commit(base)
    branch = _current_branch()
    if not has_head():
        raise UserError("rebase_no_commits")
    if not isinstance(todo, list) or not todo or not all(isinstance(i, dict) for i in todo):
        raise UserError("todo_invalid")
    items = []
    for it in todo:
        act, h = it.get("action"), str(it.get("hash", ""))
        if act not in REBASE_I_ACTIONS or not re.fullmatch(r"[0-9a-f]{40}", h):
            raise UserError("todo_invalid")
        msg = str(it.get("message") or "").strip()
        if act == "reword" and not msg:
            raise UserError("ri_reword_msg_required", hash=h[:7])
        items.append((act, h, msg))
    expected = check_rebase_i_range(base)
    if sorted(h for _, h, _ in items) != sorted(expected):
        raise UserError("ri_changed")
    kept = [t for t in items if t[0] != "drop"]
    if kept and kept[0][0] in ("squash", "fixup"):
        raise UserError("ri_first_squash")

    def fwd(path):  # sh から使うので区切りは / にそろえる (Windows 対策)
        return path.replace("\\", "/")

    before = git("rev-parse", "HEAD").strip()
    tmp = tmpdir.make_temp_dir("rebase")
    try:
        lines, pending = [], None  # pending: 直前のグループの reword 用 exec 行
        for act, h, msg in items:
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
            lambda c: git_run(["rebase", "-i", base], c, env), on_conflict, rebase_dirty=True)
        if blocked:
            return blocked
        out = (r.stdout + r.stderr).strip()
        if r.returncode != 0:
            if is_rebasing():  # 競合など → 元に戻す
                _abort_rebase()
                raise UserError("ri_aborted", tail="\n".join(out.splitlines()[-6:]))
            raise ValueError(with_stash_note({"stashed": stashed}, out or tr("rebase_failed")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    counts = Counter(act for act, _, _ in items)
    return {"branch": branch, "base": base, "noop": git("rev-parse", "HEAD").strip() == before,
            "counts": dict(counts), "stashed": stashed}
