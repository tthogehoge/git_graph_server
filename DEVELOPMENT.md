# 開発者向けガイド

この文書は、`git_graph_server` の構成、開発・テスト方法、変更時の注意点をまとめたものです。アプリの利用方法と CLI オプションは [日本語 README](README_JP.md) を参照してください。

## 開発環境

対応環境は Linux です。実行時に必要なものは Python 3、[pygit2](https://www.pygit2.org/)（`pip install pygit2`）、Git、Graphviz の `dot` です。読み取りのホットパス（グラフモデル・blob・commit 解決など）は pygit2 を使い、書き込みや複雑な porcelain は Git CLI にフォールバックします。Graphviz はコミットグラフのレイアウトと描画に使われます。

開発対象のソースツリーと、画面に表示する Git リポジトリは分けて指定できます。引数なしで起動するとカレントディレクトリが表示対象です。開発中は専用のテスト用リポジトリを `-C` で指定すると、作業ツリーへの誤操作を避けられます。

```sh
python3 git_graph_server.py -C /path/to/test-repo --open
```

ポートを固定したい場合は `--port` を指定します。起動時に翻訳検査を行う場合は `--check-i18n`、Graphviz に渡す DOT テキストだけを確認する場合は `--dot` を使います。全オプションは `python3 git_graph_server.py --help` で確認できます。

```sh
python3 git_graph_server.py -C /path/to/test-repo --port 8080
python3 git_graph_server.py --check-i18n
```

## アプリケーションの流れ

1. `git_graph_server.py` が `git_graph/server.py` の `main()` を呼び出します。`main()` は CLI 引数と言語を決定し、指定された Git リポジトリのルートを特定します。
2. `git_graph/app_state.py` に対象リポジトリや起動設定を設定します。各機能モジュールはこの状態を参照し、`git_graph/gitrun.py` のヘルパーを通じて Git を実行します。
3. `git_graph/server.py` の HTTP ハンドラーが HTML、静的アセット、API リクエストを処理します。ページの HTML/CSS/JavaScript は `git_graph/i18n.py` を通じてテンプレート展開されます。
4. API ハンドラーは機能別モジュールにあり、`@route` で登録されます。グラフ API は Git 履歴を `git_graph/graph.py` で処理し、画面側の `git_graph/static/js/` が API を呼んで画面を更新します。

サーバーは Python の `ThreadingHTTPServer` を使います。POST リクエストのハンドラーは書き込みロック内で実行されるため、書き込み操作同士は直列化されます。状態はプロセス内の共有値であり、複数リポジトリを同じプロセスで同時に操作する構成ではありません。

## テストと翻訳検査

テストは Python 標準の `unittest` で実行します。

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -v
```

Graphviz がインストールされていない環境では、Graphviz に依存するテストはスキップされます。翻訳キーの不足・不一致も確認してください。

```sh
python3 git_graph_server.py --check-i18n
```

現在のテストには、パーサーや入力検証などの単体テスト、ルート登録のテスト、一時ディレクトリに Git リポジトリを作成して実行する API 統合テストがあります。統合テストではグローバルな対象リポジトリを一時リポジトリに切り替えるため、テスト終了時に状態を復元し、一時ディレクトリを破棄する既存パターンに従ってください。書き込み API のテストでは必ず一時リポジトリを使い、開発者の実リポジトリを変更しないでください。

テストを追加したら、まず関連するテストクラスを個別に実行して素早く確認できます。

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_git_graph_server.InputValidationTests -v
```

最後に全テストと翻訳検査を実行します。`test.sh` がある環境では同スクリプトも利用できますが、標準の実行コマンドは上記です。

## 構成

| ファイル / ディレクトリ | 役割 |
| --- | --- |
| `git_graph_server.py` | 起動用エントリーポイント。各モジュールの互換エクスポートも提供します。新しい機能は原則として機能別モジュールに実装します。 |
| `git_graph/server.py` | CLI 引数、共有状態の初期化、HTTP サーバー、ページ/API の GET/POST 処理、書き込み要求の制御。 |
| `git_graph/routing.py` | `@route` による API ルート登録。HTTP メソッドとパスの組み合わせで管理し、重複登録を拒否します。 |
| `git_graph/app_state.py` | 対象リポジトリ、起動オプション、書き込み可否、worktree 状態、HTTP サーバーなどのプロセス共有状態。 |
| `git_graph/gitrun.py` | Git コマンド実行、コミット/ref/ホストの検証、書き込みコマンド、リモート通信と認証情報の処理。 |
| `git_graph/libgit.py` | pygit2 によるリポジトリ読み取り（Repository キャッシュ、rev-list 相当、ref 列挙、blob）。 |
| `git_graph/gitstate.py` | マージ中・HEAD の有無・`git status` / `--numstat` の解析など、リポジトリの状態を調べる共通関数。機能モジュールから共通で使われる低レベルの層。 |
| `git_graph/equivalent.py` | 互換の再公開。本体は `query/equivalent.py`。 |
| `git_graph/engine/` | git 操作エンジン (パッケージ)。リポジトリを変更する git の処理はここに置き、route (`api_*`) は body を読んで engine を呼び、結果を画面用のレスポンスに整える薄い層にします。`__init__.py` は操作の記録と `operation` デコレーターで、`refs.py` (ブランチ・タグ・checkout・reset)、`worktree.py` (stash・破棄・git rm)、`commit.py` (WIP からのコミット)、`remote.py` (fetch・pull・push・リモートの追加と削除。HTTP 認証の指定は `Auth`)、`patch.py` (git apply / git am)、`files.py` (編集したファイルの保存)、`integrate.py` (merge・cherry-pick・revert・競合の解決・中断)、`rebase.py` (rebase・rebase-range・rebase-descendants・rebase-equivalent・対話的リベース)、`tags.py` (リベース時のタグの付け替え)、`dirty.py` (編集中のファイルに邪魔されたときの stash / 破棄) に git の操作があります。リポジトリを変更する git の処理はすべて engine に移してあります。engine の操作は `@operation` で登録し、呼ぶと操作の前後で ref / HEAD / stash 一覧 / ローカル config / 作業ツリーと index の状態を記録して、その差分をメモリ上の履歴に積みます (ファイルには保存せず、再起動すると消えます)。元に戻す・やり直し (`/api/undo`、`/api/redo`、`/api/undo-history`) はこの差分を逆向き・順方向に適用するだけなので、操作ごとに逆操作を書く必要はありません。作業ツリーの退避 (blob / tree) は `GIT_OBJECT_DIRECTORY` を一時ディレクトリにして書き (本物の objects は `GIT_ALTERNATE_OBJECT_DIRECTORIES` で読み取り専用に見せます)、`.git` には objects も ref も残しません。一時ディレクトリは起動時・終了時・再起動時に `cleanup()` で消します。作業ツリーが操作後に変わっているのを確認のうえ上書きして戻したときは、上書きされる現在の状態を履歴側の反対向きの行き先にするので、やり直し (または元に戻す) で戻せます。エンジン内で git を呼ぶときは `_run()` を使ってください (書き込まれるオブジェクトが一時ディレクトリに入ります)。 |
| `git_graph/query/` | git からの読み取り (query)。リポジトリを変更しないデータ取得を置く。`history.py` (log・branches・tags・reflog・commit・compare・diff)、`status.py` (作業ツリー status・WIP 差分)、`files.py` (tree・file・blame)、`equivalent.py` (等価コミット探索)、`graph.py` (残すコミットとエッジのモデル)。HTTP の引数や画面用の文言は知らず、結果は dict で返す。route (`history.py` / `wip.py`) が query を呼ぶ。書き込みは `engine/` に置き、読み取りを engine に混ぜない。 |
| `git_graph/tmpdir.py` | アプリのインスタンスごとの一時ディレクトリ (`git_graph_<PID>_xxxx`)。差分用 (外部差分ツールに渡すファイル。`diff_*`) と undo 用 (退避したオブジェクトと一時 index。`objects_*` / `snap_*` / `restore_*`) の一時ファイルをここにまとめ、終了時に丸ごと消します。異常終了で残ったものは、PID のプロセスがもう居なければ次の起動時に消します (`cleanup_stale`)。 |
| `git_graph/graph.py` | グラフの描画 (DOT/SVG・クラスター) と `/api/graph` route。残すコミットとエッジの決定は `query/graph.py` の `build_model`。 |
| `git_graph/history.py` | ログ・ブランチ・タグ・reflog・stash 一覧・コミット詳細・比較・差分の route。git からの読み取りは `query/history.py` に任せ、ここは query 文字列を読んで JSON を返す薄い層。 |
| `git_graph/external.py` | 外部エディタ / 外部差分ツールの起動 (`/api/open-editor`、`/api/open-diff`)。`--desktop` かつ書き込み有効かつループバックのときだけ動き、設定ファイル (`git_graph_server.json`) のコマンド配列を、シェルを通さずに実行します。コミットやインデックスの内容は読み取り専用の一時ファイルにして渡し、作業ツリーのファイルはそのまま渡します。 |
| `git_graph/textcodec.py` | バイト列の文字コード判別と保存時エンコード。git / HTTP に依存しない。 |
| `git_graph/files.py` | ファイルの route (tree/file/blame は `query/files.py`)、作業ツリーへの保存・コピー、パッチの作成と適用。文字コードは `textcodec.py`。 |
| `git_graph/pages.py` | `/diff`(差分ページ)と `/files`(ファイル表示ページ)の HTML を組み立てます。 |
| `git_graph/wip.py` | WIP の route。status / 差分の読み取りは `query/status.py`、コミットは `engine/commit.py`。 |
| `git_graph/refops.py` | ブランチ・タグの作成/削除/rename、checkout、reset、stash、変更の破棄。 |
| `git_graph/rebase.py` | merge、rebase、cherry-pick、revert、rebase 中断、競合解決と履歴上の対応コミット検索。 |
| `git_graph/remote.py` | remote の一覧・管理、fetch、pull、push、タグ push。 |
| `git_graph/i18n.py` | 翻訳辞書の読み込み、言語選択、HTML/JavaScript テンプレート展開、翻訳整合性の検査。 |
| `git_graph/locales/ja.json`、`git_graph/locales/en.json` | 日本語・英語の翻訳辞書。 |
| `git_graph/static/index.html` | メイン画面の HTML 骨格。 |
| `git_graph/static/files.html` | ファイル表示ページ(`/files`)の HTML。`i18n.py` が読み込みます。 |
| `git_graph/static/diff.css`、`files.css`、`copy.js`、`diff_fold.js`、`diff_reload.js`、`wip_diff_commit.js` | `/diff` ページと `/files` ページで使う CSS / JavaScript。`pages.py` が読み込みます。 |
| `git_graph/static/js/*.js` | ブラウザー側の動作。番号順に連結され、1つの `/static/app.js` として配られます(`01_core` 共通部、`02_log` ログ、`03_detail` 詳細、`04_graph_compare` グラフ更新と比較、`05_actions` メニューと操作、`06_git_ops` rebase / revert / merge など、`07_screens` 画面遷移と初期化、`08_wip` WIP、`09_boot` 起動)。翻訳辞書は起動時に埋め込まれます。 |
| `git_graph/static/app.css` | メイン画面と各ビューのスタイル。 |
| `tests/test_git_graph_server.py` | 単体テスト、API 統合テスト、HTTP/書き込み操作のテスト。 |

## 変更の進め方

### API を追加・変更する

各 API のハンドラーは機能に対応するモジュールに置き、`git_graph/routing.py` の `route` デコレーターで登録します。GET は URL クエリー引数の辞書を、POST は JSON オブジェクトを受け取ります。登録されたハンドラーは `git_graph/server.py` が呼び出すため、新しいルートを `git_graph/server.py` に重ねて個別登録する必要はありません。

```python
from routing import route

@route("/api/example")
def api_example(query):
    return {"value": query.get("value", [""])[0]}
```

POST の場合は `@route("/api/example", method="POST")` を使います。別モジュールに同じ HTTP メソッド・パスを登録すると起動時にエラーになります。新しいハンドラーには、正常系だけでなく不正入力や Git コマンド失敗のテストも追加してください。

GET は JSON を返し、POST も JSON オブジェクトを受け取って JSON を返す設計です。POST のボディサイズ上限や JSON 形式の確認、書き込み可否、Host/Origin の検証は共通処理にあります。各ハンドラーではそれに加えて、必須値の有無・型・許可される値を確認してください。ユーザー入力による失敗は既存の `ValueError` / `UserError` の扱いに合わせます。新たな例外を握りつぶしたり、処理に失敗しているのに成功形式の値を返したりしないでください。

### 画面や翻訳を変更する

- 画面の構造は `git_graph/static/index.html`、動作は `git_graph/static/js/`、見た目は `git_graph/static/app.css` にあります。JavaScript の部品は番号順の1本のスクリプトとして連結されるので、トップレベルの変数や関数は部品をまたいで使えます。新しい部品を足すときは、ファイル名の番号で順序を決めてください。
- `{{translation_key}}` は HTML テンプレート用、JavaScript の `t("translation_key")` はブラウザー側の文言用です。
- ユーザー向け文言を追加・変更したら `git_graph/locales/ja.json` と `git_graph/locales/en.json` の両方を更新し、`--check-i18n` を実行してください。
- API のエラーには既存の `UserError` と翻訳キーを使い、画面に返すメッセージを一貫させてください。
- `git_graph/i18n.py` は静的ファイルをモジュール読み込み時に読み込みます。画面テンプレートや翻訳を編集した後は、サーバーを再起動して反映を確認してください。
- HTML に動的値を挿入するときは、既存の `html.escape` 等のエスケープ方法を使ってください。信頼できない値を `innerHTML` やテンプレートへそのまま連結しないでください。

### Git 操作と安全性

- Git への入力は引数配列で渡し、シェル文字列として組み立てないでください。Git の引数もオプションとして誤解される可能性があるため、既存処理と同様に ref 名・コミットハッシュ・パスを検証してください。
- パスを扱う処理では `validate_file_path` や `git_graph/gitrun.py` の literal path 対応を確認し、リポジトリ外のファイルへアクセスしないようにしてください。
- 履歴の列挙・ref・blob などホットな読み取りは `libgit`（pygit2）を優先します。フォールバックや書き込みには `git()` / `git_bytes()`、エラーをユーザーへ返す書き込みには `run_git_write()`、ロケール依存の出力を解析する場合には `git_c_locale()` を用途に合わせて使ってください。リモート通信には `run_git_net()` のタイムアウト・認証処理を使います。
- 履歴を書き換える・ref を移動する処理では、対象・副作用・失敗時の状態を明確にし、関連する既存の排他制御やエラー処理に従ってください。履歴操作の画面表示だけでなく、途中失敗・競合・中断の経路も確認してください。
- **分離の基準**: git を実行してデータを取る処理は `query/`（または既存の `gitstate`）へ。git を使わない処理（DOT/SVG 描画、HTML 組み立て、文字コード変換など）はそのモジュールに残す。
- **読み取り API の足し方 (query)**: git からデータを取る処理は `git_graph/query/` に関数として書く。関数は引数を受け取って git を実行し、結果を dict で返す (HTTP の query も画面用の文言も知りません。エラーは `UserError` / `ValueError`)。route 側 (`history.py` や `wip.py` など) は query 文字列を読んで query の関数を呼ぶ薄い層にする。書き込み用の `engine` に読み取りを入れない (undo 記録の対象外のため)。
- **書き込み API の足し方 (engine への移行後の形)**: git の処理は `git_graph/engine/` に `@operation("操作名", touches_worktree=..., detail=...)` を付けた関数として書きます。関数は引数を受け取って git を実行し、結果を dict で返します (HTTP の body も画面用の文言も知りません。エラーは `UserError(キー, ...)`、止まったときは `{"blocked": True, ...}` を返します)。route 側は `@route(パス, method="POST", engine=True)` で、body を読んで engine の関数を呼び、`tr()` でメッセージを組み立てます。`engine=True` の route は、操作が自分で履歴に記録するので、`server.py` 側では包みません。履歴に出す文言の翻訳キー (`op_label_*`) は `LABEL_KEYS` と `ja.json` / `en.json` に足してください。engine (書き込み) や query (読み取り) に移し終えた route のモジュール (`refops.py` / `remote.py` / `rebase.py` / `history.py` / `wip.py` / `files.py`) は、`subprocess` や `run_git_write` などを直接使えません (`tests/test_engine.py` の `ROUTE_ONLY_MODULES` に足すと検査されます)。`graph.py` は graphviz の `dot` を `subprocess` で実行するので対象外です。
- **未登録の書き込み API の扱い**: engine の操作を呼ばない POST API は、`git_graph/engine/__init__.py` の `IGNORED` (リポジトリを変更しない API。終了・再起動・外部アプリの起動など) に入れます。どちらにも無い API は、`server.py` が安全側 (作業ツリーも管理外ファイルの名前も記録する「other」) で履歴に記録します。登録漏れは `tests/test_engine.py` が検出します。ファイル保存 (`/api/file-save`) も `engine/files.py` の操作で、git は実行しませんが、作業ツリーを書き換えるので engine に置いています。
- stash の apply / pop が競合したとき、git には中断の仕組みがないので、適用した stash を、メモリ上 (`gitstate._STASH_MARKERS`) に目印として覚えます。ファイルは作りません (`.git` にも一時ディレクトリにも)。アプリを終了すると消え、再起動後は「中断」は出ません (競合そのものは残ります)。未解決の競合ファイルがあるあいだだけ WIP に「中断」が出て (`/api/status` の `stash_conflict`)、中断は stash が触るファイルを HEAD の内容に戻します (stash の適用は、変更先のファイルがきれいなときだけ始まるため、これで適用前の状態に戻ります。stash は残ります)。
- `engine/` の記録 (undo) は ref / HEAD / stash / ローカル config / 作業ツリーと index だけを戻します。それ以外 (リモート側の状態、`.gitignore` で無視されたファイル、git 管理外の設定ファイルなど) は戻せません。
- POST API は `git_graph/server.py` の書き込み制御を通します。新しい書き込み API を別経路で呼び出す設計にしないでください。書き込みは localhost でも明示的な共通チェックを経て実行されます。
- `--lan` や `--allow-write` は認証を提供しません。ネットワーク公開を前提とした機能を追加する場合は、既存の Host/Origin 検査だけで十分とみなさず、設計とアクセス制御を改めて検討してください。

## 変更後の確認

変更内容に応じて、関連するテストを実行してください。Git の読み取り・書き込み API を変更した場合は、一時 Git リポジトリを用いたテストで、成功時の結果とエラー時の状態の両方を確認します。翻訳またはテンプレートを変更した場合は翻訳検査を実行します。

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -v
python3 git_graph_server.py --check-i18n
```

UI/API を変更した場合は、テストに加えて専用のテスト用リポジトリでアプリを起動し、ブラウザー上の表示と操作、開発者ツールのコンソールやネットワークエラーも確認してください。グラフ生成を変更した場合は、直線的な履歴だけでなく、分岐・merge・タグ・remote ref を含む履歴も確認します。最後に `git diff --check` を実行し、変更差分に不要な空白や意図しないファイルが含まれていないことを確認してください。

## モジュールの依存の向き

モジュール間の import は、すべてファイル先頭の通常の import で、循環はありません(関数内での遅延 import は使いません)。依存は次の向きだけです。新しい関数を足すときは、下の層にあるものだけを import してください。互いに必要になったら、共通部分を下の層(`gitstate.py` など)へ移します。

```
app_state, routing
  └ i18n
      └ gitrun / libgit
          ├ textcodec  (文字コード判別。git 非依存)
          ├ files      (route: tree/file/blame は query、保存・パッチはここ)
          └ gitstate
              ├ engine  (書き込み操作の記録と git の処理)
              ├ query   (読み取り: history / status / files / equivalent / graph モデル)
              ├ rebase, refops
              │   ├ remote
              │   └ wip      (route: status は query、commit は engine)
              │       ├ graph
              │       └ history  (route: query.history)
              │           └ pages
server  (すべてを束ねる最上位)
```
