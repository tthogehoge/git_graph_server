# pygit2 によるリポジトリ読み取り。Windows での git.exe プロセス起動を避ける。
# 書き込み・ネットワーク・複雑な porcelain (rebase 等) は gitrun の CLI のまま。
import os
from . import app_state as state

try:
    import pygit2
except ImportError as e:  # pragma: no cover
    pygit2 = None
    _import_error = e
else:
    _import_error = None

_repo_cache = (None, None)  # (path, Repository)


def available():
    return pygit2 is not None


def require_pygit2():
    if pygit2 is None:
        raise RuntimeError(
            "pygit2 is required but not installed. Install with: pip install pygit2"
        ) from _import_error


def repo():
    """現在の state.REPO の pygit2.Repository (キャッシュ)。"""
    require_pygit2()
    global _repo_cache
    path = state.REPO
    if _repo_cache[0] != path:
        _repo_cache = (path, pygit2.Repository(path))
    return _repo_cache[1]


def reset_cache():
    """テストや REPO 切替用。"""
    global _repo_cache
    _repo_cache = (None, None)


def peel_commit(obj):
    """Object を Commit まで peel。失敗したら None。"""
    try:
        return obj.peel(pygit2.GIT_OBJECT_COMMIT)
    except (pygit2.GitError, ValueError, TypeError):
        try:
            if isinstance(obj, pygit2.Commit):
                return obj
        except Exception:
            pass
        return None


def resolve_commit(h):
    """短縮/フル hash をフル commit id (hex) にする。無ければ None。"""
    require_pygit2()
    r = repo()
    try:
        obj = r.revparse_single(h)
    except (KeyError, ValueError, pygit2.GitError):
        return None
    c = peel_commit(obj)
    return str(c.id) if c is not None else None


def head_commit_and_branch():
    """(head_hex or '', branch_shorthand or '')。detached なら branch は ''。"""
    r = repo()
    try:
        if r.head_is_unborn:
            return "", ""
    except Exception:
        pass
    try:
        head = r.head
    except pygit2.GitError:
        return "", ""
    try:
        target = head.peel(pygit2.GIT_OBJECT_COMMIT)
        h = str(target.id)
    except (pygit2.GitError, ValueError):
        h = str(head.target) if head.target else ""
    branch = ""
    try:
        if not r.head_is_detached and head.name.startswith("refs/heads/"):
            branch = head.shorthand
    except pygit2.GitError:
        pass
    return h, branch


def list_ref_infos():
    """[(refname, target_commit_hex, peeled_or_same, is_head), ...]
    is_head は現在 checkout 中の ref。タグは peel した commit。"""
    r = repo()
    head_name = None
    try:
        if not r.head_is_unborn:
            head_name = r.head.name
    except pygit2.GitError:
        head_name = None
    out = []
    for refname in r.listall_references():
        try:
            ref = r.references[refname]
        except KeyError:
            continue
        try:
            obj = ref.peel(pygit2.GIT_OBJECT_COMMIT)
            target = str(obj.id)
            peeled = target
        except (KeyError, ValueError, pygit2.GitError):
            try:
                target = str(ref.target)
                peeled = target
            except Exception:
                continue
        # annotated tag: target may be tag object; peel already did commit
        out.append((refname, target, peeled, refname == head_name))
    return out


def config_get(key):
    """git config --get 相当。無い / キーが不正なら None。"""
    try:
        return repo().config[key]
    except (KeyError, ValueError, pygit2.GitError):
        return None


def upstream_of_head():
    """現在のブランチの upstream の短い名前 ("origin/main")。切り離し / 未設定 / リモートの ref が無いときは None。"""
    r = repo()
    try:
        if r.head_is_unborn or r.head_is_detached:
            return None
        branch = r.branches.local.get(r.head.shorthand)
        up = branch.upstream if branch is not None else None
    except (KeyError, ValueError, pygit2.GitError):
        return None
    return up.shorthand if up is not None else None


def remotes_list():
    """登録済みのリモート [(name, fetch url)] (名前順)。"""
    return sorted((rm.name, rm.url or "") for rm in repo().remotes)


def refs_snapshot(prefixes):
    """{refname: 指し先の object id (注釈付きタグはタグオブジェクト)}。for-each-ref の objectname と同じ。"""
    r = repo()
    out = {}
    for name in r.listall_references():
        if not name.startswith(tuple(prefixes)):
            continue
        try:
            out[name] = str(r.references[name].resolve().target)
        except (KeyError, pygit2.GitError):
            continue
    return out


def has_conflicts():
    """index に未解決の競合 (unmerged) があるか。"""
    idx = repo().index
    try:
        idx.read()
    except pygit2.GitError:
        pass
    conflicts = idx.conflicts
    return conflicts is not None and any(True for _ in conflicts)


def index_contains(paths):
    """paths のうち index にあるもの (git ls-files 相当)。"""
    idx = repo().index
    try:
        idx.read()
    except pygit2.GitError:
        pass
    return {p for p in paths if p in idx}


def paths_in_tree(treeish, paths):
    """paths のうち treeish のツリーにあるもの。ディレクトリを指すパスは、配下のファイルを返す (ls-tree -r と同じ)。"""
    r = repo()
    try:
        tree = peel_commit(r.revparse_single(treeish)).tree
    except (KeyError, ValueError, AttributeError, pygit2.GitError):
        return set()
    found = set()

    def walk(t, prefix):
        for e in t:
            p = f"{prefix}/{e.name}"
            if e.type == pygit2.GIT_OBJECT_TREE:
                walk(r[e.id], p)
            else:
                found.add(p)

    for p in paths:
        try:
            entry = tree[p]
        except (KeyError, ValueError, pygit2.GitError):
            continue
        if entry.type == pygit2.GIT_OBJECT_TREE:
            walk(r[entry.id], p)
        else:
            found.add(p)
    return found


def read_head():
    """(HEAD のコミット, ブランチ名)。ブランチがまだ無い (最初のコミット前) ときはコミットが "" でブランチ名は返す。
    linked worktree でも正しく解決できる (.git を直接読むと worktree の ref を見落とす)。"""
    r = repo()
    try:
        head = r.lookup_reference("HEAD")
    except (KeyError, pygit2.GitError):
        return "", ""
    target = head.target
    if isinstance(target, str):  # シンボリック参照
        branch = target[len("refs/heads/"):] if target.startswith("refs/heads/") else ""
        try:
            return str(r.lookup_reference(target).resolve().target), branch
        except (KeyError, pygit2.GitError):
            return "", branch
    return str(target), ""  # 切り離し


def refs_key():
    """ref の変化検出用の文字列。peel せず ref の直接の指し先だけを見る (タグが多くても軽い)。"""
    r = repo()
    parts = []
    for name in r.listall_references():
        try:
            parts.append(f"{r.references[name].target}\t{name}")
        except (KeyError, pygit2.GitError):
            continue
    return "\n".join(sorted(parts))


def is_plain_rev(rev):
    """revparse_single で解釈できる単純な rev か。A..B / ^A / A^@ / --all など rev-list 専用の構文は False (CLI に任せる)。"""
    return (bool(rev) and not rev.startswith(("-", "^")) and ".." not in rev
            and not rev.endswith(("^@", "^!", "^-")) and "\0" not in rev)


def rev_list_topo_parents(tips, strict=False):
    """tips (refname or hex) から到達するコミットを topo+time 順 (子が先) で列挙。
    各要素: (hex, [parent_hex, ...], commit_time)。
    strict=True (ユーザーが指定した rev) のときは、解決できない rev を黙って無視せず UserError にする。
    """
    r = repo()
    sort = pygit2.GIT_SORT_TOPOLOGICAL | pygit2.GIT_SORT_TIME
    walker = r.walk(None, sort)
    pushed = False
    for tip in tips:
        try:
            obj = r.revparse_single(tip)
            c = peel_commit(obj)
            if c is None:
                raise KeyError(tip)
            walker.push(c.id)
            pushed = True
        except (KeyError, ValueError, pygit2.GitError):
            if strict:
                from .i18n import UserError
                raise UserError("rev_not_found", rev=tip) from None
            continue
    if not pushed:
        return []
    result = []
    for c in walker:
        result.append((str(c.id), [str(p) for p in c.parent_ids], int(c.commit_time)))
    return result


def commit_subjects(hashes):
    """{hex: subject}。一件目の行だけ。"""
    r = repo()
    out = {}
    for h in hashes:
        try:
            c = r[h]
            if not isinstance(c, pygit2.Commit):
                c = peel_commit(c)
            if c is None:
                continue
            msg = c.message or ""
            out[h] = msg.split("\n", 1)[0]
        except (KeyError, ValueError, pygit2.GitError):
            continue
    return out


def commit_message(h):
    try:
        c = repo()[h]
        if not isinstance(c, pygit2.Commit):
            c = peel_commit(c)
        return (c.message or "").strip() if c else ""
    except (KeyError, ValueError, pygit2.GitError):
        return ""


def blob_at(spec):
    """'commit:path' or oid → (type_name, data_bytes) or (None, None)。"""
    r = repo()
    try:
        if ":" in spec and not spec.startswith(":"):
            # commit:path — path may contain ':'
            # Prefer last approach: revparse treeish then path
            treeish, _, path = spec.partition(":")
            obj = r.revparse_single(treeish)
            commit = peel_commit(obj)
            if commit is None:
                # maybe tree
                try:
                    tree = obj.peel(pygit2.GIT_OBJECT_TREE)
                except Exception:
                    return None, None
            else:
                tree = commit.tree
            try:
                entry = tree[path]
            except KeyError:
                return None, None
            obj = r[entry.id]
            if isinstance(obj, pygit2.Blob):
                return "blob", obj.data
            return entry.type_str if hasattr(entry, "type_str") else None, None
        obj = r.revparse_single(spec)
        if isinstance(obj, pygit2.Blob):
            return "blob", obj.data
        return None, None
    except (KeyError, ValueError, pygit2.GitError):
        return None, None


def git_dir_path():
    return repo().path.rstrip(os.sep)


EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"




def list_stashes():
    """[(stash_hex, base_commit_hex, "stash@{n}", subject), ...] 新しい順。
    stash が無ければ []。"""
    r = repo()
    try:
        stashes = r.listall_stashes()
    except pygit2.GitError:
        return []
    out = []
    for i, s in enumerate(stashes):
        h = str(s.commit_id)
        try:
            c = r[s.commit_id]
            base = str(c.parents[0].id) if c.parents else ""
        except (KeyError, ValueError, pygit2.GitError, IndexError):
            base = ""
        msg = (s.message or "").strip()
        # git stash list の件名は "On branch: message" 形式。グラフではそのまま使う。
        out.append((h, base, f"stash@{{{i}}}", msg))
    return out


def stash_key():
    """stash 一覧の変化検出用。"""
    try:
        return "\n".join(str(s.commit_id) for s in repo().listall_stashes())
    except pygit2.GitError:
        return ""


def status_lists(limit=5000):
    """parse_status と同じ形の dict を返す (pygit2.Repository.status)。
    {staged, unstaged, untracked, conflicts, truncated}
    """
    r = repo()
    res = {"staged": [], "unstaged": [], "untracked": [], "conflicts": [], "truncated": False}
    try:
        st = r.status()
    except pygit2.GitError:
        return res
    # GIT_STATUS_* 定数 (pygit2.enums.FileStatus と同等)
    IGNORED = pygit2.GIT_STATUS_IGNORED
    CONFLICTED = pygit2.GIT_STATUS_CONFLICTED
    INDEX_NEW = pygit2.GIT_STATUS_INDEX_NEW
    INDEX_MODIFIED = pygit2.GIT_STATUS_INDEX_MODIFIED
    INDEX_DELETED = pygit2.GIT_STATUS_INDEX_DELETED
    INDEX_RENAMED = pygit2.GIT_STATUS_INDEX_RENAMED
    INDEX_TYPECHANGE = pygit2.GIT_STATUS_INDEX_TYPECHANGE
    WT_NEW = pygit2.GIT_STATUS_WT_NEW
    WT_MODIFIED = pygit2.GIT_STATUS_WT_MODIFIED
    WT_DELETED = pygit2.GIT_STATUS_WT_DELETED
    WT_TYPECHANGE = pygit2.GIT_STATUS_WT_TYPECHANGE
    WT_RENAMED = pygit2.GIT_STATUS_WT_RENAMED
    n = 0
    for path, flags in st.items():
        if flags & IGNORED:
            continue
        n += 1
        if n > limit:
            res["truncated"] = True
            break
        if flags & CONFLICTED:
            res["conflicts"].append({"path": path, "st": "U", "label": "label_conflict"})
            continue
        if flags & WT_NEW and not (flags & (INDEX_NEW | INDEX_MODIFIED | INDEX_DELETED |
                                            INDEX_RENAMED | INDEX_TYPECHANGE)):
            res["untracked"].append({"path": path, "st": "?", "label": "new_file"})
            continue
        if flags & INDEX_NEW:
            res["staged"].append({"path": path, "old_path": None, "st": "A"})
        elif flags & (INDEX_MODIFIED | INDEX_TYPECHANGE):
            res["staged"].append({"path": path, "old_path": None, "st": "M"})
        elif flags & INDEX_DELETED:
            res["staged"].append({"path": path, "old_path": None, "st": "D"})
        elif flags & INDEX_RENAMED:
            res["staged"].append({"path": path, "old_path": None, "st": "R"})
        if flags & (WT_MODIFIED | WT_TYPECHANGE):
            res["unstaged"].append({"path": path, "old_path": None, "st": "M"})
        elif flags & WT_DELETED:
            res["unstaged"].append({"path": path, "old_path": None, "st": "D"})
        elif flags & WT_RENAMED:
            res["unstaged"].append({"path": path, "old_path": None, "st": "R"})
    _pair_staged_renames(r, res)
    return res


def _pair_staged_renames(r, res):
    """libgit2 の status はリネームを「削除 + 追加」に分けて返すため、git status と同じく 1 件の R (旧パス付き) にまとめる。
    ステージ済みに追加と削除が両方あるときだけ、HEAD と index の差分でリネーム検出する (普段は何もしない)。"""
    staged = res["staged"]
    if not (any(f["st"] == "A" for f in staged) and any(f["st"] == "D" for f in staged)):
        return
    try:
        if r.head_is_unborn:
            return
        diff = r.index.diff_to_tree(r.head.peel(pygit2.GIT_OBJECT_TREE))
        diff.find_similar()
    except pygit2.GitError:
        return
    renames = {}  # 新パス -> 旧パス
    for delta in diff.deltas:
        if delta.status_char() == "R":
            renames[delta.new_file.path] = delta.old_file.path
    if not renames:
        return
    olds = set(renames.values())
    merged = []
    for f in staged:
        if f["st"] == "D" and f["path"] in olds:
            continue  # 旧パス側の削除は R に含める
        if f["st"] == "A" and f["path"] in renames:
            f = {"path": f["path"], "old_path": renames[f["path"]], "st": "R"}
        merged.append(f)
    res["staged"] = merged


def merge_base(a, b):
    """2 コミットの merge-base (hex)。無ければ None。"""
    r = repo()
    try:
        oa = peel_commit(r.revparse_single(a))
        ob = peel_commit(r.revparse_single(b))
        if oa is None or ob is None:
            return None
        mid = r.merge_base(oa.id, ob.id)
        return str(mid) if mid else None
    except (KeyError, ValueError, pygit2.GitError):
        return None


def commit_parents(h):
    """親コミット hex のリスト。"""
    try:
        c = repo()[h]
        if not isinstance(c, pygit2.Commit):
            c = peel_commit(c)
        return [str(p) for p in c.parent_ids] if c else []
    except (KeyError, ValueError, pygit2.GitError):
        return []


def path_in_tree(commit_hex, path):
    """commit の tree に path の blob があるか。"""
    kind, _ = blob_at(f"{commit_hex}:{path}")
    return kind == "blob"



def _patch_status_char(patch):
    try:
        return patch.delta.status_char()
    except Exception:
        return "M"


def _patch_paths(patch):
    d = patch.delta
    new_p = d.new_file.path if d.new_file else ""
    old_p = d.old_file.path if d.old_file else ""
    st = _patch_status_char(patch)
    if st in ("R", "C") and old_p and old_p != new_p:
        return new_p or old_p, old_p, st
    path = new_p or old_p
    return path, None, st


def _patch_numstat(patch):
    """(added, deleted) — バイナリは (None, None)。"""
    if patch.delta.is_binary:
        return None, None
    try:
        _ctx, added, deleted = patch.line_stats
        return int(added), int(deleted)
    except Exception:
        return 0, 0


def diff_name_numstat_trees(old_treeish, new_treeish=None, paths=None):
    """2 つの tree-ish（または new=None で workdir）の差分。
    new_treeish is None → workdir との diff。
    new_treeish == \":index\" → index との diff。
    戻り値: (letters, numstat_list) — diff_name_numstat と同じ形。
    """
    r = repo()
    try:
        old_obj = r.revparse_single(old_treeish)
        old_tree = old_obj.peel(pygit2.GIT_OBJECT_TREE)
    except (KeyError, ValueError, pygit2.GitError):
        return {}, []

    try:
        if new_treeish is None:
            diff = r.diff(old_tree)
        elif new_treeish == ":index":
            diff = r.index.diff_to_tree(old_tree)
        else:
            new_obj = r.revparse_single(new_treeish)
            new_tree = new_obj.peel(pygit2.GIT_OBJECT_TREE)
            diff = r.diff(old_tree, new_tree)
        try:
            diff.find_similar()
        except pygit2.GitError:
            pass
    except (KeyError, ValueError, pygit2.GitError):
        return {}, []

    path_filter = set(paths) if paths else None
    letters, files = {}, []
    for patch in diff:
        path, old_path, st = _patch_paths(patch)
        if not path:
            continue
        if path_filter is not None and path not in path_filter and (not old_path or old_path not in path_filter):
            continue
        letters[path] = (st, old_path)
        added, deleted = _patch_numstat(patch)
        files.append({"path": path, "old_path": old_path, "added": added, "deleted": deleted})
    return letters, files


def diff_index_workdir(paths=None):
    """index → workdir の numstat リスト。"""
    r = repo()
    try:
        diff = r.index.diff_to_workdir()
        try:
            diff.find_similar()
        except pygit2.GitError:
            pass
    except pygit2.GitError:
        return []
    path_filter = set(paths) if paths else None
    files = []
    for patch in diff:
        path, old_path, st = _patch_paths(patch)
        if not path:
            continue
        if path_filter is not None and path not in path_filter:
            continue
        added, deleted = _patch_numstat(patch)
        files.append({"path": path, "old_path": old_path, "added": added, "deleted": deleted, "st": st})
    return files


def blame_lines(path, commit_hex=None):
    """[{hash, line, author, date, content}, ...]
    commit_hex が None のときは HEAD。作業ツリー blame は未対応（None を返す）。
    """
    from datetime import datetime, timezone, timedelta
    r = repo()
    try:
        if commit_hex:
            c = peel_commit(r.revparse_single(commit_hex))
        else:
            c = peel_commit(r.head.peel(pygit2.GIT_OBJECT_COMMIT))
        if c is None:
            return None
        blame = r.blame(path, newest_commit=c.id)
    except (KeyError, ValueError, pygit2.GitError):
        return None

    # ファイル内容
    try:
        entry = c.tree[path]
        blob = r[entry.id]
        if not isinstance(blob, pygit2.Blob):
            return None
        data = blob.data
    except (KeyError, ValueError, pygit2.GitError):
        return None
    from .textcodec import decode_text
    text, _ = decode_text(data)
    if text is None:  # バイナリ
        return None
    # git は \n だけを行区切りにする (str.splitlines は \f, \x0b, \x85, \u2028 なども区切るので行番号がずれる)
    content_lines = text.split("\n")
    if content_lines and content_lines[-1] == "":
        content_lines.pop()
    content_lines = [ln[:-1] if ln.endswith("\r") else ln for ln in content_lines]

    out = []
    meta = {}  # commit -> (author, date)
    for hunk in blame:
        oid = str(hunk.final_commit_id)
        if oid not in meta:
            # zero oid = not committed (shouldn't happen for commit blame)
            try:
                hc = r[hunk.final_commit_id]
                author = hc.author.name if hc.author else ""
                ts = hc.author.time
                off = hc.author.offset  # minutes
                date = datetime.fromtimestamp(ts, timezone(timedelta(minutes=off))).date().isoformat()
            except (KeyError, ValueError, pygit2.GitError):
                author, date = "", "1970-01-01"
            meta[oid] = (author, date)
        author, date = meta[oid]
        start = hunk.final_start_line_number  # 1-based
        for i in range(hunk.lines_in_hunk):
            line_no = start + i
            content = content_lines[line_no - 1] if 0 < line_no <= len(content_lines) else ""
            out.append({
                "hash": oid,
                "line": line_no,
                "author": author,
                "date": date,
                "content": content,
            })
    return out





def unified_diff(a=None, b=None, *, cached=False, paths=None, binary=False, context_lines=3):
    """unified diff を bytes で返す。

    a/b の意味は pygit2.Repository.diff に合わせる:
      unified_diff()              — index vs workdir
      unified_diff("HEAD")        — HEAD vs workdir
      unified_diff("HEAD", cached=True) — HEAD vs index
      unified_diff(c1, c2)        — 2 コミット間
    paths: 含めるパスのリスト（指定時はそれ以外を落とす）。
    """
    r = repo()
    if cached:
        try:
            r.index.read()
        except pygit2.GitError:
            pass
    flags = pygit2.enums.DiffOption.NORMAL
    if binary:
        flags |= pygit2.enums.DiffOption.SHOW_BINARY
    try:
        if a is None and b is None and cached:
            a = "HEAD"
        diff = r.diff(a, b, cached=cached, flags=flags, context_lines=context_lines)
        try:
            diff.find_similar()
        except pygit2.GitError:
            pass
    except (KeyError, ValueError, pygit2.GitError) as e:
        raise ValueError(str(e) or "diff failed") from e

    want = set(paths) if paths else None
    parts = []
    for patch in diff:
        if want is not None:
            d = patch.delta
            new_p = d.new_file.path if d.new_file else ""
            old_p = d.old_file.path if d.old_file else ""
            if new_p not in want and old_p not in want:
                continue
        data = patch.data
        if data:
            parts.append(data if isinstance(data, (bytes, bytearray)) else data.encode("utf-8", "replace"))
    return b"".join(parts)


def unified_diff_no_index(path):
    """未追跡ファイルを空 blob からの new file diff として返す。"""
    r = repo()
    workdir = r.workdir or (state.REPO + os.sep)
    full = os.path.join(workdir, path)
    try:
        with open(full, "rb") as f:
            data = f.read()
    except OSError:
        return b""
    empty = r.create_blob(b"")
    blob = r.create_blob(data)
    try:
        p = pygit2.Patch.create_from(
            r[empty], r[blob], old_as_path=path, new_as_path=path
        )
        out = p.data
        return out if isinstance(out, (bytes, bytearray)) else out.encode("utf-8", "replace")
    except (KeyError, ValueError, pygit2.GitError):
        return b""





def is_ancestor(maybe_ancestor, commit):
    """maybe_ancestor が commit の祖先か (同一含む)。"""
    r = repo()
    try:
        a = peel_commit(r.revparse_single(maybe_ancestor))
        c = peel_commit(r.revparse_single(commit))
        if a is None or c is None:
            return False
        return r.descendant_of(c.id, a.id) or a.id == c.id
    except (KeyError, ValueError, pygit2.GitError):
        return False


def first_parent_chain(tip, stop_at=None):
    """tip から first-parent をたどる [hex, ...]（tip が先、新しい順）。
    stop_at に達したらそこで止める（stop_at 自身は含めない）。"""
    r = repo()
    try:
        c = peel_commit(r.revparse_single(tip))
    except (KeyError, ValueError, pygit2.GitError):
        return []
    stop = None
    if stop_at:
        try:
            s = peel_commit(r.revparse_single(stop_at))
            stop = s.id if s else None
        except (KeyError, ValueError, pygit2.GitError):
            stop = None
    out = []
    seen = set()
    while c is not None:
        cid = c.id
        if stop is not None and cid == stop:
            break
        h = str(cid)
        if h in seen:
            break
        seen.add(h)
        out.append(h)
        if not c.parents:
            break
        c = c.parents[0]
    return out


def first_parent_path_from_base(base, tip):
    """base..tip の first-parent 経路を古い順 (base の次〜tip) で返す。"""
    chain = first_parent_chain(tip, stop_at=base)
    chain.reverse()
    return chain


def commits_equal_trees(a, b):
    """2 コミットの tree が同一か。"""
    r = repo()
    try:
        ca = peel_commit(r.revparse_single(a))
        cb = peel_commit(r.revparse_single(b))
        if ca is None or cb is None:
            return False
        return ca.tree_id == cb.tree_id
    except (KeyError, ValueError, pygit2.GitError):
        return False


def list_tree(treeish, path=""):
    """[(name, kind, size_or_None), ...] kind is 'tree'|'blob'|'commit'|'link'
    path は tree 内のサブパス。"""
    r = repo()
    try:
        obj = r.revparse_single(treeish)
        commit = peel_commit(obj)
        tree = commit.tree if commit is not None else obj.peel(pygit2.GIT_OBJECT_TREE)
        if path:
            entry = tree[path]
            obj2 = r[entry.id]
            if isinstance(obj2, pygit2.Tree):
                tree = obj2
            else:
                return []
        rows = []
        for e in tree:
            kind = "blob"
            size = None
            if e.type == pygit2.GIT_OBJECT_TREE:
                kind = "tree"
            elif e.type == pygit2.GIT_OBJECT_COMMIT:
                kind = "commit"
            elif e.type == pygit2.GIT_OBJECT_BLOB:
                kind = "blob"
                try:
                    size = r[e.id].size
                except Exception:
                    size = None
            # symlink mode
            if e.filemode == pygit2.GIT_FILEMODE_LINK:
                kind = "link"
            rows.append((e.name, kind, size))
        return rows
    except (KeyError, ValueError, pygit2.GitError, TypeError):
        return None  # signal failure → CLI fallback


def tree_paths(treeish):
    """コミット/tree の全 blob パス集合。"""
    r = repo()
    try:
        obj = r.revparse_single(treeish)
        c = peel_commit(obj)
        tree = c.tree if c is not None else obj.peel(pygit2.GIT_OBJECT_TREE)
    except (KeyError, ValueError, pygit2.GitError):
        return set()
    out = set()
    def walk(t, prefix):
        for e in t:
            p = f"{prefix}{e.name}" if not prefix else f"{prefix}/{e.name}"
            if e.type == pygit2.GIT_OBJECT_TREE:
                walk(r[e.id], p)
            elif e.type == pygit2.GIT_OBJECT_BLOB:
                out.add(p)
    walk(tree, "")
    return out


def log_commits(tips, skip=0, limit=500, topo=False, path=None):
    """[{hash, short, author, date, subject, parents}, ...]
    path 指定時は None を返して CLI に任せる。
    """
    if path:
        return None
    r = repo()
    sort = pygit2.GIT_SORT_TIME
    if topo:
        sort = pygit2.GIT_SORT_TOPOLOGICAL | pygit2.GIT_SORT_TIME
    walker = r.walk(None, sort)
    pushed = False
    for tip in tips:
        try:
            c = peel_commit(r.revparse_single(tip))
            if c is None:
                continue
            walker.push(c.id)
            pushed = True
        except (KeyError, ValueError, pygit2.GitError):
            continue
    if not pushed:
        return []
    from datetime import datetime, timezone, timedelta
    rows = []
    n = 0
    for c in walker:
        if n < skip:
            n += 1
            continue
        if len(rows) >= limit:
            break
        n += 1
        off = c.author.offset if c.author else 0
        try:
            date = datetime.fromtimestamp(c.commit_time, timezone(timedelta(minutes=off))).date().isoformat()
        except Exception:
            date = ""
        msg = (c.message or "").split("\n", 1)[0]
        h = str(c.id)
        rows.append({
            "hash": h,
            "short": h[:7],
            "author": c.author.name if c.author else "",
            "date": date,
            "subject": msg,
            "parents": [str(p) for p in c.parent_ids],
        })
    return rows


def count_commits(tips, path=None):
    if path:
        return None
    r = repo()
    walker = r.walk(None, pygit2.GIT_SORT_TIME)
    for tip in tips:
        try:
            c = peel_commit(r.revparse_single(tip))
            if c:
                walker.push(c.id)
        except (KeyError, ValueError, pygit2.GitError):
            continue
    return sum(1 for _ in walker)



__all__ = [
    "EMPTY_TREE", "available", "blob_at", "commit_message", "commit_parents", "commit_subjects",
    "git_dir_path", "head_commit_and_branch", "list_ref_infos", "list_stashes", "merge_base",
    "path_in_tree", "peel_commit", "repo", "require_pygit2", "reset_cache", "resolve_commit",
    "rev_list_topo_parents", "is_plain_rev", "refs_key", "config_get", "upstream_of_head", "remotes_list", "refs_snapshot",
    "has_conflicts", "index_contains", "paths_in_tree", "read_head", "stash_key", "status_lists",
    "diff_name_numstat_trees", "diff_index_workdir", "blame_lines",
    "unified_diff", "unified_diff_no_index",
    "is_ancestor", "first_parent_chain", "first_parent_path_from_base",
    "commits_equal_trees", "list_tree", "tree_paths", "log_commits", "count_commits",
]
