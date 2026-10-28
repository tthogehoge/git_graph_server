# ファイルの一覧・内容・blame・作業ディレクトリへのコピーと、文字コードの判別。
import base64
import codecs
import hashlib
import os
import shutil
import tempfile
import subprocess
from datetime import datetime, timedelta, timezone
from . import app_state as state
from . import tmpdir
from .gitrun import git, git_bytes, resolve_commit
from .gitstate import CONFLICT_CODES, has_head, is_cherry_picking, is_merging, is_rebasing, is_reverting
from .i18n import tr, UserError
from .routing import route


FILE_CONTENT_LIMIT = 2_000_000
WIP_HASH = "WIP"  # /files?hash=WIP で作業ツリーのファイルを表示する


def validate_file_path(path, allow_empty=True):
    """リポジトリルートからの相対パスとして安全な形にする。"""
    if "\0" in path or (not allow_empty and not path) or path.startswith("/"):
        raise ValueError("bad path")
    parts = path.split("/") if path else []
    if any(p in ("", ".", "..") for p in parts):
        raise ValueError("bad path")
    return path


def is_wip(q):
    return q.get("hash", [""])[0] == WIP_HASH


def wip_head():
    """作業ツリー表示でファイルのログに使う HEAD のハッシュ。コミットがまだなければ None。"""
    r = subprocess.run(["git", "-C", state.REPO, "rev-parse", "--verify", "--quiet", "HEAD^{commit}"],
                       capture_output=True, text=True)
    return r.stdout.strip() or None


def worktree_target(path):
    """作業ツリー内のパスを絶対パスにする。シンボリックリンクなどでリポジトリの外に出るものは拒否する。"""
    if not state.HAS_WORKTREE:
        raise UserError("wip_files_no_worktree")
    if "\\" in path or path.split("/")[0].lower() == ".git":
        raise ValueError("bad path")
    root = os.path.realpath(state.REPO)
    target = os.path.join(root, *path.split("/")) if path else root
    parent = os.path.realpath(os.path.dirname(target)) if path else root
    try:
        inside = os.path.commonpath([root, parent]) == root
    except ValueError:  # 別ドライブなど
        inside = False
    if not inside:
        raise ValueError("bad path")
    return target


def status_mark(xy):
    """git status の XY を、一覧に出す1文字の印にする (競合 U / 未追跡 ? / 追加 A / 削除 D / リネーム R / 変更 M)。"""
    if xy == "??":
        return "?"
    if xy in CONFLICT_CODES:
        return "U"
    for mark in "ARDC":
        if mark in xy:
            return "R" if mark == "C" else mark
    return "M"


def wip_status_marks(prefix):
    """prefix 以下の変更を {パス: (印, XY)} で返す。"""
    raw = git("status", "--porcelain=v1", "-z", "--untracked-files=all", "--", prefix or ".", literal=True)
    toks, out, i = raw.split("\0"), {}, 0
    while i < len(toks):
        t = toks[i]
        i += 1
        if len(t) < 4 or t[2] != " ":
            continue
        xy, p = t[:2], t[3:]
        if "R" in xy or "C" in xy:  # リネーム/コピーは続くトークンが元のパス
            i += 1
        if xy != "!!":
            out[p] = (status_mark(xy), xy)
    return out


def wip_tree(path):
    """作業ツリーのディレクトリ一覧。git に管理されているファイルと、無視されていない未追跡ファイルを出す。"""
    target = worktree_target(path)
    if path and (os.path.islink(target) or not os.path.isdir(target)):
        raise UserError("dir_not_found")
    prefix = path + "/" if path else ""
    args = ["ls-files", "-z", "--cached", "--others", "--exclude-standard", "--full-name"]
    raw = git(*args, "--", prefix or ".", literal=True)
    names = {}
    for item in raw.split("\0"):
        if not item or not item.startswith(prefix):
            continue
        rest = item[len(prefix):]
        name, sep, _ = rest.partition("/")
        if sep:
            names[name] = "tree"
        else:
            names.setdefault(name, "blob")
    marks, dir_marks = wip_status_marks(prefix), {}
    for p, (mark, _) in marks.items():
        if not p.startswith(prefix) or "/" not in p[len(prefix):]:
            continue
        dir_marks.setdefault(p[len(prefix):].split("/", 1)[0], set()).add(mark)
    entries = []
    for name, kind in names.items():
        full = os.path.join(target, name)
        if not os.path.lexists(full):  # 削除済み (まだコミットしていない) のファイル
            continue
        size = None
        if kind == "blob":
            if os.path.isdir(full) and not os.path.islink(full):
                kind = "commit"  # サブモジュール
            else:
                size = os.lstat(full).st_size
        entry = {"name": name, "type": kind, "size": size, "mode": ""}
        if kind == "tree" and name in dir_marks:  # 中に変更があるディレクトリ: 競合 > 未追跡のみ > 変更 の順
            m = dir_marks[name]
            entry["st"] = "U" if "U" in m else "?" if m == {"?"} else "M"
        elif (prefix + name) in marks:
            entry["st"], entry["xy"] = marks[prefix + name]
        entries.append(entry)
    return {"hash": WIP_HASH, "head": wip_head(), "path": path,
            "entries": sorted(entries, key=lambda x: (x["type"] != "tree", x["name"]))}


# decode_text の文字コード名 → 保存し直すときのコーデック。BOM 付きのものは BOM を見て決める
_SAVE_CODECS = {"ASCII": "utf-8", "UTF-8": "utf-8", "ISO-2022-JP": "iso2022_jp",
                "Shift_JIS (CP932)": "cp932", "EUC-JP": "euc_jp"}
_BOM_CODECS = ((codecs.BOM_UTF8, "utf-8"), (codecs.BOM_UTF32_LE, "utf-32-le"), (codecs.BOM_UTF32_BE, "utf-32-be"),
               (codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be"))
EOLS = {"crlf": "\r\n", "lf": "\n", "cr": "\r"}


def save_codec(raw, label):
    """元のバイト列と文字コード名から (コーデック, BOM) を返す。保存し直せない文字コードなら None。"""
    for bom, codec in _BOM_CODECS:
        if raw.startswith(bom):
            return codec, bom
    codec = _SAVE_CODECS.get(label)
    return (codec, b"") if codec else None


def detect_eol(text):
    """改行コード ("crlf" / "lf" / "cr" / 改行なしは None) と、混在しているかどうか。多いものを選ぶ。"""
    crlf = text.count("\r\n")
    counts = {"crlf": crlf, "lf": text.count("\n") - crlf, "cr": text.count("\r") - crlf}
    used = [k for k, n in counts.items() if n]
    if not used:
        return None, False
    return max(used, key=lambda k: counts[k]), len(used) > 1


def encode_for_save(text, codec, bom, eol):
    """編集した文字列を、元の改行コード・文字コード (BOM も) のバイト列に戻す。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")  # ブラウザの編集欄は LF になっている
    if eol and eol != "lf":
        text = text.replace("\n", EOLS[eol])
    return bom + text.encode(codec)


def edit_info(raw, text, label, truncated):
    """作業ツリーのファイルを画面で編集できるか調べる。できなければ理由 (翻訳キー) を返す。"""
    if text is None:
        return {"editable": False, "edit_reason": "edit_binary"}
    if truncated:
        return {"editable": False, "edit_reason": "edit_too_large"}
    sc = save_codec(raw, label)
    if not sc:
        return {"editable": False, "edit_reason": "edit_bad_encoding"}
    try:  # 元の文字コードに戻して、まったく同じバイト列になるときだけ編集できる (CP932 の一部の文字など)
        same = sc[1] + text.encode(sc[0]) == raw
    except UnicodeEncodeError:
        same = False
    if not same:
        return {"editable": False, "edit_reason": "edit_bad_encoding"}
    eol, mixed = detect_eol(text)  # 混在しているときは、保存すると多い方に統一される
    return {"editable": True, "eol": eol, "eol_mixed": mixed, "save_encoding": sc[0] + (" (BOM)" if sc[1] else "")}


def wip_file(path):
    target = worktree_target(path)
    if os.path.islink(target):  # git と同じく、リンク先のパスを内容として扱う
        raw = os.fsencode(os.readlink(target))
    elif os.path.isfile(target):
        with open(target, "rb") as f:
            raw = f.read(FILE_CONTENT_LIMIT + 1)
    else:
        raise UserError("not_a_file")
    truncated = len(raw) > FILE_CONTENT_LIMIT
    data = raw[:FILE_CONTENT_LIMIT]
    text, encoding = decode_text(data, truncated)
    size = len(raw) if os.path.islink(target) else os.path.getsize(target)
    res = {"hash": WIP_HASH, "path": path, "content": text or "", "encoding": encoding,
           "binary": text is None, "truncated": truncated, "size": size,
           "sha": hashlib.sha256(raw).hexdigest()}
    if os.path.islink(target):
        res.update(editable=False, edit_reason="edit_symlink")
    else:
        res.update(edit_info(raw, text, encoding, truncated))
    return res


PATCH_BODY_LIMIT = 40_000_000  # /api/patch-apply の JSON の上限 (base64 にするので、パッチ本体は約 30MB まで)
SAVE_BODY_LIMIT = 16_000_000  # /api/file-save の JSON の上限 (FILE_CONTENT_LIMIT の文字列をエスケープしても収まる大きさ)


@route("/api/file-save", method="POST")
def api_file_save(body):
    """作業ツリーのファイルを、画面で編集した内容で上書きする。
    文字コード・BOM・改行コードは保存前のファイルに合わせて戻す。開いてから別の場所で変更されていたら保存しない。"""
    path = body.get("path", "")
    content, base = body.get("content"), body.get("sha", "")
    if not isinstance(path, str) or not isinstance(content, str) or not isinstance(base, str):
        raise ValueError("bad body")
    path = validate_file_path(path, allow_empty=False)
    target = worktree_target(path)
    if os.path.islink(target) or not os.path.isfile(target):
        raise UserError("not_a_file")
    with open(target, "rb") as f:
        raw = f.read()
    if hashlib.sha256(raw).hexdigest() != base:
        raise UserError("edit_changed_on_disk", path=path)
    text, label = decode_text(raw[:FILE_CONTENT_LIMIT], len(raw) > FILE_CONTENT_LIMIT)
    info = edit_info(raw, text, label, len(raw) > FILE_CONTENT_LIMIT)
    if not info["editable"]:
        raise UserError(info["edit_reason"])
    codec, bom = save_codec(raw, label)
    try:
        data = encode_for_save(content, codec, bom, info["eol"] or "lf")
    except UnicodeEncodeError as e:
        raise UserError("edit_encode_failed", encoding=label, char=e.object[e.start:e.end])
    if data == raw:
        return {"ok": True, "unchanged": True, "sha": base, "message": tr("edit_unchanged")}
    mode = os.stat(target).st_mode
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(target), prefix=".git_graph_edit_")
    try:  # 書きかけのファイルが残らないよう、一時ファイルに書いてから置き換える
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, mode & 0o7777)
        os.replace(tmp, target)
    except OSError as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise ValueError(str(e))
    return {"ok": True, "sha": hashlib.sha256(data).hexdigest(), "message": tr("edit_saved", path=path)}


@route("/api/tree")
def api_tree(q):
    if is_wip(q):
        return wip_tree(validate_file_path(q.get("path", [""])[0]))
    h = resolve_commit(q.get("hash", [""])[0])
    path = validate_file_path(q.get("path", [""])[0])
    treeish = h if not path else h + ":" + path
    try:
        raw = git("ls-tree", "-z", "-l", treeish)
    except subprocess.CalledProcessError:
        raise UserError("dir_not_found")
    entries = []
    for item in raw.split("\0"):
        if not item:
            continue
        head, name = item.split("\t", 1)
        mode, kind, obj, size = (head.split() + [""])[:4]
        entries.append({"name": name, "type": kind, "size": None if size == "-" else int(size), "mode": mode})
    return {"hash": h, "path": path, "entries": sorted(entries, key=lambda x: (x["type"] != "tree", x["name"]))}


_BOMS = ((codecs.BOM_UTF8, "utf-8-sig", "UTF-8 (BOM)"),
         (codecs.BOM_UTF32_LE, "utf-32", "UTF-32"), (codecs.BOM_UTF32_BE, "utf-32", "UTF-32"),
         (codecs.BOM_UTF16_LE, "utf-16", "UTF-16"), (codecs.BOM_UTF16_BE, "utf-16", "UTF-16"))


def _strict_decode(data, enc, truncated):
    """厳密にデコードする。先頭のみ読んだ場合は末尾の欠けた文字を最大3バイト捨てて再試行する。"""
    for cut in ((0, 1, 2, 3) if truncated else (0,)):
        try:
            return data[:len(data) - cut].decode(enc)
        except UnicodeDecodeError:
            pass
    return None


def _jp_score(text):
    """日本語として自然なほど高い点を返す (Shift_JIS と EUC-JP の判別用)。"""
    score = 0
    for ch in text:
        c = ord(ch)
        if 0x3040 <= c <= 0x30FF or 0x4E00 <= c <= 0x9FFF:
            score += 1
        elif 0xFF61 <= c <= 0xFF9F:  # 半角カナは誤判定のときに増える
            score -= 3
        elif c < 0x20 and ch not in "\t\r\n":
            score -= 5
    return score


def decode_text(data, truncated=False):
    """バイト列の文字コードを自動判別してデコードし、(text, 文字コード名) を返す。バイナリなら (None, None)。
    BOM → UTF-8 → ISO-2022-JP → Shift_JIS(CP932) / EUC-JP (点数の高い方) の順に試し、全て失敗したら UTF-8 で置換する。"""
    for bom, enc, label in _BOMS:
        if data.startswith(bom):
            text = _strict_decode(data, enc, truncated)
            if text is not None:
                return text, label
    if b"\0" in data:
        return None, None
    if b"\x1b$B" in data or b"\x1b$@" in data or b"\x1b$(" in data:  # ISO-2022-JP は 7bit なので UTF-8 より先に見る
        text = _strict_decode(data, "iso2022_jp", truncated)
        if text is not None:
            return text, "ISO-2022-JP"
    text = _strict_decode(data, "utf-8", truncated)
    if text is not None:
        return text, "ASCII" if text.isascii() else "UTF-8"
    best = None
    for enc, label in (("cp932", "Shift_JIS (CP932)"), ("euc_jp", "EUC-JP")):
        text = _strict_decode(data, enc, truncated)
        if text is not None:
            score = _jp_score(text)
            if best is None or score > best[0]:
                best = (score, text, label)
    if best:
        return best[1], best[2]
    return data.decode("utf-8", errors="replace"), "UTF-8 (invalid bytes replaced)"


@route("/api/file")
def api_file(q):
    if is_wip(q):
        return wip_file(validate_file_path(q.get("path", [""])[0], allow_empty=False))
    h = resolve_commit(q.get("hash", [""])[0])
    path = validate_file_path(q.get("path", [""])[0], allow_empty=False)
    spec = h + ":" + path
    kind = git("cat-file", "-t", spec).strip()
    if kind != "blob":
        raise UserError("not_a_file")
    raw = subprocess.check_output(["git", "-C", state.REPO, "cat-file", "blob", spec])
    truncated = len(raw) > FILE_CONTENT_LIMIT
    data = raw[:FILE_CONTENT_LIMIT]
    text, encoding = decode_text(data, truncated)
    return {"hash": h, "path": path, "content": text or "", "encoding": encoding,
            "binary": text is None, "truncated": truncated, "size": len(raw)}


def file_download(q):
    """ダウンロードするファイルを (ファイル名, バイト列) で返す。コミットのファイルは変換せず、そのままの blob を返す。"""
    if is_wip(q):
        path = validate_file_path(q.get("path", [""])[0], allow_empty=False)
        target = worktree_target(path)
        if os.path.islink(target):  # 表示と同じく、リンク先のパスを内容として扱う
            raw = os.fsencode(os.readlink(target))
        elif os.path.isfile(target):
            with open(target, "rb") as f:
                raw = f.read()
        else:
            raise UserError("not_a_file")
    else:
        h = resolve_commit(q.get("hash", [""])[0])
        path = validate_file_path(q.get("path", [""])[0], allow_empty=False)
        spec = h + ":" + path
        if git("cat-file", "-t", spec).strip() != "blob":
            raise UserError("not_a_file")
        raw = subprocess.check_output(["git", "-C", state.REPO, "cat-file", "blob", spec])
    return path.rsplit("/", 1)[-1], raw


def _git_raw(*args, env=None):
    r = subprocess.run(["git", "-C", state.REPO, *args], capture_output=True, env=env)
    if r.returncode != 0:
        raise ValueError(r.stderr.decode("utf-8", "replace").strip() or tr("git_command_failed"))
    return r.stdout


def wip_patch(base=None):
    """作業ツリーの変更 (ステージ済み・未ステージ・未追跡) をまとめた、base (既定は HEAD) からのパッチ。
    本物のインデックスは変えず、そのコピーに add -A して差分を取る。"""
    if base is None:
        try:
            base = git("rev-parse", "--verify", "--quiet", "HEAD^{commit}").strip()
        except subprocess.CalledProcessError:
            raise UserError("patch_no_head")
    tmp = tmpdir.make_temp_dir("patch")
    try:
        index = os.path.join(tmp, "index")
        real = os.path.join(state.REPO, git("rev-parse", "--git-path", "index").strip())
        env = dict(os.environ, GIT_INDEX_FILE=index)
        if os.path.isfile(real):
            shutil.copyfile(real, index)  # 手元のファイルの情報を再利用して速くする
        else:
            _git_raw("read-tree", base, env=env)
        _git_raw("add", "-A", env=env)
        return _git_raw("diff", "--cached", "--binary", "-M", base, env=env)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def patch_download(q):
    """作成したパッチを (ファイル名, バイト列) で返す。
    hash=WIP: 作業ツリーの HEAD からの全変更 / hash=<コミット>: そのコミットの変更 (format-patch)。
    from=<A>&to=<B>: 2コミット間の差分。to=WIP のときは A から作業ツリーまでの差分。"""
    if "from" in q:
        a = resolve_commit(q.get("from", [""])[0])
        to = q.get("to", [""])[0]
        if to == WIP_HASH:
            if not state.WIP_ENABLED or not state.HAS_WORKTREE:
                raise UserError("wip_disabled")
            name, raw = a[:7] + "..wip.patch", wip_patch(a)
        else:
            b = resolve_commit(to)
            name, raw = f"{a[:7]}..{b[:7]}.patch", git_bytes("diff", "--binary", "-M", a, b)
    elif is_wip(q):
        name, raw = "wip.patch", wip_patch()
    else:
        h = resolve_commit(q.get("hash", [""])[0])
        parents = git("rev-list", "--parents", "-n", "1", h).split()[1:]
        if len(parents) > 1:  # マージは format-patch が出力しないので、最初の親との差分にする
            raw = git_bytes("diff", "--binary", "-M", parents[0], h)
        else:
            raw = git_bytes("format-patch", "-1", "--stdout", "--binary", *(["--root"] if not parents else []), h)
        name = h[:7] + ".patch"
    if not raw.strip():
        raise UserError("patch_empty")
    return name, raw


def _patch_data(body):
    """リクエストのパッチ本体 (base64) を取り出す。"""
    try:
        data = base64.b64decode(body.get("data") or "", validate=True)
    except (ValueError, TypeError):
        raise UserError("patch_invalid")
    if not data.strip():
        raise UserError("patch_empty")
    return data


@route("/api/patch-apply", method="POST")
def api_patch_apply(body):
    """アップロードされたパッチを作業ツリーに git apply する (ステージ・コミットはしない)。先に解析と適用の確認をして、失敗したら何も変えない。"""
    if not state.WIP_ENABLED:
        raise UserError("wip_disabled")
    data = _patch_data(body)

    def run_apply(*opts):
        r = subprocess.run(["git", "-C", state.REPO, "apply", "--whitespace=nowarn", *opts, "-"], input=data, capture_output=True)
        if r.returncode != 0:
            raise ValueError(r.stderr.decode("utf-8", "replace").strip() or tr("git_command_failed"))
        return r.stdout.decode("utf-8", "replace")

    n = len([line for line in run_apply("--numstat").splitlines() if line.strip()])  # 壊れたパッチはここで失敗する
    if n == 0:
        raise UserError("patch_empty")
    run_apply("--check")
    run_apply()
    return {"ok": True, "message": tr("patch_applied", n=n)}


@route("/api/patch-am", method="POST")
def api_patch_am(body):
    """アップロードされたパッチ (git format-patch の出力) を git am で適用し、パッチのコミットを現在のブランチに積む。
    途中で失敗したら git am --abort で元の状態に戻す。マージなどの途中のときは、その操作を壊さないよう何もしない。"""
    if not state.WIP_ENABLED:
        raise UserError("wip_disabled")
    data = _patch_data(body)
    if is_merging() or is_rebasing() or is_cherry_picking() or is_reverting():
        raise UserError("patch_am_busy")  # rebase-apply が残っていると、am --abort が別の操作を中断してしまう
    old = git("rev-parse", "HEAD").strip() if has_head() else ""
    r = subprocess.run(["git", "-C", state.REPO, "am", "--quiet", "--whitespace=nowarn"], input=data, capture_output=True)
    if r.returncode != 0:
        subprocess.run(["git", "-C", state.REPO, "am", "--abort"], capture_output=True)
        text = (r.stderr + r.stdout).decode("utf-8", "replace").strip()
        raise ValueError(text or tr("git_command_failed"))
    n = int(git("rev-list", "--count", f"{old}..HEAD" if old else "HEAD").strip() or 0)
    return {"ok": True, "message": tr("patch_am_applied", n=n)}


@route("/api/blame")
def api_blame(q):
    path = validate_file_path(q.get("path", [""])[0], allow_empty=False)
    if is_wip(q):  # 作業ツリーの内容を blame する。未コミットの行はハッシュが 0000000 になる
        worktree_target(path)
        h = WIP_HASH
        r = subprocess.run(["git", "-C", state.REPO, "blame", "--line-porcelain", "--", path],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=dict(os.environ, GIT_LITERAL_PATHSPECS="1"))
        if r.returncode != 0:
            raise UserError("wip_blame_untracked", path=path)
        out = r.stdout
    else:
        h = resolve_commit(q.get("hash", [""])[0])
        out = git("blame", "--line-porcelain", h, "--", path, literal=True)
    raw_lines = out.splitlines()
    lines, i = [], 0
    while i < len(raw_lines):
        header = raw_lines[i].split()
        if len(header) < 3:
            raise ValueError("bad blame output")
        commit, line_no = header[0], int(header[2])
        author, author_time, author_tz = "", "", "+0000"
        i += 1
        while i < len(raw_lines) and not raw_lines[i].startswith("\t"):
            item = raw_lines[i]
            if item.startswith("author "):
                author = item[7:]
            elif item.startswith("author-time "):
                author_time = item[12:]
            elif item.startswith("author-tz "):
                author_tz = item[10:]
            i += 1
        if i >= len(raw_lines):
            raise ValueError("bad blame output")
        offset = int(author_tz[1:3]) * 60 + int(author_tz[3:5])
        if author_tz[0] == "-":
            offset = -offset
        date = datetime.fromtimestamp(
            int(author_time), timezone(timedelta(minutes=offset))
        ).date().isoformat()
        lines.append({"hash": commit, "line": line_no, "author": author,
                      "date": date, "content": raw_lines[i][1:]})
        i += 1
    return {"hash": h, "path": path, "lines": lines}


@route("/api/file-copy", method="POST")
def api_file_copy(body):
    """コミット内のファイルを、作業ツリーの別のパスにコピーする。既存のファイルは上書きしない。"""
    if not state.HAS_WORKTREE:
        raise UserError("copy_no_worktree")
    h = resolve_commit(body.get("hash", ""))
    path = validate_file_path(body.get("path", ""), allow_empty=False)
    dest = body.get("dest", "")
    if not isinstance(dest, str):
        raise UserError("copy_dest_invalid")
    dest = dest.strip()
    try:
        validate_file_path(dest, allow_empty=False)
    except ValueError:
        raise UserError("copy_dest_invalid")
    if "\\" in dest or dest.split("/")[0].lower() == ".git":
        raise UserError("copy_dest_invalid")
    root = os.path.realpath(state.REPO)
    target = os.path.realpath(os.path.join(root, *dest.split("/")))
    try:
        inside = os.path.commonpath([root, target]) == root
    except ValueError:  # 別ドライブなど
        inside = False
    if not inside or target == root:
        raise UserError("copy_dest_invalid")
    spec = h + ":" + path
    if git("cat-file", "-t", spec).strip() != "blob":
        raise UserError("not_a_file")
    if os.path.lexists(target):
        raise UserError("copy_dest_exists", path=dest)
    # --filters で改行コードなどを作業ツリーでの形 (autocrlf など) にそろえる。使えない git では素のまま
    r = subprocess.run(["git", "-C", state.REPO, "cat-file", "--filters", spec], capture_output=True)
    if r.returncode != 0:
        r = subprocess.run(["git", "-C", state.REPO, "cat-file", "blob", spec], capture_output=True)
        if r.returncode != 0:
            raise ValueError(r.stderr.decode("utf-8", errors="replace").strip() or tr("git_command_failed"))
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "xb") as f:
            f.write(r.stdout)
    except OSError as e:
        raise ValueError(str(e))
    return {"ok": True, "message": tr("file_copied", path=dest)}


__all__ = [
    "FILE_CONTENT_LIMIT", "api_file", "api_file_copy", "api_tree", "decode_text", "api_blame",
    "validate_file_path", "WIP_HASH", "api_file_save", "SAVE_BODY_LIMIT", "file_download", "patch_download", "api_patch_apply", "api_patch_am", "PATCH_BODY_LIMIT", "wip_patch",
]
