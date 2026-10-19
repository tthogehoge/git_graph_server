#!/usr/bin/env python3
"""git のコミットグラフを、ブランチ/タグ/末端 + それらの分岐点(merge-base)だけに省略して
ブラウザで表示するローカルサーバー。ログやコミット詳細はクリック時に git から動的に取得する。

使い方 (標準ライブラリのみ。Graphviz の dot が必要):
  python git_graph_server.py                    # カレントディレクトリのリポジトリ
  python git_graph_server.py -C /path/to/repo --open
  python git_graph_server.py --no-remotes --no-tags main dev
  python git_graph_server.py --dot              # dot を標準出力に出して終了
  python git_graph_server.py --lang en          # UI の言語 (ja / en)。省略時は環境変数 LANGUAGE / LC_ALL / LC_MESSAGES / LANG から決める (未対応の言語は en)
  python git_graph_server.py --check-i18n       # 翻訳の漏れ (キー不一致・未翻訳のリテラル) を検査して終了

  http://127.0.0.1:8000/                 グラフ (リロードで最新の状態に更新)
  /api/log?rev=<40桁hash>[..<40桁hash>]&path=<ファイル>&skip=0&limit=500   git log (ページング、path指定でファイル限定)
  /api/commit?hash=<hash>                                    git show 相当 (変更ファイル一覧つき)
  /files?hash=<40桁hash>                                     コミットのファイル一覧 (別タブで開く)
  /api/tree?hash=<hash>&path=<directory>                     コミットのディレクトリ内容
  /api/file?hash=<hash>&path=<file>                          コミットのファイル内容
  /diff?hash=<hash>&path=<file>[&old=<旧パス>]               1ファイルの差分ページ (別タブで開く)
  /diff?from=<hash>&to=<hash>&path=<file>[&old=<旧パス>]     2コミット間の差分ページ
  /api/graph                                                 グラフ(SVG + ノード/エッジ情報。未コミットの変更があれば WIP ノードつき)
  /api/status                                                作業ツリーの変更 (ステージ済み/未ステージ/未追跡/競合)。?amend=1 で 直前のコミットの親との差分 (amend_files) も返す
  /diff?wip=staged|unstaged|untracked|conflict|amend&path=<file>   作業ツリーの変更の差分ページ
  /api/compare?from=<hash>&to=<hash>                         2コミット間の変更ファイル一覧
  POST /api/branch | /api/tag | /api/checkout (JSON)         ブランチ作成 / タグ作成 / チェックアウト (画面では、比較のAだけを選んだときの操作)
     チェックアウトが「編集中のファイル」「同名の管理外ファイル」で止まったときは {blocked:true, modified:[..], untracked:[..]} を返す。
     on_conflict="stash" (全変更を stash -u して実行) / "discard" (止めているファイルだけ破棄・削除して実行) を付けて再送する
  POST /api/merge {hash, no_ff} (JSON)                       選んだコミットを現在の HEAD にマージ
  POST /api/reset {hash, mode} (JSON)                       現在の HEAD を選んだコミットへ reset (mode: soft | mixed | hard)
  POST /api/rebase {hash} (JSON)                             現在のブランチを選んだコミットの上にリベース (競合したら中止して元に戻す)
  POST /api/rebase-range {from, to} (JSON)                   A以降の現在のブランチをBの上にリベース (A→B。競合したら中止して元に戻す)
  POST /api/cherry-pick {hash} (JSON)                        選んだコミットを現在の HEAD にチェリーピック (競合したら中止して元に戻す)
  POST /api/revert {hash} (JSON)                             選んだコミットを打ち消すコミットを作る (git revert。マージコミットは第1親基準。競合したら中止して元に戻す)
  POST /api/abort (JSON)                                     進行中のマージ / リベース / チェリーピック / リバートを --abort で中断
  POST /api/resolve {paths, side} (JSON)                     競合ファイルを side (ours | theirs) の内容で丸ごと置き換えて解決済みにする (片側で削除されていたら削除)
  POST /api/discard {paths} (JSON)                           WIP のファイルの変更を破棄 (追跡中は HEAD に戻す / 新規ファイル・未追跡は削除。取り消せない。マージなどの途中と競合ファイルは不可)
  POST /api/branch-rename {old, new} (JSON)                  ローカルブランチ名の変更 (git branch -m。リモートのブランチは変わらない)
  /api/rebase-todo?hash=<hash>                               対話的リベースの対象コミット一覧 (古い順。git rebase -i が作る todo と同じ)
  POST /api/rebase-i {hash, todo:[{hash, action, message}]}  対話的リベース (git rebase -i 相当)
     action は pick / reword (message が新しいコミットメッセージ) / squash / fixup / drop。todo の並び順がそのまま適用順になる。
     競合やエラーで止まったときは自動で中止して元に戻す (edit は未対応)
  /api/head                                                  現在のブランチ名 (マージ/リベース前の確認用)
  /api/stash-list                                            stash の一覧 (git stash list)。画面では「操作」メニューの「stash一覧」
  POST /api/stash {hash, op} (JSON)                          stash の apply / pop / drop (hash は stash のコミット。op は apply | pop | drop)。画面では、stash を比較のAに選んだときの操作
  /api/reflog?[branch=<名前>]&skip=0&limit=500               reflog (省略時は HEAD、branch 指定でそのローカルブランチ)。画面では「操作」メニューの「reflog」
  POST /api/fetch {prune} | /api/pull {mode} | /api/push {branch, force, set_upstream} (JSON)
  POST /api/push-tag {tag, remote} (JSON)                       タグを push (リモートが複数で決まらないときは needs_remote を返す)
  POST /api/file-copy {hash, path, dest} (JSON)                 コミットのファイルを作業ツリーの別名にコピー (既存ファイルは上書きしない)
     fetch (--all) / pull (mode: ff-only | merge | rebase) / push (branch 指定でそのローカルブランチ、省略で現在のブランチ。force は --force-with-lease)
     push 先は git と同じ優先順位で決める: branch.<名前>.pushRemote > remote.pushDefault > upstream (branch.<名前>.remote)。
     どれも無いとき (リモートが複数なら) {needs_upstream, remote, remotes} を返し、remote を付けて set_upstream:true で再送する
     画面では、比較のAだけを選んだときの「操作」パネルにある。認証の入力待ちで固まらないよう、端末入力を無効にして一定時間でタイムアウトする
  /api/remotes                                               登録済みのリモート [{name, url}]
  POST /api/remote-add {name, url, fetch?} / /api/remote-remove {name} (JSON)   リモートの追加 (任意で fetch) / 削除 (リモート追跡ブランチも消える)
  POST /api/wip-commit {paths, message, amend?} (JSON)        チェックしたファイルを add してコミット (amend=true なら直前のコミットを作り直す。amend のときだけ paths は空でも可)
     書き込みは 127.0.0.1 で待ち受けているときだけ有効 (--read-only で無効化、--allow-write で他アドレスでも許可)
     --lan は --host 0.0.0.0 --allow-write の省略形 (LAN の他の端末から操作できる。認証はないので信頼できるネットワークで使うこと)
"""
import argparse
import ast
import html
import itertools
import json
import codecs
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import webbrowser
from collections import Counter, defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlencode, urlparse

REPO = "."
PROJECT_NAME = ""  # タブのタイトルに出すプロジェクト名 (リポジトリのフォルダ名)
_FAVICON_SVG = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
                "<path d='M16 7V25M16 16C16 11 24 13 24 7' stroke='#889' stroke-width='3' fill='none'/>"
                "<circle cx='16' cy='25' r='4' fill='#2c7be5'/><circle cx='16' cy='16' r='4' fill='#2c7be5'/>"
                "<circle cx='16' cy='7' r='4' fill='#2c7be5'/><circle cx='24' cy='7' r='4' fill='#27ae60'/></svg>")
FAVICON_LINK = '<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,' + quote(_FAVICON_SVG) + '">'
OPTS = None
UI_LANG = "ja"  # サーバー起動時に決定する UI の言語
WRITABLE = False  # ブランチ/タグ作成・チェックアウト等の書き込み操作を許可するか
WIP_ENABLED = False  # 未コミットの変更を WIP ノードとしてグラフに出すか
HAS_WORKTREE = False  # ワークツリーのあるリポジトリか (bare でないか)
FAST_LAYOUT_NODES = 300  # これより多くのノードを描くときは dot の最適化を軽くする (--full-layout で無効)
COLORS = {"branch": "#b7e4a7", "current": "#b7e4a7", "remote": "#a7c8f0", "tag": "#ffe9a0", "head": "#f4a7a7"}

# UI の翻訳データ。外部ファイルを使わず、このファイルだけで完結させる。
TRANSLATIONS = {
    "ja": {
        "reload": "↻ 更新",
        "clear": "クリア",
        "actions": "操作 ▾",
        "ab_actions": "AB操作 ▾",
        "cancel": "キャンセル",
        "loading_graph": "コミットグラフを作成中…",
        "loading_graph_hint": "リポジトリが大きいと時間がかかります",
        "create_branch": "ブランチ作成",
        "create_tag": "タグ作成",
        "checkout": "チェックアウト",
        "files": "ファイル表示",
        "merge": "マージ",
        "rebase": "リベース",
        "rebase_range": "A..HEADをBへリベース",
        "cherry_pick": "チェリーピック",
        "reset": "対象へリセット",
        "interactive_rebase": "対話的リベース",
        "compare_ab": "AB比較",
        "compare_logs_ab": "ABログ比較",
        "push": "push",
        "push_branch_select": "pushするブランチ",
        "push_remote_select": "push先のremote",
        "push_remote_branch": "push先のremoteブランチ名",
        "http_auth": "HTTP認証",
        "auth_username": "ユーザー名",
        "auth_password": "パスワード / トークン",
        "authenticate": "認証して再試行",
        "other": "その他 ▾",
        "menu_select_commit": "グラフのコミットをクリックして選ぶと、そのコミットに対する操作がここに出ます",
        "tip_push_head": "現在のブランチ (HEAD) を push (ダイアログで他のブランチも選べます)",
        "tip_push_selected": "選択しているコミットのブランチを push",
        "stash_list": "stash一覧",
        "reflog": "reflog",
        "stash_apply": "stash 適用 (apply)",
        "stash_pop": "stash 適用して削除 (pop)",
        "stash_drop": "stash 削除 (drop)",
        "remote_add": "remote 追加",
        "remote_remove": "remote 削除",
        "checkout_blocked": "チェックアウトできません",
        "checkout_blocked_hint": "次のファイルが邪魔をしています。",
        "stash_checkout": "stash してチェックアウト",
        "discard_checkout": "削除してチェックアウト",
        "graph_help_1": "ノードをクリックすると、そのコミットから最初（ルート）までのログを表示します。エッジをクリックすると省略された区間のログを表示します。",
        "graph_help_2": "コミットをクリックすると操作対象になります。ログ行の上にマウスを置くと、比較 A / B に設定するボタンが表示されます。",
        "graph_zoom_reset": "ズームを100%に戻す (Ctrl/⌘+0)",
        "current_branch": "カレントブランチ",
        "reload_busy": "更新中…",
        "target_label": "対象",
        "commit_label": "コミット",
        "back_history": "← 履歴一覧に戻る",
        "all_log_to_root": "最初のコミットまでの全ログ",
        "file_view": "ファイル表示",
        "select_file": "ファイルを選択してください",
        "select_file_hint": "左の一覧からファイルをクリックしてください。",
        "file_log": "このファイルのログ",
        "file_content": "ファイル内容",
        "copy_to_work": "作業ツリーにコピー",
        "prompt_copy_dest": "コピー先のパスを入力してください (リポジトリのルートからの相対パス)",
        "file_copied": "{path} にコピーしました",
        "copy_dest_exists": "{path} は既に存在します。別の名前を指定してください",
        "copy_dest_invalid": "コピー先のパスが不正です",
        "copy_no_worktree": "作業ツリーがないためコピーできません",
        "binary_file": "バイナリファイルのため内容を表示できません。",
        "loading": "読み込み中…",
        "error": "エラー: ",
        "root": "ルート",
        "empty_directory": "空のディレクトリです",
        "wip_loading": "読み込み中…",
        "wip_title": "作業ツリーの変更 (WIP)",
        "no_changes": "変更はありません",
        "wip_commit_message": "コミットメッセージ",
        "wip_commit": "コミット",
        "wip_execute": "実行中…",
        "staged": "ステージ済み (git add 済み)",
        "unstaged": "未ステージの変更",
        "untracked": "未追跡のファイル",
        "conflict": "競合",
        "amend_content": "amend 後のコミットの内容 (直前のコミットの親との差分)",
        "message_required": "コミットメッセージを入力してください",
        "select_files": "コミットするファイルにチェックを付けてください",
        "amend_checkbox": "直前のコミットを amend する (履歴が書き換わります)",
        "amend_hint": "直前のコミットのファイルはチェック済みで、外すとコミットから外れます (作業ツリーはそのまま)。[WIP] は未コミットの変更で、チェックすると取り込みます",
        "wip_hint": "チェックを外したステージ済みファイルはステージから外れます (作業ツリーはそのまま)",
        "select_all": "このグループを全選択/解除",
        "truncated_files": "ファイルが多いため先頭のみ表示しています",
        "new_file": "新規",
        "wip_file_remove_title": "外すと、このファイルは直前のコミットから外れます (作業ツリーの内容は残ります)",
        "amend_confirm": "直前のコミットを作り直します (メッセージも置き換えます)。{drop}\n履歴が書き換わります。続けますか？",
        "amend_drop": "直前のコミットから {n} 件のファイルを外します (作業ツリーの内容は残ります)。",
        "working_tree": "作業ツリーの変更 (WIP)",
        "files_changed": "ファイル変更",
        "large_file_diff": "大きいため先頭のみ表示しています",
        "no_diff": "差分はありません",
        "create": "作成",
        "add": "追加",
        "delete": "削除",
        "delete_branch": "ブランチ削除",
        "delete_remote_branch": "リモートブランチ削除",
        "branch_to_delete": "削除するブランチ",
        "remote_branch_to_delete": "削除するリモートブランチ",
        "delete_tag": "タグ削除",
        "tag_to_delete": "削除するタグ",
        "name": "名前",
        "none": "(なし)",
        "n_items": "{n} 件",
        "n_changes": "{n} 件の変更",
        "n_loaded": "{skip} / {total} 件を読み込み済み",
        "n_commits": "{n} 件",
        "label_conflict": "競合",
        "no_changed_files": "変更されたファイルはありません",
        "no_commits_yet": "(コミットなし)",
        "copy_hash_title": "ハッシュ (40桁) をコピー",
        "set_compare": "比較 {slot} に設定",
        "swap_ab": "A ⇄ B 入れ替え",
        "compare_heading": "比較 {from} → {to}",
        "compare_logs_heading": "ログ比較 {from} → {to}",
        "file_log_heading": "ファイルログ: {path}",
        "file_history_title": "{path} の変更履歴",
        "elapsed_hint": "{sec} 秒経過（リポジトリが大きいと時間がかかります）",
        "graph_build_failed": "グラフの作成に失敗しました",
        "reload_page": "再読み込み",
        "notifications": "通知",
        "close_notification": "通知を閉じる",
        "resize_panel": "右ペインの幅を調整",
        "reload_title": "グラフを最新の状態に更新",
        "tip_files": "対象コミットのファイル一覧を別タブで開く",
        "tip_merge": "Aを現在のHEADにマージ",
        "tip_rebase": "現在のブランチをAの上にリベース",
        "tip_rebase_range": "現在のブランチの A..HEAD を B の上にリベース",
        "tip_irebase": "現在のブランチをAの上に、コミットの並べ替え・squash・メッセージ変更・削除をしながらリベース (git rebase -i)",
        "tip_cherry": "Aを現在のHEADにチェリーピック",
        "tip_reset": "現在のHEADを対象コミットへリセット",
        "tip_stash_list": "stash の一覧 (git stash list)",
        "tip_reflog": "reflog (HEAD。ブランチは画面で切り替え)",
        "tip_stash_apply": "この stash の変更を作業ツリーに適用 (stash は残す)",
        "tip_stash_pop": "この stash の変更を作業ツリーに適用して、stash を削除",
        "tip_stash_drop": "この stash を削除",
        "tip_remote_add": "リモートを追加 (git remote add)",
        "tip_remote_remove": "リモートを削除 (git remote remove)。リモート追跡ブランチも消えます",
        "tip_no_ff": "fast-forwardできる場合でもマージコミットを作る",
        "pull_needs_worktree": "pull には書き込み可能なワークツリーが必要です",
        "co_hint_stash": "stash: 編集中の変更と管理外ファイルをすべて stash に退避します (あとで git stash pop で戻せます)。",
        "co_hint_discard": "削除: 上のファイルだけを破棄します。編集内容は HEAD の状態に戻り、管理外ファイルは削除されます (元に戻せません)。",
        "co_modified": "編集中のファイル",
        "co_untracked": "チェックアウト先に同名がある管理外ファイル",
        "running": "実行中…",
        "running_remote": "実行中… (リモートとの通信に時間がかかることがあります)",
        "running_fetch": "実行中… (fetch に時間がかかることがあります)",
        "graph_updating": "グラフ更新中…",
        "graph_update_failed": "グラフ更新に失敗しました: {error}",
        "current_branch_failed": "カレントブランチを取得できませんでした: {error}",
        "deleting": "削除中…",
        "stash_busy": "stash を処理中…",
        "rebasing": "リベース中…",
        "cherry_picking": "チェリーピック中…",
        "resetting": "リセット中…",
        "merging": "マージ中…",
        "loading_rebase_commits": "対象のコミットを読み込み中…",
        "stash_list_title": "stash 一覧",
        "stash_list_help": "git stash list (新しい順)。クリックで内容 (HEAD との差分) を表示します。操作対象に選ぶと、「操作」メニューで apply / pop / drop できます",
        "reflog_help": "git reflog (新しい順)。クリックでそのコミットの内容を表示します",
        "op_apply": "適用",
        "op_pop": "適用して削除",
        "op_drop": "削除",
        "stash_op_failed": "stash の{op}ができませんでした: {error}",
        "stash_confirm_apply": "{sel} の変更を作業ツリーに適用します (stash は残ります)。競合したときは作業ツリーに競合が残ります。よろしいですか？",
        "stash_confirm_pop": "{sel} の変更を作業ツリーに適用し、成功したら stash を削除します。競合したときは作業ツリーに競合が残り、stash も残ります。よろしいですか？",
        "stash_confirm_drop": "{sel} を削除します。よろしいですか？ (削除直後なら git stash store {hash} で戻せます)",
        "confirm_delete_branch": "ブランチ {name} を削除します。よろしいですか？",
        "confirm_delete_remote_branch": "リモートブランチ {name} を削除します。よろしいですか？",
        "confirm_delete_tag": "タグ {name} を削除します。よろしいですか？",
        "confirm_force_delete": "ブランチ {name} は未マージです。強制削除すると未マージのコミットがブランチから到達できなくなる可能性があります。削除しますか？",
        "force_delete_cancelled": "強制削除をキャンセルしました",
        "delete_failed": "削除できませんでした: {error}",
        "branch_name": "ブランチ名",
        "checkout_after_create": "作成後にチェックアウト",
        "branch_name_required": "ブランチ名を入力してください",
        "tag_name": "タグ名",
        "message_optional": "メッセージ (任意)",
        "tag_name_required": "タグ名を入力してください",
        "switch_to": "切り替え先",
        "checkout_detached_option": "コミットを detached HEAD でチェックアウト",
        "checkout_new_branch_option": "新しいブランチを作成してチェックアウト",
        "new_branch_name": "新しいブランチ名",
        "new_branch_placeholder": "新規作成時のみ入力",
        "track_upstream": "upstreamを設定する",
        "new_branch_name_required": "新しいブランチ名を入力してください",
        "confirm_create_checkout": "ブランチ {name} を作成してチェックアウトします (作業ツリーが切り替わります)。よろしいですか？",
        "confirm_checkout_branch": "ブランチ {branch} をチェックアウトします (作業ツリーが切り替わります)。よろしいですか？",
        "confirm_checkout_detached": "コミット {hash} を detached HEAD でチェックアウトします (作業ツリーが切り替わります)。よろしいですか？",
        "checkout_cancelled": "チェックアウトをキャンセルしました",
        "prune_label": "--prune (リモートで削除された追跡ブランチも削除)",
        "pull_mode": "取り込み方法",
        "pull_opt_ff": "fast-forwardのみ (--ff-only)",
        "pull_opt_merge": "マージ (--no-rebase)",
        "pull_opt_rebase": "リベース (--rebase)",
        "pull_mode_ff": "fast-forwardのみ",
        "pull_mode_merge": "マージ",
        "pull_mode_rebase": "リベース",
        "confirm_pull": "リモートの変更を pull します ({mode})。作業ツリーが変わります。よろしいですか？",
        "force_label": "--force-with-lease (リモート履歴を書き換える)",
        "what_branch": "ブランチ {name}",
        "confirm_push": "{what} を push します。よろしいですか？",
        "confirm_push_force": "{what} を --force-with-lease で push します。リモートの履歴が書き換わります。よろしいですか？",
        "push_cancelled": "push をキャンセルしました",
        "auth_invalid": "HTTP認証情報が不正です",
        "prompt_push_remote": "ブランチ {branch} には upstream が設定されていません。\npush 先のリモートを入力してください ({remotes})。\n{branch} として push し、upstream に設定します。",
        "confirm_set_upstream": "ブランチ {branch} には upstream が設定されていません。{remote}/{branch} として push し、upstream に設定しますか？",
        "fetch_after_add": "追加後に fetch する",
        "name_url_required": "名前と URL を入力してください",
        "remote_to_remove": "削除するリモート",
        "confirm_remove_remote": "リモート {name} を削除します。\nこのリモートのリモート追跡ブランチ ({name}/…) と設定も消えます (リモート側は変わりません)。よろしいですか？",
        "op_in_progress_finish": "マージまたはリベースの途中です。先に完了してください",
        "merge_in_progress_resolve": "マージの途中です。先に競合を解決してコミットしてください",
        "confirm_rebase": "ブランチ {branch} を コミット {hash} の上にリベースします。{branch} のコミットは作り直され、履歴が書き換わります (競合したときは自動で元に戻します)。よろしいですか？",
        "confirm_rebase_range": "ブランチ {branch} の A ({from}) より後のコミットを B ({to}) の上にリベースします。履歴が書き換わります (競合したときは自動で元に戻します)。よろしいですか？",
        "rebase_error": "リベースできませんでした: {error}",
        "target_detached_head": "現在の HEAD (detached)",
        "confirm_cherry_pick": "コミット {hash} を {target} にチェリーピックします。新しいコミットが作られます (競合したときは自動で元に戻します)。よろしいですか？",
        "cherry_pick_error": "チェリーピックできませんでした: {error}",
        "reset_mode": "リセット方式",
        "reset_opt_mixed": "mixed（インデックスを対象に合わせる）",
        "reset_opt_soft": "soft（HEADだけを対象へ移動）",
        "reset_opt_hard": "hard（作業ツリーも対象に合わせる）",
        "reset_submit": "リセット",
        "confirm_reset_mixed": "HEADをコミット {hash} へ mixed リセットします。\nインデックスを対象に合わせます。よろしいですか？",
        "confirm_reset_soft": "HEADをコミット {hash} へ soft リセットします。\nインデックスと作業ツリーの変更を残します。よろしいですか？",
        "confirm_reset_hard": "HEADをコミット {hash} へ hard リセットします。\nインデックスと作業ツリーの変更も破棄します。よろしいですか？",
        "reset_error": "リセットできませんでした: {error}",
        "ri_up": "上へ (先に適用)",
        "ri_down": "下へ (後で適用)",
        "ri_note": "ブランチ <b>{branch}</b> を <code>{hash}</code> の上にリベースします。上から順に適用されます (上が古いコミット)。<br>squash / fixup は直前のコミットにまとめます。reword を選ぶとメッセージを編集できます (reword したコミットにまとめた場合は、ここで書いたメッセージが使われます)。",
        "ri_first_squash": "先頭のコミットは squash / fixup にできません (まとめ先のコミットが必要です)",
        "ri_reword_required": "reword するコミットのメッセージを入力してください",
        "run_rebase": "リベース実行",
        "ri_part_reword": "メッセージ変更 {n}",
        "ri_part_squash": "squash {n}",
        "ri_part_fixup": "fixup {n}",
        "ri_part_drop": "削除 {n}",
        "confirm_rebase_i": "ブランチ {branch} の {count} 件のコミットを、コミット {hash} の上に対話的リベースします{note}。\nコミットは作り直され、履歴が書き換わります (競合などで止まったときは自動で元に戻します)。よろしいですか？",
        "rebase_i_error": "対話的リベースできませんでした: {error}",
        "confirm_merge": "コミット {hash} を {into} にマージします{noff} (作業ツリーが変わります)。よろしいですか？",
        "merge_error": "マージできませんでした: {error}",
        "wip_disabled": "作業ツリーの変更の表示は無効です",
        "no_worktree": "ワークツリーがありません",
        "not_untracked": "未追跡ファイルではありません",
        "git_diff_failed": "git diff に失敗しました",
        "git_command_failed": "git コマンドが失敗しました",
        "branch_not_found": "ブランチが見つかりません",
        "remote_branch_not_found": "remoteブランチが見つかりません",
        "commit_not_found": "コミットが見つかりません",
        "dir_not_found": "ディレクトリが見つかりません",
        "not_a_file": "ファイルではありません",
        "amend_unavailable": "amend できる状態ではありません (コミットがない、またはマージ・リベース中です)",
        "op_in_progress": "マージ・リベース・チェリーピック・リバートの途中です。先に完了するか中止してください",
        "merge_or_rebase_in_progress": "マージまたはリベースの途中です。先に完了するか中止してください",
        "detached_rebase": "ブランチをチェックアウトしていません (detached HEAD ではリベースできません)",
        "branch_name_invalid": "ブランチ名が不正です",
        "tag_name_invalid": "タグ名が不正です",
        "branch_name_unusable": "{name} はブランチ名として使えません",
        "tag_name_unusable": "{name} はタグ名として使えません",
        "no_remotes": "リモートが登録されていません",
        "remote_unregistered": "リモート {remote} は登録されていません",
        "stash_not_found": "stash が見つかりません (一覧を開き直してください)",
        "stash_no_worktree": "ワークツリーのないリポジトリでは stash を適用できません",
        "stash_dropped": "{sel} を削除しました (戻すときは git stash store {hash})",
        "stash_conflict": "{sel} の適用で競合しました。作業ツリーに競合が残っています (WIP で確認できます)",
        "stash_conflict_pop": "{sel} の適用で競合しました。作業ツリーに競合が残っています (WIP で確認できます)。stash は残してあります",
        "stash_apply_failed": "stash の適用に失敗しました",
        "stash_applied": "{sel} を適用しました",
        "stash_popped": "{sel} を適用して削除しました",
        "stash_commit_msg": "git_graph: チェックアウト前の退避 {time}",
        "stash_note": " (それまでの変更は {stash} に退避しました。戻すときは git stash pop)",
        "path_outside_repo": "リポジトリ外のパスは削除できません: {path}",
        "dir_no_autodelete": "ディレクトリは自動削除できません: {path}",
        "on_conflict_invalid": "on_conflict が不正です",
        "checkout_failed": "チェックアウトに失敗しました",
        "checkout_still_blocked": "変更を stash に退避しましたが、まだチェックアウトできません:",
        "branch_created_checkout": "ブランチ {name} を作成してチェックアウトしました",
        "branch_created": "ブランチ {name} を作成しました",
        "tag_created_annotated": "注釈付きタグ {name} を作成しました",
        "tag_created": "タグ {name} を作成しました",
        "ref_delete_only": "削除できるのはローカルブランチまたはタグだけです",
        "ref_not_exist": "{name} は存在しません",
        "branch_delete_failed": "ブランチを削除できません",
        "branch_deleted": "ブランチ {name} を削除しました",
        "remote_branch_deleted": "リモートブランチ {name} を削除しました",
        "tag_deleted": "タグ {name} を削除しました",
        "checkout_branch_done": "ブランチ {branch} をチェックアウトしました",
        "checkout_detached_done": "{hash} をチェックアウトしました (detached HEAD)",
        "reset_mode_invalid": "リセット方式が不正です",
        "reset_blocked": "マージ、リベース、またはチェリーピックの途中はリセットできません",
        "reset_noop": "HEAD は既に {hash} です (変更なし)",
        "reset_done": "HEAD を {hash} へ {mode} リセットしました",
        "amend_no_commit": "amend するコミットがありません",
        "amend_blocked": "マージまたはリベース中は amend できません",
        "amend_would_empty": "直前のコミットのファイルをすべて外すと、コミットが空になります (コミットごと取り消すなら git reset HEAD^)",
        "files_unchanged": "変更のないファイルが含まれています (更新してやり直してください): {files}",
        "merging_need_all": "マージ中は、ステージ済み・競合のファイルをすべて含めてコミットする必要があります",
        "wip_committed": "{hash} をコミットしました ({n} 件)",
        "wip_amended": "{hash} を amend しました ({n} 件)",
        "wip_amended_dropped": "{hash} を amend しました ({n} 件)、{dropped} 件をコミットから外しました",
        "wip_amended_msg_only": "{hash} を amend しました (メッセージのみ)",
        "merge_no_worktree": "ワークツリーのないリポジトリではマージできません",
        "merge_in_progress": "マージの途中です。先に競合を解決してコミットするか、git merge --abort してください",
        "merge_no_commits": "最初のコミットがないためマージできません",
        "merge_failed": "マージに失敗しました",
        "merge_conflict": "{target} のマージで競合しました。WIP の競合ファイルを解決してコミットしてください(WIP 画面で ours / theirs による解決や、マージの中断もできます)",
        "merge_uptodate": "{hash} は既にマージ済みです (変更なし)",
        "merge_done": "{hash} をマージしました",
        "rebase_no_worktree": "ワークツリーのないリポジトリではリベースできません",
        "rebase_conflict": "{target} の上へのリベースで競合したため中止し、元に戻しました。競合を解決したい場合はコマンドラインで {cmd} を実行してください",
        "rebase_failed": "リベースに失敗しました",
        "rebase_noop": "{branch} は既に {hash} の上にあります (変更なし)",
        "rebase_done": "{branch} を {hash} の上にリベースしました",
        "rebase_range_done": "ブランチ {branch} の A ({from}) より後のコミットを B ({to}) の上にリベースしました",
        "cherry_no_worktree": "ワークツリーのないリポジトリではチェリーピックできません",
        "cherry_no_commits": "最初のコミットがないためチェリーピックできません",
        "cherry_merge_commit": "マージコミットはチェリーピックできません (どちらの親側の変更を取るか決められないため)",
        "cherry_empty": "{hash} の変更は既に HEAD に含まれているため、チェリーピックするものがありません",
        "cherry_conflict": "{hash} のチェリーピックで競合したため中止し、元に戻しました。競合を解決したい場合はコマンドラインで git cherry-pick を実行してください",
        "cherry_failed": "チェリーピックに失敗しました",
        "cherry_noop": "{hash} は変更なしでした",
        "cherry_done": "{hash} をチェリーピックしました",
        "ri_merges": "マージコミットを含む範囲は対話的リベースできません (マージが失われてしまうため)",
        "ri_nothing": "{base} の上に載せ直すコミットがありません",
        "ri_too_many": "対象のコミットが {n} 件あり多すぎます (上限 {max} 件)。もっと HEAD に近いコミットを選んでください",
        "todo_invalid": "todo が不正です",
        "ri_reword_msg_required": "{hash} の新しいコミットメッセージを入力してください",
        "ri_changed": "対象のコミットが変わっています。画面を更新して、もう一度やり直してください",
        "ri_aborted": "対話的リベースの途中で競合またはエラーが起きたため、中止して元に戻しました。\n{tail}\n競合を解決したい場合はコマンドラインで git rebase -i を実行してください",
        "ri_noop": "{branch} は変更なしでした",
        "ri_done": "{branch} を {base} の上に対話的リベースしました",
        "ri_done_parts": "{branch} を {base} の上に対話的リベースしました ({parts})",
        "net_timeout": "git {cmd} が {sec} 秒でタイムアウトしました。認証の入力待ちなどで止まった可能性があります (認証情報を credential helper や ssh-agent に登録してください)",
        "fetch_failed": "fetch に失敗しました",
        "pull_failed": "pull に失敗しました",
        "push_failed": "push に失敗しました",
        "fetch_partial": "\n(失敗しなかったリモートの分は取得済みです: {n} 件のリモート追跡ブランチ/タグが更新されました)",
        "fetch_done": "fetch しました ({n} 件のリモート追跡ブランチ/タグが更新されました)",
        "fetch_done_none": "fetch しました (更新はありません)",
        "remote_name_invalid": "リモート名が不正です",
        "remote_name_unusable": "{name} はリモート名として使えません",
        "url_invalid": "URL が不正です",
        "url_unusable": "この URL は使えません",
        "remote_exists": "リモート {name} は既に登録されています",
        "remote_added": "リモート {name} を追加しました",
        "remote_added_fetched": "リモート {name} を追加しました (fetch しました)",
        "remote_added_fetch_failed": "リモート {name} を追加しましたが、fetch に失敗しました: {error}",
        "remote_not_found": "リモートが見つかりません",
        "remote_removed": "リモート {name} を削除しました (リモート追跡ブランチも削除しました)",
        "pull_no_worktree": "ワークツリーのないリポジトリでは pull できません",
        "pull_mode_invalid": "pull の方法が不正です",
        "pull_no_commits": "最初のコミットがないため pull できません",
        "detached_pull": "ブランチをチェックアウトしていません (detached HEAD では pull できません)",
        "no_upstream": "ブランチ {branch} には upstream が設定されていません (git branch -u <リモート>/<ブランチ> で設定するか、push で設定してください)",
        "pull_diverged_hint": "\n(履歴が分岐しています。方法を「マージ」か「リベース」にして再度 pull してください)",
        "pull_uptodate": "{branch} は既に最新です (変更なし)",
        "pull_done": "{branch} に {up} の変更を取り込みました",
        "push_no_commits": "最初のコミットがないため push できません",
        "local_branch_missing": "ローカルブランチ {branch} が見つかりません",
        "detached_push": "ブランチをチェックアウトしていません (detached HEAD では push できません)",
        "push_remote_unregistered": "push 先のリモート {remote} (pushRemote / remote.pushDefault) は登録されていません",
        "pushed": "{branch} を push しました{where}",
        "pushed_nothing": "{branch} を push しました{where} (更新するものはありませんでした)",
        "confirm_push_tag": "タグ {tag} を push します。よろしいですか？",
        "prompt_push_tag_remote": "タグ {tag} の push 先のリモートを入力してください ({remotes})。",
        "pushed_tag": "タグ {tag} を push しました{where}",
        "pushed_tag_nothing": "タグ {tag} は push 済みです{where}",
        "push_tag_failed": "タグの push に失敗しました",
        "tag_missing": "タグ {tag} が見つかりません",
        "dot_missing": "dot (Graphviz) が見つかりません。インストールしてください。",
        "write_disabled": "書き込み操作は無効です (--read-only 指定、またはローカル以外で待ち受け中)",
        "content_type_json": "Content-Type は application/json にしてください",
        "origin_mismatch": "Origin が一致しません",
        "host_invalid": "Host が不正です",
        "large_file_head": "大きいため先頭のみ表示しています。",
        "no_history": "履歴がありません",
        "commit_required": "コミットが指定されていません",
        "cli_help_revs": "対象の rev (省略時は --all)",
        "cli_help_repo": "リポジトリのパス (既定: カレント)",
        "cli_help_host": "待ち受けアドレス (既定: 127.0.0.1)",
        "cli_help_open": "起動後にブラウザで開く",
        "cli_help_no_remotes": "リモートブランチを除外",
        "cli_help_no_tags": "タグを除外",
        "cli_help_full_layout": "ノードが多くても dot のレイアウト最適化を省略しない (遅いが交差が少し減る)",
        "cli_help_no_count": "省略数 (+N) の計算をしない",
        "cli_help_rankdir": "BT(下から上) / TB / LR (既定: BT)",
        "cli_help_font": "グラフのフォント名 (既定: Helvetica)。日本語の幅が広く出るときは Meiryo や Yu Gothic などを指定する",
        "cli_help_dot": "dot を標準出力に出して終了",
        "cli_help_no_wip": "未コミットの変更 (WIP ノード) を表示しない",
        "cli_help_read_only": "書き込み操作 (ブランチ/タグ作成・チェックアウト) を無効にする",
        "cli_help_allow_write": "127.0.0.1 以外で待ち受けるときも書き込み操作を許可する (既定では無効)",
        "cli_help_lan": "--host 0.0.0.0 --allow-write の省略形 (他の端末からも操作可。認証はないので信頼できるネットワークで)",
        "cli_help_lang": "UI の言語 (既定: 環境変数 LANGUAGE / LC_ALL / LC_MESSAGES / LANG から決定)",
        "cli_help_check_i18n": "翻訳の漏れを検査して終了",
        "cli_not_a_repo": "{repo} は git リポジトリではありません",
        "cli_state_on": "有効",
        "cli_state_off": "無効",
        "cli_serving": "serving {url}  (Ctrl+C で終了)  書き込み操作: {state}",
        "cli_listening": "  待ち受け: 0.0.0.0:{port} (他の端末からは http://<このPCのIP>:{port}/ )",
        "cli_lan_warning": "  注意: 認証はありません。同じネットワークの誰でもブランチ作成・コミット・push などができます",
        "rebase_no_commits": "最初のコミットがないためリベースできません",
        "tip_revert": "Aの変更を打ち消すコミットを作る (git revert)",
        "revert": "リバート",
        "revert_no_worktree": "ワークツリーがないため、リバートできません",
        "revert_no_commits": "まだコミットがないため、リバートできません",
        "revert_conflict": "コミット {hash} を打ち消すと競合するため、リバートを中止して元に戻しました",
        "revert_empty": "コミット {hash} の変更は既に打ち消されているため、リバートできません",
        "revert_failed": "リバートに失敗しました",
        "revert_done": "コミット {hash} を打ち消すコミットを作りました",
        "revert_done_merge": "マージコミット {hash} を、第1親を基準に打ち消すコミットを作りました",
        "confirm_revert": "コミット {hash} の変更を打ち消す新しいコミットを {target} に作ります (競合したときは自動で元に戻します)。よろしいですか？",
        "reverting": "リバート中…",
        "revert_error": "リバートできませんでした: {error}",
        "abort_no_worktree": "ワークツリーがないため、中断できません",
        "nothing_to_abort": "中断できる操作 (マージ・リベース・チェリーピック・リバート) はありません",
        "aborted": "git {cmd} --abort を実行し、元の状態に戻しました",
        "abort_button": "中断 (git {cmd} --abort)",
        "confirm_abort": "git {cmd} --abort を実行して、途中の操作を取り消し、開始前の状態に戻します。競合の解決途中の内容は失われます。よろしいですか？",
        "op_banner": "git {cmd} の途中です",
        "resolve_ours": "選択したファイルを ours で解決",
        "resolve_theirs": "選択したファイルを theirs で解決",
        "resolve_hint": "ours = 現在のHEAD側、theirs = 取り込む側 (リベース中は逆)。ファイル全体をどちらかに置き換えてステージします",
        "resolve_side_invalid": "ours か theirs を指定してください",
        "resolve_not_conflict": "競合していないファイルが含まれています: {files}",
        "resolved": "{n} 個のファイルを {side} の内容で解決しました",
        "confirm_resolve": "選択した {n} 個のファイルを {side} の内容で丸ごと置き換え、解決済みにします。手で編集した内容は失われます。よろしいですか？",
        "wip_discard": "変更を破棄",
        "discard_blocked": "マージ・リベース・チェリーピック・リバートの途中では、変更を破棄できません。先に完了するか中断してください",
        "discard_conflict": "競合中のファイルは破棄できません ({files})。ours / theirs で解決するか、操作を中断してください",
        "discarded": "{n} 個のファイルの変更を破棄しました",
        "confirm_discard": "選択した {n} 個のファイルの変更を破棄します。ステージ済み・未ステージの変更は HEAD の状態に戻り、未追跡ファイルは削除されます。この操作は取り消せません。よろしいですか？",
        "rename_branch": "ブランチ名を変更",
        "rename": "変更",
        "renaming": "ブランチ名を変更中…",
        "branch_to_rename": "名前を変更するブランチ",
        "branch_rename_same": "新しい名前が元の名前と同じです",
        "branch_exists": "ブランチ {name} は既に存在します",
        "branch_renamed": "ブランチ {old} を {new} に変更しました",
        "branch_renamed_remote": "ブランチ {old} を {new} に変更しました。リモートのブランチ名は変わりません (必要なら新しい名前で push し、古いリモートブランチを削除してください)",
        "rename_failed": "名前を変更できませんでした: {error}",
    },
    "en": {
        "reload": "↻ Reload",
        "clear": "Clear",
        "actions": "Actions ▾",
        "ab_actions": "AB actions ▾",
        "cancel": "Cancel",
        "loading_graph": "Building commit graph…",
        "loading_graph_hint": "Large repositories may take longer",
        "create_branch": "Create branch",
        "create_tag": "Create tag",
        "checkout": "Checkout",
        "files": "Show files",
        "merge": "Merge",
        "rebase": "Rebase",
        "rebase_range": "Rebase A..HEAD onto B",
        "cherry_pick": "Cherry-pick",
        "reset": "Reset to target",
        "interactive_rebase": "Interactive rebase",
        "compare_ab": "Compare A/B",
        "compare_logs_ab": "Compare AB logs",
        "push": "Push",
        "push_branch_select": "Branch to push",
        "push_remote_select": "Remote to push to",
        "push_remote_branch": "Remote branch name",
        "http_auth": "HTTP authentication",
        "auth_username": "Username",
        "auth_password": "Password / token",
        "authenticate": "Authenticate and retry",
        "other": "Other ▾",
        "menu_select_commit": "Click a commit in the graph to see the actions for it here",
        "tip_push_head": "Push the current branch (HEAD); other branches can be chosen in the dialog",
        "tip_push_selected": "Push the branch at the selected commit",
        "stash_list": "Stash list",
        "reflog": "Reflog",
        "stash_apply": "Apply stash",
        "stash_pop": "Apply and drop stash",
        "stash_drop": "Drop stash",
        "remote_add": "Add remote",
        "remote_remove": "Remove remote",
        "checkout_blocked": "Checkout cannot proceed",
        "checkout_blocked_hint": "The following files are blocking checkout.",
        "stash_checkout": "Stash and checkout",
        "discard_checkout": "Discard and checkout",
        "graph_help_1": "Click a node to show the log from that commit to the root. Click an edge to show the log for the collapsed section.",
        "graph_help_2": "Click a commit to select it for an operation. Hover over a log row to show buttons for comparison A / B.",
        "graph_zoom_reset": "Reset zoom to 100% (Ctrl/⌘+0)",
        "current_branch": "Current branch",
        "reload_busy": "Reloading…",
        "target_label": "Target",
        "commit_label": "Commit",
        "back_history": "← Back to history",
        "all_log_to_root": "Full log to the first commit",
        "file_view": "File view",
        "select_file": "Select a file",
        "select_file_hint": "Click a file in the list on the left.",
        "file_log": "File log",
        "file_content": "File content",
        "copy_to_work": "Copy to working tree",
        "prompt_copy_dest": "Enter the destination path (relative to the repository root)",
        "file_copied": "Copied to {path}",
        "copy_dest_exists": "{path} already exists. Choose a different name",
        "copy_dest_invalid": "The destination path is invalid",
        "copy_no_worktree": "There is no working tree to copy into",
        "binary_file": "Cannot display the contents of a binary file.",
        "loading": "Loading…",
        "error": "Error: ",
        "root": "Root",
        "empty_directory": "This directory is empty.",
        "wip_loading": "Loading…",
        "wip_title": "Working tree changes (WIP)",
        "no_changes": "No changes.",
        "wip_commit_message": "Commit message",
        "wip_commit": "Commit",
        "wip_execute": "Running…",
        "staged": "Staged (git add)",
        "unstaged": "Unstaged changes",
        "untracked": "Untracked files",
        "conflict": "Conflicts",
        "amend_content": "Contents of amended commit (diff from the previous commit's parent)",
        "message_required": "Enter a commit message",
        "select_files": "Select files to commit",
        "amend_checkbox": "Amend the previous commit (this rewrites history)",
        "amend_hint": "Files from the previous commit are selected; uncheck to remove them from the commit (working-tree contents are kept). [WIP] means uncommitted changes; select them to include them.",
        "wip_hint": "Unchecking a staged file unstages it (working-tree contents are kept).",
        "select_all": "Select/deselect the entire group",
        "truncated_files": "Only the first files are shown because there are many files",
        "new_file": "New",
        "wip_file_remove_title": "Uncheck to remove this file from the previous commit (working-tree contents are kept)",
        "amend_confirm": "The previous commit will be recreated (and its message replaced).{drop}\nHistory will be rewritten. Continue?",
        "amend_drop": " {n} file(s) will be removed from the previous commit (working-tree contents are kept).",
        "working_tree": "Working tree changes (WIP)",
        "files_changed": "files changed",
        "large_file_diff": "Only the beginning is shown because the diff is large",
        "no_diff": "No differences",
        "create": "Create",
        "add": "Add",
        "delete": "Delete",
        "delete_branch": "Delete branch",
        "delete_remote_branch": "Delete remote branch",
        "branch_to_delete": "Branch to delete",
        "remote_branch_to_delete": "Remote branch to delete",
        "delete_tag": "Delete tag",
        "tag_to_delete": "Tag to delete",
        "name": "Name",
        "none": "(none)",
        "n_items": "{n} items",
        "n_changes": "{n} changes",
        "n_loaded": "{skip} / {total} loaded",
        "n_commits": "{n} commits",
        "label_conflict": "Conflict",
        "no_changed_files": "No changed files",
        "no_commits_yet": "(no commits)",
        "copy_hash_title": "Copy the hash (40 characters)",
        "set_compare": "Set as comparison {slot}",
        "swap_ab": "Swap A ⇄ B",
        "compare_heading": "Compare {from} → {to}",
        "compare_logs_heading": "Compare logs {from} → {to}",
        "file_log_heading": "File log: {path}",
        "file_history_title": "{path} change history",
        "elapsed_hint": "{sec}s elapsed (large repositories may take longer)",
        "graph_build_failed": "Failed to build the graph",
        "reload_page": "Reload",
        "notifications": "Notifications",
        "close_notification": "Close notification",
        "resize_panel": "Resize the right pane",
        "reload_title": "Refresh the graph to the latest state",
        "tip_files": "Open the file list of the target commit in a new tab",
        "tip_merge": "Merge A into the current HEAD",
        "tip_rebase": "Rebase the current branch onto A",
        "tip_rebase_range": "Rebase A..HEAD on the current branch onto B",
        "tip_irebase": "Rebase the current branch onto A while reordering, squashing, rewording or dropping commits (git rebase -i)",
        "tip_cherry": "Cherry-pick A onto the current HEAD",
        "tip_reset": "Reset the current HEAD to the target commit",
        "tip_stash_list": "List of stashes (git stash list)",
        "tip_reflog": "reflog (HEAD; switch branches on screen)",
        "tip_stash_apply": "Apply this stash to the working tree (keep the stash)",
        "tip_stash_pop": "Apply this stash to the working tree and delete it",
        "tip_stash_drop": "Delete this stash",
        "tip_remote_add": "Add a remote (git remote add)",
        "tip_remote_remove": "Remove a remote (git remote remove). Its remote-tracking branches are removed too",
        "tip_no_ff": "Create a merge commit even when a fast-forward is possible",
        "pull_needs_worktree": "pull requires a writable working tree",
        "co_hint_stash": "stash: stashes all edited changes and untracked files (restore them later with git stash pop).",
        "co_hint_discard": "Discard: discards only the files listed above. Edits revert to the HEAD state and untracked files are deleted (this cannot be undone).",
        "co_modified": "Files being edited",
        "co_untracked": "Untracked files with the same name in the checkout target",
        "running": "Running…",
        "running_remote": "Running… (communicating with the remote may take a while)",
        "running_fetch": "Running… (fetch may take a while)",
        "graph_updating": "Updating the graph…",
        "graph_update_failed": "Failed to update the graph: {error}",
        "current_branch_failed": "Could not get the current branch: {error}",
        "deleting": "Deleting…",
        "stash_busy": "Processing the stash…",
        "rebasing": "Rebasing…",
        "cherry_picking": "Cherry-picking…",
        "resetting": "Resetting…",
        "merging": "Merging…",
        "loading_rebase_commits": "Loading the target commits…",
        "stash_list_title": "Stash list",
        "stash_list_help": "git stash list (newest first). Click an entry to view its contents (diff from HEAD). Select it as the target to apply / pop / drop it from the Actions menu",
        "reflog_help": "git reflog (newest first). Click an entry to view that commit",
        "op_apply": "apply",
        "op_pop": "apply and drop",
        "op_drop": "drop",
        "stash_op_failed": "Could not {op} the stash: {error}",
        "stash_confirm_apply": "Apply the changes of {sel} to the working tree (the stash is kept). On conflicts, the conflicts remain in the working tree. Continue?",
        "stash_confirm_pop": "Apply the changes of {sel} to the working tree and delete the stash if that succeeds. On conflicts, the conflicts and the stash both remain. Continue?",
        "stash_confirm_drop": "Delete {sel}. Continue? (Right after deleting, you can restore it with git stash store {hash})",
        "confirm_delete_branch": "Delete branch {name}. Continue?",
        "confirm_delete_remote_branch": "Delete remote branch {name}. Continue?",
        "confirm_delete_tag": "Delete tag {name}. Continue?",
        "confirm_force_delete": "Branch {name} is not merged. Force-deleting it may make unmerged commits unreachable from any branch. Delete it?",
        "force_delete_cancelled": "Force delete cancelled",
        "delete_failed": "Could not delete: {error}",
        "branch_name": "Branch name",
        "checkout_after_create": "Check out after creating",
        "branch_name_required": "Enter a branch name",
        "tag_name": "Tag name",
        "message_optional": "Message (optional)",
        "tag_name_required": "Enter a tag name",
        "switch_to": "Switch to",
        "checkout_detached_option": "Check out the commit as a detached HEAD",
        "checkout_new_branch_option": "Create a new branch and check it out",
        "new_branch_name": "New branch name",
        "new_branch_placeholder": "Only when creating a new branch",
        "track_upstream": "Set the upstream branch",
        "new_branch_name_required": "Enter a new branch name",
        "confirm_create_checkout": "Create branch {name} and check it out (the working tree will change). Continue?",
        "confirm_checkout_branch": "Check out branch {branch} (the working tree will change). Continue?",
        "confirm_checkout_detached": "Check out commit {hash} as a detached HEAD (the working tree will change). Continue?",
        "checkout_cancelled": "Checkout cancelled",
        "prune_label": "--prune (also delete tracking branches that were deleted on the remote)",
        "pull_mode": "How to integrate",
        "pull_opt_ff": "Fast-forward only (--ff-only)",
        "pull_opt_merge": "Merge (--no-rebase)",
        "pull_opt_rebase": "Rebase (--rebase)",
        "pull_mode_ff": "fast-forward only",
        "pull_mode_merge": "merge",
        "pull_mode_rebase": "rebase",
        "confirm_pull": "Pull changes from the remote ({mode}). The working tree will change. Continue?",
        "force_label": "--force-with-lease (rewrites the remote history)",
        "what_branch": "branch {name}",
        "confirm_push": "Push {what}. Continue?",
        "confirm_push_force": "Push {what} with --force-with-lease. The remote history will be rewritten. Continue?",
        "push_cancelled": "Push cancelled",
        "auth_invalid": "Invalid HTTP authentication fields",
        "prompt_push_remote": "Branch {branch} has no upstream configured.\nEnter the remote to push to ({remotes}).\nIt will be pushed as {branch} and set as the upstream.",
        "confirm_set_upstream": "Branch {branch} has no upstream configured. Push it as {remote}/{branch} and set it as the upstream?",
        "fetch_after_add": "Fetch after adding",
        "name_url_required": "Enter a name and a URL",
        "remote_to_remove": "Remote to remove",
        "confirm_remove_remote": "Remove remote {name}.\nIts remote-tracking branches ({name}/…) and configuration are removed too (the remote itself is unchanged). Continue?",
        "op_in_progress_finish": "A merge or rebase is in progress. Finish it first",
        "merge_in_progress_resolve": "A merge is in progress. Resolve the conflicts and commit first",
        "confirm_rebase": "Rebase branch {branch} onto commit {hash}. The commits of {branch} are recreated and the history is rewritten (it is rolled back automatically on conflicts). Continue?",
        "confirm_rebase_range": "Rebase commits after A ({from}) on branch {branch} onto B ({to}). History will be rewritten (it is rolled back automatically on conflicts). Continue?",
        "rebase_error": "Could not rebase: {error}",
        "target_detached_head": "the current HEAD (detached)",
        "confirm_cherry_pick": "Cherry-pick commit {hash} onto {target}. A new commit will be created (it is rolled back automatically on conflicts). Continue?",
        "cherry_pick_error": "Could not cherry-pick: {error}",
        "reset_mode": "Reset mode",
        "reset_opt_mixed": "mixed (reset the index to the target)",
        "reset_opt_soft": "soft (move only HEAD to the target)",
        "reset_opt_hard": "hard (also reset the working tree to the target)",
        "reset_submit": "Reset",
        "confirm_reset_mixed": "Reset HEAD to commit {hash} (mixed).\nThe index will be reset to the target. Continue?",
        "confirm_reset_soft": "Reset HEAD to commit {hash} (soft).\nChanges in the index and the working tree are kept. Continue?",
        "confirm_reset_hard": "Reset HEAD to commit {hash} (hard).\nChanges in the index and the working tree are discarded as well. Continue?",
        "reset_error": "Could not reset: {error}",
        "ri_up": "Move up (applied earlier)",
        "ri_down": "Move down (applied later)",
        "ri_note": "Rebase branch <b>{branch}</b> onto <code>{hash}</code>. Commits are applied from top to bottom (the top is the oldest commit).<br>squash / fixup fold a commit into the previous one. Choose reword to edit the message (if commits are folded into a reworded commit, the message written here is used).",
        "ri_first_squash": "The first commit cannot be squash / fixup (there must be a commit to fold into)",
        "ri_reword_required": "Enter a message for the commit to reword",
        "run_rebase": "Run rebase",
        "ri_part_reword": "reword {n}",
        "ri_part_squash": "squash {n}",
        "ri_part_fixup": "fixup {n}",
        "ri_part_drop": "drop {n}",
        "confirm_rebase_i": "Interactively rebase {count} commits of branch {branch} onto commit {hash}{note}.\nThe commits are recreated and the history is rewritten (it is rolled back automatically if it stops on a conflict or error). Continue?",
        "rebase_i_error": "Could not rebase interactively: {error}",
        "confirm_merge": "Merge commit {hash} into {into}{noff} (the working tree will change). Continue?",
        "merge_error": "Could not merge: {error}",
        "wip_disabled": "Working tree change display is disabled",
        "no_worktree": "There is no working tree",
        "not_untracked": "Not an untracked file",
        "git_diff_failed": "git diff failed",
        "git_command_failed": "The git command failed",
        "branch_not_found": "Branch not found",
        "remote_branch_not_found": "Remote branch not found",
        "commit_not_found": "Commit not found",
        "dir_not_found": "Directory not found",
        "not_a_file": "Not a file",
        "amend_unavailable": "Cannot amend (there are no commits, or a merge/rebase is in progress)",
        "op_in_progress": "A merge, rebase, cherry-pick or revert is in progress. Finish or abort it first",
        "merge_or_rebase_in_progress": "A merge or rebase is in progress. Finish or abort it first",
        "detached_rebase": "No branch is checked out (cannot rebase on a detached HEAD)",
        "branch_name_invalid": "Invalid branch name",
        "tag_name_invalid": "Invalid tag name",
        "branch_name_unusable": "{name} cannot be used as a branch name",
        "tag_name_unusable": "{name} cannot be used as a tag name",
        "no_remotes": "No remotes are registered",
        "remote_unregistered": "Remote {remote} is not registered",
        "stash_not_found": "Stash not found (reopen the list)",
        "stash_no_worktree": "Cannot apply a stash in a repository without a working tree",
        "stash_dropped": "Dropped {sel} (to restore it: git stash store {hash})",
        "stash_conflict": "Applying {sel} caused conflicts. The conflicts remain in the working tree (see WIP)",
        "stash_conflict_pop": "Applying {sel} caused conflicts. The conflicts remain in the working tree (see WIP). The stash was kept",
        "stash_apply_failed": "Failed to apply the stash",
        "stash_applied": "Applied {sel}",
        "stash_popped": "Applied and dropped {sel}",
        "stash_commit_msg": "git_graph: stash before checkout {time}",
        "stash_note": " (Your earlier changes were stashed as {stash}. Restore them with git stash pop)",
        "path_outside_repo": "Cannot delete a path outside the repository: {path}",
        "dir_no_autodelete": "Cannot automatically delete a directory: {path}",
        "on_conflict_invalid": "Invalid on_conflict value",
        "checkout_failed": "Checkout failed",
        "checkout_still_blocked": "The changes were stashed, but checkout is still not possible:",
        "branch_created_checkout": "Created and checked out branch {name}",
        "branch_created": "Created branch {name}",
        "tag_created_annotated": "Created annotated tag {name}",
        "tag_created": "Created tag {name}",
        "ref_delete_only": "Only local branches and tags can be deleted",
        "ref_not_exist": "{name} does not exist",
        "branch_delete_failed": "Cannot delete the branch",
        "branch_deleted": "Deleted branch {name}",
        "remote_branch_deleted": "Deleted remote branch {name}",
        "tag_deleted": "Deleted tag {name}",
        "checkout_branch_done": "Checked out branch {branch}",
        "checkout_detached_done": "Checked out {hash} (detached HEAD)",
        "reset_mode_invalid": "Invalid reset mode",
        "reset_blocked": "Cannot reset while a merge, rebase or cherry-pick is in progress",
        "reset_noop": "HEAD is already at {hash} (no change)",
        "reset_done": "Reset HEAD to {hash} ({mode})",
        "amend_no_commit": "There is no commit to amend",
        "amend_blocked": "Cannot amend during a merge or rebase",
        "amend_would_empty": "Removing every file of the previous commit would leave it empty (to undo the commit itself, use git reset HEAD^)",
        "files_unchanged": "Some files have no changes (reload and try again): {files}",
        "merging_need_all": "During a merge, all staged and conflicted files must be included in the commit",
        "wip_committed": "Committed {hash} ({n} files)",
        "wip_amended": "Amended {hash} ({n} files)",
        "wip_amended_dropped": "Amended {hash} ({n} files), removed {dropped} files from the commit",
        "wip_amended_msg_only": "Amended {hash} (message only)",
        "merge_no_worktree": "Cannot merge in a repository without a working tree",
        "merge_in_progress": "A merge is in progress. Resolve the conflicts and commit, or run git merge --abort",
        "merge_no_commits": "Cannot merge because there are no commits yet",
        "merge_failed": "Merge failed",
        "merge_conflict": "Merging {target} caused conflicts. Resolve the conflicted files in WIP and commit (In the WIP screen you can also resolve with ours / theirs, or abort the merge)",
        "merge_uptodate": "{hash} is already merged (no change)",
        "merge_done": "Merged {hash}",
        "rebase_no_worktree": "Cannot rebase in a repository without a working tree",
        "rebase_conflict": "Rebasing onto {target} hit conflicts, so it was aborted and restored. To resolve the conflicts, run {cmd} on the command line",
        "rebase_failed": "Rebase failed",
        "rebase_noop": "{branch} is already on top of {hash} (no change)",
        "rebase_done": "Rebased {branch} onto {hash}",
        "rebase_range_done": "Rebased commits after A ({from}) on branch {branch} onto B ({to})",
        "cherry_no_worktree": "Cannot cherry-pick in a repository without a working tree",
        "cherry_no_commits": "Cannot cherry-pick because there are no commits yet",
        "cherry_merge_commit": "Cannot cherry-pick a merge commit (it is unclear which parent's changes to take)",
        "cherry_empty": "The changes of {hash} are already in HEAD, so there is nothing to cherry-pick",
        "cherry_conflict": "Cherry-picking {hash} hit conflicts, so it was aborted and restored. To resolve the conflicts, run git cherry-pick on the command line",
        "cherry_failed": "Cherry-pick failed",
        "cherry_noop": "{hash} made no changes",
        "cherry_done": "Cherry-picked {hash}",
        "ri_merges": "A range containing merge commits cannot be rebased interactively (the merges would be lost)",
        "ri_nothing": "There are no commits to replay onto {base}",
        "ri_too_many": "There are {n} target commits, which is too many (limit {max}). Choose a commit closer to HEAD",
        "todo_invalid": "Invalid todo",
        "ri_reword_msg_required": "Enter a new commit message for {hash}",
        "ri_changed": "The target commits have changed. Reload and try again",
        "ri_aborted": "The interactive rebase hit a conflict or error, so it was aborted and restored.\n{tail}\nTo resolve the conflicts, run git rebase -i on the command line",
        "ri_noop": "{branch} was left unchanged",
        "ri_done": "Interactively rebased {branch} onto {base}",
        "ri_done_parts": "Interactively rebased {branch} onto {base} ({parts})",
        "net_timeout": "git {cmd} timed out after {sec} seconds. It may be waiting for authentication input (register your credentials with a credential helper or ssh-agent)",
        "fetch_failed": "fetch failed",
        "pull_failed": "pull failed",
        "push_failed": "push failed",
        "fetch_partial": "\n(Remotes that did not fail were fetched: {n} remote-tracking branches/tags were updated)",
        "fetch_done": "Fetched ({n} remote-tracking branches/tags updated)",
        "fetch_done_none": "Fetched (nothing was updated)",
        "remote_name_invalid": "Invalid remote name",
        "remote_name_unusable": "{name} cannot be used as a remote name",
        "url_invalid": "Invalid URL",
        "url_unusable": "This URL cannot be used",
        "remote_exists": "Remote {name} is already registered",
        "remote_added": "Added remote {name}",
        "remote_added_fetched": "Added remote {name} (fetched)",
        "remote_added_fetch_failed": "Added remote {name}, but fetch failed: {error}",
        "remote_not_found": "Remote not found",
        "remote_removed": "Removed remote {name} (its remote-tracking branches were removed too)",
        "pull_no_worktree": "Cannot pull in a repository without a working tree",
        "pull_mode_invalid": "Invalid pull mode",
        "pull_no_commits": "Cannot pull because there are no commits yet",
        "detached_pull": "No branch is checked out (cannot pull on a detached HEAD)",
        "no_upstream": "Branch {branch} has no upstream configured (set it with git branch -u <remote>/<branch>, or set it when pushing)",
        "pull_diverged_hint": "\n(The history has diverged. Choose merge or rebase and pull again)",
        "pull_uptodate": "{branch} is already up to date (no change)",
        "pull_done": "Pulled the changes of {up} into {branch}",
        "push_no_commits": "Cannot push because there are no commits yet",
        "local_branch_missing": "Local branch {branch} not found",
        "detached_push": "No branch is checked out (cannot push on a detached HEAD)",
        "push_remote_unregistered": "The push remote {remote} (pushRemote / remote.pushDefault) is not registered",
        "pushed": "Pushed {branch}{where}",
        "pushed_nothing": "Pushed {branch}{where} (nothing to update)",
        "confirm_push_tag": "Push tag {tag}. Continue?",
        "prompt_push_tag_remote": "Enter the remote to push tag {tag} to ({remotes}).",
        "pushed_tag": "Pushed tag {tag}{where}",
        "pushed_tag_nothing": "Tag {tag}{where} is already up to date",
        "push_tag_failed": "Failed to push the tag",
        "tag_missing": "Tag {tag} not found",
        "dot_missing": "dot (Graphviz) was not found. Please install it.",
        "write_disabled": "Write operations are disabled (--read-only was given, or the server is listening on a non-local address)",
        "content_type_json": "Content-Type must be application/json",
        "origin_mismatch": "Origin does not match",
        "host_invalid": "Invalid Host",
        "large_file_head": "Only the beginning is shown because the file is large.",
        "no_history": "No history.",
        "commit_required": "No commit was specified",
        "cli_help_revs": "revisions to show (default: --all)",
        "cli_help_repo": "path to the repository (default: current directory)",
        "cli_help_host": "address to listen on (default: 127.0.0.1)",
        "cli_help_open": "open the browser after starting",
        "cli_help_no_remotes": "exclude remote branches",
        "cli_help_no_tags": "exclude tags",
        "cli_help_full_layout": "do not skip dot layout optimization even with many nodes (slower, slightly fewer crossings)",
        "cli_help_no_count": "do not compute the omitted-commit counts (+N)",
        "cli_help_rankdir": "BT (bottom to top) / TB / LR (default: BT)",
        "cli_help_font": "Font name for the graph (default: Helvetica). If CJK text comes out too wide, try e.g. Meiryo or Yu Gothic",
        "cli_help_dot": "print the dot source to stdout and exit",
        "cli_help_no_wip": "do not show uncommitted changes (the WIP node)",
        "cli_help_read_only": "disable write operations (creating branches/tags, checkout, etc.)",
        "cli_help_allow_write": "allow write operations when listening on a non-local address (disabled by default)",
        "cli_help_lan": "shorthand for --host 0.0.0.0 --allow-write (other devices can operate it; there is no authentication, so use a trusted network)",
        "cli_help_lang": "UI language (default: derived from LANGUAGE / LC_ALL / LC_MESSAGES / LANG)",
        "cli_help_check_i18n": "check the translations for omissions and exit",
        "cli_not_a_repo": "{repo} is not a git repository",
        "cli_state_on": "enabled",
        "cli_state_off": "disabled",
        "cli_serving": "serving {url}  (Ctrl+C to quit)  write operations: {state}",
        "cli_listening": "  Listening on 0.0.0.0:{port} (from other devices: http://<this PC's IP>:{port}/ )",
        "cli_lan_warning": "  Warning: there is no authentication. Anyone on the same network can create branches, commit, push, etc.",
        "rebase_no_commits": "Cannot rebase because there are no commits yet",
        "tip_revert": "Create a commit that undoes A (git revert)",
        "revert": "Revert",
        "revert_no_worktree": "Cannot revert because there is no working tree",
        "revert_no_commits": "Cannot revert because there are no commits yet",
        "revert_conflict": "Reverting commit {hash} conflicts, so the revert was aborted and rolled back",
        "revert_empty": "The changes of commit {hash} are already undone, so there is nothing to revert",
        "revert_failed": "Revert failed",
        "revert_done": "Created a commit that undoes {hash}",
        "revert_done_merge": "Created a commit that undoes merge commit {hash} (relative to its first parent)",
        "confirm_revert": "Create a new commit on {target} that undoes the changes of commit {hash} (it is rolled back automatically on conflicts). Continue?",
        "reverting": "Reverting…",
        "revert_error": "Could not revert: {error}",
        "abort_no_worktree": "Cannot abort because there is no working tree",
        "nothing_to_abort": "There is no merge, rebase, cherry-pick or revert to abort",
        "aborted": "Ran git {cmd} --abort and restored the previous state",
        "abort_button": "Abort (git {cmd} --abort)",
        "confirm_abort": "Run git {cmd} --abort to cancel the operation in progress and return to the state before it started. Anything done while resolving conflicts is lost. Continue?",
        "op_banner": "git {cmd} is in progress",
        "resolve_ours": "Resolve selected with ours",
        "resolve_theirs": "Resolve selected with theirs",
        "resolve_hint": "ours = the current HEAD side, theirs = the side being merged in (reversed during a rebase). The whole file is replaced by one side and staged",
        "resolve_side_invalid": "Specify ours or theirs",
        "resolve_not_conflict": "Some files are not in conflict: {files}",
        "resolved": "Resolved {n} file(s) with {side}",
        "confirm_resolve": "Replace the {n} selected file(s) entirely with the {side} version and mark them resolved. Manual edits are lost. Continue?",
        "wip_discard": "Discard changes",
        "discard_blocked": "Changes cannot be discarded during a merge, rebase, cherry-pick or revert. Finish or abort it first",
        "discard_conflict": "Conflicted files cannot be discarded ({files}). Resolve them with ours / theirs, or abort the operation",
        "discarded": "Discarded changes in {n} file(s)",
        "confirm_discard": "Discard changes in the {n} selected file(s). Staged and unstaged changes go back to HEAD, and untracked files are deleted. This cannot be undone. Continue?",
        "rename_branch": "Rename branch",
        "rename": "Rename",
        "renaming": "Renaming branch…",
        "branch_to_rename": "Branch to rename",
        "branch_rename_same": "The new name is the same as the old one",
        "branch_exists": "Branch {name} already exists",
        "branch_renamed": "Renamed branch {old} to {new}",
        "branch_renamed_remote": "Renamed branch {old} to {new}. The remote branch name is unchanged (push under the new name and delete the old remote branch if needed)",
        "rename_failed": "Could not rename: {error}",
    },
}
DEFAULT_LANG = "ja"  # 環境変数から言語を決められないときの言語


def detect_language(explicit=None):
    """UI の言語を決める。優先順位は --lang > LANGUAGE > LC_ALL > LC_MESSAGES > LANG (gettext と同じ)。
    C / POSIX (C.UTF-8 を含む) と、翻訳のない言語は en。どれも設定されていなければ DEFAULT_LANG。
    サーバー起動後は変更しない。"""
    if explicit:
        return explicit
    for key in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(key, "")
        if not value:
            continue
        for candidate in value.split(":"):  # LANGUAGE は "ja:en" のように優先順で並ぶ
            name = re.split(r"[._@-]", candidate.strip(), maxsplit=1)[0].lower()
            if name in ("c", "posix"):
                return "en"
            if name in TRANSLATIONS:
                return name
        return "en"
    return DEFAULT_LANG


def tr(key, **params):
    """現在の言語の文言を返す。{name} は params[name] に置き換える。
    str.format ではないので、文言に stash@{0} のような波括弧があってもよい。
    現在の言語に無いキーは英語で代用し、英語にも無ければ KeyError。"""
    table = TRANSLATIONS.get(UI_LANG, {})
    text = table[key] if key in table else TRANSLATIONS["en"][key]
    return re.sub(r"\{(\w+)\}", lambda m: str(params[m.group(1)]) if m.group(1) in params else m.group(0), text)


class UserError(ValueError):
    """画面にそのまま出すエラー。キーとパラメータから翻訳済みの文言を作る (HTTP では 400 になる)。"""

    def __init__(self, key, **params):
        super().__init__(tr(key, **params))
        self.key, self.params = key, params


def fill_template(text):
    """HTML テンプレート中の二重波括弧で囲んだキーを、現在の言語の文言 (HTML エスケープ済み) に置き換える。"""
    return re.sub(r"\{\{(\w+)\}\}", lambda m: html.escape(tr(m.group(1)), quote=True), text)


def client_translations():
    """ページの JS に埋め込む辞書。英語を土台に現在の言語で上書きし、コマンドライン用 (cli_) は含めない。"""
    merged = {**TRANSLATIONS["en"], **TRANSLATIONS.get(UI_LANG, {})}
    data = {k: v for k, v in merged.items() if not k.startswith("cli_")}
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def with_project(page):
    """テンプレート中のプロジェクト名とアイコンの置換。"""
    return page.replace("__PROJECT__", html.escape(PROJECT_NAME)).replace("__FAVICON__", FAVICON_LINK)


def render_main_page():
    return with_project(fill_template(HTML_TEMPLATE)
            .replace('<html lang="ja">', f'<html lang="{UI_LANG}">')
            .replace("/*__I18N__*/", "const I18N = " + client_translations() + ";"))


def git(*args, literal=False):
    env = dict(os.environ, GIT_LITERAL_PATHSPECS="1") if literal else None  # パスをglobとして解釈しない
    return subprocess.check_output(["git", "-C", REPO, *args], text=True,
                                   encoding="utf-8", errors="replace", env=env)


def git_bytes(*args, literal=False):
    """git の出力を変換せずバイト列で取得する (ファイル内容を含む出力用)。"""
    env = dict(os.environ, GIT_LITERAL_PATHSPECS="1") if literal else None
    return subprocess.check_output(["git", "-C", REPO, *args], env=env)


# --------------------------------------------------------------------------
# グラフの構築
# --------------------------------------------------------------------------
SUBJECT_WIDTH = 30  # グラフのノードに出すコミットメッセージの表示幅 (半角=1、全角=2)


def truncate_width(text, limit):
    """表示幅 (全角・東アジアの広い文字は 2) が limit 以内になるよう切り詰める。"""
    width = 0
    for i, ch in enumerate(text):
        w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if width + w > limit:
            return text[:i]
        width += w
    return text


def build_graph(opts):
    """残すコミットとエッジを決め、dot / ノード情報 / エッジ情報を返す。"""
    sys.setrecursionlimit(100000)
    revs = opts.revs or ["--all"]

    parents, subjects, order = {}, {}, []  # order: 子が親より先 (--topo-order)
    children = defaultdict(list)
    for line in git("log", *revs, "--topo-order", "--format=%H%x09%P%x09%s").splitlines():
        h, p, s = (line.split("\t", 2) + ["", ""])[:3]
        ps = p.split()
        parents[h] = ps
        subjects[h] = s
        order.append(h)
        for x in ps:
            children[x].append(h)

    labels = defaultdict(list)  # commit -> [(name, kind)]
    try:
        current_branch = git("symbolic-ref", "--short", "-q", "HEAD").strip()
    except subprocess.CalledProcessError:
        current_branch = ""
    # %(HEAD) は現在 checkout 中の ref だけ "*" になるため、
    # symbolic-ref の名前比較よりも ref 自体を確実に判定できる。
    fmt = "%(objectname)%09%(refname)%09%(*objectname)%09%(HEAD)"
    for line in git("for-each-ref", f"--format={fmt}").splitlines():
        obj, ref, peeled, head_mark = (line.split("\t") + [""])[:4]
        target = peeled or obj
        if target not in parents:
            continue
        if ref.startswith("refs/heads/"):
            name = ref[len("refs/heads/"):]
            is_current = name == current_branch or head_mark == "*"
            labels[target].append((name, "current" if is_current else "branch"))
        elif ref.startswith("refs/remotes/"):
            if opts.no_remotes or ref.endswith("/HEAD"):
                continue
            labels[target].append((ref[len("refs/remotes/"):], "remote"))
        elif ref.startswith("refs/tags/"):
            if opts.no_tags:
                continue
            labels[target].append((ref[len("refs/tags/"):], "tag"))
    try:
        head = git("rev-parse", "HEAD").strip()
        if head in parents:
            labels[head].append(("HEAD", "head"))
    except subprocess.CalledProcessError:
        pass

    # ref の指す先 + 末端 + それらの merge-base
    # merge-base を git で総当たり (O(n^2) 回のプロセス起動) せず、メモリ上で求める。
    # desc[x] = x の子孫である base コミットの集合 (ビットマスク)。
    # x が (a, b) の merge-base <=> a, b が desc[x] に入り、どの子の desc にも a, b が揃って入っていない。
    base = set(labels) | {h for h in parents if not children[h]}
    bit = {h: 1 << i for i, h in enumerate(sorted(base))}
    desc = defaultdict(int)
    for h in order:  # 子 -> 親
        d = desc[h] | bit.get(h, 0)
        desc[h] = d
        for p in parents[h]:
            if p in parents:
                desc[p] |= d
    keep = set(base)
    for x in order:
        d = desc[x]
        if d & (d - 1) == 0:  # base の子孫が 1 つ以下
            continue
        ch = children[x]
        if any(desc[y] == d for y in ch):  # 全ての組が 1 つの子で覆われる
            continue
        m = d
        while m:
            low = m & -m
            m ^= low
            covered = 0
            for y in ch:
                if desc[y] & low:
                    covered |= desc[y]
            if covered != d:  # a=low と組んで、どの子にも覆われない b がある
                keep.add(x)
                break
    del desc

    # 間に残したコミットを挟まない直接の祖先 (親 -> 子の順に 1 回なめるだけ)
    up, direct = {}, {}
    for h in reversed(order):
        ups = [up[p] for p in parents[h] if p in up]
        if h in keep:
            direct[h] = frozenset().union(*ups) if ups else frozenset()
            up[h] = frozenset((h,))
        elif len(ups) == 1:
            up[h] = ups[0]
        else:
            up[h] = frozenset().union(*ups) if ups else frozenset()
    del up

    # 推移簡約 (到達可能性もビットマスクで持つ)
    keep_order = [h for h in reversed(order) if h in keep]  # 親が先
    kbit = {h: 1 << i for i, h in enumerate(keep_order)}
    reach = {}
    edges = []
    for c in keep_order:
        deep = 0  # 直接の祖先たちのさらに先の祖先
        for e in direct[c]:
            deep |= reach[e]
        reach[c] = deep | sum(kbit[e] for e in direct[c])
        for d in sorted(direct[c]):
            if not deep & kbit[d]:  # d が他の直接祖先の祖先でなければ残す
                edges.append((d, c))
    keep_order.reverse()  # 出力の順序を毎回同じにする (レイアウトが安定する)

    font = (getattr(opts, "font", None) or "Helvetica").replace("\\", "").replace('"', "")  # dot の文字列に埋めるため引用符は除く
    out = [
        "digraph G {",
        f"  rankdir={opts.rankdir};",
        *(["  nslimit=1; nslimit1=1; mclimit=0.3;"]  # ノードが多いときは dot のレイアウト最適化を軽くして高速化
          if len(keep) > FAST_LAYOUT_NODES and not getattr(opts, "full_layout", False) else []),
        f'  node [shape=plaintext, fontname="{font}", fontsize=10];',
        f'  edge [fontname="{font}", fontsize=9];',
    ]
    for h in keep_order:
        rows = []
        for name, kind in labels.get(h, []):
            marker = "★ " if kind == "current" else ""
            rows.append(f'<TR><TD BGCOLOR="{COLORS[kind]}"><B>{marker}{html.escape(name)}</B></TD></TR>')
        rows.append(f'<TR><TD>{h[:7]}<BR/>{html.escape(truncate_width(subjects[h], SUBJECT_WIDTH))}</TD></TR>')
        label = ('<<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" CELLPADDING="3">'
                 + "".join(rows) + "</TABLE>>")
        out.append(f'  "{h}" [label={label}];')
    # 各エッジ d->c の省略数 = git rev-list --count d..c - 1 を、メモリ上で git を呼ばずに求める。
    # コミット x を「x の子孫である keep ノードの集合」(マスク) で分類して数え、
    # x が c の祖先かつ d の祖先でないもの (c を含み d を含まないマスク) を足し合わせる。
    counts = {}
    if not opts.no_count:
        groups, dk = Counter(), defaultdict(int)
        for h in order:  # 子 -> 親
            m = dk.pop(h, 0) | kbit.get(h, 0)
            groups[m] += 1
            for p in parents[h]:
                if p in parents:
                    dk[p] |= m
        for d, c in edges:
            bc, bd = kbit[c], kbit[d]
            counts[(d, c)] = sum(n for m, n in groups.items() if m & bc and not m & bd) - 1
    for d, c in edges:
        attr = ""
        n = counts.get((d, c), 0)
        if n > 0:
            attr = f' [label="+{n}", style=dashed]'
        out.append(f'  "{d}" -> "{c}"{attr};')
    out.append("}")

    nodes = {h: {"hash": h, "refs": [{"name": n, "kind": k} for n, k in labels.get(h, [])]}
             for h in keep}
    edge_data = {f"{d}->{c}": {"from": d, "to": c} for d, c in edges}
    return "\n".join(out), nodes, edge_data


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------
COPY_JS = """
function fallbackCopy(text) {
  const ta = document.createElement("textarea");
  ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
  document.body.appendChild(ta); ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (e) {}
  document.body.removeChild(ta);
  return ok;
}
function updatePushActions() {
  document.getElementById("pushBtn").disabled = !currentBranchLoaded || !DATA.writable;
}
function updateCurrentBranch() {
  return fetch("/api/head").then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }))
    .then(head => {
      currentBranch = head.branch; currentBranchLoaded = true;
      Object.keys(DATA.nodes).forEach(h => {
        document.querySelectorAll('tr[data-h="' + h + '"] .rc').forEach(el => { el.innerHTML = chips(h); });
      });
      const c = detailView.querySelector("#dChips");
      if (c && curDetailHash) c.innerHTML = detailChips(curDetailHash);
      updatePushActions();
    });
}
function copyText(text) {  // localhost / https なら Clipboard API、それ以外 (LAN の http など) は execCommand で代替
  if (navigator.clipboard && window.isSecureContext)
    return navigator.clipboard.writeText(text).then(() => true, () => fallbackCopy(text));
  return Promise.resolve(fallbackCopy(text));
}
// .copy リンクは data-copy の値をコピーする。行やリンク本来のクリック処理には渡さない
document.addEventListener("click", ev => {
  const a = ev.target.closest(".copy");
  if (!a) return;
  ev.preventDefault(); ev.stopPropagation();
  const label = a.dataset.label || (a.dataset.label = a.textContent);
  copyText(a.dataset.copy).then(ok => {
    const cls = ok ? "ok" : "ng";
    a.textContent = ok ? "✅" : "⚠️"; a.classList.add(cls);
    setTimeout(() => { a.textContent = label; a.classList.remove(cls); }, 1500);
  });
}, true);
"""

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>__PROJECT__ - git graph</title>
__FAVICON__
<style>
  html, body { height: 100%; margin: 0; font-family: system-ui, sans-serif; font-size: 13px; color: #222; }
  body { display: flex; }
  #graphWrap { flex: 1; min-width: 0; position: relative; background: #fff; display: flex; flex-direction: column; }
  #graph { flex: 1 1 auto; min-height: 0; position: relative; overflow: auto; padding: 12px; box-sizing: border-box; }
  #graphStatus { position: absolute; bottom: 12px; left: 50%; transform: translateX(-50%); z-index: 5;
    display: flex; flex-direction: column-reverse; gap: 6px; width: max-content; max-width: calc(100% - 32px);
    max-height: 45%; overflow-y: auto; pointer-events: auto; }
  #graphStatus:empty { display: none; }
  .graph-toast { display: flex; align-items: flex-start; gap: 10px; padding: 8px 10px 8px 12px;
    border: 1px solid #ccc; border-radius: 4px; background: #fff; color: #555;
    box-shadow: 0 2px 8px #0003; white-space: pre-wrap; overflow-wrap: anywhere; }
  .graph-toast.success { color: #1a7f37; border-color: #8cce9c; }
  .graph-toast.error { color: #cf222e; border-color: #e7a0a4; }
  .graph-toast-message { flex: 1; min-width: 0; }
  .graph-toast-close { flex: none; border: 0; background: transparent; color: inherit; cursor: pointer; font: inherit; padding: 0 2px; }
  #compareControls { flex: 0 0 auto; position: relative; z-index: 6; padding: 4px 8px; border-bottom: 1px solid #ccc; background: #f4f5f7; }
  #compareControls [hidden] { display: none; }
  #compareToolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 6px; }
  #compareToolbar .sbtn { margin: 0; }
  #compareToolbar button { white-space: nowrap; }
  .menubar-left { display: flex; align-items: center; gap: 6px; order: 0; }
  .menubar-group { display: flex; align-items: center; gap: 2px; }
  .menubar-spacer { flex: 1 1 auto; order: 1; }
  #remoteActions { order: 2; }
  #cmpActionsWrap { order: 3; }
  #abActionsWrap { order: 4; }
  #otherActionsWrap { order: 5; }
  #compareToolbar > .cmp-hash { order: 6; }
  .abtn.mbtn { background: transparent; border-color: transparent; padding: 4px 10px; }
  .abtn.mbtn:hover { background: #dde6fa; border-color: #b8c7ee; }
  .abtn.mbtn[aria-expanded="true"] { background: #cfdcf8; border-color: #9fb4e8; }
  .abtn.mbtn:disabled { background: transparent; border-color: transparent; }
  .menu-wrap { position: relative; display: inline-block; }
  .dropdown { position: absolute; top: calc(100% + 2px); left: 0; z-index: 20; display: flex; flex-direction: column; gap: 4px; box-sizing: border-box; min-width: 220px; max-width: min(440px, calc(100vw - 24px)); max-height: calc(100vh - 90px); overflow: auto; padding: 6px; border: 1px solid #ccc; border-radius: 4px; background: #fff; box-shadow: 0 2px 8px #0003; }
  .dropdown[hidden] { display: none; }
  .dropdown .abtn { text-align: left; }
  .dropdown .hint { margin: 2px 4px; }
  #cmpActions label { white-space: nowrap; }
  #cmpActions select { max-width: 100%; }
  .menu-row { display: flex; flex-direction: column; gap: 4px; }
  .menu-row > .abtn { align-self: stretch; text-align: left; }
  .menu-section { display: flex; flex-direction: column; gap: 4px; }
  .menu-separator { border: 0; border-top: 1px solid #ccc; width: 100%; margin: 0; }
  #compareControls .cmp-hash { font-family: monospace; white-space: nowrap; }
  #compareControls #actionTarget { color: #6f42c1; }
  #splitter { flex: 0 0 6px; cursor: col-resize; background: #eee; border-left: 1px solid #ccc; border-right: 1px solid #ccc; box-sizing: border-box; touch-action: none; }
  #splitter:hover, body.resizing #splitter { background: #c8d8f0; }
  body.resizing { cursor: col-resize; user-select: none; }
  #panel { flex: 0 0 var(--panel-width, 480px); min-width: 240px; display: flex; flex-direction: column; border-left: 1px solid #ccc; background: #fafafa; box-sizing: border-box; }
  body.compare-logs-mode #panel { flex-basis: min(760px, 68vw); }
  #listView, #detailView { flex: 1; min-height: 0; overflow: auto; padding: 14px; box-sizing: border-box; }
  #detailView { display: none; background: #fff; }
  h2 { font-size: 14px; margin: 0 0 8px; }
  pre { white-space: pre-wrap; word-break: break-word; background: #fff; border: 1px solid #ddd; padding: 8px; margin: 6px 0; font-size: 12px; }
  code { background: #eee; padding: 1px 4px; border-radius: 3px; user-select: all; }
  .chip { display: inline-block; padding: 0 6px; border-radius: 3px; margin: 0 4px 2px 0; font-weight: bold; }
  .chip .ref-delete { border: 0; background: transparent; color: #555; cursor: pointer; font: inherit; font-weight: normal; line-height: inherit; margin-left: 4px; padding: 0 2px; }
  .ref-delete:hover { color: #cf222e; }
  .tag { display: inline-block; padding: 0 6px; border-radius: 3px; margin-right: 4px; font-weight: bold; color: #fff; }
  .tagA { background: #06c; } .tagB { background: #e67e00; }
  .meta { color: #555; margin: 2px 0; }
  .hint { color: #777; }
  .copy { cursor: pointer; font-size: 12px; margin-left: 6px; user-select: none; text-decoration: none; border-radius: 3px; padding: 0 2px; }
  .copy:hover { background: #e4e4e4; }
  td.h .copy { opacity: .45; } tr:hover td.h .copy { opacity: 1; }
  .actions { border: 1px solid #ddd; background: #fafafa; border-radius: 4px; padding: 8px; margin: 8px 0; }
  .actions .arow { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin: 4px 0; }
  .actions input[type=text] { padding: 3px 6px; border: 1px solid #bbb; border-radius: 3px; min-width: 0; flex: 1; }
  .abtn { display: inline-block; background: #e8eefc; border: 1px solid #b8c7ee; padding: 3px 8px; border-radius: 3px; cursor: pointer; text-decoration: none; color: inherit; }
  .abtn[aria-disabled="true"] { opacity: .5; cursor: default; pointer-events: none; }
  .abtn:hover { background: #d6e2fb; } .abtn:disabled { opacity: .5; cursor: default; }
  .detail-files { margin-left: 8px; color: #06c; font-size: 13px; font-weight: normal; text-decoration: none; }
  .detail-files:hover { text-decoration: underline; }
  #coModal, #actionModal { position: fixed; inset: 0; background: rgba(0,0,0,.4); display: flex; align-items: center; justify-content: center; z-index: 1000; }
  #coModal[hidden] { display: none; }
  #actionModal[hidden] { display: none; }
  .comodal { background: #fff; border-radius: 6px; padding: 14px 16px; max-width: 560px; max-height: 80vh; overflow: auto; box-shadow: 0 4px 20px rgba(0,0,0,.3); }
  .action-dialog { background: #fff; border-radius: 6px; padding: 14px 16px; width: min(420px, calc(100vw - 48px)); max-height: 80vh; overflow: auto; box-shadow: 0 4px 20px rgba(0,0,0,.3); }
  .action-dialog h2 { font-size: 16px; margin: 0 0 12px; }
  .action-fields { display: flex; flex-direction: column; gap: 10px; }
  .action-fields label { display: flex; flex-direction: column; gap: 4px; }
  .action-fields label[hidden] { display: none; }
  .action-fields label.check { flex-direction: row; align-items: center; }
  .action-fields input[type=text], .action-fields select { box-sizing: border-box; width: 100%; padding: 5px 7px; }
  .dialog-buttons { display: flex; justify-content: flex-end; gap: 6px; margin-top: 14px; }
  .dialog-buttons button { margin: 0; }
  .comodal ul { margin: 2px 0 8px; padding-left: 20px; font-family: monospace; word-break: break-all; }
  .comodal .arow { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
  .action-dialog.wide { width: min(760px, calc(100vw - 48px)); }
  .ri-note { color: #555; margin-bottom: 4px; }
  .ri-list { display: flex; flex-direction: column; gap: 4px; }
  .ri-row { border: 1px solid #ddd; border-radius: 4px; padding: 4px 6px; background: #fff; }
  .ri-main { display: flex; align-items: center; gap: 6px; }
  .ri-main select { flex: none; width: 90px; padding: 2px; }
  .ri-main code { flex: none; color: #555; }
  .ri-subj { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .ri-author { flex: none; color: #888; font-size: 11px; }
  .ri-main button { flex: none; padding: 0 6px; cursor: pointer; }
  .ri-row textarea { box-sizing: border-box; width: 100%; margin-top: 4px; font: inherit; padding: 4px 6px; }
  .ri-row textarea[hidden] { display: none; }
  .ri-row.drop { background: #f6f6f6; } .ri-row.drop .ri-subj { text-decoration: line-through; color: #999; }
  .ri-row.squash, .ri-row.fixup { margin-left: 22px; background: #f3f8ff; }
  #actionError { margin-top: 8px; white-space: pre-wrap; } #actionError:empty { display: none; }
  .abtn.danger { background: #fde8e8; border-color: #eeb8b8; } .abtn.danger:hover { background: #fbd3d3; }
  h3 { font-size: 13px; margin: 10px 0 2px; }
  .st { display: inline-block; min-width: 1.3em; text-align: center; font-weight: bold; color: #fff; border-radius: 2px; margin-right: 4px; }
  .stM { background: #d29922; } .stA { background: #1a7f37; } .stD { background: #cf222e; } .stR { background: #6f42c1; } .stU { background: #cf222e; } .stN { background: #888; }
  .sbtn:disabled { opacity: .5; cursor: default; }
  table.log { border-collapse: collapse; width: 100%; margin-top: 6px; }
  table.log td { border-bottom: 1px solid #e3e3e3; padding: 4px; vertical-align: top; }
  table.log tr.clickable { cursor: pointer; }
  table.log tr.clickable:hover td { background: #eef; }
  table.log tr.cur td { background: #ffe9a0; }
  table.log tr.clickable.cmpA td { background: #cfe3ff; }
  table.log tr.clickable.cmpB td { background: #ffdcb0; }
  table.log td.h { width: 1%; font-family: monospace; white-space: nowrap; color: #b60; }
  table.log .cmp-pick { display: flex; gap: 3px; margin-top: 3px; opacity: 0; }
  table.log tr:hover .cmp-pick, table.log tr:focus-within .cmp-pick { opacity: 1; }
  table.log .cmp-pick .sbtn { margin: 0; padding: 1px 5px; font-size: 11px; }
  table.log tr.actionTarget td { box-shadow: inset 0 2px #6f42c1, inset 0 -2px #6f42c1; }
  g.node.actionTarget polygon { stroke: #6f42c1; stroke-width: 3; }
  @media (hover: none) { table.log .cmp-pick { opacity: 1; } }
  table.log .meta { font-size: 12px; color: #888; margin: 0; }
  table.log tr.log-gap td { padding-top: 4px; padding-bottom: 4px; }
  .compare-log-col table.log tr.log-common td { background: #f3f3f3; color: #666; }
  .compare-log-col table.log tr.log-common-start td { border-top: 2px solid #999; }
  .compare-logs { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 14px; }
  .compare-log-col { min-width: 0; overflow: hidden; }
  .compare-log-col h3 { margin: 8px 0 4px; font-size: 14px; }
  .compare-log-col table.log { margin-top: 0; max-width: 100%; table-layout: fixed; }
  .compare-log-col table.log td.h { width: 7.5em; max-width: 7.5em; overflow: hidden; }
  .compare-log-col table.log td:not(.h) { overflow-wrap: anywhere; word-break: break-word; }
  @media (max-width: 760px) { .compare-logs { grid-template-columns: 1fr; } }
  table.files { border-collapse: collapse; margin: 6px 0; font-family: monospace; font-size: 12px; width: 100%; }
  table.files td { padding: 2px 6px; vertical-align: top; }
  tr.frow { cursor: pointer; } tr.frow:hover td { background: #eef; }
  td.fp { color: #06c; word-break: break-all; } td.fc { text-align: right; white-space: nowrap; } td.fb { white-space: nowrap; }
  td.fp a { color: inherit; text-decoration: none; } td.fp a:hover { text-decoration: underline; }
  td.fk { width: 1%; padding-right: 0; } td.fk input, h3 .wipall { margin: 0 6px 0 0; vertical-align: middle; cursor: pointer; }
  .wipbox { border: 1px solid #ddd; background: #fafafa; border-radius: 4px; padding: 8px; margin: 8px 0; }
  .wipbox textarea { width: 100%; box-sizing: border-box; min-height: 56px; padding: 4px 6px; border: 1px solid #bbb; border-radius: 3px; font: inherit; resize: vertical; }
  .wipbox .arow { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 6px; }
  .wipbox .amendrow { display: block; margin: 0 0 6px; cursor: pointer; }
  .wipbox .amendrow input { margin: 0 6px 0 0; vertical-align: middle; }
  .wbtn { background: #1a7f37; color: #fff; border: 1px solid #176b30; padding: 3px 10px; border-radius: 3px; cursor: pointer; }
  .wbtn:hover { background: #176b30; } .wbtn:disabled { opacity: .5; cursor: default; }
  .wbtn.danger { background: #b42318; border-color: #912018; } .wbtn.danger:hover { background: #912018; }
  .wipres, .wipop { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin: 4px 0 8px; }
  .wipop { padding: 6px 8px; background: #fff4e5; border: 1px solid #f0c987; border-radius: 4px; }
  .plus { color: #1a7f37; } .minus { color: #cf222e; }
  g.node, g.edge { cursor: pointer; }
  g.node:hover polygon { stroke: #e88; } g.edge:hover path { stroke: #e88; }
  g.node.sel polygon { stroke: #d22; stroke-width: 3; }
  g.node.cmpA polygon { stroke: #06c; stroke-width: 3; }
  g.node.cmpB polygon { stroke: #e67e00; stroke-width: 3; }
  g.edge.sel path { stroke: #d22; stroke-width: 2.5; } g.edge.sel polygon { stroke: #d22; fill: #d22; }
  g.edge path.hit { fill: none; stroke: transparent; stroke-width: 14; pointer-events: stroke; }
  .back-btn, .sbtn { background: #ddd; border: none; padding: 4px 8px; border-radius: 3px; cursor: pointer; margin: 0 6px 8px 0; }
  .back-btn:hover, .sbtn:hover { background: #ccc; }
  #loading { display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; gap: 12px; color: #555; }
  .spin { width: 36px; height: 36px; border: 4px solid #ddd; border-top-color: #06c; border-radius: 50%; animation: spin 1s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>
<div id="graphWrap">
<div id="graphStatus" role="region" aria-label="{{notifications}}"></div>
<div id="compareControls">
  <div id="compareToolbar" role="menubar">
    <div class="menubar-left">
      <button id="reload" class="sbtn" disabled title="{{reload_title}}">{{reload}}</button>
      <button id="graphZoomReset" class="sbtn" title="{{graph_zoom_reset}}">100%</button>
      <span class="cmp-hash" id="actionTarget"></span>
      <span class="cmp-hash" id="cmpA"></span><span class="cmp-hash" id="cmpB"></span>
      <button class="sbtn" id="clearCmpBtn">{{clear}}</button>
    </div>
    <span class="menubar-spacer"></span>
    <span class="menu-wrap" id="cmpActionsWrap">
      <button class="abtn mbtn" id="cmpActToggle" aria-haspopup="true" aria-expanded="false" aria-controls="cmpActions">{{actions}}</button>
      <div class="dropdown" id="cmpActions" hidden>
        <div class="hint" id="actionsEmpty">{{menu_select_commit}}</div>
        <div class="menu-section" id="aActions" hidden>
          <button class="abtn" id="pushSelBtn" data-cact="push" data-push-sel="1" title="{{tip_push_selected}}">{{push}}</button>
          <button class="abtn" id="mergeBtn" title="{{tip_merge}}">{{merge}}</button>
          <button class="abtn" id="rebaseBtn" title="{{tip_rebase}}">{{rebase}}</button>
          <button class="abtn" id="irebaseBtn" title="{{tip_irebase}}">{{interactive_rebase}}</button>
          <button class="abtn" id="cherryBtn" title="{{tip_cherry}}">{{cherry_pick}}</button>
          <button class="abtn" id="revertBtn" title="{{tip_revert}}">{{revert}}</button>
          <button class="abtn danger" id="resetBtn" title="{{tip_reset}}">{{reset}}</button>
          <button class="abtn" data-cact="branch">{{create_branch}}</button>
          <button class="abtn" id="renameBranchBtn">{{rename_branch}}</button>
          <button class="abtn danger" id="deleteBranchBtn" data-cact="delete-branch">{{delete_branch}}</button>
          <button class="abtn danger" id="deleteRemoteBranchBtn">{{delete_remote_branch}}</button>
          <button class="abtn" data-cact="tag">{{create_tag}}</button>
          <button class="abtn danger" id="deleteTagBtn" data-cact="delete-tag">{{delete_tag}}</button>
          <button class="abtn" data-cact="checkout">{{checkout}}</button>
        </div>
        <div class="menu-section" id="stashActions" hidden>
          <button class="abtn" id="stashApplyBtn" title="{{tip_stash_apply}}">{{stash_apply}}</button>
          <button class="abtn" id="stashPopBtn" title="{{tip_stash_pop}}">{{stash_pop}}</button>
          <button class="abtn danger" id="stashDropBtn" title="{{tip_stash_drop}}">{{stash_drop}}</button>
        </div>
      </div>
    </span>
    <div class="menubar-group" id="remoteActions">
      <button class="abtn mbtn" id="pushBtn" data-cact="push" title="{{tip_push_head}}">{{push}}</button>
      <button class="abtn mbtn" data-cact="pull">pull</button>
      <button class="abtn mbtn" data-cact="fetch">fetch</button>
    </div>
    <span class="menu-wrap" id="abActionsWrap">
      <button class="abtn mbtn" id="abActToggle" aria-haspopup="true" aria-expanded="false" aria-controls="abActions">{{ab_actions}}</button>
      <div class="dropdown" id="abActions" hidden>
        <button class="abtn" id="compareBtn" hidden>{{compare_ab}}</button>
        <button class="abtn" id="compareLogsBtn" hidden>{{compare_logs_ab}}</button>
        <button class="abtn" id="rebaseRangeBtn" hidden title="{{tip_rebase_range}}">{{rebase_range}}</button>
      </div>
    </span>
    <span class="menu-wrap" id="otherActionsWrap">
      <button class="abtn mbtn" id="otherMenuBtn" aria-haspopup="true" aria-expanded="false" aria-controls="otherActions">{{other}}</button>
      <div class="dropdown" id="otherActions" hidden>
        <button class="abtn" id="stashListBtn" title="{{tip_stash_list}}">{{stash_list}}</button>
        <button class="abtn" id="reflogBtn" title="{{tip_reflog}}">{{reflog}}</button>
        <button class="abtn" data-cact="remote-add" title="{{tip_remote_add}}">{{remote_add}}</button>
        <button class="abtn danger" data-cact="remote-remove" title="{{tip_remote_remove}}">{{remote_remove}}</button>
      </div>
    </span>
  </div>
</div>
<div id="actionModal" hidden>
  <form class="action-dialog" id="actionForm" role="dialog" aria-modal="true" aria-labelledby="actionTitle">
    <h2 id="actionTitle"></h2>
    <div class="action-fields" id="actionFields"></div>
    <div class="amsg ng" id="actionError" role="alert"></div>
    <div class="dialog-buttons">
      <button type="button" class="abtn" data-dialog-cancel>{{cancel}}</button>
      <button type="submit" class="abtn" id="actionSubmit"></button>
    </div>
  </form>
</div>
<div id="coModal" hidden><div class="comodal" role="dialog" aria-modal="true">
  <div><b>{{checkout_blocked}}</b> — <span>{{checkout_blocked_hint}}</span></div>
  <div id="coFiles"></div>
  <div class="hint">{{co_hint_stash}}<br>
  {{co_hint_discard}}</div>
  <div class="arow"><button class="abtn" data-co="stash">{{stash_checkout}}</button>
    <button class="abtn danger" data-co="discard">{{discard_checkout}}</button>
    <button class="abtn" data-co="cancel">{{cancel}}</button></div>
</div></div>
<div id="graph">
  <div id="loading">
    <div class="spin"></div>
    <div>{{loading_graph}}</div>
    <div class="hint" id="lsub">{{loading_graph_hint}}</div>
  </div>
</div>
</div>
<div id="splitter" role="separator" aria-orientation="vertical" aria-label="{{resize_panel}}" tabindex="0"></div>
<div id="panel">
  <div id="listView"><p class="hint">{{graph_help_1}}</p>
    <p class="hint">{{graph_help_2}}</p></div>
  <div id="detailView"></div>
</div>
<script>
/*__I18N__*/
function t(key, values = {}) {  // {name} を values[name] に置き換える (値の中の $ は特別扱いしない)
  let text = key in I18N ? I18N[key] : key;
  Object.entries(values).forEach(([name, value]) => { text = text.replaceAll("{" + name + "}", () => String(value)); });
  return text;
}

let DATA = {nodes: {}, edges: {}};
const COLORS = {branch: "#b7e4a7", current: "#b7e4a7", remote: "#a7c8f0", tag: "#ffe9a0", head: "#f4a7a7"};
const CHUNK = 500;
const listView = document.getElementById("listView"), detailView = document.getElementById("detailView");
const views = {list: listView, detail: detailView}, savedScroll = {list: 0, detail: 0};
let curView = "list", selected = null, selectedKey = null, token = 0, observer = null, curRow = null;
let curCtx = null, curFiles = [];
let compareAHash = null, compareBHash = null;
let actionHash = null;  // マージ / リベースなどの操作対象
let currentBranch = null;
let currentBranchLoaded = false;
const nodeEl = {};  // ハッシュ -> グラフ上のノード要素
let graphZoom = 1;
const homeHtml = listView.innerHTML;
if (!history.state || !history.state.screen) history.replaceState({screen: "home"}, "");

function applyGraphZoom() {
  const svg = document.querySelector("#graph > svg");
  if (!svg) return;
  svg.style.transformOrigin = "0 0";
  svg.style.transform = "scale(" + graphZoom + ")";
  const reset = document.getElementById("graphZoomReset");
  if (reset) reset.textContent = Math.round(graphZoom * 100) + "%";
}
function resetGraphZoom() {
  const graph = document.getElementById("graph");
  const oldZoom = graphZoom;
  if (oldZoom === 1) return;
  const x = graph.scrollLeft + graph.clientWidth / 2;
  const y = graph.scrollTop + graph.clientHeight / 2;
  graphZoom = 1;
  applyGraphZoom();
  graph.scrollLeft = x / oldZoom - graph.clientWidth / 2;
  graph.scrollTop = y / oldZoom - graph.clientHeight / 2;
}
document.getElementById("graph").addEventListener("wheel", ev => {
  if (!ev.ctrlKey && !ev.metaKey) return;
  ev.preventDefault();  // Ctrl+ホイールによるブラウザ全体のズームをグラフのズームに置き換える
  const graph = ev.currentTarget;
  const rect = graph.getBoundingClientRect();
  const x = graph.scrollLeft + ev.clientX - rect.left;
  const y = graph.scrollTop + ev.clientY - rect.top;
  const oldZoom = graphZoom;
  graphZoom = Math.max(0.25, Math.min(4, graphZoom * Math.pow(1.1, -ev.deltaY / 100)));
  if (graphZoom === oldZoom) return;
  applyGraphZoom();
  graph.scrollLeft = x * graphZoom / oldZoom - (ev.clientX - rect.left);
  graph.scrollTop = y * graphZoom / oldZoom - (ev.clientY - rect.top);
}, {passive: false});
document.getElementById("graphZoomReset").addEventListener("click", resetGraphZoom);
document.addEventListener("keydown", ev => {
  if ((ev.ctrlKey || ev.metaKey) && ev.key === "0") {
    ev.preventDefault();
    resetGraphZoom();
  }
});

const splitter = document.getElementById("splitter");
let resizing = false;
const PANEL_WIDTH_KEY = "git-graph-panel-width";
function applyPanelWidth(width) {
  document.documentElement.style.setProperty("--panel-width", Math.max(240, Math.min(window.innerWidth - 166, width)) + "px");
}
try {  // 別ページ (差分など) から戻ったときも、右ペインの幅を保つ
  const saved = parseInt(localStorage.getItem(PANEL_WIDTH_KEY), 10);
  if (saved > 0) applyPanelWidth(saved);
} catch (e) {}
splitter.addEventListener("pointerdown", ev => {
  resizing = true;
  splitter.setPointerCapture(ev.pointerId);
  document.body.classList.add("resizing");
  ev.preventDefault();
});
splitter.addEventListener("pointermove", ev => {
  if (!resizing) return;
  applyPanelWidth(window.innerWidth - ev.clientX);
});
function stopResizing() {
  if (resizing) {
    try { localStorage.setItem(PANEL_WIDTH_KEY, String(parseInt(getComputedStyle(document.getElementById("panel")).width, 10))); } catch (e) {}
  }
  resizing = false;
  document.body.classList.remove("resizing");
}
splitter.addEventListener("pointerup", stopResizing);
splitter.addEventListener("pointercancel", stopResizing);

function esc(s) {
  return String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
}
const DEFAULT_DOCUMENT_TITLE = document.title;
const PROJECT_DOCUMENT_TITLE = DEFAULT_DOCUMENT_TITLE.replace(/ - git graph$/, "");
function setDocumentTitle(label) {
  document.title = label ? label + " - " + PROJECT_DOCUMENT_TITLE : DEFAULT_DOCUMENT_TITLE;
}
function setDocumentTitleFromHtml(titleHtml) {
  const el = document.createElement("span");
  el.innerHTML = titleHtml;
  setDocumentTitle(el.textContent || el.innerText || "");
}
function chips(h) {
  const n = DATA.nodes[h];
  if (!n) return "";
  return n.refs.map(r => {
    const current = r.kind === "current" || (r.kind === "branch" && r.name === currentBranch);
    return '<span class="chip" style="background:' + COLORS[r.kind] + '"' +
      (current ? ' title="' + esc(t("current_branch")) + '"' : "") + '>' + (current ? "★ " : "") + esc(r.name) + "</span>";
  }).join("");
}
function detailChips(h) {
  const n = DATA.nodes[h];
  if (!n) return "";
  return n.refs.map(r => {
    const current = r.kind === "current" || (r.kind === "branch" && r.name === currentBranch);
    return '<span class="chip" style="background:' + COLORS[r.kind] + '"' +
      (current ? ' title="' + esc(t("current_branch")) + '"' : "") + '>' + (current ? "★ " : "") + esc(r.name) + "</span>";
  }).join("");
}
/*__COPY_JS__*/
function copyLink(h) {  // フルハッシュをコピーするリンク
  return '<a class="copy" data-copy="' + h + '" title="' + esc(t("copy_hash_title")) + '">📋</a>';
}
function api(path, params) {
  return fetch(path + "?" + new URLSearchParams(params)).then(r =>
    r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }));
}
function showView(name, keepScroll) {  // keepScroll: 戻ってきたときに以前のスクロール位置を復元
  savedScroll[curView] = views[curView].scrollTop;
  for (const k in views) views[k].style.display = (k === name) ? "block" : "none";
  curView = name;
  views[name].scrollTop = keepScroll ? savedScroll[name] : 0;
}
function pushScreen(state) {
  const uiState = {compareA: compareAHash, compareB: compareBHash, actionHash: actionHash, cmpActOpen: cmpActOpen};
  history.replaceState(Object.assign({}, history.state, uiState, {scroll: views[curView].scrollTop}), "");
  history.pushState(Object.assign({}, state, uiState), "");
}
function saveUiState() {
  if (!history.state) return;
  history.replaceState(Object.assign({}, history.state, {
    compareA: compareAHash, compareB: compareBHash, actionHash: actionHash, cmpActOpen: cmpActOpen
  }), "");
}
function rowHtml(c, extraClass) {  // c = [hash, short, author, date, subject]
  return '<tr class="clickable' + (extraClass ? ' ' + extraClass : '') + '" data-h="' + c[0] + '"><td class="h">' + esc(c[1]) + copyLink(c[0]) +
    '<div class="cmp-pick"><button type="button" class="sbtn" data-cmp="A" title="' + esc(t("set_compare", {slot: "A"})) + '">A</button>' +
    '<button type="button" class="sbtn" data-cmp="B" title="' + esc(t("set_compare", {slot: "B"})) + '">B</button></div></td><td>' +
    '<span class="rc">' + chips(c[0]) + "</span>" + esc(c[4]) + '<div class="meta">' + esc(c[2]) + " / " + esc(c[3]) + "</div></td></tr>";
}

// git log を CHUNK 件ずつ取得して、末尾が見えたら続きを読み込む (全件読むまで続く)
// src を渡すと /api/log の代わりに src.path (src.params を付けて) から同じ形式の一覧を取る (stash 一覧 / reflog)
function showLog(titleHtml, rev, topHtml, fromHistory, restoreScroll, src) {
  document.body.classList.remove("compare-logs-mode");
  if (!fromHistory) pushScreen({screen: "log", titleHtml: titleHtml, rev: rev, topHtml: topHtml, src: src || null});
  setDocumentTitleFromHtml(titleHtml);
  const my = ++token;
  if (observer) { observer.disconnect(); observer = null; }
  curRow = null;
  listView.innerHTML = "<h2>" + titleHtml + "</h2>" + topHtml +
    '<div class="meta" id="count">' + esc(t("loading")) + '</div>' +
    '<table class="log"><tbody id="rows"></tbody></table><div id="sentinel" class="hint"></div>';
  showView("list");
  listView.dataset.src = src ? src.path : "";
  const rows = listView.querySelector("#rows"), count = listView.querySelector("#count"),
        sentinel = listView.querySelector("#sentinel");
  let skip = 0, done = false, loading = false, total = null;

  async function more() {
    if (loading || done || my !== token) return;
    loading = true;
    try {
      const r = src ? await api(src.path, Object.assign({}, src.params, {skip: skip, limit: CHUNK}))
                    : await api("/api/log", {rev: rev, skip: skip, limit: CHUNK});
      if (my !== token) return;
      rows.insertAdjacentHTML("beforeend", r.commits.map(rowHtml).join(""));
      updateCmpClasses();
      skip += r.commits.length;
      if (fromHistory && restoreScroll) {
        listView.scrollTop = Math.max(0, Math.min(restoreScroll, listView.scrollHeight - listView.clientHeight));
      }
      done = !r.has_more;
      if (r.total !== undefined) total = r.total;
      count.textContent = total === null ? t("n_items", {n: skip})
        : (done ? t("n_changes", {n: total}) : t("n_loaded", {skip: skip, total: total}));
      sentinel.textContent = done ? (skip === 0 ? t("none") : "") : t("loading");
    } catch (e) {
      sentinel.textContent = t("error") + e.message; done = true;
    } finally {
      loading = false;
    }
  }

  if ("IntersectionObserver" in window) {
    const recheck = () => {
      if (!done && observer && my === token) { observer.unobserve(sentinel); observer.observe(sentinel); }
    };
    observer = new IntersectionObserver(es => { if (es[0].isIntersecting) more().then(recheck); },
                                        {root: listView, rootMargin: "600px"});
    observer.observe(sentinel);
  } else {
    (async () => { while (!done && my === token) await more(); })();
  }
}

function showCompareLogs(a, b, fromHistory, restoreScroll) {
  document.body.classList.add("compare-logs-mode");
  if (!fromHistory) pushScreen({screen: "compare-logs", a: a, b: b});
  setDocumentTitle(t("compare_logs_heading", {from: a.slice(0, 7), to: b.slice(0, 7)}));
  const my = ++token;
  if (observer) { observer.disconnect(); observer = null; }
  curRow = null;
  listView.innerHTML = '<h2>' + esc(t("compare_logs_heading", {from: a.slice(0, 7), to: b.slice(0, 7)})) + '</h2>' +
    '<div class="compare-logs"><section class="compare-log-col"><h3>A: ' + esc(a.slice(0, 7)) + '</h3>' +
    '<table class="log"><tbody id="compareRowsA"></tbody></table></section>' +
    '<section class="compare-log-col"><h3>B: ' + esc(b.slice(0, 7)) + '</h3>' +
    '<table class="log"><tbody id="compareRowsB"></tbody></table></section></div>' +
    '<div id="compareLogSentinel" class="hint">' + esc(t("loading")) + '</div>';
  showView("list");
  listView.dataset.src = "";
  const rowsA = listView.querySelector("#compareRowsA"), rowsB = listView.querySelector("#compareRowsB"),
        sentinel = listView.querySelector("#compareLogSentinel");
  const gapRow = '<tr class="log-gap"><td colspan="2"></td></tr>';
  let skipA = 0, skipB = 0, doneA = false, doneB = false;
  let initial = true, loading = false, base = "";

  function hasCommit(commits, hash) {
    return commits.some(c => c[0] === hash);
  }

  function alignedRows(commitsA, commitsB) {
    const ia = base ? commitsA.findIndex(c => c[0] === base) : -1;
    const ib = base ? commitsB.findIndex(c => c[0] === base) : -1;
    if (ia < 0 || ib < 0) {
      return [commitsA.map(rowHtml).join(""), commitsB.map(rowHtml).join("")];
    }
    const target = Math.max(ia, ib);
    const side = (commits, index) => {
      const html = [];
      for (let i = index; i < target; i++) html.push(gapRow);
      html.push(...commits.slice(0, index).map(rowHtml));
      html.push(...commits.slice(index).map((c, i) => rowHtml(c, i === 0 ? "log-common log-common-start" : "log-common")));
      return html.join("");
    };
    return [side(commitsA, ia), side(commitsB, ib)];
  }

  function syncCompareRows() {
    const listA = Array.from(rowsA.querySelectorAll("tr"));
    const listB = Array.from(rowsB.querySelectorAll("tr"));
    const commonA = listA.findIndex(row => row.classList.contains("log-common"));
    const commonB = listB.findIndex(row => row.classList.contains("log-common"));
    if (commonA < 0 || commonA !== commonB) return;
    for (let i = 0; i <= commonA; i++) {
      listA[i].style.height = "";
      listB[i].style.height = "";
      const height = Math.max(listA[i].getBoundingClientRect().height, listB[i].getBoundingClientRect().height);
      listA[i].style.height = height + "px";
      listB[i].style.height = height + "px";
    }
  }

  async function more() {
    if (loading || (doneA && doneB) || my !== token) return;
    loading = true;
    try {
      if (initial) {
        const [mb, firstA, firstB] = await Promise.all([
          api("/api/merge-base", {from: a, to: b}),
          api("/api/log", {rev: a, skip: skipA, limit: CHUNK}),
          api("/api/log", {rev: b, skip: skipB, limit: CHUNK})
        ]);
        if (my !== token) return;
        base = mb.hash || "";
        const commitsA = firstA.commits.slice(), commitsB = firstB.commits.slice();
        skipA += firstA.commits.length; skipB += firstB.commits.length;
        doneA = !firstA.has_more; doneB = !firstB.has_more;
        if (base) {
          while ((!hasCommit(commitsA, base) && !doneA) || (!hasCommit(commitsB, base) && !doneB)) {
            const needA = !hasCommit(commitsA, base) && !doneA;
            const needB = !hasCommit(commitsB, base) && !doneB;
            const [ra, rb] = await Promise.all([
              needA ? api("/api/log", {rev: a, skip: skipA, limit: CHUNK}) : Promise.resolve({commits: [], has_more: false}),
              needB ? api("/api/log", {rev: b, skip: skipB, limit: CHUNK}) : Promise.resolve({commits: [], has_more: false})
            ]);
            if (my !== token) return;
            commitsA.push(...ra.commits); commitsB.push(...rb.commits);
            skipA += ra.commits.length; skipB += rb.commits.length;
            if (needA) doneA = !ra.has_more;
            if (needB) doneB = !rb.has_more;
          }
        }
        const aligned = alignedRows(commitsA, commitsB);
        rowsA.innerHTML = aligned[0]; rowsB.innerHTML = aligned[1];
        initial = false;
        requestAnimationFrame(syncCompareRows);
      } else {
        const [ra, rb] = await Promise.all([
          doneA ? Promise.resolve({commits: [], has_more: false}) : api("/api/log", {rev: a, skip: skipA, limit: CHUNK}),
          doneB ? Promise.resolve({commits: [], has_more: false}) : api("/api/log", {rev: b, skip: skipB, limit: CHUNK})
        ]);
        if (my !== token) return;
        rowsA.insertAdjacentHTML("beforeend", ra.commits.map(rowHtml).join(""));
        rowsB.insertAdjacentHTML("beforeend", rb.commits.map(rowHtml).join(""));
        skipA += ra.commits.length; skipB += rb.commits.length;
        doneA = doneA || !ra.has_more; doneB = doneB || !rb.has_more;
      }
      updateCmpClasses();
      const done = doneA && doneB;
      sentinel.textContent = done ? "" : t("loading");
      if (fromHistory && restoreScroll) {
        listView.scrollTop = Math.max(0, Math.min(restoreScroll, listView.scrollHeight - listView.clientHeight));
      }
    } catch (e) {
      sentinel.textContent = t("error") + e.message;
      doneA = true; doneB = true;
    } finally {
      loading = false;
    }
  }
  if ("IntersectionObserver" in window) {
    const recheck = () => {
      if (!(doneA && doneB) && observer && my === token) { observer.unobserve(sentinel); observer.observe(sentinel); }
    };
    observer = new IntersectionObserver(es => { if (es[0].isIntersecting) more().then(recheck); },
                                        {root: listView, rootMargin: "600px"});
    observer.observe(sentinel);
  } else {
    (async () => { while (!(doneA && doneB) && my === token) await more(); })();
  }
}

// 差分は別ページ (/diff) で開く。ctx は {hash} (1コミット) または {from, to} (2コミット間の比較)
function diffUrl(ctx, f) {
  const p = Object.assign({}, ctx, {path: f.path});
  if (f.old_path) p.old = f.old_path;
  if (f.wip) p.wip = f.wip;  // 作業ツリーの変更 (staged / unstaged / untracked / conflict)
  return "/diff?" + new URLSearchParams(p);
}

const ST_CLASS = {M: "stM", T: "stM", A: "stA", D: "stD", R: "stR", C: "stR", U: "stU", "?": "stN"};
function badge(f) {  // 変更の種類 (M/A/D/R...) のバッジ。WIP の一覧で使う
  return f.st ? '<span class="st ' + (ST_CLASS[f.st] || "stM") + '">' + esc(f.st) + "</span>" : "";
}
function filesHtml(ctx, files, offset, withCheck) {
  offset = offset || 0;
  if (!files.length) return '<p class="hint">' + esc(t("no_changed_files")) + '</p>';
  let add = 0, del = 0, max = 0;
  files.forEach(f => { add += f.added || 0; del += f.deleted || 0; max = Math.max(max, (f.added || 0) + (f.deleted || 0)); });
  const scale = max > 40 ? 40 / max : 1;
  const rows = files.map((f, k) => {
    const a = f.added || 0, d = f.deleted || 0;
    const pa = a ? Math.max(1, Math.round(a * scale)) : 0, pd = d ? Math.max(1, Math.round(d * scale)) : 0;
    const name = f.old_path ? f.old_path + " => " + f.path : f.path;
    return '<tr class="frow" data-k="' + (offset + k) + '">' + (withCheck ? wipCheckCell(f) : "") + '<td class="fp">' + badge(f) + (f.tag ? '<span class="hint">[' + esc(f.tag) + '] </span>' : "") + '<a href="' + esc(diffUrl(ctx, f)) +
      '">' + esc(name) + '</a></td><td class="fc">' +
      (f.label ? esc(t(f.label)) : f.added === null ? "Bin" : a + d) + '</td><td class="fb"><span class="plus">' + "+".repeat(pa) +
      '</span><span class="minus">' + "-".repeat(pd) + "</span></td></tr>";
  }).join("");
  return '<div class="meta">' + files.length + (files.some(f => f.added !== undefined)
      ? " files changed, " + add + " insertions(+), " + del + " deletions(-)" : " files") + "</div>" +
    '<table class="files">' + rows + "</table>";
}

let curDetailHash = null;

function localBranches(h) {
  const n = DATA.nodes[h];
  return n ? n.refs.filter(r => r.kind === "branch" || r.kind === "current").map(r => r.name) : [];
}
function remoteBranches(h) {
  const n = DATA.nodes[h];
  return n ? n.refs.filter(r => r.kind === "remote").map(r => r.name) : [];
}
function defaultNewBranchName(h) {
  const remote = remoteBranches(h).sort()[0] || "";
  const slash = remote.indexOf("/");
  return slash >= 0 ? remote.slice(slash + 1) : remote;
}
function localTags(h) {
  const n = DATA.nodes[h];
  return n ? n.refs.filter(r => r.kind === "tag").map(r => r.name) : [];
}
function showStashList(fromHistory) {
  loadStashes();
  showLog(t("stash_list_title"), "", '<div class="meta">' + esc(t("stash_list_help")) + '</div>',
          !!fromHistory, 0, {path: "/api/stash-list", params: {}});
}
function doStash(op) {
  if (!actionHash) return;
  const h = actionHash, sel = stashByHash[h];
  if (!sel) return;
  const label = {apply: t("op_apply"), pop: t("op_pop"), drop: t("op_drop")}[op];
  const msg = {
    apply: t("stash_confirm_apply", {sel: sel}),
    pop: t("stash_confirm_pop", {sel: sel}),
    drop: t("stash_confirm_drop", {sel: sel, hash: h.slice(0, 7)}),
  }[op];
  if (!confirm(msg)) return;
  document.querySelectorAll("#stashActions button").forEach(b => { b.disabled = true; });
  setGraphStatus(t("stash_busy"), "progress");
  const reload = () => Promise.all([loadStashes(), refreshGraph().catch(() => {})]).then(() => {
    if (curView === "list" && listView.dataset.src === "/api/stash-list") showStashList(true);
  });
  post("/api/stash", {hash: h, op: op})
    .then(r => { clearCmp(); setGraphStatus(r.message, "success"); return reload(); })
    .catch(e => { setGraphStatus(t("stash_op_failed", {op: label, error: e.message}), "error"); return reload(); })
    .finally(() => updateCmpClasses());
}
function showReflog(branch) {  // branch が空なら HEAD の reflog
  const names = [];
  Object.values(DATA.nodes).forEach(n => n.refs.forEach(r => {
    if ((r.kind === "branch" || r.kind === "current") && !names.includes(r.name)) names.push(r.name);
  }));
  names.sort();
  if (branch && !names.includes(branch)) names.push(branch);
  const opts = ['<option value=""' + (branch ? "" : " selected") + ">HEAD</option>"].concat(names.map(n =>
    '<option value="' + esc(n) + '"' + (n === branch ? " selected" : "") + ">" + esc(n) + "</option>")).join("");
  const top = '<div class="meta"><label>' + esc(t("target_label")) + ': <select class="reflog-ref">' + opts + "</select></label></div>" +
    '<div class="meta">' + esc(t("reflog_help")) + '</div>';
  showLog("reflog " + esc(branch || "HEAD"), "", top, false, 0,
          {path: "/api/reflog", params: branch ? {branch: branch} : {}});
}
listView.addEventListener("change", ev => {
  const sel = ev.target.closest(".reflog-ref");
  if (sel) showReflog(sel.value);
});
function showDetail(h, fromHistory) {
  document.body.classList.remove("compare-logs-mode");
  if (!fromHistory) pushScreen({screen: "detail", hash: h});
  setDocumentTitle(t("commit_label") + " " + h.slice(0, 7));
  const back = '<button class="back-btn">' + t("back_history") + '</button>';
  detailView.innerHTML = back + '<p class="hint">' + esc(t("loading")) + '</p>';
  showView("detail");
  api("/api/commit", {hash: h}).then(c => {
    curCtx = {hash: c.hash}; curFiles = c.files; curDetailHash = c.hash;
    const parents = c.parents.map(p => esc(p.slice(0, 7))).join(", ") || t("none");
    detailView.innerHTML = back + "<h2>" + t("commit_label") + " " + esc(c.hash.slice(0, 7)) +
      ' <a class="detail-files" href="/files?hash=' + encodeURIComponent(c.hash) + '" title="' + esc(t("tip_files")) + '">' + esc(t("files")) + "</a></h2>" +
      '<span id="dChips">' + detailChips(c.hash) + "</span>" +
      '<div class="meta">' + esc(c.hash) + copyLink(c.hash) + "</div>" +
      '<div class="meta">Author: ' + esc(c.author) + "</div>" +
      '<div class="meta">Date: ' + esc(c.date) + "</div>" +
      '<div class="meta">Parent: ' + parents + "</div>" +
      "<pre>" + esc(c.message) + "</pre>" + filesHtml(curCtx, c.files) +
      '<div class="meta"><code>git show ' + esc(c.hash.slice(0, 7)) + "</code></div>";
  }).catch(e => {
    detailView.innerHTML = back + '<p class="hint">' + esc(t("error")) + esc(e.message) + "</p>";
  });
}

function post(path, body) {
  return fetch(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)})
    .then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }));
}
function doAction(btn) {  // ブランチ/タグのチップの × (削除)
  if (btn.dataset.act !== "delete-ref") return;
  const kind = btn.dataset.kind, name = btn.dataset.name;
  const url = "/api/ref-delete", body = {kind: kind, name: name};
  if (!confirm(t(kind === "branch" ? "confirm_delete_branch" : "confirm_delete_tag", {name: name}))) return;
  btn.disabled = true; setGraphStatus(t("deleting"), "progress");
  post(url, body)
    .then(r => {
      if (!r.needs_force) return r;
      if (!confirm(t("confirm_force_delete", {name: name}))) return null;
      return post(url, Object.assign({}, body, {force: true}));
    })
    .then(r => {
      if (r === null) { setGraphStatus(t("force_delete_cancelled")); return; }
      setGraphStatus(r.message, "success");
      return refreshGraph().catch(() => {});
    })
    .catch(e => setGraphStatus(t("delete_failed", {error: e.message}), "error", true))
    .finally(() => { btn.disabled = false; });
}

const graphStatusTimers = new Map();
function setGraphStatus(message, type, clearProgress) {
  const container = document.getElementById("graphStatus");
  if (clearProgress) {
    container.querySelectorAll(".graph-toast.progress").forEach(toast => {
      clearTimeout(graphStatusTimers.get(toast));
      graphStatusTimers.delete(toast);
      toast.remove();
    });
  }
  if (!message) {
    container.querySelectorAll(".graph-toast.progress").forEach(toast => {
      clearTimeout(graphStatusTimers.get(toast));
      graphStatusTimers.delete(toast);
      toast.remove();
    });
    return;
  }
  const progress = type === "progress";  // 進捗かどうかは表示文言ではなく type で明示する (文言は翻訳で変わる)
  const toast = [...container.children].find(el =>
    el.dataset.message === message && el.classList.contains(progress ? "progress" : (type || "info")));
  if (toast) {
    clearTimeout(graphStatusTimers.get(toast));
    graphStatusTimers.delete(toast);
  }
  const item = toast || document.createElement("div");
  item.className = "graph-toast " + (type || "info");
  item.setAttribute("role", "status");
  item.setAttribute("aria-live", "polite");
  item.dataset.message = message;
  item.innerHTML = '<span class="graph-toast-message"></span><button type="button" class="graph-toast-close" aria-label="' + esc(t("close_notification")) + '" title="' + esc(t("close_notification")) + '">×</button>';
  item.querySelector(".graph-toast-message").textContent = message;
  item.querySelector(".graph-toast-close").addEventListener("click", () => {
    clearTimeout(graphStatusTimers.get(item));
    graphStatusTimers.delete(item);
    item.remove();
  });
  if (!toast) container.appendChild(item);
  if (!progress) {
    graphStatusTimers.set(item, setTimeout(() => {
      item.remove();
      graphStatusTimers.delete(item);
    }, 15000));
  }
}

// 操作のあと、グラフを(表示したまま)作り直して新しいブランチ/タグ/HEAD を反映する
function refreshGraph() {
  setGraphStatus(t("graph_updating"), "progress");
  return fetch("/api/graph")
    .then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }))
    .then(r => {
      const keys = new Set(Object.keys(DATA.nodes).concat(Object.keys(r.nodes)));
      const gc = document.getElementById("graph"), top0 = gc.scrollTop, left0 = gc.scrollLeft;
      DATA = {nodes: r.nodes, edges: r.edges, writable: r.writable, merge: r.merge};
      gc.innerHTML = r.svg;
      applyGraphZoom();
      gc.scrollTop = top0; gc.scrollLeft = left0;
      selected = null;
      for (const k in nodeEl) delete nodeEl[k];
      init();
      if (selectedKey) {
        const g = [...gc.querySelectorAll("g.node, g.edge")].find(x => {
          const t = x.querySelector("title"); return t && t.textContent === selectedKey;
        });
        if (g) { g.classList.add("sel"); selected = g; }
      }
      updateCmpClasses();
      loadStashes();
      updateCurrentBranch().catch(e => setGraphStatus(t("current_branch_failed", {error: e.message}), "error"));
      keys.forEach(h => listView.querySelectorAll('tr[data-h="' + h + '"] .rc').forEach(el => { el.innerHTML = chips(h); }));
      const c = detailView.querySelector("#dChips");
      if (c && curDetailHash) c.innerHTML = detailChips(curDetailHash);
      setGraphStatus("");
    })
    .catch(e => {
      setGraphStatus(t("graph_update_failed", {error: e.message}), "error");
      throw e;
    });
}

// ---- コミット比較 ----
function commitLine(label, c) {
  return '<div class="meta"><span class="tag tag' + label + '">' + label + "</span>" + chips(c.hash) +
    esc(c.hash.slice(0, 7)) + copyLink(c.hash) + " — " + esc(c.message.split("\n")[0]) +
    '<div class="hint">' + esc(c.author) + " / " + esc(c.date) + "</div></div>";
}
function showCompare(a, b, fromHistory) {  // a, b はコミットのハッシュ
  document.body.classList.remove("compare-logs-mode");
  if (!fromHistory) pushScreen({screen: "compare", a: a, b: b});
  setDocumentTitle(t("compare_heading", {from: a.slice(0, 7), to: b.slice(0, 7)}));
  const btns = '<button class="back-btn">' + t("back_history") + '</button><button class="sbtn">' + esc(t("swap_ab")) + '</button>';
  detailView.innerHTML = btns + '<p class="hint">' + esc(t("loading")) + '</p>';
  showView("detail");
  api("/api/compare", {from: a, to: b}).then(r => {
    curCtx = {from: r.from.hash, to: r.to.hash}; curFiles = r.files;
    detailView.innerHTML = btns + "<h2>" + esc(t("compare_heading", {from: r.from.hash.slice(0, 7), to: r.to.hash.slice(0, 7)})) + "</h2>" +
      commitLine("A", r.from) + commitLine("B", r.to) + filesHtml(curCtx, r.files) +
      '<div class="meta"><code>git diff ' + esc(r.from.hash.slice(0, 7)) + " " + esc(r.to.hash.slice(0, 7)) + "</code></div>";
  }).catch(e => {
    detailView.innerHTML = btns + '<p class="hint">' + esc(t("error")) + esc(e.message) + "</p>";
  });
}
function updateCmpClasses() {  // 選択中のコミットを、グラフのノードとログの行の両方でハイライトする
  document.querySelectorAll(".cmpA, .cmpB, .actionTarget").forEach(el => el.classList.remove("cmpA", "cmpB", "actionTarget"));
  const compareRowsA = listView.querySelector("#compareRowsA");
  const compareRowsB = listView.querySelector("#compareRowsB");
  const rowsFor = (hash, side) => {
    const root = compareRowsA && compareRowsB ? (side === "A" ? compareRowsA : compareRowsB) : listView;
    return root.querySelectorAll('tr[data-h="' + hash + '"]');
  };
  if (compareAHash) {
    if (nodeEl[compareAHash]) nodeEl[compareAHash].classList.add("cmpA");
    rowsFor(compareAHash, "A").forEach(tr => tr.classList.add("cmpA"));
  }
  if (compareBHash) {
    if (nodeEl[compareBHash]) nodeEl[compareBHash].classList.add("cmpB");
    rowsFor(compareBHash, "B").forEach(tr => tr.classList.add("cmpB"));
  }
  if (actionHash) {
    if (nodeEl[actionHash]) nodeEl[actionHash].classList.add("actionTarget");
    listView.querySelectorAll('tr[data-h="' + actionHash + '"]').forEach(tr => tr.classList.add("actionTarget"));
  }
  document.getElementById("actionTarget").textContent = actionHash ? t("target_label") + ": " + actionHash.slice(0, 7) : "";
  document.getElementById("cmpA").textContent = compareAHash ? "A: " + compareAHash.slice(0, 7) : "";
  document.getElementById("cmpB").textContent = compareBHash ? "B: " + compareBHash.slice(0, 7) : "";
  document.getElementById("cmpActToggle").setAttribute("aria-expanded", cmpActOpen ? "true" : "false");
  document.getElementById("cmpActToggle").textContent = cmpActOpen ? t("actions").replace("▾", "▴") : t("actions");
  document.getElementById("abActToggle").setAttribute("aria-expanded", abActOpen ? "true" : "false");
  document.getElementById("abActToggle").textContent = abActOpen ? t("ab_actions").replace("▾", "▴") : t("ab_actions");
  document.getElementById("clearCmpBtn").disabled = !compareAHash && !compareBHash && !actionHash;
  document.getElementById("cmpActions").hidden = !cmpActOpen;
  document.getElementById("abActions").hidden = !abActOpen;
  document.getElementById("compareBtn").hidden = !compareAHash || !compareBHash;
  document.getElementById("compareLogsBtn").hidden = !compareAHash || !compareBHash;
  document.getElementById("rebaseRangeBtn").hidden = !compareAHash || !compareBHash;
  const isStash = !!actionHash && !!stashByHash[actionHash];
  document.getElementById("aActions").hidden = !actionHash || isStash;
  document.getElementById("stashActions").hidden = !isStash;
  document.getElementById("actionsEmpty").hidden = !!actionHash || (!!compareAHash && !!compareBHash);
  const canMerge = !!actionHash && !isStash && !!DATA.writable && !!DATA.merge;
  document.getElementById("mergeBtn").disabled = !canMerge;
  document.getElementById("rebaseBtn").disabled = !canMerge;
  document.getElementById("irebaseBtn").disabled = !canMerge;
  document.getElementById("rebaseRangeBtn").disabled = !compareAHash || !compareBHash || !canMerge;
  document.getElementById("cherryBtn").disabled = !canMerge;
  document.getElementById("revertBtn").disabled = !canMerge;
  document.getElementById("resetBtn").disabled = !canMerge;
  const canWrite = !!DATA.writable;
  document.querySelectorAll("#aActions button")
    .forEach(el => { el.disabled = !canWrite || (["mergeBtn", "rebaseBtn", "irebaseBtn", "cherryBtn", "revertBtn", "resetBtn"].includes(el.id) && !DATA.merge); });
  document.querySelectorAll("#remoteActions button")
    .forEach(el => { el.disabled = !canWrite || (el.dataset.cact === "pull" && !DATA.merge); });
  document.querySelectorAll("#remoteConfigActions button").forEach(el => { el.disabled = !canWrite; });
  updatePushActions();
  document.querySelector('#remoteActions [data-cact="pull"]').title =
    DATA.merge ? "" : t("pull_needs_worktree");
  document.getElementById("stashApplyBtn").disabled = !DATA.merge;  // apply / pop はワークツリーが必要
  document.getElementById("stashPopBtn").disabled = !DATA.merge;
  document.getElementById("stashDropBtn").disabled = !canWrite;
  document.getElementById("deleteBranchBtn").disabled = !canWrite || !actionHash || isStash ||
    !localBranches(actionHash).some(name => name !== currentBranch);
  document.getElementById("deleteRemoteBranchBtn").disabled = !canWrite || !actionHash || isStash || !remoteBranches(actionHash).length;
  document.getElementById("deleteTagBtn").disabled = !canWrite || !actionHash || isStash || !localTags(actionHash).length;
  document.getElementById("pushSelBtn").disabled = !canWrite || !actionHash || isStash || !localBranches(actionHash).length;
  document.getElementById("renameBranchBtn").disabled = !canWrite || !actionHash || isStash || !localBranches(actionHash).length;
}
let cmpActOpen = false;
let abActOpen = false;
function setOtherMenu(open) {  // 「その他」のメニュー
  const btn = document.getElementById("otherMenuBtn");
  document.getElementById("otherActions").hidden = !open;
  btn.setAttribute("aria-expanded", open ? "true" : "false");
  btn.textContent = open ? t("other").replace("▾", "▴") : t("other");
}
function closeMenus() {  // 開いているメニュー (操作 / AB操作 / その他) をすべて閉じる
  const changed = cmpActOpen || abActOpen || !document.getElementById("otherActions").hidden;
  cmpActOpen = false;
  abActOpen = false;
  setOtherMenu(false);
  if (changed) updateCmpClasses();
}
let stashByHash = {};  // stash のコミットのハッシュ -> stash@{n}
function loadStashes() {
  return fetch("/api/stash-list").then(r => r.ok ? r.json() : null).then(r => {
    if (!r) return;
    stashByHash = {};
    r.commits.forEach(c => { stashByHash[c[0]] = c[2]; });
    updateCmpClasses();
  }).catch(() => {});
}
function askCheckoutConflict(r) {  // チェックアウトを止めているファイルを見せて、stash / 削除 / キャンセルを選んでもらう
  return new Promise(resolve => {
    const m = document.getElementById("coModal");
    const list = fs => "<ul>" + fs.map(f => "<li>" + esc(f) + "</li>").join("") + "</ul>";
    let html = "";
    if (r.modified.length) html += "<h3>" + esc(t("co_modified")) + "</h3>" + list(r.modified);
    if (r.untracked.length) html += "<h3>" + esc(t("co_untracked")) + "</h3>" + list(r.untracked);
    document.getElementById("coFiles").innerHTML = html;
    const done = v => { m.hidden = true; m.onclick = null; resolve(v); };
    m.hidden = false;
    m.onclick = ev => {
      const b = ev.target.closest("[data-co]");
      if (b) done(b.dataset.co === "cancel" ? null : b.dataset.co);
      else if (ev.target === m) done(null);
    };
  });
}
function openActionDialog(title, fields, submitLabel, opts) {  // opts: {wide, validate(values) -> エラー文 or null}
  opts = opts || {};
  return new Promise(resolve => {
    const modal = document.getElementById("actionModal"), form = document.getElementById("actionForm");
    form.className = "action-dialog" + (opts.wide ? " wide" : "");
    document.getElementById("actionError").textContent = "";
    let cleanup = null;
    const finish = value => {
      if (cleanup) cleanup();
      modal.hidden = true;
      form.onsubmit = null;
      modal.onclick = null;
      window.removeEventListener("keydown", onKeyDown);
      resolve(value);
    };
    const onKeyDown = ev => { if (ev.key === "Escape") finish(null); };
    document.getElementById("actionTitle").textContent = title;
    document.getElementById("actionFields").innerHTML = fields;
    document.getElementById("actionSubmit").textContent = submitLabel;
    modal.hidden = false;
    if (opts.onOpen) cleanup = opts.onOpen(form) || null;
    form.onsubmit = ev => {
      ev.preventDefault();
      const values = Object.fromEntries(new FormData(form).entries());
      form.querySelectorAll('input[type="checkbox"][name]').forEach(input => { values[input.name] = input.checked; });
      if (opts.validate) {
        const err = opts.validate(values);
        document.getElementById("actionError").textContent = err || "";
        if (err) return;
      }
      finish(values);
    };
    modal.onclick = ev => {
      if (ev.target === modal || ev.target.closest("[data-dialog-cancel]")) finish(null);
    };
    window.addEventListener("keydown", onKeyDown);
    const first = form.querySelector("input, select");
    if (first) first.focus();
  });
}
function setCmpMsg(text, state) {  // state: true = 成功 / false = 失敗 / "progress" = 実行中
  setGraphStatus(text, state === "progress" ? "progress" : state ? "success" : "error", state === false);
}
function setRemoteMsg(text, state) {
  setCmpMsg(text, state);
}
async function doCmpAction(btn) {  // 選択対象に対するブランチ作成 / タグ作成 / チェックアウト
  if (!actionHash) return;
  const h = actionHash, act = btn.dataset.cact;
  let values, url, body, ask = null;
  if (act === "branch") {
    values = await openActionDialog(t("create_branch"), '<label>' + esc(t("branch_name")) + '<input type="text" name="name" required autofocus></label>' +
      '<label class="check"><input type="checkbox" name="checkout"> ' + esc(t("checkout_after_create")) + '</label>', t("create"));
    if (!values) return;
    url = "/api/branch"; body = {hash: h, name: values.name.trim(), checkout: values.checkout};
    if (!body.name) { setCmpMsg(t("branch_name_required"), false); return; }
    if (body.checkout) ask = t("confirm_create_checkout", {name: body.name});
  } else if (act === "tag") {
    values = await openActionDialog(t("create_tag"), '<label>' + esc(t("tag_name")) + '<input type="text" name="name" required autofocus></label>' +
      '<label>' + esc(t("message_optional")) + '<input type="text" name="message"></label>', t("create"));
    if (!values) return;
    url = "/api/tag"; body = {hash: h, name: values.name.trim(), message: values.message.trim()};
    if (!body.name) { setCmpMsg(t("tag_name_required"), false); return; }
  } else if (act === "checkout") {
    const branches = localBranches(h);
    const remoteRefs = remoteBranches(h).sort();
    const defaultRemoteRef = remoteRefs[0] || "";
    const defaultNewBranch = defaultNewBranchName(h);
    values = await openActionDialog(t("checkout"), '<label>' + esc(t("switch_to")) + '<select name="branch">' +
      branches.map(branch => '<option value="' + esc(branch) + '">' + esc(branch) + '</option>').join("") +
      '<option value="">' + esc(t("checkout_detached_option")) + '</option>' +
      '<option value="__new_branch__">' + esc(t("checkout_new_branch_option")) + '</option>' +
      '</select></label>' +
      '<label id="newBranchField" hidden>' + esc(t("new_branch_name")) + '<input type="text" name="newBranch" value="' + esc(defaultNewBranch) + '" placeholder="' + esc(t("new_branch_placeholder")) + '"></label>' +
      '<label id="trackUpstreamField" class="check" hidden><input type="checkbox" name="trackUpstream"' + (defaultRemoteRef ? ' checked' : '') + '> ' + esc(t("track_upstream")) + (defaultRemoteRef ? ' (' + esc(defaultRemoteRef) + ')' : '') + '</label>', t("checkout"), {
        onOpen: form => {
          const select = form.elements.branch, field = form.querySelector("#newBranchField"), track = form.querySelector("#trackUpstreamField");
          const update = () => { field.hidden = select.value !== "__new_branch__"; track.hidden = select.value !== "__new_branch__" || !defaultRemoteRef; };
          select.addEventListener("change", update);
          update();
          return () => select.removeEventListener("change", update);
        },
        validate: values => values.branch === "__new_branch__" && !values.newBranch.trim() ?
          t("new_branch_name_required") : null
      });
    if (!values) return;
    if (values.branch === "__new_branch__") {
      const name = values.newBranch.trim();
      url = "/api/branch"; body = {hash: h, name: name, checkout: true};
      if (values.trackUpstream && defaultRemoteRef) body.upstream = defaultRemoteRef;
      ask = t("confirm_create_checkout", {name: name});
    } else if (values.branch) {
      url = "/api/checkout"; body = {branch: values.branch};
      ask = t("confirm_checkout_branch", {branch: values.branch});
    } else {
      url = "/api/checkout"; body = {hash: h};
      ask = t("confirm_checkout_detached", {hash: h.slice(0, 7)});
    }
  } else return;
  if (ask && !confirm(ask)) return;
  btn.disabled = true; setCmpMsg(t("running"), "progress");
  const run = extra => post(url, Object.assign({}, body, extra || {}));
  run()
    .then(r => {
      if (!r.blocked) return r;
      setCmpMsg("", true);
      return askCheckoutConflict(r).then(mode => {
        if (!mode) return {cancelled: true, message: t("checkout_cancelled")};
        setCmpMsg(t("running"), "progress");
        return run({on_conflict: mode});
      });
    })
    .then(r => {
      if (r.cancelled) { setCmpMsg(r.message, false); return; }
      setCmpMsg(r.message, true);
      return refreshGraph().catch(() => {});
    })
    .catch(e => setCmpMsg(e.message, false))
    .finally(() => { btn.disabled = false; });
}
async function doDeleteBranch(btn) {
  const branches = localBranches(actionHash).filter(name => name !== currentBranch).sort();
  if (!branches.length) return;
  const values = await openActionDialog(t("delete_branch"),
    '<label>' + esc(t("branch_to_delete")) + '<select name="name">' + branches.map(branch =>
      '<option value="' + esc(branch) + '">' + esc(branch) + "</option>").join("") +
    "</select></label>", t("delete"));
  if (!values) return;
  const name = values.name;
  if (!confirm(t("confirm_delete_branch", {name: name}))) return;
  const body = {kind: "branch", name: name};
  btn.disabled = true; setCmpMsg(t("deleting"), "progress");
  post("/api/ref-delete", body)
    .then(r => {
      if (!r.needs_force) return r;
      if (!confirm(t("confirm_force_delete", {name: name}))) return null;
      return post("/api/ref-delete", Object.assign({}, body, {force: true}));
    })
    .then(r => {
      if (r === null) { setCmpMsg(t("force_delete_cancelled"), false); return; }
      setCmpMsg(r.message, true);
      return refreshGraph().catch(() => {});
    })
    .catch(e => setCmpMsg(t("delete_failed", {error: e.message}), false))
    .finally(() => { btn.disabled = false; });
}
async function doDeleteTag(btn) {
  const tags = localTags(actionHash).sort();
  if (!tags.length) return;
  const values = await openActionDialog(t("delete_tag"),
    '<label>' + esc(t("tag_to_delete")) + '<select name="name">' + tags.map(tag =>
      '<option value="' + esc(tag) + '">' + esc(tag) + "</option>").join("") +
    "</select></label>", t("delete"));
  if (!values) return;
  const name = values.name;
  if (!confirm(t("confirm_delete_tag", {name: name}))) return;
  btn.disabled = true; setCmpMsg(t("deleting"), "progress");
  post("/api/ref-delete", {kind: "tag", name: name})
    .then(r => { setCmpMsg(r.message, true); return refreshGraph().catch(() => {}); })
    .catch(e => setCmpMsg(t("delete_failed", {error: e.message}), false))
    .finally(() => { btn.disabled = false; });
}
async function doDeleteRemoteBranch(btn) {
  const branches = remoteBranches(actionHash).sort();
  if (!branches.length) return;
  const values = await openActionDialog(t("delete_remote_branch"),
    '<label>' + esc(t("remote_branch_to_delete")) + '<select name="name">' + branches.map(branch =>
      '<option value="' + esc(branch) + '">' + esc(branch) + "</option>").join("") +
    "</select></label>", t("delete"));
  if (!values) return;
  const name = values.name;
  if (!confirm(t("confirm_delete_remote_branch", {name: name}))) return;
  const body = {kind: "remote-branch", name: name};
  btn.disabled = true; setCmpMsg(t("deleting"), "progress");
  const run = extra => post("/api/ref-delete", Object.assign({}, body, extra || {}));
  try {
    let r = await run();
    if (r.auth_required) {
      const auth = await openActionDialog(t("http_auth"),
        '<label>' + esc(t("auth_username")) + '<input name="username" autocomplete="username" autofocus></label>' +
        '<label>' + esc(t("auth_password")) + '<input type="password" name="password" autocomplete="current-password"></label>',
        t("authenticate"));
      if (!auth) { setCmpMsg(t("push_cancelled"), false); return; }
      r = await run({auth: auth, auth_remote: r.auth_remote, auth_retry: true});
    }
    setCmpMsg(r.message, true);
    await refreshGraph().catch(() => {});
  } catch (e) {
    setCmpMsg(e.message, false);
  } finally {
    btn.disabled = false;
  }
}
async function doRemoteAction(btn) {  // fetch / pull / push
  const act = btn.dataset.cact;
  const url = "/api/" + act;
  let body = {}, ask = null;
  if (act === "fetch") {
    const values = await openActionDialog("fetch", '<label class="check"><input type="checkbox" name="prune"> ' + esc(t("prune_label")) + '</label>', "fetch");
    if (!values) return;
    body = {prune: values.prune};
  } else if (act === "pull") {
    const values = await openActionDialog("pull", '<label>' + esc(t("pull_mode")) + '<select name="mode">' +
      '<option value="ff-only">' + esc(t("pull_opt_ff")) + '</option>' +
      '<option value="merge">' + esc(t("pull_opt_merge")) + '</option>' +
      '<option value="rebase">' + esc(t("pull_opt_rebase")) + '</option></select></label>', "pull");
    if (!values) return;
    const mode = values.mode;
    body = {mode: mode};
    ask = t("confirm_pull", {mode: {"ff-only": t("pull_mode_ff"), "merge": t("pull_mode_merge"), "rebase": t("pull_mode_rebase")}[mode]});
  } else if (act === "push") {
    const branches = [...new Set([currentBranch, ...localBranches(actionHash)])]
      .filter(Boolean).sort((a, b) => a.localeCompare(b));
    if (!branches.length) return;
    // 操作メニューの push は選択中のコミットのブランチ、メニューバーの push は HEAD (現在のブランチ) を既定にする
    const selected = btn.dataset.pushSel ? localBranches(actionHash).sort((a, b) => a.localeCompare(b)) : [];
    const defaultBranch = selected.length ? (selected.includes(currentBranch) ? currentBranch : selected[0]) : (currentBranch || branches[0]);
    const remoteInfo = await api("/api/remotes", {});
    const remotes = remoteInfo.remotes || [];
    if (!remotes.length) { setRemoteMsg(t("no_remotes"), false); return; }
    const defaultRemote = remotes.some(r => r.name === "origin") ? "origin" : remotes[0].name;
    const values = await openActionDialog("push",
      '<label>' + esc(t("push_branch_select")) + '<select name="branch">' + branches.map(branch =>
        '<option value="' + esc(branch) + '"' + (branch === defaultBranch ? " selected" : "") + '>' + esc(branch) + (branch === currentBranch ? " (HEAD)" : "") + "</option>").join("") +
      '</select></label>' +
      '<label>' + esc(t("push_remote_select")) + '<select name="remote">' + remotes.map(r =>
        '<option value="' + esc(r.name) + '"' + (r.name === defaultRemote ? " selected" : "") + '>' + esc(r.name) + "</option>").join("") +
      '</select></label>' +
      '<label>' + esc(t("push_remote_branch")) + '<input name="remoteBranch" value="' + esc(defaultBranch) + '" required></label>' +
      '<label class="check"><input type="checkbox" name="force"> ' + esc(t("force_label")) + '</label>', "push", {
        onOpen: form => {
          const branch = form.elements.branch, remoteBranch = form.elements.remoteBranch;
          const update = () => { remoteBranch.value = branch.value; };
          branch.addEventListener("change", update);
          return () => branch.removeEventListener("change", update);
        }
      });
    if (!values) return;
    body = {branch: values.branch, remote: values.remote, remote_branch: values.remoteBranch.trim(), force: values.force};
    const what = t("what_branch", {name: values.branch}) + " → " + values.remote;
    ask = body.force
      ? t("confirm_push_force", {what: what})
      : t("confirm_push", {what: what});
  } else if (act === "push-tag") {
    body = {tag: btn.dataset.tag};
    ask = t("confirm_push_tag", {tag: body.tag});
  } else return;
  if (ask && !confirm(ask)) return;
  btn.disabled = true; setRemoteMsg(t("running_remote"), "progress");
  let p = post(url, body).then(async r => {
    if (!r.auth_required) return r;
    const auth = await openActionDialog(t("http_auth"),
      '<label>' + esc(t("auth_username")) + '<input name="username" autocomplete="username" autofocus></label>' +
      '<label>' + esc(t("auth_password")) + '<input type="password" name="password" autocomplete="current-password"></label>',
      t("authenticate"));
    if (!auth) return {cancelled: true, message: t("push_cancelled")};
    setRemoteMsg(t("running_remote"), "progress");
    return post(url, Object.assign({}, body, {auth: auth, auth_remote: r.auth_remote, auth_retry: true}));
  });
  if (act === "push-tag") {
    p = p.then(r => {
      if (!r.needs_remote) return r;
      const input = prompt(t("prompt_push_tag_remote", {tag: r.tag, remotes: r.remotes.join(" / ")}), r.remote);
      if (input === null) return {cancelled: true, message: t("push_cancelled")};
      const remote = input.trim();
      if (r.remotes.indexOf(remote) < 0) throw new Error(t("remote_unregistered", {remote: remote}));
      setRemoteMsg(t("running_remote"), "progress");
      return post(url, Object.assign({}, body, {remote: remote}));
    });
  }
  if (act === "push") {
    p = p.then(r => {
      if (!r.needs_upstream) return r;
      const cancelled = {cancelled: true, message: t("push_cancelled")};
      let remote = r.remote;
      if (r.remotes && r.remotes.length > 1) {  // リモートが複数: push 先を選ばせる
        const input = prompt(t("prompt_push_remote", {branch: r.branch, remotes: r.remotes.join(" / ")}), r.remote);
        if (input === null) return cancelled;
        remote = input.trim();
        if (r.remotes.indexOf(remote) < 0) throw new Error(t("remote_unregistered", {remote: remote}));
      } else if (!confirm(t("confirm_set_upstream", {branch: r.branch, remote: r.remote}))) return cancelled;
      setRemoteMsg(t("running_remote"), "progress");
      return post(url, Object.assign({}, body, {set_upstream: true, remote: remote}));
    });
  }
  p.then(r => {
    if (r.cancelled) { setRemoteMsg(r.message, false); return; }
    if (act === "push") {
      // git push 自体の完了を先に通知し、グラフ更新は別の進捗表示で行う。
      setRemoteMsg(r.message, true);
      return refreshGraph().catch(() => {});
    }
    setRemoteMsg(r.message, r.ok !== false && !r.fetch_failed);
    return refreshGraph().catch(() => {}).then(() => {
      if (r.conflict) { clearCmp(); showWip(); }
    });
  })
    .catch(e => setRemoteMsg(e.message, false))
    .finally(() => { btn.disabled = false; });
}
async function doRemoteConfig(btn) {  // remote 追加 / 削除
  let url, body, ask = null;
  if (btn.dataset.cact === "remote-add") {
    const values = await openActionDialog(t("remote_add"),
      '<label>' + esc(t("name")) + '<input type="text" name="name" placeholder="origin" required autofocus></label>' +
      '<label>URL<input type="text" name="url" required></label>' +
      '<label class="check"><input type="checkbox" name="fetch" checked> ' + esc(t("fetch_after_add")) + '</label>', t("add"));
    if (!values) return;
    body = {name: values.name.trim(), url: values.url.trim(), fetch: !!values.fetch};
    if (!body.name || !body.url) { setRemoteMsg(t("name_url_required"), false); return; }
    url = "/api/remote-add";
  } else {
    let list;
    try { list = (await api("/api/remotes", {})).remotes; } catch (e) { setRemoteMsg(e.message, false); return; }
    if (!list.length) { setRemoteMsg(t("no_remotes"), false); return; }
    const values = await openActionDialog(t("remote_remove"),
      '<label>' + esc(t("remote_to_remove")) + '<select name="name">' + list.map(r =>
        '<option value="' + esc(r.name) + '">' + esc(r.name) + (r.url ? " — " + esc(r.url) : "") + "</option>").join("") +
      "</select></label>", t("delete"));
    if (!values) return;
    body = {name: values.name};
    url = "/api/remote-remove";
    ask = t("confirm_remove_remote", {name: body.name});
  }
  if (ask && !confirm(ask)) return;
  btn.disabled = true;
  setRemoteMsg(body.fetch ? t("running_fetch") : t("running"), "progress");
  post(url, body)
    .then(r => { setRemoteMsg(r.message, r.ok !== false && !r.fetch_failed); return refreshGraph().catch(() => {}); })
    .catch(e => setRemoteMsg(e.message, false))
    .finally(() => { btn.disabled = false; });
}
function clearCmp() {
  compareAHash = null; compareBHash = null; actionHash = null;
  updateCmpClasses();
  saveUiState();
}
function selectActionTarget(hash) { actionHash = hash; updateCmpClasses(); saveUiState(); }
function doRebase() {
  if (!actionHash) return;
  const h = actionHash, btn = document.getElementById("rebaseBtn");
  btn.disabled = true;
  fetch("/api/head").then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }))
    .then(hd => {
      if (hd.merging) throw new Error(t("op_in_progress_finish"));
      if (!hd.branch) throw new Error(t("detached_rebase"));
      if (!confirm(t("confirm_rebase", {branch: hd.branch, hash: h.slice(0, 7)}))) return null;
      setGraphStatus(t("rebasing"), "progress");
      return post("/api/rebase", {hash: h});
    })
    .then(r => {
      if (r === null) { setGraphStatus(""); return; }
      clearCmp();
      setGraphStatus(r.message, "success");
      return refreshGraph().catch(() => {});
    })
    .catch(e => { setGraphStatus(t("rebase_error", {error: e.message}), "error"); })
    .finally(() => { btn.disabled = false; });
}
function doRebaseRange() {
  if (!compareAHash || !compareBHash) return;
  const from = compareAHash, to = compareBHash, btn = document.getElementById("rebaseRangeBtn");
  btn.disabled = true;
  fetch("/api/head").then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }))
    .then(hd => {
      if (hd.merging) throw new Error(t("op_in_progress_finish"));
      if (!hd.branch) throw new Error(t("detached_rebase"));
      if (!confirm(t("confirm_rebase_range", {branch: hd.branch, from: from.slice(0, 7), to: to.slice(0, 7)}))) return null;
      setGraphStatus(t("rebasing"), "progress");
      return post("/api/rebase-range", {from: from, to: to});
    })
    .then(r => {
      if (r === null) { setGraphStatus(""); return; }
      clearCmp();
      setGraphStatus(r.message, "success");
      return refreshGraph().catch(() => {});
    })
    .catch(e => { setGraphStatus(t("rebase_error", {error: e.message}), "error"); })
    .finally(() => { btn.disabled = false; });
}
function doCherryPick() {
  if (!actionHash) return;
  const h = actionHash, btn = document.getElementById("cherryBtn");
  btn.disabled = true;
  fetch("/api/head").then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }))
    .then(hd => {
      if (hd.merging) throw new Error(t("op_in_progress_finish"));
      if (!confirm(t("confirm_cherry_pick", {hash: h.slice(0, 7), target: hd.branch ? t("what_branch", {name: hd.branch}) : t("target_detached_head")}))) return null;
      setGraphStatus(t("cherry_picking"), "progress");
      return post("/api/cherry-pick", {hash: h});
    })
    .then(r => {
      if (r === null) { setGraphStatus(""); return; }
      clearCmp();
      setGraphStatus(r.message, "success");
      return refreshGraph().catch(() => {});
    })
    .catch(e => { setGraphStatus(t("cherry_pick_error", {error: e.message}), "error"); })
    .finally(() => { btn.disabled = false; });
}
function doRevert() {
  if (!actionHash) return;
  const h = actionHash, btn = document.getElementById("revertBtn");
  btn.disabled = true;
  fetch("/api/head").then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }))
    .then(hd => {
      if (hd.merging) throw new Error(t("op_in_progress_finish"));
      if (!confirm(t("confirm_revert", {hash: h.slice(0, 7), target: hd.branch ? t("what_branch", {name: hd.branch}) : t("target_detached_head")}))) return null;
      setGraphStatus(t("reverting"), "progress");
      return post("/api/revert", {hash: h});
    })
    .then(r => {
      if (r === null) { setGraphStatus(""); return; }
      clearCmp();
      setGraphStatus(r.message, "success");
      return refreshGraph().catch(() => {});
    })
    .catch(e => { setGraphStatus(t("revert_error", {error: e.message}), "error"); })
    .finally(() => { btn.disabled = false; });
}
async function doRenameBranch(btn) {
  const branches = localBranches(actionHash).sort();
  if (!branches.length) return;
  const values = await openActionDialog(t("rename_branch"),
    '<label>' + esc(t("branch_to_rename")) + '<select name="old">' + branches.map(branch =>
      '<option value="' + esc(branch) + '">' + esc(branch) + "</option>").join("") + "</select></label>" +
    '<label>' + esc(t("new_branch_name")) + '<input type="text" name="new_name" value="' + esc(branches[0]) + '" required autofocus></label>',
    t("rename"),
    {onOpen: form => {  // 選んだブランチ名を、新しい名前の初期値にする
      const sel = form.elements["old"], inp = form.elements["new_name"];
      sel.addEventListener("change", () => { inp.value = sel.value; });
    }});
  if (!values) return;
  const oldName = values.old, newName = values.new_name.trim();
  if (!newName || newName === oldName) { setCmpMsg(t("branch_rename_same"), false); return; }
  btn.disabled = true; setCmpMsg(t("renaming"), "progress");
  post("/api/branch-rename", {old: oldName, new: newName})
    .then(r => { setCmpMsg(r.message, true); return refreshGraph().catch(() => {}); })
    .catch(e => setCmpMsg(t("rename_failed", {error: e.message}), false))
    .finally(() => { btn.disabled = false; });
}
async function doReset() {
  if (!actionHash) return;
  const h = actionHash, btn = document.getElementById("resetBtn");
  const options = await openActionDialog(t("reset"),
    '<label>' + esc(t("reset_mode")) + '<select name="mode">' +
    '<option value="mixed" selected>' + esc(t("reset_opt_mixed")) + '</option>' +
    '<option value="soft">' + esc(t("reset_opt_soft")) + '</option>' +
    '<option value="hard">' + esc(t("reset_opt_hard")) + '</option>' +
    '</select></label>', t("reset_submit"));
  if (!options) return;
  const mode = options.mode;
  if (!confirm(t({hard: "confirm_reset_hard", soft: "confirm_reset_soft", mixed: "confirm_reset_mixed"}[mode], {hash: h.slice(0, 7)}))) return;
  btn.disabled = true;
  setGraphStatus(t("resetting"), "progress");
  try {
    const r = await post("/api/reset", {hash: h, mode: mode});
    clearCmp();
    setGraphStatus(r.message, "success");
    await refreshGraph().catch(() => {});
  } catch (e) {
    setGraphStatus(t("reset_error", {error: e.message}), "error");
  } finally {
    btn.disabled = false;
  }
}
async function doRebaseI() {  // 対話的リベース (git rebase -i 相当)
  if (!actionHash) return;
  const h = actionHash, btn = document.getElementById("irebaseBtn");
  btn.disabled = true;
  try {
    const hd = await fetch("/api/head").then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }));
    if (hd.merging) throw new Error(t("op_in_progress_finish"));
    setGraphStatus(t("loading_rebase_commits"), "progress");
    const info = await fetch("/api/rebase-todo?hash=" + encodeURIComponent(h))
      .then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }));
    setGraphStatus("");
    const msgs = {};
    const opt = (v, label) => '<option value="' + v + '">' + label + "</option>";
    const rows = info.commits.map(c => {
      msgs[c.hash] = c.message;
      return '<div class="ri-row pick" data-h="' + c.hash + '"><div class="ri-main">' +
        '<select name="act_' + c.hash + '">' + opt("pick", "pick") + opt("reword", "reword") + opt("squash", "squash") +
        opt("fixup", "fixup") + opt("drop", "drop") + "</select>" +
        "<code>" + esc(c.short) + '</code><span class="ri-subj" title="' + esc(c.subject) + '">' + esc(c.subject) + "</span>" +
        '<span class="ri-author">' + esc(c.author) + " " + esc(c.date) + "</span>" +
        '<button type="button" data-ri="up" title="' + esc(t("ri_up")) + '">▲</button>' +
        '<button type="button" data-ri="down" title="' + esc(t("ri_down")) + '">▼</button></div>' +
        '<textarea name="msg_' + c.hash + '" rows="3" hidden>' + esc(c.message) + "</textarea></div>";
    }).join("");
    const fields = '<div class="ri-note">' + t("ri_note", {branch: esc(info.branch), hash: esc(h.slice(0, 7))}) + '</div>' +
      '<div class="ri-list">' + rows + "</div>";
    const fieldsEl = document.getElementById("actionFields");
    const validate = values => {
      const acts = Object.keys(values).filter(k => k.startsWith("act_"));
      const kept = acts.filter(k => values[k] !== "drop");
      if (kept.length && ["squash", "fixup"].includes(values[kept[0]]))
        return t("ri_first_squash");
      const bad = acts.find(k => values[k] === "reword" && !(values["msg_" + k.slice(4)] || "").trim());
      if (bad) return t("ri_reword_required");
      return null;
    };
    const p = openActionDialog(t("interactive_rebase"), fields, t("run_rebase"), {wide: true, validate: validate});
    fieldsEl.onclick = ev => {  // 並べ替え
      const b = ev.target.closest("[data-ri]");
      if (!b) return;
      const row = b.closest(".ri-row");
      if (b.dataset.ri === "up" && row.previousElementSibling) row.parentNode.insertBefore(row, row.previousElementSibling);
      if (b.dataset.ri === "down" && row.nextElementSibling) row.parentNode.insertBefore(row.nextElementSibling, row);
    };
    fieldsEl.onchange = ev => {  // 操作の種類に合わせて表示を切り替える
      const sel = ev.target.closest("select");
      if (!sel) return;
      const row = sel.closest(".ri-row");
      row.className = "ri-row " + sel.value;
      row.querySelector("textarea").hidden = sel.value !== "reword";
    };
    const values = await p;
    fieldsEl.onclick = null; fieldsEl.onchange = null;
    if (!values) return;
    const todo = Object.keys(values).filter(k => k.startsWith("act_"))
      .map(k => ({hash: k.slice(4), action: values[k], message: values["msg_" + k.slice(4)] || ""}));
    const n = a => todo.filter(t => t.action === a).length;
    const note = [n("reword") && t("ri_part_reword", {n: n("reword")}), n("squash") && t("ri_part_squash", {n: n("squash")}),
                  n("fixup") && t("ri_part_fixup", {n: n("fixup")}), n("drop") && t("ri_part_drop", {n: n("drop")})].filter(Boolean).join(" / ");
    if (!confirm(t("confirm_rebase_i", {branch: info.branch, count: todo.length, hash: h.slice(0, 7), note: note ? " (" + note + ")" : ""}))) return;
    setGraphStatus(t("rebasing"), "progress");
    const r = await post("/api/rebase-i", {hash: h, todo: todo});
    clearCmp();
    setGraphStatus(r.message, "success");
    await refreshGraph().catch(() => {});
  } catch (e) {
    setGraphStatus(t("rebase_i_error", {error: e.message}), "error");
  } finally {
    btn.disabled = false;
  }
}
async function doMerge() {
  if (!actionHash) return;
  const options = await openActionDialog(t("merge"), '<label class="check" title="' + esc(t("tip_no_ff")) + '">' +
    '<input type="checkbox" name="noFf"> --no-ff</label>', t("merge"));
  if (!options) return;
  const h = actionHash, noFf = options.noFf;
  const btn = document.getElementById("mergeBtn");
  btn.disabled = true;
  fetch("/api/head").then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }))
    .then(hd => {
      if (hd.merging) throw new Error(t("merge_in_progress_resolve"));
      const into = hd.branch ? t("what_branch", {name: hd.branch}) : "detached HEAD";
      if (!confirm(t("confirm_merge", {hash: h.slice(0, 7), into: into, noff: noFf ? " (--no-ff)" : ""}))) return null;
      setGraphStatus(t("merging"), "progress");
      return post("/api/merge", {hash: h, no_ff: noFf});
    })
    .then(r => {
      if (r === null) { setGraphStatus(""); return; }
      clearCmp();
      setGraphStatus(r.message, r.conflict ? "error" : "success");
      return refreshGraph().catch(() => {}).then(() => {
        if (r.conflict) showWip();
      });
    })
    .catch(e => { setGraphStatus(t("merge_error", {error: e.message}), "error"); })
    .finally(() => { btn.disabled = false; });
}
function selectCmpA(hash) {
  compareAHash = hash;
  if (compareBHash === hash) compareBHash = null;
  updateCmpClasses();
  saveUiState();
}
function selectCompareHash(slot, hash) {
  if (slot === "A") selectCmpA(hash);
  else selectCmpB(hash);
}
function selectCmpB(hash) {
  compareBHash = hash;
  if (compareAHash === hash) compareAHash = null;
  updateCmpClasses();
  saveUiState();
}
function restoreScreen(state) {
  if (Object.prototype.hasOwnProperty.call(state, "compareA")) compareAHash = state.compareA;
  if (Object.prototype.hasOwnProperty.call(state, "compareB")) compareBHash = state.compareB;
  if (Object.prototype.hasOwnProperty.call(state, "actionHash")) actionHash = state.actionHash;
  cmpActOpen = false;  // メニューは画面の復元では開かない (グラフを隠さない)
  if (state.screen === "log") showLog(state.titleHtml, state.rev, state.topHtml, true, state.scroll || 0, state.src);
  else if (state.screen === "detail") showDetail(state.hash, true);
  else if (state.screen === "compare") {
    compareAHash = state.a; compareBHash = state.b;
    showCompare(state.a, state.b, true);
  }
  else if (state.screen === "compare-logs") {
    compareAHash = state.a; compareBHash = state.b;
    showCompareLogs(state.a, state.b, true, state.scroll || 0);
  }
  else if (state.screen === "wip") showWip(true);
  else {
    if (observer) { observer.disconnect(); observer = null; }
    ++token;
    listView.innerHTML = homeHtml;
    showView("list");
    setDocumentTitle("");
  }
  updateCmpClasses();
}
window.addEventListener("popstate", ev => restoreScreen(ev.state || {screen: "home"}));
function showFileLogInGraph(hash, path) {
  showLog(t("file_log_heading", {path: esc(path)}), hash,
          '<div class="meta"><code>git log ' + esc(hash.slice(0, 7)) + " -- " + esc(path) + "</code></div>",
          false, 0, {path: "/api/log", params: {rev: hash, path: path}});
}
window.addEventListener("message", ev => {
  if (ev.origin !== location.origin || !ev.data || ev.data.type !== "git-graph-file-log") return;
  const hash = ev.data.hash, path = ev.data.path;
  if (typeof hash !== "string" || typeof path !== "string" || !hash || !path) return;
  showFileLogInGraph(hash, path);
});

listView.addEventListener("click", ev => {
  if (ev.detail > 1) return;
  const tr = ev.target.closest("tr.clickable");
  if (!tr) return;
  const cmpButton = ev.target.closest("[data-cmp]");
  if (cmpButton) {
    ev.preventDefault();
    ev.stopPropagation();
    selectCompareHash(cmpButton.dataset.cmp, tr.dataset.h);
    return;
  }
  if (ev.target.closest(".copy")) return;
  selectActionTarget(tr.dataset.h);
  if (curRow) curRow.classList.remove("cur");
  curRow = tr; tr.classList.add("cur");
  showDetail(tr.dataset.h);
});
detailView.addEventListener("click", ev => {
  if (ev.target.closest("#wipReload")) { showWip(true); refreshGraph().catch(() => {}); return; }
  if (ev.target.closest("#wipCommit")) { doWipCommit(); return; }
  if (ev.target.closest("#wipDiscard")) { doWipDiscard(); return; }
  if (ev.target.closest("#wipAbort")) { doWipAbort(); return; }
  const rb = ev.target.closest("[data-resolve]");
  if (rb) { doWipResolve(rb.dataset.resolve); return; }
  const ab = ev.target.closest(".abtn");
  if (ab) { doAction(ab); return; }
  if (ev.target.closest(".sbtn")) {
    [compareAHash, compareBHash] = [compareBHash, compareAHash];
    updateCmpClasses();
    saveUiState();
    if (compareAHash && compareBHash) showCompare(compareAHash, compareBHash);
    return;
  }
  if (ev.target.closest(".back-btn")) { history.back(); return; }
  const tr = ev.target.closest("tr.frow");
  if (tr && !ev.target.closest("a, input, td.fk") && curCtx)  // 行のどこをクリックしても差分を開く
    location.href = diffUrl(curCtx, curFiles[+tr.dataset.k]);
});
// 中クリック/Ctrl・Cmdクリックは、ブラウザの opener 設定に関係なく
// ファイル画面を別タブで開くことを明示する。
function openFilesInNewTab(ev) {
  const link = ev.target.closest("a.detail-files");
  if (!link) return;
  const newTabClick = ev.type === "auxclick" ? ev.button === 1 : (ev.ctrlKey || ev.metaKey);
  if (!newTabClick) return;
  ev.preventDefault();
  const url = new URL(link.href, location.href);
  url.searchParams.set("file_view", "1");
  window.open(url.href, "_blank", "noopener,noreferrer");
}
document.addEventListener("auxclick", openFilesInNewTab);
document.addEventListener("click", openFilesInNewTab);
document.getElementById("compareToolbar").addEventListener("click", ev => {  // メニューバー (操作 / push / pull / fetch / その他)
  if (ev.target.closest("#abActToggle")) {
    abActOpen = !abActOpen;
    if (abActOpen) { cmpActOpen = false; setOtherMenu(false); }
    updateCmpClasses();
    saveUiState();
    return;
  }
  if (ev.target.closest("#otherMenuBtn")) {
    const open = document.getElementById("otherActions").hidden;
    if (open && (cmpActOpen || abActOpen)) { cmpActOpen = false; abActOpen = false; updateCmpClasses(); }  // 同時に開くのは1つだけ
    setOtherMenu(open);
    return;
  }
  if (ev.target.closest("#compareBtn") && compareAHash && compareBHash) {
    showCompare(compareAHash, compareBHash);
    return;
  }
  if (ev.target.closest("#compareLogsBtn") && compareAHash && compareBHash) {
    showCompareLogs(compareAHash, compareBHash);
    return;
  }
  if (ev.target.closest("#rebaseRangeBtn")) { doRebaseRange(); return; }
  if (ev.target.closest("#stashListBtn")) { showStashList(); return; }
  if (ev.target.closest("#stashApplyBtn")) { doStash("apply"); return; }
  if (ev.target.closest("#stashPopBtn")) { doStash("pop"); return; }
  if (ev.target.closest("#stashDropBtn")) { doStash("drop"); return; }
  if (ev.target.closest("#reflogBtn")) { showReflog(""); return; }
  if (ev.target.closest("#deleteBranchBtn")) { doDeleteBranch(ev.target.closest("#deleteBranchBtn")); return; }
  if (ev.target.closest("#deleteRemoteBranchBtn")) { doDeleteRemoteBranch(ev.target.closest("#deleteRemoteBranchBtn")); return; }
  if (ev.target.closest("#deleteTagBtn")) { doDeleteTag(ev.target.closest("#deleteTagBtn")); return; }
  if (ev.target.closest("#mergeBtn")) { doMerge(); return; }
  if (ev.target.closest("#rebaseBtn")) { doRebase(); return; }
  if (ev.target.closest("#irebaseBtn")) { doRebaseI(); return; }
  if (ev.target.closest("#cherryBtn")) { doCherryPick(); return; }
  if (ev.target.closest("#revertBtn")) { doRevert(); return; }
  if (ev.target.closest("#renameBranchBtn")) { doRenameBranch(ev.target.closest("#renameBranchBtn")); return; }
  if (ev.target.closest("#resetBtn")) { doReset(); return; }
  const cab = ev.target.closest(".abtn[data-cact]");
  if (cab) {
    const act = cab.dataset.cact;
    if (act === "delete-branch") doDeleteBranch(cab);
    else if (act === "delete-tag") doDeleteTag(cab);
    else (["fetch", "pull", "push", "push-tag"].includes(act) ? doRemoteAction : act.startsWith("remote-") ? doRemoteConfig : doCmpAction)(cab);
    return;
  }
});
document.getElementById("compareToolbar").addEventListener("click", ev => {
  if (ev.target.closest("#cmpActToggle")) {
    cmpActOpen = !cmpActOpen;
    if (cmpActOpen) { abActOpen = false; setOtherMenu(false); }  // 同時に開くのは1つだけ
    updateCmpClasses();
    saveUiState();
  } else if (ev.target.closest("#clearCmpBtn")) {
    clearCmp();
  }
});
document.getElementById("compareToolbar").addEventListener("click", ev => {  // メニューの項目を選んだら閉じる
  if (ev.target.closest(".dropdown .abtn")) closeMenus();  // 無効なボタンはクリック自体が発火しない
});
document.addEventListener("click", ev => {  // メニューの外をクリックしたら閉じる
  if (ev.target.closest(".menu-wrap") || ev.target.closest("svg g.node")) return;  // ノードのクリックは onNodeClick が扱う
  closeMenus();
});
document.addEventListener("keydown", ev => { if (ev.key === "Escape") closeMenus(); });
function select(g) {
  if (selected) selected.classList.remove("sel");
  selected = g; g.classList.add("sel");
  const t = g.querySelector("title");
  selectedKey = t ? t.textContent : null;  // グラフを作り直したあとで選択を復元するため
}

function onNodeClick(g, n, ev) {
  if (ev && (ev.ctrlKey || ev.metaKey) && actionHash && actionHash !== n.hash && !n.wip) {
    compareAHash = actionHash;
    compareBHash = n.hash;
    cmpActOpen = true; abActOpen = false; setOtherMenu(false);
    select(g);
    updateCmpClasses();
    saveUiState();
    return;
  }
  if (!n.wip) { cmpActOpen = true; abActOpen = false; setOtherMenu(false); }  // コミットを選んだら、操作メニューを自動で開く
  if (n.wip) {  // 作業ツリーの変更 (WIP) ノード: ログではなく変更ファイルの一覧を出す
    clearCmp(); select(g); showWip();
    return;
  }
  selectActionTarget(n.hash);
  select(g);
  showLog("git log --oneline " + esc(n.hash.slice(0, 7)), n.hash,
          chips(n.hash) + '<div class="meta">' + t("all_log_to_root") + '</div>');
}

function init() {
  document.querySelectorAll("svg g.node").forEach(g => {
    const t = g.querySelector("title");
    const n = t && DATA.nodes[t.textContent];
    if (n) {
      nodeEl[n.hash] = g;
      const box = g.getBBox(), hit = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      hit.setAttribute("x", box.x); hit.setAttribute("y", box.y);
      hit.setAttribute("width", box.width); hit.setAttribute("height", box.height);
      hit.setAttribute("fill", "transparent"); hit.setAttribute("pointer-events", "all");
      g.insertBefore(hit, g.children[1] || null);
      g.addEventListener("click", ev => { if (ev.detail <= 1) onNodeClick(g, n, ev); });
    }
  });
  document.querySelectorAll("svg g.edge").forEach(g => {
    const t = g.querySelector("title");
    const e = t && DATA.edges[t.textContent];
    if (!e) return;
    const path = g.querySelector("path");
    if (path) { const hit = path.cloneNode(); hit.setAttribute("class", "hit"); g.appendChild(hit); }
    g.addEventListener("click", () => {
      clearCmp(); select(g);
      const range = e.from.slice(0, 7) + ".." + e.to.slice(0, 7);
      showLog(esc(e.from.slice(0, 7)) + " → " + esc(e.to.slice(0, 7)), e.from + ".." + e.to,
              '<div class="meta"><code>git log ' + esc(range) + "</code></div>");
    });
  });
}

// ---- 作業ツリーの変更 (WIP) ----
// チェックしたファイルだけを add してコミットする (TortoiseGit のコミットダイアログと同じ考え方)。
// 既定ではステージ済みのファイルがチェック済み。チェックを外したステージ済みファイルはステージから外れる。
let wipSel = {}, wipStaged = new Set(), wipMerging = false, wipMsg = "";  // wipSel: パス → ユーザーが付け外ししたチェック状態
// amend モード: チェック中は「直前のコミットの親」と作業ツリーの差分を出し、メッセージも直前のものを入れる
let wipAmendOn = false, wipMsgNormal = "", wipHeadMsg = "";  // wipMsgNormal: amend にする前に書いていたメッセージ (解除で戻す)
function wipIsChecked(f) {  // amend: 直前のコミットのファイルは、外さない限りコミットに残る (チェック済みが既定)
  return f.path in wipSel ? wipSel[f.path] : (wipMerging || !!f.in_head || wipStaged.has(f.path));
}
function wipCheckCell(f) {
  return '<td class="fk"><input type="checkbox" class="wipck" data-kind="' + esc(f.wip) + '" data-path="' + esc(f.path) + '"' +
    (wipIsChecked(f) ? " checked" : "") + (f.in_head ? ' title="' + t("wip_file_remove_title") + '"' : "") + "></td>";
}
function wipCks(kind) {  // 操作できる (disabled でない) チェックボックス
  return [...detailView.querySelectorAll(".wipck" + (kind ? '[data-kind="' + kind + '"]' : ""))].filter(x => !x.disabled);
}
function wipPaths() {  // チェックされたパス (同じファイルが ステージ済み と 未ステージ の両方に出ても1件)
  const s = new Set();
  wipCks().forEach(el => { if (el.checked) s.add(el.dataset.path); });
  return [...s];
}
function setWipPath(p, v) {  // 同じパスのチェックボックスをまとめて切り替える
  wipSel[p] = v;
  wipCks().forEach(x => { if (x.dataset.path === p) x.checked = v; });
}
function wipSync() {  // コミットボタンの件数と、グループの全選択チェックを更新
  const n = wipPaths().length, b = detailView.querySelector("#wipCommit");
  const dis = detailView.querySelector("#wipDiscard");
  if (dis) { dis.disabled = n === 0; dis.textContent = t("wip_discard") + " (" + n + ")"; }
  if (b) {
    b.disabled = n === 0;
    b.textContent = t("wip_commit") + " (" + n + ")" + (wipAmendOn ? " amend" : "");
  }
  detailView.querySelectorAll(".wipall").forEach(all => {
    const cks = wipCks(all.dataset.kind), c = cks.filter(x => x.checked).length;
    all.disabled = cks.length === 0;
    all.checked = cks.length > 0 && c === cks.length;
    all.indeterminate = c > 0 && c < cks.length;
  });
}
function wipSay(text, ok) {
  const el = detailView.querySelector("#wipMsgOut");
  if (el) { el.textContent = text; el.className = "amsg " + (ok ? "ok" : "ng"); }
  else setGraphStatus(text, ok ? "success" : "error");
}
function setWipAmend(on) {  // amend のチェックを切り替える: 差分の一覧とメッセージを入れ替えて再表示
  if (on === wipAmendOn) return;
  wipSel = {};  // 一覧が変わるので、付け外ししたチェックはリセット
  if (on) { wipMsgNormal = wipMsg; wipMsg = wipHeadMsg; }  // 直前のコミットのメッセージを入れておく
  else { wipMsg = wipMsgNormal; wipMsgNormal = ""; }
  wipAmendOn = on;
  showWip(true);
}
function doWipCommit() {
  const amend = wipAmendOn, paths = wipPaths(), ta = detailView.querySelector("#wipMsg"), msg = ta ? ta.value : "";
  if (!msg.trim()) { wipSay(t("message_required"), false); return; }
  if (!amend && !paths.length) { wipSay(t("select_files"), false); return; }
  if (amend) {
    const chk = new Set(paths), drops = curFiles.filter(f => f.in_head && !chk.has(f.path)).length;
    const dropText = drops ? "\n" + t("amend_drop", {n: drops}) : "";
    if (!confirm(t("amend_confirm", {drop: dropText}))) return;
  }
  const btn = detailView.querySelector("#wipCommit");
  if (btn) btn.disabled = true;
  wipSay(t("wip_execute"), true);
  post("/api/wip-commit", {paths: paths, message: msg, amend: amend})
    .then(r => {
      wipMsg = ""; wipMsgNormal = ""; wipSel = {}; wipAmendOn = false;
      showWip(true, r.message);
      refreshGraph().catch(() => {});
    })
    .catch(e => { wipSay(e.message, false); wipSync(); });
}
detailView.addEventListener("change", ev => {
  if (ev.target.id === "wipAmendCk") { setWipAmend(ev.target.checked); return; }
  const ck = ev.target.closest(".wipck"), all = ev.target.closest(".wipall");
  if (ck) setWipPath(ck.dataset.path, ck.checked);
  else if (all) wipCks(all.dataset.kind).forEach(x => setWipPath(x.dataset.path, all.checked));
  else return;
  wipSync();
});
detailView.addEventListener("input", ev => { if (ev.target.id === "wipMsg") wipMsg = ev.target.value; });

function resolveBarHtml() {  // 競合ファイルを ours / theirs で解決するボタン (チェックしたファイルが対象)
  return '<div class="wipres"><button class="abtn" data-resolve="ours">' + esc(t("resolve_ours")) + '</button>' +
    '<button class="abtn" data-resolve="theirs">' + esc(t("resolve_theirs")) + '</button>' +
    '<span class="hint">' + esc(t("resolve_hint")) + '</span></div>';
}
function opBannerHtml(cmd, canWrite) {  // マージなどの途中であることと、中断ボタン
  if (!cmd) return "";
  return '<div class="wipop"><span>' + esc(t("op_banner", {cmd: cmd})) + '</span>' +
    (canWrite ? '<button class="abtn danger" id="wipAbort" data-cmd="' + esc(cmd) + '">' + esc(t("abort_button", {cmd: cmd})) + '</button>' : "") + '</div>';
}
function doWipResolve(side) {
  const paths = wipCks("conflict").filter(x => x.checked).map(x => x.dataset.path);
  if (!paths.length) { wipSay(t("select_files"), false); return; }
  if (!confirm(t("confirm_resolve", {n: paths.length, side: side}))) return;
  wipSay(t("wip_execute"), true);
  post("/api/resolve", {paths: paths, side: side})
    .then(r => { wipSel = {}; showWip(true, r.message); refreshGraph().catch(() => {}); })
    .catch(e => wipSay(e.message, false));
}
function doWipDiscard() {
  const paths = wipPaths();
  if (!paths.length) return;
  const confl = new Set(curFiles.filter(f => f.wip === "conflict").map(f => f.path)), hit = paths.filter(p => confl.has(p));
  if (hit.length) { wipSay(t("discard_conflict", {files: hit.slice(0, 3).join(", ")}), false); return; }
  if (!confirm(t("confirm_discard", {n: paths.length}))) return;
  const btn = detailView.querySelector("#wipDiscard");
  if (btn) btn.disabled = true;
  wipSay(t("wip_execute"), true);
  post("/api/discard", {paths: paths})
    .then(r => { wipSel = {}; showWip(true, r.message); refreshGraph().catch(() => {}); })
    .catch(e => { wipSay(e.message, false); wipSync(); });
}
function doWipAbort() {
  const b = detailView.querySelector("#wipAbort");
  if (!b) return;
  if (!confirm(t("confirm_abort", {cmd: b.dataset.cmd}))) return;
  b.disabled = true;
  post("/api/abort", {})
    .then(r => { wipSel = {}; wipMsg = ""; wipMsgNormal = ""; wipAmendOn = false; showWip(true, r.message); return refreshGraph().catch(() => {}); })
    .catch(e => { b.disabled = false; wipSay(e.message, false); });
}

function showWip(fromHistory, okMsg) {
  document.body.classList.remove("compare-logs-mode");
  if (!fromHistory) pushScreen({screen: "wip"});
  setDocumentTitle(t("wip_title"));
  const btns = '<button class="back-btn">' + t("back_history") + '</button><button class="sbtn" id="wipReload">' + t("reload") + '</button>';
  detailView.innerHTML = btns + '<p class="hint">' + t("wip_loading") + '</p>';
  showView("detail"); curDetailHash = null;
  api("/api/status", wipAmendOn ? {amend: 1} : {}).then(s => {
    wipStaged = new Set(s.staged.map(f => f.path)); wipMerging = !!s.merging; wipHeadMsg = s.head_message || "";
    const canAmend = !!(s.head && !s.merging && !s.rebasing);
    if (wipAmendOn && !s.amend_files) {  // マージ中になったなどで amend できなくなった: 通常のコミットに戻す
      wipMsg = wipMsgNormal; wipMsgNormal = ""; wipAmendOn = false;
    }
    const canCommit = !!DATA.writable;  // 書き込みが無効 (--read-only など) のときはチェック欄を出さない
    const opCmd = s.merging ? "merge" : s.rebasing ? "rebase" : s.cherry_picking ? "cherry-pick" : s.reverting ? "revert" : null;
    const sections = wipAmendOn
      ? [["amend", t("amend_content"), s.amend_files]]
      : [["conflict", t("conflict"), s.conflicts], ["staged", t("staged"), s.staged],
         ["unstaged", t("unstaged"), s.unstaged], ["untracked", t("untracked"), s.untracked]];
    let files = [], body = "";
    sections.forEach(([kind, title, items]) => {
      if (!items.length) return;
      const list = items.map(f => Object.assign({wip: kind}, f));
      body += "<h3>" + (canCommit ? '<input type="checkbox" class="wipall" data-kind="' + kind + '" title="' + t("select_all") + '">' : "") +
        title + " (" + list.length + ")</h3>" + (kind === "conflict" && canCommit ? resolveBarHtml() : "") + filesHtml({}, list, files.length, canCommit);
      files = files.concat(list);
    });
    curCtx = {}; curFiles = files;
    const hint = wipAmendOn
      ? t("amend_hint")
      : t("wip_hint");
    const box = canCommit && (files.length || wipAmendOn)
      ? '<div class="wipbox">' +
        '<textarea id="wipMsg" placeholder="' + t("wip_commit_message") + '"></textarea>' +
        (canAmend ? '<label class="amendrow"><input type="checkbox" id="wipAmendCk"' + (wipAmendOn ? " checked" : "") +
          '>' + t("amend_checkbox") + '</label>' : "") +
        '<div class="arow"><button class="wbtn" id="wipCommit">' + t("wip_commit") + '</button>' +
        (!wipAmendOn && !opCmd ? '<button class="wbtn danger" id="wipDiscard">' + t("wip_discard") + '</button>' : "") +
        '<span class="hint">' + hint + '</span></div>' +
        '<div class="amsg" id="wipMsgOut"></div></div>' : "";
    detailView.innerHTML = btns + "<h2>" + t("wip_title") + "</h2>" + opBannerHtml(opCmd, canCommit) +
      '<div class="meta">HEAD: ' + (s.head ? chips(s.head) + esc(s.head.slice(0, 7)) : t("no_commits_yet")) + "</div>" +
      (body || '<p class="hint">' + t("no_changes") + '</p>') + box +
      (s.truncated ? '<p class="hint">' + t("truncated_files") + '</p>' : "");
    const ta = detailView.querySelector("#wipMsg");
    if (ta) ta.value = wipMsg;
    if (okMsg) wipSay(okMsg, true);
    wipSync();
  }).catch(e => {
    detailView.innerHTML = btns + '<p class="hint">' + esc(t("error")) + esc(e.message) + "</p>";
  });
}

document.getElementById("reload").addEventListener("click", ev => {  // グラフを最新の状態に更新
  const b = ev.currentTarget;
  b.disabled = true; b.textContent = t("reload_busy");
  refreshGraph().catch(() => {}).finally(() => { b.disabled = false; b.textContent = t("reload"); });
});

// ---- グラフの読み込み (サーバー側の作成に時間がかかるので、読み込み中画面を出す) ----
const t0 = Date.now();
const timer = setInterval(() => {
  const el = document.getElementById("lsub");
  if (el) el.textContent = t("elapsed_hint", {sec: Math.round((Date.now() - t0) / 1000)});
}, 1000);
fetch("/api/graph")
  .then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); }))
  .then(r => {
    clearInterval(timer);
    DATA = {nodes: r.nodes, edges: r.edges, writable: r.writable, merge: r.merge};
    document.getElementById("graph").innerHTML = r.svg;
    applyGraphZoom();
    init();
    updateCmpClasses();
    document.getElementById("reload").disabled = false;
    loadStashes();
    updateCurrentBranch().catch(e => setGraphStatus(t("current_branch_failed", {error: e.message}), "error"));
    restoreScreen(history.state || {screen: "home"});
    const fileLogHash = new URLSearchParams(location.search).get("file_log_hash");
    const fileLogPath = new URLSearchParams(location.search).get("file_log_path");
    if (fileLogHash && fileLogPath) {
      // 一度だけ消費し、以後の履歴には一時的なクエリを残さない。
      history.replaceState({screen: "home"}, "", "/");
      showFileLogInGraph(fileLogHash, fileLogPath);
    }
  })
  .catch(e => {
    clearInterval(timer);
    document.getElementById("graph").innerHTML = '<div id="loading"><div>' + esc(t("graph_build_failed")) + '</div><pre>' +
      esc(e.message) + '</pre><button class="sbtn" onclick="location.reload()">' + esc(t("reload_page")) + '</button></div>';
  });
</script>
</body>
</html>
"""

HTML_TEMPLATE = HTML_TEMPLATE.replace("/*__COPY_JS__*/", COPY_JS)

_cache = {"base_key": None, "base": None, "payload_key": None, "payload": None}
_lock = threading.Lock()

CONFLICT_CODES = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}


def read_status_raw():
    return git("status", "--porcelain=v1", "-z", "--untracked-files=all")


def parse_status(raw, limit=5000):
    """git status --porcelain=v1 -z を ステージ済み / 未ステージ / 未追跡 / 競合 に分ける (パスはリポジトリルート基準)。"""
    res = {"staged": [], "unstaged": [], "untracked": [], "conflicts": [], "truncated": False}
    toks = raw.split("\0")
    i = n = 0
    while i < len(toks):
        t = toks[i]
        i += 1
        if len(t) < 4 or t[2] != " ":
            continue
        xy, path = t[:2], t[3:]
        old = None
        if "R" in xy or "C" in xy:  # リネーム/コピーは続くトークンが元のパス
            old = toks[i] if i < len(toks) else None
            i += 1
        if xy == "!!":
            continue
        n += 1
        if n > limit:
            res["truncated"] = True
            break
        if xy == "??":
            res["untracked"].append({"path": path, "st": "?", "label": "new_file"})  # label は翻訳キー (画面側で翻訳する)
        elif xy in CONFLICT_CODES:
            res["conflicts"].append({"path": path, "st": "U", "label": "label_conflict"})
        else:
            x, y = xy[0], xy[1]
            if x != " ":
                res["staged"].append({"path": path, "old_path": old if x in "RC" else None, "st": x})
            if y != " ":
                res["unstaged"].append({"path": path, "old_path": None, "st": y})
    return res


def wip_count(status):
    return len({f["path"] for k in ("staged", "unstaged", "untracked", "conflicts") for f in status[k]})


def amend_base():
    """amend 後のコミットの親 (HEAD^)。最初のコミットなら空ツリー。"""
    try:
        return git("rev-parse", "--verify", "--quiet", "HEAD^").strip()
    except subprocess.CalledProcessError:
        return git("hash-object", "-t", "tree", "/dev/null").strip()


def parse_name_status(raw):
    """--name-status -z の出力を {新しいパス: (変更の種類(M/A/D/R...), 元のパス or None)} にする。"""
    toks = raw.split("\0")
    out, i = {}, 0
    while i < len(toks):
        t = toks[i].lstrip("\n")
        if not t:
            i += 1
            continue
        if t[0] in "RC":
            if i + 2 >= len(toks):
                break
            out[toks[i + 2]] = (t[0], toks[i + 1])
            i += 3
        else:
            if i + 1 >= len(toks):
                break
            out[toks[i + 1]] = (t[0], None)
            i += 2
    return out


def amend_head_changes(base):
    """直前のコミット (HEAD) が変更したファイル {新しいパス: 元のパス or None}。"""
    names = parse_name_status(git("diff", "--name-status", "-z", "-M", base, "HEAD", "--"))
    return {path: old for path, (_, old) in names.items()}


def amend_files(s):
    """amend 後のコミットの内容 = 「直前のコミットの親」と作業ツリーの差分のファイル一覧。
    直前のコミットで変更したファイルは in_head=True (チェックを外すとコミットから外れる)。WIP の変更があるものは tag=WIP。"""
    base = amend_base()
    letters = parse_name_status(git("diff", "--name-status", "-z", "-M", base, "--"))
    in_head = amend_head_changes(base)
    pending = {f["path"] for k in ("staged", "unstaged") for f in s[k]}
    out = []
    for f in parse_numstat(git("diff", "--numstat", "-z", "-M", base, "--")):
        f["st"] = letters.get(f["path"], ("M", None))[0]
        f["wip"] = "amend"
        if f["path"] in in_head:
            f["in_head"] = True
        if f["path"] in pending:
            f["tag"] = "WIP"
        out.append(f)
    for f in s["untracked"]:
        out.append(dict(f, wip="amend", tag="WIP"))
    return out


def api_status(q):
    """作業ツリーの変更 (WIP)。ステージ済み/未ステージには追加・削除行数も付ける。"""
    if not WIP_ENABLED:
        raise UserError("wip_disabled")
    s = parse_status(read_status_raw())
    for key, extra in (("staged", ["--cached"]), ("unstaged", [])):
        if not s[key]:
            continue
        stats = {f["path"]: f for f in parse_numstat(git("diff", *extra, "--numstat", "-z", "-M", "--"))}
        for f in s[key]:
            st = stats.get(f["path"])
            if st:
                f["added"], f["deleted"] = st["added"], st["deleted"]
    try:
        s["head"] = git("rev-parse", "--verify", "--quiet", "HEAD").strip()
    except subprocess.CalledProcessError:
        s["head"] = ""
    s["merging"] = is_merging()
    s["rebasing"] = is_rebasing()
    s["cherry_picking"] = is_cherry_picking()
    s["reverting"] = is_reverting()
    if s["head"]:
        s["head_message"] = commit_info(s["head"])["message"]  # amend のときにメッセージの初期値にする
        if q.get("amend", [""])[0] == "1" and not s["merging"] and not s["rebasing"]:
            s["amend_files"] = amend_files(s)
    return s


def wip_diff(kind, path, specs):
    if not HAS_WORKTREE:
        raise UserError("no_worktree")
    if kind == "staged":
        return git_bytes("diff", "--cached", "--no-color", "-M", "--", *specs, literal=True)
    if kind in ("unstaged", "conflict"):
        return git_bytes("diff", "--no-color", "--", path, literal=True)
    if kind == "untracked":
        # 任意のファイルを読ませないよう、いま未追跡と報告されているパスだけを対象にする
        known = {f["path"] for f in parse_status(read_status_raw())["untracked"]}
        if path not in known:
            raise UserError("not_untracked")
        full = os.path.join(REPO, path)
        if os.path.islink(full):
            return f"symlink -> {os.readlink(full)}\n".encode()
        r = subprocess.run(["git", "-C", REPO, "diff", "--no-index", "--no-color", "--", "/dev/null", path],
                           capture_output=True)
        if r.returncode not in (0, 1):  # 1 は「差分あり」
            raise ValueError(r.stderr.decode("utf-8", errors="replace").strip() or tr("git_diff_failed"))
        return r.stdout
    if kind == "amend":  # 直前のコミットの親 と 作業ツリー の差分 (未追跡ファイルは新規ファイルとして)
        if path in {f["path"] for f in parse_status(read_status_raw())["untracked"]}:
            return wip_diff("untracked", path, specs)
        return git_bytes("diff", "--no-color", "-M", amend_base(), "--", *specs, literal=True)
    raise ValueError("bad wip")


def add_wip(dot_text, nodes, head, count):
    """HEAD の子として、破線の WIP ノードを dot とノード情報に追加する。"""
    label = ('<<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="3" STYLE="dashed" BGCOLOR="#fff8dc">'
             f'<TR><TD><B>WIP</B></TD></TR><TR><TD>{html.escape(tr("n_changes", n=count))}</TD></TR></TABLE>>')
    lines = f'  "WIP" [label={label}];\n  "{head}" -> "WIP" [style=dashed];\n'
    i = dot_text.rindex("}")
    nodes = dict(nodes)
    nodes["WIP"] = {"hash": "WIP", "refs": [], "wip": True, "count": count}
    return dot_text[:i] + lines + dot_text[i:], nodes


def graph_payload():
    """グラフ(SVG + ノード/エッジ情報)の JSON を返す。
    重い部分 (merge-base の総当たり) は ref と HEAD が変わらない限りキャッシュし、
    作業ツリーの変更 (WIP ノード) だけを毎回付け替える。"""
    with _lock:  # 同時に複数回作らない (2つ目以降はキャッシュを使う)
        try:
            head = git("rev-parse", "HEAD").strip()
        except subprocess.CalledProcessError:
            head = ""
        # 同じコミットを指すブランチ間で checkout すると、HEAD と refs は
        # 変わらないため、現在ブランチを含めないと current (★) の表示が
        # キャッシュされたままになる。
        try:
            current_branch = git("symbolic-ref", "--short", "-q", "HEAD").strip()
        except subprocess.CalledProcessError:
            current_branch = ""
        key = git("for-each-ref") + "\0" + head + "\0" + current_branch
        if _cache["base_key"] != key:
            _cache.update(base_key=key, base=build_graph(OPTS), payload_key=None, payload=None)
        raw = ""
        if WIP_ENABLED:
            try:
                raw = read_status_raw()
            except subprocess.CalledProcessError:
                raw = ""
        if _cache["payload_key"] == (key, raw):
            return _cache["payload"]

        dot_text, nodes, edges = _cache["base"]
        if raw and head in nodes:
            count = wip_count(parse_status(raw))
            if count:
                dot_text, nodes = add_wip(dot_text, nodes, head, count)
        # Windows では text=True がロケール(cp932等)で符号化し、★ などが壊れて
        # dot が "not well-formed" になるため、UTF-8 のバイト列で入出力する
        svg = subprocess.run(["dot", "-Tsvg"], input=dot_text.encode("utf-8"),
                             capture_output=True, check=True).stdout.decode("utf-8", errors="replace")
        svg = svg[svg.index("<svg"):]
        payload = json.dumps({"svg": svg, "nodes": nodes, "edges": edges, "writable": WRITABLE,
                              "merge": WRITABLE and HAS_WORKTREE},
                             ensure_ascii=False)
        _cache.update(payload_key=(key, raw), payload=payload)
        return payload


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
REV_RE = re.compile(r"[0-9a-f]{40}(\.\.[0-9a-f]{40})?")
HASH_RE = re.compile(r"[0-9a-f]{7,40}")


def api_log(q):
    rev = q.get("rev", [""])[0]
    if not REV_RE.fullmatch(rev):
        raise ValueError("bad rev")
    path = q.get("path", [""])[0]
    if "\0" in path:
        raise ValueError("bad path")
    skip, limit = paging(q)
    args = ["log", "--date=short", f"--skip={skip}", "-n", str(limit),
            "--format=%H%x1f%h%x1f%an%x1f%ad%x1f%s", rev, "--"]
    if path:
        args.append(path)
    out = git(*args, literal=bool(path))
    commits = [l.split("\x1f") for l in out.splitlines() if l]
    res = {"commits": commits, "has_more": len(commits) == limit}
    if skip == 0:
        count_args = ["rev-list", "--count", rev, "--"]
        if path:
            count_args.append(path)
        res["total"] = int(git(*count_args, literal=bool(path)).strip())
    return res


def paging(q):
    skip = int(q.get("skip", ["0"])[0])
    limit = min(int(q.get("limit", ["500"])[0]), 2000)
    if skip < 0 or limit < 1:
        raise ValueError("bad paging")
    return skip, limit


def api_stash_list(q):
    """stash の一覧 (新しい順)。api_log と同じ [hash, short, 選択子, 日時, 件名] の形で返す。"""
    try:
        out = git("stash", "list", "--format=%H%x1f%h%x1f%gd%x1f%ci%x1f%gs")
    except subprocess.CalledProcessError:
        out = ""
    commits = []
    for l in out.splitlines():
        h, short, sel, date, subj = (l.split("\x1f") + [""] * 5)[:5]
        commits.append([h, short, sel, date[:16], subj])
    return {"commits": commits, "has_more": False, "total": len(commits)}


def api_reflog(q):
    """reflog (新しい順)。branch 省略で HEAD、指定でそのローカルブランチ。"""
    branch = q.get("branch", [""])[0]
    if branch:
        check_ref_name("branch", branch)
        ref, label = "refs/heads/" + branch, branch
        if subprocess.run(["git", "-C", REPO, "show-ref", "--verify", "--quiet", ref]).returncode != 0:
            raise UserError("branch_not_found")
    else:
        ref, label = "HEAD", "HEAD"
    skip, limit = paging(q)
    try:  # --date=iso にすると %gd が "名前@{日時}" になる (リフログの時刻を出したい)。番号は並び順から振る
        out = git("reflog", "show", "--date=iso", f"--skip={skip}", "-n", str(limit),
                  "--format=%H%x1f%h%x1f%gd%x1f%gs", ref, "--")
    except subprocess.CalledProcessError:  # コミットがまだない / reflog がない
        out = ""
    commits = []
    for i, l in enumerate(x for x in out.splitlines() if x):
        h, short, gd, subj = (l.split("\x1f") + [""] * 4)[:4]
        when = gd[gd.find("@{") + 2:-1][:16] if "@{" in gd else ""
        commits.append([h, short, f"{label}@{{{skip + i}}}", when, subj])
    res = {"commits": commits, "has_more": len(commits) == limit}
    if skip == 0:
        res["total"] = len(commits) if not res["has_more"] else sum(
            1 for _ in git("reflog", "show", "--format=%H", ref, "--").splitlines())
    return res


def api_stash(body):
    """stash の apply / pop / drop。stash の位置 (stash@{n}) は変わるので、コミットのハッシュから毎回引き直す。
    apply / pop で競合したときは、git の通常の動作どおり作業ツリーに競合を残す (pop は stash も残る)。"""
    op = body.get("op")
    if op not in ("apply", "pop", "drop"):
        raise ValueError("bad op")
    h = resolve_commit(body.get("hash", ""))
    sel = None
    for l in git("stash", "list", "--format=%H %gd").splitlines():
        sh, _, name = l.partition(" ")
        if sh == h:
            sel = name
            break
    if sel is None:
        raise UserError("stash_not_found")
    if op == "drop":
        run_git_write("stash", "drop", sel)
        return {"ok": True, "message": tr("stash_dropped", sel=sel, hash=h[:7])}
    if not HAS_WORKTREE:
        raise UserError("stash_no_worktree")
    if is_merging() or is_rebasing() or is_cherry_picking() or is_reverting():
        raise UserError("op_in_progress")
    r = subprocess.run(["git", "-C", REPO, "stash", op, sel], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        unmerged = subprocess.run(["git", "-C", REPO, "-c", "core.quotepath=false", "diff", "--name-only", "--diff-filter=U"],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
        if unmerged:
            raise ValueError(tr("stash_conflict_pop" if op == "pop" else "stash_conflict", sel=sel) + "\n" + unmerged)
        raise ValueError((r.stderr or r.stdout).strip() or tr("stash_apply_failed"))
    return {"ok": True, "message": tr("stash_applied" if op == "apply" else "stash_popped", sel=sel)}


def commit_info(h):
    if not HASH_RE.fullmatch(h):
        raise ValueError("bad hash")
    o = git("show", "-s", "--date=iso", "--format=%H%x1f%an <%ae>%x1f%ad%x1f%P%x1f%B", h, "--")
    full, author, date, par, msg = (o.split("\x1f", 4) + [""] * 5)[:5]
    return {"hash": full, "author": author, "date": date, "parents": par.split(),
            "message": msg.strip()}


def api_commit(q):
    info = commit_info(q.get("hash", [""])[0])
    raw = git("show", "--numstat", "-z", "-M", "--first-parent", "--format=", info["hash"], "--")
    info["files"] = parse_numstat(raw)
    return info


FILE_CONTENT_LIMIT = 2_000_000


def validate_file_path(path, allow_empty=True):
    """リポジトリルートからの相対パスとして安全な形にする。"""
    if "\0" in path or (not allow_empty and not path) or path.startswith("/"):
        raise ValueError("bad path")
    parts = path.split("/") if path else []
    if any(p in ("", ".", "..") for p in parts):
        raise ValueError("bad path")
    return path


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


def api_file(q):
    h = resolve_commit(q.get("hash", [""])[0])
    path = validate_file_path(q.get("path", [""])[0], allow_empty=False)
    spec = h + ":" + path
    kind = git("cat-file", "-t", spec).strip()
    if kind != "blob":
        raise UserError("not_a_file")
    raw = subprocess.check_output(["git", "-C", REPO, "cat-file", "blob", spec])
    truncated = len(raw) > FILE_CONTENT_LIMIT
    data = raw[:FILE_CONTENT_LIMIT]
    text, encoding = decode_text(data, truncated)
    return {"hash": h, "path": path, "content": text or "", "encoding": encoding,
            "binary": text is None, "truncated": truncated, "size": len(raw)}


def api_file_copy(body):
    """コミット内のファイルを、作業ツリーの別のパスにコピーする。既存のファイルは上書きしない。"""
    if not HAS_WORKTREE:
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
    root = os.path.realpath(REPO)
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
    r = subprocess.run(["git", "-C", REPO, "cat-file", "--filters", spec], capture_output=True)
    if r.returncode != 0:
        r = subprocess.run(["git", "-C", REPO, "cat-file", "blob", spec], capture_output=True)
        if r.returncode != 0:
            raise ValueError(r.stderr.decode("utf-8", errors="replace").strip() or tr("git_command_failed"))
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "xb") as f:
            f.write(r.stdout)
    except OSError as e:
        raise ValueError(str(e))
    return {"ok": True, "message": tr("file_copied", path=dest)}


def api_compare(q):
    """2つのコミット間の差分 (from → to) のファイル一覧。"""
    a = commit_info(q.get("from", [""])[0])
    b = commit_info(q.get("to", [""])[0])
    raw = git("diff", "--numstat", "-z", "-M", a["hash"], b["hash"], "--")
    return {"from": a, "to": b, "files": parse_numstat(raw)}


def api_merge_base(q):
    """2つのコミットの共通祖先 (最も新しいもの)。"""
    a = resolve_commit(q.get("from", [""])[0])
    b = resolve_commit(q.get("to", [""])[0])
    try:
        base = git("merge-base", a, b).strip()
    except subprocess.CalledProcessError:
        base = ""
    return {"hash": base}


def parse_numstat(raw):
    """--numstat -z の出力を [{path, old_path, added, deleted}] にする (バイナリは None)。"""
    toks = raw.split("\0")
    files, i = [], 0
    while i < len(toks):
        t = toks[i].lstrip("\n")
        if not t:
            i += 1
            continue
        a, d, path = (t.split("\t", 2) + ["", ""])[:3]
        old = None
        if path == "":  # リネーム: 続く2トークンが 旧パス, 新パス
            old, path = toks[i + 1], toks[i + 2]
            i += 3
        else:
            i += 1
        files.append({"path": path, "old_path": old,
                      "added": None if a == "-" else int(a),
                      "deleted": None if d == "-" else int(d)})
    return files


DIFF_LIMIT = 400000


def api_diff(q):
    """1ファイルの差分。hash=... (1コミット) か from=...&to=... (2コミット間) を指定する。"""
    path = q.get("path", [""])[0]
    old = q.get("old", [""])[0]
    if not path or "\0" in path or "\0" in old:
        raise ValueError("bad path")
    specs = [old, path] if old else [path]
    wip = q.get("wip", [""])[0]
    if wip:  # 作業ツリーの変更
        out = wip_diff(wip, path, specs)
    elif "from" in q or "to" in q:
        a, b = q.get("from", [""])[0], q.get("to", [""])[0]
        if not (HASH_RE.fullmatch(a) and HASH_RE.fullmatch(b)):
            raise ValueError("bad hash")
        out = git_bytes("diff", "--no-color", "-M", a, b, "--", *specs, literal=True)
    else:
        h = q.get("hash", [""])[0]
        if not HASH_RE.fullmatch(h):
            raise ValueError("bad hash")
        out = git_bytes("show", "--format=", "--first-parent", "-M", "--no-color", h, "--", *specs, literal=True)
    truncated = len(out) > DIFF_LIMIT
    diff, encoding = decode_text(out[:DIFF_LIMIT], truncated=truncated)
    if diff is None:
        # バイナリ差分でも git の "Binary files ... differ" 等は表示できるようにする。
        diff, encoding = out[:DIFF_LIMIT].decode("utf-8", errors="replace"), "UTF-8 (binary diff)"
    return {"diff": diff, "encoding": encoding, "truncated": truncated}


DIFF_CSS = """
  html, body { height: 100%; margin: 0; font-family: system-ui, sans-serif; font-size: 13px; color: #222; }
  body { display: flex; }
  #side { width: 320px; flex: none; overflow: auto; border-right: 1px solid #ccc; background: #fafafa; padding: 10px; box-sizing: border-box; }
  #side a { display: flex; justify-content: space-between; gap: 8px; padding: 3px 6px; color: #06c; text-decoration: none;
            font-family: monospace; font-size: 12px; border-radius: 3px; word-break: break-all; }
  #side a:hover { background: #eef; }
  #side a.cur { background: #ffe9a0; color: #222; font-weight: bold; }
  #side .n { white-space: nowrap; }
  #side .grp { margin: 10px 0 2px; font-weight: bold; }
  #main { flex: 1; min-width: 0; overflow: auto; padding: 14px 18px; }
  h1 { font-size: 15px; margin: 0 0 6px; word-break: break-all; }
  .meta { color: #555; margin: 2px 0; }
  code { background: #eee; padding: 1px 4px; border-radius: 3px; user-select: all; }
  .plus { color: #1a7f37; } .minus { color: #cf222e; }
  .hint { color: #777; }
  .copy { cursor: pointer; font-size: 12px; margin-left: 6px; user-select: none; text-decoration: none; border-radius: 3px; padding: 0 2px; }
  .copy:hover { background: #e4e4e4; }
  .tag { display: inline-block; padding: 0 6px; border-radius: 3px; margin-right: 4px; font-weight: bold; color: #fff; }
  .tagA { background: #06c; } .tagB { background: #e67e00; }
  .diff { font-family: monospace; font-size: 12px; white-space: pre; margin-top: 12px; border: 1px solid #ddd; }
  .dl { padding: 0 6px; min-width: max-content; }
  .dl.ad { background: #e6ffec; } .dl.de { background: #ffebe9; }
  .dl.hk { background: #ddf4ff; color: #555; } .dl.hd { color: #777; background: #f6f8fa; }
"""


def diff_lines_html(text):
    """差分を行ごとに色分けする (ヘッダ部と本文を区別)。"""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    in_hunk, out = False, []
    for l in lines:
        cls = ""
        if l.startswith("diff "):
            in_hunk, cls = False, "hd"
        elif not in_hunk:
            if l.startswith("@@"):
                in_hunk, cls = True, "hk"
            else:
                cls = "hd"
        elif l.startswith("@@"):
            cls = "hk"
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


def wip_files(kind=""):
    """WIP の全ファイルを 競合 → ステージ済み → 未ステージ → 未追跡 の順に、種別(wip)つきで返す。
    kind が amend のときは、amend 後のコミットの内容 (直前のコミットの親との差分) を返す。"""
    if kind == "amend":
        s = api_status({"amend": ["1"]})
        if "amend_files" not in s:
            raise UserError("amend_unavailable")
        return s["amend_files"]
    s = api_status({})
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
    d = api_diff(q)
    if wip:  # 作業ツリーの変更
        files, ctx, ref = wip_files(wip), {}, "WIP"
        info = f'<div class="meta">{html.escape(tr("working_tree"))} — {html.escape(localized_wip_title(wip))}</div>'
        cmd = {"staged": f"git diff --cached -- {path}", "amend": f"git diff HEAD^ -- {path}",
               "untracked": f"git diff --no-index /dev/null {path}"}.get(wip, f"git diff -- {path}")
    elif "from" in q:  # 2コミット間の比較
        c = api_compare(q)
        a, b = c["from"], c["to"]
        files, ctx = c["files"], {"from": a["hash"], "to": b["hash"]}
        ref = f"{a['hash'][:7]}..{b['hash'][:7]}"
        info = commit_line_html("A", a) + commit_line_html("B", b)
        cmd = f"git diff {a['hash'][:7]} {b['hash'][:7]} -- {path}"
    else:
        c = api_commit(q)
        files, ctx, ref = c["files"], {"hash": c["hash"]}, c["hash"][:7]
        subject = c["message"].split("\n", 1)[0]
        info = (f'<div class="meta">{html.escape(tr("commit_label"))} {ref}'
                f'<a class="copy" data-copy="{c["hash"]}" title="{html.escape(tr("copy_hash_title"))}">📋</a>'
                f' — {html.escape(subject)}</div>'
                f'<div class="meta">{html.escape(c["author"])} / {html.escape(c["date"])}</div>')
        cmd = f"git show {ref} -- {path}"
    name = f"{old} => {path}" if old else path

    side, last = [], None
    for f in files:
        if wip and f.get("wip") != last:
            last = f.get("wip")
            side.append(f'<div class="grp">{html.escape(localized_wip_title(last))}</div>')
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
        cur = "cur" if f["path"] == path and (not wip or f.get("wip") == wip) else ""
        side.append(f'<a class="{cur}" href="{html.escape(diff_url(ctx, f))}">'
                    f'<span>{html.escape(fname)}</span><span class="n">{counts}</span></a>')

    return f"""<!DOCTYPE html>
<html lang="{UI_LANG}"><head><meta charset="utf-8">
<title>{html.escape(path)} @ {ref} - {html.escape(PROJECT_NAME)}</title>
{FAVICON_LINK}
<style>{DIFF_CSS}</style></head><body>
<div id="side">
  <div class="meta">{len(files)} {tr("files_changed")}</div>
  {"".join(side)}
</div>
<div id="main">
  <h1>{html.escape(name)}</h1>
  {info}
  <div class="meta"><code>{html.escape(cmd)}</code></div>
  <div class="meta">{html.escape(d["encoding"] or "binary")}</div>
  {'<p class="hint">' + tr("large_file_diff") + '</p>' if d["truncated"] else ""}
  <div class="diff">{diff_lines_html(d["diff"]) or '<p class="hint">' + tr("no_diff") + '</p>'}</div>
</div>
<script>{COPY_JS}</script>
</body></html>
"""


FILES_CSS = """
  html, body { height: 100%; margin: 0; font-family: system-ui, sans-serif; font-size: 13px; color: #222; }
  body { display: flex; }
  #tree { width: 360px; flex: none; overflow: auto; border-right: 1px solid #ccc; background: #fafafa; padding: 12px; box-sizing: border-box; }
  #main { flex: 1; min-width: 0; overflow: auto; padding: 14px 18px; box-sizing: border-box; }
  h1 { font-size: 15px; margin: 0 0 8px; word-break: break-all; }
  h2 { font-size: 14px; margin: 0 0 8px; }
  .meta, .hint { color: #666; margin: 3px 0; }
  .crumb { margin: 8px 0; word-break: break-all; }
  .crumb a, .entry { color: #06c; text-decoration: none; cursor: pointer; }
  .crumb a:hover, .entry:hover { text-decoration: underline; }
  .entry { display: flex; gap: 8px; padding: 4px 6px; border-radius: 3px; font-family: monospace; }
  .entry:hover { background: #eef; }
  .entry .size { margin-left: auto; color: #777; white-space: nowrap; }
  pre { white-space: pre-wrap; word-break: break-word; background: #fff; border: 1px solid #ddd; padding: 10px; font: 12px monospace; }
  pre.code { color: #24292f; tab-size: 4; }
  pre.code { padding-left: 0; }
  .ln { display: flex; }
  .ln:hover { background: #f6f8fa; }
  .ln .no { flex: none; width: var(--nw, 3ch); box-sizing: content-box; padding: 0 10px; margin-right: 10px; text-align: right; color: #8c959f; border-right: 1px solid #e3e3e3; user-select: none; }
  .ln .tx { flex: 1; min-width: 0; }
  .tok-keyword { color: #8250df; font-weight: 600; }
  .tok-string { color: #0a7b35; }
  .tok-comment { color: #6e7781; font-style: italic; }
  .tok-number { color: #0550ae; }
  .tok-type { color: #953800; }
  .tok-tag { color: #116329; }
  .tok-attr { color: #8250df; }
  .tok-regex { color: #cf222e; }
  .binary { color: #777; padding: 10px; border: 1px solid #ddd; }
  .file-actions { display: flex; gap: 6px; margin: 8px 0; }
  .file-actions button { background: #e8eefc; border: 1px solid #b8c7ee; padding: 4px 8px; border-radius: 3px; cursor: pointer; }
  .file-actions button:hover { background: #d6e2fb; }
  .file-actions .msg { margin-left: 8px; color: #0a7b35; white-space: pre-wrap; }
  .file-actions .msg.err { color: #c00; }
  table.file-log { border-collapse: collapse; width: 100%; margin-top: 8px; }
  table.file-log td { border-bottom: 1px solid #e3e3e3; padding: 5px 6px; vertical-align: top; }
  table.file-log td.hash { width: 1%; font-family: monospace; white-space: nowrap; }
  table.file-log td.date { width: 1%; color: #777; white-space: nowrap; }
  table.file-log a { color: inherit; text-decoration: none; }
  table.file-log tr:hover td { background: #eef; }
"""


FILES_PAGE = r"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8"><title>{{file_view}} - __PROJECT__</title>
__FAVICON__
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css">
<style>__CSS__</style><script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script></head>
<body><aside id="tree"><h1>{{file_view}}</h1><div class="meta" id="commit"></div><div id="list"></div></aside>
<main id="main"><h2 id="title">{{select_file}}</h2>
<div id="fileActions" class="file-actions" hidden><button id="fileLogBtn">{{file_log}}</button><button id="fileContentBtn" hidden>{{file_content}}</button><button id="fileCopyBtn" hidden>{{copy_to_work}}</button><span id="fileMsg" class="msg"></span></div>
<div id="content" class="hint">{{select_file_hint}}</div></main>
<script>
/*__FILE_I18N__*/
function t(key, values = {}) {
  let text = key in I18N ? I18N[key] : key;
  Object.entries(values).forEach(([name, value]) => { text = text.replaceAll("{" + name + "}", () => String(value)); });
  return text;
}

const PROJECT_DOCUMENT_TITLE = document.title.substring(document.title.indexOf(" - ") + 3);
function setDocumentTitle(label) {
  document.title = label ? label + " - " + PROJECT_DOCUMENT_TITLE : t("file_view") + " - " + PROJECT_DOCUMENT_TITLE;
}

const qs = new URLSearchParams(location.search), hash = qs.get("hash"), fileViewTab = qs.get("file_view") === "1";
const list = document.getElementById("list"), content = document.getElementById("content"), title = document.getElementById("title");
const fileActions = document.getElementById("fileActions"), fileLogBtn = document.getElementById("fileLogBtn"), fileContentBtn = document.getElementById("fileContentBtn");
const fileCopyBtn = document.getElementById("fileCopyBtn"), fileMsg = document.getElementById("fileMsg");
fileCopyBtn.hidden = !CAN_COPY;
let current = "", currentFile = "";
function esc(s) { return String(s).replace(/[&<>\"]/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", "\"":"&quot;"}[c])); }
function api(path, params) { return fetch(path + "?" + new URLSearchParams(params)).then(r => r.ok ? r.json() : r.text().then(t => { throw new Error(t || r.status); })); }
function showError(e) { list.innerHTML = '<p class="hint">' + t("error") + esc(e.message) + '</p>'; }
function loadTree(path, push) {
  current = path;
  fileActions.hidden = true;
  if (push) history.pushState({path: path}, "", "?hash=" + encodeURIComponent(hash) + (path ? "&path=" + encodeURIComponent(path) : ""));
  api("/api/tree", {hash: hash, path: path}).then(r => {
    document.getElementById("commit").textContent = r.hash.slice(0, 7);
    const parts = path ? path.split("/") : [];
    let crumb = '<div class="crumb"><a data-dir="">📁 ' + t("root") + '</a>';
    parts.forEach((p, i) => { const d = parts.slice(0, i + 1).join("/"); crumb += ' / <a data-dir="' + esc(d) + '">' + esc(p) + '</a>'; });
    crumb += '</div>';
    list.innerHTML = crumb + (r.entries.length ? r.entries.map(e => {
      const p = path ? path + "/" + e.name : e.name;
      const icon = e.type === "tree" ? "📁" : "📄";
      const size = e.size === null ? "" : e.size.toLocaleString() + " B";
      return '<div class="entry" data-type="' + e.type + '" data-path="' + esc(p) + '"><span>' + icon + ' ' + esc(e.name) + '</span><span class="size">' + size + '</span></div>';
    }).join("") : '<p class="hint">' + t("empty_directory") + '</p>');
    list.querySelectorAll("[data-dir]").forEach(a => a.onclick = () => loadTree(a.dataset.dir, true));
    list.querySelectorAll(".entry").forEach(a => a.onclick = () => a.dataset.type === "tree" ? loadTree(a.dataset.path, true) : loadFile(a.dataset.path));
  }).catch(showError);
}
function setFileMsg(text, ok) { fileMsg.textContent = text; fileMsg.className = "msg" + (ok ? "" : " err"); }
function defaultCopyName(path) {  // 例: src/a.py → src/a_1234567.py
  const i = path.lastIndexOf("/"), dir = path.slice(0, i + 1), name = path.slice(i + 1), dot = name.lastIndexOf(".");
  const suffix = "_" + hash.slice(0, 7);
  return dir + (dot > 0 ? name.slice(0, dot) + suffix + name.slice(dot) : name + suffix);
}
fileCopyBtn.onclick = () => {
  if (!currentFile) return;
  const dest = prompt(t("prompt_copy_dest"), defaultCopyName(currentFile));
  if (dest === null || !dest.trim()) return;
  fetch("/api/file-copy", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({hash: hash, path: currentFile, dest: dest.trim()})})
    .then(r => r.ok ? r.json() : r.text().then(m => { throw new Error(m || r.status); }))
    .then(r => setFileMsg(r.message, true))
    .catch(e => setFileMsg(e.message, false));
};
function loadFile(path) {
  currentFile = path;
  setDocumentTitle(path);
  setFileMsg("", true);
  fileActions.hidden = false;
  fileLogBtn.hidden = false;
  fileContentBtn.hidden = true;
  api("/api/file", {hash: hash, path: path}).then(r => {
    const lang = languageFor(r.path);
    title.textContent = r.path + (lang ? " (" + lang + ")" : "") + (r.encoding ? " [" + r.encoding + "]" : "");
    content.className = r.binary ? "binary" : "";
    content.innerHTML = r.binary ? t("binary_file") : numberedCode(r.content, lang) + (r.truncated ? '<p class="hint">' + t("large_file_head") + '</p>' : '');
  }).catch(e => { title.textContent = path; content.className = "hint"; content.textContent = t("error") + e.message; });
}
function loadFileLog(path) {
  if (fileViewTab || (window.opener && !window.opener.closed)) {
    // 別タブでは親のグラフへ通知せず、このファイル画面にログを表示する。
    fileLogBtn.hidden = true;
    fileContentBtn.hidden = false;
    setDocumentTitle(t("file_history_title", {path: path}));
    title.textContent = t("file_history_title", {path: path});
    content.className = "hint";
    content.textContent = t("loading");
    api("/api/log", {rev: hash, path: path}).then(r => {
      const rows = r.commits.map(c => '<tr><td class="hash">' + esc(c[1]) +
        '</td><td class="date">' + esc(c[3]) + '</td><td>' + esc(c[4]) + '</td></tr>').join("");
      content.className = "";
      content.innerHTML = '<div class="meta">' + esc(t("n_commits", {n: r.total})) + '</div>' +
        (rows ? '<table class="file-log"><tbody>' + rows + '</tbody></table>' : '<p class="hint">' + t("no_history") + '</p>');
    }).catch(e => { content.className = "hint"; content.textContent = t("error") + e.message; });
    return;
  }
  // 同一タブではファイル画面にログを描画せず、グラフ画面へ戻して表示する。
  location.href = "/?file_log_hash=" + encodeURIComponent(hash) + "&file_log_path=" + encodeURIComponent(path);
}
fileLogBtn.onclick = () => { if (currentFile) loadFileLog(currentFile); };
fileContentBtn.onclick = () => { if (currentFile) loadFile(currentFile); };
const KEYWORDS = {
  python: "and as assert async await break case class continue def del elif else except finally for from global if import in is lambda match nonlocal not or pass raise return try while with yield None True False self",
  javascript: "as async await break case catch class const continue debugger default delete do else export extends false finally for from function get if import in instanceof let new null of return set static super switch this throw true try typeof undefined var void while with yield",
  typescript: "as async await break case catch class const continue debugger default delete do else export extends false finally for from function get if import in instanceof interface let new null of private protected public readonly return set static super switch this throw true try typeof type undefined var void while with yield",
  java: "abstract assert boolean break byte case catch char class const continue default do double else enum extends final finally float for if implements import instanceof int interface long native new package private protected public return short static strictfp super switch synchronized this throw throws transient try void volatile while true false null",
  c: "auto break case char const continue default do double else enum extern float for goto if inline int long register restrict return short signed sizeof static struct switch typedef union unsigned void volatile while true false NULL",
  go: "break default func interface select case defer go map struct chan else goto package switch const fallthrough if range type continue for import return var true false nil",
  rust: "as async await break const continue crate dyn else enum extern false fn for if impl in let loop match mod move mut pub ref return self Self static struct super trait true type unsafe use where while",
  sql: "select from where and or not insert into update delete create alter drop table index view join inner left right full outer on as group by order having limit offset distinct union all null is like values set",
  shell: "if then else elif fi for while in do done case esac function select time coproc true false",
  ruby: "BEGIN END alias and begin break case class def defined do else elsif end ensure false for if in module next nil not or redo rescue retry return self super then true undef unless until when while yield require attr_reader attr_writer attr_accessor include extend private protected public",
  json: "true false null"
};
Object.keys(KEYWORDS).forEach(k => KEYWORDS[k] = new Set(KEYWORDS[k].split(" ")));
function languageFor(path) {
  const name = path.toLowerCase().split("/").pop();
  if (["dockerfile", "makefile"].includes(name)) return "shell";
  const ext = name.includes(".") ? name.split(".").pop() : "";
  return ({py:"python", pyw:"python", js:"javascript", jsx:"javascript", mjs:"javascript", cjs:"javascript", ts:"typescript", tsx:"typescript", java:"java", c:"c", h:"c", cc:"c", cpp:"c", hpp:"c", go:"go", rs:"rust", rb:"ruby", ru:"ruby", sql:"sql", sh:"shell", bash:"shell", zsh:"shell", json:"json", css:"css", html:"html", htm:"html", xml:"html", md:"markdown", markdown:"markdown", yml:"yaml", yaml:"yaml"})[ext] || "";
}
function token(cls, text) { return '<span class="tok-' + cls + '">' + esc(text) + '</span>'; }
function highlight(source, lang) {
  if (!lang || lang === "markdown") return esc(source);
  if (lang === "html") return source.split(/(<[^>]*>)/g).map(x => x.startsWith("<") ? token("tag", x) : esc(x)).join("");
  const words = KEYWORDS[lang] || new Set(), out = [];
  let i = 0;
  while (i < source.length) {
    const rest = source.slice(i), ch = source[i];
    const lineComment = (["python", "ruby", "shell", "yaml"].includes(lang)) && ch === "#";
    const slashComment = (ch === "/" && (source[i + 1] === "/" || source[i + 1] === "*"));
    if (lineComment || slashComment) {
      const end = lineComment ? source.indexOf("\n", i) : (source[i + 1] === "/" ? source.indexOf("\n", i) : source.indexOf("*/", i) + 2);
      const j = end < (lineComment ? 0 : 2) ? source.length : end;
      out.push(token("comment", source.slice(i, j))); i = j; continue;
    }
    const triple = lang === "python" && (source.startsWith("'''", i) || source.startsWith('\"\"\"', i));
    if (triple || ch === "'" || ch === '"' || (ch === "`" && ["javascript", "typescript"].includes(lang))) {
      const quote = triple ? source.slice(i, i + 3) : ch; let j = i + quote.length;
      if (triple) {
        const end = source.indexOf(quote, j);
        j = end < 0 ? source.length : end + quote.length;
      } else {
        while (j < source.length) { if (source[j] === "\\") j += 2; else if (source[j++] === quote) break; }
      }
      out.push(token("string", source.slice(i, j))); i = j; continue;
    }
    const number = rest.match(/^(?:0x[\da-f]+|\d+(?:\.\d+)?(?:e[+-]?\d+)?)/i);
    if (number) { out.push(token("number", number[0])); i += number[0].length; continue; }
    const word = rest.match(/^[A-Za-z_$][\w$]*/);
    if (word) { const w = word[0]; out.push(words.has(w) ? token("keyword", w) : esc(w)); i += w.length; continue; }
    out.push(esc(ch)); i++;
  }
  return out.join("");
}
function highlightCode(source, lang) {
  if (window.hljs && lang && hljs.getLanguage(lang)) {
    try { return hljs.highlight(source, {language: lang, ignoreIllegals: true}).value; } catch (e) {}
  }
  return highlight(source, lang);
}
/* ハイライト済み HTML を行ごとに分割する。複数行にまたがる span は行末で閉じて次の行で開き直す */
function splitHighlightedLines(html) {
  const lines = [], open = [];
  let cur = "";
  html.split(/(<[^>]+>|\n)/).forEach(p => {
    if (p === "\n") { cur += "</span>".repeat(open.length); lines.push(cur); cur = open.join(""); }
    else if (p.startsWith("</")) { open.pop(); cur += p; }
    else if (p.startsWith("<")) { open.push(p); cur += p; }
    else cur += p;
  });
  lines.push(cur);
  return lines;
}
function numberedCode(source, lang) {
  const lines = splitHighlightedLines(highlightCode(source.replace(/\r\n?/g, "\n"), lang));
  if (lines.length > 1 && lines[lines.length - 1].replace(/<[^>]*>/g, "") === "") lines.pop();  // 末尾の改行は空行として数えない
  const width = String(lines.length).length;
  return '<pre class="code" style="--nw:' + width + 'ch"><code>' +
    lines.map((l, i) => '<div class="ln" id="L' + (i + 1) + '"><span class="no">' + (i + 1) + '</span><span class="tx">' + l + '</span></div>').join("") +
    '</code></pre>';
}
window.onpopstate = () => loadTree(new URLSearchParams(location.search).get("path") || "", false);
if (!hash) showError(new Error(t("commit_required"))); else loadTree(qs.get("path") || "", false);
</script></body></html>""".replace("__CSS__", FILES_CSS)


def render_files_page():
    return with_project(fill_template(FILES_PAGE)
            .replace('<html lang="ja">', f'<html lang="{UI_LANG}">')
            .replace("/*__FILE_I18N__*/", "const I18N = " + client_translations() + ";\n"
                     + "const CAN_COPY = " + ("true" if WRITABLE and HAS_WORKTREE else "false") + ";"))


_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uff00-\uffef]")


def _strip_comments(text):
    """JS / HTML / CSS のコメントを除く (コメントは日本語のままでよい)。URL の // は残す。行数は変えない。"""
    blank = lambda m: "\n" * m.group(0).count("\n")  # 行番号がずれないよう、改行は残す
    text = re.sub(r"/\*.*?\*/", blank, text, flags=re.S)
    text = re.sub(r"<!--.*?-->", blank, text, flags=re.S)
    return re.sub(r"(?<![:\w'\"])//[^\n]*", "", text)


def check_i18n():
    """翻訳の漏れを検査する (--check-i18n)。問題を表示して、あれば 1、なければ 0 を返す。
    1. 全言語でキー集合と {プレースホルダ} が en と一致しているか
    2. コードが使うキーが定義済みか / 使われていないキーがないか
    3. 辞書の外に日本語のリテラルが残っていないか (docstring とコメントは除く)
    4. 日本語以外の言語でテンプレートを展開しても、日本語や未置換のキーが残らないか"""
    problems = []
    base = TRANSLATIONS["en"]
    params_of = lambda s: sorted(set(re.findall(r"\{(\w+)\}", s)))
    for lang, table in TRANSLATIONS.items():
        problems += [f"[{lang}] missing key: {k}" for k in sorted(set(base) - set(table))]
        problems += [f"[{lang}] key not in en: {k}" for k in sorted(set(table) - set(base))]
        problems += [f"[{lang}] placeholders differ from en: {k}" for k in sorted(set(base) & set(table))
                     if params_of(base[k]) != params_of(table[k])]
        if lang == "en":
            problems += [f"[en] contains CJK text: {k}" for k, v in table.items() if _CJK.search(v)]

    with open(__file__, encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)
    docstrings, dict_lines = set(), (0, 0)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                docstrings.add(id(first.value))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "TRANSLATIONS" for t in node.targets):
            dict_lines = (node.lineno, node.end_lineno)
    literals = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)) or id(node) in docstrings:
            continue
        if dict_lines[0] <= node.lineno <= dict_lines[1]:
            continue
        literals.add(node.value)
        text = _strip_comments(node.value) if "\n" in node.value else node.value
        for offset, line in enumerate(text.split("\n")):
            if _CJK.search(line):
                problems.append(f"line {node.lineno + offset}: untranslated literal: {line.strip()[:80]}")

    code = "\n".join(l for i, l in enumerate(src.split("\n"), 1) if not dict_lines[0] <= i <= dict_lines[1])
    used = set()
    for pattern in (r"""\bt\(\s*["'](\w+)["']""", r"""\btr\(\s*["'](\w+)["']""",
                    r"""UserError\(\s*["'](\w+)["']""", r"\{\{(\w+)\}\}"):
        used |= set(re.findall(pattern, code))
    problems += [f"undefined key used: {k}" for k in sorted(used - set(base))]
    for k in sorted(base):
        if k not in used and k not in literals and not re.search("[\"']" + re.escape(k) + "[\"']", code):
            problems.append(f"unused key: {k}")

    global UI_LANG
    saved = UI_LANG
    try:
        for lang in sorted(TRANSLATIONS):
            if lang == "ja":
                continue
            UI_LANG = lang
            for name, page in (("main page", HTML_TEMPLATE), ("files page", FILES_PAGE)):
                text = _strip_comments(fill_template(page))
                problems += [f"[{lang}] {name}: untranslated text: {l.strip()[:80]}" for l in text.split("\n") if _CJK.search(l)]
                problems += [f"[{lang}] {name}: unresolved key: {k}" for k in re.findall(r"\{\{(\w+)\}\}", text)]
    finally:
        UI_LANG = saved

    for p in problems:
        print(p)
    print(f"i18n check: {len(problems)} problem(s), {len(base)} keys, languages: {', '.join(sorted(TRANSLATIONS))}")
    return 1 if problems else 0


# --------------------------------------------------------------------------
# 書き込み操作 (ブランチ作成 / タグ作成 / チェックアウト)
# --------------------------------------------------------------------------
class Forbidden(Exception):
    pass


_write_lock = threading.Lock()


def is_loopback(host):
    return host in ("localhost", "::1") or host.startswith("127.")


def resolve_commit(h):
    if not HASH_RE.fullmatch(h or ""):
        raise ValueError("bad hash")
    try:
        return git("rev-parse", "--verify", "--quiet", h + "^{commit}").strip()
    except subprocess.CalledProcessError:
        raise UserError("commit_not_found")


def check_ref_name(kind, name):
    prefix = {"branch": "refs/heads/", "tag": "refs/tags/"}[kind]
    label = {"branch": ("branch_name_invalid", "branch_name_unusable"),
             "tag": ("tag_name_invalid", "tag_name_unusable")}[kind]
    if not name or name.startswith("-") or "\0" in name:
        raise UserError(label[0])
    r = subprocess.run(["git", "-C", REPO, "check-ref-format", prefix + name], capture_output=True)
    if r.returncode != 0:
        raise UserError(label[1], name=repr(name))


def run_git_write(*args, literal=False, cwd=None):
    """git を実行し、失敗したら git のエラーメッセージを ValueError にして返す。"""
    env = dict(os.environ, GIT_LITERAL_PATHSPECS="1") if literal else None  # パスをglobとして解釈しない
    r = subprocess.run(["git", "-C", cwd or REPO, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    if r.returncode != 0:
        raise ValueError((r.stderr or r.stdout).strip() or tr("git_command_failed"))
    return (r.stdout + r.stderr).strip()


def git_c_locale(*args, cwd=None):
    """メッセージを解析するため、ロケールを C にして git を実行する (失敗しても例外にしない)。"""
    env = dict(os.environ, LC_ALL="C", LANGUAGE="C")
    return subprocess.run(["git", "-C", cwd or REPO, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)


def parse_checkout_blockers(text):
    """checkout が止まったときのエラーから、止めているファイルを取り出す。
    modified: 編集中 (ステージ済み含む) で上書きされてしまうファイル / untracked: 同名の管理外ファイル"""
    res = {"modified": [], "untracked": []}
    kind = None
    for line in text.splitlines():
        if "would be overwritten by" in line:
            if "local changes" in line:
                kind = "modified"
            elif "untracked working tree files" in line:
                kind = "untracked"
            else:
                kind = None
            continue
        if kind and line.startswith("\t") and line.strip():
            res[kind].append(line[1:])
        else:
            kind = None
    return res


def stash_all_changes():
    """未コミットの変更 (未追跡ファイルも) を stash に退避する。作った stash の名前を返す (変更がなければ None)。"""
    def top():
        r = subprocess.run(["git", "-C", REPO, "rev-parse", "-q", "--verify", "refs/stash"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        return r.stdout.strip()
    before = top()
    run_git_write("stash", "push", "--include-untracked", "-m",
                  tr("stash_commit_msg", time=time.strftime("%Y-%m-%d %H:%M:%S")))
    return "stash@{0}" if top() != before else None


def discard_blockers(blockers):
    """チェックアウトを止めているファイルだけを破棄する (編集中の変更は HEAD の状態に戻し、管理外ファイルは削除)。"""
    top = git("rev-parse", "--show-toplevel").strip()
    top_real = os.path.realpath(top)
    for path in blockers["modified"]:
        in_head = git_c_locale("cat-file", "-e", "HEAD:" + path, cwd=top).returncode == 0
        if in_head:
            run_git_write("checkout", "HEAD", "--", path, literal=True, cwd=top)
        else:  # インデックスにだけ追加されたファイル
            run_git_write("rm", "-f", "-q", "--", path, literal=True, cwd=top)
    for path in blockers["untracked"]:
        full = os.path.join(top_real, path)
        parent = os.path.realpath(os.path.dirname(full))
        if os.path.commonpath([parent, top_real]) != top_real:
            raise UserError("path_outside_repo", path=path)
        if os.path.isdir(full) and not os.path.islink(full):
            raise UserError("dir_no_autodelete", path=path)
        if os.path.lexists(full):
            os.remove(full)


def checkout_resolving(args, mode):
    """git checkout <args> を実行する。成功なら None を返す。
    編集中のファイル/同名の管理外ファイルで止まったとき、mode が None なら {blocked, modified, untracked} を返し、
    "stash" なら全変更を退避してから、"discard" なら止めているファイルだけ破棄してから再実行する。"""
    if mode not in (None, "stash", "discard"):
        raise UserError("on_conflict_invalid")
    stashed = None
    if mode == "stash":
        stashed = stash_all_changes()
    for _ in range(3):
        r = git_c_locale("checkout", *args)
        if r.returncode == 0:
            return {"stashed": stashed} if stashed else None
        text = (r.stderr + r.stdout).strip()
        blockers = parse_checkout_blockers(text)
        if not (blockers["modified"] or blockers["untracked"]):
            raise ValueError(text or tr("checkout_failed"))
        if mode is None:
            return {"blocked": True, **blockers}
        if mode == "stash":
            raise ValueError(tr("checkout_still_blocked") + "\n" + text)
        discard_blockers(blockers)
    raise UserError("checkout_failed")


def with_stash_note(res, msg):
    if res and res.get("stashed"):
        msg += tr("stash_note", stash=res["stashed"])
    return msg


def api_branch(body):
    h = resolve_commit(body.get("hash", ""))
    name = str(body.get("name", "")).strip()
    check_ref_name("branch", name)
    if body.get("checkout"):
        upstream = body.get("upstream")
        if upstream:
            if not isinstance(upstream, str) or upstream.startswith("-") or "\0" in upstream:
                raise UserError("remote_branch_not_found")
            ref = "refs/remotes/" + upstream
            r = subprocess.run(["git", "-C", REPO, "rev-parse", "--verify", "--quiet", ref + "^{commit}"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode != 0 or r.stdout.strip() != h:
                raise UserError("remote_branch_not_found")
            checkout_args = ["-b", name, "--track", upstream]
        else:
            checkout_args = ["-b", name, h]
        res = checkout_resolving(checkout_args, body.get("on_conflict"))
        if res and res.get("blocked"):
            return res
        return {"ok": True, "message": with_stash_note(res, tr("branch_created_checkout", name=name))}
    run_git_write("branch", name, h)
    return {"ok": True, "message": tr("branch_created", name=name)}


def api_tag(body):
    h = resolve_commit(body.get("hash", ""))
    name = str(body.get("name", "")).strip()
    msg = str(body.get("message", "")).strip()
    check_ref_name("tag", name)
    if msg:
        run_git_write("tag", "-a", name, "-m", msg, h)
        return {"ok": True, "message": tr("tag_created_annotated", name=name)}
    run_git_write("tag", name, h)
    return {"ok": True, "message": tr("tag_created", name=name)}


def api_delete_ref(body):
    kind = body.get("kind")
    if kind == "remote-branch":
        name = str(body.get("name", "")).strip()
        remotes = git("remote").split()
        remote = next((r for r in sorted(remotes, key=len, reverse=True) if name.startswith(r + "/")), None)
        branch = name[len(remote) + 1:] if remote else ""
        if not remote or not branch:
            raise UserError("remote_branch_not_found")
        check_ref_name("branch", branch)
        auth = auth_for_remote(remote, body)
        r = run_git_net("push", remote, "--delete", branch, auth=auth)
        if r.returncode != 0:
            if is_http_auth_error(r) and not body.get("auth_retry"):
                return {"auth_required": True, "auth_remote": remote}
            if body.get("auth_retry"):
                HTTP_AUTH_CACHE.pop(remote, None)
            raise ValueError(net_error(r, tr("git_command_failed")))
        remember_auth(remote, auth)
        return {"ok": True, "message": tr("remote_branch_deleted", name=name)}
    if kind not in ("branch", "tag"):
        raise UserError("ref_delete_only")
    name = str(body.get("name", "")).strip()
    check_ref_name(kind, name)
    ref = ("refs/heads/" if kind == "branch" else "refs/tags/") + name
    exists = subprocess.run(["git", "-C", REPO, "show-ref", "--verify", "--quiet", ref])
    if exists.returncode != 0:
        raise UserError("ref_not_exist", name=repr(name))
    if kind == "branch":
        if body.get("force") is True:
            run_git_write("branch", "-D", "--", name)
        else:
            r = subprocess.run(["git", "-C", REPO, "branch", "-d", "--", name],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode != 0:
                ancestor = subprocess.run(["git", "-C", REPO, "merge-base", "--is-ancestor", name, "HEAD"])
                if ancestor.returncode == 1:
                    return {"needs_force": True}
                raise ValueError((r.stderr or r.stdout).strip() or tr("branch_delete_failed"))
        return {"ok": True, "message": tr("branch_deleted", name=name)}
    run_git_write("tag", "-d", "--", name)
    return {"ok": True, "message": tr("tag_deleted", name=name)}


def api_checkout(body):
    branch = body.get("branch")
    if branch:
        branch = str(branch)
        check_ref_name("branch", branch)
        res = checkout_resolving([branch, "--"], body.get("on_conflict"))
        if res and res.get("blocked"):
            return res
        return {"ok": True, "message": with_stash_note(res, tr("checkout_branch_done", branch=branch))}
    h = resolve_commit(body.get("hash", ""))
    res = checkout_resolving(["--detach", h], body.get("on_conflict"))
    if res and res.get("blocked"):
        return res
    return {"ok": True, "message": with_stash_note(res, tr("checkout_detached_done", hash=h[:7]))}


def api_reset(body):
    """現在の HEAD を選択したコミットへ移動する。"""
    mode = body.get("mode", "mixed")
    if mode not in ("soft", "mixed", "hard"):
        raise UserError("reset_mode_invalid")
    if is_merging() or is_rebasing() or is_cherry_picking() or is_reverting():
        raise UserError("reset_blocked")
    h = resolve_commit(body.get("hash", ""))
    before = git("rev-parse", "HEAD").strip() if has_head() else None
    run_git_write("reset", "--" + mode, h)
    if before == h:
        return {"ok": True, "message": tr("reset_noop", hash=h[:7])}
    return {"ok": True, "message": tr("reset_done", hash=h[:7], mode=mode)}


def is_merging():
    return subprocess.run(["git", "-C", REPO, "rev-parse", "-q", "--verify", "MERGE_HEAD"],
                          capture_output=True).returncode == 0


def has_head():
    return subprocess.run(["git", "-C", REPO, "rev-parse", "-q", "--verify", "HEAD"],
                          capture_output=True).returncode == 0


def has_parent():
    return subprocess.run(["git", "-C", REPO, "rev-parse", "-q", "--verify", "HEAD^"],
                          capture_output=True).returncode == 0


def api_wip_commit(body):
    """チェックされたファイルを add してコミットする (TortoiseGit のコミットダイアログと同じ考え方)。
    ステージ済みでチェックのないファイルは、ステージから外す (作業ツリーの内容はそのまま)。"""
    if not WIP_ENABLED:
        raise UserError("wip_disabled")
    amend = body.get("amend") is True
    if amend:
        if not has_head():
            raise UserError("amend_no_commit")
        if is_merging() or is_rebasing():
            raise UserError("amend_blocked")
    msg = str(body.get("message", "")).strip()
    if not msg:
        raise UserError("message_required")
    paths = body.get("paths")
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths) or (not paths and not amend):
        raise UserError("select_files")  # amend はメッセージだけの変更 (paths が空) もできる
    checked = set(paths)
    s = parse_status(read_status_raw(), limit=10 ** 9)
    known = {f["path"] for k in ("staged", "unstaged", "untracked", "conflicts") for f in s[k]}
    head_changes = amend_head_changes(amend_base()) if amend else {}  # 直前のコミットが変更したファイル (チェックを外すとコミットから外す)
    if amend and head_changes and not checked:  # index を触る前に確認する (空のコミットは git が拒否する)
        raise UserError("amend_would_empty")
    bad = checked - known - set(head_changes)  # 任意のパスを add させないよう、いま変更ありと報告されているものだけを対象にする
    if bad:
        raise UserError("files_unchanged", files=", ".join(sorted(bad)[:3]))
    unstage = []
    for f in s["staged"]:
        if f["path"] not in checked:
            unstage.append(f["path"])
            if f.get("old_path"):  # リネームは元のパスも一緒に戻す
                unstage.append(f["old_path"])
    if is_merging() and (unstage or any(f["path"] not in checked for f in s["conflicts"])):
        raise UserError("merging_need_all")
    step = 200  # コマンドラインが長くなりすぎないように分割する
    if unstage:
        for i in range(0, len(unstage), step):
            part = unstage[i:i + step]
            if has_head():
                run_git_write("reset", "-q", "--", *part, literal=True)
            else:  # 最初のコミット前は HEAD がないので、インデックスから外す
                run_git_write("rm", "--cached", "-r", "-q", "-f", "--", *part, literal=True)
    drop = []  # amend: チェックを外した「直前のコミットのファイル」は、コミットから外す (親の状態に戻す。作業ツリーはそのまま)
    for path, old in head_changes.items():
        if path not in checked:
            drop.append(path)
            if old:
                drop.append(old)
    if drop:
        base = amend_base()
        for i in range(0, len(drop), step):
            part = drop[i:i + step]
            if has_parent():
                run_git_write("reset", "-q", base, "--", *part, literal=True)
            else:  # 最初のコミットには親がないので、インデックスから外す
                run_git_write("rm", "--cached", "-r", "-q", "-f", "--", *part, literal=True)
    # すでにステージ済みの削除は add -A できない (パスが見つからず失敗する) が、インデックスにあるのでそのままコミットに入る
    gone = {f["path"] for f in s["staged"] if f["st"] == "D"} - {f["path"] for k in ("unstaged", "untracked") for f in s[k]}
    todo = sorted(checked - gone)
    for i in range(0, len(todo), step):
        run_git_write("add", "-A", "--", *todo[i:i + step], literal=True)
    run_git_write("commit", *(["--amend"] if amend else []), "-m", msg)
    h = git("rev-parse", "HEAD").strip()
    if not amend:
        message = tr("wip_committed", hash=h[:7], n=len(checked))
    elif not checked:
        message = tr("wip_amended_msg_only", hash=h[:7])
    elif drop:
        message = tr("wip_amended_dropped", hash=h[:7], n=len(checked),
                     dropped=len([d for d in head_changes if d not in checked]))
    else:
        message = tr("wip_amended", hash=h[:7], n=len(checked))
    return {"ok": True, "message": message}


def is_rebasing():
    for d in ("rebase-merge", "rebase-apply"):
        r = subprocess.run(["git", "-C", REPO, "rev-parse", "--git-path", d],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        path = r.stdout.strip()
        if path and os.path.isdir(os.path.join(REPO, path)):
            return True
    return False


def api_head(q=None):
    r = subprocess.run(["git", "-C", REPO, "symbolic-ref", "--short", "-q", "HEAD"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return {"branch": r.stdout.strip() or None, "merging": (is_merging() or is_rebasing()) if HAS_WORKTREE else False}


def api_merge(body):
    """選んだコミットを、いまチェックアウトしている HEAD にマージする。
    競合したときはマージ途中の状態で止める (WIP の一覧に競合ファイルが出るので、解決してコミットする)。"""
    if not HAS_WORKTREE:
        raise UserError("merge_no_worktree")
    h = resolve_commit(body.get("hash", ""))
    if is_merging():
        raise UserError("merge_in_progress")
    if not has_head():
        raise UserError("merge_no_commits")
    before = git("rev-parse", "HEAD").strip()
    args = ["merge", "--no-edit"]
    if body.get("no_ff"):
        args.append("--no-ff")
    r = subprocess.run(["git", "-C", REPO, *args, h], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        if is_merging():  # 競合 (自動マージできなかった)
            return {"ok": True, "conflict": True,
                    "message": tr("merge_conflict", target=h[:7])}
        raise ValueError(out or tr("merge_failed"))
    if git("rev-parse", "HEAD").strip() == before:  # git の出力文言 (ロケールで変わる) ではなく、HEAD が動いたかで判定する
        return {"ok": True, "message": tr("merge_uptodate", hash=h[:7])}
    return {"ok": True, "message": tr("merge_done", hash=h[:7])}


def api_rebase(body):
    """現在のブランチを、選んだコミットの上にリベースする。競合したら自動で中止して元に戻す。"""
    if not HAS_WORKTREE:
        raise UserError("rebase_no_worktree")
    h = resolve_commit(body.get("hash", ""))
    if is_merging() or is_rebasing():
        raise UserError("merge_or_rebase_in_progress")
    head = api_head()
    if not head["branch"]:
        raise UserError("detached_rebase")
    before = git("rev-parse", "HEAD").strip()
    r = subprocess.run(["git", "-C", REPO, "rebase", h], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        if is_rebasing():  # 競合 → 元に戻す
            subprocess.run(["git", "-C", REPO, "rebase", "--abort"], capture_output=True)
            raise UserError("rebase_conflict", target=h[:7], cmd="git rebase")
        raise ValueError(out or tr("rebase_failed"))
    if git("rev-parse", "HEAD").strip() == before:
        return {"ok": True, "message": tr("rebase_noop", branch=head["branch"], hash=h[:7])}
    return {"ok": True, "message": tr("rebase_done", branch=head["branch"], hash=h[:7])}


def api_rebase_range(body):
    """A より後の現在のブランチのコミットを B の上にリベースする。
    競合したら自動で中止して元に戻す。"""
    if not HAS_WORKTREE:
        raise UserError("rebase_no_worktree")
    a = resolve_commit(body.get("from", ""))
    b = resolve_commit(body.get("to", ""))
    if is_merging() or is_rebasing():
        raise UserError("merge_or_rebase_in_progress")
    head = api_head()
    if not head["branch"]:
        raise UserError("detached_rebase")
    before = git("rev-parse", "HEAD").strip()
    r = subprocess.run(["git", "-C", REPO, "rebase", "--onto", b, a], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        if is_rebasing():
            subprocess.run(["git", "-C", REPO, "rebase", "--abort"], capture_output=True)
            raise UserError("rebase_conflict", target=b[:7], cmd="git rebase --onto")
        raise ValueError(out or tr("rebase_failed"))
    if git("rev-parse", "HEAD").strip() == before:
        return {"ok": True, "message": tr("rebase_noop", branch=head["branch"], hash=b[:7])}
    return {"ok": True, "message": tr("rebase_range_done", branch=head["branch"], **{"from": a[:7], "to": b[:7]})}


def is_cherry_picking():
    return subprocess.run(["git", "-C", REPO, "rev-parse", "-q", "--verify", "CHERRY_PICK_HEAD"],
                          capture_output=True).returncode == 0


def is_reverting():
    return subprocess.run(["git", "-C", REPO, "rev-parse", "-q", "--verify", "REVERT_HEAD"],
                          capture_output=True).returncode == 0


def api_cherry_pick(body):
    """選んだコミットを、いまチェックアウトしている HEAD にチェリーピックする。
    競合したときと、変更が空になるとき (既に取り込み済み) は、中止して元に戻す。"""
    if not HAS_WORKTREE:
        raise UserError("cherry_no_worktree")
    h = resolve_commit(body.get("hash", ""))
    if is_merging() or is_rebasing() or is_cherry_picking() or is_reverting():
        raise UserError("op_in_progress")
    if not has_head():
        raise UserError("cherry_no_commits")
    if len(git("rev-list", "--parents", "-n", "1", h).split()) > 2:
        raise UserError("cherry_merge_commit")
    before = git("rev-parse", "HEAD").strip()
    r = subprocess.run(["git", "-C", REPO, "cherry-pick", h], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        if is_cherry_picking():
            unmerged = subprocess.run(["git", "-C", REPO, "-c", "core.quotepath=false", "diff", "--name-only", "--diff-filter=U"],
                                      capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
            subprocess.run(["git", "-C", REPO, "cherry-pick", "--abort"], capture_output=True)
            if not unmerged:  # 競合ではなく、変更が空になった
                raise UserError("cherry_empty", hash=h[:7])
            raise UserError("cherry_conflict", hash=h[:7])
        raise ValueError(out or tr("cherry_failed"))
    if git("rev-parse", "HEAD").strip() == before:
        return {"ok": True, "message": tr("cherry_noop", hash=h[:7])}
    return {"ok": True, "message": tr("cherry_done", hash=h[:7])}


def api_revert(body):
    """選んだコミットの変更を打ち消すコミットを、いまの HEAD の上に作る (git revert)。
    競合したときと、変更が空になるとき (既に打ち消し済み) は、中止して元に戻す。マージコミットは第1親を基準にする。"""
    if not HAS_WORKTREE:
        raise UserError("revert_no_worktree")
    h = resolve_commit(body.get("hash", ""))
    if is_merging() or is_rebasing() or is_cherry_picking() or is_reverting():
        raise UserError("op_in_progress")
    if not has_head():
        raise UserError("revert_no_commits")
    is_merge = len(git("rev-list", "--parents", "-n", "1", h).split()) > 2
    args = ["revert", "--no-edit"] + (["-m", "1"] if is_merge else [])
    r = subprocess.run(["git", "-C", REPO, *args, h], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        if is_reverting():
            unmerged = subprocess.run(["git", "-C", REPO, "-c", "core.quotepath=false", "diff", "--name-only", "--diff-filter=U"],
                                      capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
            subprocess.run(["git", "-C", REPO, "revert", "--abort"], capture_output=True)
            if not unmerged:  # 競合ではなく、変更が空になった
                raise UserError("revert_empty", hash=h[:7])
            raise UserError("revert_conflict", hash=h[:7])
        raise ValueError(out or tr("revert_failed"))
    return {"ok": True, "message": tr("revert_done_merge" if is_merge else "revert_done", hash=h[:7])}


def api_abort(body):
    """進行中のマージ / リベース / チェリーピック / リバートを中断して、開始前の状態に戻す。"""
    if not HAS_WORKTREE:
        raise UserError("abort_no_worktree")
    for cmd, active in (("merge", is_merging), ("rebase", is_rebasing),
                        ("cherry-pick", is_cherry_picking), ("revert", is_reverting)):
        if active():
            run_git_write(cmd, "--abort")
            return {"ok": True, "message": tr("aborted", cmd=cmd)}
    raise UserError("nothing_to_abort")


def unmerged_stages(path):
    """競合しているファイルが持つインデックスのステージ番号 (2 = ours, 3 = theirs)。片側で削除されていると、その番号がない。"""
    out = git("ls-files", "-u", "-z", "--", path, literal=True)
    return {rec.split("\t", 1)[0].split()[-1] for rec in out.split("\0") if "\t" in rec}


def api_resolve(body):
    """競合しているファイルを、ours か theirs の内容で丸ごと置き換えて解決済みにする (片側で削除されていたら削除する)。"""
    if not WIP_ENABLED:
        raise UserError("wip_disabled")
    side = body.get("side")
    if side not in ("ours", "theirs"):
        raise UserError("resolve_side_invalid")
    paths = body.get("paths")
    if not isinstance(paths, list) or not paths or not all(isinstance(p, str) for p in paths):
        raise UserError("select_files")
    s = parse_status(read_status_raw(), limit=10 ** 9)
    bad = set(paths) - {f["path"] for f in s["conflicts"]}  # 任意のパスを触らせない。いま競合と報告されているものだけ
    if bad:
        raise UserError("resolve_not_conflict", files=", ".join(sorted(bad)[:3]))
    stage = "2" if side == "ours" else "3"
    keep, remove = [], []
    for p in sorted(set(paths)):
        (keep if stage in unmerged_stages(p) else remove).append(p)
    step = 200
    for i in range(0, len(keep), step):
        part = keep[i:i + step]
        run_git_write("checkout", "--" + side, "--", *part, literal=True)
        run_git_write("add", "--", *part, literal=True)
    for i in range(0, len(remove), step):
        run_git_write("rm", "-q", "-f", "--", *remove[i:i + step], literal=True)
    return {"ok": True, "message": tr("resolved", n=len(keep) + len(remove), side=side)}


def paths_in_head(paths):
    """paths のうち HEAD に存在するもの。"""
    found = set()
    if not has_head():
        return found
    for i in range(0, len(paths), 200):
        out = git_bytes("ls-tree", "-r", "-z", "--name-only", "HEAD", "--", *paths[i:i + 200], literal=True)
        found.update(x for x in out.decode("utf-8", errors="replace").split("\0") if x)
    return found


def api_discard(body):
    """選んだファイルの変更を破棄する (取り消せない)。追跡中のファイルはステージ済み・未ステージとも HEAD の状態に戻し、
    HEAD にない新規ファイル (ステージ済み) は削除し、未追跡ファイルは削除する。競合中のファイルと、マージなどの途中は対象外。"""
    if not WIP_ENABLED:
        raise UserError("wip_disabled")
    if is_merging() or is_rebasing() or is_cherry_picking() or is_reverting():
        raise UserError("discard_blocked")
    paths = body.get("paths")
    if not isinstance(paths, list) or not paths or not all(isinstance(p, str) for p in paths):
        raise UserError("select_files")
    checked = set(paths)
    s = parse_status(read_status_raw(), limit=10 ** 9)
    conflict = checked & {f["path"] for f in s["conflicts"]}
    if conflict:
        raise UserError("discard_conflict", files=", ".join(sorted(conflict)[:3]))
    untracked = {f["path"] for f in s["untracked"]}
    old_of = {f["path"]: f.get("old_path") for f in s["staged"]}
    known = untracked | {f["path"] for k in ("staged", "unstaged") for f in s[k]}
    bad = checked - known  # 任意のパスを消させない。いま変更ありと報告されているものだけ
    if bad:
        raise UserError("files_unchanged", files=", ".join(sorted(bad)[:3]))
    tracked = sorted(checked - untracked)
    in_head = paths_in_head(tracked)
    clean = sorted(checked & untracked)
    remove, restore = [], set()
    for p in tracked:
        if old_of.get(p):  # リネームは元のパスも戻す
            restore.add(old_of[p])
        (restore.add(p) if p in in_head else remove.append(p))
    step = 200
    for i in range(0, len(remove), step):  # HEAD にないファイル (git add 済みの新規ファイル): インデックスと作業ツリーから消す
        run_git_write("rm", "-q", "-f", "--", *remove[i:i + step], literal=True)
    restore = sorted(restore)
    for i in range(0, len(restore), step):
        run_git_write("checkout", "HEAD", "--", *restore[i:i + step], literal=True)
    for i in range(0, len(clean), step):
        run_git_write("clean", "-f", "-q", "--", *clean[i:i + step], literal=True)
    return {"ok": True, "message": tr("discarded", n=len(checked))}


def api_branch_rename(body):
    """ローカルブランチの名前を変える (git branch -m)。リモートのブランチは変わらない。"""
    old = str(body.get("old", "")).strip()
    new = str(body.get("new", "")).strip()
    if not old or "\0" in old:
        raise UserError("branch_name_invalid")
    check_ref_name("branch", new)
    exists = lambda name: subprocess.run(["git", "-C", REPO, "show-ref", "--verify", "--quiet", "refs/heads/" + name],
                                         capture_output=True).returncode == 0
    if not exists(old):
        raise UserError("ref_not_exist", name=repr(old))
    if old == new:
        raise UserError("branch_rename_same")
    if exists(new):
        raise UserError("branch_exists", name=repr(new))
    has_remote = bool(branch_config(old, "remote"))
    run_git_write("branch", "-m", "--", old, new)
    return {"ok": True, "message": tr("branch_renamed_remote" if has_remote else "branch_renamed", old=old, new=new)}


REBASE_I_MAX = 200  # 対話的リベースで一度に扱うコミット数の上限
REBASE_I_ACTIONS = ("pick", "reword", "squash", "fixup", "drop")


def rebase_todo_hashes(base):
    """git rebase -i <base> が最初に作る todo と同じコミット列 (古い順)。"""
    out = git("rev-list", "--reverse", "--topo-order", "--right-only", "--cherry-pick",
              "--no-merges", base + "...HEAD")
    return out.split()


def check_rebase_i_range(base):
    """対話的リベースできる範囲か調べて、対象コミットのハッシュ (古い順) を返す。"""
    r = subprocess.run(["git", "-C", REPO, "rev-list", "--count", "--merges", base + "..HEAD"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode == 0 and r.stdout.strip() not in ("", "0"):
        raise UserError("ri_merges")
    hashes = rebase_todo_hashes(base)
    if not hashes:
        raise UserError("ri_nothing", base=base[:7])
    if len(hashes) > REBASE_I_MAX:
        raise UserError("ri_too_many", n=len(hashes), max=REBASE_I_MAX)
    return hashes


def api_rebase_todo(q):
    """対話的リベースの対象コミット一覧 (画面の編集ダイアログに出す)。"""
    if not HAS_WORKTREE:
        raise UserError("rebase_no_worktree")
    base = resolve_commit((q.get("hash") or [""])[0])
    head = api_head()
    if not head["branch"]:
        raise UserError("detached_rebase")
    hashes = check_rebase_i_range(base)
    out = git("log", "--no-walk=unsorted", "--date=short", "--format=%H%x1f%an%x1f%ad%x1f%s%x1f%B%x1e", *hashes)
    commits = []
    for rec in out.split("\x1e"):
        rec = rec.lstrip("\n")
        if not rec:
            continue
        h, an, ad, subj, msg = rec.split("\x1f", 4)
        commits.append({"hash": h, "short": h[:7], "author": an, "date": ad, "subject": subj, "message": msg.strip()})
    return {"base": base, "branch": head["branch"], "commits": commits}


def api_rebase_i(body):
    """git rebase -i 相当。画面で編集した todo (順序と pick/reword/squash/fixup/drop) をそのまま実行する。
    エディタは起こさず、todo は GIT_SEQUENCE_EDITOR で差し替える。reword は、そのコミット (squash/fixup を
    まとめた後) に exec で commit --amend -F を挟んで新しいメッセージにする。
    競合やエラーで止まったときは自動で rebase --abort して元に戻す。"""
    if not HAS_WORKTREE:
        raise UserError("rebase_no_worktree")
    base = resolve_commit(body.get("hash", ""))
    if is_merging() or is_rebasing():
        raise UserError("merge_or_rebase_in_progress")
    head = api_head()
    if not head["branch"]:
        raise UserError("detached_rebase")
    if not has_head():
        raise UserError("rebase_no_commits")
    items = body.get("todo")
    if not isinstance(items, list) or not items or not all(isinstance(i, dict) for i in items):
        raise UserError("todo_invalid")
    todo = []
    for it in items:
        act, h = it.get("action"), str(it.get("hash", ""))
        if act not in REBASE_I_ACTIONS or not re.fullmatch(r"[0-9a-f]{40}", h):
            raise UserError("todo_invalid")
        msg = str(it.get("message") or "").strip()
        if act == "reword" and not msg:
            raise UserError("ri_reword_msg_required", hash=h[:7])
        todo.append((act, h, msg))
    expected = check_rebase_i_range(base)
    if sorted(h for _, h, _ in todo) != sorted(expected):
        raise UserError("ri_changed")
    kept = [t for t in todo if t[0] != "drop"]
    if kept and kept[0][0] in ("squash", "fixup"):
        raise UserError("ri_first_squash")

    def fwd(path):  # sh から使うので区切りは / にそろえる (Windows 対策)
        return path.replace("\\", "/")

    before = git("rev-parse", "HEAD").strip()
    tmp = tempfile.mkdtemp(prefix="git_graph_rebase_")
    try:
        lines, pending = [], None  # pending: 直前のグループの reword 用 exec 行
        for act, h, msg in todo:
            if act in ("pick", "reword"):
                if pending:
                    lines.append(pending)
                    pending = None
                lines.append(f"pick {h}")
                if act == "reword":
                    mp = os.path.join(tmp, f"msg{len(lines)}.txt")
                    with open(mp, "w", encoding="utf-8", newline="\n") as f:
                        f.write(msg + "\n")
                    pending = f'exec git commit --amend -q -F "{fwd(mp)}"'
            else:  # squash / fixup / drop
                lines.append(f"{act} {h}")
        if pending:
            lines.append(pending)
        todo_path = os.path.join(tmp, "todo.txt")
        with open(todo_path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
        script = os.path.join(tmp, "seq_editor.py")
        with open(script, "w", encoding="utf-8") as f:
            f.write("import shutil, sys\nshutil.copyfile(sys.argv[1], sys.argv[2])\n")  # argv[2] が git の todo
        env = dict(os.environ, GIT_EDITOR=":", GIT_TERMINAL_PROMPT="0",
                   GIT_SEQUENCE_EDITOR=f'"{fwd(sys.executable)}" "{fwd(script)}" "{fwd(todo_path)}"')
        r = subprocess.run(["git", "-C", REPO, "rebase", "-i", base], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env)
        out = (r.stdout + r.stderr).strip()
        if r.returncode != 0:
            if is_rebasing():  # 競合など → 元に戻す
                subprocess.run(["git", "-C", REPO, "rebase", "--abort"], capture_output=True)
                tail = "\n".join(out.splitlines()[-6:])
                raise UserError("ri_aborted", tail=tail)
            raise ValueError(out or tr("rebase_failed"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if git("rev-parse", "HEAD").strip() == before:
        return {"ok": True, "message": tr("ri_noop", branch=head["branch"])}
    n = Counter(a for a, _, _ in todo)
    parts = [tr(key, n=n[k]) for k, key in (("reword", "ri_part_reword"), ("squash", "ri_part_squash"),
                                           ("fixup", "ri_part_fixup"), ("drop", "ri_part_drop")) if n[k]]
    if parts:
        return {"ok": True, "message": tr("ri_done_parts", branch=head["branch"], base=base[:7], parts=" / ".join(parts))}
    return {"ok": True, "message": tr("ri_done", branch=head["branch"], base=base[:7])}


# --------------------------------------------------------------------------
# リモートとの通信 (fetch / pull / push)
# --------------------------------------------------------------------------
NET_TIMEOUT = 300  # 秒。認証待ちなどで固まったままロックを握り続けないようにする
HTTP_AUTH_CACHE = {}  # アプリ実行中だけ保持する remote 名 -> {username, password}


def run_git_net(*args, timeout=None, auth=None):
    """リモートと通信する git を実行する。必要なら一時的な HTTP 認証用 askpass を使う。"""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    timeout = timeout or NET_TIMEOUT
    askpass_dir = None
    if auth:
        if not isinstance(auth, dict) or not isinstance(auth.get("username", ""), str) or not isinstance(auth.get("password", ""), str):
            raise UserError("auth_invalid")
        askpass_dir = tempfile.mkdtemp(prefix="git_graph_askpass_")
        askpass = os.path.join(askpass_dir, "askpass.py")
        with open(askpass, "w", encoding="utf-8") as f:
            f.write("#!/usr/bin/env python3\nimport os, sys\np = (sys.argv[1] if len(sys.argv) > 1 else '').lower()\nprint(os.environ.get('GIT_GRAPH_HTTP_USERNAME' if 'username' in p or 'user' in p else 'GIT_GRAPH_HTTP_PASSWORD', ''))\n")
        os.chmod(askpass, 0o700)
        env.update(GIT_ASKPASS=askpass,
                   GIT_GRAPH_HTTP_USERNAME=auth.get("username", ""),
                   GIT_GRAPH_HTTP_PASSWORD=auth.get("password", ""))
    try:
        return subprocess.run(["git", "-C", REPO, *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env, timeout=timeout,
                              stdin=subprocess.DEVNULL, start_new_session=True)  # 制御端末を切り離し ssh が /dev/tty に尋ねないように
    except subprocess.TimeoutExpired:
        raise UserError("net_timeout", cmd=args[0], sec=timeout)
    finally:
        if askpass_dir:
            shutil.rmtree(askpass_dir, ignore_errors=True)


def net_error(r, fallback):
    text = (r.stderr or r.stdout).strip()
    text = "\n".join(l for l in text.splitlines() if not l.startswith("hint:")).strip()  # 長い hint は省く
    return text or fallback


def is_http_auth_error(r):
    text = ((r.stderr or "") + "\n" + (r.stdout or "")).lower()
    return any(s in text for s in (
        "authentication failed", "invalid username or token", "could not read username",
        "terminal prompts disabled", "http 401", "http 403",
    ))


def auth_for_remote(remote, body):
    """明示された認証情報を優先し、なければアプリ実行中のキャッシュを使う。"""
    if body.get("auth") and (body.get("auth_remote") is None or body.get("auth_remote") == remote):
        return body["auth"]
    return HTTP_AUTH_CACHE.get(remote)


def remember_auth(remote, auth):
    if auth:
        HTTP_AUTH_CACHE[remote] = {"username": auth.get("username", ""), "password": auth.get("password", "")}


def refs_snapshot():
    r = subprocess.run(["git", "-C", REPO, "for-each-ref", "--format=%(objectname) %(refname)", "refs/remotes", "refs/tags"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return {name: oid for oid, _, name in (l.partition(" ") for l in r.stdout.splitlines())}


def count_changed_refs(before, after):
    return sum(1 for k in set(before) | set(after) if before.get(k) != after.get(k))


def upstream_of_head():
    r = subprocess.run(["git", "-C", REPO, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout.strip() if r.returncode == 0 else None


def api_fetch(body):
    remotes = git("remote").split()
    before = refs_snapshot()
    errors = []
    for remote in remotes:
        args = ["fetch", remote]
        if body.get("prune") is True:
            args.append("--prune")
        auth = auth_for_remote(remote, body)
        r = run_git_net(*args, auth=auth)
        if r.returncode != 0:
            if is_http_auth_error(r) and (not body.get("auth_retry") or body.get("auth_remote") != remote):
                return {"auth_required": True, "auth_remote": remote}
            if body.get("auth_retry") and body.get("auth_remote") == remote:
                HTTP_AUTH_CACHE.pop(remote, None)
            errors.append(r)
        else:
            remember_auth(remote, auth)
    n = count_changed_refs(before, refs_snapshot())
    if errors:  # 一部のリモートだけ失敗しても、残りのリモートは取得する
        raise ValueError(net_error(errors[0], tr("fetch_failed")) + (tr("fetch_partial", n=n) if n else ""))
    return {"ok": True, "message": tr("fetch_done", n=n) if n else tr("fetch_done_none")}


def list_remotes():
    """登録済みのリモート [{name, url}] (url は fetch 用)。"""
    out = {}
    for line in git("remote", "-v").splitlines():
        m = re.fullmatch(r"(\S+)\t(.*) \((fetch|push)\)", line)
        if m and (m.group(3) == "fetch" or m.group(1) not in out):
            out[m.group(1)] = m.group(2)
    for name in git("remote").split():  # url が無いリモートも出す
        out.setdefault(name, "")
    return [{"name": n, "url": u} for n, u in out.items()]


def api_remotes(q):
    return {"remotes": list_remotes()}


def check_remote_name(name):
    if not isinstance(name, str) or not name or name.startswith("-") or "\0" in name or name != name.strip():
        raise UserError("remote_name_invalid")
    r = subprocess.run(["git", "-C", REPO, "check-ref-format", "refs/remotes/" + name + "/HEAD"], capture_output=True)
    if r.returncode != 0:
        raise UserError("remote_name_unusable", name=repr(name))


def api_remote_add(body):
    """リモートを追加する。fetch:true なら続けて fetch する (fetch に失敗してもリモートは追加済み)。"""
    name, url = body.get("name"), body.get("url")
    check_remote_name(name)
    if not isinstance(url, str) or not url.strip() or "\0" in url or "\n" in url:
        raise UserError("url_invalid")
    url = url.strip()
    if url.startswith("-") or re.match(r"(ext|fd)::", url):  # オプション注入と、任意コマンドを実行できる転送方式を避ける
        raise UserError("url_unusable")
    if name in {r["name"] for r in list_remotes()}:
        raise UserError("remote_exists", name=name)
    run_git_write("remote", "add", "--", name, url)
    msg = tr("remote_added", name=name)
    if body.get("fetch") is True:
        r = run_git_net("fetch", name)
        if r.returncode != 0:
            return {"ok": True, "fetch_failed": True,
                    "message": tr("remote_added_fetch_failed", name=name, error=net_error(r, tr("fetch_failed")))}
        msg = tr("remote_added_fetched", name=name)
    return {"ok": True, "message": msg}


def api_remote_remove(body):
    """リモートを削除する (リモート追跡ブランチと設定も消える。リモート側は変わらない)。"""
    name = body.get("name")
    if not isinstance(name, str) or name not in {r["name"] for r in list_remotes()}:  # 登録済みの名前だけ許可
        raise UserError("remote_not_found")
    run_git_write("remote", "remove", "--", name)
    return {"ok": True, "message": tr("remote_removed", name=name)}


def api_pull(body):
    """現在のブランチに upstream の変更を取り込む。
    merge で競合したときはマージ途中で止める (WIP で解決)。rebase で競合したときは中止して元に戻す。"""
    if not HAS_WORKTREE:
        raise UserError("pull_no_worktree")
    mode = body.get("mode", "ff-only")
    flags = {"ff-only": ["--ff-only"], "merge": ["--no-rebase", "--no-edit"], "rebase": ["--rebase"]}.get(str(mode))
    if flags is None:
        raise UserError("pull_mode_invalid")
    if is_merging() or is_rebasing():
        raise UserError("merge_or_rebase_in_progress")
    if not has_head():
        raise UserError("pull_no_commits")
    branch = api_head()["branch"]
    if not branch:
        raise UserError("detached_pull")
    up = upstream_of_head()
    if not up:
        raise UserError("no_upstream", branch=branch)
    remote = branch_config(branch, "remote")
    before = git("rev-parse", "HEAD").strip()
    auth = auth_for_remote(remote, body) if remote and remote != "." else None
    r = run_git_net("pull", *flags, auth=auth)
    if r.returncode != 0:
        if is_http_auth_error(r) and (not body.get("auth_retry") or body.get("auth_remote") != remote):
            return {"auth_required": True, "auth_remote": remote}
        if body.get("auth_retry") and body.get("auth_remote") == remote:
            HTTP_AUTH_CACHE.pop(remote, None)
        if is_merging():  # 競合 (自動マージできなかった)
            return {"ok": True, "conflict": True,
                    "message": tr("merge_conflict", target=up)}
        if is_rebasing():  # 競合 → 元に戻す
            subprocess.run(["git", "-C", REPO, "rebase", "--abort"], capture_output=True)
            raise UserError("rebase_conflict", target=up, cmd="git pull --rebase")
        raise ValueError(net_error(r, tr("pull_failed")) + (tr("pull_diverged_hint") if mode == "ff-only" else ""))
    if remote and remote != ".":
        remember_auth(remote, auth)
    if git("rev-parse", "HEAD").strip() == before:
        return {"ok": True, "message": tr("pull_uptodate", branch=branch)}
    return {"ok": True, "message": tr("pull_done", branch=branch, up=up)}


def branch_config(branch, key):
    r = subprocess.run(["git", "-C", REPO, "config", "--get", f"branch.{branch}.{key}"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout.strip() if r.returncode == 0 else None


def git_config(key):
    r = subprocess.run(["git", "-C", REPO, "config", "--get", key],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout.strip() if r.returncode == 0 else None


def api_push(body):
    """ブランチを push する。body.branch があればそのローカルブランチ (チェックアウト中でなくてもよい)、
    なければ現在のブランチ。body.remote が指定された場合は、その remote の同名ブランチへ push する。"""
    if not has_head():
        raise UserError("push_no_commits")
    branch = body.get("branch")
    if branch:
        if not isinstance(branch, str) or branch.startswith("-"):
            raise UserError("branch_name_invalid")
        if subprocess.run(["git", "-C", REPO, "rev-parse", "-q", "--verify", f"refs/heads/{branch}"],
                          capture_output=True).returncode != 0:
            raise UserError("local_branch_missing", branch=branch)
    else:
        branch = api_head()["branch"]
        if not branch:
            raise UserError("detached_push")
    args = ["push"]
    if body.get("force") is True:
        args.append("--force-with-lease")
    remotes = git("remote").split()
    if not remotes:
        raise UserError("no_remotes")
    upstream_remote, merge = branch_config(branch, "remote"), branch_config(branch, "merge")
    has_upstream = bool(upstream_remote and upstream_remote != "." and merge)
    # push 先: branch.<名前>.pushRemote > remote.pushDefault (git の優先順位と同じ)
    push_remote = branch_config(branch, "pushRemote") or git_config("remote.pushDefault")
    selected_remote = body.get("remote")
    if selected_remote is not None:
        if not isinstance(selected_remote, str) or selected_remote not in remotes:
            raise UserError("remote_unregistered", remote=repr(selected_remote))
        remote = selected_remote
        remote_branch = body.get("remote_branch")
        if remote_branch is None:
            remote_branch = merge[len("refs/heads/"):] if has_upstream and remote == upstream_remote and merge.startswith("refs/heads/") else branch
        if not isinstance(remote_branch, str) or not remote_branch:
            raise UserError("branch_name_invalid")
        check_ref_name("branch", remote_branch)
        dest = f"refs/heads/{remote_branch}"
        args += (["-u"] if not has_upstream else []) + [remote, f"refs/heads/{branch}:{dest}"]
    elif push_remote:
        if push_remote not in remotes:
            raise UserError("push_remote_unregistered", remote=repr(push_remote))
        remote = push_remote
        # upstream と同じリモートなら upstream のブランチ名へ、別のリモート (fork など) なら同名のブランチへ
        dest = merge if (has_upstream and push_remote == upstream_remote) else f"refs/heads/{branch}"
        args += [remote, f"refs/heads/{branch}:{dest}"]
    elif has_upstream:
        remote = upstream_remote
        args += [remote, f"refs/heads/{branch}:{merge}"]  # upstream が別名でもその先へ push する
    else:
        default = "origin" if "origin" in remotes else remotes[0]
        chosen = body.get("remote")
        if body.get("set_upstream") is not True:
            return {"needs_upstream": True, "remote": default, "remotes": remotes, "branch": branch}
        if chosen is None:
            chosen = default
        if not isinstance(chosen, str) or chosen not in remotes:  # 登録済みの名前だけ許可 (オプション注入も防ぐ)
            raise UserError("remote_unregistered", remote=repr(chosen))
        remote = chosen
        args += ["-u", remote, f"refs/heads/{branch}:refs/heads/{branch}"]
    before = refs_snapshot()
    auth = auth_for_remote(remote, body)
    r = run_git_net(*args, auth=auth)
    if r.returncode != 0:
        if not body.get("auth_retry") and is_http_auth_error(r):
            return {"auth_required": True, "auth_remote": remote}
        if body.get("auth_retry"):
            HTTP_AUTH_CACHE.pop(remote, None)
        raise ValueError(net_error(r, tr("push_failed")))
    remember_auth(remote, auth)
    changed = count_changed_refs(before, refs_snapshot()) > 0
    where = f" ({remote})" if len(remotes) > 1 else ""
    return {"ok": True, "message": tr("pushed" if changed else "pushed_nothing", branch=branch, where=where)}


def api_push_tag(body):
    """タグを push する。push 先のリモートが決まらない (複数あって pushDefault も無い) ときは、選択肢を返す。"""
    tag = body.get("tag")
    if not isinstance(tag, str) or not tag or tag.startswith("-"):
        raise UserError("tag_name_invalid")
    if subprocess.run(["git", "-C", REPO, "rev-parse", "-q", "--verify", f"refs/tags/{tag}"],
                      capture_output=True).returncode != 0:
        raise UserError("tag_missing", tag=tag)
    remotes = git("remote").split()
    if not remotes:
        raise UserError("no_remotes")
    chosen = body.get("remote")
    if chosen is None:
        default = git_config("remote.pushDefault")
        if len(remotes) == 1:
            chosen = remotes[0]
        elif default in remotes:
            chosen = default
        else:
            return {"needs_remote": True, "tag": tag, "remotes": remotes,
                    "remote": "origin" if "origin" in remotes else remotes[0]}
    if not isinstance(chosen, str) or chosen not in remotes:  # 登録済みの名前だけ許可 (オプション注入も防ぐ)
        raise UserError("remote_unregistered", remote=repr(chosen))
    auth = auth_for_remote(chosen, body)
    r = run_git_net("push", chosen, f"refs/tags/{tag}:refs/tags/{tag}", auth=auth)
    if r.returncode != 0:
        if not body.get("auth_retry") and is_http_auth_error(r):
            return {"auth_required": True, "auth_remote": chosen}
        if body.get("auth_retry"):
            HTTP_AUTH_CACHE.pop(chosen, None)
        raise ValueError(net_error(r, tr("push_tag_failed")))
    remember_auth(chosen, auth)
    where = f" ({chosen})" if len(remotes) > 1 else ""
    up_to_date = "up-to-date" in (r.stderr + r.stdout).lower() or "up to date" in (r.stderr + r.stdout).lower()
    return {"ok": True, "message": tr("pushed_tag_nothing" if up_to_date else "pushed_tag", tag=tag, where=where)}


POST_ROUTES = {"/api/stash": api_stash, "/api/rebase": api_rebase, "/api/rebase-range": api_rebase_range, "/api/rebase-i": api_rebase_i, "/api/cherry-pick": api_cherry_pick, "/api/revert": api_revert, "/api/abort": api_abort, "/api/resolve": api_resolve, "/api/discard": api_discard, "/api/branch-rename": api_branch_rename, "/api/merge": api_merge, "/api/branch": api_branch, "/api/tag": api_tag, "/api/ref-delete": api_delete_ref,
               "/api/checkout": api_checkout, "/api/reset": api_reset, "/api/wip-commit": api_wip_commit,
               "/api/fetch": api_fetch, "/api/pull": api_pull, "/api/push": api_push, "/api/push-tag": api_push_tag, "/api/file-copy": api_file_copy,
               "/api/remote-add": api_remote_add, "/api/remote-remove": api_remote_remove}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, ctype, body):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path == "/":
                self._send(200, "text/html", render_main_page())
            elif u.path == "/api/log":
                self._send(200, "application/json", json.dumps(api_log(q), ensure_ascii=False))
            elif u.path == "/api/stash-list":
                self._send(200, "application/json", json.dumps(api_stash_list(q), ensure_ascii=False))
            elif u.path == "/api/reflog":
                self._send(200, "application/json", json.dumps(api_reflog(q), ensure_ascii=False))
            elif u.path == "/api/status":
                self._send(200, "application/json", json.dumps(api_status(q), ensure_ascii=False))
            elif u.path == "/api/remotes":
                self._send(200, "application/json", json.dumps(api_remotes(q), ensure_ascii=False))
            elif u.path == "/api/graph":
                self._send(200, "application/json", graph_payload())
            elif u.path == "/api/rebase-todo":
                self._send(200, "application/json", json.dumps(api_rebase_todo(q), ensure_ascii=False))
            elif u.path == "/api/head":
                self._send(200, "application/json", json.dumps(api_head(q), ensure_ascii=False))
            elif u.path == "/api/compare":
                self._send(200, "application/json", json.dumps(api_compare(q), ensure_ascii=False))
            elif u.path == "/api/merge-base":
                self._send(200, "application/json", json.dumps(api_merge_base(q), ensure_ascii=False))
            elif u.path == "/api/commit":
                self._send(200, "application/json", json.dumps(api_commit(q), ensure_ascii=False))
            elif u.path == "/api/tree":
                self._send(200, "application/json", json.dumps(api_tree(q), ensure_ascii=False))
            elif u.path == "/api/file":
                self._send(200, "application/json", json.dumps(api_file(q), ensure_ascii=False))
            elif u.path == "/files":
                h = resolve_commit(q.get("hash", [""])[0])
                self._send(200, "text/html", render_files_page().replace("__HASH__", html.escape(h)))
            elif u.path == "/diff":
                self._send(200, "text/html", render_diff_page(q))
            else:
                self._send(404, "text/plain", "not found")
        except ValueError as e:
            self._send(400, "text/plain", str(e))
        except FileNotFoundError:
            self._send(500, "text/plain", tr("dot_missing"))
        except subprocess.CalledProcessError as e:
            self._send(500, "text/plain", f"command failed: {e.cmd}\n{e.stderr or ''}")

    def _check_post(self):
        """他サイトのページからの書き込みを防ぐ (CSRF / DNS rebinding 対策)。"""
        if not WRITABLE:
            raise Forbidden(tr("write_disabled"))
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        if ctype != "application/json":  # 他サイトからは preflight が必要になるため送れない
            raise UserError("content_type_json")
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        if origin is not None and origin != "http://" + host:
            raise Forbidden(tr("origin_mismatch"))
        if is_loopback(OPTS.host):
            name = host if host.endswith("]") or ":" not in host else host.rsplit(":", 1)[0]
            if name not in ("127.0.0.1", "localhost", "[::1]"):
                raise Forbidden(tr("host_invalid"))

    def do_POST(self):
        try:
            route = POST_ROUTES.get(urlparse(self.path).path)
            if route is None:
                self._send(404, "text/plain", "not found")
                return
            self._check_post()
            n = int(self.headers.get("Content-Length") or 0)
            if n > 100000:
                raise ValueError("body too large")
            body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("bad body")
            with _write_lock:
                res = route(body)
            self._send(200, "application/json", json.dumps(res, ensure_ascii=False))
        except Forbidden as e:
            self._send(403, "text/plain", str(e))
        except ValueError as e:  # JSON の不正も含む
            self._send(400, "text/plain", str(e))

    def log_message(self, fmt, *args):  # アクセスログは出さない
        pass


def main():
    global REPO, OPTS, UI_LANG, WRITABLE, WIP_ENABLED, HAS_WORKTREE, PROJECT_NAME
    pre = argparse.ArgumentParser(add_help=False, allow_abbrev=False)  # --lan を --lang の省略形と誤認しない  # --help の文言も言語に合わせるため、先に --lang だけ読む
    pre.add_argument("--lang", choices=sorted(TRANSLATIONS))
    UI_LANG = detect_language(pre.parse_known_args()[0].lang)
    ap = argparse.ArgumentParser(description="git commit graph server")
    ap.add_argument("revs", nargs="*", help=tr("cli_help_revs"))
    ap.add_argument("-C", dest="repo", default=".", help=tr("cli_help_repo"))
    ap.add_argument("--host", default="127.0.0.1", help=tr("cli_help_host"))
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--open", action="store_true", help=tr("cli_help_open"))
    ap.add_argument("--no-remotes", action="store_true", help=tr("cli_help_no_remotes"))
    ap.add_argument("--no-tags", action="store_true", help=tr("cli_help_no_tags"))
    ap.add_argument("--full-layout", action="store_true", help=tr("cli_help_full_layout"))
    ap.add_argument("--no-count", action="store_true", help=tr("cli_help_no_count"))
    ap.add_argument("--rankdir", default="BT", help=tr("cli_help_rankdir"))
    ap.add_argument("--font", default="Helvetica", help=tr("cli_help_font"))
    ap.add_argument("--dot", action="store_true", help=tr("cli_help_dot"))
    ap.add_argument("--no-wip", action="store_true", help=tr("cli_help_no_wip"))
    ap.add_argument("--read-only", action="store_true", help=tr("cli_help_read_only"))
    ap.add_argument("--allow-write", action="store_true", help=tr("cli_help_allow_write"))
    ap.add_argument("--lan", action="store_true", help=tr("cli_help_lan"))
    ap.add_argument("--lang", choices=sorted(TRANSLATIONS), help=tr("cli_help_lang"))
    ap.add_argument("--check-i18n", action="store_true", help=tr("cli_help_check_i18n"))
    OPTS = ap.parse_args()
    if OPTS.check_i18n:
        sys.exit(check_i18n())
    if OPTS.lan:
        if OPTS.host == "127.0.0.1":  # --host を明示しているときはそちらを優先する
            OPTS.host = "0.0.0.0"
        OPTS.allow_write = True
    REPO = OPTS.repo
    WRITABLE = (not OPTS.read_only) and (is_loopback(OPTS.host) or OPTS.allow_write)

    try:
        git("rev-parse", "--git-dir")
    except (subprocess.CalledProcessError, FileNotFoundError):
        sys.exit(tr("cli_not_a_repo", repo=repr(REPO)))
    # サブディレクトリが指定されても、パスの扱い (status / diff はルート基準) がずれないようルートに揃える
    r = subprocess.run(["git", "-C", REPO, "rev-parse", "--show-toplevel"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    HAS_WORKTREE = r.returncode == 0
    if HAS_WORKTREE:
        REPO = r.stdout.strip()
    WIP_ENABLED = HAS_WORKTREE and not OPTS.no_wip
    PROJECT_NAME = os.path.basename(os.path.abspath(REPO)) or REPO

    if OPTS.dot:
        print(build_graph(OPTS)[0])
        return

    fill_template(HTML_TEMPLATE), fill_template(FILES_PAGE)  # 未定義の翻訳キーがあれば、起動時に KeyError で気づけるようにする
    server = ThreadingHTTPServer((OPTS.host, OPTS.port), Handler)
    url = f"http://{'localhost' if OPTS.host == '0.0.0.0' else OPTS.host}:{OPTS.port}/"  # 0.0.0.0 はブラウザで開く宛先にならない
    print(tr("cli_serving", url=url, state=tr("cli_state_on" if WRITABLE else "cli_state_off")))
    if OPTS.host == "0.0.0.0":
        print(tr("cli_listening", port=OPTS.port))
        if WRITABLE:
            print(tr("cli_lan_warning"))
    if OPTS.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
