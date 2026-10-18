# git_graph_server

Git のコミット履歴を省略表示する、ローカル用のブラウザ UI です。ログ、コミット詳細、差分を確認でき、Git 操作も画面から実行できます。

## 必要なもの

- Python 3（標準ライブラリのみ）
- Git
- [Graphviz](https://graphviz.org/) の `dot`

## 起動

```sh
python git_graph_server.py --open
```

既定では `http://127.0.0.1:8000/` で起動します。

```sh
python git_graph_server.py -C /path/to/repo --port 8080
python git_graph_server.py main dev
```

対象 rev を省略すると `--all` が対象になります。終了は `Ctrl+C` です。

## 主な機能

- ブランチ、リモート、タグ、HEAD、末端、分岐点を残した見やすいコミットグラフ
- ログ、コミット詳細、ファイル差分、A / B のコミット比較
- ブランチ・タグ作成、削除、checkout、merge、rebase、対話的 rebase、cherry-pick
- stash の一覧・適用・削除、reflog の表示
- fetch、pull（fast-forward / merge / rebase）、push、リモートの追加・削除
- 未コミット変更の WIP 表示、差分確認、選択ファイルの commit / amend

競合が発生した merge / rebase / cherry-pick / 対話的 rebase は自動的に中止して元に戻します。

## 主なオプション

| オプション | 説明 |
| --- | --- |
| `-C PATH` | 対象リポジトリ（既定: カレントディレクトリ） |
| `--host HOST` | 待ち受けアドレス（既定: `127.0.0.1`） |
| `--port PORT` | ポート番号（既定: `8000`） |
| `--open` | 起動後にブラウザで開く |
| `--no-remotes` / `--no-tags` | リモート / タグを非表示 |
| `--no-wip` | 未コミット変更の WIP ノードを非表示 |
| `--no-count` | 省略コミット数を計算しない |
| `--full-layout` | 大きなグラフでも Graphviz の最適化を省略しない |
| `--rankdir DIR` | 向き（`BT` / `TB` / `LR`、既定: `BT`） |
| `--read-only` | 書き込み操作を無効化 |
| `--allow-write` | localhost 以外でも書き込みを許可 |
| `--lan` | `0.0.0.0` で公開し、書き込みも許可 |
| `--dot` | Graphviz の dot を出力して終了 |

## 書き込み操作について

書き込み操作は既定で localhost からのみ有効です。`--read-only` で完全に無効化できます。
作業ツリーに変更がある checkout では、画面から stash または対象ファイルの破棄を選べます。破棄した変更は復元できない場合があります。

## 免責事項

本ソフトウェアは現状有姿で提供されます。利用によって生じたコミット、ブランチ、タグ、stash、作業ファイル、リモートリポジトリ等の消失・破損、その他一切の損害について、作者は責任を負いません。重要なリポジトリでは、事前にバックアップを取り、各操作の内容を確認してから使用してください。

`--lan` や `--allow-write` は認証なしで操作を受け付けます。信頼できるネットワーク以外では使用しないでください。push や remote 操作の前には、接続先と対象ブランチを必ず確認してください。

## スクリーンショット

![グラフ](screenshot/Screenshot1.png)
![コミット](screenshot/Screenshot2.png)
![差分](screenshot/Screenshot3.png)
