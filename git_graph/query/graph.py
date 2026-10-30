# グラフ用の履歴モデル (残すコミットとエッジ) の読み取り・構築。
# DOT/SVG の描画は git_graph.graph に残す。HTTP は知らない。
import heapq
import subprocess
import sys
from collections import defaultdict
from .. import app_state as state
from ..gitrun import git, HASH_RE, resolve_commit
from ..gitstate import commit_info
from ..i18n import tr, UserError

DEFAULT_MERGE_MAX_NODES = 500


def stash_key(stash_ref):
    """stash@{n} 形式から n を取る。"""
    if not stash_ref.startswith("stash@{") or not stash_ref.endswith("}"):
        return None
    try:
        return int(stash_ref[7:-1])
    except ValueError:
        return None


def list_stashes():
    out = git("stash", "list", "--format=%gd%x1f%H%x1f%s")
    stashes = []
    for line in out.splitlines():
        if not line:
            continue
        parts = line.split("\x1f", 2)
        if len(parts) < 3:
            continue
        ref, h, msg = parts
        stashes.append({"ref": ref, "hash": h, "message": msg, "key": stash_key(ref)})
    return stashes


def is_ancestor(a, b):
    """a が b の祖先か。"""
    try:
        git("merge-base", "--is-ancestor", a, b)
        return True
    except subprocess.CalledProcessError:
        return False


def merge_bases(*commits):
    if not commits:
        return []
    try:
        out = git("merge-base", *commits)
        return out.split()
    except subprocess.CalledProcessError:
        return []


def head_and_branch():
    try:
        head = git("rev-parse", "HEAD").strip()
    except subprocess.CalledProcessError:
        return None, None
    try:
        branch = git("symbolic-ref", "--short", "HEAD").strip()
    except subprocess.CalledProcessError:
        branch = None
    return head, branch


def commit_timestamp(h):
    return int(git("log", "-1", "--format=%ct", h).strip())


def model_cache_key(heads, max_nodes, include_stashes):
    """モデルキャッシュのキー。"""
    parts = [",".join(sorted(heads)), str(max_nodes), "1" if include_stashes else "0"]
    # ブランチ tip の tip が変わったら無効化
    for h in sorted(heads):
        try:
            parts.append(git("rev-parse", h).strip()[:12])
        except subprocess.CalledProcessError:
            parts.append("?")
    if include_stashes:
        try:
            parts.append(git("stash", "list", "--format=%H").replace("\n", ","))
        except subprocess.CalledProcessError:
            parts.append("")
    return "\0".join(parts)


def build_model(heads=None, max_nodes=DEFAULT_MERGE_MAX_NODES, include_stashes=True):
    """残すコミット集合と親子エッジを返す。

    heads: 起点となる ref 名またはハッシュのリスト。None なら全ローカルブランチ+HEAD。
    """
    if heads is None:
        refs = git("for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines()
        head, _ = head_and_branch()
        if head and head not in refs:
            refs.append("HEAD")
        heads = refs or ["HEAD"]
    heads = [h for h in heads if h]
    if not heads:
        return {"nodes": {}, "edges": [], "heads": [], "stashes": []}

    # rev-list で到達可能なコミットを収集 (--stdin で ref を渡す)
    stdin = "\n".join(heads) + "\n"
    try:
        listed = git("rev-list", "--parents", f"--max-count={max_nodes * 3}", "--stdin", input=stdin).splitlines()
    except subprocess.CalledProcessError:
        listed = []

    nodes = {}
    edges = []
    for line in listed:
        parts = line.split()
        if not parts:
            continue
        h, parents = parts[0], parts[1:]
        if h in nodes:
            continue
        try:
            info = commit_info(h)
        except Exception:
            info = {"short": h[:7], "subject": "", "author": "", "date": ""}
        nodes[h] = {"hash": h, "short": info.get("short", h[:7]), "subject": info.get("subject", ""),
                    "author": info.get("author", ""), "date": info.get("date", ""), "parents": parents}
        for p in parents:
            edges.append((h, p))

    # max_nodes を超えたらトポロジカルに近い優先度で切る
    if len(nodes) > max_nodes:
        # 新しいもの優先
        scored = []
        for h, n in nodes.items():
            try:
                ts = commit_timestamp(h)
            except Exception:
                ts = 0
            scored.append((-ts, h))
        scored.sort()
        keep = set(h for _, h in scored[:max_nodes])
        nodes = {h: n for h, n in nodes.items() if h in keep}
        edges = [(a, b) for a, b in edges if a in keep and b in keep]

    stashes = list_stashes() if include_stashes else []
    return {"nodes": nodes, "edges": edges, "heads": heads, "stashes": stashes}


__all__ = [
    "DEFAULT_MERGE_MAX_NODES", "build_model", "commit_timestamp", "head_and_branch", "is_ancestor",
    "list_stashes", "merge_bases", "model_cache_key", "stash_key",
]
