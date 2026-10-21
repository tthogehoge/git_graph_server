# リポジトリの状態を調べる低レベルの関数 (マージ中か、HEAD があるか、status / numstat の解析など)。
# 他の機能モジュールから共通で使われる。gitrun / i18n より上、機能モジュール (rebase, refops, wip ...) より下の層。
import os
import subprocess
from . import app_state as state

CONFLICT_CODES = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}
from .gitrun import git, git_bytes, HASH_RE


def is_merging():
    return subprocess.run(["git", "-C", state.REPO, "rev-parse", "-q", "--verify", "MERGE_HEAD"],
                          capture_output=True).returncode == 0


def is_rebasing():
    for d in ("rebase-merge", "rebase-apply"):
        r = subprocess.run(["git", "-C", state.REPO, "rev-parse", "--git-path", d],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        path = r.stdout.strip()
        if path and os.path.isdir(os.path.join(state.REPO, path)):
            return True
    return False


def is_cherry_picking():
    return subprocess.run(["git", "-C", state.REPO, "rev-parse", "-q", "--verify", "CHERRY_PICK_HEAD"],
                          capture_output=True).returncode == 0


def is_reverting():
    return subprocess.run(["git", "-C", state.REPO, "rev-parse", "-q", "--verify", "REVERT_HEAD"],
                          capture_output=True).returncode == 0


def paths_in_head(paths):
    """paths のうち HEAD に存在するもの。"""
    found = set()
    if not has_head():
        return found
    for i in range(0, len(paths), 200):
        out = git_bytes("ls-tree", "-r", "-z", "--name-only", "HEAD", "--", *paths[i:i + 200], literal=True)
        found.update(x for x in out.decode("utf-8", errors="replace").split("\0") if x)
    return found


def has_head():
    return subprocess.run(["git", "-C", state.REPO, "rev-parse", "-q", "--verify", "HEAD"],
                          capture_output=True).returncode == 0


def has_parent():
    return subprocess.run(["git", "-C", state.REPO, "rev-parse", "-q", "--verify", "HEAD^"],
                          capture_output=True).returncode == 0


def branch_config(branch, key):
    r = subprocess.run(["git", "-C", state.REPO, "config", "--get", f"branch.{branch}.{key}"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout.strip() if r.returncode == 0 else None


def read_status_raw():
    return git("status", "--porcelain=v1", "-z", "--untracked-files=all")


def parse_status(raw, limit=5000):
    """git status --porcelain=v1 -z を ステージ済み / 未ステージ / 未追跡 / 競合 に分ける (パスはリポジトリルート基準)。"""
    res = {"staged": [], "unstaged": [], "untracked": [], "conflicts": [], "truncated": False}
    toks = raw.split("\0")
    i = n = 0
    while i < len(toks):
        t = toks[i]
        i += 1
        if len(t) < 4 or t[2] != " ":
            continue
        xy, path = t[:2], t[3:]
        old = None
        if "R" in xy or "C" in xy:  # リネーム/コピーは続くトークンが元のパス
            old = toks[i] if i < len(toks) else None
            i += 1
        if xy == "!!":
            continue
        n += 1
        if n > limit:
            res["truncated"] = True
            break
        if xy == "??":
            res["untracked"].append({"path": path, "st": "?", "label": "new_file"})  # label は翻訳キー (画面側で翻訳する)
        elif xy in CONFLICT_CODES:
            res["conflicts"].append({"path": path, "st": "U", "label": "label_conflict"})
        else:
            x, y = xy[0], xy[1]
            if x != " ":
                res["staged"].append({"path": path, "old_path": old if x in "RC" else None, "st": x})
            if y != " ":
                res["unstaged"].append({"path": path, "old_path": None, "st": y})
    return res


def commit_info(h):
    if not HASH_RE.fullmatch(h):
        raise ValueError("bad hash")
    o = git("show", "-s", "--date=iso", "--format=%H%x1f%an <%ae>%x1f%ad%x1f%P%x1f%B", h, "--")
    full, author, date, par, msg = (o.split("\x1f", 4) + [""] * 5)[:5]
    return {"hash": full, "author": author, "date": date, "parents": par.split(),
            "message": msg.strip()}


def parse_numstat(raw):
    """--numstat -z の出力を [{path, old_path, added, deleted}] にする (バイナリは None)。"""
    toks = raw.split("\0")
    files, i = [], 0
    while i < len(toks):
        t = toks[i].lstrip("\n")
        if not t:
            i += 1
            continue
        a, d, path = (t.split("\t", 2) + ["", ""])[:3]
        old = None
        if path == "":  # リネーム: 続く2トークンが 旧パス, 新パス
            old, path = toks[i + 1], toks[i + 2]
            i += 3
        else:
            i += 1
        files.append({"path": path, "old_path": old,
                      "added": None if a == "-" else int(a),
                      "deleted": None if d == "-" else int(d)})
    return files


__all__ = [
    "CONFLICT_CODES",
    "branch_config",
    "commit_info",
    "has_head",
    "has_parent",
    "is_cherry_picking",
    "is_merging",
    "is_rebasing",
    "is_reverting",
    "parse_numstat",
    "parse_status",
    "paths_in_head",
    "read_status_raw",
]
