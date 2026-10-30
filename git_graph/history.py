# ログ・ブランチ・タグ・reflog・stash 一覧・コミット詳細・比較・差分の API。
# git からの読み取りは git_graph.query.history に任せ、ここは query 文字列を読んで
# query を呼び、JSON を返す薄い route だけを置く。
from .query import history as qh
from .routing import route

# 互換のため再公開 (pages / テストが history.api_* や DIFF_LIMIT を参照する)
DIFF_LIMIT = qh.DIFF_LIMIT
rev_args = qh.rev_args


def paging(q):
    return qh.paging(q.get("skip", ["0"])[0], q.get("limit", ["500"])[0])


@route("/api/log")
def api_log(q):
    return qh.log(
        q.get("rev", [""])[0],
        path=q.get("path", [""])[0],
        skip=q.get("skip", ["0"])[0],
        limit=q.get("limit", ["500"])[0],
        graph=q.get("graph", [""])[0] == "1",
    )


@route("/api/stash-list")
def api_stash_list(q):
    """stash の一覧 (新しい順)。api_log と同じ [hash, short, 選択子, 日時, 件名] の形で返す。"""
    return qh.stash_list()


@route("/api/branches")
def api_branches(q):
    """ローカルブランチ → リモート追跡ブランチの順の一覧。現在のブランチには ★ を付ける。"""
    return qh.branches()


@route("/api/branches-containing")
def api_branches_containing(q):
    """指定コミットを含むローカル / リモート追跡ブランチと各 tip を返す。"""
    return qh.branches_containing(q.get("hash", [""])[0])


@route("/api/tags")
def api_tags(q):
    """タグの一覧 (新しい順)。注采付きタグは指しているコミットを使う。"""
    return qh.tags()


@route("/api/reflog")
def api_reflog(q):
    """reflog (新しい順)。branch 省略で HEAD、指定でそのローカルブランチ。"""
    return qh.reflog(
        branch=q.get("branch", [""])[0],
        skip=q.get("skip", ["0"])[0],
        limit=q.get("limit", ["500"])[0],
    )


@route("/api/commit")
def api_commit(q):
    return qh.commit(q.get("hash", [""])[0])


@route("/api/compare")
def api_compare(q):
    """2つのコミット間、またはコミットと WIP の差分ファイル一覧。"""
    return qh.compare(q.get("from", [""])[0], q.get("to", [""])[0])


@route("/api/merge-base")
def api_merge_base(q):
    """2つのコミットの共通祖先 (最も新しいもの)。"""
    return qh.merge_base(q.get("from", [""])[0], q.get("to", [""])[0])


def api_diff(q):
    """1ファイルの差分。hash、from/to のコミット間、または from/WIP の比較を指定する。"""
    from_hash = q.get("from", [""])[0] if "from" in q else ""
    to_hash = q.get("to", [""])[0] if "to" in q else ""
    wip = q.get("wip", [""])[0]
    if wip == "compare":
        return qh.diff(
            q.get("path", [""])[0],
            old=q.get("old", [""])[0],
            from_hash=q.get("from", [""])[0],
            wip=wip,
        )
    if "from" in q or "to" in q:
        return qh.diff(
            q.get("path", [""])[0],
            old=q.get("old", [""])[0],
            from_hash=from_hash,
            to_hash=to_hash,
        )
    if wip:
        return qh.diff(
            q.get("path", [""])[0],
            old=q.get("old", [""])[0],
            wip=wip,
        )
    return qh.diff(
        q.get("path", [""])[0],
        old=q.get("old", [""])[0],
        hash_=q.get("hash", [""])[0],
    )


__all__ = [
    "DIFF_LIMIT", "api_branches", "api_branches_containing", "api_commit", "api_compare",
    "api_diff", "api_log", "api_merge_base", "api_reflog", "api_stash_list", "api_tags", "paging",
]
