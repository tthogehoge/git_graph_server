# 作業ツリー (WIP) の API。
# 読み取り (status / 差分) は git_graph.query.status に、コミットは engine.commit に任せ、
# ここは薄い route と互換の再公開だけを置く。
from .engine import commit
from .gitstate import (  # noqa: F401  (読み取りだけのヘルパーは gitstate に移した。互換のため再公開する)
    addable_paths, amend_base, amend_head_changes, commit_info, is_cherry_picking, is_merging, is_rebasing,
    is_reverting, parse_name_status, parse_numstat, parse_status, read_status_raw, stash_conflict_state,
)
from .i18n import tr
from .query.status import amend_files, status as query_status, wip_count, wip_diff
from .routing import route


@route("/api/status")
def api_status(q):
    """作業ツリーの変更 (WIP)。ステージ済み/未ステージには追加・削除行数も付ける。"""
    return query_status(amend=q.get("amend", [""])[0] == "1")


@route("/api/wip-commit", method="POST", engine=True)
def api_wip_commit(body):
    """チェックされたファイルを add してコミットする (TortoiseGit のコミットダイアログと同じ考え方)。
    ステージ済みでチェックのないファイルは、ステージから外す (作業ツリーの内容はそのまま)。"""
    r = commit.commit(body.get("message", ""), body.get("paths"), staged_paths=body.get("staged_paths"),
                      amend=body.get("amend") is True, branch=body.get("branch", ""))
    if not r["amend"]:
        message = tr("wip_committed", hash=r["hash"][:7], n=r["count"])
    elif not r["count"]:
        message = tr("wip_amended_msg_only", hash=r["hash"][:7])
    elif r["dropped"]:
        message = tr("wip_amended_dropped", hash=r["hash"][:7], n=r["count"], dropped=r["dropped"])
    else:
        message = tr("wip_amended", hash=r["hash"][:7], n=r["count"])
    return {"ok": True, "message": message}


__all__ = [
    "addable_paths", "amend_base", "amend_files", "amend_head_changes", "api_status", "api_wip_commit",
    "parse_name_status", "wip_count", "wip_diff",
]
