import os
import re
import shutil
import subprocess
import sys
import threading
from collections import Counter, defaultdict
from . import app_state as state
from . import tmpdir
from .gitrun import git, resolve_commit, run_git_write
from .gitstate import (
    has_head, head_state, is_cherry_picking, is_merging, is_rebasing, is_reverting, parse_status,
    read_status_raw,
)
from .i18n import tr, UserError
from .engine import integrate, rebase
from .engine.tags import commit_patch_ids, move_tags, snapshot_for_tags, tag_mapping, tag_targets  # noqa: F401  (互換のため再公開)
from .engine.rebase import rebase_tag_only_tips  # noqa: F401
from .equivalent import equivalent_commit_prefix, first_parent_commits, nearest_equivalent_parent  # noqa: F401
from .engine.dirty import git_run as _git_run, resolve_dirty_worktree, run_resolving, with_stash_note
from .engine.integrate import unmerged_stages  # noqa: F401  (互換のため再公開)
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
    a = resolve_commit(body.get("from", ""))
    b = resolve_commit(body.get("to", ""))
    try:
        a_commits, b_commits, a_index, b_index = nearest_equivalent_parent(a, b)
    except UserError:
        return {"message": tr("find_equivalent_parents_none")}
    if a_index is None:
        return {"message": tr("find_equivalent_parents_none")}
    new_a, new_b = a_commits[a_index]["hash"], b_commits[b_index]["hash"]
    return {"from": new_a, "to": new_b,
            "message": tr("find_equivalent_parents_found", a=new_a[:7], b=new_b[:7])}

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
    a = resolve_commit(body.get("from", ""))
    b = resolve_commit(body.get("to", ""))
    candidates = []
    for ref in git("for-each-ref", "--format=%(refname)", "refs/heads").splitlines():
        name = ref.removeprefix("refs/heads/")
        tip = git("rev-parse", ref + "^{commit}").strip()
        if subprocess.run(["git", "-C", state.REPO, "merge-base", "--is-ancestor", b, tip],
                          capture_output=True).returncode != 0:
            continue
        if tip == a:
            continue
        a_commits, b_commits, a_index, b_index = nearest_equivalent_parent(a, tip)
        if a_index is not None:
            candidates.append({
                "branch": name,
                "tip": tip,
                "commit": b_commits[b_index]["hash"],
                "matched": 1,
                "remaining": a_index,
            })

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
    return {"ok": True, "message": tr("aborted", cmd=r["cmd"])}


@route("/api/resolve", method="POST", engine=True)
def api_resolve(body):
    """競合しているファイルを、ours か theirs の内容で丸ごと置き換えて解決済みにする。"""
    r = integrate.resolve_conflicts(body.get("side"), body.get("paths"))
    return {"ok": True, "message": tr("resolved", n=r["count"], side=r["side"])}


REBASE_I_MAX = 200

REBASE_I_ACTIONS = ("pick", "reword", "squash", "fixup", "drop")

def rebase_todo_hashes(base):
    """git rebase -i <base> が最初に作る todo と同じコミット列 (古い順)。"""
    out = git("rev-list", "--reverse", "--topo-order", "--right-only", "--cherry-pick",
              "--no-merges", base + "...HEAD")
    return out.split()

def check_rebase_i_range(base):
    """対話的リベースできる範囲か調べて、対象コミットのハッシュ (古い順) を返す。"""
    r = subprocess.run(["git", "-C", state.REPO, "rev-list", "--count", "--merges", base + "..HEAD"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode == 0 and r.stdout.strip() not in ("", "0"):
        raise UserError("ri_merges")
    hashes = rebase_todo_hashes(base)
    if not hashes:
        raise UserError("ri_nothing", base=base[:7])
    if len(hashes) > REBASE_I_MAX:
        raise UserError("ri_too_many", n=len(hashes), max=REBASE_I_MAX)
    return hashes

@route("/api/rebase-todo")
def api_rebase_todo(q):
    """対話的リベースの対象コミット一覧 (画面の編集ダイアログに出す)。"""
    if not state.HAS_WORKTREE:
        raise UserError("rebase_no_worktree")
    base = resolve_commit((q.get("hash") or [""])[0])
    head = api_head()
    if not head["branch"]:
        raise UserError("detached_rebase")
    hashes = check_rebase_i_range(base)
    out = git("log", "--no-walk=unsorted", "--date=short", "--format=%H%x1f%an%x1f%ad%x1f%s%x1f%B%x1e", *hashes)
    commits = []
    for rec in out.split("\x1e"):
        rec = rec.lstrip("\n")
        if not rec:
            continue
        h, an, ad, subj, msg = rec.split("\x1f", 4)
        commits.append({"hash": h, "short": h[:7], "author": an, "date": ad, "subject": subj, "message": msg.strip()})
    return {"base": base, "branch": head["branch"], "commits": commits}

@route("/api/rebase-i", method="POST")
def api_rebase_i(body):
    """git rebase -i 相当。画面で編集した todo (順序と pick/reword/squash/fixup/drop) をそのまま実行する。
    エディタは起こさず、todo は GIT_SEQUENCE_EDITOR で差し替える。reword は、そのコミット (squash/fixup を
    まとめた後) に exec で commit --amend -F を挟んで新しいメッセージにする。
    競合やエラーで止まったときは自動で rebase --abort して元に戻す。"""
    if not state.HAS_WORKTREE:
        raise UserError("rebase_no_worktree")
    base = resolve_commit(body.get("hash", ""))
    if is_merging() or is_rebasing():
        raise UserError("merge_or_rebase_in_progress")
    head = api_head()
    if not head["branch"]:
        raise UserError("detached_rebase")
    if not has_head():
        raise UserError("rebase_no_commits")
    items = body.get("todo")
    if not isinstance(items, list) or not items or not all(isinstance(i, dict) for i in items):
        raise UserError("todo_invalid")
    todo = []
    for it in items:
        act, h = it.get("action"), str(it.get("hash", ""))
        if act not in REBASE_I_ACTIONS or not re.fullmatch(r"[0-9a-f]{40}", h):
            raise UserError("todo_invalid")
        msg = str(it.get("message") or "").strip()
        if act == "reword" and not msg:
            raise UserError("ri_reword_msg_required", hash=h[:7])
        todo.append((act, h, msg))
    expected = check_rebase_i_range(base)
    if sorted(h for _, h, _ in todo) != sorted(expected):
        raise UserError("ri_changed")
    kept = [t for t in todo if t[0] != "drop"]
    if kept and kept[0][0] in ("squash", "fixup"):
        raise UserError("ri_first_squash")

    def fwd(path):  # sh から使うので区切りは / にそろえる (Windows 対策)
        return path.replace("\\", "/")

    before = git("rev-parse", "HEAD").strip()
    tmp = tmpdir.make_temp_dir("rebase")
    try:
        lines, pending = [], None  # pending: 直前のグループの reword 用 exec 行
        for act, h, msg in todo:
            if act in ("pick", "reword"):
                if pending:
                    lines.append(pending)
                    pending = None
                lines.append(f"pick {h}")
                if act == "reword":
                    mp = os.path.join(tmp, f"msg{len(lines)}.txt")
                    with open(mp, "w", encoding="utf-8", newline="\n") as f:
                        f.write(msg + "\n")
                    pending = f'exec git commit --amend -q -F "{fwd(mp)}"'
            else:  # squash / fixup / drop
                lines.append(f"{act} {h}")
        if pending:
            lines.append(pending)
        todo_path = os.path.join(tmp, "todo.txt")
        with open(todo_path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
        # todo の差し替えは python ではなく cp で行う。PyInstaller の exe などでは sys.executable が
        # アプリ自身なので、python のスクリプトのつもりで起動すると、引数がアプリのリポジトリ指定として
        # 解釈されて「seq_editor.py は git リポジトリではありません」になる。git は sh 経由で editor を呼ぶので、
        # Windows でも Git for Windows 付属の cp が使える (git が todo のパスを最後に付ける)。
        env = dict(os.environ, GIT_EDITOR=":", GIT_TERMINAL_PROMPT="0",
                   GIT_SEQUENCE_EDITOR=f'cp "{fwd(todo_path)}"')
        r, blocked, stashed = run_resolving(
            lambda c: _git_run(["rebase", "-i", base], c, env), body.get("on_conflict"), rebase_dirty=True)
        if blocked:
            return blocked
        stash = {"stashed": stashed}
        out = (r.stdout + r.stderr).strip()
        if r.returncode != 0:
            if is_rebasing():  # 競合など → 元に戻す
                subprocess.run(["git", "-C", state.REPO, "rebase", "--abort"], capture_output=True)
                tail = "\n".join(out.splitlines()[-6:])
                raise UserError("ri_aborted", tail=tail)
            raise ValueError(with_stash_note(stash, out or tr("rebase_failed")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if git("rev-parse", "HEAD").strip() == before:
        return {"ok": True, "message": with_stash_note(stash, tr("ri_noop", branch=head["branch"]))}
    n = Counter(a for a, _, _ in todo)
    parts = [tr(key, n=n[k]) for k, key in (("reword", "ri_part_reword"), ("squash", "ri_part_squash"),
                                           ("fixup", "ri_part_fixup"), ("drop", "ri_part_drop")) if n[k]]
    if parts:
        return {"ok": True, "message": with_stash_note(stash, tr("ri_done_parts", branch=head["branch"], base=base[:7], parts=" / ".join(parts)))}
    return {"ok": True, "message": with_stash_note(stash, tr("ri_done", branch=head["branch"], base=base[:7]))}


__all__ = [
    "REBASE_I_ACTIONS", "REBASE_I_MAX", "api_abort", "api_cherry_pick",
    "api_find_equivalent_branch", "api_find_equivalent_parents", "api_head", "api_merge",
    "api_rebase", "api_rebase_descendants", "api_rebase_equivalent", "api_rebase_i",
    "api_rebase_range", "api_rebase_todo", "api_resolve", "api_revert", "check_rebase_i_range",
    "equivalent_commit_prefix", "first_parent_commits", "nearest_equivalent_parent",
    "rebase_todo_hashes", "unmerged_stages",
]
