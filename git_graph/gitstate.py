# リポジトリの状態を調べる低レベルの関数 (マージ中か、HEAD があるか、status / numstat の解析など)。
# 他の機能モジュールから共通で使われる。gitrun / i18n より上、機能モジュール (rebase, refops, wip ...) より下の層。
import json
import os
import re
import subprocess
from . import app_state as state

CONFLICT_CODES = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}
from .gitrun import git, git_bytes, HASH_RE

# git-dir の解決結果を REPO ごとにキャッシュし、MERGE_HEAD などの存在確認を
# プロセス起動なしのファイルチェックにする (Windows で効く)。
_git_dir_cache = (None, None)  # (repo, abs path)


def git_dir():
    """現在の state.REPO の git ディレクトリ (worktree 対応)。"""
    global _git_dir_cache
    repo = state.REPO
    if _git_dir_cache[0] != repo:
        p = git("rev-parse", "--git-dir").strip()
        if not os.path.isabs(p):
            p = os.path.normpath(os.path.join(repo, p))
        _git_dir_cache = (repo, p)
    return _git_dir_cache[1]


def git_path(*parts):
    """git ディレクトリ配下のパス。"""
    return os.path.join(git_dir(), *parts)


def is_merging():
    return os.path.isfile(git_path("MERGE_HEAD"))


def is_rebasing():
    return os.path.isdir(git_path("rebase-merge")) or os.path.isdir(git_path("rebase-apply"))


def is_cherry_picking():
    return os.path.isfile(git_path("CHERRY_PICK_HEAD"))


def is_reverting():
    return os.path.isfile(git_path("REVERT_HEAD"))


def read_ref(ref):
    """refs/... を解決して object id を返す。無ければ ""。git プロセスは使わない。"""
    # loose ref (シンボリック ref は再帰)
    seen = set()
    while ref and ref not in seen:
        seen.add(ref)
        path = os.path.join(git_dir(), *ref.split("/"))
        try:
            with open(path, encoding="utf-8") as f:
                val = f.read().strip()
        except OSError:
            val = None
        if val is None:
            break
        if val.startswith("ref: "):
            ref = val[5:].strip()
            continue
        return val if HASH_RE.fullmatch(val) else ""
    # packed-refs
    try:
        with open(git_path("packed-refs"), encoding="utf-8") as f:
            for line in f:
                if not line or line[0] in " #^":
                    continue
                parts = line.split()
                if len(parts) >= 2 and parts[1] == ref and HASH_RE.fullmatch(parts[0]):
                    return parts[0]
    except OSError:
        pass
    return ""


def read_head():
    """(commit_hash, branch_name_or_empty)。git プロセスは使わない。HEAD が無い / 無効なら ("", "")。"""
    try:
        with open(git_path("HEAD"), encoding="utf-8") as f:
            line = f.read().strip()
    except OSError:
        return "", ""
    if line.startswith("ref: "):
        ref = line[5:].strip()
        branch = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ""
        return read_ref(ref), branch
    if HASH_RE.fullmatch(line):
        return line, ""
    return "", ""


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
    """HEAD がコミットを指しているか。.git を読むだけ (プロセス起動なし)。"""
    h, _ = read_head()
    return bool(h)


def has_parent():
    """HEAD に親があるか。ルートコミットなら False。"""
    h, _ = read_head()
    if not h:
        return False
    try:
        return bool(git("log", "-1", "--format=%P", h).strip())
    except subprocess.CalledProcessError:
        return False


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



def head_state():
    """現在のブランチ名 (切り離されていれば None) と、マージ / リベースの途中かどうか。
    ブランチ名は .git/HEAD を読むだけ (プロセス起動なし)。"""
    _, branch = read_head()
    return {"branch": branch or None, "merging": (is_merging() or is_rebasing()) if state.HAS_WORKTREE else False}


def amend_base():
    """amend 後のコミットの親 (HEAD^)。最初のコミットなら空ツリー。"""
    head, _ = read_head()
    if not head:
        return git("hash-object", "-t", "tree", "/dev/null").strip()
    try:
        # %P = 親ハッシュ一覧。空ならルートコミット
        parents = git("log", "-1", "--format=%P", head).strip()
    except subprocess.CalledProcessError:
        return git("hash-object", "-t", "tree", "/dev/null").strip()
    if not parents:
        return git("hash-object", "-t", "tree", "/dev/null").strip()
    return parents.split()[0]

def parse_name_status(raw):
    """--name-status -z の出力を {新しいパス: (変更の種類(M/A/D/R...), 元のパス or None)} にする。"""
    toks = raw.split("\0")
    out, i = {}, 0
    while i < len(toks):
        t = toks[i].lstrip("\n")
        if not t:
            i += 1
            continue
        if t[0] in "RC":
            if i + 2 >= len(toks):
                break
            out[toks[i + 2]] = (t[0], toks[i + 1])
            i += 3
        else:
            if i + 1 >= len(toks):
                break
            out[toks[i + 1]] = (t[0], None)
            i += 2
    return out

def amend_head_changes(base):
    """直前のコミット (HEAD) が変更したファイル {新しいパス: 元のパス or None}。"""
    names = parse_name_status(git("diff", "--name-status", "-z", "-M", base, "HEAD", "--"))
    return {path: old for path, (_, old) in names.items()}

def addable_paths(paths, step=200):
    """add -A できるパスだけにする。作業ツリーにもインデックスにも無いパス (直前のコミットで git rm 済みのファイルなど) は、
    add -A が「pathspec が見つからない」で失敗する。そのパスはインデックスの状態のままコミットに入る。"""
    paths = sorted(paths)
    in_index = set()
    for i in range(0, len(paths), step):
        out = git("ls-files", "-z", "--", *paths[i:i + step], literal=True)
        in_index.update(p for p in out.split("\0") if p)
    return [p for p in paths if p in in_index or os.path.lexists(os.path.join(state.REPO, p))]

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

def list_remotes():
    """登録済みのリモート [{name, url}] (url は fetch 用)。"""
    out = {}
    for line in git("remote", "-v").splitlines():
        m = re.fullmatch(r"(\S+)\t(.*) \((fetch|push)\)", line)
        if m and (m.group(3) == "fetch" or m.group(1) not in out):
            out[m.group(1)] = m.group(2)
    for name in git("remote").split():  # url が無いリモートも出す
        out.setdefault(name, "")
    return [{"name": n, "url": u} for n, u in out.items()]

def git_config(key):
    r = subprocess.run(["git", "-C", state.REPO, "config", "--get", key],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout.strip() if r.returncode == 0 else None

STASH_MARKER = "git_graph_stash_apply"  # stash の apply / pop が競合したときに .git に残す目印 (どの stash を適用したか)


def stash_marker_path():
    return git_path(STASH_MARKER)


def stash_conflict_state():
    """stash の apply / pop が競合したまま残っているときの {"stash": 適用した stash のコミット, "action"}。
    競合が解決済み (未解決の競合ファイルがない) なら None (目印が古いだけなので無視する)。"""
    try:
        with open(stash_marker_path(), encoding="utf-8") as f:
            marker = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(marker, dict) or not HASH_RE.fullmatch(str(marker.get("stash", ""))):
        return None
    unmerged = subprocess.run(["git", "-C", state.REPO, "diff", "--name-only", "--diff-filter=U"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
    return marker if unmerged else None


__all__ = [
    "CONFLICT_CODES",
    "addable_paths",
    "count_changed_refs",
    "git_config",
    "git_dir",
    "git_path",
    "list_remotes",
    "refs_snapshot",
    "STASH_MARKER",
    "stash_conflict_state",
    "stash_marker_path",
    "upstream_of_head",
    "amend_base",
    "amend_head_changes",
    "head_state",
    "parse_name_status",
    "branch_config",
    "commit_info",
    "has_head",
    "read_ref",
    "read_head",
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
