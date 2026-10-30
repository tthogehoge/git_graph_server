# ファイルの一覧・内容・blame の route、作業ツリーへの保存・コピー、パッチの作成と適用。
# 読み取り (tree / file / blame) は git_graph.query.files に任せ、文字コードは textcodec に置く。
import base64
import os
from . import app_state as state
from .engine import files, patch
from .i18n import tr, UserError
from .query import files as qf
from .routing import route
from .textcodec import decode_text, detect_eol  # noqa: F401  (decode_text / detect_eol は互換のため再公開)

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


@route("/api/file-save", method="POST", engine=True)
def api_file_save(body):
    """作業ツリーのファイルを、画面で編集した内容で上書きする。開いてから別の場所で変更されていたら保存しない。"""
    r = files.save_file(body.get("path", ""), body.get("content"), body.get("sha", ""))
    if r["unchanged"]:
        return {"ok": True, "unchanged": True, "sha": r["sha"], "message": tr("edit_unchanged")}
    return {"ok": True, "sha": r["sha"], "message": tr("edit_saved", path=r["path"])}


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
    h = qf.commit_of(body.get("hash", ""))
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
    qf.require_blob(h, path)
    if os.path.lexists(target):
        raise UserError("copy_dest_exists", path=dest)
    data = qf.blob_for_worktree(h, path)  # 改行コードなどは作業ツリーでの形にそろえる
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "xb") as f:
            f.write(data)
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
