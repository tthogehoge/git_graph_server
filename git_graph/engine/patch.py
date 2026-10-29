# パッチの適用 (git apply / git am)。パッチの中身 (bytes) を受け取って git を実行し、結果を dict で返す。
# リクエストの base64 を bytes に戻すことや、画面に出す文言の組み立ては route 側の仕事。
import subprocess
from .. import app_state as state
from ..gitrun import git
from ..gitstate import has_head, is_cherry_picking, is_merging, is_rebasing, is_reverting
from ..i18n import tr, UserError
from . import operation


@operation("patch_apply", touches_worktree="names")
def apply_patch(data):
    """パッチを作業ツリーに git apply する (ステージ・コミットはしない)。先に解析と適用の確認をして、失敗したら何も変えない。
    戻り値: {"files": パッチが変更するファイルの数}。"""
    if not state.WIP_ENABLED:
        raise UserError("wip_disabled")
    if not data.strip():
        raise UserError("patch_empty")

    def run_apply(*opts):
        r = subprocess.run(["git", "-C", state.REPO, "apply", "--whitespace=nowarn", *opts, "-"], input=data, capture_output=True)
        if r.returncode != 0:
            raise ValueError(r.stderr.decode("utf-8", "replace").strip() or tr("git_command_failed"))
        return r.stdout.decode("utf-8", "replace")

    n = len([line for line in run_apply("--numstat").splitlines() if line.strip()])  # 壊れたパッチはここで失敗する
    if n == 0:
        raise UserError("patch_empty")
    run_apply("--check")
    run_apply()
    return {"files": n}


@operation("patch_am", touches_worktree=True)
def am_patch(data):
    """パッチ (git format-patch の出力) を git am で適用し、パッチのコミットを現在のブランチに積む。
    途中で失敗したら git am --abort で元の状態に戻す。マージなどの途中のときは、その操作を壊さないよう何もしない。
    戻り値: {"commits": 積んだコミットの数}。"""
    if not state.WIP_ENABLED:
        raise UserError("wip_disabled")
    if not data.strip():
        raise UserError("patch_empty")
    if is_merging() or is_rebasing() or is_cherry_picking() or is_reverting():
        raise UserError("patch_am_busy")  # rebase-apply が残っていると、am --abort が別の操作を中断してしまう
    old = git("rev-parse", "HEAD").strip() if has_head() else ""
    r = subprocess.run(["git", "-C", state.REPO, "am", "--quiet", "--whitespace=nowarn"], input=data, capture_output=True)
    if r.returncode != 0:
        subprocess.run(["git", "-C", state.REPO, "am", "--abort"], capture_output=True)
        text = (r.stderr + r.stdout).decode("utf-8", "replace").strip()
        raise ValueError(text or tr("git_command_failed"))
    return {"commits": int(git("rev-list", "--count", f"{old}..HEAD" if old else "HEAD").strip() or 0)}
