# git 操作エンジン。
#
# リポジトリを変更する POST API はすべて server.py の do_POST から engine.run() を通る。
# エンジンは操作の前後で「リポジトリの状態」(ref / HEAD / stash 一覧 / ローカル config / 作業ツリーと index)
# を記録し、その差分を履歴に積む。元に戻す (undo) はこの差分を逆向きに適用するだけなので、
# 操作ごとに逆操作を書く必要がない。やり直し (redo) は同じ差分を順方向に適用する。
#
# - 競合などで途中停止した merge / rebase / cherry-pick / revert は、完了または中断されるまでを1つの操作として扱う。
# - push / push-tag / リモートブランチ削除などリモート側を変える操作は履歴に積まない (戻せないため)。
# - 履歴はメモリ上だけに持つ (サーバーを再起動すると消える)。
# - 作業ツリーは「追跡ファイルの内容」だけを退避する (変更のあるファイルの blob と小さなコミット1つ。サイズは変更量に比例)。
#   管理外ファイルの中身と index は退避しない。管理外ファイルは名前だけ記録し、元に戻すときに消してよいファイルの判定に使う。
#   退避した blob / tree は .git/objects ではなく一時ディレクトリ (GIT_OBJECT_DIRECTORY) に書き、本物の objects は
#   GIT_ALTERNATE_OBJECT_DIRECTORIES で読み取り専用に見せる。.git には何も残さない (ref も作らない)。
#   一時ディレクトリは起動時・終了時に cleanup() で消す。
#   作業ツリーが操作後に変わっているのを確認のうえ上書きして戻すときは、上書きされる現在の状態を履歴側の
#   「反対向きの行き先」にする (元に戻す → やり直しで、上書きされた状態に戻れる)。ref も退避ファイルも作らない。
import os
import shutil
import subprocess
import time
from . import app_state as state
from . import tmpdir
from .gitrun import HASH_RE
from .gitstate import is_cherry_picking, is_merging, is_rebasing, is_reverting
from .i18n import tr, UserError
from .routing import route

HIST_LIMIT = 30                       # 残す履歴の数 (古いものから捨てる)
UNTRACKED_NAMES_LIMIT = 5000          # 管理外ファイルの名前をこれ以上記録しない (超えたら、元に戻すときに何も消さない)

# 書き込み API ごとの扱い: パス -> (操作名, 作業ツリーに触れるか)。作業ツリーに触れるかは False / True /
# "names" (管理外ファイルが追跡対象になりうる操作。管理外ファイルの名前も記録する。コミットなど)。
# 履歴に積まない API は IGNORED に入れる。どちらにも無い POST API は、安全側に倒して「作業ツリーに触れる操作」とみなす
# (新しい書き込み API を足したらここへ登録すること。tests が登録漏れを検出する)。
WRITE_OPS = {
    "/api/merge": ("merge", True),
    "/api/rebase": ("rebase", True),
    "/api/rebase-range": ("rebase", True),
    "/api/rebase-descendants": ("rebase", True),
    "/api/rebase-equivalent": ("rebase", True),
    "/api/rebase-i": ("rebase_i", True),
    "/api/cherry-pick": ("cherry_pick", True),
    "/api/revert": ("revert", True),
    "/api/abort": ("abort", True),
    "/api/resolve": ("resolve", "names"),
    "/api/stash": ("stash", True),
    "/api/stash-push": ("stash_push", True),
    "/api/branch": ("branch", False),
    "/api/tag": ("tag", False),
    "/api/ref-delete": ("delete_ref", False),
    "/api/branch-rename": ("branch_rename", False),
    "/api/checkout": ("checkout", True),
    "/api/reset": ("reset", True),
    "/api/discard": ("discard", True),
    "/api/rm": ("rm", True),
    "/api/wip-commit": ("commit", "names"),
    "/api/fetch": ("fetch", False),
    "/api/pull": ("pull", True),
    "/api/remote-add": ("remote_add", False),
    "/api/remote-remove": ("remote_remove", False),
    "/api/patch-apply": ("patch_apply", "names"),
    "/api/patch-am": ("patch_am", True),
    "/api/file-save": ("file_save", True),
}
IGNORED = {
    "/api/push", "/api/push-tag",                          # リモート側を変えるので戻せない
    "/api/shutdown", "/api/restart",
    "/api/open-editor", "/api/open-diff", "/api/file-copy",  # リポジトリを変更しない
    "/api/find-equivalent-parents", "/api/find-equivalent-branch",  # POST だが読み取りだけ
    "/api/undo", "/api/redo",
}
# 操作名 -> 履歴に出す文言の翻訳キー
LABEL_KEYS = {
    "merge": "op_label_merge", "rebase": "op_label_rebase", "rebase_i": "op_label_rebase_i",
    "cherry_pick": "op_label_cherry_pick", "revert": "op_label_revert", "abort": "op_label_abort",
    "resolve": "op_label_resolve", "stash": "op_label_stash", "stash_push": "op_label_stash_push",
    "branch": "op_label_branch", "tag": "op_label_tag", "delete_ref": "op_label_delete_ref",
    "branch_rename": "op_label_branch_rename", "checkout": "op_label_checkout", "reset": "op_label_reset",
    "discard": "op_label_discard", "rm": "op_label_rm", "commit": "op_label_commit", "fetch": "op_label_fetch",
    "pull": "op_label_pull", "remote_add": "op_label_remote_add", "remote_remove": "op_label_remote_remove",
    "patch_apply": "op_label_patch_apply", "patch_am": "op_label_patch_am", "file_save": "op_label_file_save",
}


PROBLEM_KEYS = {"snapshot_incomplete": "undo_reason_snapshot_incomplete"}


def write_op_for(path, body):
    """(操作名, 作業ツリーに触れるか) を返す。履歴に積まない操作は None。"""
    if path in IGNORED:
        return None
    if path == "/api/ref-delete" and isinstance(body, dict) and body.get("kind") == "remote-branch":
        return None  # git push --delete: リモート側が変わるので戻せない
    return WRITE_OPS.get(path, ("other", "names"))


# ---------------------------------------------------------------- git の低レベル呼び出し

_SCRATCH = {}  # リポジトリのパス -> 退避したオブジェクトを書き込む一時ディレクトリ


def _object_env():
    """エンジンが作るオブジェクトの書き込み先を一時ディレクトリにする環境変数。本物の objects は読み取り専用の代替として見せる。"""
    if state.REPO not in _SCRATCH:
        real = _git_path("objects")
        alternates = [real]
        try:  # 元のリポジトリ自身が代替 objects (--reference や --shared) を使っているときは、それも見せる
            with open(os.path.join(real, "info", "alternates"), encoding="utf-8") as f:
                alternates += [os.path.normpath(os.path.join(real, ln.strip())) for ln in f if ln.strip() and not ln.startswith("#")]
        except OSError:
            pass
        _SCRATCH[state.REPO] = (tmpdir.make_temp_dir("objects"), os.pathsep.join(alternates))
    scratch, alternates = _SCRATCH[state.REPO]
    return {"GIT_OBJECT_DIRECTORY": scratch, "GIT_ALTERNATE_OBJECT_DIRECTORIES": alternates}


def _run(args, *, env=None, input=None, check=True):
    """git を実行する。書き込まれるオブジェクトは一時ディレクトリに入り、.git/objects には入らない。"""
    env = dict(os.environ if env is None else env, **_object_env())
    r = subprocess.run(["git", "-C", state.REPO, *args], capture_output=True, input=input, env=env)
    if check and r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).decode("utf-8", errors="replace").strip() or "git failed")
    return r


def _text(args, **kw):
    return _run(args, **kw).stdout.decode("utf-8", errors="replace")


_GIT_PATHS = {}


def _git_path(name):
    key = (state.REPO, name)
    if key not in _GIT_PATHS:
        # _run を通さない: _run は _object_env を呼び、_object_env はこのパスを使うので、通すと再帰する (何も書かないので不要)
        p = subprocess.run(["git", "-C", state.REPO, "rev-parse", "--git-path", name],
                           capture_output=True, text=True, check=True).stdout.strip()
        _GIT_PATHS[key] = p if os.path.isabs(p) else os.path.join(state.REPO, p)
    return _GIT_PATHS[key]


def in_progress():
    return is_merging() or is_rebasing() or is_cherry_picking() or is_reverting()


# ---------------------------------------------------------------- 状態の記録

def _untracked_names():
    """管理外ファイルの名前 (中身は読まない)。多すぎるときは None。"""
    names = []
    for p in _run(["ls-files", "-o", "--exclude-standard", "-z"]).stdout.split(b"\0"):
        if p:
            names.append(os.fsdecode(p))
            if len(names) > UNTRACKED_NAMES_LIMIT:
                return None
    return names


def _worktree_snapshot(names=False):
    """作業ツリー上の追跡ファイルの内容を木 (tree) にして退避する。gc されないよう、記録するときに ref で守る。
    names が真なら管理外ファイルの名前も記録する (偽なら「管理外ファイルは追跡対象にならない」とみなして空)。
    本物の index は触らず、コピーした一時 index に add -u して木を作る (書き込まれるのは変更のあるファイルだけ)。"""
    tmp = tmpdir.make_temp_dir("snap")
    try:
        env = dict(os.environ, GIT_INDEX_FILE=os.path.join(tmp, "index"))
        real_index = _git_path("index")
        if os.path.exists(real_index):
            shutil.copyfile(real_index, env["GIT_INDEX_FILE"])
        else:
            _run(["read-tree", "--empty"], env=env)
        incomplete = _run(["add", "-u"], env=env, check=False).returncode != 0
        tree = _text(["write-tree"], env=env).strip()
        return {"tree": tree, "untracked": _untracked_names() if names else [],
                "problem": "snapshot_incomplete" if incomplete else None}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def take(with_worktree):  # with_worktree: False / True / "names"
    """現在の状態を辞書にする。with_worktree が偽、または作業ツリーが無い / 操作の途中なら wt は None。"""
    snap = {"refs": {}, "head": None, "stash": [], "config": {}, "wt": None}
    for line in _text(["for-each-ref", "--format=%(objectname) %(refname)"]).splitlines():
        sha, _, ref = line.partition(" ")
        if ref == "refs/stash":  # stash は一覧 (reflog) で扱う
            continue
        snap["refs"][ref] = sha
    r = _run(["symbolic-ref", "-q", "HEAD"], check=False)
    if r.returncode == 0:
        snap["head"] = {"ref": r.stdout.decode("utf-8", errors="replace").strip()}
    else:
        r = _run(["rev-parse", "-q", "--verify", "HEAD"], check=False)
        if r.returncode == 0:
            snap["head"] = {"sha": r.stdout.decode().strip()}
    if _run(["rev-parse", "-q", "--verify", "refs/stash"], check=False).returncode == 0:
        for line in _text(["reflog", "show", "--format=%H%x1f%gs", "refs/stash"]).splitlines():
            sha, _, subject = line.partition("\x1f")
            snap["stash"].append([sha, subject])
    for item in _run(["config", "--local", "--list", "-z"], check=False).stdout.decode("utf-8", errors="replace").split("\0"):
        if item:
            key, _, value = item.partition("\n")
            snap["config"].setdefault(key, []).append(value)
    if with_worktree and state.HAS_WORKTREE:
        snap["wt"] = _worktree_snapshot(with_worktree == "names")
    return snap


def _slim_worktree(bw, aw):
    """履歴に残す退避情報。管理外ファイルの名前は、操作で追跡対象になった / 追跡対象から外れたものだけ残す
    (元に戻すときに、それらを消さないための印。記録が多すぎて取れなかったときは None = 何も消さない)。"""
    added, removed = set(), set()
    out = _run(["diff-tree", "-r", "-z", "--no-renames", "--name-status", bw["tree"], aw["tree"]]).stdout.split(b"\0")
    for i in range(0, len(out) - 1, 2):
        (removed if out[i] == b"D" else added).add(os.fsdecode(out[i + 1]))

    def protect(untracked, paths):
        return None if untracked is None else sorted(paths & set(untracked))
    return [{"tree": bw["tree"], "protect": protect(bw["untracked"], added)},
            {"tree": aw["tree"], "protect": protect(aw["untracked"], removed)}]


def _make_entry(before, after, meta):
    """前後の状態の差分を履歴1件にする。何も変わっていなければ None。"""
    refs = {r: [before["refs"].get(r), after["refs"].get(r)]
            for r in set(before["refs"]) | set(after["refs"])
            if before["refs"].get(r) != after["refs"].get(r)}
    config = {k: [before["config"].get(k), after["config"].get(k)]
              for k in set(before["config"]) | set(after["config"])
              if before["config"].get(k) != after["config"].get(k)}
    head = [before["head"], after["head"]] if before["head"] != after["head"] else None
    stash = [before["stash"], after["stash"]] if before["stash"] != after["stash"] else None
    wt = None
    bw, aw = before["wt"], after["wt"]
    if bw and aw and bw["tree"] != aw["tree"]:
        wt = _slim_worktree(bw, aw)
    if not (refs or config or head or stash or wt):
        return None
    problem = next((w["problem"] for w in (bw, aw) if w and w["problem"]), None)
    return {"id": f"{int(time.time() * 1000):x}", "ts": int(time.time()), "op": meta["op"], "detail": meta["detail"],
            "refs": refs, "config": config, "head": head, "stash": stash, "wt": wt,
            "undoable": problem is None, "problem": problem}


# ---------------------------------------------------------------- 履歴の保存

_HISTORY = {}  # リポジトリのパス -> 履歴 (メモリ上だけ)


def _load():
    return _HISTORY.setdefault(state.REPO, {"cursor": 0, "entries": [], "pending": None})


def cleanup():
    """履歴と、退避したオブジェクトの一時ディレクトリを消す。起動時と終了時に呼ぶ。"""
    _HISTORY.pop(state.REPO, None)
    scratch = _SCRATCH.pop(state.REPO, None)
    if scratch:
        shutil.rmtree(scratch[0], ignore_errors=True)


def _push(h, entry):
    del h["entries"][h["cursor"]:]  # undo 済みの先 (redo できたもの) は捨てる
    h["entries"].append(entry)
    del h["entries"][:-HIST_LIMIT]
    h["cursor"] = len(h["entries"])


def _detail(body):
    if isinstance(body, dict):
        for k in ("name", "branch", "tag", "remote", "new_name", "hash"):
            v = body.get(k)
            if isinstance(v, str) and v:
                return v[:7] if HASH_RE.fullmatch(v) else v[:60]
    return ""


# ---------------------------------------------------------------- 実行

def run(path, handler, body):
    """書き込み API の handler を実行し、状態の変化を履歴に積む。履歴の記録に失敗しても操作そのものは妨げない。"""
    spec = write_op_for(path, body)
    if spec is None or getattr(state.OPTS, "no_undo", False):
        return handler(body)
    op, touches_wt = spec
    try:
        hist = _load()
        busy_before = in_progress()
        before = take(False if busy_before else touches_wt)  # 途中状態では index が競合を含むので、作業ツリーは退避しない
    except Exception:
        return handler(body)
    error = None
    try:
        result = handler(body)
    except BaseException as e:  # 途中まで変更された状態も履歴に積むため、いったん受ける
        error = e
    try:
        _finish(hist, {"op": op, "detail": _detail(body), "touches_wt": touches_wt}, busy_before, before)
    except Exception:
        pass
    if error is not None:
        raise error
    return result


def _finish(hist, meta, busy_before, before):
    pending = hist["pending"]
    if busy_before:  # 競合解決などの続き: 最初の操作の前の状態を基準にする
        if pending is None:
            return  # このエンジンを通さずに始まった操作
        base, meta = pending["before"], pending["meta"]
    else:
        base = before
    if in_progress():  # まだ途中 (競合で停止中): 完了か中断までは履歴に積まない
        if not busy_before:
            hist["pending"] = {"before": before, "meta": meta}
        return
    after = take(meta["touches_wt"])
    hist["pending"] = None
    entry = _make_entry(base, after, meta)
    if entry:
        _push(hist, entry)


# ---------------------------------------------------------------- undo / redo

def _expect(entry, cur, e, force):
    """現在の状態が、この操作が終わった直後 (undo のとき) / 始まる前 (redo のとき) のままか確認する。"""
    if in_progress():
        raise UserError("undo_in_progress")
    for ref, pair in entry["refs"].items():
        if cur["refs"].get(ref) != pair[e]:
            raise UserError("undo_state_changed", what=ref)
    if entry["head"] and cur["head"] != entry["head"][e]:
        raise UserError("undo_state_changed", what="HEAD")
    if entry["stash"] and cur["stash"] != entry["stash"][e]:
        raise UserError("undo_state_changed", what="stash")
    for key, pair in entry["config"].items():
        if cur["config"].get(key) != pair[e]:
            raise UserError("undo_state_changed", what=key)
    if entry["wt"] and not force:
        now = _worktree_snapshot()
        want = entry["wt"][e]
        if now["tree"] != want["tree"]:
            raise UserError("undo_worktree_changed")


def _restore_worktree(target):
    """追跡ファイルの内容を target の退避に合わせる。管理外ファイルには触れない:
    退避に無く現在あるファイルは、退避時点で管理外だったもの (protect に載っているもの) と、記録が無いときは消さない。"""
    cur = _worktree_snapshot()
    keep = set(target["protect"]) if target.get("protect") is not None else None
    changed, removed = [], []
    out = _run(["diff-tree", "-r", "-z", "--no-renames", "--name-status", cur["tree"], target["tree"]]).stdout.split(b"\0")
    for i in range(0, len(out) - 1, 2):
        status, path = out[i].decode(), os.fsdecode(out[i + 1])
        if status == "D":
            if keep is not None and path not in keep:
                removed.append(path)
        else:
            changed.append(path)
    root = os.path.realpath(state.REPO)
    for path in removed:
        full = os.path.realpath(os.path.join(state.REPO, path))
        if not full.startswith(root + os.sep):
            continue
        try:
            os.remove(full)
            d = os.path.dirname(full)
            while d != root and not os.listdir(d):  # 空になったディレクトリも片付ける
                os.rmdir(d)
                d = os.path.dirname(d)
        except OSError:
            pass
    if changed:  # 退避した内容を一時 index 経由で書き出す (フィルターや実行権限も通常のチェックアウトと同じ)
        tmp = tmpdir.make_temp_dir("restore")
        try:
            env = dict(os.environ, GIT_INDEX_FILE=os.path.join(tmp, "index"))
            _run(["read-tree", target["tree"]], env=env)
            _run(["checkout-index", "-f", "-z", "--stdin"], env=env, input=b"\0".join(os.fsencode(p) for p in changed) + b"\0")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def _stash_untracked(entry):
    """stash push で stash の第3親 (管理外ファイル) に入った {パス: blob} を、push で増えた stash ごとに集める。"""
    before = {sha for sha, _ in entry["stash"][0]}
    files = {}
    for sha, _ in entry["stash"][1]:
        if sha in before:
            continue
        r = _run(["ls-tree", "-r", "-z", sha + "^3"], check=False)
        for rec in r.stdout.split(b"\0") if r.returncode == 0 else []:
            meta, _, path = rec.partition(b"\t")
            if path:
                files[os.fsdecode(path)] = meta.split()[2].decode()
    return files


def _restore_stash_untracked(entry, t):
    """stash push の元に戻す / やり直し。stash は管理外ファイルも作業ツリーから持ち去るので、
    元に戻すときは stash の中から書き戻し (既にあるファイルは上書きしない)、やり直すときは内容が同じものだけ消す。"""
    files = _stash_untracked(entry)
    if not files:
        return
    root = os.path.realpath(state.REPO)
    if t == 0:
        shas = [sha for sha, _ in entry["stash"][1] if sha not in {x for x, _ in entry["stash"][0]}]
        tmp = tmpdir.make_temp_dir("restore")
        try:
            env = dict(os.environ, GIT_INDEX_FILE=os.path.join(tmp, "index"))
            for sha in shas:
                if _run(["rev-parse", "-q", "--verify", sha + "^3"], check=False).returncode != 0:
                    continue
                _run(["read-tree", sha + "^3"], env=env)
                paths = [p for p in files if not os.path.lexists(os.path.join(state.REPO, p))]
                if paths:
                    _run(["checkout-index", "-z", "--stdin"], env=env, input=b"\0".join(os.fsencode(p) for p in paths) + b"\0")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return
    for path, blob in files.items():
        full = os.path.realpath(os.path.join(state.REPO, path))
        if not full.startswith(root + os.sep) or not os.path.isfile(full):
            continue
        if _text(["hash-object", "--", full]).strip() == blob:
            os.remove(full)


def _apply(entry, t, cur):
    """状態を entry の t 側 (0: 操作前 / 1: 操作後) にそろえる。"""
    if entry["head"] and entry["head"][t]:
        h = entry["head"][t]
        if "ref" in h:
            _run(["symbolic-ref", "HEAD", h["ref"]])
        else:
            _run(["update-ref", "--no-deref", "HEAD", h["sha"]])
    deletes = "".join(f"delete {r}\n" for r, pair in entry["refs"].items() if pair[t] is None)
    updates = "".join(f"update {r} {pair[t]}\n" for r, pair in entry["refs"].items() if pair[t] is not None)
    if deletes:  # ディレクトリ/ファイルの衝突を避けるため、削除を先に別のトランザクションで行う
        _run(["update-ref", "--stdin"], input=deletes.encode())
    if updates:
        _run(["update-ref", "--stdin"], input=updates.encode())
    if entry["stash"]:
        _run(["update-ref", "-d", "refs/stash"], check=False)  # reflog ごと消える
        for sha, subject in reversed(entry["stash"][t]):       # 古いものから積み直す
            _run(["stash", "store", "-m", subject, sha])
    for key, pair in entry["config"].items():
        if key.startswith("-"):
            continue
        _run(["config", "--local", "--unset-all", key], check=False)
        for v in pair[t] or []:
            _run(["config", "--local", "--add", key, v])
    if entry["op"] == "stash_push" and entry["stash"]:
        _restore_stash_untracked(entry, t)
    if entry["wt"]:
        _restore_worktree(entry["wt"][t])
        if _run(["reset", "-q"], check=False).returncode != 0:  # index は HEAD に合わせる (ステージ状態は戻さない)
            _run(["read-tree", "--empty"], check=False)


def _move(direction, force):
    h = _load()
    if h["pending"]:
        raise UserError("undo_in_progress")
    if direction < 0:
        if h["cursor"] == 0:
            raise UserError("undo_nothing")
        entry, t, e = h["entries"][h["cursor"] - 1], 0, 1
        if not entry["undoable"]:
            raise UserError("undo_irreversible", reason=_problem_text(entry.get("problem")))
    else:
        if h["cursor"] >= len(h["entries"]):
            raise UserError("redo_nothing")
        entry, t, e = h["entries"][h["cursor"]], 1, 0
    cur = take(False)
    _expect(entry, cur, e, force)
    overwritten = False
    if force and entry["wt"]:  # 上書きされる作業ツリーは、反対向き (やり直し / 元に戻す) の行き先にして、あとから戻せるようにする
        now = _worktree_snapshot()
        overwritten = now["tree"] != entry["wt"][e]["tree"]
    _apply(entry, t, cur)
    if overwritten:
        entry["wt"][e] = {"tree": now["tree"], "protect": entry["wt"][e]["protect"]}
    h["cursor"] += direction
    return entry, overwritten


def _problem_text(problem):
    return tr(PROBLEM_KEYS[problem]) if problem in PROBLEM_KEYS else ""


def _label(entry):
    label = tr(LABEL_KEYS.get(entry["op"], "op_label_other"))
    return f"{label} {entry['detail']}".strip()


@route("/api/undo-history")
def api_undo_history(query):
    if getattr(state.OPTS, "no_undo", False):  # 記録していないので、履歴も元に戻す先も無い
        return {"entries": [], "can_undo": False, "can_redo": False, "next_undo": "", "next_redo": "", "pending": False}
    h = _load()
    writable = bool(getattr(state, "WRITABLE", True))  # 読み取り専用のときは実行できない
    items = []
    for i, e in enumerate(h["entries"]):
        items.append({"id": e["id"], "label": _label(e), "time": e["ts"], "done": i < h["cursor"],
                      "undoable": e["undoable"], "problem": _problem_text(e.get("problem"))})
    cur = h["cursor"]
    return {
        "entries": list(reversed(items)),
        "can_undo": writable and cur > 0 and h["entries"][cur - 1]["undoable"] and not h["pending"],
        "can_redo": writable and cur < len(h["entries"]) and not h["pending"],
        "next_undo": _label(h["entries"][cur - 1]) if cur > 0 else "",
        "next_redo": _label(h["entries"][cur]) if cur < len(h["entries"]) else "",
        "pending": bool(h["pending"]),
    }


@route("/api/undo", method="POST")
def api_undo(body):
    """直前の操作を元に戻す。作業ツリーが操作後から変わっているときは {worktree_changed: true} を返し、
    force=true で再実行すると上書きして戻す (上書きされる状態は「やり直し」で戻せる)。"""
    try:
        entry, overwritten = _move(-1, body.get("force") is True)
    except UserError as e:
        if e.key == "undo_worktree_changed":
            return {"worktree_changed": True, "message": str(e)}
        raise
    return {"ok": True, "message": tr("undo_done", op=_label(entry)) + (" " + tr("undo_rescued") if overwritten else "")}


@route("/api/redo", method="POST")
def api_redo(body):
    try:
        entry, overwritten = _move(1, body.get("force") is True)
    except UserError as e:
        if e.key == "undo_worktree_changed":
            return {"worktree_changed": True, "message": str(e)}
        raise
    return {"ok": True, "message": tr("redo_done", op=_label(entry)) + (" " + tr("redo_rescued") if overwritten else "")}


__all__ = ["cleanup", "IGNORED", "WRITE_OPS", "api_redo", "api_undo", "api_undo_history", "in_progress", "run", "take", "write_op_for"]
