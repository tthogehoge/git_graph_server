# HTML ページ (/diff 差分ページと /files ファイル表示ページ) の組み立て。
import html
import json
import os
from collections import Counter
from urllib.parse import urlencode
from . import app_state as state
from .history import api_commit, api_compare, api_diff
from .i18n import (
    client_translations, FAVICON_LINK, FILES_PAGE, fill_template, read_asset, tr, UserError,
    with_project,
)
from .external import configured
from .wip import api_status
from .files import WIP_HASH, worktree_target
from .query.files import file_exists_at


def wip_file_exists(path):
    try:
        target = worktree_target(path)
    except ValueError:  # UserError も含む
        return False
    return os.path.isfile(target) and not os.path.islink(target)

COPY_JS = read_asset("copy.js")
DIFF_CSS = read_asset("diff.css")
DIFF_FOLD_JS = read_asset("diff_fold.js")
DIFF_RELOAD_JS = read_asset("diff_reload.js")
PANE_RESIZE_JS = read_asset("pane_resize.js")
WIP_DIFF_COMMIT_JS = read_asset("wip_diff_commit.js")
WIP_DIFF_REMOTE_JS = read_asset("wip_diff_remote.js")
DIFF_DIALOG_JS = read_asset("diff_dialog.js")
# WIP の差分ページの push / pull / fetch ダイアログが使う文言
REMOTE_TEXT_KEYS = (
    "cancel", "ok", "prune_label", "pull_mode", "pull_opt_ff", "pull_opt_merge", "pull_opt_rebase",
    "pull_mode_ff", "pull_mode_merge", "pull_mode_rebase", "confirm_pull",
    "push_branch_select", "push_remote_select", "push_remote_branch", "push_tags_label", "force_label",
    "what_branch", "confirm_push", "confirm_push_force", "push_cancelled", "operation_cancelled",
    "no_remotes", "push_no_commits", "running_remote", "http_auth", "auth_username", "auth_password",
    "authenticate", "prompt_push_remote", "confirm_set_upstream", "remote_unregistered",
    "co_blocked_op", "co_blocked_hint_op", "co_modified", "co_untracked_op", "co_hint_stash", "co_hint_discard",
    "co_stash_retry", "co_discard_retry",
)
FILES_CSS = read_asset("files.css")
# ヘッダーのアイコンボタン。files.html / index.html にも同じ絵を直接書いてある
_SVG = '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false">{p}</svg>'
_ICON_GRAPH = _SVG.format(p='<path d="M2 8 8 2.5 14 8M3.8 7v6.5h3V10h2.4v3.5h3V7" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>')
_ICON_FILES = _SVG.format(p='<path d="M4 2.5h5.5L12 5v8.5H4zM9.5 2.5V5H12" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>')
_ICON_DIFF = _SVG.format(p='<path d="M2.5 3h4v10h-4zM9.5 3h4v10h-4zM7.2 5.5v5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>')


def page_nav_html(current, files_href=None, diff_href=None, graph_href="/"):
    """グラフ / ファイル / 差分の切り替え。current は 'graph'|'files'|'diff'。
    href が無い項目は無効ボタン。今のページは .current で強調する。
    graph_href に select_hash を付けると、グラフに戻ったときに選択を復元する。"""
    def item(kind, href, label_key, svg):
        label = html.escape(tr(label_key), quote=True)
        if kind == current:
            return (f'<span class="iconbtn current" title="{label}" aria-label="{label}" aria-current="page">'
                    f'{svg}</span>')
        if href:
            return (f'<a class="iconbtn" href="{html.escape(href, quote=True)}" title="{label}" aria-label="{label}">'
                    f'{svg}</a>')
        return (f'<button type="button" class="iconbtn" disabled title="{label}" aria-label="{label}">'
                f'{svg}</button>')
    return (item("graph", graph_href, "nav_graph", _ICON_GRAPH)
            + item("files", files_href, "nav_files", _ICON_FILES)
            + item("diff", diff_href, "nav_diff", _ICON_DIFF))


NAV_RELOAD_BUTTON = ('<button type="button" class="iconbtn" id="rld" title="{label}" aria-label="{label}">'
                     '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false"><path d="M13.5 8a5.5 5.5 0 1 1-1.7-4M13 2.5v3.2h-3.2" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg></button>')
NAV_BACK_BUTTON = ('<button type="button" class="iconbtn" onclick="history.back()" title="{label}" aria-label="{label}">'
                   '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false"><path d="M13 8H3M7.5 3.5 3 8l4.5 4.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg></button>')
EXT_DIFF_JS = read_asset("ext_diff.js")
DIFF_PATCH_JS = read_asset("diff_patch.js")


WIP_DIR_COLLAPSE = 10


def dir_of(path):
    return path.rpartition("/")[0]




def files_view_url(hash_, path):
    return "/files?" + urlencode({"hash": hash_, "path": dir_of(path), "file": path})


def diff_lines_html(text):
    """差分を行ごとに色分けする (ヘッダ部と本文を区別)。"""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    in_hunk, combined, out = False, False, []  # combined: 競合の差分 (diff --cc)。行頭が親の数だけ並ぶ (" +" "+ " "++" など)
    for l in lines:
        cls = ""
        if l.startswith("diff "):
            in_hunk, combined, cls = False, l.startswith("diff --cc") or l.startswith("diff --combined"), "hd"
        elif not in_hunk:
            if l.startswith("@@"):
                in_hunk, cls = True, "hk"
            else:
                cls = "hd"
        elif l.startswith("@@"):
            cls = "hk"
        elif combined and "+" in l[:2]:
            cls = "ad"
        elif combined and "-" in l[:2]:
            cls = "de"
        elif combined:
            cls = ""
        elif l.startswith("+"):
            cls = "ad"
        elif l.startswith("-"):
            cls = "de"
        elif l.startswith("\\"):
            cls = "hd"
        out.append(f'<div class="dl {cls}">{html.escape(l) or " "}</div>')
    return "".join(out)


def localized_wip_title(kind):
    return {
        "amend": tr("amend_content"),
        "conflict": tr("conflict"),
        "staged": tr("staged"),
        "unstaged": tr("unstaged"),
        "untracked": tr("untracked"),
    }.get(kind, kind)


def diff_url(ctx, f):
    params = dict(ctx, path=f["path"])
    if f.get("wip"):
        params["wip"] = f["wip"]
    if f.get("old_path"):
        params["old"] = f["old_path"]
    return "/diff?" + urlencode(params)


def commit_line_html(label, c):
    subject = c["message"].split("\n", 1)[0]
    return (f'<div class="meta"><span class="tag tag{label}">{label}</span>{c["hash"][:7]}'
            f'<a class="copy" data-copy="{c["hash"]}" title="{html.escape(tr("copy_hash_title"))}">📋</a> — '
            f'{html.escape(subject)} <span class="hint">({html.escape(c["author"])} / '
            f'{html.escape(c["date"])})</span></div>')


def wip_files(kind="", status=None):
    """WIP の全ファイルを 競合 → ステージ済み → 未ステージ → 未追跡 の順に、種別(wip)つきで返す。
    kind が amend のときは、amend 後のコミットの内容 (直前のコミットの親との差分) を返す。"""
    if kind == "amend":
        s = api_status({"amend": ["1"]})
        if "amend_files" not in s:
            raise UserError("amend_unavailable")
        return s["amend_files"]
    s = status or api_status({})
    out = []
    for kind, key in (("conflict", "conflicts"), ("staged", "staged"),
                      ("unstaged", "unstaged"), ("untracked", "untracked")):
        out.extend(dict(f, wip=kind) for f in s[key])
    return out


def render_diff_page(q):
    """1ファイルの差分ページ。左にそのコミット(比較 / WIP)の変更ファイル一覧を出す。"""
    path = q.get("path", [""])[0]
    old = q.get("old", [""])[0]
    wip = q.get("wip", [""])[0]
    is_wip_compare = wip == "compare"
    clean_wip_page = not path and wip == "unstaged"
    d = ({"diff": "", "encoding": "UTF-8", "truncated": False}
         if clean_wip_page else api_diff(q))
    wip_status = None
    patch = None   # (ダウンロード用のパラメータ, ファイル名, ツールチップの翻訳キー)。パッチ作成ボタン用
    view_hash = None  # ファイル表示で開くコミット (None なら出さない)
    if wip == "compare":  # コミットと作業ツリーの比較
        c = api_compare({"from": q.get("from", [""]), "to": ["WIP"]})
        a = c["from"]
        files, ctx = c["files"], {"from": a["hash"], "wip": "compare"}
        ref = f'{a["hash"][:7]}..WIP'
        info = (commit_line_html("A", a) +
                f'<div class="meta"><span class="tag tagB">B</span>{html.escape(tr("wip_title"))}</div>')
        cmd = f"git diff {a['hash'][:7]} -- {path}"
        patch = ({"from": a["hash"], "to": "WIP"}, f"{a['hash'][:7]}..wip.patch", "tip_patch_create_compare_wip")
        view_hash = WIP_HASH if wip_file_exists(path) else None
    elif wip:  # 作業ツリーの変更
        if wip != "amend":
            wip_status = api_status({})
        files, ctx, ref = wip_files(wip, wip_status), {}, "WIP"
        info = f'<div class="meta">{html.escape(tr("working_tree"))} — {html.escape(localized_wip_title(wip))}</div>'
        cmd = {"staged": f"git diff --cached -- {path}", "amend": f"git diff HEAD^ -- {path}",
               "untracked": f"git diff --no-index /dev/null {path}"}.get(wip, f"git diff -- {path}")
        if wip != "amend":  # amend の内容は、まだ存在しないコミットの変更なのでパッチにしない
            patch = ({"hash": WIP_HASH}, "wip.patch", "tip_patch_create_wip")
        view_hash = WIP_HASH if wip_file_exists(path) else None
    elif "from" in q:  # 2コミット間の比較
        c = api_compare(q)
        a, b = c["from"], c["to"]
        files, ctx = c["files"], {"from": a["hash"], "to": b["hash"]}
        ref = f"{a['hash'][:7]}..{b['hash'][:7]}"
        info = commit_line_html("A", a) + commit_line_html("B", b)
        cmd = f"git diff {a['hash'][:7]} {b['hash'][:7]} -- {path}"
        patch = ({"from": a["hash"], "to": b["hash"]}, f"{a['hash'][:7]}..{b['hash'][:7]}.patch", "tip_patch_create_compare")
        view_hash = next((h for h in (b["hash"], a["hash"]) if file_exists_at(h, path)), None)  # B で消えたファイルは A で開く
    else:
        c = api_commit(q)
        files, ctx, ref = c["files"], {"hash": c["hash"]}, c["hash"][:7]
        subject = c["message"].split("\n", 1)[0]
        info = (f'<div class="meta">{html.escape(tr("commit_label"))} {ref}'
                f'<a class="copy" data-copy="{c["hash"]}" title="{html.escape(tr("copy_hash_title"))}">📋</a>'
                f' — {html.escape(subject)}</div>'
                f'<div class="meta">{html.escape(c["author"])} / {html.escape(c["date"])}</div>')
        cmd = f"git show {ref} -- {path}"
        patch = ({"hash": c["hash"]}, f"{ref}.patch", "tip_patch_create_commit")
        view_hash = next((h for h in [c["hash"], *c["parents"][:1]] if file_exists_at(h, path)), None)  # 削除されたファイルは親で開く
    name = f"{old} => {path}" if old else path or tr("no_changes")
    edit_link = ""
    if wip and state.WRITABLE and state.HAS_WORKTREE and wip_file_exists(path):  # 作業ツリーにあるファイルは編集画面へ
        edit_url = "/files?" + urlencode({"hash": WIP_HASH, "path": dir_of(path), "file": path, "view": "edit"})
        edit_link = (f'<a class="ebtn" href="{html.escape(edit_url)}">{html.escape(tr("file_edit"))}</a>')

    files_link = ""
    if view_hash:  # この差分のファイルを、ファイル表示 (/files) で開く
        files_link = (f'<a class="ebtn" href="{html.escape(files_view_url(view_hash, path))}" '
                      f'title="{html.escape(tr("tip_diff_to_file"), quote=True)}">{html.escape(tr("files"))}</a>')
    patch_btn = ""
    if patch:  # そのコミット (比較・作業ツリー) の変更をパッチとしてダウンロードする
        params, patch_name, tip = patch
        patch_btn = (f'<button class="ebtn" id="pbtn" data-params="{html.escape(json.dumps(params), quote=True)}" '
                     f'data-name="{html.escape(patch_name, quote=True)}" title="{html.escape(tr(tip), quote=True)}">'
                     f'{html.escape(tr("patch_create"))}</button>')

    ext_diff = ""
    if state.DESKTOP and state.WRITABLE:  # pywebview など: 外部の差分ツール (WinMerge など) で開くボタン
        ext_diff = (f'<button class="ebtn" id="xdiff" title="{html.escape(tr("tip_open_diff_tool"), quote=True)}">'
                    f'{html.escape(tr("open_diff_tool"))}</button>')

    can_write = bool(files and wip_status is not None and state.WRITABLE and state.WIP_ENABLED)
    can_commit = can_write and not wip_status.get("applying")  # git am の競合は am --continue で確定する
    patch_bar = ""
    if patch and wip and not is_wip_compare and wip != "amend":  # 右ペインの WIP と同じ: パッチ作成と、書き込めるときの適用 (git apply / git am)
        attr_t = lambda key: html.escape(tr(key), quote=True)
        apply_btns = (f'<button class="wbtn" id="sidePatchApply" title="{attr_t("tip_patch_apply")}">{html.escape(tr("patch_apply"))}</button>'
                      f'<button class="wbtn" id="sidePatchAm" title="{attr_t("tip_patch_am")}">{html.escape(tr("patch_am"))}</button>'
                      '<input type="file" id="sidePatchFile" hidden>') if state.WRITABLE else ""
        patch_bar = (f'<div class="wippatch" id="sidePatch" data-wait="{attr_t("wip_execute")}" '
                     f'data-confirm-apply="{attr_t("confirm_patch_apply")}" data-confirm-am="{attr_t("confirm_patch_am")}">'
                     f'<button class="wbtn" id="sidePatchCreate" data-params="{html.escape(json.dumps(patch[0]), quote=True)}" '
                     f'data-name="{html.escape(patch[1], quote=True)}" title="{attr_t(patch[2])}">{html.escape(tr("patch_create"))}</button>'
                     f'{apply_btns}</div><div class="hint" id="spmsg"></div>')
        patch_btn = ""  # 見出し側のパッチ作成は、左ペインのものと重複するので出さない
    remote_bar = ""
    can_remote = bool(wip and not is_wip_compare and wip != "amend" and state.WRITABLE and state.WIP_ENABLED)
    if can_remote:  # グラフ画面のメニューバーと同じ push / pull / fetch (変更がなくても出す)
        remote_bar = ('<div class="wipremote" id="sideRemote">'
                      f'<button class="wbtn" data-remote="push" title="{html.escape(tr("tip_push_head"), quote=True)}">{html.escape(tr("push"))}</button>'
                      '<button class="wbtn" data-remote="pull">pull</button>'
                      '<button class="wbtn" data-remote="fetch">fetch</button></div>'
                      '<div class="hint" id="rmsg"></div>')
    # マージ中と、チェリーピック / リバートの途中は、競合ファイルを含めて全部チェック済みが既定 (これで1つのコミットが終わる)
    merging = bool(wip_status and (wip_status.get("merging") or wip_status.get("applying")
                                   or wip_status.get("cherry_picking") or wip_status.get("reverting")))
    rebasing = bool(wip_status and wip_status.get("rebasing"))
    op_in_progress = bool(wip_status and any(
        wip_status.get(key) for key in ("merging", "rebasing", "applying", "cherry_picking", "reverting", "stash_conflict")
    ))
    op_cmd = next((cmd for key, cmd in (("merging", "merge"), ("rebasing", "rebase"), ("applying", "am"),
                                        ("cherry_picking", "cherry-pick"), ("reverting", "revert"),
                                        ("stash_conflict", "stash")) if wip_status and wip_status.get(key)), None)

    # WIP: ディレクトリごとの件数・行数 (右ペインの filesHtml と同じ集計)
    dir_count, dir_stat = Counter(), {}
    if wip and not is_wip_compare:
        for f in files:
            key = (f.get("wip"), dir_of(f["path"]))
            dir_count[key] += 1
            st = dir_stat.setdefault(key, [0, 0])
            st[0] += f.get("added") or 0
            st[1] += f.get("deleted") or 0
    cur_dir_key = (wip, dir_of(path)) if wip and not is_wip_compare else None  # いま開いているファイルのディレクトリは最初から開く
    seen_dirs = set()
    attr = lambda s: html.escape(s, quote=True)

    side, last = [], None
    for f in files:
        if wip and not is_wip_compare and f.get("wip") != last:
            last = f.get("wip")
            gall = (f'<input type="checkbox" class="gall" data-kind="{html.escape(last)}" '
                    f'title="{html.escape(tr("select_all"))}"> ') if can_write else ""
            side.append(f'<div class="grp">{gall}{html.escape(localized_wip_title(last))}</div>')
            if last == "conflict" and can_write:  # 競合ファイルを ours / theirs で解決する (右ペインと同じ)
                side.append(f'<div class="wipres"><button class="wbtn" data-resolve="ours">{html.escape(tr("resolve_ours"))}</button>'
                            f'<button class="wbtn" data-resolve="theirs">{html.escape(tr("resolve_theirs"))}</button>'
                            f'<button class="wbtn" data-resolve="current">{html.escape(tr("resolve_current"))}</button>'
                            f'<span class="hint">{html.escape(tr("resolve_hint"))}{(" " + html.escape(tr("rebase_side_hint"))) if rebasing else ""}</span></div>')
        fdir = dir_of(f["path"])
        key = (f.get("wip"), fdir)
        folded = bool(wip and not is_wip_compare) and dir_count[key] >= WIP_DIR_COLLAPSE
        is_cur_dir = folded and key == cur_dir_key
        if folded and key not in seen_dirs:
            seen_dirs.add(key)
            dck = (f'<input type="checkbox" class="dck" data-kind="{attr(f["wip"])}" data-dir="{attr(fdir)}"> '
                   if can_write else "")
            data_cur = ' data-cur="1"' if is_cur_dir else ""
            label = fdir + "/" if fdir else tr("dir_root")
            plus, minus = dir_stat[key]
            side.append(
                f'<div class="srow drow" data-kind="{attr(f["wip"])}" data-dir="{attr(fdir)}"'
                f'{data_cur} title="{attr(tr("dir_toggle"))}">{dck}'
                f'<span class="dname"><span class="dtog">▶</span> {html.escape(label)} '
                f'<span class="hint">({html.escape(tr("n_items", n=dir_count[key]))}) '
                f'<span class="plus">+{plus}</span> <span class="minus">-{minus}</span></span></span></div>')
        fname = f"{f['old_path']} => {f['path']}" if f.get("old_path") else f["path"]
        if f.get("label"):
            counts = html.escape(tr(f["label"]))
        elif "added" not in f:
            counts = ""
        elif f["added"] is None:
            counts = "Bin"
        else:
            counts = (f'<span class="plus">+{f["added"]}</span> '
                      f'<span class="minus">-{f["deleted"]}</span>')
        cur = "cur" if f["path"] == path and (not wip or is_wip_compare or f.get("wip") == wip) else ""
        link = (f'<a class="{cur}" href="{html.escape(diff_url(ctx, f))}">'
                f'<span>{html.escape(fname)}</span><span class="n">{counts}</span></a>')
        if wip and not is_wip_compare:
            box = ""
            if can_write:
                # ステージ済みと未ステージは別々にチェックする。マージ中は、未追跡ファイル以外は全部チェック済みが既定
                on = " checked" if ((merging and f.get("wip") != "untracked") or f.get("wip") == "staged") else ""
                box = (f'<input type="checkbox" class="wck" data-kind="{attr(f["wip"])}" '
                       f'data-dir="{attr(fdir)}" data-path="{attr(f["path"])}"{on}>')
            hide = " dhide" if folded and not is_cur_dir else ""
            link = (f'<div class="srow frow{hide}" data-kind="{attr(f["wip"])}" '
                    f'data-dir="{attr(fdir)}">{box}{link}</div>')
        side.append(link)
    commit_box = ""
    commit_js = ""
    fold_js = DIFF_FOLD_JS if wip and not is_wip_compare else ""
    if can_commit:
        commit_box = ('<div class="cbox"><textarea id="cmsg" placeholder="' + html.escape(tr("wip_commit_message"), quote=True) + '"></textarea>'
                      '<input type="text" id="cbranch" placeholder="' + html.escape(tr("wip_commit_branch_placeholder"), quote=True)
                      + '" aria-label="' + html.escape(tr("wip_commit_branch"), quote=True) + '">'
                      '<div class="arow"><button id="cbtn" class="wbtn">' + html.escape(tr("wip_commit")) + '</button>'
                      + ('<button id="sbtn" class="wbtn" title="' + html.escape(tr("tip_wip_stash"), quote=True) + '">' + html.escape(tr("wip_stash")) + '</button>' if not op_in_progress else "")
                      + ('<button id="dbtn" class="wbtn danger">' + html.escape(tr("wip_discard")) + '</button>' if not op_in_progress else "")
                      + '<button id="rbtn" class="wbtn danger" title="' + html.escape(tr("tip_wip_rm"), quote=True) + '">' + html.escape(tr("wip_rm")) + '</button>'
                      + '<span class="hint">' + html.escape(tr("wip_hint")) + '</span></div>'
                      '<div id="cout" class="hint"></div></div>')
    if can_write and op_cmd:
        banner = tr("op_banner_stash") if op_cmd == "stash" else tr("op_banner", cmd=op_cmd)
        abort_text = tr("abort_button_stash") if op_cmd == "stash" else tr("abort_button", cmd=op_cmd)
        cont = (f'<button class="wbtn" id="ctbtn">{html.escape(tr("continue_button"))}</button>'
                if op_cmd == "rebase" and state.WRITABLE else "")
        commit_box = (f'<div class="wipop"><span>{html.escape(banner)}</span>{cont}'
                      f'<button class="wbtn danger" id="abtn" data-cmd="{html.escape(op_cmd, quote=True)}">'
                      f'{html.escape(abort_text)}</button><div id="cout" class="hint"></div></div>') + commit_box
    commit_keys = ()
    if can_write:
        commit_keys = (
            "wip_commit", "message_required", "select_files", "wip_execute",
            "wip_discard", "confirm_discard", "discard_conflict", "cancel",
            "wip_rm", "rm_dialog_note", "rm_cached_label", "rm_force_label", "rm_untracked",
            "wip_stash", "stash_message", "stash_scope", "stash_scope_all", "stash_scope_selected", "stash_include_untracked",
            "confirm_resolve", "confirm_resolve_current", "confirm_abort", "confirm_abort_stash",
        )
        commit_js = WIP_DIFF_COMMIT_JS
    # 文言 T と共通のダイアログ部品は、コミット欄と push / pull 欄のどちらかがあるときに1度だけ入れる
    text_keys = [*commit_keys, *(REMOTE_TEXT_KEYS if remote_bar else ())]
    dialog_js = ""
    if text_keys:
        texts = {k: tr(k) for k in dict.fromkeys(text_keys)}
        dialog_js = "const T = " + json.dumps(texts, ensure_ascii=False).replace("</", "<\\/") + ";\n" + DIFF_DIALOG_JS
    remote_js = WIP_DIFF_REMOTE_JS if remote_bar else ""
    # グラフへ戻るとき、この差分のコミットを選択状態で復元する (作業ツリーだけの WIP は対象外)
    graph_href = "/"
    if wip == "compare":
        sel = ctx.get("from")  # 比較元コミット
    elif not wip:
        sel = ctx.get("hash") or ctx.get("to") or ctx.get("from")
    else:
        sel = None
    if sel:
        graph_href = "/?" + urlencode({"select_hash": sel})

    return f"""<!DOCTYPE html>
<html lang="{state.UI_LANG}"><head><meta charset="utf-8">
<title>{html.escape(state.PROJECT_NAME)} - {html.escape(path)} @ {ref}</title>
{FAVICON_LINK}
<style>{DIFF_CSS}</style></head><body>
<div id="side">
  <div class="navbar">{page_nav_html("diff", files_href=files_view_url(view_hash, path) if view_hash and path else None, graph_href=graph_href)}{NAV_BACK_BUTTON.format(label=html.escape(tr("back_prev"), quote=True))}{NAV_RELOAD_BUTTON.format(label=html.escape(tr("reload_page"), quote=True)) if wip else ""}</div>
  <div class="meta">{len(files)} {tr("files_changed")}</div>
  {patch_bar}
  {remote_bar}
  {f'<div class="meta hint">{html.escape(tr("no_changes"))}</div>' if not files else ""}
  {commit_box}
  {"".join(side)}
</div>
<div id="sideSplitter" role="separator" aria-orientation="vertical" aria-label="{html.escape(tr("resize_side"), quote=True)}" tabindex="0"></div>
<div id="main">
  <h1>{html.escape(name)}{edit_link}{files_link}{patch_btn}{ext_diff}</h1>
  {'<div class="meta hint" id="pmsg"></div>' if patch_btn else ""}
  {'<div class="meta hint" id="xmsg"></div>' if ext_diff else ""}
  {info}
  <div class="meta"><code>{html.escape(cmd)}</code></div>
  <div class="meta">{html.escape(d["encoding"] or "binary")}</div>
  {'<p class="hint">' + tr("large_file_diff") + '</p>' if d["truncated"] else ""}
  <div class="diff">{diff_lines_html(d["diff"]) or '<p class="hint">' + tr("no_diff") + '</p>'}</div>
</div>
<script>{COPY_JS}</script>
<script>{PANE_RESIZE_JS}</script>
<script>{fold_js}</script>
<script>{dialog_js}</script>
<script>{commit_js}</script>
<script>{remote_js}</script>
<script>{EXT_DIFF_JS if ext_diff else ""}</script>
<script>{DIFF_PATCH_JS if patch_btn or patch_bar else ""}</script>
<script>{DIFF_RELOAD_JS if wip else ""}</script>
</body></html>
"""


def render_files_page():
    return with_project(fill_template(FILES_PAGE)
            .replace('<html lang="ja">', f'<html lang="{state.UI_LANG}">')
            .replace("/*__PANE_RESIZE__*/", PANE_RESIZE_JS)
            .replace("/*__FILE_I18N__*/", "const I18N = " + client_translations() + ";\n"
                     + "const CAN_COPY = " + ("true" if state.WRITABLE and state.HAS_WORKTREE else "false") + ";\n"
                     + "const DESKTOP = " + ("true" if state.DESKTOP and state.WRITABLE and state.HAS_WORKTREE else "false") + ";"))


__all__ = [
    "WIP_DIR_COLLAPSE", "diff_lines_html", "diff_url", "dir_of", "localized_wip_title",
    "render_diff_page", "render_files_page", "wip_files",
]
