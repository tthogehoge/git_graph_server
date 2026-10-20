import re
import subprocess
import app_state as state


def refs_snapshot():
    r = subprocess.run(["git", "-C", state.REPO, "for-each-ref", "--format=%(objectname) %(refname)", "refs/remotes", "refs/tags"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return {name: oid for oid, _, name in (l.partition(" ") for l in r.stdout.splitlines())}

def count_changed_refs(before, after):
    return sum(1 for k in set(before) | set(after) if before.get(k) != after.get(k))

def upstream_of_head():
    r = subprocess.run(["git", "-C", state.REPO, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout.strip() if r.returncode == 0 else None

def api_fetch(body):
    from gitrun import HTTP_AUTH_CACHE, auth_for_remote, git, is_http_auth_error, net_error, remember_auth, run_git_net
    from i18n import tr
    remotes = git("remote").split()
    before = refs_snapshot()
    errors = []
    for remote in remotes:
        args = ["fetch", remote]
        if body.get("prune") is True:
            args.append("--prune")
        auth = auth_for_remote(remote, body)
        r = run_git_net(*args, auth=auth)
        if r.returncode != 0:
            if is_http_auth_error(r) and (not body.get("auth_retry") or body.get("auth_remote") != remote):
                return {"auth_required": True, "auth_remote": remote}
            if body.get("auth_retry") and body.get("auth_remote") == remote:
                HTTP_AUTH_CACHE.pop(remote, None)
            errors.append(r)
        else:
            remember_auth(remote, auth)
    n = count_changed_refs(before, refs_snapshot())
    if errors:  # 一部のリモートだけ失敗しても、残りのリモートは取得する
        raise ValueError(net_error(errors[0], tr("fetch_failed")) + (tr("fetch_partial", n=n) if n else ""))
    return {"ok": True, "message": tr("fetch_done", n=n) if n else tr("fetch_done_none")}

def list_remotes():
    """登録済みのリモート [{name, url}] (url は fetch 用)。"""
    from gitrun import git
    out = {}
    for line in git("remote", "-v").splitlines():
        m = re.fullmatch(r"(\S+)\t(.*) \((fetch|push)\)", line)
        if m and (m.group(3) == "fetch" or m.group(1) not in out):
            out[m.group(1)] = m.group(2)
    for name in git("remote").split():  # url が無いリモートも出す
        out.setdefault(name, "")
    return [{"name": n, "url": u} for n, u in out.items()]

def api_remotes(q):
    return {"remotes": list_remotes()}

def check_remote_name(name):
    from i18n import UserError
    if not isinstance(name, str) or not name or name.startswith("-") or "\0" in name or name != name.strip():
        raise UserError("remote_name_invalid")
    r = subprocess.run(["git", "-C", state.REPO, "check-ref-format", "refs/remotes/" + name + "/HEAD"], capture_output=True)
    if r.returncode != 0:
        raise UserError("remote_name_unusable", name=repr(name))

def api_remote_add(body):
    """リモートを追加する。fetch:true なら続けて fetch する (fetch に失敗してもリモートは追加済み)。"""
    from gitrun import net_error, run_git_net, run_git_write
    from i18n import UserError, tr
    name, url = body.get("name"), body.get("url")
    check_remote_name(name)
    if not isinstance(url, str) or not url.strip() or "\0" in url or "\n" in url:
        raise UserError("url_invalid")
    url = url.strip()
    if url.startswith("-") or re.match(r"(ext|fd)::", url):  # オプション注入と、任意コマンドを実行できる転送方式を避ける
        raise UserError("url_unusable")
    if name in {r["name"] for r in list_remotes()}:
        raise UserError("remote_exists", name=name)
    run_git_write("remote", "add", "--", name, url)
    msg = tr("remote_added", name=name)
    if body.get("fetch") is True:
        r = run_git_net("fetch", name)
        if r.returncode != 0:
            return {"ok": True, "fetch_failed": True,
                    "message": tr("remote_added_fetch_failed", name=name, error=net_error(r, tr("fetch_failed")))}
        msg = tr("remote_added_fetched", name=name)
    return {"ok": True, "message": msg}

def api_remote_remove(body):
    """リモートを削除する (リモート追跡ブランチと設定も消える。リモート側は変わらない)。"""
    from gitrun import run_git_write
    from i18n import UserError, tr
    name = body.get("name")
    if not isinstance(name, str) or name not in {r["name"] for r in list_remotes()}:  # 登録済みの名前だけ許可
        raise UserError("remote_not_found")
    run_git_write("remote", "remove", "--", name)
    return {"ok": True, "message": tr("remote_removed", name=name)}

def api_pull(body):
    """現在のブランチに upstream の変更を取り込む。
    merge で競合したときはマージ途中で止める (WIP で解決)。rebase で競合したときは中止して元に戻す。"""
    from gitrun import HTTP_AUTH_CACHE, auth_for_remote, git, is_http_auth_error, net_error, remember_auth, run_git_net
    from i18n import UserError, tr
    from rebase import api_head, is_merging, is_rebasing
    from refops import has_head
    if not state.HAS_WORKTREE:
        raise UserError("pull_no_worktree")
    mode = body.get("mode", "ff-only")
    flags = {"ff-only": ["--ff-only"], "merge": ["--no-rebase", "--no-edit"], "rebase": ["--rebase"]}.get(str(mode))
    if flags is None:
        raise UserError("pull_mode_invalid")
    if is_merging() or is_rebasing():
        raise UserError("merge_or_rebase_in_progress")
    if not has_head():
        raise UserError("pull_no_commits")
    branch = api_head()["branch"]
    if not branch:
        raise UserError("detached_pull")
    up = upstream_of_head()
    if not up:
        raise UserError("no_upstream", branch=branch)
    remote = branch_config(branch, "remote")
    before = git("rev-parse", "HEAD").strip()
    auth = auth_for_remote(remote, body) if remote and remote != "." else None
    r = run_git_net("pull", *flags, auth=auth)
    if r.returncode != 0:
        if is_http_auth_error(r) and (not body.get("auth_retry") or body.get("auth_remote") != remote):
            return {"auth_required": True, "auth_remote": remote}
        if body.get("auth_retry") and body.get("auth_remote") == remote:
            HTTP_AUTH_CACHE.pop(remote, None)
        if is_merging():  # 競合 (自動マージできなかった)
            return {"ok": True, "conflict": True,
                    "message": tr("merge_conflict", target=up)}
        if is_rebasing():  # 競合 → 元に戻す
            subprocess.run(["git", "-C", state.REPO, "rebase", "--abort"], capture_output=True)
            raise UserError("rebase_conflict", target=up, cmd="git pull --rebase")
        raise ValueError(net_error(r, tr("pull_failed")) + (tr("pull_diverged_hint") if mode == "ff-only" else ""))
    if remote and remote != ".":
        remember_auth(remote, auth)
    if git("rev-parse", "HEAD").strip() == before:
        return {"ok": True, "message": tr("pull_uptodate", branch=branch)}
    return {"ok": True, "message": tr("pull_done", branch=branch, up=up)}

def branch_config(branch, key):
    r = subprocess.run(["git", "-C", state.REPO, "config", "--get", f"branch.{branch}.{key}"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout.strip() if r.returncode == 0 else None

def git_config(key):
    r = subprocess.run(["git", "-C", state.REPO, "config", "--get", key],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout.strip() if r.returncode == 0 else None

def api_push(body):
    """ブランチを push する。body.branch があればそのローカルブランチ (チェックアウト中でなくてもよい)、
    なければ現在のブランチ。body.remote が指定された場合は、その remote の同名ブランチへ push する。"""
    from gitrun import HTTP_AUTH_CACHE, auth_for_remote, check_ref_name, git, is_http_auth_error, net_error, remember_auth, run_git_net
    from i18n import UserError, tr
    from rebase import api_head
    from refops import has_head
    if not has_head():
        raise UserError("push_no_commits")
    branch = body.get("branch")
    if branch:
        if not isinstance(branch, str) or branch.startswith("-"):
            raise UserError("branch_name_invalid")
        if subprocess.run(["git", "-C", state.REPO, "rev-parse", "-q", "--verify", f"refs/heads/{branch}"],
                          capture_output=True).returncode != 0:
            raise UserError("local_branch_missing", branch=branch)
    else:
        branch = api_head()["branch"]
        if not branch:
            raise UserError("detached_push")
    args = ["push"]
    if body.get("force") is True:
        args.append("--force-with-lease")
    remotes = git("remote").split()
    if not remotes:
        raise UserError("no_remotes")
    upstream_remote, merge = branch_config(branch, "remote"), branch_config(branch, "merge")
    has_upstream = bool(upstream_remote and upstream_remote != "." and merge)
    # push 先: branch.<名前>.pushRemote > remote.pushDefault (git の優先順位と同じ)
    push_remote = branch_config(branch, "pushRemote") or git_config("remote.pushDefault")
    selected_remote = body.get("remote")
    if selected_remote is not None:
        if not isinstance(selected_remote, str) or selected_remote not in remotes:
            raise UserError("remote_unregistered", remote=repr(selected_remote))
        remote = selected_remote
        remote_branch = body.get("remote_branch")
        if remote_branch is None:
            remote_branch = merge[len("refs/heads/"):] if has_upstream and remote == upstream_remote and merge.startswith("refs/heads/") else branch
        if not isinstance(remote_branch, str) or not remote_branch:
            raise UserError("branch_name_invalid")
        check_ref_name("branch", remote_branch)
        dest = f"refs/heads/{remote_branch}"
        args += (["-u"] if not has_upstream else []) + [remote, f"refs/heads/{branch}:{dest}"]
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
        default = "origin" if "origin" in remotes else remotes[0]
        chosen = body.get("remote")
        if body.get("set_upstream") is not True:
            return {"needs_upstream": True, "remote": default, "remotes": remotes, "branch": branch}
        if chosen is None:
            chosen = default
        if not isinstance(chosen, str) or chosen not in remotes:  # 登録済みの名前だけ許可 (オプション注入も防ぐ)
            raise UserError("remote_unregistered", remote=repr(chosen))
        remote = chosen
        args += ["-u", remote, f"refs/heads/{branch}:refs/heads/{branch}"]
    before = refs_snapshot()
    auth = auth_for_remote(remote, body)
    r = run_git_net(*args, auth=auth)
    if r.returncode != 0:
        if not body.get("auth_retry") and is_http_auth_error(r):
            return {"auth_required": True, "auth_remote": remote}
        if body.get("auth_retry"):
            HTTP_AUTH_CACHE.pop(remote, None)
        raise ValueError(net_error(r, tr("push_failed")))
    remember_auth(remote, auth)
    changed = count_changed_refs(before, refs_snapshot()) > 0
    where = f" ({remote})" if len(remotes) > 1 else ""
    return {"ok": True, "message": tr("pushed" if changed else "pushed_nothing", branch=branch, where=where)}

def api_push_tag(body):
    """タグを push する。push 先のリモートが決まらない (複数あって pushDefault も無い) ときは、選択肢を返す。"""
    from gitrun import HTTP_AUTH_CACHE, auth_for_remote, git, is_http_auth_error, net_error, remember_auth, run_git_net
    from i18n import UserError, tr
    tag = body.get("tag")
    if not isinstance(tag, str) or not tag or tag.startswith("-"):
        raise UserError("tag_name_invalid")
    if subprocess.run(["git", "-C", state.REPO, "rev-parse", "-q", "--verify", f"refs/tags/{tag}"],
                      capture_output=True).returncode != 0:
        raise UserError("tag_missing", tag=tag)
    remotes = git("remote").split()
    if not remotes:
        raise UserError("no_remotes")
    chosen = body.get("remote")
    if chosen is None:
        default = git_config("remote.pushDefault")
        if len(remotes) == 1:
            chosen = remotes[0]
        elif default in remotes:
            chosen = default
        else:
            return {"needs_remote": True, "tag": tag, "remotes": remotes,
                    "remote": "origin" if "origin" in remotes else remotes[0]}
    if not isinstance(chosen, str) or chosen not in remotes:  # 登録済みの名前だけ許可 (オプション注入も防ぐ)
        raise UserError("remote_unregistered", remote=repr(chosen))
    auth = auth_for_remote(chosen, body)
    r = run_git_net("push", chosen, f"refs/tags/{tag}:refs/tags/{tag}", auth=auth)
    if r.returncode != 0:
        if not body.get("auth_retry") and is_http_auth_error(r):
            return {"auth_required": True, "auth_remote": chosen}
        if body.get("auth_retry"):
            HTTP_AUTH_CACHE.pop(chosen, None)
        raise ValueError(net_error(r, tr("push_tag_failed")))
    remember_auth(chosen, auth)
    where = f" ({chosen})" if len(remotes) > 1 else ""
    up_to_date = "up-to-date" in (r.stderr + r.stdout).lower() or "up to date" in (r.stderr + r.stdout).lower()
    return {"ok": True, "message": tr("pushed_tag_nothing" if up_to_date else "pushed_tag", tag=tag, where=where)}


__all__ = [
    "api_fetch", "api_pull", "api_push", "api_push_tag", "api_remote_add",
    "api_remote_remove", "api_remotes", "branch_config", "check_remote_name",
    "count_changed_refs", "git_config", "list_remotes", "refs_snapshot",
    "upstream_of_head",
]
