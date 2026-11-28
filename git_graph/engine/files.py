# 作業ツリーのファイルの保存 (画面で編集した内容での上書き)・アップロード・新規作成・名前の変更と移動。
# 名前の変更と移動 (move_path) だけは、追跡中のものに git mv を使う。作業ツリーを書き換えて、
# 履歴 (undo) に記録する操作なので engine に置く。引数を受け取って結果を dict で返す。画面に出す文言の組み立ては route 側の仕事。
import hashlib
import os
import tempfile
from .. import app_state as state
from ..gitrun import git, run_git_write
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


UPLOAD_LIMIT = 40_000_000  # 1 ファイルの上限 (バイト)


@operation("file_upload", touches_worktree=True, detail=lambda path, *_, **__: path if isinstance(path, str) else "")
def upload_file(path, data, overwrite=False):
    """ブラウザからドロップしたファイルを作業ツリーに書く。
    data は生の bytes。ディレクトリは必要なら作る。既存ファイルは overwrite が真のときだけ上書き。"""
    if not isinstance(path, str) or not isinstance(data, (bytes, bytearray)):
        raise ValueError("bad body")
    data = bytes(data)
    if len(data) > UPLOAD_LIMIT:
        raise UserError("upload_too_large")
    path = validate_file_path(path, allow_empty=False)
    # パス区切りを含む name をそのまま書かない (クライアントは basename を渡す想定だが防御)
    if path.endswith("/") or path.endswith("\\"):
        raise UserError("not_a_file")
    target = worktree_target(path)
    if os.path.isdir(target):
        raise UserError("upload_is_dir", path=path)
    if os.path.lexists(target) and not overwrite:
        raise UserError("upload_exists", path=path)
    parent = os.path.dirname(target)
    if parent:
        os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=parent or ".", prefix=".git_graph_upload_")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, target)
    except OSError as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise ValueError(str(e))
    return {"path": path, "size": len(data), "sha": hashlib.sha256(data).hexdigest()}


@operation("file_create", touches_worktree=True, detail=lambda path, *_, **__: path if isinstance(path, str) else "")
def create_file(path):
    """作業ツリーに空のファイルを新規作成する。既にあればエラー。親ディレクトリは必要なら作る。"""
    if not isinstance(path, str):
        raise ValueError("bad body")
    path = validate_file_path(path, allow_empty=False)
    target = worktree_target(path)
    if os.path.lexists(target):
        raise UserError("create_exists", path=path)
    parent = os.path.dirname(target)
    if parent:
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError as e:
            raise ValueError(str(e))
    try:
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        os.close(fd)
    except FileExistsError:
        raise UserError("create_exists", path=path)
    except OSError as e:
        raise ValueError(str(e))
    return {"path": path}


@operation("dir_create", touches_worktree=True, detail=lambda path, *_, **__: path if isinstance(path, str) else "")
def create_dir(path):
    """作業ツリーにディレクトリを新規作成する。既にあればエラー。"""
    if not isinstance(path, str):
        raise ValueError("bad body")
    path = validate_file_path(path, allow_empty=False)
    target = worktree_target(path)
    if os.path.lexists(target):
        raise UserError("create_exists", path=path)
    try:
        os.makedirs(target, exist_ok=False)
    except FileExistsError:
        raise UserError("create_exists", path=path)
    except OSError as e:
        raise ValueError(str(e))
    return {"path": path}


def _remove_dirs(dirs):
    for p in reversed(dirs):
        try:
            os.rmdir(p)
        except OSError:
            pass


def _make_parents(target):
    """target の親ディレクトリのうち、まだ無いものを作る。作ったものを返す (あとで失敗したときに消すため)。"""
    todo, d = [], os.path.dirname(target)
    while not os.path.lexists(d):
        todo.append(d)
        up = os.path.dirname(d)
        if up == d:
            break
        d = up
    if os.path.lexists(d) and not os.path.isdir(d):
        raise UserError("move_dest_invalid")  # 親のどれかが、ディレクトリでなくファイル
    made = []
    try:
        for p in reversed(todo):
            os.mkdir(p)
            made.append(p)
    except OSError as e:
        _remove_dirs(made)
        raise ValueError(str(e))
    return made


def _is_case_rename(src, dest, src_target, dest_target):
    """大文字小文字だけを変える名前の変更か。大文字小文字を区別しないファイルシステムでは、新しい名前が「既にある」ように見えるので、
    元と同じものかを調べる。"""
    try:
        return src.lower() == dest.lower() and os.path.samestat(os.lstat(src_target), os.lstat(dest_target))
    except OSError:
        return False


@operation("file_move", touches_worktree=True,
           detail=lambda src, dest, *_, **__: f"{src} \u2192 {dest}" if isinstance(src, str) and isinstance(dest, str) else "")
def move_path(src, dest):
    """作業ツリーのファイル / ディレクトリの名前を変える、または別のディレクトリへ移す (dest は新しいパス全体。親ディレクトリは必要なら作る)。
    追跡中なら git mv (名前の変更をステージする)、追跡中でないもの (未追跡・無視) は作業ツリーで動かすだけ。
    移動先に何かあれば、上書きせずエラー。失敗したら、作った親ディレクトリも消して、元の状態に戻す。
    戻り値: {"src", "dest", "tracked" (git mv したか)}。"""
    if not state.WIP_ENABLED:
        raise UserError("wip_disabled")
    if not isinstance(src, str) or not isinstance(dest, str):
        raise ValueError("bad body")
    src = validate_file_path(src, allow_empty=False)
    src_target = worktree_target(src)
    try:
        dest = validate_file_path(dest, allow_empty=False)
        dest_target = worktree_target(dest)
    except ValueError:
        raise UserError("move_dest_invalid")
    if src == dest:
        raise UserError("move_same")
    if dest.startswith(src + "/"):
        raise UserError("move_into_self")
    if not os.path.lexists(src_target):
        raise UserError("move_missing", path=src)
    if os.path.lexists(dest_target) and not _is_case_rename(src, dest, src_target, dest_target):
        raise UserError("move_exists", path=dest)
    tracked = _is_tracked(src)
    made = _make_parents(dest_target)
    try:
        _move_one(src, dest, src_target, dest_target, tracked)
    except BaseException:
        _remove_dirs(made)
        raise
    return {"src": src, "dest": dest, "tracked": tracked}


def _is_tracked(path):
    """追跡中か (ディレクトリは、中に追跡中のファイルがあれば)。"""
    return any(git("ls-files", "-z", "--", path, literal=True).split("\0"))


def _move_one(src, dest, src_target, dest_target, tracked):
    """1 つ動かす (移動先の親ディレクトリは、呼ぶ前に作っておく)。追跡中なら git mv、そうでなければ作業ツリーで動かすだけ。"""
    if tracked:
        run_git_write("mv", "--", src, dest, literal=True)
        return
    try:
        os.rename(src_target, dest_target)
    except OSError as e:
        raise ValueError(str(e))


@operation("files_move", touches_worktree=True,
           detail=lambda paths, dest_dir, *_, **__: f"{len(paths)} \u2192 {dest_dir or '/'}" if isinstance(paths, list) and isinstance(dest_dir, str) else "")
def move_paths(paths, dest_dir):
    """作業ツリーのファイル / ディレクトリを、まとめて 1 つのディレクトリ (dest_dir。"" はルート。無ければ作る) の中へ、名前はそのままで移す。
    全部を先に確かめてから動かし、途中で失敗したら動かした分を戻す (作った親ディレクトリも消す)。どれかの移動先に何かあれば、何も動かさずにエラー。
    祖先と子孫の両方を渡したときは、子孫は祖先と一緒に動く。もう dest_dir の中にあるものは、飛ばす。
    追跡中のものは git mv (変更をステージする)、そうでなければ作業ツリーで動かすだけ。
    戻り値: {"moved": [{"src", "dest", "tracked"}], "dest_dir"}。"""
    if not state.WIP_ENABLED:
        raise UserError("wip_disabled")
    if not isinstance(paths, list) or not paths or not all(isinstance(p, str) for p in paths) or not isinstance(dest_dir, str):
        raise ValueError("bad body")
    try:
        dest_dir = validate_file_path(dest_dir, allow_empty=True)
        dest_dir_target = worktree_target(dest_dir)
    except ValueError:
        raise UserError("move_dest_invalid")
    srcs = []
    for p in paths:
        p = validate_file_path(p, allow_empty=False)
        if p not in srcs:
            srcs.append(p)
    srcs = [p for p in srcs if not any(q != p and p.startswith(q + "/") for q in srcs)]  # 子孫は、祖先と一緒に動く
    plans = []
    for src in srcs:
        dest = (dest_dir + "/" if dest_dir else "") + src.rsplit("/", 1)[-1]
        if src == dest:
            continue  # もう、そこにある
        if dest_dir == src or dest_dir.startswith(src + "/"):
            raise UserError("move_into_self")
        plans.append((src, dest))
    if not plans:
        raise UserError("move_same")
    seen = set()
    for src, dest in plans:
        if dest in seen:
            raise UserError("move_name_conflict", name=dest.rsplit("/", 1)[-1])
        seen.add(dest)
    targets = []
    for src, dest in plans:
        src_target, dest_target = worktree_target(src), worktree_target(dest)
        if not os.path.lexists(src_target):
            raise UserError("move_missing", path=src)
        if os.path.lexists(dest_target) and not _is_case_rename(src, dest, src_target, dest_target):
            raise UserError("move_exists", path=dest)
        targets.append((src, dest, src_target, dest_target, _is_tracked(src)))
    made = _make_parents(os.path.join(dest_dir_target, "_")) if dest_dir else []  # 移動先のディレクトリ (の無い分)
    done = []
    try:
        for item in targets:
            _move_one(*item)
            done.append(item)
    except BaseException:
        for src, dest, src_target, dest_target, tracked in reversed(done):  # 動かした分を戻す
            try:
                _move_one(dest, src, dest_target, src_target, tracked)
            except Exception:
                pass
        _remove_dirs(made)
        raise
    return {"moved": [{"src": s, "dest": d, "tracked": tr} for s, d, _st, _dt, tr in targets], "dest_dir": dest_dir}
