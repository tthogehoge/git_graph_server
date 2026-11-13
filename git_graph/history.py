# ログ・ブランチ・タグ・reflog・stash 一覧・コミット詳細・比較・差分の API。
# git からの読み取りは git_graph.query.history に任せ、ここは query 文字列を読んで
# query を呼び、JSON を返す薄い route だけを置く。
from .i18n import tr
from .query import history as qh
from .query.diff_sources import diff_sources
from .routing import route

# 互換のため再公開 (pages / テストが history.api_* や DIFF_LIMIT を参照する)
DIFF_LIMIT = qh.DIFF_LIMIT
rev_args = qh.rev_args


def paging(q):
    return qh.paging(q.get("skip", ["0"])[0], q.get("limit", ["500"])[0])


@route("/api/log-position")
def api_log_position(q):
    return qh.log_position(q.get("rev", [""])[0], q.get("hash", [""])[0])


@route("/api/log-total")
def api_log_total(q):
    return qh.topo_total_status(q.get("rev", [""])[0])


def search_params(q):
    """検索条件 (hash / grep / author / committer / since / until。scope=all なら全コミットが対象)。"""
    search = {k: q.get(k, [""])[0].strip() for k in ("hash", "grep", "author", "committer", "since", "until")}
    search = {k: v for k, v in search.items() if v}
    if q.get("scope", [""])[0] == "all":
        search["all"] = True
    return search


@route("/api/log")
def api_log(q):
    return qh.log(
        q.get("rev", [""])[0],
        path=q.get("path", [""])[0],
        skip=q.get("skip", ["0"])[0],
        limit=q.get("limit", ["500"])[0],
        graph=q.get("graph", [""])[0] == "1",
        search=search_params(q),
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


@route("/api/submodules")
def api_submodules(q):
    """登録されている submodule の一覧。"""
    return qh.submodules()


@route("/api/tags")
def api_tags(q):
    """タグの一覧 (新しい順)。注釈付きタグは指しているコミットを使う。"""
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
    # from/to の有無でコミット間差分かどうかを判定する (空文字でもキーがあればコミット間)
    from_hash = q.get("from", [""])[0] if "from" in q else ""
    to_hash = q.get("to", [""])[0] if "to" in q else ""
    # wip=compare のときは from が base。通常の from/to とは別経路
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


def _side_label(side):
    """差分エディタの片側の見出し (どのコミット / 状態のものか)。"""
    kind, ref = side["kind"], side["ref"]
    if kind == "a":
        return "A " + ref
    if kind == "b":
        return "B " + ref
    if kind == "commit":
        return ref
    if kind == "head":
        return "HEAD"
    labels = {"parent": "diff_side_parent", "amend_base": "diff_side_amend_base", "index": "diff_side_index",
              "worktree": "diff_side_worktree", "empty": "diff_side_empty"}
    return tr(labels[kind], h=ref).strip()


@route("/api/diff-sources")
def api_diff_sources(q):
    """1ファイルの差分の変更前・変更後の内容 (差分エディタ用)。指定は /diff のクエリと同じ (hash / from・to / wip)。"""
    r = diff_sources(
        q.get("path", [""])[0],
        old=q.get("old", [""])[0],
        hash_=q.get("hash", [""])[0],
        from_hash=q.get("from", [""])[0],
        to_hash=q.get("to", [""])[0],
        wip=q.get("wip", [""])[0],
        between="from" in q or "to" in q,  # wip の指定があるときは、diff_sources が先に wip を見る
    )
    for side in (r["old"], r["new"]):
        side["label"] = _side_label(side)
    return r


__all__ = [
    "DIFF_LIMIT", "api_branches", "api_branches_containing", "api_commit", "api_compare",
    "api_diff", "api_diff_sources", "api_log", "api_log_position", "api_log_total", "api_merge_base", "api_reflog", "api_stash_list", "api_tags", "paging",
    "search_params",
]
