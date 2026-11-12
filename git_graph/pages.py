# HTML ページ (/diff 差分ページと /files ファイル表示ページ) の組み立て。
import html
import json
import os
import re
from urllib.parse import urlencode
from . import app_state as state
from .history import api_commit, api_compare, api_diff
from .i18n import (
    client_translations, FAVICON_LINK, FILES_PAGE, fill_template, MENUBAR_TEMPLATE, MERGE_PAGE, read_asset, tr, UserError,
    with_project,
)
from .external import configured
from .wip import api_status
from .files import validate_file_path, WIP_HASH, worktree_target
from .query.files import file_exists_at


def wip_file_exists(path):
    try:
        target = worktree_target(path)
    except ValueError:  # UserError も含む
        return False
    return os.path.isfile(target) and not os.path.islink(target)

COPY_JS = read_asset("copy.js")
DIFF_CSS = read_asset("diff.css")
DIFF_KEYBOARD_JS = read_asset("diff_keyboard.js")
DIFF_RELOAD_JS = read_asset("diff_reload.js")
PANE_RESIZE_JS = read_asset("pane_resize.js")
WIP_PANEL_JS = read_asset("wip_panel.js")  # 作業ツリーの変更の一覧とコミットフォーム。グラフ・ファイル表示・差分で共通
WIP_PANEL_MOUNT_JS = read_asset("wip_panel_mount.js")  # ファイル表示 / 差分ページへ置くための接続
WIP_PANEL_CSS = read_asset("wip_panel.css")
# WIP パネルが使う文言は、パネルの中の t("...") から集める (書き足したときに、ここを直し忘れない)。t(f.label) は、ファイルの種類の表示
WIP_PANEL_TEXT_KEYS = (*dict.fromkeys(re.findall(r'\bt\("([A-Za-z0-9_]+)"', WIP_PANEL_JS)), "label_conflict", "new_file")
WIP_DIFF_REMOTE_JS = read_asset("wip_diff_remote.js")
DIFF_DIALOG_JS = read_asset("diff_dialog.js")
MENUBAR_CSS = read_asset("menubar.css")
PAGE_MENUBAR_JS = read_asset("page_menubar.js")
MERGE_JS = read_asset("merge.js")
MERGE_EDITOR_JS = read_asset("vendor/merge_editor.js")  # CodeMirror 6 をまとめたもの (tools/merge_editor でビルド)
TEXT_EDITOR_JS = read_asset("vendor/text_editor.js")  # ファイル編集のエディタ (CodeMirror 6 + 言語 + Vim)。こちらも tools/merge_editor でビルド
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
# /diff と /files の共通メニューバー (page_menubar.js) が使う文言
MENUBAR_TEXT_KEYS = (
    "cancel", "ok", "create", "create_branch", "create_tag", "branch_name", "tag_name", "message_optional",
    "checkout_after_create", "branch_name_required", "tag_name_required", "confirm_create_checkout",
    "target_label", "tip_go_commit", "mb_no_commit", "mb_unavailable", "operation_cancelled",
    "set_as_a", "set_as_b", "unset_a", "unset_b", "server_restarting", "server_stopped", "quit_failed",
    "co_blocked_op", "co_blocked_hint_op", "co_modified", "co_untracked_op", "co_hint_stash", "co_hint_discard",
    "co_stash_retry", "co_discard_retry",
    "running", "checkout", "switch_to", "checkout_detached_option", "checkout_new_branch_option", "new_branch_name",
    "new_branch_placeholder", "track_upstream", "new_branch_name_required", "confirm_checkout_branch",
    "confirm_checkout_detached", "checkout_cancelled",
    "reset", "reset_mode", "reset_opt_mixed", "reset_opt_soft", "reset_opt_hard", "reset_submit", "confirm_reset_hard",
    "confirm_reset_soft", "confirm_reset_mixed", "resetting", "reset_error",
)
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
        ident = f'id="nav{kind.capitalize()}Btn"'  # グラフ / ファイル表示の JS が、リンクやボタンを付け替えるのに使う
        if kind == current:
            return (f'<span class="iconbtn current page-nav" {ident} title="{label}" aria-label="{label}" aria-current="page">'
                    f'{svg}</span>')
        if href:
            return (f'<a class="sbtn iconbtn page-nav" {ident} href="{html.escape(href, quote=True)}" title="{label}" aria-label="{label}">'
                    f'{svg}</a>')
        return (f'<button type="button" class="sbtn iconbtn page-nav" {ident} disabled title="{label}" aria-label="{label}">'
                f'{svg}</button>')
    return (item("graph", graph_href, "nav_graph", _ICON_GRAPH)
            + item("files", files_href, "nav_files", _ICON_FILES)
            + item("diff", diff_href, "nav_diff", _ICON_DIFF))


EXT_DIFF_JS = read_asset("ext_diff.js")
DIFF_PATCH_JS = read_asset("diff_patch.js")


def menubar_html(page, *, files_href=None, diff_href=None, graph_href="/", reload_id="reload", reload_enabled=False,
                 commit="", wip=False):
    """全画面 (グラフ / ファイル表示 / 差分) 共通のメニューバー。static/menubar.html を 1 つの定義として使い、
    ページごとに変わるのは、ページ切り替えのリンク・再読み込みボタンの id と有効 / 無効・data 属性だけ。
    その画面でできない操作は、page_menubar.js (グラフ以外の画面) が無効にする。
    commit: 表示中のコミット (作業ツリーなどは空)。wip: 作業ツリーの変更を表示している。"""
    out = fill_template(MENUBAR_TEMPLATE).replace("<!--__NAV__-->", page_nav_html(page, files_href, diff_href, graph_href))
    reload_old = '<button id="reload" class="sbtn iconbtn" disabled'
    assert reload_old in out
    out = out.replace(reload_old, f'<button id="{reload_id}" class="sbtn iconbtn"' + ("" if reload_enabled else " disabled"), 1)
    toolbar_old = '<div id="compareToolbar" role="menubar">'
    assert toolbar_old in out
    attrs = (f'data-page="{page}" data-commit="{html.escape(commit, quote=True)}" data-wip="{1 if wip else 0}" '
             f'data-writable="{1 if state.WRITABLE else 0}" data-worktree="{1 if state.WIP_ENABLED else 0}"')
    return out.replace(toolbar_old, f'<div id="compareToolbar" role="menubar" {attrs}>', 1)


def bar_has_remote():
    """push / pull / fetch を使えるか (書き込めて、作業ツリーを扱えるとき)。使えないときは、メニューバーのボタンを無効にする。"""
    return bool(state.WRITABLE and state.WIP_ENABLED)


def menubar_dialog_js(extra_keys=()):
    """文言 T (メニューバーの分と extra_keys) と、入力ダイアログの共通部品 (diff_dialog.js)。ページに 1 度だけ入れる。"""
    texts = {k: tr(k) for k in dict.fromkeys([*MENUBAR_TEXT_KEYS, *extra_keys])}
    return "const T = " + json.dumps(texts, ensure_ascii=False).replace("</", "<\\/") + ";\n" + DIFF_DIALOG_JS




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
    no_file_page = not path  # ファイルを選んでいない表示 (WIP の一覧や、メニューバーから別のコミットへ移ったとき)。一覧だけを出す
    d = ({"diff": "", "encoding": "UTF-8", "truncated": False}
         if no_file_page else api_diff(q))
    bar_commit = ""  # いま表示しているコミット (メニューバーでブランチ・タグの作成元にする)。作業ツリーだけの表示は空
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
        bar_commit = a["hash"]
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
        bar_commit = b["hash"]
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
        bar_commit = c["hash"]
    if not path:
        view_hash = None  # ファイルを選んでいないときは、ファイル表示へ飛ぶ先が無い (空のパスはルートのツリーとして存在してしまう)
    name = f"{old} => {path}" if old else path or (tr("select_file") if files else tr("no_changes"))
    edit_link = ""
    if wip and state.WRITABLE and state.HAS_WORKTREE and wip_file_exists(path):  # 作業ツリーにあるファイルは編集画面へ
        edit_url = "/files?" + urlencode({"hash": WIP_HASH, "path": dir_of(path), "file": path, "view": "edit"})
        edit_link = (f'<a class="ebtn" href="{html.escape(edit_url)}">{html.escape(tr("file_edit"))}</a>')

    merge_link = ""
    if wip == "conflict" and path and state.WRITABLE and state.HAS_WORKTREE and wip_file_exists(path):  # 競合しているファイルは、マージエディタで解決できる
        merge_link = (f'<a class="ebtn" href="{html.escape("/merge?" + urlencode({"path": path}))}" '
                      f'title="{html.escape(tr("tip_merge_editor"), quote=True)}">{html.escape(tr("merge_editor"))}</a>')

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
    remote_bar = bar_has_remote()  # グラフ画面と同じ push / pull / fetch は、メニューバーに出す (変更がなくても、どの差分ページでも)
    # 作業ツリーの変更: 一覧とコミットフォームは、グラフ・ファイル表示と共通の WIP パネル (wip_panel.js) が、ブラウザで描く。
    # (画面ごとに作り直すと、amend や差分マークなどが画面によって違ってしまうため)。コミットや比較のときだけ、ここで一覧を作る
    use_panel = bool(wip and not is_wip_compare)
    side = []
    if not use_panel:
        for f in files:
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
            cur = "cur" if f["path"] == path else ""
            side.append(f'<a class="{cur}" href="{html.escape(diff_url(ctx, f))}">'
                        f'<span>{html.escape(fname)}</span><span class="n">{counts}</span></a>')
    side_head = "" if use_panel else (f'<div class="meta">{len(files)} {tr("files_changed")}</div>'
                                      + ("" if files else f'<div class="meta hint">{html.escape(tr("no_changes"))}</div>'))
    panel_box = '<div id="wipPanel"></div>' if use_panel else ""
    panel_js = (WIP_PANEL_JS + "\n" + WIP_PANEL_MOUNT_JS + '\nmountWipPanel(document.getElementById("wipPanel"), {diff: true});') if use_panel else ""
    # 文言 T と共通のダイアログ部品は、メニューバー・コミット欄・push / pull で共通なので、1度だけ入れる
    dialog_js = menubar_dialog_js([*(WIP_PANEL_TEXT_KEYS if use_panel else ()), *(REMOTE_TEXT_KEYS if remote_bar else ())])
    remote_js = WIP_DIFF_REMOTE_JS if remote_bar else ""
    # グラフへ戻るとき、この差分のコミットを選択状態で復元する。作業ツリーの WIP は、WIP ノードを選んだ状態に戻す (HEAD にしない)
    graph_href = "/"
    if wip == "compare":
        sel = ctx.get("from")  # 比較元コミット
    elif not wip:
        sel = ctx.get("hash") or ctx.get("to") or ctx.get("from")
    else:
        sel = None
    if sel:
        graph_href = "/?" + urlencode({"select_hash": sel})
    elif wip and wip != "compare":
        graph_href = "/?select_wip=1"

    # ファイル表示へは、いつでも移れる。開いているファイルがあればそのファイルを、無ければ (削除されたファイルなども) このコミット / 作業ツリーのルートを開く
    files_href = (files_view_url(view_hash, path) if view_hash and path
                  else "/files?" + urlencode({"hash": bar_commit or WIP_HASH}))
    menubar = menubar_html("diff", files_href=files_href, graph_href=graph_href, reload_id="rld", reload_enabled=bool(wip),
                           commit=bar_commit, wip=bool(wip and not is_wip_compare))
    return f"""<!DOCTYPE html>
<html lang="{state.UI_LANG}"><head><meta charset="utf-8">
<title>{html.escape(state.PROJECT_NAME)} - {html.escape(path)} @ {ref}</title>
{FAVICON_LINK}
<style>{DIFF_CSS}{MENUBAR_CSS}{WIP_PANEL_CSS}</style></head><body>
{menubar}
<div id="panes">
<div id="side">
  {side_head}
  {patch_bar}
  {panel_box}
  {"".join(side)}
</div>
<div id="sideSplitter" role="separator" aria-orientation="vertical" aria-label="{html.escape(tr("resize_side"), quote=True)}" tabindex="0"></div>
<div id="main">
  <h1>{html.escape(name)}{merge_link}{edit_link}{files_link}{patch_btn}{ext_diff}</h1>
  {'<div class="meta hint" id="pmsg"></div>' if patch_btn else ""}
  {'<div class="meta hint" id="xmsg"></div>' if ext_diff else ""}
  {info}
  <div class="meta"><code>{html.escape(cmd)}</code></div>
  <div class="meta">{html.escape(d["encoding"] or "binary")}</div>
  {'<p class="hint">' + tr("large_file_diff") + '</p>' if d["truncated"] else ""}
  <div class="diff">{diff_lines_html(d["diff"]) or ("" if no_file_page and files else '<p class="hint">' + tr("no_diff") + '</p>')}</div>
</div>
</div>
<script>{COPY_JS}</script>
<script>{PANE_RESIZE_JS}</script>
<script>{dialog_js}</script>
<script>{PAGE_MENUBAR_JS}</script>
<script>{panel_js}</script>
<script>{remote_js}</script>
<script>{EXT_DIFF_JS if ext_diff else ""}</script>
<script>{DIFF_PATCH_JS if patch_btn or patch_bar else ""}</script>
<script>{DIFF_RELOAD_JS if wip else ""}</script>
<script>{DIFF_KEYBOARD_JS}</script>
</body></html>
"""


def render_files_page():
    remote = bar_has_remote()
    # 作業ツリー (hash=WIP) を開いているときは、左ペインに、グラフ・差分と共通の WIP の一覧とコミットフォームを置く
    mount = ('if (new URLSearchParams(location.search).get("hash") === "WIP") {\n'
             '  document.getElementById("wipPanelBox").hidden = false;\n'
             '  mountWipPanel(document.getElementById("wipPanel"));\n}')
    menubar_js = "\n".join((menubar_dialog_js([*WIP_PANEL_TEXT_KEYS, *(REMOTE_TEXT_KEYS if remote else ())]), PAGE_MENUBAR_JS,
                            WIP_PANEL_JS, WIP_PANEL_MOUNT_JS, WIP_DIFF_REMOTE_JS if remote else "", mount))
    # グラフへ戻るリンクと差分ボタンは、ページの JS が表示中のコミット / ファイルに合わせて付け替える
    menubar = menubar_html("files", reload_id="reloadBtn")
    return with_project(fill_template(FILES_PAGE)
            .replace('<html lang="ja">', f'<html lang="{state.UI_LANG}">')
            .replace("/*__MENUBAR_CSS__*/", MENUBAR_CSS + WIP_PANEL_CSS)
            .replace("<!--__MENUBAR__-->", menubar)
            .replace("/*__PANE_RESIZE__*/", PANE_RESIZE_JS)
            .replace("/*__FILE_I18N__*/", "const I18N = " + client_translations() + ";\n"
                     + "const CAN_COPY = " + ("true" if state.WRITABLE and state.HAS_WORKTREE else "false") + ";\n"
                     + "const DESKTOP = " + ("true" if state.DESKTOP and state.WRITABLE and state.HAS_WORKTREE else "false") + ";")
            .replace("/*__MENUBAR_JS__*/", menubar_js))


def render_merge_page(q):
    """競合しているファイルのマージエディタ (/merge?path=...)。内容は /api/merge-sources で読み込む。"""
    path = validate_file_path(q.get("path", [""])[0], allow_empty=False)
    back = "/diff?" + urlencode({"wip": "conflict", "path": path})
    values = {"PATH": html.escape(path), "BACK": html.escape(back)}
    page = re.sub(r"__(PATH|BACK)__", lambda m: values[m.group(1)],  # 1 回の置換で済ませる (パスの中に __BACK__ があっても置き換えない)
                  with_project(fill_template(MERGE_PAGE)).replace('<html lang="ja">', f'<html lang="{state.UI_LANG}">'))
    return (page
            .replace("/*__MERGE_I18N__*/", "const I18N = " + client_translations() + ";\n"
                     + "const MERGE_PATH = " + json.dumps(path, ensure_ascii=False).replace("</", "<\\/") + ";")
            .replace("/*__MERGE_JS__*/", MERGE_JS)
            .replace("/*__MERGE_EDITOR__*/", MERGE_EDITOR_JS))


__all__ = [
    "diff_lines_html", "diff_url", "dir_of", "localized_wip_title",
    "TEXT_EDITOR_JS", "render_diff_page", "render_files_page", "render_merge_page", "wip_files",
]
