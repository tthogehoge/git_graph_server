# 作業ツリーのファイルの保存 (画面で編集した内容での上書き)。git は実行しないが、作業ツリーを書き換えて、
# 履歴 (undo) に記録する操作なので engine に置く。引数を受け取って結果を dict で返す。画面に出す文言の組み立ては route 側の仕事。
import hashlib
import os
import tempfile
from ..i18n import UserError
from ..query.files import FILE_CONTENT_LIMIT, validate_file_path, worktree_target
from ..textcodec import decode_text, edit_info, encode_for_save, save_codec
from . import operation


@operation("file_save", touches_worktree=True)
def save_file(path, content, base_sha):
    """作業ツリーのファイルを、画面で編集した内容 (content) で上書きする。
    文字コード・BOM・改行コードは保存前のファイルに合わせて戻す。開いてから別の場所で変更されていたら (base_sha が合わなければ)
    保存しない。書きかけのファイルが残らないよう、一時ファイルに書いてから置き換える。
    戻り値: {"path", "sha" (保存後の内容の sha256), "unchanged" (内容が同じで書かなかった)}。"""
    if not isinstance(path, str) or not isinstance(content, str) or not isinstance(base_sha, str):
        raise ValueError("bad body")
    path = validate_file_path(path, allow_empty=False)
    target = worktree_target(path)
    if os.path.islink(target) or not os.path.isfile(target):
        raise UserError("not_a_file")
    with open(target, "rb") as f:
        raw = f.read()
    if hashlib.sha256(raw).hexdigest() != base_sha:
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
        return {"path": path, "sha": base_sha, "unchanged": True}
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
    return {"path": path, "sha": hashlib.sha256(data).hexdigest(), "unchanged": False}
