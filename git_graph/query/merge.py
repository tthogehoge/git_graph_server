# 競合しているファイルの読み取り (マージエディタ用)。
# インデックスのステージ 2 (ours) / 1 (base) / 3 (theirs) の内容と、git merge-file による 3 way マージの結果、
# 作業ツリーの現在の内容を返す。リポジトリは変更しない。HTTP や画面文言は知らない。
import os
import shutil
import subprocess
from .. import app_state as state
from .. import tmpdir
from ..gitrun import git_bytes
from ..gitstate import is_rebasing
from ..i18n import UserError
from ..textcodec import decode_text
from .files import FILE_CONTENT_LIMIT, validate_file_path, wip_file

SPECIAL_MODES = ("160000", "120000")  # submodule (gitlink) とシンボリックリンクは、内容をマージできない


def unmerged_entries(path):
    """インデックスにある、競合ファイルのステージごとの (mode, sha)。{"1": ..., "2": ..., "3": ...} (無いステージは含まない)。"""
    out = git_bytes("ls-files", "-u", "-z", "--", path, literal=True)
    entries = {}
    for rec in out.split(b"\0"):
        if b"\t" not in rec:
            continue
        mode, sha, stage = rec.split(b"\t", 1)[0].decode("ascii", "replace").split()
        entries[stage] = (mode, sha)
    return entries


def three_way_merge(ours, base, theirs):
    """git merge-file で 3 way マージする。競合は diff3 形式 (ours / base / theirs の 3 つのセクション) のマーカーで残る。
    戻り値は (マージ結果のバイト列, 競合の数)。競合の数は git の仕様で最大 127。"""
    work = tmpdir.make_temp_dir("merge")
    try:
        files = []
        for name, data in (("ours", ours), ("base", base), ("theirs", theirs)):
            files.append(os.path.join(work, name))
            with open(files[-1], "wb") as f:
                f.write(data)
        r = subprocess.run(["git", "-C", state.REPO, "merge-file", "-p", "--diff3", "-L", "ours", "-L", "base", "-L", "theirs", *files],
                           capture_output=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if r.returncode < 0 or r.returncode > 127:  # 255 は git merge-file の失敗 (競合の数ではない)
        raise ValueError(r.stderr.decode("utf-8", "replace").strip() or "git merge-file failed")
    return r.stdout, r.returncode


def _unsupported(res, reason):
    return {**res, "supported": False, "reason": reason}


def merge_sources(path):
    """マージエディタに渡す、競合ファイルの情報。
    supported が False のときは reason (翻訳キー) を返す: 片側で削除された競合、submodule / シンボリックリンク、
    バイナリ、大きすぎる、文字コードを元に戻せないファイル。
    supported のとき: ours / theirs / base (base が無い add/add 競合は空文字、has_base=False)、merged (3 way マージの結果。
    競合は diff3 形式のマーカー)、working (作業ツリーの現在の内容。保存・解決はこれを元にする)、sha (working の sha256。
    /api/file-save に渡す)、encoding、rebasing (リベース中は ours / theirs の向きが逆)。"""
    path = validate_file_path(path, allow_empty=False)
    if not state.HAS_WORKTREE:
        raise UserError("no_worktree")
    entries = unmerged_entries(path)
    if not entries:
        raise UserError("merge_not_conflict", path=path)
    res = {"path": path, "rebasing": is_rebasing()}
    if "2" not in entries or "3" not in entries:
        return _unsupported(res, "merge_one_side_deleted")
    if any(mode in SPECIAL_MODES for mode, _ in entries.values()):
        return _unsupported(res, "merge_special_file")
    working = wip_file(path)
    if not working.get("editable"):
        return _unsupported(res, working.get("edit_reason") or "edit_binary")
    raws = {stage: git_bytes("cat-file", "blob", sha) for stage, (_, sha) in entries.items() if stage in ("1", "2", "3")}
    if any(len(raw) > FILE_CONTENT_LIMIT for raw in raws.values()):
        return _unsupported(res, "edit_too_large")
    texts = {}
    for stage, raw in raws.items():
        texts[stage], _ = decode_text(raw)
        if texts[stage] is None:
            return _unsupported(res, "edit_binary")
    merged_raw, conflicts = three_way_merge(raws["2"], raws.get("1", b""), raws["3"])
    merged, _ = decode_text(merged_raw)
    if merged is None or len(merged_raw) > FILE_CONTENT_LIMIT:
        return _unsupported(res, "edit_too_large" if merged is not None else "edit_binary")
    return {**res, "supported": True, "reason": None, "ours": texts["2"], "theirs": texts["3"], "base": texts.get("1", ""),
            "has_base": "1" in entries, "merged": merged, "merge_conflicts": conflicts,
            "working": working["content"], "sha": working["sha"], "encoding": working["encoding"],
            "eol": working.get("eol"), "save_encoding": working.get("save_encoding")}


__all__ = ["merge_sources", "three_way_merge", "unmerged_entries"]
