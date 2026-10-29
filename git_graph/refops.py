# 参照 (ブランチ・タグ)・checkout・reset・stash・変更の破棄・git rm の API。
# git の処理は git_graph.engine (refs / worktree / dirty) に任せ、ここは body を読んで engine を呼び、
# 結果を画面用のレスポンス ({"ok", "message"} など) に整える薄い route だけを置く。
from . import app_state as state  # noqa: F401  (互換のため)
from .engine.dirty import (  # noqa: F401  (他の route や git_graph_server.py から使われる名前)
    ALREADY_EXISTS_RE, DIRTY_RE, checkout_resolving, dirty_tracked_files, dirty_worktree_blockers, discard_blockers,
    parse_checkout_blockers, resolve_dirty_worktree, run_resolving, stash_all_changes, stash_push, stash_top,
    with_stash_note,
)
from .engine import refs, worktree
from .i18n import tr, UserError
from .routing import route


def _blocked(result):
    """止まった結果 (編集中のファイルに邪魔された、など) は、そのまま画面に返してユーザーに選ばせる。"""
    return result if result.get("blocked") else None


@route("/api/stash", method="POST", engine=True)
def api_stash(body):
    r = worktree.stash_apply(body.get("hash", ""), body.get("op"), on_conflict=body.get("on_conflict"))
    if _blocked(r):
        return r
    key = {"drop": "stash_dropped", "apply": "stash_applied", "pop": "stash_popped"}[r["action"]]
    return {"ok": True, "message": tr(key, sel=r["sel"], hash=r["hash"][:7])}


@route("/api/stash-push", method="POST", engine=True)
def api_stash_push(body):
    r = worktree.stash_save(message=body.get("message"), paths=body.get("paths"), untracked=body.get("untracked") is not False)
    return {"ok": True, "message": tr("stash_pushed", sel=r["sel"])}


@route("/api/branch", method="POST", engine=True)
def api_branch(body):
    r = refs.create_branch(body.get("hash", ""), body.get("name", ""), checkout=bool(body.get("checkout")),
                           upstream=body.get("upstream"), on_conflict=body.get("on_conflict"))
    if _blocked(r):
        return r
    if r["checked_out"]:
        return {"ok": True, "message": with_stash_note(r, tr("branch_created_checkout", name=r["name"]))}
    return {"ok": True, "message": tr("branch_created", name=r["name"])}


@route("/api/tag", method="POST", engine=True)
def api_tag(body):
    r = refs.create_tag(body.get("hash", ""), body.get("name", ""), body.get("message", ""))
    return {"ok": True, "message": tr("tag_created_annotated" if r["annotated"] else "tag_created", name=r["name"])}


@route("/api/ref-delete", method="POST", engine=True)
def api_delete_ref(body):
    kind = body.get("kind")
    if kind == "remote-branch":  # リモート側が変わって戻せないので、履歴に記録しない操作
        r = refs.delete_remote_branch(body.get("name", ""), auth=body.get("auth"), auth_remote=body.get("auth_remote"),
                                      auth_retry=bool(body.get("auth_retry")))
        if r.get("auth_required"):
            return r
        return {"ok": True, "message": tr("remote_branch_deleted", name=r["name"])}
    r = refs.delete_ref(kind, body.get("name", ""), force=body.get("force") is True)
    if r.get("needs_force"):
        return r
    return {"ok": True, "message": tr("branch_deleted" if r["kind"] == "branch" else "tag_deleted", name=r["name"])}


@route("/api/checkout", method="POST", engine=True)
def api_checkout(body):
    branch = body.get("branch")
    r = refs.checkout(branch if branch else body.get("hash", ""), branch=bool(branch), on_conflict=body.get("on_conflict"))
    if _blocked(r):
        return r
    if "branch" in r:
        return {"ok": True, "message": with_stash_note(r, tr("checkout_branch_done", branch=r["branch"]))}
    return {"ok": True, "message": with_stash_note(r, tr("checkout_detached_done", hash=r["hash"][:7]))}


@route("/api/reset", method="POST", engine=True)
def api_reset(body):
    """現在の HEAD を選択したコミットへ移動する。"""
    r = refs.reset(body.get("hash", ""), body.get("mode", "mixed"))
    if not r["moved"]:
        return {"ok": True, "message": tr("reset_noop", hash=r["hash"][:7])}
    return {"ok": True, "message": tr("reset_done", hash=r["hash"][:7], mode=r["mode"])}


@route("/api/discard", method="POST", engine=True)
def api_discard(body):
    r = worktree.discard_changes(body.get("paths"))
    return {"ok": True, "message": tr("discarded", n=r["count"])}


@route("/api/rm", method="POST", engine=True)
def api_rm(body):
    r = worktree.remove_files(body.get("paths"), cached=body.get("cached") is True, force=body.get("force") is True)
    return {"ok": True, "message": tr("rm_cached_done" if r["cached"] else "rm_done", n=r["count"])}


@route("/api/branch-rename", method="POST", engine=True)
def api_branch_rename(body):
    """ローカルブランチの名前を変える (git branch -m)。リモートのブランチは変わらない。"""
    r = refs.rename_branch(body.get("old", ""), body.get("new", ""))
    return {"ok": True, "message": tr("branch_renamed_remote" if r["has_remote"] else "branch_renamed", old=r["old"], new=r["new"])}


__all__ = [
    "api_branch", "api_branch_rename", "api_checkout", "api_delete_ref", "api_discard", "api_reset", "api_rm",
    "api_stash", "api_tag", "checkout_resolving", "discard_blockers", "parse_checkout_blockers",
    "ALREADY_EXISTS_RE", "DIRTY_RE", "api_stash_push", "dirty_tracked_files",
    "dirty_worktree_blockers", "resolve_dirty_worktree", "run_resolving",
    "stash_all_changes", "stash_push", "stash_top", "with_stash_note",
]
