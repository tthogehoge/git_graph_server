# 開発者向けガイド

この文書は、`git_graph_server` の構成、開発・テスト方法、変更時の注意点をまとめたものです。アプリの利用方法と CLI オプションは [日本語 README](README_JP.md) を参照してください。

## 開発環境

対応環境は Linux です。実行時に必要なものは Python 3、Git、Graphviz の `dot` です。Python は標準ライブラリのみを使用し、依存パッケージのインストールは不要です。Graphviz はコミットグラフのレイアウトと描画に使われます。

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
4. API ハンドラーは機能別モジュールにあり、`@route` で登録されます。グラフ API は Git 履歴を `git_graph/graph.py` で処理し、画面側の `git_graph/static/app.js` が API を呼んで画面を更新します。

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
| `git_graph/graph.py` | Git の履歴・ref を読み込み、表示対象ノードとエッジを決定し、Graphviz の DOT/SVG 情報を生成。 |
| `git_graph/viewer.py` | ログ、コミット、ツリー、ファイル、差分、ブランチ、タグ、reflog、stash などの参照 API と HTML ページ。 |
| `git_graph/wip.py` | `git status` の解析、作業ツリー差分の表示、未コミット変更を含めた操作。 |
| `git_graph/refops.py` | ブランチ・タグの作成/削除/rename、checkout、reset、stash、変更の破棄。 |
| `git_graph/rebase.py` | merge、rebase、cherry-pick、revert、rebase 中断、競合解決と履歴上の対応コミット検索。 |
| `git_graph/remote.py` | remote の一覧・管理、fetch、pull、push、タグ push。 |
| `git_graph/i18n.py` | 翻訳辞書の読み込み、言語選択、HTML/JavaScript テンプレート展開、翻訳整合性の検査。 |
| `git_graph/locales/ja.json`、`git_graph/locales/en.json` | 日本語・英語の翻訳辞書。 |
| `git_graph/static/index.html` | メイン画面の HTML 骨格。 |
| `git_graph/static/app.js` | グラフ、ログ、詳細、アクションなどブラウザー側の動作。翻訳辞書は起動時に埋め込まれます。 |
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

- 画面の構造は `git_graph/static/index.html`、動作は `git_graph/static/app.js`、見た目は `git_graph/static/app.css` にあります。
- `{{translation_key}}` は HTML テンプレート用、JavaScript の `t("translation_key")` はブラウザー側の文言用です。
- ユーザー向け文言を追加・変更したら `git_graph/locales/ja.json` と `git_graph/locales/en.json` の両方を更新し、`--check-i18n` を実行してください。
- API のエラーには既存の `UserError` と翻訳キーを使い、画面に返すメッセージを一貫させてください。
- `git_graph/i18n.py` は静的ファイルをモジュール読み込み時に読み込みます。画面テンプレートや翻訳を編集した後は、サーバーを再起動して反映を確認してください。
- HTML に動的値を挿入するときは、既存の `html.escape` 等のエスケープ方法を使ってください。信頼できない値を `innerHTML` やテンプレートへそのまま連結しないでください。

### Git 操作と安全性

- Git への入力は引数配列で渡し、シェル文字列として組み立てないでください。Git の引数もオプションとして誤解される可能性があるため、既存処理と同様に ref 名・コミットハッシュ・パスを検証してください。
- パスを扱う処理では `validate_file_path` や `git_graph/gitrun.py` の literal path 対応を確認し、リポジトリ外のファイルへアクセスしないようにしてください。
- 読み取りには `git()` / `git_bytes()`、エラーをユーザーへ返す書き込みには `run_git_write()`、ロケール依存の出力を解析する場合には `git_c_locale()` を用途に合わせて使ってください。リモート通信には `run_git_net()` のタイムアウト・認証処理を使います。
- 履歴を書き換える・ref を移動する処理では、対象・副作用・失敗時の状態を明確にし、関連する既存の排他制御やエラー処理に従ってください。履歴操作の画面表示だけでなく、途中失敗・競合・中断の経路も確認してください。
- POST API は `git_graph/server.py` の書き込み制御を通します。新しい書き込み API を別経路で呼び出す設計にしないでください。書き込みは localhost でも明示的な共通チェックを経て実行されます。
- `--lan` や `--allow-write` は認証を提供しません。ネットワーク公開を前提とした機能を追加する場合は、既存の Host/Origin 検査だけで十分とみなさず、設計とアクセス制御を改めて検討してください。

## 変更後の確認

変更内容に応じて、関連するテストを実行してください。Git の読み取り・書き込み API を変更した場合は、一時 Git リポジトリを用いたテストで、成功時の結果とエラー時の状態の両方を確認します。翻訳またはテンプレートを変更した場合は翻訳検査を実行します。

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -v
python3 git_graph_server.py --check-i18n
```

UI/API を変更した場合は、テストに加えて専用のテスト用リポジトリでアプリを起動し、ブラウザー上の表示と操作、開発者ツールのコンソールやネットワークエラーも確認してください。グラフ生成を変更した場合は、直線的な履歴だけでなく、分岐・merge・タグ・remote ref を含む履歴も確認します。最後に `git diff --check` を実行し、変更差分に不要な空白や意図しないファイルが含まれていないことを確認してください。
