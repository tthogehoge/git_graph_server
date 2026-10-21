# ファイルの一覧・内容・blame・作業ディレクトリへのコピーと、文字コードの判別。
import codecs
import os
import subprocess
from datetime import datetime, timedelta, timezone
from . import app_state as state
from .gitrun import git, resolve_commit
from .i18n import tr, UserError
from .routing import route


FILE_CONTENT_LIMIT = 2_000_000


def validate_file_path(path, allow_empty=True):
    """リポジトリルートからの相対パスとして安全な形にする。"""
    if "\0" in path or (not allow_empty and not path) or path.startswith("/"):
        raise ValueError("bad path")
    parts = path.split("/") if path else []
    if any(p in ("", ".", "..") for p in parts):
        raise ValueError("bad path")
    return path


@route("/api/tree")
def api_tree(q):
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


@route("/api/blame")
def api_blame(q):
    h = resolve_commit(q.get("hash", [""])[0])
    path = validate_file_path(q.get("path", [""])[0], allow_empty=False)
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
    "validate_file_path",
]
