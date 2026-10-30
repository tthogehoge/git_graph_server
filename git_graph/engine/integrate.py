# 他のコミットの取り込み (merge / cherry-pick / revert) と、競合の解決・途中の操作の中断 (resolve / abort)。
# 引数を受け取って git を実行し、結果を dict で返す。HTTP の body も、画面に出す文言の組み立ても route 側の仕事。
# 編集中のファイルに邪魔されて止まったときは {"blocked": True, ...} を返し、route がそのまま画面へ返す (ユーザーが選んだ方法で再度呼ぶ)。
import os
import subprocess
from .. import app_state as state
from ..gitrun import git, resolve_commit, run_git_write
from ..gitstate import (
    has_head, is_cherry_picking, is_merging, is_rebasing, is_reverting, paths_in_head, stash_conflict_state,
    status_lists,
)
from ..i18n import tr, UserError
from . import operation
from .dirty import git_run, run_resolving, with_stash_note
from .worktree import clear_stash_marker

_STEP = 200  # コマンドラインが長くなりすぎないよう、ファイルはこの数ずつ git に渡す


def _unmerged_files():
    return subprocess.run(["git", "-C", state.REPO, "-c", "core.quotepath=false", "diff", "--name-only", "--diff-filter=U"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()


def _is_merge_commit(h):
    return len(git("rev-list", "--parents", "-n", "1", h).split()) > 2


@operation("merge", touches_worktree=True, detail=lambda commit, *_, **__: commit)
def merge(commit, *, no_ff=False, on_conflict=None):
    """選んだコミットを、いまチェックアウトしている HEAD にマージする。
    競合したときはマージ途中の状態で止める (WIP の一覧に競合ファイルが出るので、解決してコミットする)。
    編集中のファイルや同名の管理外ファイルに邪魔されてマージできないときは、止めているファイルを返す ({blocked, modified, untracked})。
    on_conflict="stash" で再実行すると全変更を stash してから、"discard" で再実行すると止めているファイルだけ破棄してからマージする。
    戻り値: {"hash", "updated", "stashed"} / 競合 {"conflict": True, "hash", "stashed"} / blocked。"""
    if not state.HAS_WORKTREE:
        raise UserError("merge_no_worktree")
    h = resolve_commit(commit)
    if is_merging():
        raise UserError("merge_in_progress")
    if not has_head():
        raise UserError("merge_no_commits")
    before = git("rev-parse", "HEAD").strip()
    args = ["merge", "--no-edit"] + (["--no-ff"] if no_ff else []) + [h]
    r, blocked, stashed = run_resolving(lambda c_locale: git_run(args, c_locale), on_conflict)
    if blocked:
        return blocked
    if r.returncode != 0:
        if is_merging():  # 競合 (自動マージできなかった)
            return {"conflict": True, "hash": h, "stashed": stashed}
        raise ValueError(with_stash_note({"stashed": stashed}, (r.stdout + r.stderr).strip() or tr("merge_failed")))
    # git の出力文言 (ロケールで変わる) ではなく、HEAD が動いたかで判定する
    return {"hash": h, "updated": git("rev-parse", "HEAD").strip() != before, "stashed": stashed}


@operation("cherry_pick", touches_worktree=True, detail=lambda commit, *_, **__: commit)
def cherry_pick(commit, *, on_conflict=None):
    """選んだコミットを、いまチェックアウトしている HEAD にチェリーピックする。
    競合したときと、変更が空になるとき (既に取り込み済み) は、中止して元に戻す。
    編集中のファイルや同名の管理外ファイルに邪魔されたときは、止めているファイルを返す ({blocked, modified, untracked})。
    戻り値: {"hash", "noop" (HEAD が動かなかった), "stashed"} / blocked。"""
    if not state.HAS_WORKTREE:
        raise UserError("cherry_no_worktree")
    h = resolve_commit(commit)
    if is_merging() or is_rebasing() or is_cherry_picking() or is_reverting():
        raise UserError("op_in_progress")
    if not has_head():
        raise UserError("cherry_no_commits")
    if _is_merge_commit(h):
        raise UserError("cherry_merge_commit")
    before = git("rev-parse", "HEAD").strip()
    r, blocked, stashed = run_resolving(lambda c: git_run(["cherry-pick", h], c), on_conflict)
    if blocked:
        return blocked
    if r.returncode != 0:
        if is_cherry_picking():
            unmerged = _unmerged_files()
            subprocess.run(["git", "-C", state.REPO, "cherry-pick", "--abort"], capture_output=True)
            if not unmerged:  # 競合ではなく、変更が空になった
                raise UserError("cherry_empty", hash=h[:7])
            raise UserError("cherry_conflict", hash=h[:7])
        raise ValueError(with_stash_note({"stashed": stashed}, (r.stdout + r.stderr).strip() or tr("cherry_failed")))
    return {"hash": h, "noop": git("rev-parse", "HEAD").strip() == before, "stashed": stashed}


@operation("revert", touches_worktree=True, detail=lambda commit, *_, **__: commit)
def revert(commit, *, on_conflict=None):
    """選んだコミットの変更を打ち消すコミットを、いまの HEAD の上に作る (git revert)。
    競合したときと、変更が空になるとき (既に打ち消し済み) は、中止して元に戻す。マージコミットは第1親を基準にする。
    編集中のファイルや同名の管理外ファイルに邪魔されたときは、止めているファイルを返す ({blocked, modified, untracked})。
    戻り値: {"hash", "merge" (マージコミットを打ち消した), "stashed"} / blocked。"""
    if not state.HAS_WORKTREE:
        raise UserError("revert_no_worktree")
    h = resolve_commit(commit)
    if is_merging() or is_rebasing() or is_cherry_picking() or is_reverting():
        raise UserError("op_in_progress")
    if not has_head():
        raise UserError("revert_no_commits")
    is_merge = _is_merge_commit(h)
    args = ["revert", "--no-edit"] + (["-m", "1"] if is_merge else []) + [h]
    r, blocked, stashed = run_resolving(lambda c: git_run(args, c), on_conflict)
    if blocked:
        return blocked
    if r.returncode != 0:
        if is_reverting():
            unmerged = _unmerged_files()
            subprocess.run(["git", "-C", state.REPO, "revert", "--abort"], capture_output=True)
            if not unmerged:  # 競合ではなく、変更が空になった
                raise UserError("revert_empty", hash=h[:7])
            raise UserError("revert_conflict", hash=h[:7])
        raise ValueError(with_stash_note({"stashed": stashed}, (r.stdout + r.stderr).strip() or tr("revert_failed")))
    return {"hash": h, "merge": is_merge, "stashed": stashed}


@operation("abort", touches_worktree=True)
def abort_operation():
    """進行中のマージ / リベース / チェリーピック / リバートを中断して、開始前の状態に戻す。戻り値: {"cmd": 中断した操作}。"""
    if not state.HAS_WORKTREE:
        raise UserError("abort_no_worktree")
    for cmd, active in (("merge", is_merging), ("rebase", is_rebasing),
                        ("cherry-pick", is_cherry_picking), ("revert", is_reverting)):
        if active():
            run_git_write(cmd, "--abort")
            return {"cmd": cmd}
    marker = stash_conflict_state()
    if marker:  # stash の apply / pop が競合したまま (git には中断の仕組みがない)
        _abort_stash_apply(marker["stash"])
        return {"cmd": "stash"}
    raise UserError("nothing_to_abort")


def _abort_stash_apply(stash):
    """stash の適用で変わったファイルを、適用前の状態 (HEAD) に戻す。
    stash の適用は、変更先のファイルがきれいなとき (HEAD と同じとき) だけ始まる (編集中なら適用前に止まる) ので、
    stash が触るファイルを HEAD の内容に戻せば、適用前の状態に戻る。stash 自体は残る (pop が競合したときも消えていない)。"""
    names = set()
    for rev in (f"{stash}^1", f"{stash}"):
        if subprocess.run(["git", "-C", state.REPO, "rev-parse", "-q", "--verify", rev + "^{commit}"], capture_output=True).returncode != 0:
            raise UserError("stash_not_found")
    names.update(p for p in git("diff", "--name-only", "-z", "--no-renames", f"{stash}^1", stash).split("\0") if p)
    if subprocess.run(["git", "-C", state.REPO, "rev-parse", "-q", "--verify", f"{stash}^3"], capture_output=True).returncode == 0:
        names.update(p for p in git("ls-tree", "-r", "--name-only", "-z", f"{stash}^3").split("\0") if p)  # 未追跡ファイルの分
    paths = sorted(names)
    in_head = paths_in_head(paths)
    restore = [p for p in paths if p in in_head]
    remove = [p for p in paths if p not in in_head]  # stash が新しく作ったファイル
    for i in range(0, len(restore), _STEP):
        run_git_write("checkout", "HEAD", "--", *restore[i:i + _STEP], literal=True)
    for i in range(0, len(remove), _STEP):
        part = remove[i:i + _STEP]
        subprocess.run(["git", "-C", state.REPO, "rm", "-q", "-f", "--ignore-unmatch", "--", *part], capture_output=True)
        for p in part:  # 未追跡のまま作られたファイルは git rm では消えない
            full = os.path.join(state.REPO, p)
            if os.path.isfile(full):
                os.remove(full)
    clear_stash_marker()


def unmerged_stages(path):
    """競合しているファイルが持つインデックスのステージ番号 (2 = ours, 3 = theirs)。片側で削除されていると、その番号がない。"""
    out = git("ls-files", "-u", "-z", "--", path, literal=True)
    return {rec.split("\t", 1)[0].split()[-1] for rec in out.split("\0") if "\t" in rec}


@operation("resolve", touches_worktree="names")
def resolve_conflicts(side, paths):
    """競合しているファイルを、ours か theirs の内容で丸ごと置き換えて解決済みにする (片側で削除されていたら削除する)。
    戻り値: {"count", "side"}。"""
    if not state.WIP_ENABLED:
        raise UserError("wip_disabled")
    if side not in ("ours", "theirs"):
        raise UserError("resolve_side_invalid")
    if not isinstance(paths, list) or not paths or not all(isinstance(p, str) for p in paths):
        raise UserError("select_files")
    s = status_lists(limit=10 ** 9)
    bad = set(paths) - {f["path"] for f in s["conflicts"]}  # 任意のパスを触らせない。いま競合と報告されているものだけ
    if bad:
        raise UserError("resolve_not_conflict", files=", ".join(sorted(bad)[:3]))
    stage = "2" if side == "ours" else "3"
    keep, remove = [], []
    for p in sorted(set(paths)):
        (keep if stage in unmerged_stages(p) else remove).append(p)
    for i in range(0, len(keep), _STEP):
        part = keep[i:i + _STEP]
        run_git_write("checkout", "--" + side, "--", *part, literal=True)
        run_git_write("add", "--", *part, literal=True)
    for i in range(0, len(remove), _STEP):
        run_git_write("rm", "-q", "-f", "--", *remove[i:i + _STEP], literal=True)
    return {"count": len(keep) + len(remove), "side": side}
