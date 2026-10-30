# WIP (作業ツリー) からのコミット。チェックされたファイルを add してコミットし、ステージ済みでチェックのないファイルはステージから外す。
# 引数を受け取って git を実行し、結果を dict で返す。HTTP の body も、画面に出す文言の組み立ても route 側の仕事。
import subprocess
from .. import app_state as state
from ..gitrun import check_ref_name, git, run_git_write
from ..gitstate import (
    addable_paths, amend_base, amend_head_changes, has_head, has_parent, head_state, is_merging, is_rebasing,
    status_lists,
)
from ..i18n import tr, UserError
from . import operation
from .dirty import checkout_resolving

_STEP = 200  # コマンドラインが長くなりすぎないよう、ファイルはこの数ずつ git に渡す


def _switch_branch(branch):
    """コミット先のブランチへ切り替える (無ければ作る)。"""
    check_ref_name("branch", branch)
    if branch == head_state().get("branch"):
        return
    exists = subprocess.run(["git", "-C", state.REPO, "rev-parse", "--verify", "--quiet", "refs/heads/" + branch],
                            capture_output=True).returncode == 0
    if exists:
        res = checkout_resolving([branch], None)
        if res and res.get("blocked"):
            raise ValueError(tr("checkout_failed"))
    elif has_head():
        run_git_write("branch", branch, "HEAD")
        run_git_write("checkout", branch)
    else:
        run_git_write("checkout", "-b", branch)


@operation("commit", touches_worktree="names", detail=lambda *_, branch="", **__: str(branch or "").strip())
def commit(message, paths, *, staged_paths=None, amend=False, branch=""):
    """チェックされたファイル (paths) を add してコミットする。staged_paths は、ステージ済みの内容だけをコミットするファイル
    (未ステージの変更は add しない)。amend=True なら直前のコミットを作り直す (メッセージだけの変更も可)。
    branch があれば、そのブランチ (無ければ作る) へ切り替えてからコミットする。
    戻り値: {"hash", "amend", "count" (コミットに入れたファイル数), "dropped" (amend でコミットから外したファイル数)}。"""
    if not state.WIP_ENABLED:
        raise UserError("wip_disabled")
    branch = str(branch or "").strip()
    if branch and amend:
        raise UserError("amend_blocked")
    if branch:
        _switch_branch(branch)
    if amend:
        if not has_head():
            raise UserError("amend_no_commit")
        if is_merging() or is_rebasing():
            raise UserError("amend_blocked")
    msg = str(message or "").strip()
    if not msg:
        raise UserError("message_required")
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
        raise UserError("select_files")
    if not paths and not staged_paths and not amend:
        raise UserError("select_files")  # amend はメッセージだけの変更 (paths が空) もできる
    staged_only = staged_paths or []  # ステージ済みの内容だけをコミットする (未ステージの変更は add しない)
    if not isinstance(staged_only, list) or not all(isinstance(p, str) for p in staged_only):
        raise UserError("select_files")
    if amend and staged_only:
        raise UserError("amend_blocked")
    keep = set(staged_only) - set(paths)  # paths にもあるものは、作業ツリーの内容をコミットする
    checked = set(paths) | keep  # コミットに入れるファイル
    s = status_lists(limit=10 ** 9)
    known = {f["path"] for k in ("staged", "unstaged", "untracked", "conflicts") for f in s[k]}
    not_staged = keep - {f["path"] for f in s["staged"]}
    if not_staged:
        raise UserError("files_unchanged", files=", ".join(sorted(not_staged)[:3]))
    head_changes = amend_head_changes(amend_base()) if amend else {}  # 直前のコミットが変更したファイル (チェックを外すとコミットから外す)
    if amend and head_changes and not checked:  # index を触る前に確認する (空のコミットは git が拒否する)
        raise UserError("amend_would_empty")
    bad = checked - known - set(head_changes)  # 任意のパスを add させないよう、いま変更ありと報告されているものだけを対象にする
    if bad:
        raise UserError("files_unchanged", files=", ".join(sorted(bad)[:3]))
    unstage = []
    for f in s["staged"]:
        if f["path"] not in checked:
            unstage.append(f["path"])
            if f.get("old_path"):  # リネームは元のパスも一緒に戻す
                unstage.append(f["old_path"])
    if is_merging() and (unstage or any(f["path"] not in checked for f in s["conflicts"])):
        raise UserError("merging_need_all")
    for i in range(0, len(unstage), _STEP):
        part = unstage[i:i + _STEP]
        if has_head():
            run_git_write("reset", "-q", "--", *part, literal=True)
        else:  # 最初のコミット前は HEAD がないので、インデックスから外す
            run_git_write("rm", "--cached", "-r", "-q", "-f", "--", *part, literal=True)
    drop = []  # amend: チェックを外した「直前のコミットのファイル」は、コミットから外す (親の状態に戻す。作業ツリーはそのまま)
    for path, old in head_changes.items():
        if path not in checked:
            drop.append(path)
            if old:
                drop.append(old)
    if drop:
        base = amend_base()
        for i in range(0, len(drop), _STEP):
            part = drop[i:i + _STEP]
            if has_parent():
                run_git_write("reset", "-q", base, "--", *part, literal=True)
            else:  # 最初のコミットには親がないので、インデックスから外す
                run_git_write("rm", "--cached", "-r", "-q", "-f", "--", *part, literal=True)
    # ステージ済みの削除や、直前のコミットで git rm 済みのファイルは add -A できない (パスが見つからず失敗する) が、
    # インデックスにはその状態が入っているのでそのままコミットに入る
    todo = addable_paths(set(paths))
    for i in range(0, len(todo), _STEP):
        run_git_write("add", "-A", "--", *todo[i:i + _STEP], literal=True)
    run_git_write("commit", *(["--amend"] if amend else []), "-m", msg)
    return {"hash": git("rev-parse", "HEAD").strip(), "amend": amend, "count": len(checked),
            "dropped": len([d for d in head_changes if d not in checked])}
