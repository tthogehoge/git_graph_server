# リモート (fetch / pull / push / リモートの追加・削除) の API。
# git の処理は git_graph.engine.remote に任せ、ここは body を読んで engine を呼び、
# 結果を画面用のレスポンスに整える薄い route だけを置く。
from .engine import remote
from .engine.dirty import with_stash_note
from .engine.remote import check_remote_name  # noqa: F401  (互換のため再公開)
from .gitstate import (  # noqa: F401  (読み取りだけのヘルパーは gitstate に移した。互換のため再公開する)
    count_changed_refs, git_config, list_remotes, refs_snapshot, upstream_of_head,
)
from .i18n import tr
from .routing import route


def _auth(body):
    """body の認証の指定 (auth / auth_remote / auth_retry) を engine に渡す形にする。"""
    return remote.Auth(body.get("auth"), body.get("auth_remote"), body.get("auth_retry"))


def _auth_required(result):
    return result if result.get("auth_required") else None


@route("/api/fetch", method="POST", engine=True)
def api_fetch(body):
    r = remote.fetch(prune=body.get("prune") is True, auth=_auth(body))
    if _auth_required(r):
        return r
    return {"ok": True, "message": tr("fetch_done", n=r["changed"]) if r["changed"] else tr("fetch_done_none")}


@route("/api/remotes")
def api_remotes(q):
    return {"remotes": list_remotes()}


@route("/api/remote-add", method="POST", engine=True)
def api_remote_add(body):
    """リモートを追加する。fetch:true なら続けて fetch する (fetch に失敗してもリモートは追加済み)。"""
    r = remote.add_remote(body.get("name"), body.get("url"), fetch=body.get("fetch") is True)
    if r["fetched"] is False:
        return {"ok": True, "fetch_failed": True,
                "message": tr("remote_added_fetch_failed", name=r["name"], error=r["error"])}
    return {"ok": True, "message": tr("remote_added_fetched" if r["fetched"] else "remote_added", name=r["name"])}


@route("/api/remote-remove", method="POST", engine=True)
def api_remote_remove(body):
    """リモートを削除する (リモート追跡ブランチと設定も消える。リモート側は変わらない)。"""
    r = remote.remove_remote(body.get("name"))
    return {"ok": True, "message": tr("remote_removed", name=r["name"])}


@route("/api/pull", method="POST", engine=True)
def api_pull(body):
    """現在のブランチに upstream の変更を取り込む。"""
    r = remote.pull(mode=body.get("mode", "ff-only"), on_conflict=body.get("on_conflict"), auth=_auth(body))
    if r.get("blocked") or _auth_required(r):
        return r
    if r.get("conflict"):  # 自動マージできなかった。マージ途中の状態で止めてあるので、WIP で解決する
        return {"ok": True, "conflict": True, "message": with_stash_note(r, tr("merge_conflict", target=r["up"]))}
    if not r["updated"]:
        return {"ok": True, "message": with_stash_note(r, tr("pull_uptodate", branch=r["branch"]))}
    return {"ok": True, "message": with_stash_note(r, tr("pull_done", branch=r["branch"], up=r["up"]))}


@route("/api/push", method="POST", engine=True)
def api_push(body):
    """ブランチを push する。body.branch があればそのローカルブランチ (チェックアウト中でなくてもよい)、
    なければ現在のブランチ。body.remote が指定された場合は、その remote の同名ブランチへ push する。"""
    r = remote.push(branch=body.get("branch"), remote=body.get("remote"), remote_branch=body.get("remote_branch"),
                    force=body.get("force") is True, push_tags=body.get("push_tags") is True,
                    set_upstream=body.get("set_upstream") is True, auth=_auth(body))
    if r.get("needs_upstream") or _auth_required(r):
        return r
    where = f" ({r['remote']})" if r["remotes"] > 1 else ""
    key = ("pushed_with_tags" if r["tags"] else "pushed") if r["changed"] else "pushed_nothing"
    return {"ok": True, "message": tr(key, branch=r["branch"], where=where)}


@route("/api/push-tag", method="POST", engine=True)
def api_push_tag(body):
    """タグを push する。push 先のリモートが決まらない (複数あって pushDefault も無い) ときは、選択肢を返す。"""
    r = remote.push_tag(body.get("tag"), remote=body.get("remote"), auth=_auth(body))
    if r.get("needs_remote") or _auth_required(r):
        return r
    where = f" ({r['remote']})" if r["remotes"] > 1 else ""
    return {"ok": True, "message": tr("pushed_tag_nothing" if r["up_to_date"] else "pushed_tag", tag=r["tag"], where=where)}


__all__ = [
    "api_fetch", "api_pull", "api_push", "api_push_tag", "api_remote_add", "api_remote_remove",
    "api_remotes", "check_remote_name", "count_changed_refs", "git_config", "list_remotes",
    "refs_snapshot", "upstream_of_head",
]
