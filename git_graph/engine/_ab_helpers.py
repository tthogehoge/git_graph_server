# A/B 操作で「このコミットを先端に持つローカルブランチ」を選ぶ共通処理。
import subprocess
from ..gitrun import git
from ..gitstate import head_state
from ..i18n import tr, UserError


def local_branches_at(commit):
    """commit を先端に持つローカルブランチ名のリスト (ソート済み)。"""
    names = []
    for ref in git("for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines():
        tip = git("rev-parse", ref + "^{commit}").strip()
        if tip == commit:
            names.append(ref)
    names.sort()
    return names


def pick_local_branch_at(commit, *, error_key):
    """commit を先端に持つローカルブランチを1つ選ぶ。現在ブランチが候補ならそれを優先。
    無ければ UserError(error_key, hash=短縮ハッシュ)。"""
    names = local_branches_at(commit)
    if not names:
        raise UserError(error_key, hash=commit[:7])
    current = head_state().get("branch") or ""
    if current in names:
        return current
    return names[0]
