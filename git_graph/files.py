# ファイルの一覧・内容・blame の route、作業ツリーへの保存・コピー、パッチの作成と適用。
# 読み取り (tree / file / blame) は git_graph.query.files に任せ、文字コードは textcodec に置く。
import base64
import hashlib
import os
import shutil
import tempfile
import subprocess
from . import app_state as state
from . import tmpdir
from .engine import patch
from .gitrun import git, resolve_commit
from .i18n import tr, UserError
from .query import files as qf
from .routing import route
from .textcodec import decode_text, detect_eol, edit_info, encode_for_save, save_codec

# 互換の再公開
FILE_CONTENT_LIMIT = qf.FILE_CONTENT_LIMIT
WIP_HASH = qf.WIP_HASH
validate_file_path = qf.validate_file_path
is_wip = qf.is_wip
wip_head = qf.wip_head
worktree_target = qf.worktree_target
status_mark = qf.status_mark
wip_status_marks = qf.wip_status_marks
wip_tree = qf.wip_tree
wip_file = qf.wip_file

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
    return qf.tree(q.get("hash", [""])[0], q.get("path", [""])[0])


@route("/api/file")
def api_file(q):
    return qf.file_content(q.get("hash", [""])[0], q.get("path", [""])[0])



def file_download(q):
    """ダウンロードするファイルを (ファイル名, バイト列) で返す。"""
    h = q.get("hash", [""])[0]
    return qf.file_bytes(h, q.get("path", [""])[0])


def wip_patch(base=None):
    return qf.wip_patch(base)


def patch_download(q):
    """作成したパッチを (ファイル名, バイト列) で返す。"""
    if "from" in q:
        return qf.patch_bytes(from_hash=q.get("from", [""])[0], to_hash=q.get("to", [""])[0], has_from=True)
    return qf.patch_bytes(hash_=q.get("hash", [""])[0])



def _patch_data(body):
    """リクエストのパッチ本体 (base64) を取り出す。"""
    try:
        data = base64.b64decode(body.get("data") or "", validate=True)
    except (ValueError, TypeError):
        raise UserError("patch_invalid")
    if not data.strip():
        raise UserError("patch_empty")
    return data


@route("/api/patch-apply", method="POST", engine=True)
def api_patch_apply(body):
    """アップロードされたパッチを作業ツリーに git apply する (ステージ・コミットはしない)。"""
    r = patch.apply_patch(_patch_data(body))
    return {"ok": True, "message": tr("patch_applied", n=r["files"])}


@route("/api/patch-am", method="POST", engine=True)
def api_patch_am(body):
    """アップロードされたパッチ (git format-patch の出力) を git am で適用し、パッチのコミットを現在のブランチに積む。"""
    r = patch.am_patch(_patch_data(body))
    return {"ok": True, "message": tr("patch_am_applied", n=r["commits"])}


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


@route("/api/blame")
def api_blame(q):
    return qf.blame(q.get("hash", [""])[0], q.get("path", [""])[0])


__all__ = [
    "FILE_CONTENT_LIMIT", "api_file", "api_file_copy", "api_tree", "decode_text", "api_blame",
    "validate_file_path", "WIP_HASH", "api_file_save", "SAVE_BODY_LIMIT", "file_download", "patch_download",
    "api_patch_apply", "api_patch_am", "PATCH_BODY_LIMIT", "wip_patch", "worktree_target", "status_mark",
]
