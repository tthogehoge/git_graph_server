# リモートとの通信 (fetch / pull / push) と、リモートの追加・削除。
# 引数を受け取って git を実行し、結果を dict で返す。HTTP の body も、画面に出す文言の組み立ても route 側の仕事。
# 認証が必要なときは {"auth_required": True, "auth_remote": リモート名} を返す (route がそのまま返して、画面が認証情報を尋ねる)。
import re
import subprocess
from .. import app_state as state
from ..gitrun import (
    auth_for_remote, check_ref_name, git, HTTP_AUTH_CACHE, is_http_auth_error, net_error, remember_auth, run_git_net,
    run_git_write,
)
from ..gitstate import (
    branch_config, count_changed_refs, git_config, has_head, head_state, is_merging, is_rebasing, list_remotes,
    refs_snapshot, upstream_of_head,
)
from ..i18n import tr, UserError
from . import operation
from .dirty import run_resolving, with_stash_note


class Auth:
    """リクエストで渡された HTTP 認証の指定。credentials は {username, password}、remote はその認証情報を使うリモート、
    retry は「認証情報を入れ直して再実行している」ことを表す (失敗したら、覚えていた認証情報を捨てる)。"""

    def __init__(self, credentials=None, remote=None, retry=False):
        self.credentials = credentials
        self.remote = remote
        self.retry = bool(retry)

    def for_remote(self, remote):
        """明示された認証情報を優先し、なければアプリ実行中のキャッシュを使う。"""
        return auth_for_remote(remote, {"auth": self.credentials, "auth_remote": self.remote})

    def retrying_for(self, remote):
        return self.retry and self.remote == remote


def check_remote_name(name):
    if not isinstance(name, str) or not name or name.startswith("-") or "\0" in name or name != name.strip():
        raise UserError("remote_name_invalid")
    r = subprocess.run(["git", "-C", state.REPO, "check-ref-format", "refs/remotes/" + name + "/HEAD"], capture_output=True)
    if r.returncode != 0:
        raise UserError("remote_name_unusable", name=repr(name))


@operation("fetch", touches_worktree=False)
def fetch(*, prune=False, auth=None):
    """全リモートから fetch する。一部のリモートだけ失敗しても、残りのリモートは取得する。
    戻り値: {"changed": 更新された ref の数} / 認証が必要なとき {"auth_required": True, "auth_remote"}。"""
    auth = auth or Auth()
    before = refs_snapshot()
    errors = []
    for remote in (x["name"] for x in list_remotes()):
        args = ["fetch", remote] + (["--prune"] if prune else [])
        credentials = auth.for_remote(remote)
        r = run_git_net(*args, auth=credentials)
        if r.returncode != 0:
            if is_http_auth_error(r) and not auth.retrying_for(remote):
                return {"auth_required": True, "auth_remote": remote}
            if auth.retrying_for(remote):
                HTTP_AUTH_CACHE.pop(remote, None)
            errors.append(r)
        else:
            remember_auth(remote, credentials)
    changed = count_changed_refs(before, refs_snapshot())
    if errors:
        raise ValueError(net_error(errors[0], tr("fetch_failed")) + (tr("fetch_partial", n=changed) if changed else ""))
    return {"changed": changed}


@operation("remote_add", touches_worktree=False, detail=lambda name, *_, **__: name if isinstance(name, str) else "")
def add_remote(name, url, *, fetch=False):
    """リモートを追加する。fetch=True なら続けて fetch する (fetch に失敗してもリモートは追加済み)。
    戻り値: {"name", "fetched": True / False / None (fetch しなかった), "error": fetch の失敗の内容}。"""
    check_remote_name(name)
    if not isinstance(url, str) or not url.strip() or "\0" in url or "\n" in url:
        raise UserError("url_invalid")
    url = url.strip()
    if url.startswith("-") or re.match(r"(ext|fd)::", url):  # オプション注入と、任意コマンドを実行できる転送方式を避ける
        raise UserError("url_unusable")
    if name in {r["name"] for r in list_remotes()}:
        raise UserError("remote_exists", name=name)
    run_git_write("remote", "add", "--", name, url)
    if not fetch:
        return {"name": name, "fetched": None, "error": ""}
    r = run_git_net("fetch", name)
    if r.returncode != 0:
        return {"name": name, "fetched": False, "error": net_error(r, tr("fetch_failed"))}
    return {"name": name, "fetched": True, "error": ""}


@operation("remote_remove", touches_worktree=False, detail=lambda name, *_, **__: name if isinstance(name, str) else "")
def remove_remote(name):
    """リモートを削除する (リモート追跡ブランチと設定も消える。リモート側は変わらない)。戻り値: {"name"}。"""
    if not isinstance(name, str) or name not in {r["name"] for r in list_remotes()}:  # 登録済みの名前だけ許可
        raise UserError("remote_not_found")
    run_git_write("remote", "remove", "--", name)
    return {"name": name}


@operation("pull", touches_worktree=True)
def pull(*, mode="ff-only", on_conflict=None, auth=None):
    """現在のブランチに upstream の変更を取り込む。
    merge で競合したときはマージ途中で止める (WIP で解決)。rebase で競合したときは中止して元に戻す。
    編集中のファイルや同名の管理外ファイルに邪魔されて取り込めないときは、止めているファイルを返す ({blocked, modified, untracked})。
    on_conflict="stash" で再実行すると全変更を stash してから、"discard" で再実行すると止めているファイルだけ破棄してから取り込む。
    戻り値: {"branch", "up", "updated", "stashed"} / 競合 {"conflict": True, "up", "stashed"} / blocked / 認証が必要なとき auth_required。"""
    auth = auth or Auth()
    if not state.HAS_WORKTREE:
        raise UserError("pull_no_worktree")
    flags = {"ff-only": ["--ff-only"], "merge": ["--no-rebase", "--no-edit"], "rebase": ["--rebase"]}.get(str(mode))
    if flags is None:
        raise UserError("pull_mode_invalid")
    if is_merging() or is_rebasing():
        raise UserError("merge_or_rebase_in_progress")
    if not has_head():
        raise UserError("pull_no_commits")
    branch = head_state()["branch"]
    if not branch:
        raise UserError("detached_pull")
    up = upstream_of_head()
    if not up:
        raise UserError("no_upstream", branch=branch)
    remote = branch_config(branch, "remote")
    before = git("rev-parse", "HEAD").strip()
    credentials = auth.for_remote(remote) if remote and remote != "." else None
    r, blocked, stashed = run_resolving(
        lambda c_locale: run_git_net("pull", *flags, auth=credentials, c_locale=c_locale),
        on_conflict, rebase_dirty=(mode == "rebase"))
    stash = {"stashed": stashed}
    if blocked:  # 手元の変更に邪魔された (通信までは成功している)
        if remote and remote != ".":
            remember_auth(remote, credentials)
        return blocked
    if r.returncode != 0:
        if is_http_auth_error(r) and not auth.retrying_for(remote):
            return {"auth_required": True, "auth_remote": remote}
        if auth.retrying_for(remote):
            HTTP_AUTH_CACHE.pop(remote, None)
        if is_merging():  # 競合 (自動マージできなかった)
            return {"conflict": True, "up": up, "stashed": stashed}
        if is_rebasing():  # 競合 → 元に戻す
            subprocess.run(["git", "-C", state.REPO, "rebase", "--abort"], capture_output=True)
            raise ValueError(with_stash_note(stash, tr("rebase_conflict", target=up, cmd="git pull --rebase")))
        raise ValueError(with_stash_note(stash, net_error(r, tr("pull_failed")) + (tr("pull_diverged_hint") if mode == "ff-only" else "")))
    if remote and remote != ".":
        remember_auth(remote, credentials)
    return {"branch": branch, "up": up, "updated": git("rev-parse", "HEAD").strip() != before, "stashed": stashed}


# ---- push: リモート側が変わって元に戻せないので、履歴 (undo) には記録しない (operation にしない) ----

def push(*, branch=None, remote=None, remote_branch=None, force=False, push_tags=False, set_upstream=False, auth=None):
    """ブランチを push する。branch があればそのローカルブランチ (チェックアウト中でなくてもよい)、なければ現在のブランチ。
    remote が指定されたら、その remote の同名ブランチ (remote_branch があればその名前) へ push する。
    戻り値: {"branch", "remote", "remotes" (登録されているリモートの数), "tags", "changed"} /
    push 先が決まらないとき {"needs_upstream": True, "remote", "remotes", "branch"} / 認証が必要なとき auth_required。"""
    auth = auth or Auth()
    if not has_head():
        raise UserError("push_no_commits")
    if branch:
        if not isinstance(branch, str) or branch.startswith("-"):
            raise UserError("branch_name_invalid")
        if subprocess.run(["git", "-C", state.REPO, "rev-parse", "-q", "--verify", f"refs/heads/{branch}"],
                          capture_output=True).returncode != 0:
            raise UserError("local_branch_missing", branch=branch)
    else:
        branch = head_state()["branch"]
        if not branch:
            raise UserError("detached_push")
    args = ["push"]
    if force:
        args.append("--force-with-lease")
    if push_tags:  # ブランチと一緒に全タグも push する (--tags)
        args.append("--tags")
    remotes = [x["name"] for x in list_remotes()]
    if not remotes:
        raise UserError("no_remotes")
    upstream_remote, merge = branch_config(branch, "remote"), branch_config(branch, "merge")
    has_upstream = bool(upstream_remote and upstream_remote != "." and merge)
    # push 先: branch.<名前>.pushRemote > remote.pushDefault (git の優先順位と同じ)
    push_remote = branch_config(branch, "pushRemote") or git_config("remote.pushDefault")
    if remote is not None:
        if not isinstance(remote, str) or remote not in remotes:
            raise UserError("remote_unregistered", remote=repr(remote))
        if remote_branch is None:
            remote_branch = merge[len("refs/heads/"):] if has_upstream and remote == upstream_remote and merge.startswith("refs/heads/") else branch
        if not isinstance(remote_branch, str) or not remote_branch:
            raise UserError("branch_name_invalid")
        check_ref_name("branch", remote_branch)
        args += (["-u"] if not has_upstream else []) + [remote, f"refs/heads/{branch}:refs/heads/{remote_branch}"]
    elif push_remote:
        if push_remote not in remotes:
            raise UserError("push_remote_unregistered", remote=repr(push_remote))
        remote = push_remote
        # upstream と同じリモートなら upstream のブランチ名へ、別のリモート (fork など) なら同名のブランチへ
        dest = merge if (has_upstream and push_remote == upstream_remote) else f"refs/heads/{branch}"
        args += [remote, f"refs/heads/{branch}:{dest}"]
    elif has_upstream:
        remote = upstream_remote
        args += [remote, f"refs/heads/{branch}:{merge}"]  # upstream が別名でもその先へ push する
    else:
        if set_upstream is not True:
            return {"needs_upstream": True, "remote": "origin" if "origin" in remotes else remotes[0], "remotes": remotes, "branch": branch}
        remote = "origin" if "origin" in remotes else remotes[0]
        args += ["-u", remote, f"refs/heads/{branch}:refs/heads/{branch}"]
    before = refs_snapshot()
    credentials = auth.for_remote(remote)
    r = run_git_net(*args, auth=credentials)
    if r.returncode != 0:
        if not auth.retry and is_http_auth_error(r):
            return {"auth_required": True, "auth_remote": remote}
        if auth.retry:
            HTTP_AUTH_CACHE.pop(remote, None)
        raise ValueError(net_error(r, tr("push_failed")))
    remember_auth(remote, credentials)
    changed = count_changed_refs(before, refs_snapshot()) > 0
    if push_tags and not changed:  # タグだけが送られた場合はローカルの ref が変わらないので、git の出力で判定する
        out = (r.stderr + r.stdout).lower()
        changed = "up-to-date" not in out and "up to date" not in out
    return {"branch": branch, "remote": remote, "remotes": len(remotes), "tags": bool(push_tags), "changed": changed}


def push_tag(tag, *, remote=None, auth=None):
    """タグを push する。push 先のリモートが決まらない (複数あって pushDefault も無い) ときは、選択肢を返す。
    戻り値: {"tag", "remote", "remotes", "up_to_date"} / {"needs_remote": True, "tag", "remotes", "remote"} / auth_required。"""
    auth = auth or Auth()
    if not isinstance(tag, str) or not tag or tag.startswith("-"):
        raise UserError("tag_name_invalid")
    if subprocess.run(["git", "-C", state.REPO, "rev-parse", "-q", "--verify", f"refs/tags/{tag}"],
                      capture_output=True).returncode != 0:
        raise UserError("tag_missing", tag=tag)
    remotes = [x["name"] for x in list_remotes()]
    if not remotes:
        raise UserError("no_remotes")
    chosen = remote
    if chosen is None:
        default = git_config("remote.pushDefault")
        if len(remotes) == 1:
            chosen = remotes[0]
        elif default in remotes:
            chosen = default
        else:
            return {"needs_remote": True, "tag": tag, "remotes": remotes, "remote": "origin" if "origin" in remotes else remotes[0]}
    if not isinstance(chosen, str) or chosen not in remotes:  # 登録済みの名前だけ許可 (オプション注入も防ぐ)
        raise UserError("remote_unregistered", remote=repr(chosen))
    credentials = auth.for_remote(chosen)
    r = run_git_net("push", chosen, f"refs/tags/{tag}:refs/tags/{tag}", auth=credentials)
    if r.returncode != 0:
        if not auth.retry and is_http_auth_error(r):
            return {"auth_required": True, "auth_remote": chosen}
        if auth.retry:
            HTTP_AUTH_CACHE.pop(chosen, None)
        raise ValueError(net_error(r, tr("push_tag_failed")))
    remember_auth(chosen, credentials)
    text = (r.stderr + r.stdout).lower()
    return {"tag": tag, "remote": chosen, "remotes": len(remotes), "up_to_date": "up-to-date" in text or "up to date" in text}
