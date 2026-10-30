# マージ・リベース・チェリーピック・リバート・競合の解決・中断の API。
# git の処理は git_graph.engine (integrate / rebase) に任せ、ここは body を読んで engine を呼び、
# 結果を画面用のレスポンスに整える薄い route だけを置く。等価コミットの探索 (読み取りだけ) は equivalent.py。
from .engine import integrate, rebase
from .engine.dirty import with_stash_note
from .engine.integrate import unmerged_stages  # noqa: F401  (互換のため再公開)
from .engine.rebase import (  # noqa: F401  (互換のため再公開)
    REBASE_I_ACTIONS, REBASE_I_MAX, check_rebase_i_range, rebase_tag_only_tips, rebase_todo_hashes,
)
from .engine.tags import commit_patch_ids, move_tags, snapshot_for_tags, tag_mapping, tag_targets  # noqa: F401
from .query.equivalent import (  # noqa: F401
    equivalent_commit_prefix, find_equivalent_branches, find_equivalent_parents, first_parent_commits,
    nearest_equivalent_parent,
)
from .gitstate import head_state
from .i18n import tr
from .routing import route


def with_tags_note(message, moved):
    return f"{message} {tr('rebase_tags_moved', n=moved)}" if moved else message



@route("/api/head")
def api_head(q=None):
    return head_state()

@route("/api/merge", method="POST", engine=True)
def api_merge(body):
    """選んだコミットを、いまチェックアウトしている HEAD にマージする。競合したときはマージ途中の状態で止める。"""
    r = integrate.merge(body.get("hash", ""), no_ff=bool(body.get("no_ff")), on_conflict=body.get("on_conflict"))
    if r.get("blocked"):
        return r
    if r.get("conflict"):
        return {"ok": True, "conflict": True, "message": with_stash_note(r, tr("merge_conflict", target=r["hash"][:7]))}
    return {"ok": True, "message": with_stash_note(r, tr("merge_done" if r["updated"] else "merge_uptodate", hash=r["hash"][:7]))}


@route("/api/rebase", method="POST", engine=True)
def api_rebase(body):
    """現在のブランチを、選んだコミットの上にリベースする。競合したら自動で中止して元に戻す。"""
    r = rebase.rebase(body.get("hash", ""), on_conflict=body.get("on_conflict"))
    if r.get("blocked"):
        return r
    return {"ok": True, "message": with_stash_note(r, tr("rebase_noop" if r["noop"] else "rebase_done", branch=r["branch"], hash=r["hash"][:7]))}


@route("/api/rebase-range", method="POST", engine=True)
def api_rebase_range(body):
    """A より後の現在のブランチのコミットを B の上にリベースする。競合したら自動で中止して元に戻す。"""
    r = rebase.rebase_range(body.get("from", ""), body.get("to", ""), on_conflict=body.get("on_conflict"))
    if r.get("blocked"):
        return r
    if r["noop"]:
        message = tr("rebase_noop", branch=r["branch"], hash=r["to"][:7])
    else:
        message = tr("rebase_range_done", branch=r["branch"], **{"from": r["from"][:7], "to": r["to"][:7]})
    return {"ok": True, "message": with_tags_note(with_stash_note(r, message), r["moved_tags"])}


@route("/api/rebase-descendants", method="POST", engine=True)
def api_rebase_descendants(body):
    """A の子孫にある全ローカルブランチとブランチのないタグ先履歴を、分岐構造を保って B の上へ移す。"""
    r = rebase.rebase_descendants(body.get("from", ""), body.get("to", ""), on_conflict=body.get("on_conflict"))
    if r.get("blocked"):
        return r
    if r["noop"]:
        return {"ok": True, "message": with_stash_note(r, tr("rebase_noop", branch=r["branch"], hash=r["to"][:7]))}
    message = tr("rebase_descendants_done", branches=r["branches"], **{"from": r["from"][:7], "to": r["to"][:7]})
    return {"ok": True, "message": with_tags_note(with_stash_note(r, message), r["moved_tags"])}


@route("/api/find-equivalent-parents", method="POST")
def api_find_equivalent_parents(body):
    """A/B の先端から親へ探し、件名と親相対差分が一致するコミットを返す。"""
    found = find_equivalent_parents(body.get("from", ""), body.get("to", ""))
    if found is None:
        return {"message": tr("find_equivalent_parents_none")}
    return {"from": found["from"], "to": found["to"],
            "message": tr("find_equivalent_parents_found", a=found["from"][:7], b=found["to"][:7])}


@route("/api/rebase-equivalent", method="POST", engine=True)
def api_rebase_equivalent(body):
    """A/B の先端から親へ同一差分を探し、A の一致コミット以降を B 側へ寄せる。"""
    r = rebase.rebase_equivalent(body.get("from", ""), body.get("to", ""), on_conflict=body.get("on_conflict"))
    if r.get("blocked"):
        return r
    if r["noop"]:
        return {"ok": True, "message": with_stash_note(r, tr("rebase_equivalent_noop", **{"from": r["from"][:7], "to": r["to"][:7]}))}
    message = tr("rebase_equivalent_done", **{"from": r["from"][:7], "to": r["to"][:7]}, matched=1)
    return {"ok": True, "message": with_tags_note(with_stash_note(r, message), r["moved_tags"])}


@route("/api/find-equivalent-branch", method="POST")
def api_find_equivalent_branch(body):
    """B の子孫ブランチから、A と最も近い連続同一差分の履歴を探す。"""
    a, b = body.get("from", ""), body.get("to", "")
    candidates = find_equivalent_branches(a, b)
    if not candidates:
        return {"message": tr("find_equivalent_branch_none", **{"from": a[:7], "to": b[:7]})}
    nearest = min(c["remaining"] for c in candidates)
    best = [c for c in candidates if c["remaining"] == nearest]
    best.sort(key=lambda c: (-c["matched"], c["branch"]))
    labels = ", ".join(tr("find_equivalent_branch_candidate", branch=c["branch"],
                          commit=c["commit"][:7], matched=c["matched"]) for c in best)
    return {"to": best[0]["tip"],
            "message": tr("find_equivalent_branch_found", branches=labels, remaining=nearest)}


@route("/api/cherry-pick", method="POST", engine=True)
def api_cherry_pick(body):
    """選んだコミットを、いまチェックアウトしている HEAD にチェリーピックする。"""
    r = integrate.cherry_pick(body.get("hash", ""), on_conflict=body.get("on_conflict"))
    if r.get("blocked"):
        return r
    return {"ok": True, "message": with_stash_note(r, tr("cherry_noop" if r["noop"] else "cherry_done", hash=r["hash"][:7]))}


@route("/api/revert", method="POST", engine=True)
def api_revert(body):
    """選んだコミットの変更を打ち消すコミットを、いまの HEAD の上に作る (git revert)。"""
    r = integrate.revert(body.get("hash", ""), on_conflict=body.get("on_conflict"))
    if r.get("blocked"):
        return r
    return {"ok": True, "message": with_stash_note(r, tr("revert_done_merge" if r["merge"] else "revert_done", hash=r["hash"][:7]))}


@route("/api/abort", method="POST", engine=True)
def api_abort(body):
    """進行中のマージ / リベース / チェリーピック / リバートを中断して、開始前の状態に戻す。"""
    r = integrate.abort_operation()
    return {"ok": True, "message": tr("aborted_stash") if r["cmd"] == "stash" else tr("aborted", cmd=r["cmd"])}


@route("/api/resolve", method="POST", engine=True)
def api_resolve(body):
    """競合しているファイルを、ours か theirs の内容で丸ごと置き換えて解決済みにする。"""
    r = integrate.resolve_conflicts(body.get("side"), body.get("paths"))
    return {"ok": True, "message": tr("resolved", n=r["count"], side=r["side"])}


@route("/api/rebase-todo")
def api_rebase_todo(q):
    """対話的リベースの対象コミット一覧 (画面の編集ダイアログに出す)。"""
    return rebase.rebase_todo((q.get("hash") or [""])[0])


@route("/api/rebase-i", method="POST", engine=True)
def api_rebase_i(body):
    """git rebase -i 相当。画面で編集した todo (順序と pick/reword/squash/fixup/drop) をそのまま実行する。"""
    r = rebase.rebase_interactive(body.get("hash", ""), body.get("todo"), on_conflict=body.get("on_conflict"))
    if r.get("blocked"):
        return r
    if r["noop"]:
        return {"ok": True, "message": with_stash_note(r, tr("ri_noop", branch=r["branch"]))}
    n = r["counts"]
    parts = [tr(key, n=n[k]) for k, key in (("reword", "ri_part_reword"), ("squash", "ri_part_squash"),
                                           ("fixup", "ri_part_fixup"), ("drop", "ri_part_drop")) if n.get(k)]
    if parts:
        return {"ok": True, "message": with_stash_note(r, tr("ri_done_parts", branch=r["branch"], base=r["base"][:7], parts=" / ".join(parts)))}
    return {"ok": True, "message": with_stash_note(r, tr("ri_done", branch=r["branch"], base=r["base"][:7]))}


__all__ = [
    "REBASE_I_ACTIONS", "REBASE_I_MAX", "api_abort", "api_cherry_pick",
    "api_find_equivalent_branch", "api_find_equivalent_parents", "api_head", "api_merge",
    "api_rebase", "api_rebase_descendants", "api_rebase_equivalent", "api_rebase_i",
    "api_rebase_range", "api_rebase_todo", "api_resolve", "api_revert", "check_rebase_i_range",
    "equivalent_commit_prefix", "first_parent_commits", "nearest_equivalent_parent",
    "rebase_todo_hashes", "unmerged_stages",
]
